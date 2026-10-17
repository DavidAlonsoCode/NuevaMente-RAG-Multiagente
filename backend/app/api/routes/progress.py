"""Issue #35: operaciones inmediatas de estudio, fuera de la cola de generación."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request

from app.api.deps import get_sesion_actual
from app.api.openapi_metadata import IDEMPOTENCIA_COMUN, parametros, respuestas
from app.schemas.progress import ProgressEventResponse, ProgressResponse, QuizAnswerResponse
from app.schemas.requests import ProgressEvent, QuizAnswerRequest

router = APIRouter(prefix="/api", tags=["Estudio"])


def get_progreso(request: Request):
    return request.app.state.progress


@router.post(
    "/quizzes/{generation_id}/answers",
    response_model=QuizAnswerResponse,
    responses=respuestas(400, 401, 404, 409, 422, 429, 503),
    openapi_extra=parametros(),
    description="JSON con question_id, option_id y event_id estable. Corrección determinista, sin LLM ni cola. "
    "Devuelve clave, justificación y citas exclusivamente de la pregunta respondida. El primer intento y el "
    "acierto los calcula el backend; event_id es global al espacio y no puede reutilizarse con otro cuerpo.",
)
def responder(
    generation_id: str,
    solicitud: QuizAnswerRequest,
    sesion=Depends(get_sesion_actual),
    servicio=Depends(get_progreso),
    clave: Annotated[str | None, Header(alias="Idempotency-Key", description=IDEMPOTENCIA_COMUN)] = None,
):
    return servicio.responder(sesion["token"], generation_id, solicitud, clave)


@router.post(
    "/progress/events",
    response_model=ProgressEventResponse,
    responses=respuestas(400, 401, 404, 409, 422, 429, 503),
    openapi_extra=parametros(),
    description="JSON con event_id estable y tipo concepto_revisado o flashcard_vista. concepto_revisado requiere "
    "document_id/concepto; flashcard_vista requiere generation_id/flashcard_id (frente y dorso). "
    "respuesta_quiz y puntajes no son entradas públicas. Repetir el evento conserva su version, sin duplicar métricas.",
)
def evento(
    solicitud: ProgressEvent,
    sesion=Depends(get_sesion_actual),
    servicio=Depends(get_progreso),
    clave: Annotated[str | None, Header(alias="Idempotency-Key", description=IDEMPOTENCIA_COMUN)] = None,
):
    return servicio.registrar_evento(sesion["token"], solicitud, clave)


@router.get(
    "/progress",
    response_model=ProgressResponse,
    responses=respuestas(401, 409, 503),
    openapi_extra=parametros(),
    description="Agregados del espacio autenticado; sin eventos, version y contadores son cero. "
    "Tiempo restante orientativo, calculado sobre material aprobado y fuente ready; no acredita dominio.",
)
def consultar(sesion=Depends(get_sesion_actual), servicio=Depends(get_progreso)):
    return servicio.consultar(sesion["token"])
