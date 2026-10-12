"""Admisión de #19: identidad por intención, persistencia confirmada y cola existente.

No implementa extracción/indexación ni respuestas HTTP. El procesador se inyecta
explícitamente; jamás se encola un ejecutable vacío como sustituto del pipeline.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO

from app.core.rag.parser import LimitesIngesta, ParserError, ParserErrorCode, validar_entrada_archivo
from app.core.rag.rebuild import ManifiestoDocumento
from app.jobs.manager import ColaLlenaError, ContextoEjecucion, EspacioOcupadoError, GestorTrabajos
from app.jobs.store import ConflictoIdempotenciaDocumento, EspacioDocumentalOcupado, RegistroOperativo
from app.schemas.documents import AdmisionDocumento, EntradaArchivoDocumento, MetadatosDocumento
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.requests import UploadRequest
from app.storage.object_keys import clave_documento, clave_original
from app.storage.provider import StorageConflict, StorageError, StorageProvider, StorageUnavailable

logger = logging.getLogger("nuevamente.documents.admission")


class AdmisionNoConfirmadaError(TimeoutError):
    """Espera interna agotada: reserva existente, sin recibo de aceptación.

    No fija una respuesta HTTP nueva. La integración HTTP deberá decidir su
    traducción mediante revisión de contrato, nunca convertirla en un 202.
    """

    def __init__(self, document_id: str, job_id: str):
        super().__init__("La admisión todavía no tiene una confirmación verificable.")
        self.document_id, self.job_id = document_id, job_id


class ServicioAdmisionDocumental:
    def __init__(
        self,
        db: RegistroOperativo,
        storage: StorageProvider,
        gestor: GestorTrabajos,
        procesar: Callable[[ContextoEjecucion, ManifiestoDocumento], object],
        *,
        limites: LimitesIngesta | None = None,
        espera_admision_segundos: float = 10,
        al_terminar: Callable[[str, str, JobStatus], None] | None = None,
    ):
        if gestor.store is not db:
            raise ValueError("Admisión y gestor deben compartir el registro")
        if (
            not callable(procesar)
            or inspect.iscoroutinefunction(procesar)
            or inspect.iscoroutinefunction(procesar.__call__)
        ):
            raise TypeError("La admisión exige un procesador síncrono explícito")
        if not math.isfinite(espera_admision_segundos) or espera_admision_segundos <= 0:
            raise ValueError("La espera de admisión debe ser positiva y finita")
        self.db, self.storage, self.gestor, self.procesar = db, storage, gestor, procesar
        self.limites = limites or LimitesIngesta()
        self.espera_admision_segundos = espera_admision_segundos
        self.al_terminar = al_terminar

    def admitir_texto(self, token: str, entrada: UploadRequest, *, clave: str | None = None) -> AdmisionDocumento:
        return self.admitir_archivo(
            token,
            entrada.documento_contenido.encode("utf-8"),
            EntradaArchivoDocumento(source_name="documento.txt", titulo=entrada.documento_titulo),
            clave=clave,
        )

    def admitir_archivo(
        self,
        token: str,
        archivo: bytes | BinaryIO,
        entrada: EntradaArchivoDocumento,
        *,
        clave: str | None = None,
    ) -> AdmisionDocumento:
        sesion = self.db.obtener_sesion(token)
        if sesion is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión no está disponible.")
        if clave is not None and (not isinstance(clave, str) or not clave.strip()):
            raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Idempotency-Key debe tener un valor.")
        try:
            contenido = self._leer_acotado(archivo, entrada.source_name)
            extension = validar_entrada_archivo(contenido, entrada.source_name, self.limites)
            if not contenido or (extension != "pdf" and not contenido.decode("utf-8").strip()):
                raise ParserError(ParserErrorCode.EXTRACCION_INSUFICIENTE, "El documento no tiene contenido.")
        except ParserError as error:
            codigo = (
                ErrorCode.DOCUMENT_TOO_LARGE
                if error.codigo == ParserErrorCode.DEMASIADO_GRANDE
                else ErrorCode.VALIDATION_ERROR
            )
            raise ErrorAplicacion(
                codigo, error.mensaje, {**error.detalles, "parser_code": error.codigo.value}
            ) from error

        titulo = entrada.titulo or entrada.source_name
        huella = hashlib.sha256(contenido).hexdigest()
        cuerpo = json.dumps(
            {"source_name": entrada.source_name, "titulo": titulo, "hash_sha256": huella},
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        document_id, job_id = f"doc_{uuid.uuid4().hex}", f"job_{uuid.uuid4().hex}"
        metadatos = MetadatosDocumento(
            extension=extension, tamano_bytes=len(contenido), creado_en=datetime.now(timezone.utc)
        )
        try:
            registro, creada = self.db.reservar_admision_documental(
                token=token,
                document_id=document_id,
                job_id=job_id,
                titulo=titulo,
                source_name=entrada.source_name,
                hash_documento=huella,
                metadatos=metadatos,
                cuerpo=cuerpo,
                clave=clave,
            )
        except ConflictoIdempotenciaDocumento:
            raise ErrorAplicacion(ErrorCode.IDEMPOTENCY_CONFLICT, "La clave ya identifica otro documento.") from None
        except EspacioDocumentalOcupado:
            # Compatibilidad interna; HTTP distingue este límite operativo.
            raise ErrorAplicacion(
                ErrorCode.INVALID_STATE, "El espacio tiene un trabajo activo.", {"motivo": "workspace_busy"}
            ) from None
        except ValueError:
            if self.db.obtener_sesion(token) is None:
                raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión no está disponible.") from None
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El espacio o la admisión no están disponibles.") from None
        if not creada:
            return self._esperar_confirmacion(token, registro)

        workspace_id = registro["workspace_id"]
        manifest = ManifiestoDocumento(
            workspace_id=workspace_id,
            document_id=document_id,
            source_name=entrada.source_name,
            titulo=titulo,
            hash_sha256=huella,
            metadatos=metadatos,
            parser=self.limites,
            admision_confirmada=False,
            admision_sqlite_confirmada=False,
        )
        terminada = threading.Event()
        admitida = False
        sqlite_confirmada = False
        sqlite_indeterminada = False

        def ejecutar(contexto: ContextoEjecucion):
            while not terminada.wait(0.05):
                contexto.chequear()
            contexto.chequear()
            if not admitida:
                raise StorageUnavailable("La admisión no se confirmó")
            return self.procesar(contexto, manifest)

        try:
            self._persistir_confirmado(clave_original(workspace_id, document_id), contenido, crear_solo=True)
            self._sesion_vigente(token)
            objeto = self._persistir_confirmado(
                clave_documento(workspace_id, document_id),
                manifest.model_dump_json().encode("utf-8"),
                crear_solo=True,
                content_type="application/json",
            )
            self._sesion_vigente(token)
            opciones = {}
            if self.al_terminar is not None:
                opciones["al_terminar"] = lambda estado: self.al_terminar(workspace_id, document_id, estado)
            self.gestor.enqueue(workspace_id, "ingestion", ejecutar, job_id=job_id, document_id=document_id, **opciones)
            manifest = manifest.model_copy(update={"admision_confirmada": True})
            confirmado = self._persistir_confirmado(
                clave_documento(workspace_id, document_id),
                manifest.model_dump_json().encode("utf-8"),
                if_match=objeto.etag,
                content_type="application/json",
            )
            self.db.confirmar_admision_documental(token, document_id)
            sqlite_confirmada = True
            # El flag OCI previo no acredita el commit SQLite. Esta escritura
            # adicional lo acredita para recuperación incluso sin la DB local.
            manifest = manifest.model_copy(update={"admision_sqlite_confirmada": True})
            self._persistir_confirmado(
                clave_documento(workspace_id, document_id),
                manifest.model_dump_json().encode("utf-8"),
                if_match=confirmado.etag,
                content_type="application/json",
            )
            self._sesion_vigente(token)
            if self.db.obtener_documento(document_id) is None:
                raise ValueError("La admisión fue retirada durante la confirmación")
            admitida = True
        except Exception as error:
            publico = self._error_publico(error)
            if not sqlite_confirmada:
                try:
                    actual = self.db.obtener_admision_documental(workspace_id, document_id)
                    sqlite_confirmada = actual is not None and actual["estado"] == "encolada"
                except Exception as secundario:
                    # Compensar un commit desconocido podría sobrescribir éxito.
                    sqlite_indeterminada = True
                    self._registrar_secundario(error, "resultado_sqlite_no_verificable", secundario)
            if not sqlite_confirmada and not sqlite_indeterminada:
                try:
                    self.db.fallar_admision_documental(
                        document_id,
                        self._codigo_error(error).value,
                        "No se pudo confirmar la admisión documental.",
                        respuesta_error=publico.error.model_dump(mode="json") if publico else None,
                    )
                except Exception as secundario:
                    self._registrar_secundario(error, "limpieza_durable_no_confirmada", secundario)
            else:
                # La escritura final pudo aplicarse con respuesta perdida. No
                # convertir esa incertidumbre en una admisión fallida: la DB
                # conserva encolada y los lectores exigen evidencia en storage.
                logger.warning("Confirmación documental indeterminada; no se acredita limpieza pendiente")
                error.add_note("No se pudo verificar la confirmación completa; se conserva el registro de intención.")
                if publico is None:
                    publico = ErrorAplicacion(
                        ErrorCode.STORAGE_UNAVAILABLE, "La confirmación documental es indeterminada."
                    )
            try:
                self.gestor.cancelar(job_id)
            except Exception as secundario:
                self._registrar_secundario(error, "cancelacion_no_confirmada", secundario)
            if publico is not None and publico is not error:
                raise publico from error
            raise
        finally:
            try:
                self.db.finalizar_escritura_admision(document_id)
            except Exception as secundario:
                # La marca conservada impide confirmar una purga mientras se
                # desconoce si el productor sigue escribiendo. Reinicio la libera.
                logger.error("Fin de escritura de admisión no confirmado; tipo=%s", type(secundario).__name__)
            finally:
                terminada.set()
        return AdmisionDocumento(
            document_id=document_id, job_id=job_id, estado=DocumentStatus.PROCESSING, admision="encolada"
        )

    def _esperar_confirmacion(self, token: str, registro: dict) -> AdmisionDocumento:
        """Observa commits breves; nunca mantiene una transacción durante la espera.

        La coordinación usa el registro compartido, también entre instancias del
        servicio. El timeout no cancela al propietario ni vuelve a encolar.
        """
        limite = time.monotonic() + self.espera_admision_segundos
        while True:
            self._sesion_vigente(token)
            actual = self.db.obtener_admision_documental(registro["workspace_id"], registro["document_id"])
            if actual is None:
                raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La admisión dejó de estar disponible.")
            if actual["estado"] == "fallida":
                if "respuesta_error" in actual:
                    respuesta = actual["respuesta_error"]
                    raise ErrorAplicacion(ErrorCode(respuesta["code"]), respuesta["message"], respuesta.get("details"))
                try:
                    codigo = ErrorCode(actual["error_code"])
                except ValueError:
                    codigo = ErrorCode.INVALID_STATE
                raise ErrorAplicacion(
                    codigo,
                    "La admisión anterior falló; sus identificadores se conservan.",
                    {"document_id": actual["document_id"], "job_id": actual["job_id"]},
                )
            if actual["estado"] == "encolada":
                try:
                    manifest = ManifiestoDocumento.model_validate_json(
                        self.storage.get(clave_documento(actual["workspace_id"], actual["document_id"]))
                    )
                except StorageError as error:
                    raise ErrorAplicacion(
                        self._codigo_error(error), "No se pudo verificar la confirmación documental."
                    ) from error
                except ValueError as error:
                    raise ErrorAplicacion(
                        ErrorCode.INVALID_STATE, "La confirmación documental no es válida."
                    ) from error
                self._sesion_vigente(token)
                documento = self.db.obtener_documento(actual["document_id"])
                if (
                    documento is None
                    or manifest.workspace_id != actual["workspace_id"]
                    or manifest.document_id != actual["document_id"]
                    or manifest.hash_sha256 != documento["hash"]
                    or manifest.borrado
                ):
                    raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La confirmación documental no es válida.")
                if manifest.admision_confirmada is True and manifest.admision_sqlite_confirmada is True:
                    return self._recibo(actual, reutilizada=True)
            restante = limite - time.monotonic()
            if restante <= 0:
                raise AdmisionNoConfirmadaError(actual["document_id"], actual["job_id"])
            time.sleep(min(0.05, restante))

    @staticmethod
    def _error_publico(error: Exception) -> ErrorAplicacion | None:
        if isinstance(error, ErrorAplicacion):
            return error
        if isinstance(error, StorageConflict):
            return ErrorAplicacion(ErrorCode.INVALID_STATE, "El original o manifiesto cambió durante la admisión.")
        if isinstance(error, StorageError):
            return ErrorAplicacion(ErrorCode.STORAGE_UNAVAILABLE, "No se pudo confirmar el almacenamiento documental.")
        if isinstance(error, ColaLlenaError):
            return ErrorAplicacion(ErrorCode.QUEUE_FULL, "La cola de trabajos está llena.")
        if isinstance(error, EspacioOcupadoError):
            return ErrorAplicacion(
                ErrorCode.INVALID_STATE, "El espacio tiene un trabajo activo.", {"motivo": "workspace_busy"}
            )
        if isinstance(error, ValueError):
            return ErrorAplicacion(ErrorCode.INVALID_STATE, "El espacio o la admisión cambió durante la carga.")
        return None

    @staticmethod
    def _codigo_error(error: Exception) -> ErrorCode:
        if isinstance(error, ErrorAplicacion):
            return error.error.code
        if isinstance(error, StorageConflict):
            return ErrorCode.INVALID_STATE
        if isinstance(error, StorageError):
            return ErrorCode.STORAGE_UNAVAILABLE
        if isinstance(error, ColaLlenaError):
            return ErrorCode.QUEUE_FULL
        if isinstance(error, (EspacioOcupadoError, ValueError)):
            return ErrorCode.INVALID_STATE
        return ErrorCode.INTERNAL

    @staticmethod
    def _registrar_secundario(original: Exception, etapa: str, secundario: Exception) -> None:
        # Sin mensajes crudos, repr ni traceback: pueden contener secretos.
        tipo = type(secundario).__name__
        logger.error("Compensación documental no confirmada; etapa=%s tipo=%s", etapa, tipo)
        original.add_note(f"Compensación no confirmada: {etapa}; tipo={tipo}.")

    def _sesion_vigente(self, token: str) -> None:
        if self.db.obtener_sesion(token) is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión dejó de estar disponible durante la carga.")

    def _persistir_confirmado(self, clave: str, contenido: bytes, **condiciones):
        objeto = self.storage.upload(clave, contenido, **condiciones)
        if objeto.object_name != clave or self.storage.get(clave, if_match=objeto.etag) != contenido:
            raise StorageUnavailable("No se pudo verificar el objeto persistido")
        return objeto

    def _leer_acotado(self, archivo: bytes | BinaryIO, nombre: str) -> bytes:
        extension = Path(nombre.strip().lower()).suffix.lstrip(".")
        if extension not in ("pdf", "md", "markdown", "txt"):
            validar_entrada_archivo(b"", nombre, self.limites)
        maximo = self.limites.max_bytes_pdf if extension == "pdf" else self.limites.max_bytes_texto
        if isinstance(archivo, bytes):
            return archivo
        partes, total = [], 0
        while total <= maximo:
            parte = archivo.read(min(64 * 1024, maximo + 1 - total))
            if not isinstance(parte, bytes):
                raise TypeError("El archivo debe ser un flujo binario síncrono")
            if not parte:
                break
            partes.append(parte)
            total += len(parte)
        return b"".join(partes)

    @staticmethod
    def _recibo(registro: dict, *, reutilizada: bool = False) -> AdmisionDocumento:
        if registro["estado"] != "encolada":
            raise ValueError("Una reserva no es un recibo de aceptación")
        return AdmisionDocumento(
            document_id=registro["document_id"],
            job_id=registro["job_id"],
            # Reproduce la aceptación inicial, no el estado posterior del job.
            estado=DocumentStatus.PROCESSING,
            admision=registro["estado"],
            reutilizada=reutilizada,
        )
