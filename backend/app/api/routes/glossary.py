"""Glosario #39: caché verificada 200 o recibo del gestor común 202."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.deps import get_sesion_actual
from app.api.openapi_metadata import IDEMPOTENCIA_COMUN, parametros, respuestas
from app.api.routes.generations import _operar
from app.api.routes.jobs import _respuesta
from app.schemas.glossary import GlossaryJobResponse, GlossaryOutput
from app.schemas.requests import GlossaryRequest

router = APIRouter(prefix="/api/glossaries", tags=["Glosario"])


@router.post(
    "",
    status_code=202,
    response_model=GlossaryJobResponse,
    responses={
        **respuestas(400, 401, 404, 409, 422, 429, 503, exito=202),
        **respuestas(exito=200, modelo=GlossaryJobResponse, descripcion="Caché aprobada verificada, con result."),
    },
    openapi_extra=parametros(),
    description="JSON con document_id propio ready, perfil_destinatario e idioma_salida. "
    "202 para trabajo en curso; 200 para caché completada verificada. Ambos usan GlossaryJobResponse, "
    "glossary_id = job_id; seguir status/events/cancel comunes. La caché no crea trabajos ni consume IA.",
)
def generar(
    solicitud: GlossaryRequest,
    request: Request,
    response: Response,
    sesion=Depends(get_sesion_actual),
    clave: Annotated[str | None, Header(alias="Idempotency-Key", description=IDEMPOTENCIA_COMUN)] = None,
):
    servicio = request.app.state.glossary
    trabajo = _operar(servicio.encolar, sesion["token"], solicitud, clave)
    if trabajo["status"] == "completed":
        _operar(servicio.consultar, sesion["token"], trabajo["job_id"])
        response.status_code = 200
    servicio.consulta._sesion(sesion["token"])
    return GlossaryJobResponse(**_respuesta(trabajo).model_dump(), glossary_id=trabajo["job_id"])


@router.get(
    "/{glossary_id}",
    response_model=GlossaryOutput,
    responses=respuestas(401, 404, 409, 422, 503),
    openapi_extra=parametros(),
    description="Glosario verificado, con citas y limitaciones por término. "
    "Antes de completarse devuelve 409; ajeno o retirado, 404. Revalida vigencia de la fuente.",
)
def consultar(glossary_id: str, request: Request, sesion=Depends(get_sesion_actual)):
    return _operar(request.app.state.glossary.consultar, sesion["token"], glossary_id)
