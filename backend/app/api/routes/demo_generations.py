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
    responses=respuestas(401, 409, 422, 503),
    openapi_extra=parametros(),
    description="Catálogo curado con Bearer vigente; sin catálogo devuelve items=[]. No crea trabajos, "
    "no renueva actividad ni admite workspace_id. Los ejemplos compartidos no otorgan acceso al espacio privado.",
)
def listar(token=Depends(get_token), servicio=Depends(get_resultados)):
    return servicio.leer(token) or CatalogoVacio()


@router.get(
    "/{generation_id}",
    response_model=PedagogicalStudentOutput,
    responses=respuestas(401, 404, 409, 422, 503),
    openapi_extra=parametros(),
    description="Ejemplo publicado e íntegro con sesión vigente. Vista estudiante del quiz, sin soluciones "
    "ni justificación; un diagnóstico rechazado no entrega paquete. No admite IDs privados ni workspace_id.",
)
def detalle(generation_id: str, token=Depends(get_token), servicio=Depends(get_resultados)):
    return servicio.estudiante(token, generation_id)


@router.get(
    "/{generation_id}/sources/{chunk_id}",
    response_model=FuenteDocumentoResponse,
    responses=respuestas(401, 404, 409, 422, 503),
    openapi_extra=parametros(),
    description="Fuente del snapshot publicado, con identidad/hash y localización verificados. "
    "No busca chunks privados ni crea embeddings; requiere Bearer y rechaza workspace_id.",
)
def fuente(generation_id: str, chunk_id: str, token=Depends(get_token), servicio=Depends(get_resultados)):
    return servicio.fuente(token, generation_id, chunk_id)


@router.get(
    "/{generation_id}/sources/{chunk_id}/image",
    response_class=Response,
    responses=respuestas(
        401,
        404,
        409,
        422,
        503,
        contenido={"image/png": {"schema": {"type": "string", "format": "binary"}}},
        cabeceras={"Cache-Control": {"schema": {"type": "string", "const": "no-store"}}},
    ),
    openapi_extra=parametros(),
    description="PNG original asociado a la fuente visual del ejemplo, nunca su interpretación textual. "
    "Bearer vigente; sin imagen publicada devuelve 404. No admite workspace_id.",
)
def imagen(generation_id: str, chunk_id: str, token=Depends(get_token), servicio=Depends(get_resultados)):
    return Response(
        servicio.imagen(token, generation_id, chunk_id), media_type="image/png", headers={"Cache-Control": "no-store"}
    )


@router.post(
    "/{generation_id}/answers",
    response_model=QuizAnswerResponse,
    responses=respuestas(401, 404, 409, 422, 503),
    openapi_extra=parametros(),
    description="Corrección determinista del quiz publicado, con justificación y citas. Conserva el body "
    "QuizAnswerRequest (incluido event_id), pero no registra progreso privado ni llama al LLM. "
    "Pregunta inexistente devuelve 404; opción ajena o formato no quiz devuelve 422.",
)
def responder(
    generation_id: str, solicitud: QuizAnswerRequest, token=Depends(get_token), servicio=Depends(get_resultados)
):
    return servicio.responder(token, generation_id, solicitud)
