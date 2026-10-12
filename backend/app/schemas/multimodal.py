"""Interpretaciones del modelo separadas del texto extraído del PDF (#30)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

NotaVisual = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]


class DescripcionPagina(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    estado: Literal["interpretada", "ambigua", "ilegible"]
    descripcion: str = Field(max_length=6000)
    cobertura_completa: bool
    limitaciones: list[NotaVisual] = Field(max_length=20)

    @model_validator(mode="after")
    def descripcion_coherente(self):
        if self.estado != "ilegible" and not self.descripcion.strip():
            raise ValueError("Una interpretación requiere descripción")
        if self.estado == "ilegible" and self.cobertura_completa:
            raise ValueError("Una página ilegible no tiene cobertura completa")
        if (self.estado != "interpretada" or not self.cobertura_completa) and not self.limitaciones:
            raise ValueError("Las omisiones y ambigüedades requieren explicación")
        return self


class InterpretacionVisual(DescripcionPagina):
    document_id: str = Field(min_length=1)
    hash_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    pagina: int = Field(ge=1, le=100)
    imagen_referencia: str = Field(min_length=1)
    imagen_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    modelo: str = Field(min_length=1)
    version_prompt: str
    version_raster: str

    @model_validator(mode="after")
    def referencia_canonica(self):
        if self.imagen_referencia != f"pagina:{self.pagina}":
            raise ValueError("La imagen de referencia es la página original completa")
        return self
