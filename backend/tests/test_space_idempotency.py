"""Intenciones de espacio completo: HTTP y SQLite reales, ejecución y fuentes dobles.

Los fingerprints históricos son cuerpos literales independientes del helper de
admisión. Se ejercitan las rutas, reservas, worker y catálogo sin Chroma ni LLM.
"""

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.api.deps import get_sesion_actual
from app.api.routes.generations import router
from app.config import Configuracion
from app.core.agents.graph_state import DocumentoGeneracion
from app.generations import service as generation_service
from app.generations.service import ServicioGeneraciones
from app.jobs.manager import EspacioOcupadoError, GestorTrabajos
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.responses import DocumentoFuente, cargar_paquete_json
from app.session.manifests import ManifiestoEspacio
from app.storage.object_keys import clave_output, clave_workspace
from app.storage.provider import StorageNotFound
from doubles.gemini import DobleGemini
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from test_schemas import FLASHCARDS, salida_valida

pytestmark = pytest.mark.integration_mock

INTENCION = (
    '{"alcance": {"seccion_id": null, "tipo": "espacio_completo"}, '
    '"formato_salida": "flashcards", "idioma_salida": "es", "nicho_sector": "general", '
    '"nivel_detalle": "didactico", "perfil_destinatario": "principiante"}'
)
HISTORICA_AB = (
    '{"alcance": {"document_ids": ["doc_a", "doc_b"], "seccion_id": null, "tipo": "espacio_completo"}, '
    '"document_id": "doc_a", "document_ids": ["doc_a", "doc_b"], "formato_salida": "flashcards", '
    '"idioma_salida": "es", "nicho_sector": "general", "nivel_detalle": "didactico", '
    '"perfil_destinatario": "principiante"}'
)
HISTORICA_A = (
    '{"alcance": {"seccion_id": null, "tipo": "espacio_completo"}, "document_id": "doc_a", '
    '"document_ids": ["doc_a"], "formato_salida": "flashcards", "idioma_salida": "es", '
    '"nicho_sector": "general", "nivel_detalle": "didactico", "perfil_destinatario": "principiante"}'
)
HISTORICA_A_ANIDADA = (
    '{"alcance": {"document_ids": ["doc_a"], "seccion_id": null, "tipo": "espacio_completo"}, '
    '"document_id": "doc_a", "document_ids": ["doc_a"], "formato_salida": "flashcards", '
    '"idioma_salida": "es", "nicho_sector": "general", "nivel_detalle": "didactico", '
    '"perfil_destinatario": "principiante"}'
)
HISTORICA_BASICA_AB = (
    '{"alcance": {"document_ids": ["doc_a", "doc_b"], "seccion_id": null, "tipo": "espacio_completo"}, '
    '"document_id": "doc_a", "document_ids": ["doc_a", "doc_b"], "formato_salida": "flashcards", '
    '"idioma_salida": "es", "modo_generacion": "basico", "nicho_sector": "general", "nivel_detalle": "didactico", '
    '"perfil_destinatario": "principiante"}'
)


def fuera_de_transaccion(db):
    # Una transacción de otro hilo puede existir; importa quién ejecuta la I/O.
    assert not (db._lock._is_owned() and db._conn.in_transaction), "I/O dentro de una transacción SQLite"


class StorageDoble:
    def __init__(self):
        self.objetos = {}
        self.db = None

    def get(self, clave, **kwargs):
        fuera_de_transaccion(self.db)
        if clave not in self.objetos:
            raise StorageNotFound(clave)
        return self.objetos[clave]


