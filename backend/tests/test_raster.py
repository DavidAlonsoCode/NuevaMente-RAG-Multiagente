"""PDFium real con límites; sin llamadas a proveedores externos."""

import io
import time

import pytest
from app.core.rag.raster import RasterError, rasterizar_pagina
from app.jobs.manager import ContextoEjecucion, DeadlineExcedidoError, TrabajoCanceladoError
from PIL import Image

pytestmark = pytest.mark.unit


def contexto():
    return ContextoEjecucion("raster_test", "ws", "ingestion", time.monotonic() + 30)


def test_raster_vcn_conserva_pagina_y_limita_recursos(documento_demo_vcn):
    imagen = rasterizar_pagina(documento_demo_vcn.read_bytes(), 3, contexto=contexto())
    assert imagen.contenido.startswith(b"\x89PNG") and len(imagen.contenido) <= 8_000_000
    with Image.open(io.BytesIO(imagen.contenido)) as png:
        assert png.size == (imagen.ancho, imagen.alto)
        assert max(png.size) <= 2001 and png.width * png.height <= 3_010_000
    assert imagen.tokens_estimados >= 258


@pytest.mark.parametrize("pagina", [0, 101, True])
def test_rechaza_pagina_fuera_de_limites(documento_demo_vcn, pagina):
    with pytest.raises(RasterError):
        rasterizar_pagina(documento_demo_vcn.read_bytes(), pagina, contexto=contexto())


@pytest.mark.parametrize("estado", ["cancelado", "vencido"])
def test_raster_respeta_contexto_antes_de_abrir_pdf(documento_demo_vcn, estado):
    ctx = contexto()
    if estado == "cancelado":
        ctx._evento_cancelacion.set()
    else:
        ctx.deadline = time.monotonic() - 1
    with pytest.raises(TrabajoCanceladoError if estado == "cancelado" else DeadlineExcedidoError):
        rasterizar_pagina(documento_demo_vcn.read_bytes(), 3, contexto=ctx)


def test_pagina_inexistente_y_pdf_corrupto_terminan_sin_bitmap(crear_pdf):
    with pytest.raises(RasterError):
        rasterizar_pagina(crear_pdf(paginas=1).read_bytes(), 2, contexto=contexto())
    with pytest.raises(RasterError):
        rasterizar_pagina(b"%PDF-1.7 corrupto", 1, contexto=contexto())
