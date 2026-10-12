"""Compatibilidad y caché verificable; el canónico sigue siendo la única fuente."""

import threading
from dataclasses import dataclass

from app.exports.anki import exportar_apkg, exportar_texto
from app.exports.markdown import renderizar_markdown
from app.exports.pdf import renderizar_pdf
from app.schemas.enums import PedagogicalFormat
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.storage.object_keys import clave_export
from app.storage.provider import StorageConflict, StorageNotFound, StorageUnavailable

_TODOS = frozenset(f.value for f in PedagogicalFormat)
COMPATIBILIDAD = {
    "json": _TODOS,
    "md": _TODOS,
    "pdf": _TODOS,
    "csv": frozenset({"flashcards"}),
    "tsv": frozenset({"flashcards"}),
    "apkg": frozenset({"flashcards"}),
}
TIPOS_IMPLEMENTADOS = {
    "json": "application/json",
    "md": "text/markdown; charset=utf-8",
    "pdf": "application/pdf",
    "csv": "text/csv; charset=utf-8",
    "tsv": "text/tab-separated-values; charset=utf-8",
    "apkg": "application/octet-stream",
}


def renderizar(paquete, canonico, formato):
    if formato == "json":
        return canonico
    if formato == "md":
        return renderizar_markdown(paquete)
    if formato == "pdf":
        return renderizar_pdf(paquete)
    if formato in ("csv", "tsv"):
        return exportar_texto(paquete, formato)
    if formato == "apkg":
        return exportar_apkg(paquete)
    raise ValueError("Exportador no implementado")


def validar_formato(tipo, formato):
    if tipo not in COMPATIBILIDAD.get(formato, ()):
        raise ErrorAplicacion(ErrorCode.EXPORT_INCOMPATIBLE, "El formato de descarga es incompatible con el contenido.")
    if formato not in TIPOS_IMPLEMENTADOS:
        raise ErrorAplicacion(
            ErrorCode.EXPORT_INCOMPATIBLE,
            "El exportador pedido aún no está disponible en esta versión.",
            details={"formatos_disponibles": list(TIPOS_IMPLEMENTADOS)},
        )


@dataclass(frozen=True)
class Exportacion:
    contenido: bytes
    content_type: str
    nombre: str


class ServicioExportaciones:
    def __init__(self, catalogo, *, guardia=None):
        self.catalogo, self.storage = catalogo, catalogo.storage
        # Compartida con la retirada documental: un GET legacy no recrea
        # derivados después de que su documento ya fue purgado.
        self._lock = guardia if guardia is not None else threading.RLock()

    def _cache(self, ws, gid, formato, esperado, chequear):
        clave = clave_export(ws, gid, formato)
        chequear()
        try:
            almacenado = self.storage.get(clave)
        except StorageNotFound:
            chequear()
            try:
                self.storage.upload(clave, esperado, content_type=TIPOS_IMPLEMENTADOS[formato], crear_solo=True)
            except StorageConflict:
                pass  # Otra solicitud escribió la misma vista; se comprueba abajo.
            almacenado = self.storage.get(clave)
        if almacenado != esperado:
            raise StorageUnavailable("La exportación almacenada no corresponde al canónico aprobado")
        chequear()
        return almacenado

    def preparar(self, ws, paquete, canonico, chequear):
        """Antes de completed, confirma las vistas compatibles sin repetir IA."""
        with self._lock:
            chequear()
            for formato in TIPOS_IMPLEMENTADOS:
                if paquete.contenido_adaptado.tipo in COMPATIBILIDAD[formato]:
                    chequear()
                    datos = renderizar(paquete, canonico, formato)
                    self._cache(ws, paquete.generation_id, formato, datos, chequear)

    def descargar(self, token, generation_id, formato="json"):
        with self._lock:
            paquete, canonico = self.catalogo.cargar(token, generation_id)
            validar_formato(paquete.contenido_adaptado.tipo, formato)
            fila, _ = self.catalogo.fuente(token, generation_id)
            esperado = renderizar(paquete, canonico, formato)
            datos = self._cache(
                fila["workspace_id"],
                generation_id,
                formato,
                esperado,
                lambda: self.catalogo.validar_fuente(token, paquete),
            )
            # Solo IDs generados para el nombre: el título nunca llega a headers.
            return Exportacion(datos, TIPOS_IMPLEMENTADOS[formato], generation_id + "." + formato)
