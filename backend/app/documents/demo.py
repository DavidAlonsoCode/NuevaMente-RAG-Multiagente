"""Catálogo demo de lectura explícita: OCI autoriza, Chroma verifica publicación.

No admite, reconstruye ni genera embeddings. El inventario incluye documentos
sin índice y no crea filas privadas ni inventa trabajos de mantenimiento.
"""

import hashlib
import math
import time

from pydantic import ValidationError

from app.core.rag.parser import ParserError, validar_entrada_archivo
from app.core.rag.rebuild import ManifiestoDocumento
from app.core.rag.vectorstore import DEMO_WORKSPACE_ID, IndiceInconsistenteError, VectorStoreChroma
from app.documents.cursor import CursorDemo
from app.jobs.manager import ContextoEjecucion, DeadlineExcedidoError, TrabajoCanceladoError
from app.jobs.store import RegistroOperativo
from app.schemas.documents import huella_documental_demo
from app.schemas.documents_http import (
    CoberturaDocumentoResponse,
    DocumentoResponse,
    DocumentosResponse,
    ErrorDocumentoResponse,
    FuenteDocumentoResponse,
    MetadataDocumentoResponse,
)
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.storage.object_keys import clave_demo, clave_documento
from app.storage.provider import (
    StorageConflict,
    StorageInvalidName,
    StorageNotFound,
    StorageProvider,
    StorageUnavailable,
)


