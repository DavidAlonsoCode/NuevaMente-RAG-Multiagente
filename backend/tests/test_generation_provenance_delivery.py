"""Frontera HTTP/canónico con vínculos y snapshot SQLite reales; fuentes y OCI dobles."""

import json
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from app.api.routes.exports import router as exports_router
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.storage.object_keys import clave_export, clave_output
from app.storage.provider import StorageConflict, StorageUnavailable
from fastapi.responses import JSONResponse
from test_schemas import FLASHCARDS, salida_valida
from test_space_idempotency import espacio as espacio
from test_space_idempotency import fuera_de_transaccion

pytestmark = pytest.mark.integration_mock


@pytest.fixture
def catalogo(espacio, monkeypatch):
    e = espacio
    e.cliente.app.include_router(exports_router)
    e.escrituras = []

    def upload(clave, datos, *, crear_solo=False, **kwargs):
        fuera_de_transaccion(e.db)
        if crear_solo and clave in e.storage.objetos:
            raise StorageConflict(clave)
        e.escrituras.append(clave)
        e.storage.objetos[clave] = datos.encode() if isinstance(datos, str) else datos

    monkeypatch.setattr(e.storage, "upload", upload, raising=False)

    @e.cliente.app.exception_handler(StorageUnavailable)
    async def storage_error(request, exc):
        return JSONResponse(status_code=503, content={"error": {"code": "STORAGE_UNAVAILABLE"}})

    return e


def sembrar(e, *, ids=("doc_a", "doc_b"), contrato="1.0", snapshot=True):
    for doc_id in ids:
        e.documento(doc_id)
    fuentes = [e.consulta.fuente_aprobada("token-a", doc_id) for doc_id in ids]
    assert len({f.hash for f in fuentes}) == len(ids)
    e.db.registrar_generacion(
        "gen_ab", "ws_a", ids[0], JobStatus.COMPLETED, document_ids=list(ids), snapshot=fuentes if snapshot else None
    )
    alcance = {"tipo": "multi_documento" if len(ids) > 1 else "documento_completo", "document_ids": list(ids)}
    parametros = {"document_id": ids[0], "document_ids": list(ids), "alcance": alcance}
    if contrato != "1.0":
        parametros["modo_generacion"] = "basico" if contrato == "basico" else "multiagente"
    e.db.guardar_parametros_generacion("gen_ab", parametros)
    datos = salida_valida(FLASHCARDS).model_dump(mode="json")
    datos.update(
        generation_id="gen_ab",
        documento_fuente=fuentes[0].model_dump(mode="json"),
        documentos_fuente=[f.model_dump(mode="json") for f in fuentes],
    )
    datos["metadatos"]["alcance"] = {**alcance, "secciones_cubiertas": ["Conceptos"]}
    datos["referencias"] = [{"chunk_id": "chunk_" + f.document_id, "document_id": f.document_id} for f in fuentes]
    plantilla = datos["contenido_adaptado"]["items"][0]
    datos["contenido_adaptado"]["items"] = []
    for index, ref in enumerate(datos["referencias"]):
        item = deepcopy(plantilla)
        item.update(
            id=f"item_{index}",
            frente=f"¿Qué aporta {ids[index]}?",
            dorso=f"Aporte propio de {ids[index]}.",
            referencias=[deepcopy(ref)],
        )
        datos["contenido_adaptado"]["items"].append(item)
    if contrato != "1.0":
        datos.update(schema_version="1.1", modo_generacion=parametros["modo_generacion"])
    if contrato == "basico":
        datos.update(status="no_evaluado", evaluacion_calidad={"estado_evaluacion": "no_realizada"})
        datos["trazabilidad"]["modelo_verificacion"] = None
    datos["almacenamiento_oci"]["objeto_id"] = clave_output("ws_a", "gen_ab")
    return datos


def guardar(e, datos):
    original = json.dumps(datos, ensure_ascii=False, indent=2).encode()
    e.storage.objetos[clave_output("ws_a", "gen_ab")] = original
    return original


