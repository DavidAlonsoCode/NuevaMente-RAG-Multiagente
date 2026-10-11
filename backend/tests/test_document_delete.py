"""Retirada real: SQLite conservada, mock OCI y Chroma temporal, sin servicios reales."""

import hashlib
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from app.config import Configuracion
from app.core.rag.rebuild import ManifiestoDocumento
from app.core.rag.vectorstore import _filtro
from app.jobs.manager import CuotasModelo, CuotasProveedor, TrabajoCanceladoError
from app.main import crear_app
from app.schemas.enums import DocumentStatus, JobStatus
from app.session.manifests import Lapida, ManifiestoEspacio, SincronizadorManifiestos, sincronizar_manifiesto
from app.storage.object_keys import (
    clave_documento,
    clave_export,
    clave_original,
    clave_output,
    clave_progress,
    clave_workspace,
)
from app.storage.provider import StorageConflict, StorageNotFound, StorageUnavailable
from doubles.gemini import DobleGemini
from fastapi.testclient import TestClient
from test_document_http import entorno as entorno
from test_document_http import listo, subir

pytestmark = pytest.mark.integration_mock


def hasta(condicion):
    limite = time.monotonic() + 10
    while time.monotonic() < limite:
        if valor := condicion():
            return valor
        time.sleep(0.005)
    pytest.fail("No se confirmó la condición")


def borrar(e, doc, headers=None):
    return e.cliente.delete(f"/api/documents/{doc}", headers=e.headers if headers is None else headers)


def debido(e):
    with e.db.transaccion():
        e.db._conn.execute("UPDATE document_cleanup SET proximo_intento_en=NULL")


def ausencia(e, clave):
    with pytest.raises(StorageNotFound):
        e.storage.get(clave)


def chunks_ausentes(e, doc):
    indice = e.app.state.vectorstore
    ws = e.ws["workspace_id"]
    assert indice._retirado(ws, doc)
    assert indice._activo(indice._privada, ws, doc) is None
    for coleccion in indice._cliente.list_collections():
        if coleccion.name.startswith("embeddings-"):
            assert not indice._cliente.get_collection(coleccion.name, embedding_function=None).get(
                where=_filtro(ws, doc), include=[]
            )["ids"]


def terminal(e, job):
    return hasta(lambda: (j := e.db.obtener_job(job)) and j["status"] not in ("queued", "running") and j)


def test_ready_204_verifica_recursos_gets_y_repeticion(entorno):
    e = entorno
    datos = listo(e)
    doc, ws = datos["document_id"], e.ws["workspace_id"]
    chunk = e.consulta.chunks(e.ws["token"], doc)[0]
    llamadas = e.doble.llamadas_embeddings
    respuesta = borrar(e, doc)
    assert respuesta.status_code == 204 and respuesta.content == b""
    assert e.db.esta_borrado("document", doc)
    assert e.db.limpieza_documental_completada(ws, doc)
    chunks_ausentes(e, doc)
    ausencia(e, clave_original(ws, doc))
    ausencia(e, clave_documento(ws, doc))
    remoto = ManifiestoEspacio.model_validate_json(e.storage.get(clave_workspace(ws)))
    assert any(lapida.recurso_id == doc and lapida.recurso_tipo == "document" for lapida in remoto.recursos_borrados)
    for ruta in (
        f"/api/documents/{doc}",
        f"/api/documents/{doc}/sources/{chunk.chunk_id}",
        datos["status_url"],
        datos["events_url"],
    ):
        assert e.cliente.get(ruta, headers=e.headers).status_code == 404
    assert e.cliente.get("/api/documents", headers=e.headers).json()["items"] == []
    previo = dict(e.db._conn.execute("SELECT * FROM document_cleanup WHERE document_id=?", (doc,)).fetchone())
    assert borrar(e, doc).status_code == 404
    assert dict(e.db._conn.execute("SELECT * FROM document_cleanup WHERE document_id=?", (doc,)).fetchone()) == previo
    assert e.doble.llamadas_embeddings == llamadas
    nuevo = subir(e, clave="despues_de_borrar")
    assert nuevo.status_code == 202 and nuevo.json()["document_id"] != doc
    assert terminal(e, nuevo.json()["job_id"])["status"] == "completed"


@pytest.mark.parametrize("caso", ["sin_sesion", "ajeno", "inexistente"])
def test_delete_autorizacion_sin_efectos_ajenos(entorno, caso):
    e = entorno
    datos = listo(e)
    if caso == "sin_sesion":
        respuesta = borrar(e, datos["document_id"], {})
        esperado = 401
    elif caso == "ajeno":
        otro = e.cliente.post("/api/workspaces").json()
        respuesta = borrar(e, datos["document_id"], {"Authorization": f"Bearer {otro['token']}"})
        esperado = 404
    else:
        respuesta = borrar(e, "no_existe")
        esperado = 404
    assert respuesta.status_code == esperado
    assert not e.db.esta_borrado("document", datos["document_id"])
    assert e.consulta.detalle(e.ws["token"], datos["document_id"])["estado"] == "ready"


