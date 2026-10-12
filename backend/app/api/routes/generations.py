"""Rutas #31; el gestor común conserva ejecución, eventos y cancelación."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.api.deps import get_sesion_actual, get_session_manager
from app.api.routes.jobs import _stream, get_gestor
from app.jobs.manager import ColaLlenaError, DeadlineExcedidoError, EspacioOcupadoError, TrabajoCanceladoError
from app.schemas.enums import JobStatus, OutputLanguage, PedagogicalFormat, RecipientProfile
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.requests import GenerateRequest
from app.schemas.responses import GenerationJobResponse

router = APIRouter(prefix="/api", tags=["Generaciones"])


class GeneracionHistorial(BaseModel):
    model_config = ConfigDict(extra="forbid")
    generation_id: str
    document_id: str
    status: JobStatus
    created_at: datetime
    parametros: GenerateRequest | None


class PaginaGeneraciones(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[GeneracionHistorial]
    next_cursor: str | None


def get_generaciones(request: Request):
    servicio = request.app.state.generations
    if servicio is None:
        raise ErrorAplicacion(ErrorCode.PROVIDER_UNAVAILABLE, "Generación requiere proveedores y cuotas explícitos.")
    return servicio


def _operar(funcion, *args):
    try:
        return funcion(*args)
    except ColaLlenaError:
        raise ErrorAplicacion(ErrorCode.QUEUE_FULL, "La cola está llena.") from None
    except EspacioOcupadoError:
        raise ErrorAplicacion(ErrorCode.RATE_LIMITED, "El espacio tiene un trabajo activo.") from None
    except (TrabajoCanceladoError, DeadlineExcedidoError):
        raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La fuente no está disponible para esta operación.") from None


@router.post("/generate", status_code=202, response_model=GenerationJobResponse)
def generar(
    solicitud: GenerateRequest,
    sesion=Depends(get_sesion_actual),
    servicio=Depends(get_generaciones),
    clave: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    fila = _operar(servicio.generar, sesion["token"], solicitud, clave)
    return _operar(servicio.estado, sesion["token"], fila["generation_id"])


@router.get("/generations", response_model=PaginaGeneraciones)
def listar(
    sesion=Depends(get_sesion_actual),
    servicio=Depends(get_generaciones),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: str | None = None,
    documento: str | None = None,
    perfil: RecipientProfile | None = None,
    formato: PedagogicalFormat | None = None,
    idioma: OutputLanguage | None = None,
    desde: datetime | None = None,
    hasta: datetime | None = None,
):
    fechas = {}
    for clave, valor in (("desde", desde), ("hasta", hasta)):
        if valor is not None:
            if valor.tzinfo is None:
                raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Las fechas requieren zona horaria.")
            fechas[clave] = valor.astimezone(timezone.utc).isoformat()
    if desde is not None and hasta is not None and desde > hasta:
        raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Intervalo de fechas inválido.")
    return servicio.pagina(
        sesion["token"],
        cursor=cursor,
        limite=limit,
        documento=documento,
        perfil=perfil.value if perfil else None,
        formato=formato.value if formato else None,
        idioma=idioma.value if idioma else None,
        **fechas,
    )


@router.get("/generations/{generation_id}", response_model=GenerationJobResponse)
def consultar(generation_id: str, sesion=Depends(get_sesion_actual), servicio=Depends(get_generaciones)):
    return _operar(servicio.estado, sesion["token"], generation_id)


@router.post("/generations/{generation_id}/cancel", response_model=GenerationJobResponse)
def cancelar(generation_id: str, sesion=Depends(get_sesion_actual), servicio=Depends(get_generaciones)):
    return _operar(servicio.cancelar, sesion["token"], generation_id)


@router.post("/generations/{generation_id}/persist", status_code=202, response_model=GenerationJobResponse)
def persistir(
    generation_id: str,
    sesion=Depends(get_sesion_actual),
    servicio=Depends(get_generaciones),
    clave: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    _operar(servicio.persistir, sesion["token"], generation_id, clave)
    return _operar(servicio.estado, sesion["token"], generation_id)


@router.get("/generations/{generation_id}/events", response_class=StreamingResponse)
def eventos(
    generation_id: str,
    request: Request,
    sesion=Depends(get_sesion_actual),
    servicio=Depends(get_generaciones),
    gestor=Depends(get_gestor),
    manager=Depends(get_session_manager),
    ultimo_id: Annotated[int, Header(alias="Last-Event-ID", ge=0, le=2**63 - 1)] = 0,
):
    _operar(servicio._propia, sesion["token"], generation_id)
    job = servicio.db.job_generacion(generation_id)
    if job is None:
        raise ErrorAplicacion(ErrorCode.INVALID_STATE, "La generación no tiene trabajo disponible.")

    def leer(gestor, manager, sesion, job_id, ultimo):
        fila = servicio._propia_db(sesion["token"], generation_id)
        eventos = gestor.store.eventos_desde(generation_id, ultimo)
        if eventos or fila["status"] not in {"queued", "running"}:
            _operar(servicio._propia, sesion["token"], generation_id)
        return gestor.estado(job_id), eventos

    return StreamingResponse(
        _stream(request, gestor, manager, sesion, job["job_id"], ultimo_id, lector=leer, generation_id=generation_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
