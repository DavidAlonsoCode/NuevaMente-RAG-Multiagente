"""Glosario #39: caché verificada 200 o recibo del gestor común 202."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response

from app.api.deps import get_sesion_actual
from app.api.routes.generations import _operar
from app.api.routes.jobs import _respuesta
from app.schemas.glossary import GlossaryJobResponse, GlossaryOutput
from app.schemas.requests import GlossaryRequest

router = APIRouter(prefix="/api/glossaries", tags=["Glosario"])


@router.post("", status_code=202, response_model=GlossaryJobResponse)
def generar(
    solicitud: GlossaryRequest,
    request: Request,
    response: Response,
    sesion=Depends(get_sesion_actual),
    clave: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    servicio = request.app.state.glossary
    trabajo = _operar(servicio.encolar, sesion["token"], solicitud, clave)
    if trabajo["status"] == "completed":
        _operar(servicio.consultar, sesion["token"], trabajo["job_id"])
        response.status_code = 200
    servicio.consulta._sesion(sesion["token"])
    return GlossaryJobResponse(**_respuesta(trabajo).model_dump(), glossary_id=trabajo["job_id"])


@router.get("/{glossary_id}", response_model=GlossaryOutput)
def consultar(glossary_id: str, request: Request, sesion=Depends(get_sesion_actual)):
    return _operar(request.app.state.glossary.consultar, sesion["token"], glossary_id)
