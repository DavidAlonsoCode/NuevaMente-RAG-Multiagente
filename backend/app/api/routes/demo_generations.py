"""Ejemplos curados #55: lectura pública autenticada, sin acceso privado implícito."""

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel

from app.api.routes.demo_documents import ERRORES, get_token
from app.demo.catalog import CatalogoResultadosDemo
from app.demo.models import CatalogoEjemplos
from app.schemas.documents_http import FuenteDocumentoResponse
from app.schemas.progress import QuizAnswerResponse
from app.schemas.requests import QuizAnswerRequest
from app.schemas.responses import PedagogicalStudentOutput

router = APIRouter(prefix="/api/demo/generations", tags=["Ejemplos demo"], responses=ERRORES)


def get_resultados(request: Request):
    return CatalogoResultadosDemo(request.app.state.storage_provider, request.app.state.demo_documents_query)


class CatalogoVacio(BaseModel):
    items: list = []


@router.get("", response_model=CatalogoEjemplos | CatalogoVacio)
def listar(token=Depends(get_token), servicio=Depends(get_resultados)):
    return servicio.leer(token) or CatalogoVacio()


@router.get("/{generation_id}", response_model=PedagogicalStudentOutput)
def detalle(generation_id: str, token=Depends(get_token), servicio=Depends(get_resultados)):
    return servicio.estudiante(token, generation_id)


@router.get("/{generation_id}/sources/{chunk_id}", response_model=FuenteDocumentoResponse)
def fuente(generation_id: str, chunk_id: str, token=Depends(get_token), servicio=Depends(get_resultados)):
    return servicio.fuente(token, generation_id, chunk_id)


@router.get("/{generation_id}/sources/{chunk_id}/image")
def imagen(generation_id: str, chunk_id: str, token=Depends(get_token), servicio=Depends(get_resultados)):
    return Response(
        servicio.imagen(token, generation_id, chunk_id), media_type="image/png", headers={"Cache-Control": "no-store"}
    )


@router.post("/{generation_id}/answers", response_model=QuizAnswerResponse)
def responder(
    generation_id: str, solicitud: QuizAnswerRequest, token=Depends(get_token), servicio=Depends(get_resultados)
):
    return servicio.responder(token, generation_id, solicitud)
