"""Streamlit/cliente -> corrección HTTP real; Gemini y OCI son dobles explícitos."""

from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
import test_api_generate as generaciones
from api_client import APIClient
from i18n import t
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.integration_mock
entorno = generaciones.entorno


def test_respuesta_perdida_y_nuevo_intento_registran_solo_dos_eventos(entorno, monkeypatch):
    e = entorno
    recibo = generaciones.generar(e, formato_salida="quiz").json()
    estudiante = generaciones.esperar(e, recibo)
    pregunta = estudiante["contenido"]["contenido_adaptado"]["preguntas"][0]
    assert "correct_option_id" not in pregunta and "justificacion" not in pregunta
    canonico = e.app.state.generations.canonico(e.ws["token"], recibo["generation_id"])
    correcta = canonico.contenido_adaptado.preguntas[0].correct_option_id
    incorrecta = next(o["option_id"] for o in pregunta["opciones"] if o["option_id"] != correcta)
    perdidas, envios = [True], []

    def solicitar(method, url, *, json=None, params=None, headers=None, **kwargs):
        r = e.cliente.request(method, urlsplit(url).path, json=json, params=params, headers=headers)
        if method == "POST":
            envios.append(json.copy())
            assert r.status_code == 200, r.text
            if perdidas[0]:
                perdidas[0] = False
                raise requests.ConnectionError("Respuesta perdida después del commit")
        return SimpleNamespace(
            ok=r.is_success, status_code=r.status_code, headers=r.headers, json=r.json, content=r.content
        )

    monkeypatch.setattr(requests, "request", solicitar)
    monkeypatch.setattr(PagesManager, "uses_pages_directory", None)
    app = AppTest.from_string("""
import streamlit as st
from components.quiz_interactive import render_quiz_interactivo
render_quiz_interactivo(st.session_state.api, "es", token=st.session_state.token_prueba,
    workspace_id=st.session_state.ws, generation_id=st.session_state.gen)
""")
    app.session_state.api = APIClient()
    app.session_state.token_prueba = e.ws["token"]
    app.session_state.ws = e.ws["workspace_id"]
    app.session_state.gen = recibo["generation_id"]
    app.run()
    assert not app.exception
    llm = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    app.radio[0].set_value(incorrecta).run()
    next(b for b in app.button if b.label == t("quiz.responder")).click().run()
    assert app.radio[0].disabled
    next(b for b in app.button if b.label == t("quiz.reenviar")).click().run()
    assert not app.exception and envios[0] == envios[1]
    progreso = e.cliente.get("/api/progress", headers=e.headers).json()
    assert (progreso["quiz_intentos"], progreso["quiz_total_primer_intento"]) == (1, 1)
    next(b for b in app.button if b.label == t("quiz.reintentar")).click().run()
    app.radio[0].set_value(correcta).run()
    next(b for b in app.button if b.label == t("quiz.responder")).click().run()
    assert not app.exception and envios[-1]["event_id"] != envios[0]["event_id"]
    progreso = e.cliente.get("/api/progress", headers=e.headers).json()
    assert (
        progreso["quiz_intentos"],
        progreso["quiz_aciertos_primer_intento"],
        progreso["quiz_aciertos_posteriores"],
    ) == (2, 0, 1)
    assert (e.doble.llamadas_generacion, e.doble.llamadas_verificacion) == llm
