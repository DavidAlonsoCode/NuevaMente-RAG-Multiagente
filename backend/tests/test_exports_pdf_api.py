"""#44: PDF en el HTTP real, persistencia previa a completed y recuperación sin IA."""

from io import BytesIO

import pytest
import test_api_generate as generaciones
from app.exports.pdf import renderizar_pdf
from app.storage.object_keys import clave_export
from app.storage.provider import StorageUnavailable
from pypdf import PdfReader
from test_api_generate import esperar, generar

pytestmark = pytest.mark.integration_mock
entorno = generaciones.entorno


@pytest.mark.parametrize("tipo", ["flashcards", "quiz", "tutorial", "resumen_ejecutivo", "guion_clase"])
def test_pdf_cacheado_al_completar_headers_y_sin_nuevas_llamadas(entorno, tipo, monkeypatch):
    e = entorno
    recibo = generar(e, formato_salida=tipo).json()
    esperar(e, recibo)
    gid, ws = recibo["generation_id"], e.ws["workspace_id"]
    paquete = e.app.state.generations.canonico(e.ws["token"], gid)
    esperado = renderizar_pdf(paquete)
    assert e.storage.get(clave_export(ws, gid, "pdf")) == esperado
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    upload = e.storage.upload

    def prohibir(clave, *args, **kwargs):
        if clave.startswith("exports/"):
            pytest.fail("completed debe haber confirmado el PDF")
        return upload(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", prohibir)
    respuesta = e.cliente.get("/api/exports/" + gid, params={"format": "pdf"}, headers=e.headers)
    assert respuesta.status_code == 200 and respuesta.content == esperado
    assert respuesta.headers["content-type"] == "application/pdf"
    assert respuesta.headers["content-disposition"] == f'attachment; filename="{gid}.pdf"'
    assert respuesta.headers["cache-control"] == "private, no-store"
    assert respuesta.headers["x-request-id"] and respuesta.headers["x-content-type-options"] == "nosniff"
    texto = "\n".join(p.extract_text() for p in PdfReader(BytesIO(respuesta.content)).pages)
    assert e.ws["token"] not in texto and e.ws["recovery_code"] not in texto
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    otro = e.cliente.post("/api/workspaces").json()
    assert (
        e.cliente.get(
            "/api/exports/" + gid, params={"format": "pdf"}, headers={"Authorization": "Bearer " + otro["token"]}
        ).status_code
        == 404
    )


def test_fallo_pdf_y_persist_sin_repetir_ia(entorno, monkeypatch):
    e = entorno
    upload = e.storage.upload

    def fallar(clave, *args, **kwargs):
        if clave.startswith("exports/") and clave.endswith("/pdf"):
            raise StorageUnavailable("detalle privado")
        return upload(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", fallar)
    recibo = generar(e).json()
    fallo = esperar(e, recibo, "failed")
    assert fallo["error"]["code"] == "STORAGE_UNAVAILABLE" and fallo["contenido"] is None
    assert e.db.aprobado_retenido(recibo["generation_id"])
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    monkeypatch.setattr(e.storage, "upload", upload)
    assert e.cliente.post(recibo["status_url"] + "/persist", headers=e.headers).status_code == 202
    esperar(e, recibo)
    assert (
        e.cliente.get(
            "/api/exports/" + recibo["generation_id"], params={"format": "pdf"}, headers=e.headers
        ).status_code
        == 200
    )
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
