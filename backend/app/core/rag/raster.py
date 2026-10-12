"""PDFium aislado: límites antes del bitmap y cancelación durante el rasterizado."""

import io
import math
import multiprocessing
import time
from contextlib import closing
from dataclasses import dataclass

from app.jobs.manager import ContextoEjecucion

VERSION_RASTER = "pdfium-5.14.0-page-png-v1"


class RasterError(ValueError):
    """No se pudo obtener una imagen original dentro de los límites."""


@dataclass(frozen=True)
class PaginaRasterizada:
    contenido: bytes
    ancho: int
    alto: int

    @property
    def tokens_estimados(self) -> int:
        # Reserva conservadora para imágenes Gemini 2.5, no factura del SDK.
        return 258 * math.ceil(self.ancho / 360) * math.ceil(self.alto / 360)


def _raster_worker(canal, original: bytes, numero: int):
    try:
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        except ImportError:
            pass  # Windows: presupuesto de reloj y límites del bitmap.
        import pypdfium2 as pdfium

        with pdfium.PdfDocument(original) as documento:
            if not 1 <= numero <= len(documento) <= 100:
                raise RasterError("Página fuera de límites")
            with closing(documento[numero - 1]) as pagina:
                ancho, alto = pagina.get_size()
                if any(not math.isfinite(n) or not 0 < n <= 1_000_000 for n in (ancho, alto)):
                    raise RasterError("Dimensiones de página inválidas")
                escala = min(2.5, 2000 / max(ancho, alto), math.sqrt(3_000_000 / ancho / alto))
                with closing(pagina.render(scale=escala)) as bitmap:
                    imagen = bitmap.to_pil()
                    try:
                        ancho_px, alto_px = imagen.size
                        if ancho_px * alto_px > 3_010_000 or max(ancho_px, alto_px) > 2001:
                            raise RasterError("Bitmap fuera de límites")
                        salida = io.BytesIO()
                        imagen.save(salida, format="PNG")
                        contenido = salida.getvalue()
                        if len(contenido) > 8_000_000:
                            raise RasterError("Imagen original demasiado grande")
                    finally:
                        imagen.close()  # Antes de liberar el buffer compartido PDFium.
        canal.send((True, contenido, ancho_px, alto_px))
    except Exception:
        canal.send((False,))  # Nunca propagar texto del PDF ni errores nativos.
    finally:
        canal.close()


def rasterizar_pagina(original: bytes, pagina: int, *, contexto: ContextoEjecucion) -> PaginaRasterizada:
    contexto.chequear()
    if b"%PDF-" not in original[:1024] or len(original) > 20 * 1024 * 1024 or type(pagina) is not int:
        raise RasterError("Fuente PDF inválida")
    if not 1 <= pagina <= 100:
        raise RasterError("Página fuera de límites")
    proceso_ctx = multiprocessing.get_context("spawn")
    receptor, emisor = proceso_ctx.Pipe(duplex=False)
    proceso = proceso_ctx.Process(target=_raster_worker, args=(emisor, original, pagina), daemon=True)
    try:
        proceso.start()
        emisor.close()
        limite = min(contexto.deadline, time.monotonic() + 30)
        while not receptor.poll(0.02):
            contexto.chequear()
            if time.monotonic() >= limite or not proceso.is_alive():
                raise RasterError("El rasterizado no pudo completarse dentro del presupuesto")
        contexto.chequear()
        try:
            resultado = receptor.recv()
        except EOFError:
            raise RasterError("PDFium no pudo completar la página") from None
        if not resultado[0]:
            raise RasterError("PDFium no pudo completar la página")
        contexto.chequear()
        return PaginaRasterizada(resultado[1], resultado[2], resultado[3])
    finally:
        receptor.close()
        emisor.close()
        if proceso.pid is not None:
            if proceso.is_alive():
                proceso.terminate()
            proceso.join(timeout=2)
            if proceso.is_alive():
                proceso.kill()
                proceso.join(timeout=2)
            proceso.close()
