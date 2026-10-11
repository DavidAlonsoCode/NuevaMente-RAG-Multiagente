"""Ingestión #19 sobre parser/chunker y el único worker común.

El índice se publica por versión; el documento sigue processing hasta que el
worker confirma completed y su estado documental en la misma transacción local.
OCI usa ETags y lecturas de comprobación, nunca una transacción distribuida.
"""

from __future__ import annotations

import hashlib
import json
import logging

from app.core.rag.chunker import trocear
from app.core.rag.parser import ParserError, parsear_archivo
from app.core.rag.rebuild import ManifiestoDocumento, _workspace_vigente
from app.core.rag.vectorstore import IndiceInconsistenteError, ResultadoIndexacion, VectorStoreChroma
from app.documents.admission import ServicioAdmisionDocumental
from app.documents.lifecycle import EfectosDocumentales
from app.jobs.manager import (
    ContextoEjecucion,
    CuotaAgotadaError,
    DeadlineExcedidoError,
    GestorTrabajos,
    ReintentableError,
    TrabajoCanceladoError,
)
from app.jobs.store import RegistroOperativo
from app.schemas.documents import (
    CoberturaDocumento,
    ErrorProcesamientoDocumental,
    ResultadoIngestionDocumental,
    SeccionDocumental,
)
from app.schemas.enums import DocumentStatus, JobStatus
from app.storage.object_keys import clave_documento, clave_original
from app.storage.provider import StorageError, StorageNotFound, StorageProvider, StorageUnavailable

logger = logging.getLogger("nuevamente.documents.ingestion")