@pytest.mark.parametrize(
    "punto,aplicada",
    [
        ("original", False),
        ("original", True),
        ("manifest", True),
        ("workspace", False),
        ("workspace", True),
        ("chroma", False),
        ("chroma", True),
        ("lapida_documental", False),
        ("lapida_documental", True),
    ],
)
def test_fallos_y_respuesta_perdida_conservan_obligacion(entorno, monkeypatch, punto, aplicada):
    e = entorno
    datos = listo(e)
    doc, ws = datos["document_id"], e.ws["workspace_id"]
    clave = clave_original(ws, doc) if punto == "original" else clave_documento(ws, doc)
    original_delete, original_upload = e.storage.delete, e.storage.upload
    indice = e.app.state.vectorstore
    retirar = indice._retirar_verificado

    def delete(k):
        if k == clave:
            if aplicada:
                original_delete(k)
            raise StorageUnavailable("respuesta perdida")
        return original_delete(k)

    def upload(k, contenido, **kw):
        if (punto == "workspace" and k == clave_workspace(ws)) or (
            punto == "lapida_documental" and k == clave_documento(ws, doc) and json.loads(contenido).get("borrado")
        ):
            if aplicada:
                original_upload(k, contenido, **kw)
            raise StorageUnavailable("respuesta perdida")
        return original_upload(k, contenido, **kw)

    def chroma(*args, **kw):
        if aplicada:
            retirar(*args, **kw)
        raise RuntimeError("respuesta perdida")

    if punto in ("original", "manifest"):
        monkeypatch.setattr(e.storage, "delete", delete)
    elif punto in ("workspace", "lapida_documental"):
        monkeypatch.setattr(e.storage, "upload", upload)
    else:
        monkeypatch.setattr(indice, "_retirar_verificado", chroma)
    respuesta = borrar(e, doc)
    assert respuesta.status_code == 202 and respuesta.json() == {"cleanup_pending": True}
    assert not e.db.limpieza_documental_completada(ws, doc)
    assert e.cliente.get(f"/api/documents/{doc}", headers=e.headers).status_code == 404
    assert borrar(e, doc).status_code == 404
    monkeypatch.setattr(e.storage, "delete", original_delete)
    monkeypatch.setattr(e.storage, "upload", original_upload)
    monkeypatch.setattr(indice, "_retirar_verificado", retirar)
    debido(e)
    assert e.app.state.documents_effects.reconciliar(doc) == 1
    assert e.db.limpieza_documental_completada(ws, doc)
    chunks_ausentes(e, doc)
    ausencia(e, clave)


def test_purge_conserva_derivados_y_progreso_de_otro_documento(entorno):
    e = entorno
    uno, dos = listo(e), listo(e)
    ws, doc = e.ws["workspace_id"], uno["document_id"]
    for gen, documento in (("gen_uno", doc), ("gen_dos", dos["document_id"])):
        e.db.registrar_generacion(gen, ws, documento, JobStatus.COMPLETED)
        e.db.registrar_job("job_" + gen, ws, "generation", JobStatus.COMPLETED)
        with e.db.transaccion():
            e.db._conn.execute(
                "UPDATE jobs SET generation_id=?, resultado=? WHERE job_id=?",
                (gen, '{"texto":"derivado"}', "job_" + gen),
            )
        e.db.registrar_evento(ws, "ready", JobStatus.COMPLETED, generation_id=gen, job_id="job_" + gen)
        e.storage.upload(clave_output(ws, gen), b"output")
        for formato in ("json", "md", "pdf", "csv", "tsv", "apkg"):
            e.storage.upload(clave_export(ws, gen, formato), b"export")
    progreso = {
        "version": 1,
        "eventos": [
            {"document_id": doc, "dato": 1},
            {"generation_id": "gen_uno"},
            {"document_id": dos["document_id"], "dato": 2},
        ],
        "sin_referencia": {"valor": 3},
    }
    e.db.guardar_progreso_restaurado(ws, progreso)
    e.storage.upload(clave_progress(ws), json.dumps(progreso))
    assert borrar(e, doc).status_code == 204
    assert e.db.obtener_generacion("gen_uno") is None
    assert e.db.obtener_generacion("gen_dos") is not None
    assert e.db.obtener_job("job_gen_uno")["resultado"] is None
    assert e.db.obtener_job("job_gen_dos")["resultado"] is not None
    assert not e.db.eventos_desde("gen_uno")
    assert e.db.eventos_desde("gen_dos")
    ausencia(e, clave_output(ws, "gen_uno"))
    assert e.storage.get(clave_output(ws, "gen_dos")) == b"output"
    for formato in ("json", "md", "pdf", "csv", "tsv", "apkg"):
        ausencia(e, clave_export(ws, "gen_uno", formato))
        assert e.storage.get(clave_export(ws, "gen_dos", formato)) == b"export"
    for estado in (e.db.obtener_progreso_restaurado(ws), json.loads(e.storage.get(clave_progress(ws)))):
        assert estado["eventos"] == [{"document_id": dos["document_id"], "dato": 2}]
        assert estado["sin_referencia"] == {"valor": 3}
    assert e.consulta.chunks(e.ws["token"], dos["document_id"])


