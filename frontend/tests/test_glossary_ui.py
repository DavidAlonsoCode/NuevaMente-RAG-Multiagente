"""#40: estados visibles, búsqueda, separación de contextos e idempotencia."""

import sys
from copy import deepcopy

import pytest
from api_client import APIError
from glossary_flow import admitir_glosario, cancelar_glosario, consultar_glosario, estado_glosario, filtrar_terminos
from i18n import t
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.unit
APP = """
import streamlit as st
from components.glossary import render_glosario
if "api" in st.session_state:
    render_glosario(st.session_state.api, st.session_state.lang,
        document_id=st.session_state.doc, perfil="principiante", idioma_salida="es")
"""


@pytest.fixture(autouse=True)
def aislar_principal_streamlit(monkeypatch):
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])
    monkeypatch.setattr(PagesManager, "uses_pages_directory", None)


def termino(id, original, *, traduccion=None, insuficiente=False):
    return dict(
        id=id,
        termino_original=original,
        es_identificador=original == "VCN",
        estado="sin_definicion_suficiente" if insuficiente else "definido",
        definicion=None if insuficiente else "Conecta subredes.",
        traduccion=traduccion,
        limitacion="La fuente sólo menciona el término." if insuficiente else None,
        referencias=[dict(chunk_id="chunk-1", pagina=1, seccion=None)],
    )


def salida():
    return dict(
        glossary_id="g1",
        documento_fuente={"document_id": "d1"},
        perfil_destinatario="principiante",
        idioma_salida="es",
        estado="verificado",
        candidatos_omitidos=2,
        terminos=[
            termino("v", "VCN"),
            termino("a", "aislamiento", traduccion="isolation"),
            termino("z", "Zona", insuficiente=True),
        ],
        evaluacion_calidad=dict(
            estado_evaluacion="aprobada",
            anclaje_fuente_score=1,
            cantidad_afirmaciones=2,
            cantidad_respaldadas=2,
            cobertura_objetivos="completa",
            verificacion_visual="no_aplica",
            claridad_pedagogica="alta",
            adecuacion_perfil="alta",
            coherencia_didactica="alta",
            razones_bloqueo=None,
        ),
        trazabilidad={},
    )


class API:
    def __init__(self):
        self.status, self.calls, self.cancelled, self.lost = "completed", [], False, False
        self.output = salida()

    def create_glossary(self, token, payload, *, idempotency_key):
        self.calls.append((deepcopy(payload), idempotency_key))
        if self.lost:
            self.lost = False
            raise APIError("SERVICE_UNAVAILABLE", "Privado", 503)
        return dict(job_id="g1", glossary_id="g1", status=self.status)

    def get_job(self, token, job_id):
        return dict(job_id=job_id, status=self.status, result={"borrador": "SECRETO"})

    def get_glossary(self, token, job_id):
        return deepcopy(self.output)

    def cancel_glossary(self, token, job_id):
        self.cancelled = True


def app(api, lang="es"):
    test = AppTest.from_string(APP)
    for k, v in dict(api=api, lang=lang, doc="d1", session_token="test-token", workspace_id="w1").items():
        test.session_state[k] = v
    return test.run()


@pytest.mark.parametrize("lang", ["es", "en", "pt"])
def test_busqueda_traduccion_limitacion_orden_y_citas(lang):
    a = app(API(), lang)
    a.button[0].click().run()
    assert not a.exception
    assert [m.value for m in a.markdown if m.value in {"aislamiento", "VCN", "Zona"}] == ["aislamiento", "VCN", "Zona"]
    assert any("isolation" in c.value for c in a.caption)
    assert any(t("glosario.limitacion", lang) == w.value for w in a.warning)
    assert {c.value for c in a.code} == {"chunk-1"}
    a.text_input[0].set_value("ISOLATION").run()
    assert [m.value for m in a.markdown if m.value in {"aislamiento", "VCN", "Zona"}] == ["aislamiento"]
    a.text_input[0].set_value("no existe").run()
    assert t("glosario.sin_resultados", lang) in [i.value for i in a.info]
    a.session_state.doc = "otro"
    a.run()
    assert not a.text_input and not a.code


