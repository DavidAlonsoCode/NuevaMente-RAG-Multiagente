"""Recursos documentales compartidos por los routers y el único worker."""

from dataclasses import dataclass

from app.config import Configuracion
from app.core.rag.embeddings import EmbeddingProvider, crear_cliente_embeddings
from app.core.rag.vectorstore import VectorStoreChroma
from app.documents.admission import ServicioAdmisionDocumental
from app.documents.ingestion import crear_servicio_ingestion
from app.documents.query import ServicioConsultaDocumental
from app.jobs.manager import GestorTrabajos
from app.jobs.store import RegistroOperativo
from app.storage.provider import StorageProvider


@dataclass(frozen=True)
class RecursosDocumentales:
    indice: VectorStoreChroma
    admision: ServicioAdmisionDocumental
    consulta: ServicioConsultaDocumental


def crear_recursos_documentales(
    configuracion: Configuracion,
    db: RegistroOperativo,
    storage: StorageProvider,
    gestor: GestorTrabajos,
    *,
    proveedor: EmbeddingProvider | None = None,
) -> RecursosDocumentales:
    if gestor.store is not db:
        raise ValueError("Los servicios documentales deben compartir SQLite con el gestor")
    if gestor.cuotas.disponibles_hoy(configuracion.gemini_embedding_model) is None:
        raise ValueError("La integración documental requiere cuotas explícitas para el modelo de embeddings")
    embeddings = crear_cliente_embeddings(configuracion=configuracion, proveedor=proveedor)
    indice = VectorStoreChroma(db.ruta_db.parent / "chroma", embeddings)
    try:
        return RecursosDocumentales(
            indice=indice,
            admision=crear_servicio_ingestion(db, storage, gestor, indice),
            consulta=ServicioConsultaDocumental(
                db, storage, indice, plazo_lectura=configuracion.generation_deadline_seconds
            ),
        )
    except Exception as error:
        try:
            indice.cerrar()
        except Exception as secundario:
            error.add_note(f"Cierre del índice no confirmado; tipo={type(secundario).__name__}.")
        raise
