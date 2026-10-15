"""#41: agregados del servidor y registro sólo tras consultar ambas caras."""

import sys
from copy import deepcopy

import pytest
from api_client import APIError
from i18n import t
from progress_flow import cara_flashcard, concepto_revisado, consultar_progreso, enviar_evento, pendientes
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.unit
APP = """
import streamlit as st
from components.progress import registrar_cara_flashcard, render_marca_concepto, render_progreso
if "api" in st.session_state:
    if st.button("Frente"):
        st.session_state.cara = "frente"
    if st.button("Dorso"):
        st.session_state.cara = "dorso"
    if st.session_state.get("cara"):
        registrar_cara_flashcard(st.session_state.api, st.session_state.lang,
            generation_id="gen1", flashcard_id="c1", cara=st.session_state.cara)
    render_marca_concepto(st.session_state.api, st.session_state.lang, document_id="doc1", concepto="VCN")
    render_progreso(st.session_state.api, st.session_state.lang)
"""


@pytest.fixture(autouse=True)
def aislar_principal_streamlit(monkeypatch):
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])
    monkeypatch.setattr(PagesManager, "uses_pages_directory", None)


def agregado():
    return dict(
        version=0,
        conceptos_revisados=0,
        flashcards_vistas=0,
        quiz_intentos=0,
        quiz_aciertos_primer_intento=0,
        quiz_total_primer_intento=0,
        quiz_aciertos_posteriores=0,
        tiempo_estimado_restante_minutos=8.5,
    )


class API:
    def __init__(self):
        self.data, self.calls, self.events, self.falla = agregado(), [], {}, False

    def get_progress(self, token):
        return deepcopy(self.data)

    def record_progress(self, token, payload):
        self.calls.append(deepcopy(payload))
        if self.falla:
            raise APIError("SERVICE_UNAVAILABLE", "Privado", 503)
        event_id = payload["event_id"]
        if event_id not in self.events:
            self.data["version"] += 1
            key = "conceptos_revisados" if payload["tipo"] == "concepto_revisado" else "flashcards_vistas"
            self.data[key] += 1
            self.events[event_id] = self.data["version"]
        return dict(event_id=event_id, version=self.events[event_id])


def app(api, lang="es"):
    a = AppTest.from_string(APP)
    for k, v in dict(api=api, lang=lang, session_token="test-token", workspace_id="w1").items():
        a.session_state[k] = v
    return a.run()


@pytest.mark.parametrize("lang", ["es", "en", "pt"])
def test_solo_ambas_caras_y_concepto_explicito_cuentan_una_vez(lang):
    api, a = API(), None
    a = app(api, lang)
    assert not a.exception and not api.calls
    a.button[0].click().run()
    assert api.data["flashcards_vistas"] == 0 and not api.calls
    a.button[1].click().run()
    assert api.data["flashcards_vistas"] == 1 and len(api.calls) == 1
    a.run()
    a.button[0].click().run()
    a.button[1].click().run()
    assert len(api.calls) == 1
    marca = next(b for b in a.button if b.label == t("progreso.marcar_concepto", lang).format(concepto="VCN"))
    marca.click().run()
    assert api.data["conceptos_revisados"] == 1 and len(api.calls) == 2
    assert any(m.label == t("progreso.flashcards", lang) and m.value == "1" for m in a.metric)
    assert t("progreso.orientativo", lang) in [c.value for c in a.caption]


def test_error_de_escritura_no_inventa_progreso_y_reintento_mismo_evento():
    api = API()
    api.falla = True
    a = app(api)
    a.button[0].click().run()
    a.button[1].click().run()
    assert api.data["flashcards_vistas"] == 0 and len(api.calls) == 1
    a.run()
    assert len(api.calls) == 1
    api.falla = False
    next(b for b in a.button if b.label == t("progreso.reintentar_evento", "es")).click().run()
    assert api.calls[0] == api.calls[1] and api.data["flashcards_vistas"] == 1
    assert not a.warning


