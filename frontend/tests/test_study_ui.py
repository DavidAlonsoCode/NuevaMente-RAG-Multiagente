"""La pantalla principal conecta el tutor y el visor sin mezclar contextos."""

import sys

import pytest
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest
from test_chat_ui import Cliente, respuesta, trabajo
from test_result import ClienteResultado, ui

pytestmark = pytest.mark.integration_mock


class ClienteEstudio(ClienteResultado, Cliente):
    pass


@pytest.fixture
def estudio(monkeypatch):
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])
    monkeypatch.setattr(PagesManager, "uses_pages_directory", None)
    base = ui()
    app = AppTest.from_string("""
import streamlit as st
from components.session import inicializar_estado_sesion
from pages import home
home.render_sidebar = lambda *args: None
inicializar_estado_sesion()
home.render(st.session_state.api, st.session_state.idioma_ui)
""")
    for key, value in base.session_state.filtered_state.items():
        app.session_state[key] = value
    api = ClienteEstudio()
    app.session_state.api = api
    app.session_state.generacion_activa = {
        **base.session_state.activa,
        "documento_titulo": "Redes",
        "seccion_titulo": None,
    }
    app.session_state.gen_perfil = "ejecutivo"
    app.session_state.gen_idioma_salida = "pt"
    return app.run(), api


def test_home_conecta_cinco_turnos_con_fuentes_y_parametros_del_resultado(estudio):
    app, api = estudio
    assert not app.exception
    for n in range(5):
        app.chat_input[0].set_value(f"Pregunta {n}").run()
        body, key = api.envios[-1]
        assert body["perfil_destinatario"] == "principiante" and body["idioma_salida"] == "es"
        result = respuesta(body)
        result["referencias"] = [{"chunk_id": "chk_001", "pagina": 2, "seccion": "Conceptos"}]
        api.jobs[api.intenciones[key]] = trabajo(api.intenciones[key], "completed", result)
        app.run()
        assert not app.exception
    # Cinco citas iguales pueden abrirse de manera independiente sin claves duplicadas.
    fuentes = [b for b in app.button if b.label == "Consultar fuente original"]
    assert len(fuentes) == 6  # Una del material y cinco del tutor.
    fuentes[-1].click().run()
    assert not app.exception
    assert any("EVIDENCIA" in m.value for m in app.markdown)
    assert len(api.envios) == 5
