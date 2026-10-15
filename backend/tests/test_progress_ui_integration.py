"""#41: cliente/panel, persistencia y recuperación con OCI/Gemini mock explícitos."""

import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
import test_api_generate as generaciones
from api_client import APIClient, APIError
from progress_flow import cara_flashcard, concepto_revisado, consultar_progreso, enviar_evento
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest

pytestmark = pytest.mark.integration_mock
entorno = generaciones.entorno


@pytest.fixture(autouse=True)
def aislar_principal_streamlit(monkeypatch):
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])
    monkeypatch.setattr(PagesManager, "uses_pages_directory", None)


@pytest.fixture
def api(entorno, monkeypatch):
    def request(method, url, **kw):
        r = entorno.cliente.request(
            method, urlsplit(url).path, json=kw.get("json"), params=kw.get("params"), headers=kw.get("headers")
        )
        return SimpleNamespace(
            ok=r.is_success, status_code=r.status_code, headers=r.headers, json=r.json, content=r.content
        )

    monkeypatch.setattr(requests, "request", request)
    return APIClient(base_url="http://backend.test")


def test_ambas_caras_recuperacion_exacta_y_nueva_presentacion(entorno, api):
    e, s = entorno, {}
    recibo = generaciones.generar(e).json()
    paquete = generaciones.esperar(e, recibo)["contenido"]
    gid, fid = recibo["generation_id"], paquete["contenido_adaptado"]["items"][0]["id"]
    ws, token = e.ws["workspace_id"], e.ws["token"]
    evento = cara_flashcard(s, ws, gid, fid, "frente")
    assert not enviar_evento(api, token, evento)
    assert consultar_progreso(api, token)["flashcards_vistas"] == 0
    evento = cara_flashcard(s, ws, gid, fid, "dorso")
    assert enviar_evento(api, token, evento)
    enviar_evento(api, token, concepto_revisado(s, ws, e.doc, "VCN", gid))
    confirmado = consultar_progreso(api, token)
    assert confirmado["flashcards_vistas"] == confirmado["conceptos_revisados"] == 1
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    nuevo = api.recover_session(e.ws["recovery_code"])
    assert nuevo["workspace_id"] == ws
    assert consultar_progreso(api, nuevo["token"]) == confirmado
    a = AppTest.from_string("""
import streamlit as st
from components.progress import render_progreso
if "api" in st.session_state:
    render_progreso(st.session_state.api, "es")
""")
    for k, v in dict(api=api, workspace_id=ws, session_token=nuevo["token"]).items():
        a.session_state[k] = v
    a.run()
    assert not a.exception
    assert any(m.label == "Flashcards repasadas" and m.value == "1" for m in a.metric)
    # F5 crea otra presentación, pero la actividad tiene el mismo ID estable.
    nuevo_estado = {}
    cara_flashcard(nuevo_estado, ws, gid, fid, "dorso")
    otra = cara_flashcard(nuevo_estado, ws, gid, fid, "frente")
    assert enviar_evento(api, nuevo["token"], otra)
    assert consultar_progreso(api, nuevo["token"]) == confirmado
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    ajeno = api.create_workspace()
    assert consultar_progreso(api, ajeno["token"])["flashcards_vistas"] == 0
    with pytest.raises(APIError) as error:
        api.record_progress(ajeno["token"], {**otra["datos"], "event_id": otra["event_id"]})
    assert error.value.status_code == 404


def test_respuesta_perdida_despues_de_persistir_no_duplica_version(entorno, api, monkeypatch):
    e = entorno
    evento = concepto_revisado({}, e.ws["workspace_id"], e.doc, "VCN")
    original, calls = api.record_progress, []

    def perder(token, payload):
        calls.append(dict(payload))
        r = original(token, payload)
        if len(calls) == 1:
            raise APIError("SERVICE_UNAVAILABLE", "Respuesta perdida", 503)
        return r

    monkeypatch.setattr(api, "record_progress", perder)
    with pytest.raises(APIError):
        enviar_evento(api, e.ws["token"], evento)
    antes = consultar_progreso(api, e.ws["token"])
    assert antes["conceptos_revisados"] == antes["version"] == 1
    assert enviar_evento(api, e.ws["token"], evento)
    assert calls[0] == calls[1]
    assert consultar_progreso(api, e.ws["token"]) == antes


def test_metricas_quiz_provienen_del_endpoint_de_respuesta(entorno, api):
    e = entorno
    recibo = generaciones.generar(e, formato_salida="quiz").json()
    generaciones.esperar(e, recibo)
    p = e.app.state.generations.canonico(e.ws["token"], recibo["generation_id"]).contenido_adaptado.preguntas[0]
    mala = next(o.option_id for o in p.opciones if o.option_id != p.correct_option_id)
    ruta = "/api/quizzes/" + recibo["generation_id"] + "/answers"
    for eid, oid in (("primero", mala), ("segundo", p.correct_option_id)):
        r = e.cliente.post(ruta, headers=e.headers, json={"question_id": p.id, "option_id": oid, "event_id": eid})
        assert r.status_code == 200
    datos = consultar_progreso(api, e.ws["token"])
    assert datos["quiz_intentos"] == 2 and datos["quiz_total_primer_intento"] == 1
    assert datos["quiz_aciertos_primer_intento"] == 0 and datos["quiz_aciertos_posteriores"] == 1
