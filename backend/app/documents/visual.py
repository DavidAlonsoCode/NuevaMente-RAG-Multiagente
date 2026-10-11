"""Fuentes visuales privadas: original verificado y permisos revalidados tras I/O."""

import hashlib

from pydantic import ValidationError

from app.core.agents.critic_contracts import ImagenOriginal
from app.core.rag.raster import VERSION_RASTER, rasterizar_pagina
from app.core.rag.rebuild import ManifiestoDocumento, _workspace_vigente
from app.jobs.manager import ContextoEjecucion
from app.schemas.documents import CoberturaDocumento, MetadatosDocumento
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.storage.object_keys import clave_documento, clave_original
from app.storage.provider import StorageNotFound, StorageUnavailable


class FuentesVisuales:
    def __init__(self, db, storage, indice):
        self.db, self.storage, self.indice = db, storage, indice

    def _autorizar(self, contexto, document_id, source_hash):
        contexto.chequear()
        doc = self.db.obtener_documento(document_id)
        if doc is None or doc["workspace_id"] != contexto.workspace_id:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Fuente visual no encontrada.")
        _workspace_vigente(self.storage, contexto, document_id)
        manifest = ManifiestoDocumento.model_validate_json(
            self.storage.get(clave_documento(contexto.workspace_id, document_id))
        )
        if manifest.borrado:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Fuente visual no encontrada.")
        admision = self.db.obtener_admision_documental(contexto.workspace_id, document_id)
        if admision is not None:
            confirmada = (
                admision["estado"] == "encolada"
                and manifest.admision_confirmada is True
                and manifest.admision_sqlite_confirmada is True
            )
        elif doc.get("recuperacion_evidencia") is not None:
            confirmada = (
                self.db.procedencia_documental_verificada(doc)
                and manifest.admision_confirmada is True
                and manifest.admision_sqlite_confirmada is True
                and manifest.source_name == doc["source_name"]
            )
        else:
            confirmada = manifest.admision_confirmada is None and manifest.admision_sqlite_confirmada is None
        if not confirmada:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Fuente visual no encontrada.")
        try:
            cobertura = (
                CoberturaDocumento.model_validate_json(doc["cobertura_json"])
                if doc["cobertura_json"] is not None
                else None
            )
            metadata = (
                MetadatosDocumento.model_validate_json(doc["metadatos_json"])
                if doc["metadatos_json"] is not None
                else None
            )
        except ValidationError:
            raise StorageUnavailable("La cobertura visual no es verificable") from None
        if (
            (manifest.workspace_id, manifest.document_id, manifest.hash_sha256)
            != (contexto.workspace_id, document_id, source_hash.removeprefix("sha256:"))
            or doc["hash"] != manifest.hash_sha256
            or doc["estado"] != DocumentStatus.READY.value
            or manifest.estado != DocumentStatus.READY
            or manifest.cobertura is None
            or manifest.cobertura.paginas_omitidas
            or manifest.cobertura.paginas_visuales_pendientes
            or cobertura != manifest.cobertura
            or metadata is None
            or metadata != manifest.metadatos
            or not metadata.cantidad_tokens
            or not metadata.huella_config_parser
            or metadata.cantidad_paginas is None
            or set(cobertura.paginas_procesadas) != set(range(1, metadata.cantidad_paginas + 1))
            or (doc.get("recuperacion_evidencia") is not None and doc["recuperacion_estado"] != "confirmada")
        ):
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La fuente visual no está confirmada.")
        if self.indice is None:
            raise ErrorAplicacion(ErrorCode.PROVIDER_UNAVAILABLE, "El índice documental no está configurado.")
        return manifest

    def obtener(self, contexto: ContextoEjecucion, document_id: str, source_hash: str, chunk_id: str):
        manifest = self._autorizar(contexto, document_id, source_hash)
        chunks = self.indice.listar_chunks(
            contexto.workspace_id, document_id, contexto=contexto, source_hash=manifest.hash_sha256
        )
        chunk = next((c for c in chunks if c.chunk_id == chunk_id), None)
        if chunk is None or chunk.source_type != "image_description" or not chunk.es_diagrama:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Fuente visual no encontrada.")
        interpretacion = next((i for i in manifest.interpretaciones_visuales if i.pagina == chunk.pagina), None)
        if (
            interpretacion is None
            or interpretacion.version_raster != VERSION_RASTER
            or not interpretacion.cobertura_completa
            or interpretacion.estado == "ilegible"
        ):
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La procedencia visual requiere reconstrucción.")
        original = self.storage.get(clave_original(contexto.workspace_id, document_id))
        if hashlib.sha256(original).hexdigest() != manifest.hash_sha256:
            raise StorageUnavailable("La fuente original no corresponde a su hash")
        imagen = rasterizar_pagina(original, chunk.pagina, contexto=contexto)
        if hashlib.sha256(imagen.contenido).hexdigest() != interpretacion.imagen_sha256:
            raise StorageUnavailable("La página original no corresponde a la interpretación")
        if (
            hashlib.sha256(self.storage.get(clave_original(contexto.workspace_id, document_id))).hexdigest()
            != manifest.hash_sha256
        ):
            raise StorageUnavailable("La fuente original cambió durante la lectura")
        actual = self._autorizar(contexto, document_id, source_hash)
        if actual.interpretaciones_visuales != manifest.interpretaciones_visuales:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La interpretación cambió durante la lectura.")
        contexto.chequear()
        return ImagenOriginal(
            workspace_id=contexto.workspace_id,
            document_id=document_id,
            source_hash=source_hash,
            chunk_id=chunk_id,
            contenido=imagen.contenido,
            mime_type="image/png",
            tokens_estimados=imagen.tokens_estimados,
            interpretacion_ambigua=interpretacion.estado == "ambigua",
        )

    def loader_critic(self, contexto: ContextoEjecucion):
        def obtener(workspace_id, document_id, source_hash, chunk_id):
            if workspace_id != contexto.workspace_id:
                return None
            try:
                return self.obtener(contexto, document_id, source_hash, chunk_id)
            except StorageNotFound:
                return None
            except ErrorAplicacion as error:
                if error.error.code == ErrorCode.NOT_FOUND:
                    return None
                raise

        return obtener
