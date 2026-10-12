"""Datos documentales internos de #19; no amplían las respuestas públicas v1."""

import hashlib
import json
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.enums import DocumentStatus


class ErrorProcesamientoDocumental(Exception):
    """Diagnóstico técnico seguro del pipeline, sin texto ni excepciones del proveedor."""

    def __init__(self, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo


class EntradaArchivoDocumento(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source_name: str = Field(min_length=1, max_length=255)
    titulo: str | None = Field(default=None, min_length=1, max_length=300)

    @field_validator("source_name", "titulo")
    @classmethod
    def etiqueta_valida(cls, valor: str | None) -> str | None:
        if valor is not None and not valor.strip():
            raise ValueError("La etiqueta debe tener texto")
        return valor


class MetadatosDocumento(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    extension: Literal["pdf", "md", "txt"]
    tamano_bytes: int = Field(gt=0)
    creado_en: AwareDatetime
    cantidad_paginas: int | None = Field(default=None, ge=0)
    cantidad_tokens: int | None = Field(default=None, ge=0)
    huella_config_parser: str | None = None


class SeccionDocumental(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    seccion_id: str = Field(min_length=1)
    titulo: str
    linea_inicio: int = Field(ge=1)
    linea_fin: int = Field(ge=1)

    @model_validator(mode="after")
    def rango_valido(self):
        if self.linea_fin < self.linea_inicio:
            raise ValueError("Rango de sección invertido")
        return self


class CoberturaDocumento(BaseModel):
    """None en el manifiesto significa extracción desconocida, no cobertura vacía."""

    model_config = ConfigDict(extra="forbid", strict=True)

    paginas_procesadas: list[int] = Field(default_factory=list)
    paginas_omitidas: list[int] = Field(default_factory=list)
    paginas_visuales_pendientes: list[int] = Field(default_factory=list)
    secciones: list[SeccionDocumental] = Field(default_factory=list)
    detalle: str = ""

    @model_validator(mode="after")
    def paginas_validas(self):
        for paginas in (self.paginas_procesadas, self.paginas_omitidas, self.paginas_visuales_pendientes):
            if any(type(p) is not int or p < 1 for p in paginas) or len(paginas) != len(set(paginas)):
                raise ValueError("Las páginas deben ser positivas y únicas")
        if set(self.paginas_procesadas) & set(self.paginas_omitidas):
            raise ValueError("Una página no puede estar procesada y omitida")
        return self


class PublicacionDemo(BaseModel):
    """Evidencia de mantenimiento para una versión demo completa, nunca pública.

    #49 la prepara tras verificar fuente e índice; #19 solamente la consume.
    Un estado ready o un catálogo Chroma aislado no sustituyen esta evidencia.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    version: Literal[1] = 1
    hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    version_indice: str = Field(pattern=r"^[0-9a-f]{32}$")
    cantidad_chunks: int = Field(gt=0)
    huella_chunks: str = Field(pattern=r"^[0-9a-f]{64}$")
    huella_config_chunker: str = Field(pattern=r"^[0-9a-f]{64}$")
    huella_documental: str = Field(pattern=r"^[0-9a-f]{64}$")
    modelo_embeddings: str = Field(min_length=1)
    dimensiones_embeddings: int = Field(gt=0)
    preparacion_embeddings: str = Field(min_length=1)


def huella_documental_demo(metadatos: MetadatosDocumento, cobertura: CoberturaDocumento) -> str:
    """Vincula metadata/cobertura exactas con la evidencia de mantenimiento."""
    datos = {"metadatos": metadatos.model_dump(mode="json"), "cobertura": cobertura.model_dump(mode="json")}
    return hashlib.sha256(json.dumps(datos, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class AdmisionDocumento(BaseModel):
    """Aceptación confirmada; los IDs reservados solos no constituyen un recibo."""

    model_config = ConfigDict(extra="forbid")

    document_id: str
    job_id: str
    estado: DocumentStatus
    admision: Literal["encolada"]
    reutilizada: bool = False


class ResultadoIngestionDocumental(BaseModel):
    """Evidencia interna del worker; ready se publica con el terminal SQLite.

    No reemplaza DocumentoParseado: admite un escaneo puro con cero tokens.
    """

    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    document_id: str
    hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    estado: DocumentStatus
    cantidad_chunks: int = Field(ge=0)
    reutilizado: bool
    metadatos: MetadatosDocumento
    cobertura: CoberturaDocumento

    @model_validator(mode="after")
    def resultado_publicable(self):
        if self.estado not in (DocumentStatus.READY, DocumentStatus.PROCESSING):
            raise ValueError("Una ingestión confirmada no puede estar fallida")
        if self.estado == DocumentStatus.READY and (
            not self.cantidad_chunks
            or self.cobertura.paginas_visuales_pendientes
            or self.cobertura.paginas_omitidas
            or not self.metadatos.cantidad_tokens
        ):
            raise ValueError("Ready exige texto indexado y cobertura completa")
        if self.metadatos.cantidad_tokens is None or not self.metadatos.huella_config_parser:
            raise ValueError("La extracción confirmada exige metadata y configuración trazables")
        if self.estado == DocumentStatus.PROCESSING and not self.cobertura.paginas_visuales_pendientes:
            raise ValueError("Processing completado exige visión pendiente")
        return self