class ConsultaDoble:
    indice = None

    def __init__(self, db):
        self.db = db
        self.admisiones = []

    def detalle(self, token, document_id):
        fuera_de_transaccion(self.db)
        doc = self.db.obtener_documento(document_id)
        sesion = self.db.obtener_sesion(token)
        if doc is None or sesion is None or doc["workspace_id"] != sesion["workspace_id"]:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no encontrado.")
        return doc

    def _listo(self, token, document_id):
        self.admisiones.append(document_id)
        doc = self.detalle(token, document_id)
        if doc["estado"] != "ready":
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "Documento no listo.")
        return doc

    def fuente_aprobada(self, token, document_id):
        doc = self.detalle(token, document_id)
        return DocumentoFuente(document_id=document_id, titulo=doc["titulo"], hash=doc["hash"], version="v1")

    def documento_grafo(self, contexto, document_id):
        fuera_de_transaccion(self.db)
        doc = self.db.obtener_documento(document_id)
        if doc is None or doc["workspace_id"] != contexto.workspace_id:
            return None
        return DocumentoGeneracion(
            workspace_id=contexto.workspace_id,
            fuente=DocumentoFuente(document_id=document_id, titulo=doc["titulo"], hash=doc["hash"], version="v1"),
            estado=doc["estado"],
            idioma_origen="es",
            secciones=("Conceptos",),
        )

    def version_parser(self, contexto, document_id):
        return "1.0"

    def loader_critic(self, contexto):
        return None


@pytest.fixture
def espacio(tmp_path, monkeypatch):
    """Ruta de producción con dependencias locales explícitas y worker real."""
    e = SimpleNamespace(
        ruta=tmp_path / "espacio.sqlite3",
        cfg=Configuracion(
            app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path), workspace_max_documents=12
        ),
        storage=StorageDoble(),
        proveedor=DobleGemini(16),
        ejecutados=[],
        liberar=threading.Event(),
        iniciado=threading.Event(),
        bloquear=False,
    )
    app = FastAPI()
    app.include_router(router)

    @app.exception_handler(ErrorAplicacion)
    async def error_aplicacion(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.error.model_dump(mode="json"), "request_id": "test"},
        )

    def arrancar():
        e.db = RegistroOperativo(e.ruta)
        e.storage.db = e.db
        e.consulta = ConsultaDoble(e.db)
        e.gestor = GestorTrabajos(e.db)
        e.servicio = ServicioGeneraciones(e.db, e.storage, e.consulta, e.gestor, e.cfg, e.proveedor, e.proveedor)
        e.servicio.retriever = SimpleNamespace(
            recuperar=lambda *args, **kwargs: pytest.fail("El doble de ejecución no debe invocar retrieval"),
            indice=SimpleNamespace(
                listar_chunks=lambda *args, **kwargs: pytest.fail("El doble de ejecución no debe consultar Chroma")
            ),
        )
        app.state.generations = e.servicio

    arrancar()
    e.db.crear_workspace("ws_a", "codigo-a", 30)
    e.db.crear_workspace("ws_b", "codigo-b", 30)
    e.db.crear_sesion("token-a", "ws_a", 24)

    def manifest():
        ws = e.db.workspace_para_manifest("ws_a")
        e.storage.objetos[clave_workspace("ws_a")] = (
            ManifiestoEspacio(
                workspace_id="ws_a",
                codigo_hash=ws["codigo_hash"],
                version=ws["version_manifiesto"],
                expira_en=ws["expira_en"],
                recursos_borrados=e.db.listar_borrados("ws_a"),
            )
            .model_dump_json()
            .encode()
        )

    manifest()
    app.dependency_overrides[get_sesion_actual] = lambda: {**e.db.obtener_sesion("token-a"), "token": "token-a"}

    def documento(document_id, *, workspace="ws_a", estado=DocumentStatus.READY):
        contenido = f"Contenido exclusivo de {document_id}: redes, almacenamiento y bases de datos."
        e.db.registrar_documento(
            document_id, workspace, f"Fuente {document_id}", estado, hashlib.sha256(contenido.encode()).hexdigest()
        )

    def ejecutar(estado, *, basico=False):
        fuera_de_transaccion(e.db)
        e.ejecutados.append((estado.generation_id, estado.parametros.model_copy(deep=True), estado.schema_version))
        e.iniciado.set()
        if e.bloquear:
            assert e.liberar.wait(20), "El test no liberó el ejecutable local"
        fila = e.db.obtener_generacion(estado.generation_id)
        paquete = salida_valida(FLASHCARDS).model_dump(mode="json")
        parametros = estado.parametros
        paquete.update(
            generation_id=estado.generation_id,
            documento_fuente=e.db.snapshot_generacion(estado.generation_id)[0],
            documentos_fuente=e.db.snapshot_generacion(estado.generation_id),
            created_at=fila["creado_en"],
            almacenamiento_oci={
                "bucket": e.cfg.oci_bucket_name,
                "objeto_id": clave_output("ws_a", estado.generation_id),
            },
        )
        paquete["metadatos"].update(
            perfil_aplicado=parametros.perfil_destinatario.value,
            idioma_salida=parametros.idioma_salida.value,
            nicho_sector=parametros.nicho_sector.value,
            nivel_detalle=parametros.nivel_detalle.value,
            alcance={**parametros.alcance.model_dump(mode="json"), "secciones_cubiertas": ["Conceptos"]},
        )
        if len(paquete["documentos_fuente"]) > 1:
            plantilla = paquete["contenido_adaptado"]["items"][0]
            paquete["referencias"], paquete["contenido_adaptado"]["items"] = [], []
            for indice, fuente in enumerate(paquete["documentos_fuente"]):
                referencia = {"chunk_id": "chunk_" + fuente["document_id"], "document_id": fuente["document_id"]}
                paquete["referencias"].append(referencia)
                item = deepcopy(plantilla)
                item.update(id=f"item_{indice}", dorso="Aporte de " + fuente["titulo"], referencias=[referencia])
                paquete["contenido_adaptado"]["items"].append(item)
        if basico:
            paquete.update(schema_version="1.1", modo_generacion="basico", status="no_evaluado")
            paquete["evaluacion_calidad"] = {"estado_evaluacion": "no_realizada"}
            paquete["trazabilidad"]["modelo_verificacion"] = None
        elif estado.schema_version == "1.1":
            paquete.update(schema_version="1.1", modo_generacion="multiagente")
        canonico = cargar_paquete_json(json.dumps(paquete))
        e.storage.objetos[clave_output("ws_a", estado.generation_id)] = canonico.model_dump_json().encode()
        return canonico

    monkeypatch.setattr(generation_service, "ejecutar_en_worker", lambda estado, deps: ejecutar(estado))
    monkeypatch.setattr(
        generation_service, "ejecutar_basico", lambda estado, *args, **kwargs: ejecutar(estado, basico=True)
    )

    def reiniciar():
        e.gestor.detener()
        e.db.cerrar()
        arrancar()

    e.documento, e.manifest, e.reiniciar = documento, manifest, reiniciar
    with TestClient(app) as cliente:
        e.cliente = cliente
        yield e
    e.liberar.set()
    e.gestor.detener()
    e.db.cerrar()


