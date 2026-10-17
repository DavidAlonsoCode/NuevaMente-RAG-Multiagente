"""#45: descargas HTTP y recuperación de la persistencia, con proveedores dobles."""

import pytest
import test_api_generate as generaciones
from app.exports.router import renderizar
from app.storage.object_keys import clave_export, clave_output
from app.storage.provider import StorageUnavailable
from test_api_generate import esperar, generar

pytestmark = pytest.mark.integration_mock
entorno = generaciones.entorno


def test_cinco_descargas_flashcards_preparadas_antes_de_completed(entorno, monkeypatch):
    e = entorno
    recibo = generar(e).json()
    esperar(e, recibo)
    gid, ws = recibo["generation_id"], e.ws["workspace_id"]
    paquete = e.app.state.generations.canonico(e.ws["token"], gid)
    canonico = e.storage.get(clave_output(ws, gid))
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    upload = e.storage.upload

    def sin_escritura(clave, *args, **kwargs):
        if clave.startswith("exports/"):
            pytest.fail("La exportación debe existir antes de completed")
        return upload(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", sin_escritura)
    for formato, tipo in (
        ("json", "application/json"),
        ("md", "text/markdown"),
        ("csv", "text/csv"),
        ("tsv", "text/tab-separated-values"),
        ("apkg", "application/octet-stream"),
    ):
        respuesta = e.cliente.get("/api/exports/" + gid, params={"format": formato}, headers=e.headers)
        assert respuesta.status_code == 200
        assert (
            respuesta.content == renderizar(paquete, canonico, formato) == e.storage.get(clave_export(ws, gid, formato))
        )
        assert respuesta.headers["content-type"].startswith(tipo)
        assert respuesta.headers["content-disposition"] == f'attachment; filename="{gid}.{formato}"'
        assert respuesta.headers["cache-control"] == "private, no-store"
        assert respuesta.headers["x-request-id"] and respuesta.headers["x-content-type-options"] == "nosniff"
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)


def test_fallo_apkg_y_persist_sin_repetir_ia(entorno, monkeypatch):
    e = entorno
    upload = e.storage.upload

    def fallar(clave, *args, **kwargs):
        if clave.startswith("exports/") and clave.endswith("/apkg"):
            raise StorageUnavailable("detalle privado")
        return upload(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", fallar)
    recibo = generar(e).json()
    fallo = esperar(e, recibo, "failed")
    assert fallo["error"]["code"] == "STORAGE_UNAVAILABLE"
    assert e.db.aprobado_retenido(recibo["generation_id"])
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    monkeypatch.setattr(e.storage, "upload", upload)
    assert e.cliente.post(recibo["status_url"] + "/persist", headers=e.headers).status_code == 202
    esperar(e, recibo)
    assert (
        e.cliente.get(
            "/api/exports/" + recibo["generation_id"], params={"format": "apkg"}, headers=e.headers
        ).status_code
        == 200
    )
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
