"""Precisión de transporte del tutor #37, sin ampliar formatos pedagógicos v1."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.enums import OutputLanguage, RecipientProfile
from app.schemas.pedagogical import Referencia
from app.schemas.responses import EvaluacionCalidad


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=128)
    pregunta: str = Field(min_length=1, max_length=2000)
    perfil_destinatario: RecipientProfile
    idioma_salida: OutputLanguage

    @field_validator("pregunta")
    @classmethod
    def pregunta_no_vacia(cls, valor):
        if not valor.strip():
            raise ValueError("La pregunta no puede estar vacía")
        return valor.strip()


class BorradorChat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fuera_de_alcance: bool
    respuesta: str = Field(max_length=5000)
    referencias: list[Referencia] = Field(max_length=100)

    @model_validator(mode="after")
    def respuesta_coherente(self):
        if self.fuera_de_alcance:
            if self.respuesta or self.referencias:
                raise ValueError("Una abstención no puede introducir contenido")
        elif not self.respuesta.strip() or not self.referencias:
            raise ValueError("Una respuesta requiere texto y citas")
        return self


class ChatAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    document_hash: str
    pregunta: str
    perfil_destinatario: RecipientProfile
    idioma_salida: OutputLanguage
    estado: Literal["respondida", "fuera_de_alcance"]
    respuesta: str
    referencias: list[Referencia]
    evaluacion_calidad: EvaluacionCalidad | None

    @model_validator(mode="after")
    def aprobacion(self):
        if self.estado == "respondida" and (
            not self.referencias
            or self.evaluacion_calidad is None
            or self.evaluacion_calidad.estado_evaluacion != "aprobada"
        ):
            raise ValueError("La respuesta requiere citas y revisión aprobada")
        if self.estado == "fuera_de_alcance" and (self.referencias or self.evaluacion_calidad is not None):
            raise ValueError("La abstención no es material aprobado")
        return self