def pedir(e, clave="todo", **cambios):
    datos = {**json.loads(INTENCION), **cambios}
    return e.cliente.post("/api/generate", json=datos, headers={"Idempotency-Key": clave} if clave else {})


def terminado(e, respuesta):
    assert respuesta.status_code == 202, respuesta.text
    url = respuesta.json()["status_url"]
    limite = time.monotonic() + 10
    while time.monotonic() < limite:
        consulta = e.cliente.get(url)
        assert consulta.status_code == 200, consulta.text
        datos = consulta.json()
        if datos["status"] == "completed":
            return datos
        assert datos["status"] in {"queued", "running"}, datos
        time.sleep(0.005)
    pytest.fail("El ejecutable local no terminó")


def intencion(e, clave="todo"):
    fila = e.db._conn.execute(
        "SELECT rowid,* FROM idempotency_keys WHERE workspace_id='ws_a' AND operacion='generate' AND clave=?", (clave,)
    ).fetchone()
    return dict(fila) if fila else None


def congelado(e, generation_id, clave="todo"):
    return (
        e.db.obtener_generacion(generation_id),
        e.db.documentos_generacion(generation_id),
        e.db.snapshot_generacion(generation_id),
        intencion(e, clave),
        tuple(e.db._conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("generations", "jobs")),
        len(e.ejecutados),
    )


