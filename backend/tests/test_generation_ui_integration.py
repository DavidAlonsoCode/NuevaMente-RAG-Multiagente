"""Panel Streamlit -> API -> worker/grafo/Chroma/storage reales, IA con doble explícito."""

import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
import test_api_generate as generaciones
from api_client import FORMATOS, PERFILES, APIClient
from i18n import t
from streamlit.runtime.pages_manager import PagesManager
from streamlit.testing.v1 import AppTest

entorno_generacion = generaciones.entorno

pytestmark = pytest.mark.integration_mock
APP = Path(__file__).resolve().parents[2] / "frontend" / "app.py"


@pytest.fixture(autouse=True)
def aislar_navegacion_streamlit(monkeypatch):
    # Streamlit mantiene este indicador en la clase, incluso entre AppTest.
    # Restaurarlo permite que cada script derive su modo de navegación de su
    # propio directorio; los scripts temporales de from_string no tienen pages/.
    monkeypatch.setattr(PagesManager, "uses_pages_directory", None)


@pytest.mark.parametrize("perfil", PERFILES)
@pytest.mark.parametrize("formato", FORMATOS)
def test_formatos_perfiles_del_panel_completan_el_pipeline(entorno_generacion, monkeypatch, perfil, formato):
    e = entorno_generacion
    admisiones = []

    def solicitar(method, url, *, json=None, params=None, headers=None, **kwargs):
        response = e.cliente.request(method, urlsplit(url).path, json=json, params=params, headers=headers)
        if method == "POST" and urlsplit(url).path == "/api/generate":
            assert response.status_code == 202, response.text
            admisiones.append(response.json())
        return SimpleNamespace(
            ok=response.is_success,
            status_code=response.status_code,
            headers=response.headers,
            json=response.json,
            content=response.content,
        )

    monkeypatch.setattr(requests, "request", solicitar)
    monkeypatch.setattr(APIClient, "create_workspace", lambda _: e.ws)
    app = AppTest.from_file(str(APP)).run()
    assert not app.exception
    assert app.selectbox(key="gen_document_id").value == e.doc
    index = PERFILES.index(perfil) * len(FORMATOS) + FORMATOS.index(formato)
    app.selectbox(key="gen_perfil").set_value(perfil).run()
    app.selectbox(key="gen_formato").set_value(formato).run()
    app.selectbox(key="gen_idioma_salida").set_value(("es", "en", "pt")[index % 3]).run()
    app.radio(key="gen_alcance").set_value("seccion" if index % 2 else "documento_completo").run()
    next(b for b in app.button if b.label == t("sidebar.generar")).click().run()
    next(b for b in app.button if b.label == t("generar.confirmar")).click().run()
    assert not app.exception
    # Cada caso tiene su almacenamiento aislado y conserva el límite de 5000
    # solicitudes. El polling tampoco añade llamadas sin pausa.
    for _ in range(60):
        time.sleep(0.5)
        consulta = e.cliente.get(admisiones[-1]["status_url"], headers=e.headers)
        assert consulta.status_code == 200, consulta.text
        resultado = consulta.json()
        if resultado["status"] not in ("queued", "running"):
            break
    assert resultado["status"] == "completed", resultado
    metadata = resultado["contenido"]["metadatos"]
    assert metadata["perfil_aplicado"] == perfil
    assert metadata["formato_generado"] == formato
    assert metadata["idioma_salida"] == ("es", "en", "pt")[index % 3]
    solicitado = app.session_state.generacion_activa["parametros"]["alcance"]
    assert all(metadata["alcance"][k] == v for k, v in solicitado.items())
    assert len(admisiones) == 1
