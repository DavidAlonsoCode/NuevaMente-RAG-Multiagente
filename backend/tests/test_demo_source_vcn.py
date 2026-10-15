"""La fuente corregida conserva versión, lectura, visión y alcance negativo aislado."""

import hashlib
import json
import runpy
from pathlib import Path

import pytest
from app.core.rag.chunker import trocear
from app.core.rag.parser import parsear_archivo
from pypdf import PdfReader

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]


def test_pdf_v2_es_reproducible_y_coincide_con_su_version(tmp_path, monkeypatch):
    modulo = runpy.run_path(str(ROOT / "documents/generar_pdf_vcn.py"))
    salida = tmp_path / "vcn.pdf"
    monkeypatch.setitem(modulo["construir"].__globals__, "SALIDA", salida)
    modulo["construir"]()
    primero = salida.read_bytes()
    modulo["construir"]()
    assert salida.read_bytes() == primero == (ROOT / "documents/redes_vcn_oci.pdf").read_bytes()
    version = json.loads((ROOT / "documents/redes_vcn_oci.version.json").read_text(encoding="utf-8"))
    assert hashlib.sha256(primero).hexdigest() == version["sha256"]
    lector = PdfReader(salida, strict=True)
    assert len(lector.pages) == version["paginas"]
    texto = " ".join((p.extract_text() or "").replace("\n", " ") for p in lector.pages)
    assert "Versión v2" in texto and "253 direcciones asignables" in texto
    assert "254 restantes se asignan" not in texto and "NO es una casilla" not in texto
    assert "No exige recrear siempre" in texto and "Prohibidas por configuración" in texto


def test_parser_conserva_diagrama_y_chunk_insuficiente_sin_glosario():
    version = json.loads((ROOT / "documents/redes_vcn_oci.version.json").read_text(encoding="utf-8"))
    pdf = (ROOT / "documents/redes_vcn_oci.pdf").read_bytes()
    resultado = parsear_archivo(pdf, "redes_vcn_oci.pdf")
    assert version["diagrama"]["pagina"] in resultado.paginas_visuales
    chunks = [
        c.como_chunk_interno()
        for c in trocear(resultado, "prueba-vcn", "documento-v2", source_name="redes_vcn_oci.pdf")
    ]
    pagina = version["seccion_insuficiente"]["pagina"]
    acotados = [c for c in chunks if c.pagina == pagina]
    assert acotados and all(c.seccion_id == version["seccion_insuficiente"]["seccion_id"] for c in acotados)
    texto = " ".join(c.texto for c in acotados)
    assert "Costos y límites" in texto and "Glosario" not in texto
