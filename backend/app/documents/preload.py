"""Precarga administrativa offline (#49), sin rutas públicas de escritura."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.core.rag.chunker import trocear
from app.core.rag.multimodal import InterpretadorVisual, chunks_visuales
from app.core.rag.parser import parsear_archivo
from app.core.rag.rebuild import ManifiestoDocumento
from app.core.rag.vectorstore import DEMO_WORKSPACE_ID, VectorStoreChroma, _huella
from app.jobs.manager import ContextoEjecucion, TrabajoCanceladoError
from app.schemas.documents import (
    CoberturaDocumento,
    MetadatosDocumento,
    PublicacionDemo,
    SeccionDocumental,
    huella_documental_demo,
)
from app.schemas.enums import DocumentStatus
from app.storage.object_keys import clave_demo
from app.storage.provider import StorageProvider, StorageUnavailable


@dataclass(frozen=True)
class FuenteDemo:
    document_id: str
    archivo: str
    titulo: str


FUENTES_DEMO = (
    FuenteDemo("demo-redes-vcn", "redes_vcn_oci.pdf", "Redes VCN en Oracle Cloud Infrastructure"),
    FuenteDemo("demo-pagos-fintech", "integracion_apis_pagos.md", "Integración de APIs de pagos"),
    FuenteDemo("demo-salud-gobernanza", "gobernanza_datos_salud.md", "Gobernanza de datos de salud"),
)


def leer_fuentes(directorio: Path) -> list[tuple[FuenteDemo, bytes]]:
    """Sólo los tres archivos explícitos; no recorrer carpetas ni seguir symlinks."""
    raiz = directorio.resolve(strict=True)
    fuentes = []
    for fuente in FUENTES_DEMO:
        ruta = raiz / fuente.archivo
        if ruta.is_symlink() or ruta.resolve(strict=True).parent != raiz or not ruta.is_file():
            raise ValueError("La fuente demo debe ser un archivo regular del directorio indicado")
        # Limitar también la lectura previa al parser.
        if ruta.stat().st_size > (20 * 1024 * 1024 if ruta.suffix == ".pdf" else 5 * 1024 * 1024):
            raise ValueError("Fuente demo fuera del límite de tamaño")
        fuentes.append((fuente, ruta.read_bytes()))
    return fuentes


class PrecargadorDemo:
    def __init__(self, storage: StorageProvider, indice: VectorStoreChroma, *, vision: InterpretadorVisual | None):
        self.storage, self.indice, self.vision = storage, indice, vision

    def _leer(self, clave):
        objetos = self.storage.list(clave, limit=1).items
        if not objetos or objetos[0].object_name != clave:
            return None, None
        objeto = objetos[0]
        return objeto.etag, self.storage.get(clave, if_match=objeto.etag)

    def _guardar(self, manifest, etag):
        clave = clave_demo(f"{manifest.document_id}/manifest.json")
        contenido = manifest.model_dump_json().encode()
        objeto = self.storage.upload(
            clave, contenido, if_match=etag, crear_solo=etag is None, content_type="application/json"
        )
        if objeto.object_name != clave or self.storage.get(clave, if_match=objeto.etag) != contenido:
            raise StorageUnavailable("La escritura del manifiesto demo no fue confirmada")
        return objeto.etag

    def preparar(self, contexto: ContextoEjecucion, fuente: FuenteDemo, contenido: bytes) -> dict:
        if contexto.workspace_id != DEMO_WORKSPACE_ID or fuente not in FUENTES_DEMO:
            raise PermissionError("La precarga exige el contexto reservado y el catálogo explícito")
        contexto.chequear()
        if self.indice._retirado(DEMO_WORKSPACE_ID, fuente.document_id):
            raise TrabajoCanceladoError()
        clave = clave_demo(f"{fuente.document_id}/manifest.json")
        etag, previo = self._leer(clave)
        anterior = ManifiestoDocumento.model_validate_json(previo) if previo is not None else None
        huella = hashlib.sha256(contenido).hexdigest()
        if anterior is not None and (
            anterior.borrado
            or (anterior.workspace_id, anterior.document_id, anterior.source_name, anterior.hash_sha256)
            != (DEMO_WORKSPACE_ID, fuente.document_id, fuente.archivo, huella)
        ):
            raise ValueError("No se sobrescribe una fuente distinta ni se revierte una lápida")
        contexto.registrar_paso("parser_demo", 1)
        parseo = parsear_archivo(contenido, fuente.archivo)
        contexto.chequear()
        meta = MetadatosDocumento(
            extension=parseo.extension,
            tamano_bytes=len(contenido),
            creado_en=anterior.metadatos.creado_en if anterior and anterior.metadatos else datetime.now(timezone.utc),
            cantidad_paginas=len({p.numero for p in parseo.paginas} | set(parseo.paginas_visuales)),
            cantidad_tokens=parseo.cantidad_tokens,
            huella_config_parser=parseo.huella_config_parser,
        )
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
        manifest = ManifiestoDocumento(
            workspace_id=DEMO_WORKSPACE_ID,
            document_id=fuente.document_id,
            source_name=fuente.archivo,
            titulo=fuente.titulo,
            hash_sha256=huella,
            metadatos=meta,
            cobertura=cobertura,
            interpretaciones_visuales=anterior.interpretaciones_visuales if anterior else [],
        )
        original = clave_demo(f"{fuente.document_id}/original")
        etag_original, guardado = self._leer(original)
        if guardado is not None and guardado != contenido:
            raise ValueError("El original demo existente no corresponde a la fuente")
        if guardado is None:
            objeto = self.storage.upload(
                original,
                contenido,
                crear_solo=True,
                content_type="application/pdf" if parseo.extension == "pdf" else "text/markdown",
            )
            etag_original = objeto.etag
        if self.storage.get(original, if_match=etag_original) != contenido:
            raise StorageUnavailable("El original demo no fue confirmado")
        etag = self._guardar(manifest, etag)

        def confirmar():
            contexto.chequear()
            if self.indice._retirado(DEMO_WORKSPACE_ID, fuente.document_id):
                raise TrabajoCanceladoError()
            self.storage.get(clave, if_match=etag)
            if hashlib.sha256(self.storage.get(original, if_match=etag_original)).hexdigest() != huella:
                raise StorageUnavailable("La fuente demo cambió durante su preparación")
            contexto.chequear()

        chunks = [
            c.como_chunk_interno()
            for c in trocear(
                parseo,
                DEMO_WORKSPACE_ID,
                fuente.document_id,
                source_name=fuente.archivo,
                language=manifest.language,
                config=manifest.chunker,
            )
        ]
        if parseo.requiere_vision and self.vision is not None:
            contexto.registrar_paso("vision_demo", 1)
            visuales = self.vision.interpretar(
                contenido, manifest, parseo.paginas_visuales, contexto=contexto, confirmar=confirmar
            )
            confirmar()
            omitidas = [i.pagina for i in visuales if i.estado == "ilegible" or not i.cobertura_completa]
            cobertura = cobertura.model_copy(
                update={
                    "paginas_procesadas": sorted(
                        (set(cobertura.paginas_procesadas) | set(parseo.paginas_visuales)) - set(omitidas)
                    ),
                    "paginas_omitidas": omitidas,
                    "paginas_visuales_pendientes": [],
                    "detalle": "Interpretaciones visuales separadas del texto original.",
                }
            )
            manifest = manifest.model_copy(update={"interpretaciones_visuales": visuales, "cobertura": cobertura})
            derivados = chunks_visuales(manifest, indice_inicial=len(chunks))
            chunks.extend(derivados)
            meta = meta.model_copy(
                update={"cantidad_tokens": meta.cantidad_tokens + sum(c.cantidad_tokens for c in derivados)}
            )
            manifest = manifest.model_copy(update={"metadatos": meta})
            # Persistir la interpretación antes de embeddings: repetir una escritura
            # fallida nunca requiere volver a describir la imagen.
            etag = self._guardar(manifest, etag)
            if omitidas or {i.pagina for i in visuales} != set(parseo.paginas_visuales):
                raise ValueError("La interpretación visual no acredita cobertura completa")
        if meta.cantidad_tokens > manifest.parser.max_tokens_extraidos:
            raise ValueError("La extracción demo excede el límite de tokens")
        confirmar()
        if cobertura.paginas_visuales_pendientes:
            return {
                "document_id": fuente.document_id,
                "estado": "processing",
                "cantidad_chunks": 0,
                "vision_pendiente": True,
            }
        if not chunks:
            raise ValueError("La fuente demo no produjo evidencia indexable")
        contexto.registrar_paso("indexar_demo", 1)
        indexado = self.indice._indexar(
            self.indice._demo, DEMO_WORKSPACE_ID, fuente.document_id, chunks, contexto, confirmar
        )
        confirmar()
        activo = self.indice._activo(self.indice._demo, DEMO_WORKSPACE_ID, fuente.document_id)
        if activo is None:
            raise StorageUnavailable("La versión demo no quedó activa")
        publicacion = PublicacionDemo(
            hash_sha256=huella,
            version_indice=activo["version"],
            cantidad_chunks=len(chunks),
            huella_chunks=_huella(chunks),
            huella_config_chunker=manifest.chunker.huella(manifest.parser),
            huella_documental=huella_documental_demo(meta, cobertura),
            modelo_embeddings=self.indice.embeddings.modelo,
            dimensiones_embeddings=self.indice.embeddings.dimensiones,
            preparacion_embeddings=self.indice.embeddings.preparation_version,
        )
        verificados = self.indice.listar_chunks_demo(
            fuente.document_id, contexto=contexto, source_hash=huella, publicacion=publicacion
        )
        if verificados != chunks:
            raise StorageUnavailable("El inventario demo no conserva los chunks originales")
        confirmar()
        listo = ManifiestoDocumento.model_validate(
            manifest.model_copy(
                update={
                    "estado": DocumentStatus.READY,
                    "publicacion_demo": publicacion,
                }
            ).model_dump()
        )
        etag = self._guardar(listo, etag)
        confirmar()
        self.indice.listar_chunks_demo(
            fuente.document_id, contexto=contexto, source_hash=huella, publicacion=publicacion
        )
        return {
            "document_id": fuente.document_id,
            "estado": "ready",
            "cantidad_chunks": indexado.cantidad_chunks,
            "reutilizado": indexado.reutilizado,
            "vision_pendiente": False,
        }
