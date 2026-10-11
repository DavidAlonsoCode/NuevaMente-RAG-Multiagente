"""Recursos documentales compartidos por los routers y el único worker."""

from dataclasses import dataclass

from app.config import Configuracion
from app.core.agents.critic_contracts import VerificadorSync
from app.core.agents.gemini_generation import ClienteGeminiGeneracion
from app.core.rag.embeddings import EmbeddingProvider, crear_cliente_embeddings
from app.core.rag.multimodal import InterpretadorVisual
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
    vision: InterpretadorVisual | None = None


def crear_recursos_documentales(
    configuracion: Configuracion,
    db: RegistroOperativo,
    storage: StorageProvider,
    gestor: GestorTrabajos,
    *,
    proveedor: EmbeddingProvider | None = None,
    proveedor_vision: VerificadorSync | None = None,
) -> RecursosDocumentales:
    if gestor.store is not db:
        raise ValueError("Los servicios documentales deben compartir SQLite con el gestor")
    if gestor.cuotas.disponibles_hoy(configuracion.gemini_embedding_model) is None:
        raise ValueError("La integración documental requiere cuotas explícitas para el modelo de embeddings")
    embeddings = crear_cliente_embeddings(configuracion=configuracion, proveedor=proveedor)
    indice = VectorStoreChroma(db.ruta_db.parent / "chroma", embeddings)
    vision = None
    try:
        cuotas_vision = gestor.cuotas.disponibles_hoy(configuracion.gemini_generation_model)
        if proveedor_vision is not None and cuotas_vision is None:
            raise ValueError("La visión requiere cuotas centrales explícitas para su modelo")
        if proveedor_vision is None and cuotas_vision is not None and not configuracion.mock_gemini:
            proveedor_vision = ClienteGeminiGeneracion(configuracion)
        if proveedor_vision is not None:
            vision = InterpretadorVisual(
                proveedor_vision,
                modelo=configuracion.gemini_generation_model,
                mock=configuracion.mock_gemini,
                max_output_tokens=configuracion.generation_max_output_tokens,
            )
        return RecursosDocumentales(
            indice=indice,
            admision=crear_servicio_ingestion(db, storage, gestor, indice, vision=vision),
            consulta=ServicioConsultaDocumental(
                db, storage, indice, plazo_lectura=configuracion.generation_deadline_seconds
            ),
            vision=vision,
        )
    except Exception as error:
        cerrar_vision = getattr(proveedor_vision, "cerrar", None)
        if callable(cerrar_vision):
            try:
                cerrar_vision()
            except Exception as secundario:
                error.add_note(f"Cierre del proveedor visual no confirmado; tipo={type(secundario).__name__}.")
        try:
            indice.cerrar()
        except Exception as secundario:
            error.add_note(f"Cierre del índice no confirmado; tipo={type(secundario).__name__}.")
        raise