class ProcesadorIngestionDocumental:
    def __init__(self, db: RegistroOperativo, storage: StorageProvider, indice: VectorStoreChroma):
        self.db, self.storage, self.indice = db, storage, indice
        self.efectos = EfectosDocumentales(db, storage, indice)
        self.efectos.procesador = self

    def recuperar(self, contexto: ContextoEjecucion, document_id: str) -> ResultadoIngestionDocumental:
        """Nuevo trabajo real sobre una procedencia restaurada, sin otra admisión."""
        doc = self.db.obtener_documento(document_id)
        if (
            doc is None
            or not self.db.procedencia_documental_verificada(doc)
            or doc["recuperacion_job_id"] != contexto.job_id
            or doc["recuperacion_estado"] != "en_curso"
        ):
            raise TrabajoCanceladoError()
        _, manifest = self._leer_manifest(clave_documento(contexto.workspace_id, document_id))
        evidencia = json.loads(doc["recuperacion_evidencia"])
        anterior = ManifiestoDocumento.model_validate_json(json.dumps(evidencia["manifest"]))
        if any(
            getattr(manifest, campo) != getattr(anterior, campo)
            for campo in ("workspace_id", "document_id", "hash_sha256", "source_name", "language", "parser", "chunker")
        ):
            raise StorageUnavailable("La fuente recuperable cambió de identidad o configuración")
        original = self.storage.get(clave_original(contexto.workspace_id, document_id))
        if (
            manifest.metadatos is None
            or manifest.metadatos.tamano_bytes != len(original)
            or manifest.metadatos.extension != anterior.metadatos.extension
            or manifest.metadatos.creado_en != anterior.metadatos.creado_en
        ):
            raise StorageUnavailable("La fuente restaurada no corresponde a su metadata")
        return self(contexto, manifest)

    def _leer_manifest(self, clave: str):
        # El proveedor vigente ofrece list/get condicional, no una API head.
        pagina = self.storage.list(clave, limit=1)
        if not pagina.items or pagina.items[0].object_name != clave:
            raise StorageNotFound("Manifiesto documental ausente")
        objeto = pagina.items[0]
        return objeto, ManifiestoDocumento.model_validate_json(self.storage.get(clave, if_match=objeto.etag))

    def _vigente(self, contexto: ContextoEjecucion, manifest: ManifiestoDocumento, etag: str):
        contexto.chequear()
        if contexto.workspace_id != manifest.workspace_id:
            raise PermissionError("Contexto de otro espacio")
        documento = self.db.obtener_documento(manifest.document_id)
        job = self.db.obtener_job(contexto.job_id)
        if (
            documento is None
            or documento["workspace_id"] != manifest.workspace_id
            or documento["hash"] != manifest.hash_sha256
            or documento["estado"] != DocumentStatus.PROCESSING.value
            or job is None
            or job["document_id"] != manifest.document_id
            or job["workspace_id"] != manifest.workspace_id
            or job["status"] != "running"
            or job["tipo"] != "ingestion"
        ):
            raise TrabajoCanceladoError()
        if documento.get("recuperacion_evidencia") is not None and (
            not self.db.procedencia_documental_verificada(documento)
            or documento["recuperacion_estado"] != "en_curso"
            or documento["recuperacion_job_id"] != contexto.job_id
            or manifest.admision_confirmada is not True
            or manifest.admision_sqlite_confirmada is not True
        ):
            raise TrabajoCanceladoError()
        _workspace_vigente(self.storage, contexto, manifest.document_id)
        actual = ManifiestoDocumento.model_validate_json(
            self.storage.get(clave_documento(manifest.workspace_id, manifest.document_id), if_match=etag)
        )
        if (
            actual.workspace_id != manifest.workspace_id
            or actual.document_id != manifest.document_id
            or actual.hash_sha256 != manifest.hash_sha256
            or actual.borrado
            or not actual.admision_recuperable
            or actual.estado == DocumentStatus.FAILED
        ):
            raise TrabajoCanceladoError()
        contexto.chequear()

    def _persistir(self, manifest: ManifiestoDocumento, etag: str) -> str:
        clave = clave_documento(manifest.workspace_id, manifest.document_id)
        contenido = manifest.model_dump_json().encode("utf-8")
        objeto = self.storage.upload(clave, contenido, if_match=etag, content_type="application/json")
        if objeto.object_name != clave or self.storage.get(clave, if_match=objeto.etag) != contenido:
            raise StorageUnavailable("No se pudo verificar el manifiesto documental")
        return objeto.etag

    def _fuente_vigente(self, contexto: ContextoEjecucion, manifest: ManifiestoDocumento, etag: str):
        self._vigente(contexto, manifest, etag)
        original = self.storage.get(clave_original(manifest.workspace_id, manifest.document_id))
        if hashlib.sha256(original).hexdigest() != manifest.hash_sha256:
            raise ValueError("La fuente trazable dejó de corresponder al índice")
        contexto.chequear()

    def __call__(self, contexto: ContextoEjecucion, admision: ManifiestoDocumento) -> ResultadoIngestionDocumental:
        etapa = "FUENTE"
        try:
            contexto.chequear()
            clave = clave_documento(admision.workspace_id, admision.document_id)
            objeto, manifest = self._leer_manifest(clave)
            if manifest != admision or manifest.metadatos is None:
                raise ValueError("La fuente no corresponde a la admisión confirmada")
            etag = objeto.etag
            self._vigente(contexto, manifest, etag)
            original = self.storage.get(clave_original(manifest.workspace_id, manifest.document_id))
            if hashlib.sha256(original).hexdigest() != manifest.hash_sha256:
                raise ValueError("El SHA-256 del original cambió")
            self._vigente(contexto, manifest, etag)
            etapa = "PARSER"
            parseo = parsear_archivo(original, manifest.source_name, manifest.parser)
            self._vigente(contexto, manifest, etag)
            if parseo.hash_sha256 != manifest.hash_sha256:
                raise ValueError("El parser no conservó el hash del original")
            etapa = "CHUNKER"
            chunks = [
                c.como_chunk_interno()
                for c in trocear(
                    parseo,
                    workspace_id=manifest.workspace_id,
                    document_id=manifest.document_id,
                    source_name=manifest.source_name,
                    language=manifest.language,
                    config=manifest.chunker,
                )
            ]
            cobertura = CoberturaDocumento(
                paginas_procesadas=[p.numero for p in parseo.paginas],
                paginas_visuales_pendientes=list(parseo.paginas_visuales),
                secciones=[
                    SeccionDocumental(
                        seccion_id=f"seccion:{s.linea_inicio}",
                        titulo=s.titulo,
                        linea_inicio=s.linea_inicio,
                        linea_fin=s.linea_fin,
                    )
                    for s in parseo.secciones
                ],
                detalle=parseo.cobertura,
            )
            cantidad_paginas = len(parseo.paginas) + len(parseo.paginas_visuales)
            if parseo.extension == "pdf":
                # Texto y visión pueden cubrir la misma página física.
                cantidad_paginas = len({p.numero for p in parseo.paginas} | set(parseo.paginas_visuales))
            metadatos = manifest.metadatos.model_copy(
                update={
                    "cantidad_paginas": cantidad_paginas,
                    "cantidad_tokens": parseo.cantidad_tokens,
                    "huella_config_parser": parseo.huella_config_parser,
                }
            )
            manifest = manifest.model_copy(update={"metadatos": metadatos, "cobertura": cobertura})
            self._vigente(contexto, manifest, etag)
            etapa = "METADATA"
            etag = self._persistir(manifest, etag)
            self._vigente(contexto, manifest, etag)
            self.db.guardar_metadata_documental(
                manifest.workspace_id, manifest.document_id, metadatos=metadatos, cobertura=cobertura
            )
            etapa = "INDICE"
            documento = self.db.obtener_documento(manifest.document_id)
            if documento is not None and documento.get("recuperacion_evidencia") is not None:
                previo = None
                activo = self.indice._activo(self.indice._privada, manifest.workspace_id, manifest.document_id)
                if activo is not None:
                    try:
                        existentes = self.indice.listar_chunks(
                            manifest.workspace_id,
                            manifest.document_id,
                            contexto=contexto,
                            source_hash=manifest.hash_sha256,
                        )
                    except IndiceInconsistenteError:
                        existentes = None
                    if chunks and existentes == chunks:
                        previo = dict(activo)
                self.db.registrar_indice_reconstruccion(manifest.document_id, contexto.job_id, previo)
            if chunks:
                indexado = self.indice.indexar(
                    manifest.workspace_id,
                    manifest.document_id,
                    chunks,
                    contexto=contexto,
                    confirmar=lambda: self._fuente_vigente(contexto, manifest, etag),
                )
                guardados = self.indice.listar_chunks(
                    manifest.workspace_id, manifest.document_id, contexto=contexto, source_hash=manifest.hash_sha256
                )
                if guardados != chunks or indexado.cantidad_chunks != len(chunks):
                    raise ValueError("El inventario del índice no coincide con los chunks")
            elif parseo.requiere_vision:
                self._vigente(contexto, manifest, etag)
                self.indice._borrar(self.indice._privada, manifest.workspace_id, manifest.document_id, definitivo=False)
                indexado = ResultadoIndexacion(manifest.document_id, 0, False)
            else:
                raise ValueError("La extracción no produjo chunks ni páginas visuales pendientes")
            self._fuente_vigente(contexto, manifest, etag)
            resultado = ResultadoIngestionDocumental(
                workspace_id=manifest.workspace_id,
                document_id=manifest.document_id,
                hash_sha256=manifest.hash_sha256,
                estado=parseo.estado_documento,
                cantidad_chunks=indexado.cantidad_chunks,
                reutilizado=indexado.reutilizado,
                metadatos=metadatos,
                cobertura=cobertura,
            )
            etapa = "PERSISTENCIA"
            manifest = manifest.model_copy(update={"estado": resultado.estado, "detalle_error": None})
            etag = self._persistir(manifest, etag)
            self._fuente_vigente(contexto, manifest, etag)
            return resultado
        except (TrabajoCanceladoError, DeadlineExcedidoError, CuotaAgotadaError, ReintentableError, StorageUnavailable):
            raise
        except StorageError as error:
            raise StorageUnavailable("No se pudo confirmar la fuente o su manifiesto") from error
        except ParserError as error:
            raise ErrorProcesamientoDocumental(f"PARSER_{error.codigo.value}", error.mensaje) from error
        except Exception as error:
            raise ErrorProcesamientoDocumental(
                f"INGESTION_{etapa}", f"No se pudo completar la etapa {etapa.lower()} del documento."
            ) from error

    def al_terminar(self, workspace_id: str, document_id: str, estado: JobStatus) -> None:
        """Intento inmediato del efecto reservado en el commit terminal SQLite."""
        if estado == JobStatus.COMPLETED:
            return
        if self.efectos.reconciliar(document_id) == 0:
            raise StorageUnavailable("La sincronización terminal documental quedó pendiente")


def crear_servicio_ingestion(
    db: RegistroOperativo,
    storage: StorageProvider,
    gestor: GestorTrabajos,
    indice: VectorStoreChroma,
    **opciones,
) -> ServicioAdmisionDocumental:
    """Composición explícita: ninguna instancia SDK, cola o thread adicional."""
    procesador = ProcesadorIngestionDocumental(db, storage, indice)
    return ServicioAdmisionDocumental(db, storage, gestor, procesador, al_terminar=procesador.al_terminar, **opciones)
