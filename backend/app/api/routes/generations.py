"""Rutas #31; el gestor común conserva ejecución, eventos y cancelación."""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from app.api.deps import get_sesion_actual, get_session_manager
from app.api.openapi_metadata import CURSOR_SSE, IDEMPOTENCIA_GENERACION, parametros, respuestas, stream
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


@router.post(
    "/generate",
    status_code=202,
    response_model=GenerationJobResponse,
    responses=respuestas(400, 401, 404, 409, 422, 429, 503, exito=202),
    openapi_extra=parametros(),
    description="JSON con documento propio ready y parámetros pedagógicos. 202 admite el trabajo, no acredita éxito. "
    "modo_generacion: multiagente por defecto o basico sin evaluación independiente. Explicitarlo solicita paquete 1.1; "
    "omitirlo conserva 1.0. En básico el artefacto es no_evaluado, con métricas nulas; no hay fallback desde multiagente. "
    "Usar status_url/events_url; contenido sólo se entrega al completar y confirmar persistencia. "
    "El quiz es la vista estudiante, sin correct_option_id ni justificacion; el export autorizado es canónico.",
)
def generar(
    solicitud: GenerateRequest,
    sesion=Depends(get_sesion_actual),
    servicio=Depends(get_generaciones),
    clave: Annotated[str | None, Header(alias="Idempotency-Key", description=IDEMPOTENCIA_GENERACION)] = None,
):
    fila = _operar(servicio.generar, sesion["token"], solicitud, clave)
    return _operar(servicio.estado, sesion["token"], fila["generation_id"])


@router.get(
    "/generations",
    response_model=PaginaGeneraciones,
    responses=respuestas(400, 401, 409, 422, 503),
    openapi_extra=parametros(),
    description="Historial propio: orden descendente por creación e ID. "
    "Cursor opaco firmado, ligado al espacio y filtros; alterado o incompatible devuelve 400. "
    "desde/hasta requieren zona horaria y desde <= hasta; fechas se convierten a UTC. "
    "documento filtra por document_id; perfil/formato/idioma usan los enums existentes.",
)
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


@router.get(
    "/generations/{generation_id}",
    response_model=GenerationJobResponse,
    responses=respuestas(401, 404, 409, 422, 503),
    openapi_extra=parametros(),
    description="Estado propio, con HTTP 200 también en failed/rejected_quality/cancelled. "
    "contenido es null salvo completed con persistencia confirmada; quiz usa la vista estudiante. "
    "El diagnóstico terminal en error no convierte la consulta en error HTTP.",
)
def consultar(generation_id: str, sesion=Depends(get_sesion_actual), servicio=Depends(get_generaciones)):
    return _operar(servicio.estado, sesion["token"], generation_id)


@router.post(
    "/generations/{generation_id}/cancel",
    response_model=GenerationJobResponse,
    responses=respuestas(401, 404, 409, 422, 503),
    openapi_extra=parametros(),
    description="Cancelación cooperativa; running puede continuar hasta observarla. Repetir cancelled es idempotente; "
    "otro terminal devuelve 409. Desconectar SSE no equivale a cancelar.",
)
def cancelar(generation_id: str, sesion=Depends(get_sesion_actual), servicio=Depends(get_generaciones)):
    return _operar(servicio.cancelar, sesion["token"], generation_id)


@router.post(
    "/generations/{generation_id}/persist",
    status_code=202,
    response_model=GenerationJobResponse,
    responses=respuestas(400, 401, 404, 409, 422, 429, 503, exito=202),
    openapi_extra=parametros(),
    description="Reintenta sólo persistencia del aprobado retenido hasta 24 h, mismo objeto y sin llamadas LLM. "
    "Responde 202 incluso si ya estaba completed; un aprobado local perdido no se reconstruye desde OCI.",
)
def persistir(
    generation_id: str,
    sesion=Depends(get_sesion_actual),
    servicio=Depends(get_generaciones),
    clave: Annotated[str | None, Header(alias="Idempotency-Key", description=IDEMPOTENCIA_GENERACION)] = None,
):
    _operar(servicio.persistir, sesion["token"], generation_id, clave)
    return _operar(servicio.estado, sesion["token"], generation_id)


@router.get(
    "/generations/{generation_id}/events",
    response_class=StreamingResponse,
    responses=respuestas(401, 404, 409, 422, 503, **stream("generation_id")),
    openapi_extra=parametros(),
    description=stream("generation_id")["descripcion"],
)
def eventos(
    generation_id: str,
    request: Request,
    sesion=Depends(get_sesion_actual),
    servicio=Depends(get_generaciones),
    gestor=Depends(get_gestor),
    manager=Depends(get_session_manager),
    ultimo_id: Annotated[int, Header(alias="Last-Event-ID", ge=0, le=2**63 - 1, description=CURSOR_SSE)] = 0,
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
