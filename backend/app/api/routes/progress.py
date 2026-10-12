"""Issue #35: operaciones inmediatas de estudio, fuera de la cola de generación."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request

from app.api.deps import get_sesion_actual
from app.schemas.progress import ProgressEventResponse, ProgressResponse, QuizAnswerResponse
from app.schemas.requests import ProgressEvent, QuizAnswerRequest

router = APIRouter(prefix="/api", tags=["Estudio"])


def get_progreso(request: Request):
    return request.app.state.progress


@router.post("/quizzes/{generation_id}/answers", response_model=QuizAnswerResponse)
def responder(
    generation_id: str,
    solicitud: QuizAnswerRequest,
    sesion=Depends(get_sesion_actual),
    servicio=Depends(get_progreso),
    clave: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    return servicio.responder(sesion["token"], generation_id, solicitud, clave)


@router.post("/progress/events", response_model=ProgressEventResponse)
def evento(
    solicitud: ProgressEvent,
    sesion=Depends(get_sesion_actual),
    servicio=Depends(get_progreso),
    clave: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    return servicio.registrar_evento(sesion["token"], solicitud, clave)


@router.get("/progress", response_model=ProgressResponse)
def consultar(sesion=Depends(get_sesion_actual), servicio=Depends(get_progreso)):
    return servicio.consultar(sesion["token"])