def test_lapida_y_obligacion_rollback_atomico(entorno, monkeypatch):
    e = entorno
    doc = listo(e)["document_id"]

    def falla(*args):
        raise sqlite3.OperationalError("sin disco")

    monkeypatch.setattr(e.db, "agendar_efecto_documental", falla)
    respuesta = borrar(e, doc)
    assert respuesta.status_code == 503 and respuesta.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert not e.db.esta_borrado("document", doc)
    assert e.db.obtener_documento(doc)["estado"] == "ready"


def test_nueva_obligacion_invalida_ack_y_ready_completado_es_seleccionado(entorno):
    e = entorno
    datos = listo(e)
    ws, doc = e.ws["workspace_id"], datos["document_id"]
    e.db.agendar_efecto_documental(ws, doc)
    with e.db.transaccion():
        e.db._conn.execute("UPDATE document_cleanup SET estado='completada' WHERE document_id=?", (doc,))
    anterior = dict(e.db._conn.execute("SELECT * FROM document_cleanup WHERE document_id=?", (doc,)).fetchone())
    assert e.db.retirar_documento(e.ws["token"], doc)
    efectos = e.db.listar_efectos_documentales()
    assert any(x["document_id"] == doc and x["job_status"] == "completed" for x in efectos)
    assert not e.db.registrar_resultado_efecto_documental(anterior)
    assert not e.db.limpieza_documental_completada(ws, doc)
    assert e.app.state.documents_effects.reconciliar(doc) == 1


def test_processing_cancelacion_no_publica_ready_y_limpia_despues(entorno, monkeypatch):
    e = entorno
    entro, salir = threading.Event(), threading.Event()
    # Bloqueo del índice real antes de escribir chunks: el worker sigue activo.
    indexar = e.app.state.vectorstore.indexar

    def detenido(*args, **kw):
        entro.set()
        assert salir.wait(10)
        return indexar(*args, **kw)

    monkeypatch.setattr(e.app.state.vectorstore, "indexar", detenido)
    datos = subir(e).json()
    assert entro.wait(10)
    try:
        respuesta = borrar(e, datos["document_id"])
        assert respuesta.status_code == 202 and respuesta.json() == {"cleanup_pending": True}
        assert e.db.obtener_documento(datos["document_id"]) is None
        assert e.db.obtener_job(datos["job_id"])["status"] == "running"
        assert not e.db.limpieza_documental_completada(e.ws["workspace_id"], datos["document_id"])
    finally:
        salir.set()
    assert terminal(e, datos["job_id"])["status"] == "cancelled"
    hasta(lambda: e.gestor._observadores_en_vuelo == 0)
    debido(e)
    e.app.state.documents_effects.reconciliar(datos["document_id"])
    assert e.db.limpieza_documental_completada(e.ws["workspace_id"], datos["document_id"])
    chunks_ausentes(e, datos["document_id"])


def test_admision_en_vuelo_no_recrea_original_despues_de_ack(entorno, monkeypatch):
    e = entorno
    entro, salir = threading.Event(), threading.Event()
    upload = e.storage.upload

    def detenido(clave, contenido, **kw):
        if clave.endswith("/original"):
            entro.set()
            assert salir.wait(10)
        return upload(clave, contenido, **kw)

    monkeypatch.setattr(e.storage, "upload", detenido)
    with ThreadPoolExecutor(1) as pool:
        futuro = pool.submit(subir, e)
        assert entro.wait(10)
        fila = dict(e.db._conn.execute("SELECT * FROM document_admissions").fetchone())
        try:
            assert borrar(e, fila["document_id"]).status_code == 202
            assert e.db.escritura_documental_en_curso(e.ws["workspace_id"], fila["document_id"])
        finally:
            salir.set()
        assert futuro.result(10).status_code != 202
    debido(e)
    e.app.state.documents_effects.reconciliar(fila["document_id"])
    assert e.db.limpieza_documental_completada(e.ws["workspace_id"], fila["document_id"])
    ausencia(e, clave_original(e.ws["workspace_id"], fila["document_id"]))
    ausencia(e, clave_documento(e.ws["workspace_id"], fila["document_id"]))


