"""Descargas #43 autorizadas, sin redirecciones ni credenciales en los archivos."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.api.deps import get_sesion_actual
from app.api.routes.generations import _operar

router = APIRouter(prefix="/api/exports", tags=["Exportaciones"])


@router.get(
    "/{generation_id}",
    responses={
        200: {
            "content": {
                "application/json": {},
                "text/markdown": {},
                "text/csv": {},
                "text/tab-separated-values": {},
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            }
        }
    },
)
def descargar(
    generation_id: str,
    request: Request,
    format: str = Query(default="json"),
    sesion=Depends(get_sesion_actual),
):
    archivo = _operar(request.app.state.generations.exports.descargar, sesion["token"], generation_id, format)
    return Response(
        archivo.contenido,
        media_type=archivo.content_type,
        headers={
            "Content-Disposition": f'attachment; filename="{archivo.nombre}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
