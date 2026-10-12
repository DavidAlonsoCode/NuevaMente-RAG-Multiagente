"""Lectura autorizada del aprobado, independiente de proveedores LLM o de la cola."""

from datetime import datetime, timezone

from pydantic import ValidationError

from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.responses import PedagogicalOutput
from app.session.manifests import ManifiestoEspacio
from app.storage.object_keys import clave_output, clave_workspace
from app.storage.provider import StorageNotFound, StorageUnavailable


class CatalogoGeneraciones:
    def __init__(self, db, storage, consulta, bucket):
        self.db, self.storage, self.consulta, self.bucket = db, storage, consulta, bucket

    def espacio(self, token):
        sesion = self.db.obtener_sesion(token)
        if sesion is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión no está disponible.")
        ws = sesion["workspace_id"]
        try:
            manifest = ManifiestoEspacio.model_validate_json(self.storage.get(clave_workspace(ws)))
        except StorageNotFound:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "El espacio no está disponible.") from None
        except ValidationError:
            raise StorageUnavailable("La autorización del espacio no es verificable") from None
        if manifest.workspace_id != ws:
            raise StorageUnavailable("Manifiesto de espacio ajeno")
        if manifest.borrado or manifest.borrado_en or manifest.expira_en <= datetime.now(timezone.utc):
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "El espacio no está disponible.")
        if self.db.obtener_sesion(token) is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión dejó de estar disponible.")
        return ws, manifest

    def canonico(self, token, generation_id):
        fila, fuente = self.fuente(token, generation_id)
        clave = clave_output(fila["workspace_id"], generation_id)
        try:
            paquete = PedagogicalOutput.model_validate_json(self.storage.get(clave))
        except (ValidationError, StorageNotFound):
            raise StorageUnavailable("El paquete publicado no es verificable") from None
        if (
            paquete.generation_id != generation_id
            or paquete.documento_fuente != fuente
            or paquete.almacenamiento_oci.objeto_id != clave
            or paquete.almacenamiento_oci.bucket != self.bucket
        ):
            raise StorageUnavailable("El paquete no corresponde a la fuente vigente")
        self.validar_fuente(token, paquete)
        return paquete

    def fuente(self, token, generation_id):
        sesion = self.db.obtener_sesion(token)
        if sesion is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión no está disponible.")
        fila = self.db.obtener_generacion(generation_id)
        if fila is None or fila["workspace_id"] != sesion["workspace_id"]:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Generación no encontrada.")
        _, manifest = self.espacio(token)
        if any(
            (r.recurso_tipo, r.recurso_id) in {("generation", generation_id), ("document", fila["document_id"])}
            for r in manifest.recursos_borrados
        ):
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Generación no encontrada.")
        if fila["status"] != "completed":
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La generación no está completada.")
        fuente = self.consulta.fuente_aprobada(token, fila["document_id"])
        if self.db.obtener_sesion(token) is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión dejó de estar disponible.")
        actual = self.db.obtener_generacion(generation_id)
        if actual is None:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Generación no encontrada.")
        if (actual["document_id"], actual["workspace_id"], actual["status"]) != (
            fila["document_id"],
            fila["workspace_id"],
            "completed",
        ):
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La generación cambió durante la consulta.")
        return actual, fuente

    def validar_fuente(self, token, paquete):
        """Revalida la identidad del aprobado inmutable sin volver a descargarlo."""
        _, fuente = self.fuente(token, paquete.generation_id)
        if fuente != paquete.documento_fuente:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La fuente cambió durante la consulta.")
