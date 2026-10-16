"""Ejemplos curados #55: lectura pública autenticada, sin acceso privado implícito."""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.api.openapi_metadata import parametros, respuestas
from app.api.routes.demo_documents import get_token
from app.demo.catalog import CatalogoResultadosDemo
from app.demo.models import CatalogoEjemplos
from app.schemas.documents_http import FuenteDocumentoResponse
from app.schemas.progress import QuizAnswerResponse
from app.schemas.requests import QuizAnswerRequest
from app.schemas.responses import PedagogicalStudentOutput

router = APIRouter(prefix="/api/demo/generations", tags=["Ejemplos demo"])


def get_resultados(request: Request):
    return CatalogoResultadosDemo(request.app.state.storage_provider, request.app.state.demo_documents_query)


class CatalogoVacio(BaseModel):
    items: list = []


@router.get(
    "",
    response_model=CatalogoEjemplos | CatalogoVacio,
    responses=respuestas(401, 422, 503),
    openapi_extra=parametros(),
    description="Ejemplos publicados previamente, Bearer vigente sin renovar actividad. No admite workspace_id (422). "
    "Catálogo finito sin paginación, hasta doce entradas con fuente/hash, parámetros, fecha, tiempo y estado. "
    "Si no existe catálogo devuelve sólo items=[]; fallos técnicos no se convierten en catálogo vacío. "
    "provider=mock no acredita proveedores reales; rejected_quality sólo contiene diagnóstico, no borrador.",
)
def listar(token=Depends(get_token), servicio=Depends(get_resultados)):
    return servicio.leer(token) or CatalogoVacio()


@router.get(
    "/{generation_id}",
    response_model=PedagogicalStudentOutput,
    responses=respuestas(401, 404, 422, 503),
    openapi_extra=parametros(),
    description="Proyección estudiante de un ejemplo completed publicado, sin claves ni justificaciones iniciales del quiz. "
    "No lee generaciones privadas ni crea trabajos. Ejemplo ausente o rechazado: 404. "
    "Revalida sesión y huellas de paquete/fuente; no acepta workspace_id ni renueva actividad.",
)
def detalle(generation_id: str, token=Depends(get_token), servicio=Depends(get_resultados)):
    return servicio.estudiante(token, generation_id)


@router.get(
    "/{generation_id}/sources/{chunk_id}",
    response_model=FuenteDocumentoResponse,
    responses=respuestas(401, 404, 422, 503),
    openapi_extra=parametros(),
    description="Fragmento del snapshot publicado y verificado del ejemplo; no consulta recursos privados por fallback. "
    "Fuente o ejemplo ausente: 404; integridad o storage no verificables: 503. Bearer obligatorio, sin renovar actividad.",
)
def fuente(generation_id: str, chunk_id: str, token=Depends(get_token), servicio=Depends(get_resultados)):
    return servicio.fuente(token, generation_id, chunk_id)


@router.get(
    "/{generation_id}/sources/{chunk_id}/image",
    response_class=Response,
    responses=respuestas(
        401,
        404,
        422,
        503,
        contenido={"image/png": {"schema": {"type": "string", "format": "binary"}}},
        cabeceras={"Cache-Control": {"schema": {"type": "string", "const": "no-store"}}},
    ),
    openapi_extra=parametros(),
    description="PNG original del snapshot demo, con firma y hash verificados; Cache-Control: no-store. "
    "No es una descripción generada. Ejemplo, fuente o imagen ausente: 404; snapshot no verificable: 503. "
    "Requiere sesión válida y no acepta workspace_id.",
)
def imagen(generation_id: str, chunk_id: str, token=Depends(get_token), servicio=Depends(get_resultados)):
    return Response(
        servicio.imagen(token, generation_id, chunk_id), media_type="image/png", headers={"Cache-Control": "no-store"}
    )


@router.post(
    "/{generation_id}/answers",
    response_model=QuizAnswerResponse,
    responses=respuestas(401, 404, 422, 503),
    openapi_extra=parametros(),
    description="Práctica sobre un quiz demo publicado, sin persistir progreso privado, escribir ni llamar al LLM. "
    "Recibe QuizAnswerRequest; event_id no registra un evento de progreso y no usa Idempotency-Key. "
    "Repetir la misma respuesta conserva el feedback. Pregunta ausente: 404; opción inválida o formato no quiz: 422. "
    "Requiere Bearer vigente; no admite workspace_id ni renueva actividad.",
)
def responder(
    generation_id: str, solicitud: QuizAnswerRequest, token=Depends(get_token), servicio=Depends(get_resultados)
):
    return servicio.responder(token, generation_id, solicitud)
