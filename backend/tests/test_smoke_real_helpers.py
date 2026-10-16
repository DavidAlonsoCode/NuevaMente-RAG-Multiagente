"""Validadores del smoke con artefactos simulados; no acreditan proveedores reales."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.config import Configuracion
from app.exports.router import renderizar
from app.smoke_real import ejecutar, validar_exportaciones
from test_schemas import TODOS_LOS_FORMATOS, salida_valida

pytestmark = pytest.mark.integration_mock


@pytest.fixture
def exportaciones():
    paquete = salida_valida(deepcopy(TODOS_LOS_FORMATOS["flashcards"][0]))
    canonico = paquete.model_dump_json().encode()
    return paquete, {f: renderizar(paquete, canonico, f) for f in ("json", "md", "pdf", "apkg")}


def test_abre_anki_pdf_y_fuentes_markdown_escapadas(exportaciones):
    paquete, archivos = exportaciones
    assert r"chk\_" in archivos["md"].decode()
    resultado = validar_exportaciones(paquete, archivos)
    assert resultado["paginas_pdf"] >= 1
    assert resultado["notas_anki"] == resultado["tarjetas_anki"] == len(paquete.contenido_adaptado.items)


@pytest.mark.parametrize(
    "formato,corrupto",
    [("md", b"Sin fuentes"), ("pdf", b"%PDF-invalido"), ("apkg", b"PK-archivo-invalido"), ("json", b"{}")],
)
def test_no_confunde_extension_con_exportacion_valida(exportaciones, formato, corrupto):
    paquete, archivos = exportaciones
    archivos[formato] = corrupto
    with pytest.raises(Exception):
        validar_exportaciones(paquete, archivos)


def test_smoke_rechaza_mock_antes_de_consumir_proveedores(tmp_path):
    servicio = SimpleNamespace(generador=SimpleNamespace(es_mock=True), verificador=SimpleNamespace(es_mock=True))
    app = SimpleNamespace(state=SimpleNamespace(storage_provider=None, generations=servicio, db=None))
    with pytest.raises(ValueError, match="simulados"):
        ejecutar(app, Configuracion(mock_oci=True, mock_gemini=True), b"texto", "fixture.txt", tmp_path)
    assert not list(tmp_path.iterdir())