def test_lapidas_remotas_nuevas_no_son_sobrescritas(entorno):
    e = entorno
    doc = listo(e)["document_id"]
    ws = e.ws["workspace_id"]
    clave = clave_workspace(ws)
    remoto = ManifiestoEspacio.model_validate_json(e.storage.get(clave))
    ahora = datetime.now(timezone.utc) + timedelta(seconds=1)
    otras = [
        Lapida(
            recurso_tipo="document",
            recurso_id="otro_retirado",
            solicitado_en=ahora,
            purga_despues_en=ahora + timedelta(days=30),
        )
    ]
    e.storage.upload(
        clave, remoto.model_copy(update={"version": remoto.version + 20, "recursos_borrados": otras}).model_dump_json()
    )
    assert borrar(e, doc).status_code == 204
    nuevo = ManifiestoEspacio.model_validate_json(e.storage.get(clave))
    assert otras[0] in nuevo.recursos_borrados
    assert any(x.recurso_id == doc for x in nuevo.recursos_borrados)
    assert nuevo.version > remoto.version + 20


def test_observador_y_reconciliador_concurrentes_no_pierden_ack(entorno):
    e = entorno
    doc = listo(e)["document_id"]
    assert e.db.retirar_documento(e.ws["token"], doc)
    with ThreadPoolExecutor(2) as pool:
        resultados = list(pool.map(lambda _: e.app.state.documents_effects.reconciliar(doc), range(2)))
    assert sorted(resultados) == [0, 1]
    assert e.db.limpieza_documental_completada(e.ws["workspace_id"], doc)


def test_sincronizacion_workspace_fallida_no_impide_efecto_documental(entorno, monkeypatch):
    e = entorno
    doc = listo(e)["document_id"]
    assert e.db.retirar_documento(e.ws["token"], doc)
    confirmado = threading.Event()

    def falla(*args):
        raise sqlite3.OperationalError("fallo independiente")

    def aplicar():
        resultado = e.app.state.documents_effects.reconciliar(doc)
        confirmado.set()
        return resultado

    monkeypatch.setattr("app.session.manifests.sincronizar_pendientes", falla)
    sincronizador = SincronizadorManifiestos(e.db, e.storage, intervalo=0.01, efectos_documentales=aplicar)
    sincronizador.iniciar()
    try:
        assert confirmado.wait(10)
    finally:
        sincronizador.detener()
    assert e.db.limpieza_documental_completada(e.ws["workspace_id"], doc)


def test_failed_y_compensacion_previa_completed_se_reabren_para_delete(entorno, monkeypatch):
    e = entorno

    def fallo_parser(*args, **kw):
        raise ValueError("parser interrumpido")

    with monkeypatch.context() as parche:
        parche.setattr("app.documents.ingestion.parsear_archivo", fallo_parser)
        datos = subir(e).json()
        assert terminal(e, datos["job_id"])["status"] == "failed"
        hasta(lambda: e.gestor._observadores_en_vuelo == 0)
    doc = datos["document_id"]
    ws = e.ws["workspace_id"]
    assert e.db.obtener_documento(doc)["estado"] == "failed"
    assert e.db.limpieza_documental_completada(ws, doc)
    assert borrar(e, doc).status_code == 204
    ausencia(e, clave_original(ws, doc))
    assert e.db.limpieza_documental_completada(ws, doc)


def test_lapidas_confirmadas_antes_de_borrar_original_y_no_republica(entorno, monkeypatch):
    e = entorno
    doc = listo(e)["document_id"]
    ws = e.ws["workspace_id"]
    chunks = e.consulta.chunks(e.ws["token"], doc)
    delete = e.storage.delete
    comprobado = []

    def verificar(clave):
        if clave == clave_original(ws, doc):
            assert ManifiestoDocumento.model_validate_json(e.storage.get(clave_documento(ws, doc))).borrado
            espacio = ManifiestoEspacio.model_validate_json(e.storage.get(clave_workspace(ws)))
            assert any(x.recurso_tipo == "document" and x.recurso_id == doc for x in espacio.recursos_borrados)
            assert e.db.esta_borrado("document", doc)
            comprobado.append(True)
        return delete(clave)

    monkeypatch.setattr(e.storage, "delete", verificar)
    assert borrar(e, doc).status_code == 204 and comprobado == [True]
    with pytest.raises(TrabajoCanceladoError):
        e.app.state.vectorstore.indexar(ws, doc, chunks, contexto=e.consulta._contexto(e.ws["token"], ws))
    with pytest.raises(ValueError):
        e.db.registrar_generacion("gen_tardia", ws, doc)


