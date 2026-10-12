"""#43: HTTP, StateGraph, persistencia y retirada reales; proveedores simulados."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import test_api_generate as generaciones
from app.exports.markdown import renderizar_markdown
from app.storage.object_keys import clave_export, clave_output, clave_workspace
from app.storage.provider import StorageNotFound, StorageUnavailable
from test_api_generate import esperar, generar

pytestmark = pytest.mark.integration_mock
entorno = generaciones.entorno


def descargar(e, gid, formato="json", headers=None):
    return e.cliente.get("/api/exports/" + gid, params={"format": formato}, headers=headers or e.headers)


@pytest.mark.parametrize("tipo", ["flashcards", "quiz", "tutorial", "resumen_ejecutivo", "guion_clase"])
def test_json_md_cinco_formatos_cache_al_completar_sin_llm_ni_cuotas_nuevas(entorno, tipo, monkeypatch):
    e = entorno
    recibo = generar(e, formato_salida=tipo).json()
    esperar(e, recibo)
    gid, ws = recibo["generation_id"], e.ws["workspace_id"]
    canonico = e.storage.get(clave_output(ws, gid))
    paquete = e.app.state.generations.canonico(e.ws["token"], gid)
    assert e.storage.get(clave_export(ws, gid, "json")) == canonico
    assert e.storage.get(clave_export(ws, gid, "md")) == renderizar_markdown(paquete)
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    upload = e.storage.upload

    def prohibir_derivados(clave, *args, **kwargs):
        if clave.startswith("exports/"):
            pytest.fail("completed ya debe conservar ambas exportaciones")
        return upload(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", prohibir_derivados)
    for formato in ("json", "md"):
        respuesta = descargar(e, gid, formato)
        assert respuesta.status_code == 200, respuesta.text
        assert respuesta.headers["content-disposition"] == f'attachment; filename="{gid}.{formato}"'
        assert respuesta.headers["cache-control"] == "private, no-store"
        assert respuesta.headers["x-content-type-options"] == "nosniff" and respuesta.headers["x-request-id"]
        assert respuesta.headers["content-type"].startswith(
            "application/json" if formato == "json" else "text/markdown"
        )
        assert e.ws["token"] not in respuesta.text and e.ws["recovery_code"] not in respuesta.text
        assert "GITHUB_TOKEN" not in respuesta.text and "GEMINI_API_KEY" not in respuesta.text
        assert respuesta.content == (canonico if formato == "json" else renderizar_markdown(paquete))
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)


@pytest.mark.parametrize("idioma,soluciones", [("es", "Soluciones"), ("en", "Solutions"), ("pt", "Soluções")])
def test_quiz_preguntas_antes_de_soluciones_en_idioma_del_paquete(entorno, idioma, soluciones):
    e = entorno
    recibo = generar(e, formato_salida="quiz", idioma_salida=idioma).json()
    esperar(e, recibo)
    gid = recibo["generation_id"]
    paquete = e.app.state.generations.canonico(e.ws["token"], gid)
    md = descargar(e, gid, "md")
    assert md.status_code == 200
    antes, despues = md.text.split("## " + soluciones, 1)
    from app.exports.markdown import texto

    for pregunta in paquete.contenido_adaptado.preguntas:
        assert texto(pregunta.enunciado) in antes
        assert texto(pregunta.justificacion) not in antes and texto(pregunta.justificacion) in despues
    assert "correct_option_id" in descargar(e, gid).text  # Canónico completo, incluso sin responder el quiz.


def test_incompatibles_422_ajenos_404_y_sesion_requerida(entorno):
    e = entorno
    recibo = generar(e, formato_salida="tutorial").json()
    esperar(e, recibo)
    for formato in ("csv", "tsv", "apkg", "desconocido", "../md"):
        respuesta = descargar(e, recibo["generation_id"], formato)
        assert respuesta.status_code == 422 and respuesta.json()["error"]["code"] == "EXPORT_INCOMPATIBLE"
    otro = e.cliente.post("/api/workspaces").json()
    ajeno = {"Authorization": "Bearer " + otro["token"]}
    for formato in ("json", "md", "csv"):
        assert descargar(e, recibo["generation_id"], formato, ajeno).status_code == 404
    assert e.cliente.get("/api/exports/" + recibo["generation_id"]).status_code == 401


def test_no_aprobados_409_y_rechazados_no_publican_derivados(entorno):
    e = entorno
    e.doble.bloquear = threading.Event()
    recibo = generar(e).json()
    gid = recibo["generation_id"]
    assert descargar(e, gid, "json").status_code == descargar(e, gid, "md").status_code == 409
    e.cliente.post(recibo["cancel_url"], headers=e.headers)
    e.doble.bloquear.set()
    esperar(e, recibo, "cancelled")
    assert descargar(e, gid, "md").status_code == 409
    assert not e.storage.list("exports/" + e.ws["workspace_id"] + "/" + gid + "/").items
    e.doble.bloquear, e.doble.rechazar = None, True
    rechazado = generar(e).json()
    esperar(e, rechazado, "rejected_quality")
    assert descargar(e, rechazado["generation_id"], "md").status_code == 409
    assert not e.storage.list("exports/" + e.ws["workspace_id"] + "/" + rechazado["generation_id"] + "/").items


def test_fallo_al_guardar_md_retiene_aprobado_y_persist_no_repite_llm(entorno, monkeypatch):
    e = entorno
    upload = e.storage.upload

    def fallar(clave, *args, **kwargs):
        if clave.startswith("exports/") and clave.endswith("/md"):
            raise StorageUnavailable("detalle privado del almacenamiento")
        return upload(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", fallar)
    recibo = generar(e).json()
    gid, ws = recibo["generation_id"], e.ws["workspace_id"]
    fallo = esperar(e, recibo, "failed")
    assert fallo["error"]["code"] == "STORAGE_UNAVAILABLE" and fallo["contenido"] is None
    assert descargar(e, gid, "json").status_code == descargar(e, gid, "md").status_code == 409
    retenido = e.db.aprobado_retenido(gid)
    assert retenido and e.storage.get(clave_output(ws, gid)) == retenido.encode()
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    monkeypatch.setattr(e.storage, "upload", upload)
    persist = e.cliente.post(recibo["status_url"] + "/persist", headers=e.headers)
    assert persist.status_code == 202, persist.text
    esperar(e, recibo)
    assert descargar(e, gid, "md").status_code == 200
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)


def test_cache_sin_runtime_de_ia_y_borrado_retira_derivados(entorno):
    from app.generations.service import ServicioGeneraciones

    e = entorno
    recibo = generar(e).json()
    esperar(e, recibo)
    gid = recibo["generation_id"]
    indice = e.app.state.documents_query.indice
    e.app.state.documents_query.indice = None
    e.app.state.generations = ServicioGeneraciones(
        e.db, e.storage, e.app.state.documents_query, e.gestor, e.app.state.config
    )
    try:
        assert descargar(e, gid, "md").status_code == descargar(e, gid).status_code == 200
    finally:
        e.app.state.documents_query.indice = indice
    assert e.cliente.delete("/api/documents/" + e.doc, headers=e.headers).status_code in (204, 202)
    e.app.state.documents_effects.reconciliar()
    assert descargar(e, gid, "md").status_code == 404
    assert not e.storage.list("exports/" + e.ws["workspace_id"] + "/" + gid + "/").items


def test_revocacion_remota_y_derivado_ajeno_no_se_entregan(entorno):
    e = entorno
    recibo = generar(e).json()
    esperar(e, recibo)
    gid, ws = recibo["generation_id"], e.ws["workspace_id"]
    e.storage.upload(clave_export(ws, gid, "md"), b"secreto ajeno")
    respuesta = descargar(e, gid, "md")
    assert respuesta.status_code == 503 and "secreto ajeno" not in respuesta.text
    manifest = json.loads(e.storage.get(clave_workspace(ws)))
    manifest["recursos_borrados"] = [
        {
            "recurso_tipo": "generation",
            "recurso_id": gid,
            "solicitado_en": "2026-10-12T00:00:00Z",
            "purga_despues_en": "2026-11-12T00:00:00Z",
        }
    ]
    e.storage.upload(clave_workspace(ws), json.dumps(manifest))
    assert descargar(e, gid, "json").status_code == descargar(e, gid, "md").status_code == 404


def test_legacy_regenera_caches_desde_canonico_sin_llm(entorno):
    e = entorno
    recibo = generar(e).json()
    esperar(e, recibo)
    gid, ws = recibo["generation_id"], e.ws["workspace_id"]
    for formato in ("json", "md"):
        e.storage.delete(clave_export(ws, gid, formato))
        with pytest.raises(StorageNotFound):
            e.storage.get(clave_export(ws, gid, formato))
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    assert descargar(e, gid, "json").status_code == descargar(e, gid, "md").status_code == 200
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)


def test_borrado_durante_escritura_legacy_no_entrega_ni_recrea_derivados(entorno, monkeypatch):
    e = entorno
    assert e.app.state.generations.exports._lock is e.app.state.documents_effects._lock
    recibo = generar(e).json()
    esperar(e, recibo)
    gid, ws = recibo["generation_id"], e.ws["workspace_id"]
    clave = clave_export(ws, gid, "md")
    e.storage.delete(clave)
    entro, continuar = threading.Event(), threading.Event()
    upload = e.storage.upload

    def demorar(objeto, *args, **kwargs):
        if objeto == clave:
            entro.set()
            assert continuar.wait(10)
        return upload(objeto, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", demorar)
    with ThreadPoolExecutor(2) as pool:
        descarga = pool.submit(descargar, e, gid, "md")
        try:
            assert entro.wait(5)
            borrado = pool.submit(e.cliente.delete, "/api/documents/" + e.doc, headers=e.headers)
            limite = time.monotonic() + 5
            while not e.db.esta_borrado("document", e.doc) and time.monotonic() < limite:
                time.sleep(0.01)
            assert e.db.esta_borrado("document", e.doc)
        finally:
            continuar.set()
        assert descarga.result(10).status_code == 404
        assert borrado.result(10).status_code in (204, 202)
    e.app.state.documents_effects.reconciliar()
    assert not e.storage.list("exports/" + ws + "/" + gid + "/").items
