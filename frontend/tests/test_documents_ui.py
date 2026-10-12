"""#25: estados, errores e idempotencia en la interfaz real de Streamlit."""

import json
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
import streamlit as st
from api_client import APIClient, APIError
from components import documents, upload
from i18n import t
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.unit

DOCUMENTO = {
    "document_id": "doc",
    "titulo": "<img src=https://example.invalid/privado>",
    "status": "ready",
    "metadata": {"extension": "pdf", "tamano_bytes": 500, "cantidad_paginas": 1},
    "cobertura": {"paginas_procesadas": [1], "paginas_omitidas": [], "paginas_visuales_pendientes": []},
    "error": None,
}


@pytest.fixture
def estado(monkeypatch):
    class Estado(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    datos = Estado(session_token="token")
    monkeypatch.setattr(st, "session_state", datos)
    upload.inicializar_estado_documentos()
    return datos


def test_error_de_admision_no_inventa_ready_y_preserva_intencion(estado, monkeypatch):
    monkeypatch.setattr(upload, "mostrar_error_ui", Mock())
    api = Mock()
    api.upload_document.side_effect = APIError("STORAGE_UNAVAILABLE", "privado", 503)
    for _ in range(2):
        assert not upload.admitir_documento(api, "es", titulo="Fuente", contenido=b"Contenido suficiente")
    claves = [c.kwargs["idempotency_key"] for c in api.upload_document.call_args_list]
    assert claves[0] == claves[1]
    assert estado.documentos == [] and not estado.trabajos_documentales
    api.upload_document.side_effect = None
    api.upload_document.return_value = {"document_id": "doc", "job_id": "job"}
    assert upload.admitir_documento(api, "es", titulo="Fuente", contenido=b"Contenido suficiente")
    assert estado.trabajos_documentales["doc"]["estado"] == "queued" and estado.documentos == []


def test_pdf_25mb_rechazado_antes_de_leer_o_enviar(estado, monkeypatch):
    archivo = SimpleNamespace(name="grande.pdf", size=25 * 1024 * 1024, getvalue=Mock())
    api = Mock()
    for nombre in ("markdown", "caption"):
        monkeypatch.setattr(st, nombre, Mock())
    monkeypatch.setattr(st, "file_uploader", lambda *a, **kw: archivo)
    monkeypatch.setattr(st, "checkbox", lambda *a, **kw: True)
    error, boton = Mock(), Mock(return_value=False)
    monkeypatch.setattr(st, "error", error)
    monkeypatch.setattr(st, "button", boton)
    upload.render_subida_archivo("es", api)
    error.assert_called_once_with(t("errors.DOCUMENT_TOO_LARGE"))
    assert boton.call_args.kwargs["disabled"] is True
    archivo.getvalue.assert_not_called()
    api.upload_file.assert_not_called()


def test_borrado_fallido_conserva_catalogo_y_seleccion(estado):
    estado.documentos = [deepcopy(DOCUMENTO)]
    estado.documento_activo_id = "doc"
    api = Mock()
    api.delete_document.side_effect = APIError("STORAGE_UNAVAILABLE", "privado", 503)
    with pytest.raises(APIError):
        documents.eliminar_documento(api, "token", "doc")
    assert estado.documentos and estado.documento_activo_id == "doc"
    api.delete_document.side_effect = None
    documents.eliminar_documento(api, "token", "doc")
    assert estado.documentos == [] and estado.documento_activo_id is None


def test_seleccion_revalida_ready_y_desactiva_fuente_incompleta(estado):
    api = Mock()
    api.get_document.return_value = {**DOCUMENTO, "status": "processing"}
    assert not documents.seleccionar_documento(api, "token", "doc")
    assert estado.documento_activo_id is None
    api.get_document.return_value = DOCUMENTO
    assert documents.seleccionar_documento(api, "token", "doc")
    assert estado.documento_activo_id == "doc"


def test_vision_pendiente_apaga_sondeo_y_no_habilita_documento(estado):
    estado.documentos = [
        {
            **DOCUMENTO,
            "status": "processing",
            "cobertura": {"paginas_procesadas": [1], "paginas_omitidas": [], "paginas_visuales_pendientes": [3]},
        }
    ]
    assert not documents.hay_procesamiento()
    estado.documentos[0]["cobertura"]["paginas_visuales_pendientes"] = []
    assert documents.hay_procesamiento()


@pytest.mark.parametrize("provider", ["mock", "oci", None])
@pytest.mark.parametrize("idioma", ["es", "en", "pt"])
def test_presentacion_literal_cobertura_real_y_proveedor(provider, idioma):
    fuente = (
        """
import json
import streamlit as st
from components.upload import inicializar_estado_documentos
from components.documents import presentar_documento
inicializar_estado_documentos()
presentar_documento(json.loads(DATOS), IDIOMA, provider=PROVIDER)
""".replace("DATOS", repr(json.dumps(DOCUMENTO)))
        .replace("IDIOMA", repr(idioma))
        .replace("PROVIDER", repr(provider))
    )
    app = AppTest.from_string(fuente).run()
    assert not app.exception
    assert app.text[0].value == DOCUMENTO["titulo"]
    assert all(DOCUMENTO["titulo"] not in m.value for m in app.markdown)
    captions = [c.value for c in app.caption]
    assert t("upload.cobertura", idioma).format(procesadas=1, total=1) in captions
    assert (t("upload.guardado_oci", idioma) in captions) == (provider == "oci")
    assert (t("almacenamiento.local_dev", idioma) in captions) == (provider == "mock")


def test_demo_seleccion_sin_subida_y_fuente_pendiente_deshabilitada():
    demos = [
        {**DOCUMENTO, "document_id": "demo-pagos-fintech"},
        {**DOCUMENTO, "document_id": "demo-redes-vcn", "status": "processing"},
    ]
    fuente = """
import json
import streamlit as st
from components.upload import inicializar_estado_documentos
from components.documents import render_documentos_demo
st.session_state.setdefault("session_token", "token")
inicializar_estado_documentos()
class API:
    def get_demo_documents(self, *a): return {"items": json.loads(DATOS), "next_cursor": None}
    def get_demo_document(self, token, cid): return next(d for d in json.loads(DATOS) if d["document_id"] == cid)
render_documentos_demo("es", API())
""".replace("DATOS", repr(json.dumps(demos)))
    app = AppTest.from_string(fuente).run()
    assert not app.exception
    assert app.button(key="demo_demo-redes-vcn").disabled
    assert app.button(key="demo_demo-salud-gobernanza").disabled
    app.button(key="demo_demo-pagos-fintech").click().run()
    assert not app.exception
    assert app.session_state.documento_activo_id == "demo-pagos-fintech"
    assert app.session_state.documento_activo_demo


def test_sse_cursor_heartbeat_orden_y_cierre(monkeypatch):
    res = Mock(status_code=200, headers={"Content-Type": "text/event-stream"})
    res.iter_lines.return_value = [
        b": heartbeat",
        b"",
        b"id: 4",
        b"event: progress",
        b'data: {"job_id":"job","step":"parser"}',
        b"",
        b"id: 3",
        b"event: progress",
        b'data: {"job_id":"job","step":"fuente"}',
        b"",
        b"id: 5",
        b"event: progress",
        b'data: {"job_id":"ajeno","step":"indice"}',
        b"",
    ]
    transporte = Mock(return_value=res)
    monkeypatch.setattr(requests, "request", transporte)
    assert APIClient("http://test").read_job_progress("token", "job", 2) == {
        "id": 4,
        "data": {"job_id": "job", "step": "parser"},
    }
    assert transporte.call_args.kwargs["headers"]["Last-Event-ID"] == "2"
    assert transporte.call_args.kwargs["allow_redirects"] is False
    res.close.assert_called_once()


def test_sse_error_cierra_y_recibo_incompleto_no_es_admision(monkeypatch):
    res = Mock(status_code=401)
    monkeypatch.setattr(requests, "request", lambda *a, **kw: res)
    with pytest.raises(APIError) as error:
        APIClient("http://test").read_job_progress("token", "job")
    assert error.value.code == "SESSION_INVALID"
    res.close.assert_called_once()
    with pytest.raises(APIError) as error:
        APIClient._recibo_upload({"document_id": "doc", "status": "processing"})
    assert error.value.code == "INVALID_RESPONSE"
