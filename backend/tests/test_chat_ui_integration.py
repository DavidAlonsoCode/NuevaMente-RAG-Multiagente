"""UI/cliente del tutor contra API/worker/grafo reales, con proveedores simulados."""

import sys
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
import test_api_generate as generaciones
import test_chat as tutor
from api_client import APIClient
from chat_flow import Conversacion
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.integration_mock


@pytest.fixture(autouse=True)
def aislar_principal_streamlit(monkeypatch):
    # AppTest reemplaza __main__; spawn debe conservar la entrada original.
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])


entorno_generacion = generaciones.entorno
entorno = tutor.entorno


def puente(e, monkeypatch):
    def solicitar(method, url, *, json=None, params=None, headers=None, **kwargs):
        r = e.cliente.request(method, urlsplit(url).path, json=json, params=params, headers=headers)
        return SimpleNamespace(
            ok=r.is_success, status_code=r.status_code, headers=r.headers, json=r.json, content=r.content
        )

    monkeypatch.setattr(requests, "request", solicitar)


def test_cinco_turnos_streamlit_con_citas_y_sin_fuga_a_otro_documento(entorno, monkeypatch):
    e = entorno
    puente(e, monkeypatch)
    monkeypatch.setattr(PagesManager, "uses_pages_directory", None)
    app = AppTest.from_string("""
import streamlit as st
from components.chat import render_chat
def fuentes(doc, referencias):
    for ref in referencias:
        fuente=st.session_state.api._solicitar("GET",
            "/api/documents/"+doc+"/sources/"+ref["chunk_id"],token=st.session_state.token_prueba)
        assert fuente["chunk_id"]==ref["chunk_id"] and fuente["document_id"]==doc
        st.text(fuente["texto"])
if "api" in st.session_state:
    render_chat(st.session_state.api,"es",token=st.session_state.token_prueba,
    workspace_id=st.session_state.ws,document_id=st.session_state.doc,
    perfil_destinatario="principiante",idioma_salida="es",render_fuentes=fuentes)
""")
    app.session_state.api = APIClient()
    app.session_state.token_prueba = e.ws["token"]
    app.session_state.ws = e.ws["workspace_id"]
    app.session_state.doc = e.doc
    app.run()
    for n in range(5):
        app.chat_input[0].set_value("¿Qué conecta una VCN? " + str(n)).run()
        assert not app.exception
        c = app.session_state.chat_conversaciones[(e.ws["workspace_id"], e.doc, "principiante", "es")]
        turno = c.turnos[-1]
        assert turno.job_id is not None
        limite = time.monotonic() + 15
        while turno.activo and time.monotonic() < limite:
            time.sleep(0.1)
            turno.consultar(app.session_state.api, e.ws["token"])
        assert turno.status == "completed", turno.status
        app.run()
        assert not app.exception and turno.respuesta["referencias"]
    assert len(c.turnos) == 5 and len(e.entradas_chat) == 5
    app.session_state.doc = "inexistente"
    app.run()
    assert not app.exception and not app.chat_input
    assert not any("Una VCN conecta subredes privadas" in m.value for m in app.markdown)


def test_respuesta_perdida_despues_de_admitir_reutiliza_el_mismo_job(entorno, monkeypatch):
    e = entorno
    c = Conversacion(e.doc, "principiante", "es")
    turno = c.iniciar("¿Qué conecta una VCN?")
    perdida = [True]
    envios = []

    def solicitar(method, url, *, json=None, params=None, headers=None, **kwargs):
        r = e.cliente.request(method, urlsplit(url).path, json=json, params=params, headers=headers)
        if method == "POST" and urlsplit(url).path == "/api/chat":
            assert r.status_code == 202, r.text
            envios.append((json.copy(), headers["Idempotency-Key"], r.json()["job_id"]))
            if perdida[0]:
                perdida[0] = False
                raise requests.ConnectionError("Respuesta perdida tras confirmar admisión")
        return SimpleNamespace(
            ok=r.is_success, status_code=r.status_code, headers=r.headers, json=r.json, content=r.content
        )

    monkeypatch.setattr(requests, "request", solicitar)
    api = APIClient()
    turno.enviar(api, e.ws["token"])
    assert turno.job_id is None and turno.activo
    turno.enviar(api, e.ws["token"])
    assert len(envios) == 2 and envios[0] == envios[1]
    limite = time.monotonic() + 15
    while turno.activo and time.monotonic() < limite:
        time.sleep(0.1)
        turno.consultar(api, e.ws["token"])
    assert turno.status == "completed" and len(e.entradas_chat) == 1
