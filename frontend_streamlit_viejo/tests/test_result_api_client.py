"""Las fuentes y la persistencia mantienen Bearer en el servidor y rutas propias."""

from unittest.mock import MagicMock, patch

import pytest
from api_client import APIClient, APIError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("demo", [False, True])
def test_fuente_usa_ruta_autorizada_y_no_token_en_url(demo):
    respuesta = MagicMock(ok=True, status_code=200, headers={})
    respuesta.json.return_value = {"document_id": "doc", "chunk_id": "chunk", "texto": "Fuente"}
    cliente = APIClient(base_url="http://backend:8000")
    with patch("requests.request", return_value=respuesta) as request:
        cliente.get_document_source("token_privado", "doc/a", "chunk?x", demo=demo)
    args = request.call_args.kwargs
    base = "/api/demo/documents" if demo else "/api/documents"
    assert args["url"] == "http://backend:8000" + base + "/doc%2Fa/sources/chunk%3Fx"
    assert args["headers"]["Authorization"] == "Bearer token_privado"
    assert "token_privado" not in args["url"] and not args["allow_redirects"]


def test_imagen_no_sigue_url_del_modelo_ni_acepta_html():
    respuesta = MagicMock(ok=True, status_code=200, headers={})
    cliente = APIClient(base_url="http://backend:8000")
    respuesta.content = b'<svg onload="alert(1)"></svg>'
    with patch("requests.request", return_value=respuesta) as request:
        with pytest.raises(APIError) as fallo:
            cliente.get_document_source_image("tok", "doc", "chunk")
        assert fallo.value.code == "INVALID_RESPONSE"
    assert request.call_args.kwargs["url"] == "http://backend:8000/api/documents/doc/sources/chunk/image"
    respuesta.content = b"\x89PNG\r\n\x1a\ncontenido"
    with patch("requests.request", return_value=respuesta):
        assert cliente.get_document_source_image("tok", "doc", "chunk") == respuesta.content


def test_reintentar_persistencia_llama_solo_al_endpoint_persist_con_idempotencia():
    respuesta = MagicMock(ok=True, status_code=202, headers={})
    respuesta.json.return_value = {"generation_id": "gen", "status": "queued"}
    cliente = APIClient(base_url="http://backend:8000")
    with patch("requests.request", return_value=respuesta) as request:
        assert cliente.persist_generation("tok", "gen", idempotency_key="persist-key")["status"] == "queued"
    assert request.call_count == 1
    args = request.call_args.kwargs
    assert args["method"] == "POST" and args["url"].endswith("/api/generations/gen/persist")
    assert args["headers"]["Idempotency-Key"] == "persist-key" and args["json"] is None
