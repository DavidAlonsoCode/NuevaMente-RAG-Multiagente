"""Catálogo curado: referencias a objetos inmutables, sin credenciales de preparación."""

from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.schemas.chat import ChatAnswer
from app.schemas.documents_http import FuenteDocumentoResponse
from app.schemas.requests import GenerateRequest

Huella = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class ModeloDemo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EjemploDemo(ModeloDemo):
    escenario: str = Field(pattern=r"^[a-z0-9-]{1,40}$")
    generation_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    fuente: str
    document_hash: Huella
    parametros: GenerateRequest
    created_at: AwareDatetime
    segundos: float = Field(ge=0)
    status: Literal["completed", "rejected_quality"]
    paquete_sha256: Huella | None = None
    fuente_sha256: Huella | None = None
    diagnostico: str | None = None

    @model_validator(mode="after")
    def contenido_coherente(self):
        if self.status == "completed":
            if not self.paquete_sha256 or not self.fuente_sha256 or self.diagnostico:
                raise ValueError("Un ejemplo aprobado requiere paquete y fuentes confirmados")
        elif self.paquete_sha256 or self.fuente_sha256 or not self.diagnostico:
            raise ValueError("Un rechazo sólo publica su diagnóstico")
        return self


class FuenteDemoGuardada(ModeloDemo):
    document_id: str
    document_hash: Huella
    original_sha256: Huella
    chunks: list[FuenteDocumentoResponse] = Field(min_length=1, max_length=1000)
    imagenes: dict[str, Huella] = Field(default_factory=dict)

    @model_validator(mode="after")
    def fuentes_coherentes(self):
        ids = {c.chunk_id for c in self.chunks}
        if len(ids) != len(self.chunks) or any(c.document_id != self.document_id for c in self.chunks):
            raise ValueError("El snapshot contiene fuentes ambiguas o ajenas")
        if set(self.imagenes) - ids:
            raise ValueError("Una imagen no tiene fuente asociada")
        return self


class DiagramaDemo(ModeloDemo):
    respuesta: ChatAnswer
    fuente_sha256: Huella
    segundos: float = Field(ge=0)


class CatalogoEjemplos(ModeloDemo):
    schema_version: Literal["1.0"] = "1.0"
    etiqueta: Literal["Ejemplo generado previamente"] = "Ejemplo generado previamente"
    provider: Literal["oci", "mock"]
    publicado_en: AwareDatetime
    completo: bool
    items: list[EjemploDemo] = Field(max_length=12)
    diagrama: DiagramaDemo | None = None
    quiz_verificado: bool = False

    @model_validator(mode="after")
    def ids_unicos(self):
        if len({e.generation_id for e in self.items}) != len(self.items):
            raise ValueError("El catálogo repite generaciones")
        if len({e.escenario for e in self.items}) != len(self.items):
            raise ValueError("El catálogo repite escenarios")
        return self
