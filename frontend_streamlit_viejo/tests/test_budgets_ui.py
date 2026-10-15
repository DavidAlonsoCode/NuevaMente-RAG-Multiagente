"""La alerta y el límite se muestran en la interfaz real de Streamlit (#50)."""

import json

import pytest
from i18n import t
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("idioma", ["es", "en", "pt"])
@pytest.mark.parametrize("bloqueado", [False, True])
def test_alerta_vencimiento_y_limite(idioma, bloqueado):
    datos = {
        "expira_en": "2026-11-12T12:00:00Z",
        "uso": {"documentos": 4, "limite_documentos": 5, "generaciones": 18, "limite_generaciones": 20},
        "almacenamiento": {"alerta": True, "escrituras_bloqueadas": bloqueado},
    }
    fuente = """
import json
import streamlit as st
from components.budgets import render_presupuesto
class API:
    def workspace_status(self, token):
        return json.loads(DATOS)
st.session_state.session_token='token_prueba'
render_presupuesto(API(), IDIOMA)
""".replace("DATOS", repr(json.dumps(datos))).replace("IDIOMA", repr(idioma))
    app = AppTest.from_string(fuente).run()
    assert not app.exception
    assert datos["expira_en"] in app.caption[0].value
    assert "4/5" in app.caption[1].value and "18/20" in app.caption[1].value
    if bloqueado:
        assert app.error[0].value == t("presupuesto.bloqueado", idioma) and not app.warning
    else:
        assert app.warning[0].value == t("presupuesto.alerta", idioma) and not app.error
