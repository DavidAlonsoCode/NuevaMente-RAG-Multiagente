"""Descargas #43 autorizadas, sin redirecciones ni credenciales en los archivos."""

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response

from app.api.deps import get_sesion_actual
from app.api.openapi_metadata import parametros, respuestas
from app.api.routes.generations import _operar
from app.schemas.responses import PaqueteExportado

router = APIRouter(prefix="/api/exports", tags=["Exportaciones"])


@router.get(
    "/{generation_id}",
    responses=respuestas(
        401,
        404,
        409,
        422,
        503,
        modelo=PaqueteExportado,
        contenido={
            "application/json": {},
            "text/markdown": {"schema": {"type": "string"}},
            "application/pdf": {"schema": {"type": "string", "format": "binary"}},
            "text/csv": {"schema": {"type": "string"}},
            "text/tab-separated-values": {"schema": {"type": "string"}},
            "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
        },
        cabeceras={
            "Content-Disposition": {
                "description": 'attachment; filename="{generation_id}.{format}"',
                "schema": {"type": "string"},
            },
            "Cache-Control": {"schema": {"type": "string", "const": "private, no-store"}},
            "X-Content-Type-Options": {"schema": {"type": "string", "const": "nosniff"}},
        },
        error_422="EXPORT_INCOMPATIBLE para format desconocido o incompatible; VALIDATION_ERROR para petición inválida.",
    ),
    openapi_extra=parametros(),
    description="Descarga autorizada de una generación completada con fuente vigente. JSON conserva los bytes "
    "del canónico completo, incluidas soluciones del quiz; no es GenerationJobResponse ni su vista estudiante. "
    "json/md/pdf admiten los cinco formatos pedagógicos; csv/tsv/apkg sólo flashcards. Markdown/CSV/TSV usan UTF-8. "
    "Validar sesión, pertenencia y estado precede a validar format: ajeno 404, no completado 409. "
    "No redirige a URLs públicas, ni llama al LLM. Revalida autorización y contenido después del I/O.",
)
def descargar(
    generation_id: str,
    request: Request,
    format: str = Query(
        default="json",
        description="json, md, pdf, csv, tsv o apkg; desconocido/incompatible: 422 EXPORT_INCOMPATIBLE.",
    ),
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
