"""Cliente y panel #40 contra backend real; OCI/Gemini explícitamente simulados."""

import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
import test_glossary as glosarios
from api_client import APIClient, APIError
from glossary_flow import admitir_glosario, consultar_glosario, estado_glosario
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest
from test_api_generate import esperar

pytestmark = pytest.mark.integration_mock
entorno = glosarios.entorno
entorno_generacion = glosarios.entorno_generacion


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


def test_panel_cache_citas_y_cambio_de_idioma_no_regenera(entorno, api):
    e, session = entorno, {}
    s = estado_glosario(session, e.ws["workspace_id"], e.doc, "principiante", "es")
    admitir_glosario(api, e.ws["token"], s)
    esperar(e, {"status_url": "/api/jobs/" + s["job_id"]})
    output = consultar_glosario(api, e.ws["token"], s)
    calls = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    a = AppTest.from_string("""
import streamlit as st
from components.glossary import render_glosario
if "api" in st.session_state:
    def fuentes(doc, refs):
        for ref in refs:
            dato = st.session_state.api._solicitar("GET",
                "/api/documents/" + doc + "/sources/" + ref["chunk_id"], token=st.session_state.session_token)
            st.write(dato["texto"])
    render_glosario(st.session_state.api, st.session_state.lang, document_id=st.session_state.doc,
                    perfil="principiante", idioma_salida="es", render_fuentes=fuentes)
""")
    for k, v in dict(
        api=api,
        session_token=e.ws["token"],
        workspace_id=e.ws["workspace_id"],
        doc=e.doc,
        lang="es",
        _glosarios_ui=session["_glosarios_ui"],
    ).items():
        a.session_state[k] = v
    a.run()
    assert not a.exception
    assert any(output["terminos"][0]["definicion"] == m.value for m in a.markdown)
    assert any("subredes privadas" in m.value for m in a.markdown)
    a.session_state.lang = "en"
    a.run()
    assert not a.exception and calls == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    admitir_glosario(api, e.ws["token"], s)  # Backend sirve la caché aprobada.
    assert consultar_glosario(api, e.ws["token"], s) == output
    assert calls == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    otro = api.create_workspace()
    with pytest.raises(APIError) as error:
        consultar_glosario(api, otro["token"], s)
    assert error.value.status_code == 404


def test_respuesta_perdida_reintenta_mismo_job(entorno, api, monkeypatch):
    e = entorno
    original, keys, jobs = api.create_glossary, [], []

    def perder(token, payload, *, idempotency_key):
        keys.append(idempotency_key)
        recibo = original(token, payload, idempotency_key=idempotency_key)
        jobs.append(recibo["job_id"])
        if len(keys) == 1:
            raise APIError("SERVICE_UNAVAILABLE", "Respuesta perdida", 503)
        return recibo

    monkeypatch.setattr(api, "create_glossary", perder)
    s = estado_glosario({}, e.ws["workspace_id"], e.doc, "principiante", "es")
    with pytest.raises(APIError):
        admitir_glosario(api, e.ws["token"], s)
    admitir_glosario(api, e.ws["token"], s)
    assert keys[0] == keys[1] and jobs[0] == jobs[1] == s["job_id"]
    esperar(e, {"status_url": "/api/jobs/" + s["job_id"]})
    assert consultar_glosario(api, e.ws["token"], s)["estado"] == "verificado"
