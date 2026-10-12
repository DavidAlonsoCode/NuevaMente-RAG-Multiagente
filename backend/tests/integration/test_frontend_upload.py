"""El cliente UI debe construir multipart aceptado por el HTTP real (#25/#44)."""

from urllib.parse import urlsplit

import pytest
import requests
from api_client import APIClient
from test_api_generate import esperar

pytestmark = pytest.mark.integration_mock


@pytest.mark.parametrize("extension,mime", [("txt", "text/plain"), ("md", "text/markdown"), ("pdf", "application/pdf")])
def test_cliente_frontend_upload_con_titulo_y_backend_real(entorno, monkeypatch, crear_pdf, extension, mime):
    e = entorno

    def transporte(**kw):
        # Sólo sustituir el transporte de Requests; formulario, headers y
        # validación multipart se ejercitan sin alterar el código del cliente.
        respuesta = e.cliente.request(
            kw["method"],
            urlsplit(kw["url"]).path,
            headers=kw["headers"],
            files=kw.get("files"),
            data=kw.get("data"),
        )
        respuesta.ok = respuesta.is_success
        return respuesta

    monkeypatch.setattr(requests, "request", transporte)
    texto = "Una VCN conecta subredes y mantiene los recursos aislados. " * 30
    contenido = crear_pdf(texto=texto).read_bytes() if extension == "pdf" else texto.encode("utf-8")
    recibo = APIClient("http://test").upload_file(
        e.ws["token"], "redes." + extension, contenido, mime, title="Título desde la UI", idempotency_key="ui-upload"
    )
    esperar(e, recibo)
    documento = e.cliente.get("/api/documents/" + recibo["document_id"], headers=e.headers)
    assert documento.status_code == 200
    assert documento.json()["titulo"] == "Título desde la UI"
    assert documento.json()["status"] == "ready"
