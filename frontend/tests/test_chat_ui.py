"""Conversación, aislamiento y espera cancelable sin publicar borradores."""

import sys
from copy import deepcopy
from types import SimpleNamespace

import pytest
import requests
from api_client import APIClient, APIError
from chat_flow import Conversacion, TurnoChat, conversacion_actual, pendiente_del_espacio
from i18n import t
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def aislar_principal_streamlit(monkeypatch):
    # AppTest reemplaza __main__; spawn debe conservar la entrada original.
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])


def trabajo(job_id="job1", status="queued", result=None):
    return {"job_id": job_id, "status": status, "result": result, "error": None}


def respuesta(payload, *, fuera=False):
    return {
        **payload,
        "document_hash": "a" * 64,
        "estado": "fuera_de_alcance" if fuera else "respondida",
        "respuesta": "Sin evidencia suficiente." if fuera else "Respuesta confirmada con evidencia.",
        "referencias": [] if fuera else [{"chunk_id": "chunk", "pagina": 1, "seccion": "Redes"}],
        "evaluacion_calidad": None
        if fuera
        else {
            "estado_evaluacion": "aprobada",
            "razones_bloqueo": [],
            "cobertura_objetivos": "completa",
            "verificacion_visual": "no_aplica",
            "claridad_pedagogica": "alta",
            "adecuacion_perfil": "alta",
            "coherencia_didactica": "alta",
            "anclaje_fuente_score": 1.0,
            "cantidad_afirmaciones": 1,
            "cantidad_respaldadas": 1,
        },
    }


class Cliente:
    def __init__(self):
        self.envios, self.cancelaciones = [], []
        self.jobs, self.intenciones = {}, {}
        self.error_envio, self.error_documento = None, None

    def get_document(self, token, document_id):
        if self.error_documento:
            raise self.error_documento
        return {"document_id": document_id, "status": "ready"}

    def create_chat(self, token, payload, *, idempotency_key):
        self.envios.append((deepcopy(payload), idempotency_key))
        if self.error_envio:
            error, self.error_envio = self.error_envio, None
            raise error
        if idempotency_key not in self.intenciones:
            job_id = "job" + str(len(self.jobs) + 1)
            self.intenciones[idempotency_key] = job_id
            self.jobs[job_id] = trabajo(job_id)
        return self.jobs[self.intenciones[idempotency_key]]

    def get_job(self, token, job_id):
        return self.jobs[job_id]

    def cancel_job(self, token, job_id):
        self.cancelaciones.append(job_id)
        self.jobs[job_id] = trabajo(job_id, "running")
        return self.jobs[job_id]


@pytest.fixture
def ui(monkeypatch):
    monkeypatch.setattr(PagesManager, "uses_pages_directory", None)
    api = Cliente()
    app = AppTest.from_string("""
import streamlit as st
from components.chat import render_chat
if "api" in st.session_state:
    render_chat(st.session_state.api, st.session_state.lang, token="token-de-prueba",
        workspace_id="ws", document_id=st.session_state.doc,
        perfil_destinatario="principiante", idioma_salida=st.session_state.salida)
""")
    for k, v in {"api": api, "lang": "es", "doc": "doc", "salida": "es"}.items():
        app.session_state[k] = v
    app.run()
    assert not app.exception
    return app, api


def boton(app, etiqueta):
    return next(b for b in app.button if b.label == etiqueta)


def test_cinco_turnos_reruns_y_cambio_de_documento_no_mezclan_historial(ui):
    app, api = ui
    for n in range(5):
        app.chat_input[0].set_value("Pregunta " + str(n)).run()
        assert not app.exception and app.chat_input[0].disabled
        body, clave = api.envios[-1]
        job_id = api.intenciones[clave]
        api.jobs[job_id] = trabajo(job_id, "completed", respuesta(body))
        app.run()
        assert not app.exception and not app.chat_input[0].disabled
    assert len(api.envios) == 5 and len({k for _, k in api.envios}) == 5
    c = app.session_state.chat_conversaciones[("ws", "doc", "principiante", "es")]
    assert len(c.turnos) == 5 and all(x.respuesta["referencias"] for x in c.turnos)
    app.session_state.doc = "otro"
    app.run()
    assert not any(m.value.startswith("Pregunta") for m in app.markdown)
    assert not app.session_state.chat_conversaciones[("ws", "otro", "principiante", "es")].turnos
    app.session_state.doc = "doc"
    app.run()
    assert len(api.envios) == 5 and len(c.turnos) == 5


def test_envio_indeterminado_conserva_cuerpo_clave_e_idioma_de_contenido(ui):
    app, api = ui
    api.error_envio = APIError("SERVICE_UNAVAILABLE", "dato privado", 503)
    app.chat_input[0].set_value("Pregunta pendiente").run()
    assert not app.exception and app.chat_input[0].disabled
    app.session_state.lang = "en"
    app.run()
    assert len(api.envios) == 1 and "dato privado" not in str(app)
    boton(app, t("chat.reenviar", "en")).click().run()
    assert not app.exception and api.envios[0] == api.envios[1]
    assert api.envios[-1][0]["idioma_salida"] == "es"