def sembrar_historica(e, generation_id, cuerpo, clave="todo"):
    with e.db.transaccion():
        e.db._conn.execute("UPDATE generations SET parametros_json=? WHERE generation_id=?", (cuerpo, generation_id))
        e.db._conn.execute(
            """INSERT INTO idempotency_keys(workspace_id,operacion,clave,hash_cuerpo,recurso_id,estado,creado_en)
               VALUES('ws_a','generate',?,?,?,'completada','2026-10-01T12:00:00+00:00')""",
            (clave, hashlib.sha256(cuerpo.encode()).hexdigest(), generation_id),
        )


def prohibir_admision(e, monkeypatch):
    def prohibido(*args, **kwargs):
        pytest.fail("El reintento volvió a resolver fuentes o comprobar cuotas de un trabajo nuevo")

    monkeypatch.setattr(e.db, "documentos_ready", prohibido)
    monkeypatch.setattr(e.db, "comprobar_limite_recursos", prohibido)
    monkeypatch.setattr(e.consulta, "_listo", prohibido)
    e.servicio.generador = e.servicio.verificador = e.servicio.retriever = None


def test_intencion_nueva_conserva_fuentes_snapshot_y_hash_tras_crecer_a_seis(espacio, monkeypatch):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    original = terminado(e, pedir(e))
    generation_id = original["generation_id"]
    assert intencion(e)["hash_cuerpo"] == hashlib.sha256(INTENCION.encode()).hexdigest()
    assert e.db.documentos_generacion(generation_id) == ["doc_a", "doc_b"]
    assert len({f["hash"] for f in e.db.snapshot_generacion(generation_id)}) == 2
    for identificador in ("doc_c", "doc_d", "doc_e", "doc_f"):
        e.documento(identificador)
    e.documento("doc_pendiente", estado=DocumentStatus.PROCESSING)
    e.documento("doc_ajeno", workspace="ws_b")
    previo = congelado(e, generation_id)
    prohibir_admision(e, monkeypatch)
    for selectores in ({}, {"document_id": "doc_a"}, {"document_ids": ["doc_b", "doc_a"]}):
        repetida = pedir(e, **selectores)
        assert repetida.status_code == 202, repetida.text
        assert repetida.json() == original
        assert congelado(e, generation_id) == previo


@pytest.mark.parametrize("con_primario", [False, True])
@pytest.mark.parametrize(
    "cantidad,cuerpo,modo",
    [
        (1, HISTORICA_A, "multiagente"),
        (1, HISTORICA_A_ANIDADA, "multiagente"),
        (2, HISTORICA_AB, "multiagente"),
        (2, HISTORICA_BASICA_AB, "basico"),
    ],
)
def test_historica_literal_se_compara_con_fuentes_originales_no_actuales(
    espacio, monkeypatch, con_primario, cantidad, cuerpo, modo
):
    e = espacio
    e.documento("doc_a")
    if cantidad == 2:
        e.documento("doc_b")
    resultado = terminado(e, pedir(e, None, modo_generacion=modo))
    generation_id = resultado["generation_id"]
    sembrar_historica(e, generation_id, cuerpo)
    for identificador in ("doc_b", "doc_c", "doc_d", "doc_e", "doc_f"):
        if identificador != "doc_b" or cantidad == 1:
            e.documento(identificador)
    previo = congelado(e, generation_id)
    prohibir_admision(e, monkeypatch)
    repetida = pedir(e, modo_generacion=modo, **({"document_id": "doc_a"} if con_primario else {}))
    assert repetida.status_code == 202, repetida.text
    assert repetida.json() == resultado
    assert congelado(e, generation_id) == previo
    assert intencion(e)["hash_cuerpo"] == hashlib.sha256(cuerpo.encode()).hexdigest()