def test_omitir_secundaria_modificada_no_entrega_contenido_http(espacio):
    e = espacio
    datos = sembrar(e)
    datos["documentos_fuente"] = datos["documentos_fuente"][:1]
    original = guardar(e, datos)
    snapshot = e.db.snapshot_generacion("gen_ab")
    e.db.actualizar_documento("doc_b", DocumentStatus.READY, hash_documento="f" * 64)
    assert e.db.documentos_generacion("gen_ab") == ["doc_a", "doc_b"]
    assert [f["document_id"] for f in snapshot] == ["doc_a", "doc_b"]
    respuesta = e.cliente.get("/api/generations/gen_ab")
    assert respuesta.status_code == 409, respuesta.text
    assert respuesta.json()["error"]["code"] == "INVALID_STATE"
    assert "contenido" not in respuesta.json()
    assert e.db.snapshot_generacion("gen_ab") == snapshot
    assert e.storage.objetos[clave_output("ws_a", "gen_ab")] == original
    assert not e.ejecutados


@pytest.mark.parametrize("contrato", ["1.0", "multiagente", "basico"])
def test_paquete_valido_conserva_contrato_bytes_y_exports(catalogo, contrato):
    e = catalogo
    original = guardar(e, sembrar(e, contrato=contrato))
    paquete, datos = e.servicio.catalogo.cargar("token-a", "gen_ab")
    assert datos == original
    assert [f.document_id for f in paquete.documentos_fuente] == ["doc_a", "doc_b"]
    respuesta = e.cliente.get("/api/generations/gen_ab")
    assert respuesta.status_code == 200, respuesta.text
    assert respuesta.json()["contenido"]["schema_version"] == ("1.0" if contrato == "1.0" else "1.1")
    for formato in ("json", "md"):
        descarga = e.cliente.get("/api/exports/gen_ab", params={"format": formato})
        assert descarga.status_code == 200, descarga.text
        if formato == "json":
            assert descarga.content == original
    assert not e.ejecutados


@pytest.mark.parametrize(
    "caso",
    [
        "omitida",
        "extra",
        "duplicada",
        "primario_en_lista",
        "primario_ausente",
        "primario_distinto",
        "alcance",
        "alcance_duplicado",
        "cita_ajena",
        "cita_anidada",
        "cita_sin_id",
    ],
)
def test_declaraciones_incoherentes_no_se_normalizan_ni_crean_exports(catalogo, caso):
    e = catalogo
    datos = sembrar(e)
    if caso in {"omitida", "primario_ausente"}:
        datos["documentos_fuente"] = (
            datos["documentos_fuente"][:1] if caso == "omitida" else datos["documentos_fuente"][1:]
        )
    elif caso == "extra":
        e.documento("doc_c")
        datos["documentos_fuente"].append(e.consulta.fuente_aprobada("token-a", "doc_c").model_dump(mode="json"))
    elif caso == "duplicada":
        datos["documentos_fuente"].append(deepcopy(datos["documentos_fuente"][1]))
    elif caso == "primario_en_lista":
        datos["documentos_fuente"][0]["hash"] = "f" * 64
    elif caso == "primario_distinto":
        datos["documento_fuente"] = deepcopy(datos["documentos_fuente"][1])
    elif caso.startswith("alcance"):
        datos["metadatos"]["alcance"]["document_ids"] = ["doc_a"] if caso == "alcance" else ["doc_a", "doc_b", "doc_b"]
    elif caso == "cita_sin_id":
        datos["referencias"][1].pop("document_id")
    else:
        ref = (
            datos["referencias"][0]
            if caso == "cita_ajena"
            else datos["contenido_adaptado"]["items"][0]["referencias"][0]
        )
        ref["document_id"] = "doc_ajeno"
        if caso == "cita_anidada":
            assert {r["document_id"] for r in datos["referencias"]} == {"doc_a", "doc_b"}
    original = guardar(e, datos)
    for ruta in ("/api/generations/gen_ab", "/api/exports/gen_ab?format=json", "/api/exports/gen_ab?format=md"):
        respuesta = e.cliente.get(ruta)
        assert respuesta.status_code == 409, respuesta.text
        assert respuesta.json()["error"]["code"] == "INVALID_STATE"
        assert "contenido" not in respuesta.json()
    assert not e.escrituras and not e.ejecutados
    assert e.storage.objetos[clave_output("ws_a", "gen_ab")] == original


