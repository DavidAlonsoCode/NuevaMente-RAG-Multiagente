"""Admisión del tutor; consulta/SSE/cancelación en /api/jobs de #20."""

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_sesion_actual
from app.api.routes.generations import _operar
from app.api.routes.jobs import _autorizar_chat, _respuesta
from app.schemas.chat import ChatRequest
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.internal import TrabajoComunResponse

router = APIRouter(prefix="/api/chat", tags=["Chat"])


@router.post("", status_code=202, response_model=TrabajoComunResponse)
def conversar(solicitud: ChatRequest, request: Request, sesion=Depends(get_sesion_actual)):
    servicio = request.app.state.chat
    if servicio is None:
        raise ErrorAplicacion(ErrorCode.PROVIDER_UNAVAILABLE, "El tutor requiere proveedores y cuotas explícitos.")
    job = _operar(servicio.encolar, sesion["token"], solicitud)
    trabajo = _autorizar_chat(request, servicio.gestor.estado(job), sesion["token"])
    servicio.consulta._sesion(sesion["token"])
    return _respuesta(trabajo)
