"""La pantalla principal conecta el tutor y el visor sin mezclar contextos."""

import sys

import pytest
from i18n import t
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest
from test_chat_ui import Cliente, respuesta, trabajo
from test_glossary_ui import API as GlosarioAPI
from test_result import ClienteResultado, ui

pytestmark = pytest.mark.integration_mock


class ClienteEstudio(ClienteResultado, Cliente):
    def __init__(self):
        super().__init__()
        self.glosario = GlosarioAPI()

    def create_glossary(self, token, payload, **kwargs):
        self.glosario.output["documento_fuente"]["document_id"] = payload["document_id"]
        for termino in self.glosario.output["terminos"]:
            termino["referencias"] = [{"chunk_id": "chk_001", "pagina": 2, "seccion": "Conceptos"}]
        return self.glosario.create_glossary(token, payload, **kwargs)

    def get_glossary(self, token, gid):
        return self.glosario.get_glossary(token, gid)


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


def test_home_glosario_filtra_y_abre_fuentes_sin_generar_de_nuevo(estudio):
    app, api = estudio
    next(b for b in app.button if b.label == "Generar glosario").click().run()
    assert not app.exception and len(api.glosario.calls) == 1
    payload = api.glosario.calls[0][0]
    assert payload["perfil_destinatario"] == "principiante" and payload["idioma_salida"] == "es"
    fuentes = [b for b in app.button if b.label == "Consultar fuente original"]
    assert len(fuentes) == 4  # Material y tres términos con la misma cita.
    fuentes[-1].click().run()
    assert not app.exception and any("EVIDENCIA" in m.value for m in app.markdown)
    next(i for i in app.text_input if i.label == t("glosario.buscar")).set_value("VCN").run()
    assert not app.exception and len(api.glosario.calls) == 1


def test_home_solo_registra_estudio_explicito_y_consulta_agregados(estudio):
    app, _ = estudio
    assert not app.exception and not app.session_state.filtered_state.get("eventos_progreso")
    next(b for b in app.button if b.label == t("material.mostrar_respuesta")).click().run()
    assert not app.exception and len(app.session_state.eventos_progreso) == 1
    assert any(m.label == t("progreso.flashcards") and m.value == "1" for m in app.metric)
    next(b for b in app.button if b.label == t("material.ocultar_respuesta")).click().run()
    next(b for b in app.button if b.label == t("material.mostrar_respuesta")).click().run()
    assert len(app.session_state.eventos_progreso) == 1
    concepto = app.session_state.paquete["metadatos"]["conceptos_clave"][0]
    next(b for b in app.button if b.label == t("progreso.marcar_concepto").format(concepto=concepto)).click().run()
    assert not app.exception and len(app.session_state.eventos_progreso) == 2
    assert any(m.label == t("progreso.conceptos") and m.value == "1" for m in app.metric)