def test_cancelacion_cooperativa_no_libera_hilo_hasta_terminal(ui):
    app, api = ui
    app.chat_input[0].set_value("Pregunta cancelable").run()
    boton(app, t("chat.cancelar")).click().run()
    assert api.cancelaciones == ["job1"] and app.chat_input[0].disabled
    assert boton(app, t("chat.cancelar")).disabled
    api.jobs["job1"] = trabajo(status="cancelled")
    app.run()
    assert not app.exception and not app.chat_input[0].disabled
    assert len(api.envios) == 1
    assert any(m.value == t("chat.cancelada") for m in app.caption)


@pytest.mark.parametrize("status", ["failed", "rejected_quality", "cancelled"])
def test_terminal_no_publica_borrador_aunque_lo_reciba(ui, status):
    app, api = ui
    app.chat_input[0].set_value("Pregunta").run()
    api.jobs["job1"] = trabajo(status=status, result={"respuesta": "BORRADOR_PRIVADO"})
    app.run()
    assert not app.exception and not app.chat_input[0].disabled
    assert "BORRADOR_PRIVADO" not in str(app)


def test_respuesta_de_otro_documento_no_se_publica(ui):
    app, api = ui
    app.chat_input[0].set_value("Pregunta").run()
    body = api.envios[-1][0]
    api.jobs["job1"] = trabajo(status="completed", result=respuesta({**body, "document_id": "ajeno"}))
    app.run()
    assert not app.exception and app.chat_input[0].disabled
    assert "Respuesta confirmada con evidencia." not in str(app)


def test_documento_retirado_descarta_historial(ui):
    app, api = ui
    app.chat_input[0].set_value("Pregunta").run()
    api.jobs["job1"] = trabajo(status="completed", result=respuesta(api.envios[-1][0]))
    app.run()
    api.error_documento = APIError("NOT_FOUND", "dato privado", 404)
    app.run()
    assert not app.exception and not app.session_state.chat_conversaciones
    assert not app.chat_input and "Respuesta confirmada con evidencia." not in str(app)


@pytest.mark.parametrize("idioma", ["es", "en", "pt"])
def test_abstencion_y_etiquetas_separan_idiomas(ui, idioma):
    app, api = ui
    app.session_state.lang = idioma
    app.session_state.salida = "pt"
    app.run()
    app.chat_input[0].set_value("Pregunta sin evidencia").run()
    assert api.envios[-1][0]["idioma_salida"] == "pt"
    api.jobs["job1"] = trabajo(status="completed", result=respuesta(api.envios[-1][0], fuera=True))
    app.run()
    assert not app.exception and any(i.value == "Sin evidencia suficiente." for i in app.info)


def test_otra_configuracion_no_abre_un_segundo_turno_pendiente():
    conversaciones = {}
    a = conversacion_actual(conversaciones, "ws", "doc", "principiante", "es")
    turno = a.iniciar("Pregunta")
    b = conversacion_actual(conversaciones, "ws", "otro", "principiante", "es")
    assert not b.turnos and pendiente_del_espacio(conversaciones, "ws") is turno
    assert pendiente_del_espacio(conversaciones, "otro-ws") is None
    with pytest.raises(ValueError):
        a.limpiar_vista()


def test_limpiar_vista_no_llama_api_ni_simula_borrado_de_memoria():
    c = Conversacion("doc", "principiante", "es")
    turno = c.iniciar("Pregunta")
    turno.status = "cancelled"
    c.limpiar_vista()
    assert not c.turnos


def test_job_de_otro_id_y_respuesta_invalida_no_cambian_terminal():
    t1 = TurnoChat("doc", "Pregunta", "principiante", "es")
    t1.recibir(trabajo())
    with pytest.raises(APIError):
        t1.recibir(trabajo("ajeno", "completed", respuesta(t1.payload())))
    assert t1.status == "queued" and t1.respuesta is None
    with pytest.raises(APIError):
        t1.recibir(trabajo(status="completed", result=None))


def test_cliente_no_envia_historial_tokens_en_url_ni_sigue_redirects(monkeypatch):
    llamadas = []

    def solicitar(**kwargs):
        llamadas.append(kwargs)
        return SimpleNamespace(ok=True, status_code=202, headers={}, json=lambda: trabajo())

    monkeypatch.setattr(requests, "request", solicitar)
    api = APIClient()
    body = TurnoChat("doc", "Pregunta", "principiante", "es").payload()
    api.create_chat("token", body, idempotency_key="intencion")
    api.cancel_job("token", "job/con espacios")
    assert llamadas[0]["headers"]["Idempotency-Key"] == "intencion"
    assert llamadas[0]["headers"]["Authorization"] == "Bearer token"
    assert llamadas[0]["json"] == body and "historial" not in body
    assert llamadas[1]["url"].endswith("/api/jobs/job%2Fcon%20espacios/cancel")
    assert all(r["allow_redirects"] is False for r in llamadas)


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("cobertura_objetivos", "parcial"),
        ("verificacion_visual", "insuficiente"),
        ("cantidad_respaldadas", 0),
        ("anclaje_fuente_score", None),
        ("claridad_pedagogica", "baja"),
    ],
)
def test_calidad_incompatible_no_publica_una_respuesta(ui, campo, valor):
    app, api = ui
    app.chat_input[0].set_value("Pregunta").run()
    datos = respuesta(api.envios[-1][0])
    datos["evaluacion_calidad"][campo] = valor
    api.jobs["job1"] = trabajo(status="completed", result=datos)
    app.run()
    assert not app.exception and app.chat_input[0].disabled
    assert not any("Respuesta confirmada con evidencia." in m.value for m in app.markdown)