def test_agregados_no_se_cachean_y_cambio_de_espacio_no_muestra_pendientes(monkeypatch):
    api = API()
    api.data.update(version=9, conceptos_revisados=7)
    a = app(api)
    assert any(m.value == "7" for m in a.metric)

    def no_disponible(token):
        raise APIError("SERVICE_UNAVAILABLE", "Privado", 503)

    monkeypatch.setattr(api, "get_progress", no_disponible)
    a.run()
    assert not a.metric and t("progreso.no_disponible", "es") in [i.value for i in a.info]
    api.falla = True
    a.button[0].click().run()
    a.button[1].click().run()
    assert a.warning
    a.session_state.workspace_id = "otro"
    a.session_state.cara = None
    a.run()
    assert not a.warning and not a.metric


def test_evento_estable_tras_f5_y_aislamiento_por_recurso():
    eventos = []
    for _ in range(2):
        session = {}
        e = cara_flashcard(session, "w", "g", "c", "dorso")
        assert not e["listo"] and not pendientes(session, "w")
        e = cara_flashcard(session, "w", "g", "c", "frente")
        eventos.append(e["event_id"])
        assert pendientes(session, "w") == [e] and not pendientes(session, "otro")
    assert eventos[0] == eventos[1]
    assert cara_flashcard({}, "w", "otra-gen", "c", "frente")["event_id"] != eventos[0]
    assert cara_flashcard({}, "otro-ws", "g", "c", "frente")["event_id"] != eventos[0]
    assert concepto_revisado({}, "w", "d", " VCN ")["event_id"] == concepto_revisado({}, "w", "d", "VCN")["event_id"]


@pytest.mark.parametrize("cara", ["canto", "", None])
def test_cara_invalida_no_registra(cara):
    with pytest.raises(ValueError):
        cara_flashcard({}, "w", "g", "c", cara)


@pytest.mark.parametrize(
    "defecto",
    [
        None,
        {"version": -1},
        {"quiz_total_primer_intento": 1},
        {"tiempo_estimado_restante_minutos": float("nan")},
        {"flashcards_vistas": True},
        {"quiz_aciertos_posteriores": 2},
    ],
)
def test_progreso_invalido_no_se_muestra(defecto):
    api = API()
    api.data = defecto if defecto is None else {**api.data, **defecto}
    with pytest.raises(APIError) as error:
        consultar_progreso(api, "token")
    assert error.value.code == "INVALID_RESPONSE"


def test_confirmacion_invalida_conserva_evento_para_comprobarlo(monkeypatch):
    api = API()
    e = concepto_revisado({}, "w", "d", "VCN")
    monkeypatch.setattr(api, "record_progress", lambda *args: {"event_id": "ajeno", "version": 1})
    with pytest.raises(APIError):
        enviar_evento(api, "token", e)
    assert not e["confirmado"] and e["intentado"]


def test_payloads_compatibles_con_contrato_sin_puntajes():
    from app.schemas.requests import ProgressEvent

    api = API()
    e = concepto_revisado({}, "w", "d", "VCN", "g")
    enviar_evento(api, "token", e)
    e = cara_flashcard({}, "w", "g", "c", "frente")
    e["caras"].add("dorso")
    e["listo"] = True
    enviar_evento(api, "token", e)
    for payload in api.calls:
        assert ProgressEvent.model_validate(payload).model_dump(exclude_none=True) == payload
        assert payload["tipo"] != "respuesta_quiz"


def test_un_pendiente_no_bloquea_el_reintento_de_otro():
    api = API()
    api.falla = True
    a = app(api)
    a.button[0].click().run()
    a.button[1].click().run()
    next(b for b in a.button if "VCN" in b.label).click().run()
    assert len(api.calls) == 2 and len(a.selectbox[0].options) == 2
    pendiente = next(
        e for e in a.session_state["_progreso_eventos_ui"].values() if e["datos"]["tipo"] == "concepto_revisado"
    )
    a.selectbox[0].set_value(pendiente["event_id"]).run()
    api.falla = False
    next(b for b in a.button if b.label == t("progreso.reintentar_evento", "es")).click().run()
    assert api.data["conceptos_revisados"] == 1 and api.data["flashcards_vistas"] == 0
    assert len(api.calls) == 3


def test_sesion_revocada_limpia_presentacion_y_metricas(monkeypatch):
    api = API()
    a = app(api)
    a.button[0].click().run()
    a.button[1].click().run()

    def revocada(token):
        raise APIError("SESSION_INVALID", "Privado", 401)

    monkeypatch.setattr(api, "get_progress", revocada)
    a.run()
    assert not a.exception and not a.metric
    assert a.session_state.session_token is None
    assert "_progreso_eventos_ui" not in a.session_state
