"""Expiración y purga física durables; usa el sincronizador existente, sin otra cola."""

import logging
from datetime import datetime, timedelta, timezone

from app.session.manifests import sincronizar_manifiesto
from app.session.recovery import _objetos
from app.storage.object_keys import clave_workspace
from app.storage.oci_budget import mantenimiento_storage
from app.storage.provider import StorageNotFound, StorageUnavailable

logger = logging.getLogger("nuevamente.workspace.cleanup")


class CicloVidaEspacios:
    def __init__(self, db, storage, efectos, gestor):
        self.db, self.storage, self.efectos, self.gestor = db, storage, efectos, gestor

    def _borrar(self, clave):
        try:
            self.storage.delete(clave)
        except StorageNotFound:
            pass
        try:
            self.storage.get(clave)
        except StorageNotFound:
            return
        raise StorageUnavailable("La purga no quedó confirmada")

    def reconciliar(self):
        with mantenimiento_storage(), self.efectos._lock:
            self.db.expirar_workspaces()
            self.db.purgar_aprobados_expirados()
            for fila in self.db.limpiezas_espacios():
                ws, estado = fila["workspace_id"], fila["estado"]
                try:
                    if estado == "pendiente":
                        docs, trabajos, escrituras = self.db.recursos_espacio(ws)
                        for job in trabajos:
                            self.gestor.cancelar(job["job_id"])
                        if escrituras or self.db.recursos_espacio(ws)[1]:
                            raise StorageUnavailable("Hay productores pendientes de detenerse")
                        # Confirmar lápida remota antes de retirar sus artefactos.
                        sincronizar_manifiesto(self.db, self.storage, ws)
                        if docs and self.efectos.indice is None:
                            raise StorageUnavailable("El índice requiere configuración para confirmar su retirada")
                        for doc in docs:
                            if self.efectos.indice is not None:
                                self.efectos.indice._retirar_verificado(ws, doc, definitivo=True)
                        for raiz in ("source_documents", "outputs", "exports", "progress"):
                            prefijo = f"{raiz}/{ws}/"
                            claves = [o.object_name for o in _objetos(self.storage, prefijo)]
                            for clave in claves:
                                self._borrar(clave)
                            if self.storage.list(prefijo, limit=1).items:
                                raise StorageUnavailable("Quedan objetos del espacio")
                        self.db.purgar_datos_espacio(ws)
                        estado = "datos_purgados"
                    # Conservar evidencia de revocación durante el plazo de lápidas.
                    if datetime.fromisoformat(fila["borrado_en"]) + timedelta(
                        days=self.db.dias_retencion
                    ) <= datetime.now(timezone.utc):
                        self._borrar(clave_workspace(ws))
                        estado = "completada"
                except Exception as error:
                    self.db.resultado_limpieza_espacio(ws, estado, type(error).__name__)
                    logger.warning("Limpieza pendiente workspace_id=%s tipo=%s", ws, type(error).__name__)
                else:
                    self.db.resultado_limpieza_espacio(ws, estado)
