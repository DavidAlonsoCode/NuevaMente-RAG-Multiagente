"""Lecturas internas de #19; todavía no establecen respuestas HTTP públicas."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass

from pydantic import ValidationError

from app.core.rag.rebuild import ManifiestoDocumento, _workspace_vigente
from app.core.rag.vectorstore import VectorStoreChroma
from app.documents.cursor import CursorDocumental
from app.documents.visual import FuentesVisuales
from app.jobs.manager import ContextoEjecucion, TrabajoCanceladoError
from app.jobs.store import RegistroOperativo
from app.schemas.documents import CoberturaDocumento, MetadatosDocumento
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.internal import Chunk
from app.storage.object_keys import clave_documento
from app.storage.provider import StorageNotFound, StorageProvider, StorageUnavailable


@dataclass(frozen=True)
class PaginaDocumental:
    """Resultado interno, sin serialización ni esquema HTTP comprometidos."""

    documentos: list[dict]
    siguiente_id: str | None


class ServicioConsultaDocumental:
    def __init__(
        self, db: RegistroOperativo, storage: StorageProvider, indice: VectorStoreChroma | None, *, plazo_lectura: float
    ):
        if not math.isfinite(plazo_lectura) or plazo_lectura <= 0:
            raise ValueError("El presupuesto de lectura debe ser positivo")
        self.db, self.storage, self.indice = db, storage, indice
        self.plazo_lectura = plazo_lectura
        self.cursor = CursorDocumental(db.ruta_db.parent / "document-cursor.key")
        self.visuales = FuentesVisuales(db, storage, indice)

    def _sesion(self, token: str) -> dict:
        sesion = self.db.obtener_sesion(token)
        if sesion is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión no está disponible.")
        return sesion

    def _contexto(self, token: str, workspace_id: str) -> ContextoEjecucion:
        # Lectura local: no crea ni ejecuta otro trabajo, ni llama a Gemini.
        return ContextoEjecucion(
            "lectura_documental",
            workspace_id,
            "lectura_documental",
            time.monotonic() + self.plazo_lectura,
            espacio_vigente=lambda: (
                (sesion := self.db.obtener_sesion(token)) is not None and sesion["workspace_id"] == workspace_id
            ),
        )

    def _propio(self, token: str, document_id: str) -> dict:
        sesion = self._sesion(token)
        doc = self.db.obtener_documento(document_id)
        if (
            doc is None
            or doc["workspace_id"] != sesion["workspace_id"]
            or self.db.esta_borrado("workspace", sesion["workspace_id"])
        ):
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no encontrado.")
        return doc

    def _autorizar_storage(self, token: str, doc: dict) -> ManifiestoDocumento:
        contexto = self._contexto(token, doc["workspace_id"])
        try:
            _workspace_vigente(self.storage, contexto, doc["document_id"])
            manifest = ManifiestoDocumento.model_validate_json(
                self.storage.get(clave_documento(doc["workspace_id"], doc["document_id"]))
            )
        except (StorageNotFound, TrabajoCanceladoError):
            self._sesion(token)
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no encontrado.") from None
        except (ValidationError, ValueError, KeyError, TypeError, AttributeError) as error:
            raise StorageUnavailable("La autorización documental en storage no es verificable") from error
        try:
            contexto.chequear()
        except TrabajoCanceladoError:
            self._sesion(token)
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no encontrado.") from None
        if (manifest.workspace_id, manifest.document_id, manifest.hash_sha256) != (
            doc["workspace_id"],
            doc["document_id"],
            doc["hash"],
        ):
            raise StorageUnavailable("El manifiesto no corresponde al documento autorizado")
        if manifest.borrado:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no encontrado.")
        admision = self.db.obtener_admision_documental(doc["workspace_id"], doc["document_id"])
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
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no encontrado.")
        actual = self._propio(token, doc["document_id"])
        if actual["hash"] != doc["hash"]:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La fuente cambió durante la autorización.")
        return manifest

    def detalle(self, token: str, document_id: str) -> dict:
        doc = self._propio(token, document_id)
        self._autorizar_storage(token, doc)
        # Estado y metadata vienen siempre de SQLite. OCI no publica ready.
        return self._propio(token, document_id)

    def listar(self, token: str, *, limit: int = 20, cursor: str = "") -> PaginaDocumental:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("La página interna admite entre 1 y 100 documentos")
        sesion = self._sesion(token)
        filas = self.db.listar_documentos(sesion["workspace_id"], limit=limit + 1, cursor=cursor)
        documentos = []
        for doc in filas[:limit]:
            try:
                documentos.append(self.detalle(token, doc["document_id"]))
            except ErrorAplicacion as error:
                if error.error.code != ErrorCode.NOT_FOUND:
                    raise
                # Una reserva incompleta o lápida remota no se presenta como admisión.
        actuales = []
        for doc in documentos:
            try:
                actuales.append(self._propio(token, doc["document_id"]))
            except ErrorAplicacion as error:
                if error.error.code != ErrorCode.NOT_FOUND:
                    raise
        self._sesion(token)
        siguiente = filas[limit - 1]["document_id"] if len(filas) > limit else None
        return PaginaDocumental(actuales, siguiente)

    def pagina(self, token: str, *, limit: int = 20, cursor: str | None = None) -> PaginaDocumental:
        """Página pública con keyset firmado y filtrado antes de contar elementos.

        El lookahead no se consume: el cursor apunta al último elemento entregado.
        Cada lote limita filas SQLite; I/O y revalidación ocurren fuera del lock.
        """
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("La página admite entre 1 y 100 documentos")
        ws = self._sesion(token)["workspace_id"]
        ancla = self.cursor.decodificar(cursor, ws) if cursor is not None else ("", "")
        contexto = self._contexto(token, ws)
        documentos = []
        while len(documentos) <= limit:
            contexto.chequear()
            filas = self.db.pagina_documentos(ws, despues=ancla)
            if not filas:
                break
            for fila in filas:
                contexto.chequear()
                ancla = (fila["creado_en"], fila["document_id"])
                try:
                    documentos.append(self.detalle(token, fila["document_id"]))
                except ErrorAplicacion as error:
                    if error.error.code != ErrorCode.NOT_FOUND:
                        raise
                if len(documentos) > limit:
                    break
        # Revalidar también contra lápidas remotas antes de la entrega del lote.
        actuales = []
        for doc in documentos:
            contexto.chequear()
            try:
                actuales.append(self.detalle(token, doc["document_id"]))
            except ErrorAplicacion as error:
                if error.error.code != ErrorCode.NOT_FOUND:
                    raise
        self._sesion(token)
        contexto.chequear()
        siguiente = None
        if len(actuales) > limit:
            ultimo = actuales[limit - 1]
            siguiente = self.cursor.codificar(ws, ultimo["creado_en"], ultimo["document_id"])
        elif len(documentos) > limit:
            # Una retirada durante la revalidación puede acortar esta página;
            # avanzar solo hasta lo entregado conserva los elementos posteriores.
            ultimo = actuales[-1] if actuales else documentos[-1]
            siguiente = self.cursor.codificar(ws, ultimo["creado_en"], ultimo["document_id"])
        return PaginaDocumental(actuales[:limit], siguiente)

    def _listo(self, token: str, document_id: str) -> dict:
        doc = self._propio(token, document_id)
        if doc["estado"] != DocumentStatus.READY.value:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El documento todavía no está listo para consultar fuentes.")
        if doc.get("recuperacion_evidencia") is not None and doc["recuperacion_estado"] != "confirmada":
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La reconstrucción documental no está confirmada.")
        manifest = self._autorizar_storage(token, doc)
        doc = self._propio(token, document_id)
        if doc["estado"] != DocumentStatus.READY.value:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El documento ya no está disponible para consultar fuentes.")
        try:
            cobertura = (
                CoberturaDocumento.model_validate_json(doc["cobertura_json"])
                if doc["cobertura_json"] is not None
                else None
            )
        except ValidationError:
            raise StorageUnavailable("La cobertura documental no es verificable") from None
        if (
            cobertura is None
            or manifest.cobertura is None
            or manifest.metadatos is None
            or doc["metadatos_json"] is None
        ):
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La cobertura documental no está confirmada.")
        try:
            metadata = MetadatosDocumento.model_validate_json(doc["metadatos_json"])
        except ValidationError:
            raise StorageUnavailable("La metadata documental no es verificable") from None
        if (
            cobertura != manifest.cobertura
            or metadata != manifest.metadatos
            or not metadata.cantidad_tokens
            or not metadata.huella_config_parser
            or (
                metadata.extension == "pdf"
                and (
                    metadata.cantidad_paginas is None
                    or set(cobertura.paginas_procesadas) != set(range(1, metadata.cantidad_paginas + 1))
                )
            )
        ):
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La cobertura documental no está confirmada.")
        if manifest.estado != DocumentStatus.READY or any(
            c is not None and (c.paginas_visuales_pendientes or c.paginas_omitidas)
            for c in (cobertura, manifest.cobertura)
        ):
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La publicación documental no está confirmada.")
        return doc

    def chunks(self, token: str, document_id: str) -> list[Chunk]:
        doc = self._listo(token, document_id)
        if self.indice is None:
            raise ErrorAplicacion(ErrorCode.PROVIDER_UNAVAILABLE, "El índice documental no está configurado.")
        contexto = self._contexto(token, doc["workspace_id"])
        try:
            chunks = self.indice.listar_chunks(
                doc["workspace_id"],
                document_id,
                contexto=contexto,
                source_hash=doc["hash"],
            )
            contexto.chequear()
        except TrabajoCanceladoError:
            self._sesion(token)
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no encontrado.") from None
        # La revocación, lápida o cambio de estado durante el I/O bloquea la entrega.
        actual = self._listo(token, document_id)
        if actual["hash"] != doc["hash"]:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La fuente cambió durante la lectura.")
        if not chunks:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El índice documental no está disponible.")
        cobertura = (
            CoberturaDocumento.model_validate_json(actual["cobertura_json"]) if actual["cobertura_json"] else None
        )
        if cobertura is not None and any(
            c.pagina is not None and c.pagina not in cobertura.paginas_procesadas for c in chunks
        ):
            raise ErrorAplicacion(
                ErrorCode.INVALID_STATE, "La ubicación de la fuente no pertenece a la cobertura confirmada."
            )
        return chunks

    def fuente(self, token: str, document_id: str, chunk_id: str) -> Chunk:
        chunks = self.chunks(token, document_id)
        for chunk in chunks:
            if chunk.chunk_id == chunk_id:
                return chunk
        raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Fuente no encontrada.")

    def secciones(self, token: str, document_id: str) -> list[dict]:
        # Inventario autorizado: tampoco la cobertura por sí sola habilita contenido.
        chunks = self.chunks(token, document_id)
        resultado = {}
        for chunk in chunks:
            if chunk.seccion_id is not None:
                resultado.setdefault(chunk.seccion_id, {"seccion_id": chunk.seccion_id, "titulo": chunk.seccion})
        return list(resultado.values())

    def imagen(self, token: str, document_id: str, chunk_id: str):
        doc = self._listo(token, document_id)
        try:
            imagen = self.visuales.obtener(
                self._contexto(token, doc["workspace_id"]), document_id, doc["hash"], chunk_id
            )
        except TrabajoCanceladoError:
            self._sesion(token)
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Fuente visual no encontrada.") from None
        self._listo(token, document_id)
        return imagen

    def loader_critic(self, contexto: ContextoEjecucion):
        """#29 enlaza este callback a DependenciasCritic del mismo trabajo."""
        return self.visuales.loader_critic(contexto)

    def documento_grafo(self, contexto: ContextoEjecucion, document_id: str):
        """Vista interna ligada al trabajo: no retiene ni recupera tokens de sesión."""
        from app.core.agents.graph_state import DocumentoGeneracion
        from app.core.rag.chunker import detectar_idioma
        from app.core.rag.vectorstore import seccion_del_chunk
        from app.schemas.responses import DocumentoFuente

        contexto.chequear()
        doc = self.db.obtener_documento(document_id)
        if doc is None or doc["workspace_id"] != contexto.workspace_id:
            return None
        mf = self.visuales._autorizar(contexto, document_id, doc["hash"])
        chunks = self.indice.listar_chunks(
            contexto.workspace_id, document_id, contexto=contexto, source_hash=mf.hash_sha256
        )
        if not chunks:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La fuente no tiene un índice confirmado.")
        self.visuales._autorizar(contexto, document_id, mf.hash_sha256)
        return DocumentoGeneracion(
            workspace_id=contexto.workspace_id,
            fuente=DocumentoFuente(
                document_id=document_id,
                titulo=doc["titulo"],
                hash="sha256:" + mf.hash_sha256,
                version=f"v{mf.version}",
            ),
            estado=DocumentStatus.READY,
            idioma_origen=detectar_idioma("\n".join(c.texto for c in chunks)),
            secciones=tuple(dict.fromkeys(seccion_del_chunk(c) for c in chunks)),
            contiene_material_visual=any(c.es_diagrama for c in chunks),
        )
