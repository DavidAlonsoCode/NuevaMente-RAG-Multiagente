"""Multipart de la UI aceptado por la API real (#25/#44)."""

import pytest
from test_api_generate import esperar

pytestmark = pytest.mark.integration_mock


@pytest.mark.parametrize("extension,mime", [("txt", "text/plain"), ("md", "text/markdown"), ("pdf", "application/pdf")])
def test_cliente_frontend_upload_con_titulo_y_backend_real(entorno, crear_pdf, extension, mime):
    e = entorno

    texto = "Una VCN conecta subredes y mantiene los recursos aislados. " * 30
    contenido = crear_pdf(texto=texto).read_bytes() if extension == "pdf" else texto.encode("utf-8")
    respuesta = e.cliente.post(
        "/api/documents/upload",
        headers=e.headers | {"Idempotency-Key": "ui-upload"},
        files={"file": ("redes." + extension, contenido, mime)},
        data={"titulo": "Título desde la UI"},
    )
    assert respuesta.status_code == 202, respuesta.text
    recibo = respuesta.json()
    esperar(e, recibo)
    documento = e.cliente.get("/api/documents/" + recibo["document_id"], headers=e.headers)
    assert documento.status_code == 200
    assert documento.json()["titulo"] == "Título desde la UI"
    assert documento.json()["status"] == "ready"