@pytest.mark.parametrize("aplicada", [False, True])
def test_progress_cas_y_respuesta_perdida_no_pierden_otras_referencias(entorno, monkeypatch, aplicada):
    e = entorno
    uno, otro = listo(e), listo(e)
    doc, ws = uno["document_id"], e.ws["workspace_id"]
    clave = clave_progress(ws)
    e.storage.upload(
        clave, json.dumps({"version": 1, "eventos": [{"document_id": doc}, {"document_id": otro["document_id"]}]})
    )
    upload = e.storage.upload

    def perdido(k, contenido, **kw):
        if k == clave:
            if aplicada:
                upload(k, contenido, **kw)
            else:
                # Cambio concurrente invalida el ETag de la primera lectura.
                actual = json.loads(e.storage.get(k))
                actual["concurrente"] = 42
                upload(k, json.dumps(actual))
                return upload(k, contenido, **kw)
            raise StorageUnavailable("respuesta perdida")
        return upload(k, contenido, **kw)

    monkeypatch.setattr(e.storage, "upload", perdido)
    assert borrar(e, doc).status_code == 202
    monkeypatch.setattr(e.storage, "upload", upload)
    debido(e)
    assert e.app.state.documents_effects.reconciliar(doc) == 1
    estado = json.loads(e.storage.get(clave))
    assert estado["eventos"] == [{"document_id": otro["document_id"]}]
    if not aplicada:
        assert estado["concurrente"] == 42


def test_fallo_secundario_de_ack_conserva_seguimiento_y_reintento(entorno, monkeypatch):
    e = entorno
    doc = listo(e)["document_id"]
    ack = e.db.registrar_resultado_efecto_documental

    def fallar(*args, **kw):
        raise sqlite3.OperationalError("fallo secundario")

    monkeypatch.setattr(e.db, "registrar_resultado_efecto_documental", fallar)
    assert borrar(e, doc).status_code == 202
    assert e.db.listar_efectos_documentales()[0]["document_id"] == doc
    monkeypatch.setattr(e.db, "registrar_resultado_efecto_documental", ack)
    assert e.app.state.documents_effects.reconciliar(doc) == 1
    assert e.db.limpieza_documental_completada(e.ws["workspace_id"], doc)


def test_reinicio_con_sqlite_conservada_reintenta_delete_sin_embeddings(tmp_path, monkeypatch):
    ajustes = Configuracion(
        app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path), embedding_dimensions=16
    )
    doble = DobleGemini(16)
    cuotas = CuotasProveedor({ajustes.gemini_embedding_model: CuotasModelo(1000, 1000000, 1000)})
    app = crear_app(ajustes, proveedor_embeddings=doble, cuotas_proveedor=cuotas)
    with TestClient(app) as cliente:
        ws = cliente.post("/api/workspaces").json()
        headers = {"Authorization": f"Bearer {ws['token']}"}
        datos = cliente.post(
            "/api/documents/upload",
            headers=headers,
            json={
                "documento_titulo": "Redes",
                "documento_contenido": "Una red privada conserva rutas y documentos aislados.\n" * 30,
            },
        ).json()
        hasta(lambda: app.state.db.obtener_job(datos["job_id"])["status"] == "completed")
        storage = app.state.storage_provider
        delete = storage.delete

        def perdida(clave):
            raise StorageUnavailable("sin respuesta")

        with monkeypatch.context() as parche:
            parche.setattr(storage, "delete", perdida)
            assert cliente.delete(f"/api/documents/{datos['document_id']}", headers=headers).status_code == 202
        with app.state.db.transaccion():
            app.state.db._conn.execute("UPDATE document_cleanup SET proximo_intento_en=NULL")
        llamadas = doble.llamadas_embeddings
        assert callable(delete)
    app = crear_app(ajustes, proveedor_embeddings=doble, cuotas_proveedor=cuotas)
    with TestClient(app) as cliente:
        assert app.state.db.limpieza_documental_completada(ws["workspace_id"], datos["document_id"])
        assert app.state.db.esta_borrado("document", datos["document_id"])
        assert cliente.get(f"/api/documents/{datos['document_id']}", headers=headers).status_code == 404
        with pytest.raises(StorageNotFound):
            app.state.storage_provider.get(clave_original(ws["workspace_id"], datos["document_id"]))
        assert doble.llamadas_embeddings == llamadas


@pytest.mark.parametrize("estado", list(DocumentStatus))
def test_documento_legacy_sin_job_tambien_se_limpia(entorno, estado):
    e = entorno
    doc, ws, contenido = "doc_legacy", e.ws["workspace_id"], b"original legacy"
    huella = hashlib.sha256(contenido).hexdigest()
    e.db.registrar_documento(doc, ws, "Legacy", estado, huella)
    manifest = ManifiestoDocumento(workspace_id=ws, document_id=doc, source_name="legacy.txt", hash_sha256=huella)
    e.storage.upload(clave_original(ws, doc), contenido)
    e.storage.upload(clave_documento(ws, doc), manifest.model_dump_json())
    assert borrar(e, doc).status_code == 204
    assert e.db.limpieza_documental_completada(ws, doc)
    ausencia(e, clave_original(ws, doc))


