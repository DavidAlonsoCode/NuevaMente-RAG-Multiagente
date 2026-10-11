"""Tres lecturas demo aprobadas por delegación del equipo; contract-change H03."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.documents.demo import ServicioCatalogoDemo
from app.schemas.documents_http import DocumentoResponse, DocumentosResponse, FuenteDocumentoResponse
from app.schemas.errors import ErrorAplicacion, ErrorCode, ErrorResponse

router = APIRouter(prefix="/api/demo/documents", tags=["Documentos demo"])
bearer_demo = HTTPBearer(auto_error=False)
ERRORES = {codigo: {"model": ErrorResponse} for codigo in (400, 401, 404, 409, 422, 503)}


def get_catalogo(request: Request) -> ServicioCatalogoDemo:
    catalogo = request.app.state.demo_documents_query
    if catalogo is None:
        raise ErrorAplicacion(ErrorCode.PROVIDER_UNAVAILABLE, "El catálogo demo no está disponible.")
    return catalogo


def get_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_demo),
    catalogo: ServicioCatalogoDemo = Depends(get_catalogo),
) -> str:
    if credentials is None:
        raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "Se requiere una sesión válida.")
    # Validación existente de sesión/expiración/revocación, sin renovar actividad
    # ni escribir en SQLite/OCI desde las lecturas demo.
    catalogo.sesion(credentials.credentials)
    if "workspace_id" in request.query_params:
        raise ErrorAplicacion(ErrorCode.VALIDATION_ERROR, "Las rutas demo no aceptan workspace_id.")
    return credentials.credentials


@router.get("", response_model=DocumentosResponse, responses=ERRORES)
def listar(
    token: str = Depends(get_token),
    catalogo: ServicioCatalogoDemo = Depends(get_catalogo),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> DocumentosResponse:
    return catalogo.pagina(token, limit=limit, cursor=cursor)


@router.get("/{document_id}", response_model=DocumentoResponse, responses=ERRORES)
def detalle(document_id: str, token: str = Depends(get_token), catalogo: ServicioCatalogoDemo = Depends(get_catalogo)):
    return catalogo.detalle(token, document_id)


@router.get("/{document_id}/sources/{chunk_id}", response_model=FuenteDocumentoResponse, responses=ERRORES)
def fuente(
    document_id: str,
    chunk_id: str,
    token: str = Depends(get_token),
    catalogo: ServicioCatalogoDemo = Depends(get_catalogo),
):
    return catalogo.fuente(token, document_id, chunk_id)
