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


class ConceptoProgreso(BaseModel):
    """Estado de un concepto marcado: la UI decide si ofrece repasarlo hoy."""

    model_config = ConfigDict(extra="forbid")
    document_id: str
    generation_id: str | None = None
    concepto: str
    veces: int = Field(ge=1)
    ultima_fecha: str | None = None


class ProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=0)
    conceptos_revisados: int = Field(ge=0)
    conceptos_repasos: int = Field(ge=0)
    conceptos: list[ConceptoProgreso] = Field(default_factory=list)
    flashcards_vistas: int = Field(ge=0)
    quiz_intentos: int = Field(ge=0)
    quiz_aciertos_primer_intento: int = Field(ge=0)
    quiz_total_primer_intento: int = Field(ge=0)
    quiz_aciertos_posteriores: int = Field(ge=0)
    tiempo_estimado_restante_minutos: float = Field(ge=0)