@pytest.mark.parametrize("historica", [False, True])
@pytest.mark.parametrize(
    "cambio",
    [
        {"perfil_destinatario": "ejecutivo"},
        {"idioma_salida": "en"},
        {"nivel_detalle": "practico"},
        {"formato_salida": "quiz"},
        {"nicho_sector": "fintech"},
        {"modo_generacion": "basico"},
        {"document_id": "doc_a", "alcance": {"tipo": "documento_completo"}},
        {"alcance": {"tipo": "espacio_completo", "seccion_id": "otra-seccion"}},
    ],
)
def test_cambios_semanticos_son_conflicto_aunque_no_haya_proveedores(espacio, monkeypatch, historica, cambio):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    resultado = terminado(e, pedir(e, None if historica else "todo"))
    generation_id = resultado["generation_id"]
    if historica:
        sembrar_historica(e, generation_id, HISTORICA_AB)
    previo = congelado(e, generation_id)
    prohibir_admision(e, monkeypatch)
    respuesta = pedir(e, **cambio)
    assert respuesta.status_code == 409, respuesta.text
    assert respuesta.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    assert congelado(e, generation_id) == previo


def test_clave_nueva_resuelve_estado_actual_una_vez_y_excluye_no_listos_y_ajenos(espacio, monkeypatch):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    original = terminado(e, pedir(e))
    e.documento("doc_c")
    e.documento("doc_pendiente", estado=DocumentStatus.PROCESSING)
    e.documento("doc_ajeno", workspace="ws_b")
    consultas = []
    resolver = e.db.documentos_ready

    def contar(workspace_id):
        consultas.append(workspace_id)
        return resolver(workspace_id)

    monkeypatch.setattr(e.db, "documentos_ready", contar)
    nueva = terminado(e, pedir(e, "nueva"))
    assert nueva["generation_id"] != original["generation_id"]
    assert consultas == ["ws_a"]
    assert e.db.documentos_generacion(nueva["generation_id"]) == ["doc_a", "doc_b", "doc_c"]
    assert e.db.documentos_generacion(original["generation_id"]) == ["doc_a", "doc_b"]
    assert len(e.ejecutados) == 2


@pytest.mark.parametrize("cantidad,codigo", [(0, "NOT_FOUND"), (6, "VALIDATION_ERROR")])
def test_intencion_nueva_rechaza_conjunto_vacio_o_exceso_sin_truncar(espacio, cantidad, codigo):
    e = espacio
    assert e.cfg.workspace_max_documents > 5
    for numero in range(cantidad):
        e.documento(f"doc_{numero}")
    respuesta = pedir(e)
    assert respuesta.status_code == (404 if cantidad == 0 else 422), respuesta.text
    assert respuesta.json()["error"]["code"] == codigo
    if cantidad:
        assert respuesta.json()["error"]["details"] == {"cantidad": 6, "maximo": 5}
    assert intencion(e) is None
    assert e.db._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 0
    assert e.ejecutados == []


@pytest.mark.parametrize("historica", [False, True])
def test_fuente_original_retirada_deniega_sin_generacion_alternativa(espacio, monkeypatch, historica):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    original = terminado(e, pedir(e, None if historica else "todo"))
    generation_id = original["generation_id"]
    if historica:
        sembrar_historica(e, generation_id, HISTORICA_AB)
    e.documento("doc_c")
    intencion_original = intencion(e)
    e.db.agendar_borrado("document", "doc_b", "ws_a")
    e.manifest()
    prohibir_admision(e, monkeypatch)
    respuesta = pedir(e)
    assert respuesta.status_code == 404, respuesta.text
    assert respuesta.json()["error"]["code"] == "NOT_FOUND"
    assert e.cliente.get(original["status_url"]).status_code == 404
    assert intencion(e) == intencion_original
    assert e.db._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 1
    assert e.db.documentos_generacion(generation_id) == ["doc_a", "doc_b"]
    assert len(e.ejecutados) == 1


