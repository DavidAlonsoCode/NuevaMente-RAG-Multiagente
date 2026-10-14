"""#42 sobre API, SQLite y worker reales; OCI/Gemini explícitamente simulados."""

import sys
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
import test_api_generate as generaciones
from api_client import APIClient
from history_flow import cargar_detalle, cargar_pagina, comparar, estado_historial
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


def test_historial_comparacion_reapertura_export_y_recuperacion_sin_llm(entorno, api):
    e = entorno
    ids = []
    for params in (
        {},
        {"perfil_destinatario": "ejecutivo", "formato_salida": "resumen_ejecutivo", "idioma_salida": "pt"},
    ):
        recibo = generaciones.generar(e, **params).json()
        generaciones.esperar(e, recibo)
        ids.append(recibo["generation_id"])
    token = e.ws["token"]
    contador = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    state = estado_historial({}, e.ws["workspace_id"])
    pagina = cargar_pagina(api, token, state, {}, limite=1)
    assert len(pagina["items"]) == 1 and pagina["next_cursor"]
    state["cursor"] = pagina["next_cursor"]
    segunda = cargar_pagina(api, token, state, {}, limite=1)
    assert segunda["items"][0]["generation_id"] != pagina["items"][0]["generation_id"]
    paquetes = comparar(api, token, *ids)
    assert [p["metadatos"]["formato_generado"] for p in paquetes] == ["flashcards", "resumen_ejecutivo"]
    assert [p["metadatos"]["idioma_salida"] for p in paquetes] == ["es", "pt"]
    assert cargar_detalle(api, token, ids[0])["contenido"] == paquetes[0]
    assert b'"generation_id"' in api.download_export(token, ids[0], "json")
    assert api.download_export(token, ids[1], "md")
    recuperado = api.recover_session(e.ws["recovery_code"])
    assert {
        i["generation_id"]
        for i in cargar_pagina(api, recuperado["token"], estado_historial({}, recuperado["workspace_id"]), {})["items"]
    } == set(ids)
    a = AppTest.from_string("""
import streamlit as st
from components.history import render_historial_completo
if "api" in st.session_state:
    render_historial_completo(st.session_state.api,"es",render_resultado=lambda paquete, idioma, key_prefix: st.write(paquete["metadatos"]["formato_generado"]))
""")
    for k, v in dict(api=api, workspace_id=recuperado["workspace_id"], session_token=recuperado["token"]).items():
        a.session_state[k] = v
    a.run()
    assert not a.exception and any("30 días" in c.value for c in a.caption)
    for i, gid in enumerate(ids):
        next(s for s in a.selectbox if s.label == "Adaptación " + ("A" if i == 0 else "B")).set_value(gid).run()
    assert not a.exception
    assert {"flashcards", "resumen_ejecutivo"} <= {m.value for m in a.markdown}
    assert contador == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)


def test_filtros_reales_y_usuario_ajeno(entorno, api):
    e = entorno
    recibo = generaciones.generar(e).json()
    generaciones.esperar(e, recibo)
    s = estado_historial({}, e.ws["workspace_id"])
    assert (
        len(
            cargar_pagina(
                api,
                e.ws["token"],
                s,
                {"documento": e.doc, "perfil": "principiante", "formato": "flashcards", "idioma": "es"},
            )["items"]
        )
        == 1
    )
    assert not cargar_pagina(api, e.ws["token"], s, {"perfil": "ejecutivo"})["items"]
    ajeno = api.create_workspace()
    assert not cargar_pagina(api, ajeno["token"], estado_historial({}, ajeno["workspace_id"]), {})["items"]


def test_navegacion_desde_home_al_historial(entorno, api):
    from pathlib import Path

    e = entorno
    recibo = generaciones.generar(e).json()
    generaciones.esperar(e, recibo)
    a = AppTest.from_file(str(Path(__file__).resolve().parents[2] / "frontend" / "app.py"))
    a.session_state.workspace_id = e.ws["workspace_id"]
    a.session_state.session_token = e.ws["token"]
    a.run()
    assert not a.exception
    a.button(key="historial:abrir:" + e.ws["workspace_id"]).click().run()
    assert not a.exception and a.session_state.historial_abierto
    assert any(h.value == "Historial de generaciones" for h in a.subheader)
    a.button(key="historial:" + e.ws["workspace_id"] + ":open:" + recibo["generation_id"]).click().run()
    assert not a.exception and a.session_state.historial_detalle == recibo["generation_id"]
    assert any("Redes VCN" in m.value for m in a.markdown)
