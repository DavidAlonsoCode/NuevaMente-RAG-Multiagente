"""Adaptador HTTP documental #19 sobre los servicios y gestor compartidos."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.api.deps import get_sesion_actual
from app.core.rag.vectorstore import IndiceInconsistenteError
from app.documents.admission import AdmisionNoConfirmadaError
from app.documents.query import ServicioConsultaDocumental
from app.jobs.manager import DeadlineExcedidoError, TrabajoCanceladoError
from app.schemas.documents import CoberturaDocumento, EntradaArchivoDocumento, MetadatosDocumento
from app.schemas.documents_http import (
    CoberturaDocumentoResponse,
    DocumentoResponse,
    DocumentosResponse,
    ErrorDocumentoResponse,
    FuenteDocumentoResponse,
    MetadataDocumentoResponse,
    RetiradaDocumentoResponse,
    UploadDocumentoResponse,
)
from app.schemas.errors import ErrorAplicacion, ErrorCode, ErrorResponse
from app.schemas.requests import UploadRequest
from app.storage.provider import StorageUnavailable

router = APIRouter(prefix="/api/documents", tags=["Documentos"])
ERRORES = {codigo: {"model": ErrorResponse} for codigo in (400, 401, 404, 409, 413, 422, 429, 503)}
MARGEN_TRANSPORTE = 64 * 1024


def get_consulta(request: Request) -> ServicioConsultaDocumental:
    consulta = request.app.state.documents_query
    if consulta is None:
        raise ErrorAplicacion(ErrorCode.PROVIDER_UNAVAILABLE, "El servicio documental no está disponible.")
    return consulta


def _documento(fila: dict) -> DocumentoResponse:
    # Proyección explícita; ninguna fila operativa se serializa al cliente.
    try:
        metadata = MetadatosDocumento.model_validate_json(fila["metadatos_json"]) if fila["metadatos_json"] else None
        cobertura = CoberturaDocumento.model_validate_json(fila["cobertura_json"]) if fila["cobertura_json"] else None
    except ValidationError:
        raise StorageUnavailable("La metadata documental no es verificable") from None
    return DocumentoResponse(
        document_id=fila["document_id"],
        titulo=fila["titulo"],
        status=fila["estado"],
        metadata=MetadataDocumentoResponse.model_validate(metadata.model_dump(exclude={"huella_config_parser"}))
        if metadata is not None
        else None,
        cobertura=CoberturaDocumentoResponse(
            paginas_procesadas=cobertura.paginas_procesadas,
            paginas_omitidas=cobertura.paginas_omitidas,
            paginas_visuales_pendientes=cobertura.paginas_visuales_pendientes,
            secciones_procesadas=cobertura.secciones,
            secciones_omitidas=None if cobertura.paginas_omitidas or cobertura.paginas_visuales_pendientes else [],
            secciones_visuales_pendientes=None if cobertura.paginas_visuales_pendientes else [],
        )
        if cobertura is not None
        else None,
        # Un diagnóstico legacy no validado puede contener datos privados.
        error=ErrorDocumentoResponse(message="El procesamiento no se completó; consultar el trabajo vinculado.")
        if fila["estado"] == "failed"
        else None,
    )


async def _stream_acotado(request: Request, maximo: int):
    total = 0
    async for parte in request.stream():
        total += len(parte)
        if total > maximo:
            raise ErrorAplicacion(ErrorCode.DOCUMENT_TOO_LARGE, "La petición excede el límite de upload.")
        yield parte


def _validacion(error: ValidationError):
    raise RequestValidationError(error.errors()) from None


def _admitir(servicio, funcion, token, entrada, clave, *archivo):
    try:
        recibo = funcion(token, *archivo, entrada, clave=clave)
    except AdmisionNoConfirmadaError:
        raise ErrorAplicacion(
            ErrorCode.STORAGE_UNAVAILABLE,
            "La confirmación de admisión sigue indeterminada.",
            {"confirmacion": "indeterminada", "idempotency_key_presente": clave is not None},
        ) from None
    except ErrorAplicacion as error:
        if error.error.details and error.error.details.get("motivo") == "workspace_busy":
            raise ErrorAplicacion(ErrorCode.RATE_LIMITED, "El espacio tiene un trabajo activo o encolado.") from None
        if error.error.code == ErrorCode.STORAGE_UNAVAILABLE:
            # 503 jamás prueba fracaso definitivo ni acredita recuperación.
            raise ErrorAplicacion(
                ErrorCode.STORAGE_UNAVAILABLE,
                "No se pudo verificar la confirmación documental.",
                {"confirmacion": "no_verificable", "idempotency_key_presente": clave is not None},
            ) from error
        raise
    ruta = f"/api/jobs/{recibo.job_id}"
    return UploadDocumentoResponse(
        document_id=recibo.document_id,
        status=recibo.estado,
        job_id=recibo.job_id,
        status_url=ruta,
        events_url=f"{ruta}/events",
        cancel_url=f"{ruta}/cancel",
    )


@router.post(
    "/upload",
    status_code=202,
    response_model=UploadDocumentoResponse,
    responses=ERRORES,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": UploadRequest.model_json_schema()},
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "additionalProperties": False,
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            "titulo": {"type": "string", "maxLength": 300},
                        },
                    }
                },
            },
        }
    },
)
async def upload(
    request: Request,
    sesion: dict = Depends(get_sesion_actual),
    clave: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> UploadDocumentoResponse:
    servicio = request.app.state.documents_admission
    if servicio is None:
        raise ErrorAplicacion(
            ErrorCode.PROVIDER_UNAVAILABLE, "Upload requiere proveedor y cuotas de embeddings explícitos."
        )
    tipo = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if tipo == "application/json":
        partes = [p async for p in _stream_acotado(request, 6 * servicio.limites.max_bytes_texto + MARGEN_TRANSPORTE)]
        try:
            entrada = UploadRequest.model_validate_json(b"".join(partes))
        except ValidationError as error:
            _validacion(error)
        return await run_in_threadpool(_admitir, servicio, servicio.admitir_texto, sesion["token"], entrada, clave)
    if tipo != "multipart/form-data":
        raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Usar application/json o multipart/form-data.")
    try:
        form = await MultiPartParser(
            request.headers,
            _stream_acotado(request, servicio.limites.max_bytes_pdf + MARGEN_TRANSPORTE),
            max_files=1,
            max_fields=1,
            max_part_size=1200,
        ).parse()
    except MultiPartException:
        raise ErrorAplicacion(
            ErrorCode.VALIDATION_ERROR, "Multipart inválido: usar un file y titulo opcional."
        ) from None
    try:
        pares = form.multi_items()
        if any(k not in ("file", "titulo") for k, _ in pares) or len(pares) != len(set(k for k, _ in pares)):
            raise ErrorAplicacion(ErrorCode.VALIDATION_ERROR, "Campos multipart desconocidos o repetidos.")
        archivo, titulo = form.get("file"), form.get("titulo")
        if not isinstance(archivo, UploadFile) or isinstance(titulo, UploadFile):
            raise ErrorAplicacion(ErrorCode.VALIDATION_ERROR, "El campo file debe ser un archivo.")
        try:
            entrada = EntradaArchivoDocumento(source_name=archivo.filename or "", titulo=titulo)
        except ValidationError as error:
            _validacion(error)
        return await run_in_threadpool(
            _admitir, servicio, servicio.admitir_archivo, sesion["token"], entrada, clave, archivo.file
        )
    finally:
        await form.close()


@router.get("", response_model=DocumentosResponse, responses=ERRORES)
def listar(
    sesion: dict = Depends(get_sesion_actual),
    consulta: ServicioConsultaDocumental = Depends(get_consulta),
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    cursor: Annotated[str | None, Query(max_length=1024)] = None,
) -> DocumentosResponse:
    try:
        pagina = consulta.pagina(sesion["token"], limit=limit, cursor=cursor)
    except (DeadlineExcedidoError, TrabajoCanceladoError):
        consulta._sesion(sesion["token"])
        raise ErrorAplicacion(ErrorCode.STORAGE_UNAVAILABLE, "La lectura documental no pudo completarse.") from None
    return DocumentosResponse(items=[_documento(d) for d in pagina.documentos], next_cursor=pagina.siguiente_id)


@router.get("/{document_id}", response_model=DocumentoResponse, responses=ERRORES)
def detalle(
    document_id: str,
    sesion: dict = Depends(get_sesion_actual),
    consulta: ServicioConsultaDocumental = Depends(get_consulta),
) -> DocumentoResponse:
    try:
        return _documento(consulta.detalle(sesion["token"], document_id))
    except DeadlineExcedidoError:
        raise ErrorAplicacion(ErrorCode.STORAGE_UNAVAILABLE, "La lectura documental no pudo completarse.") from None


@router.get("/{document_id}/sources/{chunk_id}", response_model=FuenteDocumentoResponse, responses=ERRORES)
def fuente(
    document_id: str,
    chunk_id: str,
    sesion: dict = Depends(get_sesion_actual),
    consulta: ServicioConsultaDocumental = Depends(get_consulta),
) -> FuenteDocumentoResponse:
    try:
        chunk = consulta.fuente(sesion["token"], document_id, chunk_id)
    except IndiceInconsistenteError:
        raise ErrorAplicacion(
            ErrorCode.INVALID_STATE, "El índice documental no es consistente con la fuente."
        ) from None
    except DeadlineExcedidoError:
        raise ErrorAplicacion(ErrorCode.STORAGE_UNAVAILABLE, "La lectura del índice no pudo completarse.") from None
    return FuenteDocumentoResponse(
        document_id=chunk.document_id,
        chunk_id=chunk.chunk_id,
        texto=chunk.texto,
        pagina=chunk.pagina,
        seccion=chunk.seccion,
        seccion_id=chunk.seccion_id,
    )


@router.delete(
    "/{document_id}",
    status_code=204,
    responses={**ERRORES, 202: {"model": RetiradaDocumentoResponse}},
    summary="Retirar documento y limpiar sus recursos vinculados",
)
def retirar(document_id: str, request: Request, sesion: dict = Depends(get_sesion_actual)) -> Response:
    efectos = request.app.state.documents_effects
    if efectos is None:
        raise ErrorAplicacion(ErrorCode.STORAGE_UNAVAILABLE, "El ciclo de vida documental no está disponible.")
    try:
        completada = efectos.retirar(sesion["token"], document_id)
    except ErrorAplicacion:
        raise
    except Exception as error:
        # Antes del commit no existe una aceptación verificable. Después,
        # EfectosDocumentales conserva la obligación y responde pendiente.
        raise ErrorAplicacion(ErrorCode.STORAGE_UNAVAILABLE, "La retirada no pudo confirmarse.") from error
    if completada is None:
        raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no encontrado.")
    if not completada:
        return JSONResponse(status_code=202, content=RetiradaDocumentoResponse().model_dump())
    return Response(status_code=204)