def test_sincronizador_no_descarta_lapida_remota_mas_reciente(entorno):
    e = entorno
    doc, ws = listo(e)["document_id"], e.ws["workspace_id"]
    clave = clave_workspace(ws)
    manifest = ManifiestoEspacio.model_validate_json(e.storage.get(clave))
    futuro = datetime.now(timezone.utc) + timedelta(days=1)
    lapida = Lapida(
        recurso_tipo="document", recurso_id=doc, solicitado_en=futuro, purga_despues_en=futuro + timedelta(days=30)
    )
    e.storage.upload(clave, manifest.model_copy(update={"recursos_borrados": [lapida]}).model_dump_json())
    # Aun si la versión local ya es mayor, no se puede perder la lápida.
    e.db.registrar_actividad(ws, 30, intervalo_segundos=0)
    with pytest.raises(StorageConflict):
        sincronizar_manifiesto(e.db, e.storage, ws)
    assert borrar(e, doc).status_code == 204
    sincronizar_manifiesto(e.db, e.storage, ws)
    assert lapida in ManifiestoEspacio.model_validate_json(e.storage.get(clave)).recursos_borrados


def test_carrera_etag_lapida_concurrente_se_conserva_al_reintentar(entorno, monkeypatch):
    e = entorno
    doc, ws = listo(e)["document_id"], e.ws["workspace_id"]
    clave = clave_workspace(ws)
    upload = e.storage.upload
    ahora = datetime.now(timezone.utc)
    lapida = Lapida(
        recurso_tipo="document",
        recurso_id="retirada_concurrente",
        solicitado_en=ahora,
        purga_despues_en=ahora + timedelta(days=30),
    )

    def concurrente(k, contenido, **kw):
        if k == clave:
            actual = ManifiestoEspacio.model_validate_json(e.storage.get(k))
            upload(
                k,
                actual.model_copy(
                    update={"version": actual.version + 10, "recursos_borrados": [lapida]}
                ).model_dump_json(),
            )
        return upload(k, contenido, **kw)

    monkeypatch.setattr(e.storage, "upload", concurrente)
    assert borrar(e, doc).status_code == 202
    assert e.storage.get(clave_original(ws, doc))
    monkeypatch.setattr(e.storage, "upload", upload)
    debido(e)
    assert e.app.state.documents_effects.reconciliar(doc) == 1
    assert lapida in ManifiestoEspacio.model_validate_json(e.storage.get(clave)).recursos_borrados


def test_lapida_remota_ajena_no_bloquea_ni_elimina_otro_workspace(entorno):
    e = entorno
    doc, ws = listo(e)["document_id"], e.ws["workspace_id"]
    otro = e.cliente.post("/api/workspaces").json()
    e.db.registrar_documento("doc_ajeno", otro["workspace_id"], "Ajeno", DocumentStatus.READY, "b" * 64)
    original_ajeno = clave_original(otro["workspace_id"], "doc_ajeno")
    e.storage.upload(original_ajeno, b"otro documento")
    clave = clave_workspace(ws)
    manifest = ManifiestoEspacio.model_validate_json(e.storage.get(clave))
    ahora = datetime.now(timezone.utc)
    lapida = Lapida(
        recurso_tipo="document",
        recurso_id="doc_ajeno",
        solicitado_en=ahora,
        purga_despues_en=ahora + timedelta(days=30),
    )
    e.storage.upload(
        clave,
        manifest.model_copy(update={"version": manifest.version + 1, "recursos_borrados": [lapida]}).model_dump_json(),
    )
    assert borrar(e, doc).status_code == 202
    assert e.db.obtener_documento("doc_ajeno")["workspace_id"] == otro["workspace_id"]
    assert not e.db.esta_borrado("document", "doc_ajeno")
    assert e.storage.get(original_ajeno) == b"otro documento"
    assert not e.db.limpieza_documental_completada(ws, doc)


def test_generacion_activa_vinculada_por_fk_se_cancela_sin_publicar(entorno):
    e = entorno
    doc, ws = listo(e)["document_id"], e.ws["workspace_id"]
    e.db.registrar_generacion("gen_activa", ws, doc)
    entro, salir = threading.Event(), threading.Event()

    def generacion(contexto):
        entro.set()
        assert salir.wait(10)
        contexto.chequear()
        return {"texto": "no debe publicarse"}

    job = e.gestor.enqueue(ws, "generation", generacion, generation_id="gen_activa")
    assert entro.wait(10)
    try:
        assert e.db.obtener_job(job)["document_id"] is None
        assert borrar(e, doc).status_code == 202
        assert e.db.obtener_generacion("gen_activa") is None
    finally:
        salir.set()
    assert terminal(e, job)["status"] == "cancelled"
    assert e.db.obtener_job(job)["resultado"] is None
    debido(e)
    assert e.app.state.documents_effects.reconciliar(doc) == 1
    assert e.db.limpieza_documental_completada(ws, doc)