class ServicioCatalogoDemo:
    def __init__(
        self, db: RegistroOperativo, storage: StorageProvider, indice: VectorStoreChroma | None, *, plazo: float
    ):
        if not math.isfinite(plazo) or plazo <= 0:
            raise ValueError("El plazo de lectura demo debe ser positivo")
        self.db, self.storage, self.indice, self.plazo = db, storage, indice, plazo
        # La clave se abre durante lifespan; GET nunca crea archivos.
        self.cursor = CursorDemo(db.ruta_db.parent / "document-cursor.key")

    def sesion(self, token: str) -> dict:
        sesion = self.db.obtener_sesion(token)
        if sesion is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión no está disponible.")
        return sesion

    def _contexto(self, token: str) -> ContextoEjecucion:
        ws = self.sesion(token)["workspace_id"]
        return ContextoEjecucion(
            "lectura_demo",
            ws,
            "lectura_demo",
            time.monotonic() + self.plazo,
            espacio_vigente=lambda: (s := self.db.obtener_sesion(token)) is not None and s["workspace_id"] == ws,
        )

    def _chequear(self, ctx):
        try:
            ctx.chequear()
        except TrabajoCanceladoError:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "La sesión dejó de estar disponible.") from None
        except DeadlineExcedidoError:
            raise StorageUnavailable("La lectura demo excedió su presupuesto") from None

    @staticmethod
    def _no_encontrado():
        return ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento o fuente no encontrados.")

    @staticmethod
    def _clave(document_id):
        try:
            clave_documento(DEMO_WORKSPACE_ID, document_id)  # Validación de ID; jamás lee ese prefijo privado.
            return clave_demo(f"{document_id}/manifest.json")
        except StorageInvalidName:
            raise ServicioCatalogoDemo._no_encontrado() from None

    def _retirado(self, document_id):
        try:
            if self.indice is not None:
                return self.indice._retirado(DEMO_WORKSPACE_ID, document_id)
            return (DEMO_WORKSPACE_ID, document_id) in VectorStoreChroma.lapidas_persistidas(
                self.db.ruta_db.parent / "chroma"
            )
        except Exception as error:
            raise ErrorAplicacion(
                ErrorCode.PROVIDER_UNAVAILABLE, "El catálogo del índice no está disponible."
            ) from error

    def _leer(self, ctx, document_id, *, etag=None):
        self._chequear(ctx)
        clave = self._clave(document_id)
        if self._retirado(document_id):
            raise self._no_encontrado()
        try:
            if etag is None:
                objetos = self.storage.list(clave, limit=1).items
                if not objetos or objetos[0].object_name != clave:
                    raise self._no_encontrado()
                etag = objetos[0].etag
            manifest = ManifiestoDocumento.model_validate_json(self.storage.get(clave, if_match=etag))
        except StorageNotFound:
            raise self._no_encontrado() from None
        except StorageConflict:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La biblioteca cambió durante la lectura.") from None
        except (ValidationError, ValueError, TypeError) as error:
            raise StorageUnavailable("El manifiesto demo no es verificable") from error
        self._chequear(ctx)
        if (manifest.workspace_id, manifest.document_id) != (DEMO_WORKSPACE_ID, document_id):
            raise StorageUnavailable("La identidad demo no coincide con su clave canónica")
        if manifest.borrado or self._retirado(document_id):
            raise self._no_encontrado()
        return manifest, etag

    def _original(self, ctx, manifest):
        try:
            contenido = self.storage.get(clave_demo(f"{manifest.document_id}/original"))
            extension = validar_entrada_archivo(contenido, manifest.source_name, manifest.parser)
        except StorageNotFound:
            raise StorageUnavailable("La fuente demo no está disponible") from None
        except ParserError as error:
            raise StorageUnavailable("La fuente demo no es verificable") from error
        self._chequear(ctx)
        if hashlib.sha256(contenido).hexdigest() != manifest.hash_sha256:
            raise StorageUnavailable("La fuente demo no conserva su identidad")
        if manifest.metadatos is not None and (
            manifest.metadatos.tamano_bytes != len(contenido) or manifest.metadatos.extension != extension
        ):
            raise StorageUnavailable("La metadata demo no corresponde al original")

    @staticmethod
    def _evidencia(manifest):
        m, c, p = manifest.metadatos, manifest.cobertura, manifest.publicacion_demo
        if (
            manifest.estado != DocumentStatus.READY
            or m is None
            or c is None
            or p is None
            or not m.cantidad_tokens
            or not m.huella_config_parser
            or c.paginas_omitidas
            or c.paginas_visuales_pendientes
            or p.hash_sha256 != manifest.hash_sha256
            or p.huella_config_chunker != manifest.chunker.huella(manifest.parser)
            or p.huella_documental != huella_documental_demo(m, c)
        ):
            return False
        if m.extension == "pdf":
            return (
                m.cantidad_paginas is not None
                and m.cantidad_paginas > 0
                and (set(c.paginas_procesadas) == set(range(1, m.cantidad_paginas + 1)))
            )
        return bool(c.secciones) and len({s.seccion_id for s in c.secciones}) == len(c.secciones)

    def _chunks(self, ctx, manifest):
        if self.indice is None:
            raise ErrorAplicacion(ErrorCode.PROVIDER_UNAVAILABLE, "El índice demo no está configurado.")
        try:
            chunks = self.indice.listar_chunks_demo(
                manifest.document_id,
                contexto=ctx,
                source_hash=manifest.hash_sha256,
                publicacion=manifest.publicacion_demo,
            )
        except (IndiceInconsistenteError, ValidationError, ValueError, KeyError, TypeError):
            return []  # Ausente/incompatible/incompleto: no se publica ready.
        except (TrabajoCanceladoError, DeadlineExcedidoError):
            self._chequear(ctx)
            raise
        except Exception as error:
            raise ErrorAplicacion(ErrorCode.PROVIDER_UNAVAILABLE, "El índice demo no está disponible.") from error
        c = manifest.cobertura
        secciones = {s.seccion_id: s for s in c.secciones}
        for chunk in chunks:
            if chunk.source_name != manifest.source_name or chunk.source_type != manifest.metadatos.extension:
                return []
            if manifest.metadatos.extension == "pdf":
                if chunk.pagina not in c.paginas_procesadas or chunk.es_diagrama:
                    return []
            else:
                seccion = secciones.get(chunk.seccion_id)
                if (
                    seccion is None
                    or chunk.seccion != seccion.titulo
                    or chunk.linea_inicio is None
                    or chunk.linea_fin is None
                    or not seccion.linea_inicio <= chunk.linea_inicio <= chunk.linea_fin <= seccion.linea_fin
                ):
                    return []
        if manifest.metadatos.extension != "pdf" and {ch.seccion_id for ch in chunks} != set(secciones):
            return []
        if manifest.metadatos.extension == "pdf" and {ch.pagina for ch in chunks} != set(c.paginas_procesadas):
            return []
        return chunks

    @staticmethod
    def _respuesta(manifest, status):
        m, c = manifest.metadatos, manifest.cobertura
        return DocumentoResponse(
            document_id=manifest.document_id,
            titulo=manifest.titulo or manifest.source_name,
            status=status,
            metadata=MetadataDocumentoResponse.model_validate(m.model_dump(exclude={"huella_config_parser"}))
            if m
            else None,
            cobertura=CoberturaDocumentoResponse(
                paginas_procesadas=c.paginas_procesadas,
                paginas_omitidas=c.paginas_omitidas,
                paginas_visuales_pendientes=c.paginas_visuales_pendientes,
                secciones_procesadas=sorted(c.secciones, key=lambda s: (s.linea_inicio, s.seccion_id)),
                secciones_omitidas=None if c.paginas_omitidas or c.paginas_visuales_pendientes else [],
                secciones_visuales_pendientes=None if c.paginas_visuales_pendientes else [],
            )
            if c
            else None,
            error=ErrorDocumentoResponse(message="La preparación de la fuente demo no se completó.")
            if status == DocumentStatus.FAILED
            else None,
        )

    def _detalle(self, ctx, document_id):
        manifest, etag = self._leer(ctx, document_id)
        self._original(ctx, manifest)
        status = DocumentStatus.FAILED if manifest.estado == DocumentStatus.FAILED else DocumentStatus.PROCESSING
        chunks = self._chunks(ctx, manifest) if self._evidencia(manifest) else []
        if chunks:
            status = DocumentStatus.READY
        self._original(ctx, manifest)
        if chunks and self._chunks(ctx, manifest) != chunks:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El índice demo cambió durante la lectura.")
        self._leer(ctx, document_id, etag=etag)
        self._chequear(ctx)
        return self._respuesta(manifest, status), chunks

    def detalle(self, token, document_id):
        return self._detalle(self._contexto(token), document_id)[0]

    def pagina(self, token, *, limit=20, cursor=None):
        ctx = self._contexto(token)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("El límite demo debe estar entre 1 y 100")
        ancla = self.cursor.decodificar(cursor) if cursor is not None else ""
        ids, cursor_oci = set(), None
        while True:
            self._chequear(ctx)
            pagina = self.storage.list("demo/", limit=100, cursor=cursor_oci)
            for objeto in pagina.items:
                partes = objeto.object_name.split("/")
                if len(partes) == 3 and partes[0] == "demo" and partes[2] == "manifest.json":
                    if self._clave(partes[1]) != objeto.object_name:
                        raise StorageUnavailable("El inventario demo contiene una clave no canónica")
                    if partes[1] > ancla:
                        ids.add(partes[1])
            if pagina.next_cursor is None:
                break
            if pagina.next_cursor == cursor_oci:
                raise StorageUnavailable("El inventario demo repitió su cursor")
            cursor_oci = pagina.next_cursor
        pendientes = iter(sorted(ids))
        actuales = []
        while len(actuales) <= limit:
            documentos = []
            for document_id in pendientes:
                try:
                    documentos.append(self._detalle(ctx, document_id)[0])
                except ErrorAplicacion as error:
                    if error.error.code != ErrorCode.NOT_FOUND:
                        raise
                if len(actuales) + len(documentos) > limit:
                    break
            if not documentos:
                break
            # Revalidar también el lookahead y reemplazar los descartados
            # recorriendo los IDs restantes, sin reiniciar el inventario.
            for doc in documentos:
                try:
                    actuales.append(self._detalle(ctx, doc.document_id)[0])
                except ErrorAplicacion as error:
                    if error.error.code != ErrorCode.NOT_FOUND:
                        raise
        siguiente = self.cursor.codificar(actuales[limit - 1].document_id) if len(actuales) > limit else None
        self._chequear(ctx)
        return DocumentosResponse(items=actuales[:limit], next_cursor=siguiente)

    def fuente(self, token, document_id, chunk_id):
        doc, chunks = self._detalle(self._contexto(token), document_id)
        if doc.status != DocumentStatus.READY:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La fuente demo todavía no está disponible.")
        for chunk in chunks:
            if chunk.chunk_id == chunk_id:
                return FuenteDocumentoResponse(
                    document_id=chunk.document_id,
                    chunk_id=chunk.chunk_id,
                    texto=chunk.texto,
                    pagina=chunk.pagina,
                    seccion=chunk.seccion,
                    seccion_id=chunk.seccion_id,
                )
        raise self._no_encontrado()
