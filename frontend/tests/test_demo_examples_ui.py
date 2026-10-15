"""UI con paquetes simulados: no representa una preparación real."""

from copy import deepcopy

import pytest
import streamlit as st
from i18n import t
from streamlit.testing.v1 import AppTest
from test_result import ClienteResultado, paquete

pytestmark = pytest.mark.integration_mock

SCRIPT = """
import streamlit as st
from components.demo_examples import render_catalogo, render_ejemplo
from test_demo_examples_ui import ClienteDemo
if st.session_state.get('ejemplo_demo_id'):
    render_ejemplo(ClienteDemo(), st.session_state.idioma_ui)
else:
    render_catalogo(ClienteDemo(), st.session_state.idioma_ui)
"""


class ClienteDemo:
    def _solicitar(self, metodo, ruta, **kwargs):
        st.session_state.rutas_demo.append((metodo, ruta))
        if ruta == "/api/demo/generations":
            return st.session_state.catalogo
        if ruta.endswith("/answers"):
            return ClienteResultado().answer_quiz(
                kwargs["token"], st.session_state.paquete["generation_id"], kwargs["json"]
            )
        return st.session_state.paquete


def ui(formato="flashcards", idioma="es"):
    p = paquete(formato)
    item = {
        "generation_id": p["generation_id"],
        "fuente": "FUENTE_DEMO.pdf",
        "status": "completed",
        "created_at": p["created_at"],
        "segundos": 12.75,
        "parametros": {
            "document_id": p["documento_fuente"]["document_id"],
            "perfil_destinatario": "principiante",
            "formato_salida": formato,
            "nicho_sector": "general",
            "idioma_salida": "es",
        },
    }
    app = AppTest.from_string(SCRIPT)
    for key, value in {
        "workspace_id": "ws",
        "session_token": "token",
        "idioma_ui": idioma,
        "paquete": p,
        "catalogo": {"provider": "mock", "items": [item]},
        "rutas_demo": [],
        "llamadas": [],
    }.items():
        app.session_state[key] = value
    return app.run()


@pytest.mark.parametrize("idioma", ["es", "en", "pt"])
def test_catalogo_abre_material_sin_registrar_progreso(idioma):
    app = ui(idioma=idioma)
    assert not app.exception
    next(b for b in app.button if b.label == t("demo.abrir", idioma)).click().run()
    assert not app.exception
    assert any(t("historial.ejemplo_guardado", idioma) in info.value for info in app.info)
    assert not app.session_state.llamadas
    for button in app.button:
        if button.label == t("material.mostrar_respuesta", idioma):
            button.click().run()
            break
    assert not app.session_state.llamadas
    assert all(ruta.startswith("/api/demo/generations") for _, ruta in app.session_state.rutas_demo)


def test_rechazo_muestra_solo_diagnostico():
    app = ui()
    catalogo = deepcopy(app.session_state.catalogo)
    catalogo["items"][0].update(status="rejected_quality", diagnostico="Evidencia insuficiente")
    app.session_state.catalogo = catalogo
    app.run()
    assert not app.exception
    assert any("Evidencia insuficiente" in w.value for w in app.warning)
    assert not any(b.label == t("demo.abrir", "es") for b in app.button)


def test_quiz_reutiliza_correccion_y_endpoint_demo():
    app = ui("quiz")
    next(b for b in app.button if b.label == t("demo.abrir", "es")).click().run()
    assert not app.exception
    opcion = app.session_state.paquete["contenido_adaptado"]["preguntas"][0]["opciones"][0]["option_id"]
    app.radio[0].set_value(opcion).run()
    next(b for b in app.button if b.label == t("quiz.responder", "es")).click().run()
    assert not app.exception
    assert any(m == "POST" and ruta.endswith("/answers") for m, ruta in app.session_state.rutas_demo)
