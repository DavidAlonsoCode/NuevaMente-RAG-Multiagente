"""Efectos documentales durables sobre el seguimiento y sincronizador existentes.

No ejecuta ingestiones: un terminal local fallido/cancelado es autoritativo.
Cada escritura es condicional y cada ack refiere a una solicitud concreta.
"""

import hashlib
import json
import logging
import threading
import uuid
from contextlib import nullcontext
from datetime import datetime, timezone

from app.core.rag.rebuild import ManifiestoDocumento
from app.core.rag.vectorstore import IndiceInconsistenteError, VectorStoreChroma, _huella
from app.jobs.manager import ColaLlenaError, EspacioOcupadoError
from app.jobs.store import RegistroOperativo, retirar_referencias
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.internal import Chunk
from app.session.manifests import ManifiestoEspacio, sincronizar_manifiesto
from app.storage.object_keys import clave_documento, clave_original, clave_output, clave_progress, clave_workspace
from app.storage.oci_budget import mantenimiento_storage
from app.storage.provider import StorageNotFound, StorageProvider, StorageUnavailable

logger = logging.getLogger("nuevamente.documents.lifecycle")


class EfectosDocumentales:
    def __init__(self, db: RegistroOperativo, storage: StorageProvider, indice: VectorStoreChroma | None):
        self.db, self.storage, self.indice = db, storage, indice
        # Coordinación de intentos del observador y el sincronizador, nunca del
        # gestor ni SQLite durante I/O. No crea un hilo ni una cola.
        self._lock = threading.RLock()
        self.gestor = None
        self.procesador = None

    def retirar(self, token: str, document_id: str) -> bool | None:
        """None: inexistente/ajeno; False: retirada durable pendiente; True: purga verificada."""
        sesion = self.db.obtener_sesion(token)
        if sesion is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión no está disponible.")
        if not self.db.retirar_documento(token, document_id):
            if self.db.obtener_sesion(token) is None:
                raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión dejó de estar disponible.")
            return None
        try:
            self.reconciliar(document_id)
            return self.db.limpieza_documental_completada(sesion["workspace_id"], document_id)
        except Exception as error:
            # El commit de retirada ya ocurrió; jamás traducirlo a fracaso ni
            # perder la obligación si también falla registrar el intento.
            logger.warning("Retirada confirmada con limpieza pendiente; tipo=%s", type(error).__name__)
            return False

    def _sin_productores(self, efecto: dict) -> list[str]:
        generaciones, trabajos = self.db.recursos_documentales(efecto["workspace_id"], efecto["document_id"])
        activos = [j for j in trabajos if j["status"] in ("queued", "running")]
        for trabajo in activos:
            if self.gestor is not None:
                self.gestor.cancelar(trabajo["job_id"])
        if self.db.escritura_documental_en_curso(efecto["workspace_id"], efecto["document_id"]) or any(
            j["status"] in ("queued", "running")
            for j in self.db.recursos_documentales(efecto["workspace_id"], efecto["document_id"])[1]
        ):
            raise StorageUnavailable("Hay productores documentales que todavía deben detenerse")
        return generaciones

    def _limpiar_derivados(self, ws: str, doc: str, generaciones: list[str]) -> None:
        clave = clave_progress(ws)
        pagina = self.storage.list(clave, limit=1)
        objeto = next((x for x in pagina.items if x.object_name == clave), None)
        if objeto is not None:
            progreso = json.loads(self.storage.get(clave, if_match=objeto.etag))
            if not isinstance(progreso, dict) or type(progreso.get("version")) is not int:
                raise StorageUnavailable("Progreso sin versión verificable")
            nuevo = retirar_referencias(progreso, doc, set(generaciones)) or {"version": progreso["version"]}
            if nuevo != progreso:
                nuevo["version"] = progreso["version"] + 1
                contenido = json.dumps(nuevo, ensure_ascii=False, allow_nan=False).encode()
                guardado = self.storage.upload(clave, contenido, if_match=objeto.etag, content_type="application/json")
                if self.storage.get(clave, if_match=guardado.etag) != contenido:
                    raise StorageUnavailable("Progreso retirado sin confirmación")
        for generacion in generaciones:
            prefijo = f"exports/{ws}/{generacion}/"
            # La lista se materializa antes de borrar para no saltar elementos
            # al cambiar la paginación. Solo generaciones vinculadas por SQLite.
            from app.session.recovery import _objetos

            claves = [objeto.object_name for objeto in _objetos(self.storage, prefijo)]
            for clave in claves:
                self._borrar_objeto(clave)
            if self.storage.list(prefijo, limit=1).items:
                raise StorageUnavailable("Quedan exports documentales")
            # El paquete acredita la relación documento-generación al perder
            # SQLite. Conservarlo hasta retirar progreso y todos sus exports.
            self._borrar_objeto(clave_output(ws, generacion))
        with self.db.transaccion():
            self.db.purgar_datos_documentales(ws, doc)

    def _leer(self, workspace_id: str, document_id: str):
        clave = clave_documento(workspace_id, document_id)
        pagina = self.storage.list(clave, limit=1)
        objeto = next((o for o in pagina.items if o.object_name == clave), None)
        if objeto is None:
            raise StorageNotFound("Manifiesto documental ausente")
        manifest = ManifiestoDocumento.model_validate_json(self.storage.get(clave, if_match=objeto.etag))
        if (manifest.workspace_id, manifest.document_id) != (workspace_id, document_id):
            raise StorageUnavailable("Manifiesto ajeno al efecto pendiente")
        return objeto, manifest

    def _borrar_objeto(self, clave: str) -> None:
        try:
            self.storage.delete(clave)
        except StorageNotFound:
            pass
        try:
            self.storage.get(clave)
        except StorageNotFound:
            return
        raise StorageUnavailable("La retirada del objeto no está confirmada")

    def _aplicar(self, efecto: dict) -> None:
        ws, doc = efecto["workspace_id"], efecto["document_id"]
        generaciones = None
        if self.db.esta_borrado("document", doc):
            generaciones = self._sin_productores(efecto)
        if self.indice is None:
            raise StorageUnavailable("El índice requiere configuración para confirmar su retirada")
        # Retirar el derivado aun cuando OCI no responda. No se borra evidencia
        # del original y el ack seguirá pendiente si otra etapa falla.
        definitivo = self.db.esta_borrado("document", doc) or self.db.obtener_workspace(ws) is None
        if definitivo or not self._conservar_indice_recuperable(efecto):
            self.indice._retirar_verificado(ws, doc, definitivo=definitivo)
        else:
            intento = json.loads(efecto["recuperacion_evidencia"]).get("indice_intento")
            if intento and intento["job_id"] == efecto["job_id"] and intento.get("previo") is not None:
                self.indice._retirar_versiones_inactivas_verificado(ws, doc, intento["previo"])
        espacio = ManifiestoEspacio.model_validate_json(self.storage.get(clave_workspace(ws)))
        if espacio.workspace_id != ws:
            raise StorageUnavailable("Espacio ajeno al efecto pendiente")
        retirado_remoto = (
            espacio.borrado
            or espacio.borrado_en is not None
            or espacio.expira_en <= datetime.now(timezone.utc)
            or any(r.recurso_tipo == "document" and r.recurso_id == doc for r in espacio.recursos_borrados)
        )
        retirado = (
            self.db.esta_borrado("document", doc)
            or self.db.esta_borrado("workspace", ws)
            or self.db.obtener_workspace(ws) is None
            or retirado_remoto
        )
        try:
            objeto, manifest = self._leer(ws, doc)
        except StorageNotFound:
            if not retirado and efecto["admision_estado"] != "fallida":
                raise
            manifest = None
        retirado = retirado or (manifest is not None and manifest.borrado)
        if (
            manifest is not None
            and manifest.hash_sha256 != efecto["hash"]
            and not (retirado and efecto["hash"] is None)
        ):
            raise StorageUnavailable("Fuente ajena al efecto pendiente")

        purgar = retirado or efecto["admision_estado"] == "fallida"
        if retirado:
            # Materializar la lápida local y confirmarla en el manifiesto
            # privado ANTES de eliminar evidencia de storage.
            with self.db.transaccion():
                if not self.db.esta_borrado("document", doc):
                    self.db.agendar_borrado("document", doc, ws)
            # Una lápida remota ya confirmada no se sobrescribe con una
            # versión local atrasada. Sigue bloqueando reconstrucciones.
            self.db.incorporar_lapidas_remotas(espacio.model_dump(mode="json"))
            if not retirado_remoto:
                sincronizar_manifiesto(self.db, self.storage, ws)
                confirmado = ManifiestoEspacio.model_validate_json(self.storage.get(clave_workspace(ws)))
                if not any(
                    lapida.recurso_tipo == "document" and lapida.recurso_id == doc
                    for lapida in confirmado.recursos_borrados
                ):
                    raise StorageUnavailable("La lápida remota no está confirmada")
            if manifest is not None and not manifest.borrado:
                contenido = manifest.model_copy(update={"borrado": True}).model_dump_json().encode()
                guardado = self.storage.upload(
                    clave_documento(ws, doc), contenido, if_match=objeto.etag, content_type="application/json"
                )
                if self.storage.get(guardado.object_name, if_match=guardado.etag) != contenido:
                    raise StorageUnavailable("Lápida documental sin confirmación")
            if generaciones is None:
                generaciones = self._sin_productores(efecto)
        elif not purgar and efecto.get("recuperacion_evidencia") is not None:
            # Un intento de reconstrucción no es una admisión histórica fallida.
            # Se retira el índice parcial/incompatible, pero nunca su fuente ni
            # se convierte el manifiesto recuperable en un fallo del upload.
            documento = self.db.obtener_documento(doc)
            if (
                documento is None
                or not self.db.procedencia_documental_verificada(documento)
                or manifest is None
                or manifest.admision_confirmada is not True
                or manifest.admision_sqlite_confirmada is not True
            ):
                raise StorageUnavailable("La fuente recuperable no es verificable")
            if hashlib.sha256(self.storage.get(clave_original(ws, doc))).hexdigest() != efecto["hash"]:
                raise StorageUnavailable("El original recuperable cambió")
        elif not purgar:
            if manifest is None or efecto["admision_estado"] != "encolada":
                raise StorageUnavailable("No se puede verificar la admisión persistida")
            if manifest.admision_confirmada is not True or manifest.admision_sqlite_confirmada is not True:
                original = self.storage.get(clave_original(ws, doc))
                if hashlib.sha256(original).hexdigest() != efecto["hash"]:
                    raise StorageUnavailable("El original no acredita la admisión")
            # El commit SQLite encolada acredita la intención, no el éxito del
            # pipeline. Incluso un manifiesto ready perdido se degrada a failed.
            deseado = manifest.model_copy(
                update={
                    "admision_confirmada": True,
                    "admision_sqlite_confirmada": True,
                    "estado": DocumentStatus.FAILED,
                    "detalle_error": "La ingestión no se completó; consultar el trabajo vinculado.",
                }
            )
            if manifest != deseado:
                contenido = deseado.model_dump_json().encode()
                nuevo = self.storage.upload(
                    clave_documento(ws, doc), contenido, if_match=objeto.etag, content_type="application/json"
                )
                if (
                    nuevo.object_name != clave_documento(ws, doc)
                    or self.storage.get(nuevo.object_name, if_match=nuevo.etag) != contenido
                ):
                    raise StorageUnavailable("Diagnóstico documental no confirmado")

        # El catálogo podría estar inactivo aunque queden chunks físicos tras
        # una compensación fallida. Verificar también todas sus colecciones.
        if retirado:
            self.indice._retirar_verificado(ws, doc, definitivo=True)
        if purgar:
            if retirado:
                self._limpiar_derivados(ws, doc, generaciones)
            self._borrar_objeto(clave_original(ws, doc))
            self._borrar_objeto(clave_documento(ws, doc))
        else:
            # Una lápida concurrente no queda cubierta por este ack: reagenda
            # una solicitud distinta y conserva la protección de acceso local.
            if self.db.esta_borrado("document", doc) or self.db.obtener_workspace(ws) is None:
                self.db.agendar_efecto_documental(ws, doc)
                raise StorageUnavailable("Retirada concurrente; efectos pendientes")

    def _conservar_indice_recuperable(self, efecto: dict) -> bool:
        if efecto.get("recuperacion_evidencia") is None:
            return False
        intento = json.loads(efecto["recuperacion_evidencia"]).get("indice_intento")
        if intento is None or intento["job_id"] != efecto["job_id"]:
            # El intento no llegó a mutar Chroma; no retirar índices de otro intento.
            return True
        previo = intento.get("previo")
        activo = self.indice._activo(self.indice._privada, efecto["workspace_id"], efecto["document_id"])
        if previo is None or activo is None or dict(activo) != previo:
            return False
        try:
            datos = self.indice._datos(self.indice._privada, activo)
            chunks = [
                Chunk.model_validate_json(m["chunk"]) for m in sorted(datos["metadatas"], key=lambda m: m["orden"])
            ]
            return _huella(chunks) == previo["firma"] and all(
                (c.workspace_id, c.document_id, c.document_hash)
                == (efecto["workspace_id"], efecto["document_id"], efecto["hash"])
                for c in chunks
            )
        except (IndiceInconsistenteError, ValueError, KeyError, TypeError):
            return False

    def reconciliar(self, document_id: str | None = None) -> int:
        confirmados, cursor = 0, ""
        with self._lock:
            self.db.recuperar_efectos_documentales()
            while True:
                lote = self.db.listar_efectos_documentales(cursor=cursor)
                if not lote:
                    break
                for efecto in lote:
                    cursor = efecto["document_id"]
                    if document_id is not None and cursor != document_id:
                        continue
                    try:
                        retirado = (
                            self.db.esta_borrado("document", efecto["document_id"])
                            or self.db.obtener_workspace(efecto["workspace_id"]) is None
                        )
                        with mantenimiento_storage() if retirado else nullcontext():
                            self._aplicar(efecto)
                    except Exception as error:
                        # Sin mensajes del proveedor ni documentos en logs/SQL.
                        codigo = type(error).__name__
                        logger.warning("Efecto documental pendiente; tipo=%s", codigo)
                        try:
                            self.db.registrar_resultado_efecto_documental(efecto, error_code=codigo)
                        except Exception as secundario:
                            error.add_note(f"Registro del reintento no confirmado; tipo={type(secundario).__name__}.")
                            raise StorageUnavailable("El efecto conserva seguimiento sin ack") from error
                    else:
                        confirmados += self.db.registrar_resultado_efecto_documental(efecto)
            if document_id is None:
                self._despachar_recuperaciones()
            return confirmados

    def _despachar_recuperaciones(self) -> None:
        """Solo despacha en el gestor común; el sincronizador no procesa fuentes."""
        if self.gestor is None or self.procesador is None:
            return
        for doc in self.db.reconstrucciones_documentales_pendientes():
            reserva = self.db.reservar_reconstruccion_documental(
                doc["document_id"], f"job_recovery_{uuid.uuid4().hex[:16]}"
            )
            if reserva is None:
                continue
            ws, document_id = reserva["workspace_id"], reserva["document_id"]
            try:
                self.gestor.enqueue(
                    ws,
                    "ingestion",
                    lambda ctx, document_id=document_id: self.procesador.recuperar(ctx, document_id),
                    job_id=reserva["recuperacion_job_id"],
                    document_id=document_id,
                    al_terminar=lambda estado, ws=ws, document_id=document_id: self.procesador.al_terminar(
                        ws, document_id, estado
                    ),
                )
            except (ColaLlenaError, EspacioOcupadoError):
                # La misma reserva se conserva; no consume un intento ni pierde
                # documentos cuando la cola/ranura de su espacio están ocupadas.
                continue