@pytest.mark.parametrize(
    "cambio", ["ausente", "vacio", "duplicado", "version", "procedencia", "ajeno", "vinculo", "parametros"]
)
def test_procedencia_persistida_no_se_reconstruye_desde_documentos_actuales(catalogo, cambio):
    e = catalogo
    original = guardar(e, sembrar(e))
    snapshot = e.db.snapshot_generacion("gen_ab")
    if cambio == "ausente":
        snapshot = None
    elif cambio == "vacio":
        snapshot = []
    elif cambio == "duplicado":
        snapshot.append(deepcopy(snapshot[1]))
    elif cambio in {"version", "procedencia"}:
        snapshot[1][cambio] = "otra"
    elif cambio == "ajeno":
        e.documento("doc_x", workspace="ws_b")
        snapshot[1]["document_id"] = "doc_x"
    with e.db.transaccion():
        if cambio == "vinculo":
            e.db._conn.execute("DELETE FROM generation_documents WHERE generation_id='gen_ab' AND document_id='doc_b'")
        elif cambio == "parametros":
            e.db._conn.execute(
                "UPDATE generations SET parametros_json=? WHERE generation_id='gen_ab'",
                (json.dumps({"document_id": "doc_a", "document_ids": ["doc_a"]}),),
            )
        else:
            e.db._conn.execute(
                "UPDATE generations SET snapshot_fuentes_json=? WHERE generation_id='gen_ab'",
                (json.dumps(snapshot) if snapshot is not None else None,),
            )
    respuesta = e.cliente.get("/api/generations/gen_ab")
    assert respuesta.status_code == 409, respuesta.text
    assert respuesta.json()["error"]["code"] == "INVALID_STATE"
    assert not e.escrituras and not e.ejecutados
    assert e.storage.objetos[clave_output("ws_a", "gen_ab")] == original


@pytest.mark.parametrize("momento", ["antes", "durante_io"])
@pytest.mark.parametrize("cambio", ["hash", "titulo", "version", "procedencia", "retirada"])
def test_fuente_secundaria_completa_se_revalida_al_entregar(catalogo, monkeypatch, momento, cambio):
    e = catalogo
    guardar(e, sembrar(e))
    fuente_original = e.consulta.fuente_aprobada
    alterada = False

    def cambiar():
        nonlocal alterada
        alterada = True
        if cambio == "retirada":
            e.db.retirar_documento("token-a", "doc_b")
        elif cambio == "hash":
            e.db.actualizar_documento("doc_b", DocumentStatus.READY, hash_documento="f" * 64)
        elif cambio == "titulo":
            with e.db.transaccion():
                e.db._conn.execute("UPDATE documents SET titulo='Otro título' WHERE document_id='doc_b'")

    def fuente(token, doc_id):
        actual = fuente_original(token, doc_id)
        return (
            actual.model_copy(update={cambio: "otra"})
            if alterada and doc_id == "doc_b" and cambio in {"version", "procedencia"}
            else actual
        )

    monkeypatch.setattr(e.consulta, "fuente_aprobada", fuente)
    if momento == "antes":
        cambiar()
    else:
        get = e.storage.get

        def leer(clave, **kwargs):
            contenido = get(clave, **kwargs)
            if clave == clave_output("ws_a", "gen_ab") and not alterada:
                cambiar()
            return contenido

        monkeypatch.setattr(e.storage, "get", leer)
    respuesta = e.cliente.get("/api/generations/gen_ab")
    assert respuesta.status_code == (404 if cambio == "retirada" else 409), respuesta.text
    assert "contenido" not in respuesta.json()
    assert not e.escrituras and not e.ejecutados


@pytest.mark.parametrize("contrato", ["1.0", "multiagente", "basico"])
@pytest.mark.parametrize("snapshot", [False, True])
def test_unidocumental_historico_verificable_sin_nuevos_campos(catalogo, contrato, snapshot):
    e = catalogo
    datos = sembrar(e, ids=("doc_a",), contrato=contrato, snapshot=snapshot)
    datos.pop("documentos_fuente")
    datos["metadatos"]["alcance"].pop("document_ids")
    for ref in [*datos["referencias"], *datos["contenido_adaptado"]["items"][0]["referencias"]]:
        ref.pop("document_id", None)
    original = guardar(e, datos)
    respuesta = e.cliente.get("/api/generations/gen_ab")
    assert respuesta.status_code == 200, respuesta.text
    descarga = e.cliente.get("/api/exports/gen_ab")
    assert descarga.status_code == 200 and descarga.content == original
    assert e.db.snapshot_generacion("gen_ab") is not None if snapshot else e.db.snapshot_generacion("gen_ab") is None


