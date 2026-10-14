"""Regresiones de la vista estudiante, intenciones y reruns del quiz."""

import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest
import requests
from api_client import APIClient, APIError
from i18n import t
from quiz_flow import EstadoQuiz, estado_quiz, validar_quiz
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def aislar_principal_streamlit(monkeypatch):
    # AppTest no debe cambiar la entrada heredada por procesos spawn de PDF.
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])


PREGUNTA = {
    "id": "q1",
    "enunciado": "¿Qué conserva el aislamiento?",
    "opciones": [{"option_id": k, "texto": "Opción " + k} for k in "ABCD"],
}
RECIBO = {
    "generation_id": "gen",
    "status": "completed",
    "contenido": {
        "documento_fuente": {"document_id": "doc"},
        "contenido_adaptado": {"tipo": "quiz", "preguntas": [PREGUNTA]},
    },
}
MALA = {
    "correcta": False,
    "correct_option_id": "A",
    "justificacion": "Justificación confirmada",
    "referencias": [{"chunk_id": "chunk", "pagina": 1, "seccion": "Redes"}],
}
BUENA = {**MALA, "correcta": True}


class Cliente:
    def __init__(self):
        self.recibo = deepcopy(RECIBO)
        self.respuestas = []
        self.envios = []

    def get_generation(self, token, generation_id):
        if isinstance(self.recibo, Exception):
            raise self.recibo
        return self.recibo

    def answer_quiz(self, token, generation_id, payload):
        self.envios.append(deepcopy(payload))
        salida = self.respuestas.pop(0) if self.respuestas else MALA
        if isinstance(salida, Exception):
            raise salida
        return salida


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setattr(PagesManager, "uses_pages_directory", None)
    api = Cliente()
    app = AppTest.from_string("""
import streamlit as st
from components.quiz_interactive import render_quiz_interactivo
if "api" in st.session_state:
    render_quiz_interactivo(st.session_state.api, st.session_state.lang,
        token="token-de-prueba", workspace_id=st.session_state.ws, generation_id="gen")
""")
    app.session_state.api = api
    app.session_state.lang = "es"
    app.session_state.ws = "ws"
    app.run()
    assert not app.exception
    return app, api


def boton(app, etiqueta):
    return next(b for b in app.button if b.label == etiqueta)


def test_perdida_de_respuesta_bloquea_opcion_y_reutiliza_evento(ui):
    app, api = ui
    api.respuestas[:] = [APIError("SERVICE_UNAVAILABLE", "dato privado", 503), MALA]
    app.radio[0].set_value("B").run()
    boton(app, t("quiz.responder")).click().run()
    assert not app.exception
    assert app.radio[0].disabled and len(api.envios) == 1
    app.run()
    assert len(api.envios) == 1
    boton(app, t("quiz.reenviar")).click().run()
    assert not app.exception
    assert api.envios[0] == api.envios[1]
    assert app.error[0].value == t("quiz.incorrecto")
    assert "dato privado" not in str(app)
    boton(app, t("quiz.reintentar")).click().run()
    assert not app.radio[0].disabled and app.radio[0].value is None
    api.respuestas[:] = [BUENA]
    app.radio[0].set_value("A").run()
    boton(app, t("quiz.responder")).click().run()
    assert not app.exception
    assert api.envios[-1]["event_id"] != api.envios[0]["event_id"]
    estado = app.session_state.quizzes_interactivos[("ws", "gen")][1]
    assert estado.resumen() == {"respondidas": 1, "primeros": 0, "posteriores": 1}


@pytest.mark.parametrize("idioma", ["es", "en", "pt"])
def test_idioma_y_rerun_no_envian_ni_revelan_la_clave(ui, idioma):
    app, api = ui
    app.session_state.lang = idioma
    app.run()
    assert not app.exception and not api.envios
    assert app.radio[0].label == t("quiz.elegir", idioma)
    assert not app.success and not app.error
    assert not any("Justificación confirmada" in m.value for m in app.markdown)


def test_generaciones_y_espacios_no_comparten_feedback():
    estados = {}
    a = estado_quiz(estados, "ws", "gen", "doc", [deepcopy(PREGUNTA)])
    a.preparar(PREGUNTA, "B").feedback = MALA
    assert estado_quiz(estados, "ws", "gen", "doc", [PREGUNTA]) is a
    assert estado_quiz(estados, "otro", "gen", "doc", [PREGUNTA]).resumen()["respondidas"] == 0
    assert estado_quiz(estados, "ws", "otra-gen", "doc", [PREGUNTA]).resumen()["respondidas"] == 0


@pytest.mark.parametrize("campo,valor", [("correct_option_id", "A"), ("justificacion", "CLAVE_SECRETA")])
def test_se_rechaza_una_vista_canonica(campo, valor):
    datos = deepcopy(RECIBO)
    datos["contenido"]["contenido_adaptado"]["preguntas"][0][campo] = valor
    with pytest.raises(APIError, match="INVALID_RESPONSE"):
        validar_quiz(datos, "gen")


@pytest.mark.parametrize(
    "valor", [None, [], {"documento_fuente": None}, {"documento_fuente": {}, "contenido_adaptado": []}]
)
def test_contenido_invalido_falla_de_forma_controlada(valor):
    with pytest.raises(APIError):
        validar_quiz({**RECIBO, "contenido": valor}, "gen")


@pytest.mark.parametrize("status", [401, 404])
def test_revocacion_descarta_feedback_y_bloquea_nuevos_envios(status):
    api = Cliente()
    estado = EstadoQuiz("doc", [PREGUNTA])
    api.respuestas = [APIError("NOT_FOUND" if status == 404 else "SESSION_INVALID", "privado", status)]
    estado.enviar(api, "token", "gen", PREGUNTA, "B")
    assert estado.invalidado and not estado.intentos
    with pytest.raises(ValueError):
        estado.preparar(PREGUNTA, "A")


def test_feedback_incoherente_no_confirma_intencion():
    api = Cliente()
    api.respuestas = [{**MALA, "correcta": True}, MALA]
    estado = EstadoQuiz("doc", [PREGUNTA])
    assert estado.enviar(api, "token", "gen", PREGUNTA, "B") is None
    assert estado.error.code == "INVALID_RESPONSE"
    estado.enviar(api, "token", "gen", PREGUNTA, "B")
    assert api.envios[0] == api.envios[1]


def test_cliente_transporta_evento_y_token_en_cabecera(monkeypatch):
    llamadas = []

    def solicitar(**kwargs):
        llamadas.append(kwargs)
        return SimpleNamespace(ok=True, status_code=200, headers={}, json=lambda: MALA)

    monkeypatch.setattr(requests, "request", solicitar)
    body = {"question_id": "q1", "option_id": "B", "event_id": "evento"}
    APIClient().answer_quiz("token", "gen/con espacios", body)
    assert llamadas[0]["url"].endswith("/api/quizzes/gen%2Fcon%20espacios/answers")
    assert llamadas[0]["headers"]["Authorization"] == "Bearer token"
    assert llamadas[0]["headers"]["Idempotency-Key"] == "evento"
    assert llamadas[0]["json"] == body and llamadas[0]["allow_redirects"] is False
