"""Propuesta HTTP #19 implementada bajo dirección del equipo; requiere contract-change."""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.documents import SeccionDocumental
from app.schemas.enums import DocumentStatus


class ErrorDocumentoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str | None = None
    message: str


class MetadataDocumentoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extension: str
    tamano_bytes: int = Field(gt=0)
    creado_en: AwareDatetime
    cantidad_paginas: int | None = Field(ge=0)
    cantidad_tokens: int | None = Field(ge=0)


class CoberturaDocumentoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paginas_procesadas: list[int]
    paginas_omitidas: list[int]
    paginas_visuales_pendientes: list[int]
    secciones_procesadas: list[SeccionDocumental]
    # El parser textual no conoce IDs/títulos de secciones en imágenes.
    secciones_omitidas: list[SeccionDocumental] | None
    secciones_visuales_pendientes: list[SeccionDocumental] | None


class DocumentoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    titulo: str
    status: DocumentStatus
    metadata: MetadataDocumentoResponse | None
    cobertura: CoberturaDocumentoResponse | None
    error: ErrorDocumentoResponse | None = None


class DocumentosResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[DocumentoResponse]
    next_cursor: str | None


class FuenteDocumentoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    chunk_id: str
    texto: str
    pagina: int | None
    seccion: str | None
    seccion_id: str | None
    source_type: str | None = None
    interpretacion_modelo: bool = False
    imagen_url: str | None = None


class UploadDocumentoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    status: DocumentStatus
    job_id: str
    status_url: str
    events_url: str
    cancel_url: str


class RetiradaDocumentoResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cleanup_pending: Literal[True] = True
