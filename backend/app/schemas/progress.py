"""Feedback y agregados de estudio del contrato v1, issue #35."""

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.pedagogical import Referencia


class QuizAnswerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    correcta: bool
    correct_option_id: str
    justificacion: str
    referencias: list[Referencia]


class ProgressEventResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str
    version: int = Field(ge=1)


class ProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    conceptos_revisados: int = Field(ge=0)
    flashcards_vistas: int = Field(ge=0)
    quiz_intentos: int = Field(ge=0)
    quiz_aciertos_primer_intento: int = Field(ge=0)
    quiz_total_primer_intento: int = Field(ge=0)
    quiz_aciertos_posteriores: int = Field(ge=0)
    tiempo_estimado_restante_minutos: float = Field(ge=0)