def test_sesion_ajena_no_lee_canonico(catalogo, monkeypatch):
    e = catalogo
    guardar(e, sembrar(e))
    e.db.crear_sesion("token-b", "ws_b", 24)
    get = e.storage.get
    claves = []

    def leer(clave, **kwargs):
        claves.append(clave)
        return get(clave, **kwargs)

    monkeypatch.setattr(e.storage, "get", leer)
    with pytest.raises(ErrorAplicacion) as error:
        e.servicio.catalogo.cargar("token-b", "gen_ab")
    assert error.value.error.code == ErrorCode.NOT_FOUND and not claves


@pytest.mark.parametrize("snapshot", [False, True])
def test_hash_del_vinculo_no_se_sustituye_por_canonico_y_version_actual(catalogo, snapshot):
    e = catalogo
    datos = sembrar(e, ids=("doc_a",), snapshot=snapshot)
    datos["documento_fuente"]["hash"] = datos["documentos_fuente"][0]["hash"] = "f" * 64
    e.db.actualizar_documento("doc_a", DocumentStatus.READY, hash_documento="f" * 64)
    if snapshot:
        with e.db.transaccion():
            e.db._conn.execute(
                "UPDATE generations SET snapshot_fuentes_json=? WHERE generation_id='gen_ab'",
                (json.dumps(datos["documentos_fuente"]),),
            )
    guardar(e, datos)
    respuesta = e.cliente.get("/api/generations/gen_ab")
    assert respuesta.status_code == 409, respuesta.text
    assert not e.escrituras and not e.ejecutados