def test_job_encolado_cancelado_no_espera_ni_afecta_worker_ajeno(entorno):
    e = entorno
    doc, ws = listo(e)["document_id"], e.ws["workspace_id"]
    otro = e.cliente.post("/api/workspaces").json()
    entro, salir = threading.Event(), threading.Event()

    def ajeno(contexto):
        entro.set()
        assert salir.wait(10)
        contexto.chequear()
        return {"ajeno": True}

    job_ajeno = e.gestor.enqueue(otro["workspace_id"], "chat", ajeno)
    assert entro.wait(10)
    ejecuciones = []
    try:
        e.db.registrar_generacion("gen_encolada", ws, doc)
        job = e.gestor.enqueue(
            ws, "generation", lambda contexto: ejecuciones.append(True), generation_id="gen_encolada"
        )
        assert e.db.obtener_job(job)["status"] == "queued"
        assert borrar(e, doc).status_code == 204
        assert e.db.obtener_job(job)["status"] == "cancelled"
        assert e.db.obtener_job(job_ajeno)["status"] == "running"
        assert not ejecuciones
    finally:
        salir.set()
    assert terminal(e, job_ajeno)["status"] == "completed"


@pytest.mark.parametrize("fallo_storage", [False, True])
def test_delete_http_simultaneo_confirma_una_sola_retirada(entorno, monkeypatch, fallo_storage):
    e = entorno
    datos = listo(e)
    doc, ws = datos["document_id"], e.ws["workspace_id"]
    chunks = e.consulta.chunks(e.ws["token"], doc)
    embeddings = e.doble.llamadas_embeddings
    indice = e.app.state.vectorstore
    barrera = threading.Barrier(2)
    entro, liberar = threading.Event(), threading.Event()
    retirar_documento = e.db.retirar_documento
    retirar_indice = indice._retirar_verificado
    eliminar = e.storage.delete
    original, manifiesto = clave_original(ws, doc), clave_documento(ws, doc)
    borrados, retiros = [], []

    def admision_simultanea(token, document_id):
        barrera.wait(timeout=10)
        return retirar_documento(token, document_id)

    def indice_pausado(*args, **kw):
        retiros.append((args, kw))
        if not entro.is_set():
            entro.set()
            assert liberar.wait(10)
        return retirar_indice(*args, **kw)

    def delete(clave):
        borrados.append(clave)
        if fallo_storage and clave == original and borrados.count(original) == 1:
            raise StorageUnavailable("Fallo antes de eliminar el original")
        return eliminar(clave)

    monkeypatch.setattr(e.db, "retirar_documento", admision_simultanea)
    monkeypatch.setattr(indice, "_retirar_verificado", indice_pausado)
    monkeypatch.setattr(e.storage, "delete", delete)
    with ThreadPoolExecutor(2) as pool:
        peticiones = [pool.submit(borrar, e, doc) for _ in range(2)]
        try:
            assert entro.wait(10)
            hasta(lambda: any(p.done() for p in peticiones))
            terminada = next(p for p in peticiones if p.done())
            assert terminada.result(timeout=10).status_code == 404
            assert not borrados
            for ruta in (
                f"/api/documents/{doc}",
                f"/api/documents/{doc}/sources/{chunks[0].chunk_id}",
                datos["status_url"],
                datos["events_url"],
            ):
                respuesta = e.cliente.get(ruta, headers=e.headers)
                assert respuesta.status_code == 404 and respuesta.json()["error"]["code"] == "NOT_FOUND"
            assert e.cliente.get("/api/documents", headers=e.headers).json()["items"] == []
            lapidas = e.db._conn.execute(
                "SELECT * FROM pending_deletes WHERE recurso_tipo='document' AND recurso_id=?", (doc,)
            ).fetchall()
            obligaciones = e.db._conn.execute("SELECT * FROM document_cleanup WHERE document_id=?", (doc,)).fetchall()
            assert len(lapidas) == len(obligaciones) == 1
            assert obligaciones[0]["estado"] == "pendiente"
            solicitud = obligaciones[0]["solicitado_en"]
        finally:
            liberar.set()
        respuestas = [p.result(timeout=10) for p in peticiones]
    assert sorted(r.status_code for r in respuestas) == [202 if fallo_storage else 204, 404]
    aceptada = next(r for r in respuestas if r.status_code != 404)
    if fallo_storage:
        assert aceptada.json() == {"cleanup_pending": True}
        assert not e.db.limpieza_documental_completada(ws, doc)
        debido(e)
        assert e.app.state.documents_effects.reconciliar(doc) == 1
    else:
        assert aceptada.content == b""
    assert e.db.limpieza_documental_completada(ws, doc)
    assert e.db._conn.execute("SELECT COUNT(*) FROM document_cleanup WHERE document_id=?", (doc,)).fetchone()[0] == 1
    assert (
        e.db._conn.execute("SELECT solicitado_en FROM document_cleanup WHERE document_id=?", (doc,)).fetchone()[0]
        == solicitud
    )
    remoto = ManifiestoEspacio.model_validate_json(e.storage.get(clave_workspace(ws)))
    assert (
        sum(lapida.recurso_tipo == "document" and lapida.recurso_id == doc for lapida in remoto.recursos_borrados) == 1
    )
    assert borrados.count(original) == (2 if fallo_storage else 1)
    assert borrados.count(manifiesto) == 1
    assert all(args == (ws, doc) and kw == {"definitivo": True} for args, kw in retiros)
    chunks_ausentes(e, doc)
    ausencia(e, original)
    ausencia(e, manifiesto)
    with pytest.raises(TrabajoCanceladoError):
        indice.indexar(ws, doc, chunks, contexto=e.consulta._contexto(e.ws["token"], ws))
    assert e.db.obtener_documento(doc) is None
    assert e.db.obtener_job(datos["job_id"])["status"] == "completed"
    assert e.db._conn.execute("SELECT COUNT(*) FROM jobs WHERE document_id=?", (doc,)).fetchone()[0] == 1
    assert e.doble.llamadas_embeddings == embeddings
    previo = list(borrados)
    assert e.app.state.documents_effects.reconciliar(doc) == 0
    assert borrados == previo