def test_admision_perdida_reutiliza_intencion_y_parametros():
    api, s = API(), {}
    estado = estado_glosario(s, "w", "d1", "principiante", "es")
    api.lost = True
    with pytest.raises(APIError):
        admitir_glosario(api, "token", estado)
    admitir_glosario(api, "token", estado)
    assert api.calls[0] == api.calls[1]
    assert estado_glosario(s, "w", "d1", "ejecutivo", "en")["job_id"] is None


@pytest.mark.parametrize("status", ["failed", "rejected_quality", "cancelled"])
def test_terminal_no_entrega_borradores(status):
    api = API()
    api.status = status
    estado = estado_glosario({}, "w", "d1", "principiante", "es")
    estado.update(job_id="g1", status="running")
    assert consultar_glosario(api, "token", estado) is None
    a = app(api)
    a.button[0].click().run()
    assert not a.exception and not a.code and not a.text_input


def test_cancelacion_espera_confirmacion_del_servidor():
    api = API()
    api.status = "running"
    s = estado_glosario({}, "w", "d1", "principiante", "es")
    admitir_glosario(api, "token", s)
    cancelar_glosario(api, "token", s)
    assert api.cancelled and s["status"] == "running"
    assert consultar_glosario(api, "token", s) is None
    api.status = "cancelled"
    assert consultar_glosario(api, "token", s) is None and s["status"] == "cancelled"


@pytest.mark.parametrize("defecto", ["documento", "score", "sin_revision", "identificador", "citas", "no_definiciones"])
def test_respuesta_inconsistente_no_se_muestra(defecto):
    api = API()
    s = estado_glosario({}, "w", "d1", "principiante", "es")
    admitir_glosario(api, "token", s)
    if defecto == "documento":
        api.output["documento_fuente"]["document_id"] = "ajeno"
    elif defecto == "score":
        api.output["evaluacion_calidad"]["anclaje_fuente_score"] = 0.1
    elif defecto == "sin_revision":
        api.output["evaluacion_calidad"]["cobertura_objetivos"] = "parcial"
    elif defecto == "identificador":
        api.output["terminos"][0]["traduccion"] = "inventada"
    elif defecto == "citas":
        api.output["terminos"][0]["referencias"] = []
    else:
        api.output["estado"] = "sin_definiciones"
    with pytest.raises(APIError) as error:
        consultar_glosario(api, "token", s)
    assert error.value.code == "INVALID_RESPONSE" and error.value.status_code == 502


def test_inventario_sin_definiciones_no_recibe_score():
    api = API()
    api.output.update(
        estado="sin_definiciones",
        terminos=[termino("z", "Zona", insuficiente=True)],
        evaluacion_calidad=None,
        trazabilidad=None,
    )
    s = estado_glosario({}, "w", "d1", "principiante", "es")
    admitir_glosario(api, "token", s)
    assert consultar_glosario(api, "token", s)["evaluacion_calidad"] is None
    assert filtrar_terminos(api.output["terminos"], "zona")[0]["id"] == "z"


def test_cancelar_desde_panel_y_no_exponer_contenido_en_espera():
    api = API()
    api.status = "running"
    a = app(api)
    a.button[0].click().run()
    assert not a.text_input and not a.code
    a.button[0].click().run()
    assert api.cancelled and a.button[0].disabled
    assert t("glosario.cancelando", "es") in [i.value for i in a.info]
    api.status = "cancelled"
    a.run()
    assert not a.exception and not a.text_input


def test_borrado_revoca_resultado_previamente_visible(monkeypatch):
    api = API()
    a = app(api)
    a.button[0].click().run()
    assert a.code

    def revocado(*args):
        raise APIError("NOT_FOUND", "Privado", 404)

    monkeypatch.setattr(api, "get_glossary", revocado)
    a.run()
    assert not a.exception and not a.text_input
    assert "chunk-1" not in [c.value for c in a.code]
    assert "Conecta subredes." not in [m.value for m in a.markdown]