@pytest.mark.parametrize("caso", ["omitida", "normalizacion", "snapshot_ausente", "cita_anidada", "hash_secundaria"])
def test_reintento_de_persistencia_no_publica_derivados_incoherentes(catalogo, caso):
    e = catalogo
    datos = sembrar(e)
    if caso == "omitida":
        datos["documentos_fuente"] = datos["documentos_fuente"][:1]
    elif caso == "normalizacion":
        datos["documentos_fuente"][0]["hash"] = "f" * 64
    elif caso == "cita_anidada":
        datos["contenido_adaptado"]["items"][0]["referencias"][0]["document_id"] = "doc_x"
        assert {r["document_id"] for r in datos["referencias"]} == {"doc_a", "doc_b"}
    texto = json.dumps(datos, ensure_ascii=False)
    # Retención durable incoherente preexistente: no pasar por el helper nuevo para sembrarla.
    e.db.actualizar_generacion("gen_ab", JobStatus.FAILED, "STORAGE_UNAVAILABLE", "Fallo simulado")
    with e.db.transaccion():
        e.db._conn.execute(
            "INSERT INTO approved_outputs VALUES (?, ?, ?)",
            ("gen_ab", texto, (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()),
        )
        if caso == "snapshot_ausente":
            e.db._conn.execute("UPDATE generations SET snapshot_fuentes_json=NULL WHERE generation_id='gen_ab'")
    if caso == "hash_secundaria":
        e.db.actualizar_documento("doc_b", DocumentStatus.READY, hash_documento="f" * 64)
    respuesta = e.cliente.post("/api/generations/gen_ab/persist", headers={"Idempotency-Key": "persistir"})
    if caso == "hash_secundaria":
        assert respuesta.status_code == 202, respuesta.text
        limite = time.monotonic() + 5
        while time.monotonic() < limite:
            fila = e.db.obtener_generacion("gen_ab")
            if fila["status"] == "failed":
                break
            time.sleep(0.01)
        assert fila["status"] == "failed" and fila["error_code"] == "INVALID_STATE"
    else:
        assert respuesta.status_code == 409, respuesta.text
        assert e.db.job_generacion("gen_ab") is None
        assert (
            e.db._conn.execute("SELECT COUNT(*) FROM idempotency_keys WHERE operacion='persist:gen_ab'").fetchone()[0]
            == 0
        )
    assert not e.escrituras and not e.ejecutados
    assert not any(k.startswith(("outputs/", "exports/")) for k in e.storage.objetos)
    assert e.db.aprobado_retenido("gen_ab") == texto


def test_export_cache_no_se_recrea_si_secundaria_cambia_durante_io(catalogo, monkeypatch):
    e = catalogo
    guardar(e, sembrar(e))
    get = e.storage.get

    def leer(clave, **kwargs):
        if clave == clave_export("ws_a", "gen_ab", "md"):
            e.db.actualizar_documento("doc_b", DocumentStatus.READY, hash_documento="f" * 64)
        return get(clave, **kwargs)

    monkeypatch.setattr(e.storage, "get", leer)
    respuesta = e.cliente.get("/api/exports/gen_ab?format=md")
    assert respuesta.status_code == 409, respuesta.text
    assert not e.escrituras


def test_cache_existente_no_evade_validacion_del_canonico(catalogo):
    e = catalogo
    datos = sembrar(e)
    datos["documentos_fuente"] = datos["documentos_fuente"][:1]
    guardar(e, datos)
    for formato in ("json", "md"):
        clave = clave_export("ws_a", "gen_ab", formato)
        e.storage.objetos[clave] = b"DERIVADO NO AUTORIZADO"
        respuesta = e.cliente.get("/api/exports/gen_ab", params={"format": formato})
        assert respuesta.status_code == 409, respuesta.text
        assert b"DERIVADO NO AUTORIZADO" not in respuesta.content
        assert e.storage.objetos[clave] == b"DERIVADO NO AUTORIZADO"
    assert not e.escrituras and not e.ejecutados


def test_revalidacion_compartida_tampoco_confia_en_lista_del_modelo(catalogo):
    e = catalogo
    guardar(e, sembrar(e))
    paquete, _ = e.servicio.catalogo.cargar("token-a", "gen_ab")
    incompleto = paquete.model_copy(update={"documentos_fuente": paquete.documentos_fuente[:1]})
    with pytest.raises(ErrorAplicacion) as error:
        e.servicio.catalogo.validar_fuente("token-a", incompleto)
    assert error.value.error.code == ErrorCode.INVALID_STATE
    assert not e.escrituras and not e.ejecutados


@pytest.mark.parametrize("contrato", ["1.0", "multiagente", "basico"])
@pytest.mark.parametrize("historico", [False, True])
def test_reintento_valido_conserva_bytes_originales_sin_modelos(catalogo, contrato, historico):
    e = catalogo
    datos = sembrar(e, ids=("doc_a",) if historico else ("doc_a", "doc_b"), contrato=contrato, snapshot=not historico)
    if historico:
        datos.pop("documentos_fuente")
        datos["metadatos"]["alcance"].pop("document_ids")
        for ref in [*datos["referencias"], *datos["contenido_adaptado"]["items"][0]["referencias"]]:
            ref.pop("document_id", None)
    texto = json.dumps(datos, ensure_ascii=False, indent=2)
    e.db.actualizar_generacion("gen_ab", JobStatus.FAILED, "STORAGE_UNAVAILABLE", "Fallo simulado")
    e.db.guardar_aprobado("gen_ab", texto, (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat())
    respuesta = e.cliente.post("/api/generations/gen_ab/persist")
    assert respuesta.status_code == 202, respuesta.text
    limite = time.monotonic() + 5
    while time.monotonic() < limite:
        fila = e.db.obtener_generacion("gen_ab")
        if fila["status"] not in {"queued", "running"}:
            break
        time.sleep(0.01)
    assert fila["status"] == "completed", fila
    assert e.storage.objetos[clave_output("ws_a", "gen_ab")] == texto.encode()
    assert e.storage.objetos[clave_export("ws_a", "gen_ab", "json")] == texto.encode()
    assert e.cliente.get("/api/exports/gen_ab").content == texto.encode()
    assert e.db.aprobado_retenido("gen_ab") is None and not e.ejecutados