def test_delete_commit_aplicado_con_error_conserva_retirada_y_sincroniza(entorno, monkeypatch):
    e = entorno
    datos = listo(e)
    doc, ws = datos["document_id"], e.ws["workspace_id"]
    chunks = e.consulta.chunks(e.ws["token"], doc)
    embeddings = e.doble.llamadas_embeddings
    original, manifiesto = clave_original(ws, doc), clave_documento(ws, doc)

    class ConexionConRespuestaPerdida:
        def __init__(self, conexion):
            self.conexion = conexion
            self.commit_aplicado = False

        def __getattr__(self, nombre):
            return getattr(self.conexion, nombre)

        def execute(self, sql, *args, **kw):
            resultado = self.conexion.execute(sql, *args, **kw)
            if sql == "COMMIT" and not self.commit_aplicado:
                self.commit_aplicado = True
                assert not self.conexion.in_transaction
                raise sqlite3.OperationalError("Respuesta perdida después del commit aplicado")
            return resultado

    conexion = ConexionConRespuestaPerdida(e.db._conn)
    with monkeypatch.context() as parche:
        parche.setattr(e.db, "_conn", conexion)
        respuesta = borrar(e, doc)
    assert conexion.commit_aplicado and not e.db._conn.in_transaction
    assert respuesta.status_code == 503
    assert set(respuesta.json()) == {"error", "request_id"}
    assert respuesta.json()["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert respuesta.json()["request_id"] == respuesta.headers["X-Request-ID"]
    assert "cleanup_pending" not in respuesta.json()
    assert "commit aplicado" not in respuesta.text
    # Otra conexión comprueba persistencia real, no estado de la conexión que falló.
    with sqlite3.connect(e.db.ruta_db) as persistida:
        assert (
            persistida.execute(
                "SELECT COUNT(*) FROM pending_deletes WHERE recurso_tipo='document' AND recurso_id=?", (doc,)
            ).fetchone()[0]
            == 1
        )
        obligaciones = persistida.execute(
            "SELECT estado, solicitado_en FROM document_cleanup WHERE document_id=? AND workspace_id=?", (doc, ws)
        ).fetchall()
        assert len(obligaciones) == 1 and obligaciones[0][0] == "pendiente"
        solicitud = obligaciones[0][1]
    assert e.storage.get(original)
    assert e.storage.get(manifiesto)
    for ruta in (
        f"/api/documents/{doc}",
        f"/api/documents/{doc}/sources/{chunks[0].chunk_id}",
        datos["status_url"],
        datos["events_url"],
    ):
        assert e.cliente.get(ruta, headers=e.headers).status_code == 404
    assert e.cliente.get("/api/documents", headers=e.headers).json()["items"] == []
    assert borrar(e, doc).status_code == 404
    assert (
        e.db._conn.execute("SELECT solicitado_en FROM document_cleanup WHERE document_id=?", (doc,)).fetchone()[0]
        == solicitud
    )
    confirmado = threading.Event()

    def aplicar():
        resultado = e.app.state.documents_effects.reconciliar(doc)
        if e.db.limpieza_documental_completada(ws, doc):
            confirmado.set()
        return resultado

    sincronizador = SincronizadorManifiestos(e.db, e.storage, intervalo=0.01, efectos_documentales=aplicar)
    sincronizador.iniciar()
    try:
        assert confirmado.wait(10)
    finally:
        sincronizador.detener()
    assert e.db.limpieza_documental_completada(ws, doc)
    chunks_ausentes(e, doc)
    ausencia(e, original)
    ausencia(e, manifiesto)
    remoto = ManifiestoEspacio.model_validate_json(e.storage.get(clave_workspace(ws)))
    assert (
        sum(lapida.recurso_tipo == "document" and lapida.recurso_id == doc for lapida in remoto.recursos_borrados) == 1
    )
    assert e.db.obtener_documento(doc) is None
    assert e.doble.llamadas_embeddings == embeddings