def test_concurrencia_y_reintento_en_curso_conservan_un_trabajo(espacio, monkeypatch):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    e.bloquear = True
    barrera = threading.Barrier(2)
    resolver = e.db.documentos_ready
    consultas = []

    def resolver_concurrente(workspace_id):
        consultas.append(workspace_id)
        return resolver(workspace_id)

    def enviar_concurrente(selector):
        barrera.wait(10)
        return pedir(e, **selector)

    monkeypatch.setattr(e.db, "documentos_ready", resolver_concurrente)
    try:
        with ThreadPoolExecutor(2) as executor:
            respuestas = list(executor.map(enviar_concurrente, [{}, {"document_id": "doc_a"}]))
        assert all(r.status_code == 202 for r in respuestas), [r.text for r in respuestas]
        assert len({r.json()["generation_id"] for r in respuestas}) == 1
        assert consultas == ["ws_a"]
        assert e.iniciado.wait(10)
        generation_id = respuestas[0].json()["generation_id"]
        e.documento("doc_c")
        previo = congelado(e, generation_id)
        prohibir_admision(e, monkeypatch)
        repetida = pedir(e)
        assert repetida.status_code == 202, repetida.text
        assert repetida.json()["generation_id"] == generation_id
        assert congelado(e, generation_id) == previo
        assert len(e.ejecutados) == 1
    finally:
        e.liberar.set()
    terminado(e, respuestas[0])


def test_fallo_de_admision_limpia_su_reserva_y_permite_reintentar(espacio, monkeypatch):
    e = espacio
    e.documento("doc_a")
    enqueue = e.gestor.enqueue

    def fallar(*args, **kwargs):
        raise EspacioOcupadoError("ws_a")

    monkeypatch.setattr(e.gestor, "enqueue", fallar)
    fallo = pedir(e)
    assert fallo.status_code == 429, fallo.text
    assert intencion(e) is None
    for tabla in ("generations", "generation_documents", "jobs", "job_documents"):
        assert e.db._conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0
    monkeypatch.setattr(e.gestor, "enqueue", enqueue)
    resultado = terminado(e, pedir(e))
    assert intencion(e)["recurso_id"] == resultado["generation_id"]
    assert len(e.ejecutados) == 1


@pytest.mark.parametrize("historica", [False, True])
def test_reinicio_conserva_intencion_y_material_completado_sin_proveedores(espacio, monkeypatch, historica):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    original = terminado(e, pedir(e, None if historica else "todo"))
    generation_id = original["generation_id"]
    if historica:
        sembrar_historica(e, generation_id, HISTORICA_AB)
    e.documento("doc_c")
    previo = congelado(e, generation_id)
    e.reiniciar()
    prohibir_admision(e, monkeypatch)
    repetida = pedir(e)
    assert repetida.status_code == 202, repetida.text
    assert repetida.json() == original
    assert congelado(e, generation_id) == previo


@pytest.mark.parametrize("cantidad,anidada", [(1, False), (1, True), (2, False)])
@pytest.mark.parametrize("modo,version", [(None, "1.0"), ("multiagente", "1.1"), ("basico", "1.1")])
def test_expansion_preserva_presencia_del_modo_y_contrato_publico(espacio, cantidad, anidada, modo, version):
    e = espacio
    e.documento("doc_a")
    if cantidad == 2:
        e.documento("doc_b")
    entrada = {"modo_generacion": modo} if modo else {}
    if anidada:
        entrada["alcance"] = {"tipo": "espacio_completo", "document_ids": ["doc_a"]}
    resultado = terminado(e, pedir(e, **entrada))
    assert resultado["contenido"]["schema_version"] == version
    solicitud = e.ejecutados[0][1]
    assert ("modo_generacion" in solicitud.model_fields_set) == (modo is not None)
    assert solicitud.document_ids == (["doc_a", "doc_b"] if cantidad == 2 else ["doc_a"])
    assert resultado["contenido"]["metadatos"]["alcance"]["tipo"] == "espacio_completo"
    if modo is None:
        assert "modo_generacion" not in resultado["contenido"]
    else:
        assert resultado["contenido"]["modo_generacion"] == modo
    if anidada:
        assert resultado["contenido"]["metadatos"]["alcance"]["document_ids"] == ["doc_a"]
        repetida = pedir(e, **({"modo_generacion": modo} if modo else {}))
        assert repetida.status_code == 202, repetida.text
        assert repetida.json() == resultado
        assert len(e.ejecutados) == 1
