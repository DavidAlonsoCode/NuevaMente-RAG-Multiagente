"""Adaptador HTTP documental #19 sobre los servicios y gestor compartidos."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from python_multipart.exceptions import FormParserError
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.api.deps import get_sesion_actual
from app.api.openapi_metadata import IDEMPOTENCIA_UPLOAD, parametros, respuestas
from app.core.rag.raster import RasterError
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
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.requests import UploadRequest
from app.storage.provider import StorageUnavailable

router = APIRouter(prefix="/api/documents", tags=["Documentos"])
MARGEN_TRANSPORTE = 64 * 1024


# con esta clase extiendo el analizador de formularios de starlette (MultiPartParser)
# sobreescribo el metodo on_end para verificar que el cuerpo de la peticion llego completo y no truncado
# en caso de error me aseguro de cerrar y limpiar todos los archivos temporales creados en disco
class _MultipartDocumental(MultiPartParser):
    """Exige el cierre del cuerpo y libera incluso partes no entregadas al form."""

    _terminado = False

    def on_end(self) -> None:
        super().on_end()
        self._terminado = True

    async def parse(self):
        try:
            form = await super().parse()
            # python-multipart.finalize() no valida el estado final. Una parte
            # truncada puede conservar un tempfile fuera de FormData.items.
            if not self._terminado:
                raise MultiPartException("El cuerpo multipart no está completo.")
            return form
        except BaseException:
            # Starlette conserva todos los temporales creados en esta lista,
            # también los de partes que todavía no llegaron a on_part_end.
            for archivo in self._files_to_close_on_error:
                archivo.close()
            raise


# con esta funcion de inyeccion de dependencias (Depends) obtengo el servicio de consulta documental
# valido que el servicio este cargado en la aplicacion para garantizar que el indice de busqueda vectorial este disponible
def get_consulta(request: Request) -> ServicioConsultaDocumental:
    consulta = request.app.state.documents_query
    if consulta is None:
        raise ErrorAplicacion(ErrorCode.PROVIDER_UNAVAILABLE, "El servicio documental no está disponible.")
    return consulta


# aqui transformo el registro crudo de la base de datos en el modelo de respuesta formal (DocumentoResponse)
# valido la estructura de los metadatos y la cobertura en formato json para no exponer datos internos sensibles
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
        # Transmitir detalle_error y codigo si existen
        error=ErrorDocumentoResponse(
            code=fila.get("codigo_error"),
            message=fila.get("detalle_error") or "El procesamiento no se completó; consultar el trabajo vinculado.",
        )
        if fila["estado"] == "failed"
        else None,
    )


# con esta funcion asincrona (generator) leo el flujo de datos de la peticion byte por byte
# controlo el tamanio acumulado para interrumpir la transferencia si excede el limite maximo permitido en la configuracion
async def _stream_acotado(request: Request, maximo: int):
    total = 0
    async for parte in request.stream():
        total += len(parte)
        if total > maximo:
            raise ErrorAplicacion(ErrorCode.DOCUMENT_TOO_LARGE, "La petición excede el límite de upload.")
        yield parte


def _validacion(error: ValidationError):
    raise RequestValidationError(error.errors()) from None


# aqui gestiono la admision del documento coordinando la transaccion entre la base de datos y el gestor de trabajos
# traduzco las excepciones de concurrencia o limite de almacenamiento en respuestas http estructuradas
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
    responses=respuestas(400, 401, 409, 413, 422, 429, 503, exito=202),
    description="JSON documento_titulo/documento_contenido (TXT), o multipart con un file PDF/MD/TXT y titulo opcional. "
    "Campos extra/repetidos, contenido vacío o firma inválida se rechazan. 20 MiB PDF, 5 MiB MD/TXT; "
    "100 páginas y 100k tokens se comprueban durante procesamiento. El transporte multipart admite 64 KiB extra; "
    "JSON se acota a 6 × 5 MiB + 64 KiB. 202 confirma admisión, no documento ready: consultar el job y el documento. "
    "Un espacio ocupado responde 429 RATE_LIMITED. Las precisiones documentales conservan su estado de revisión contractual.",
    openapi_extra={
        **parametros(),
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
                            "titulo": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 300,
                                "description": "Texto no vacío ni sólo espacios; opcional.",
                            },
                        },
                    }
                },
            },
        },
    },
)
# en esta ruta asincrona recibo la carga de documentos tecnicos en formato json o como archivo adjunto (multipart)
# uso la funcion run_in_threadpool de starlette para delegar el procesamiento intensivo a un hilo secundario sin bloquear el bucle principal
# esto cumple con el requisito de ingestion documental para alimentar el sistema de recuperacion aumentada (rag)
async def upload(
    request: Request,
    sesion: dict = Depends(get_sesion_actual),
    clave: Annotated[str | None, Header(alias="Idempotency-Key", description=IDEMPOTENCIA_UPLOAD)] = None,
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
        form = await _MultipartDocumental(
            request.headers,
            _stream_acotado(request, servicio.limites.max_bytes_pdf + MARGEN_TRANSPORTE),
            max_files=1,
            max_fields=1,
            max_part_size=1200,
        ).parse()
    except (MultiPartException, FormParserError):
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


@router.get(
    "",
    response_model=DocumentosResponse,
    responses=respuestas(400, 401, 409, 422, 503),
    openapi_extra=parametros(),
    description="Documentos propios, orden ascendente por creación UTC e ID. limit=20 (1..100); cursor opaco firmado "
    "ligado al espacio, no un offset ni snapshot. Metadata/cobertura desconocida se representa con null.",
)
# aqui devuelvo la lista paginada de todos los documentos subidos por el espacio de trabajo actual
# aplico un cursor de navegacion para recorrer los registros ordenados cronologicamente segun su tiempo de creacion en utc
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


@router.get(
    "/{document_id}",
    response_model=DocumentoResponse,
    responses=respuestas(401, 404, 409, 422, 503),
    openapi_extra=parametros(),
    description="Estado y metadata propios. processing/failed no habilitan fuentes. "
    "document_id es un identificador opaco; no acepta workspace_id para cambiar la autorización.",
)
# con este punto de entrada obtengo el detalle completo y el estado de procesamiento de un documento particular
# compruebo que el documento pertenezca a la sesion que realiza la consulta para garantizar el aislamiento entre usuarios
def detalle(
    document_id: str,
    sesion: dict = Depends(get_sesion_actual),
    consulta: ServicioConsultaDocumental = Depends(get_consulta),
) -> DocumentoResponse:
    try:
        return _documento(consulta.detalle(sesion["token"], document_id))
    except DeadlineExcedidoError:
        raise ErrorAplicacion(ErrorCode.STORAGE_UNAVAILABLE, "La lectura documental no pudo completarse.") from None


@router.get(
    "/{document_id}/sources/{chunk_id}",
    response_model=FuenteDocumentoResponse,
    responses=respuestas(401, 404, 409, 422, 503),
    openapi_extra=parametros(),
    description="Fragmento autorizado del documento ready; chunk_id debe pertenecer a esa fuente vigente. "
    "source_type=image_description identifica interpretación del modelo; imagen_url es una ruta autenticada "
    "a la página original. En texto, imagen_url es null; ubicación desconocida también usa null.",
)
# aqui consulto un fragmento textual especifico (chunk) que fue utilizado como fuente para responder o generar material
# diferencio si el texto proviene de una extraccion directa del documento o de una interpretacion de imagen realizada por el modelo
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
        source_type=chunk.source_type,
        interpretacion_modelo=chunk.source_type == "image_description",
        imagen_url=f"/api/documents/{document_id}/sources/{chunk.chunk_id}/image"
        if chunk.source_type == "image_description"
        else None,
    )


@router.get(
    "/{document_id}/sources/{chunk_id}/image",
    response_class=Response,
    responses=respuestas(
        401,
        404,
        409,
        422,
        503,
        contenido={
            "image/png": {"schema": {"type": "string", "format": "binary"}},
        },
        cabeceras={
            "Cache-Control": {"schema": {"type": "string", "const": "no-store"}},
            "X-Content-Type-Options": {"schema": {"type": "string", "const": "nosniff"}},
        },
    ),
    openapi_extra=parametros(),
    summary="Ver la página original autorizada",
    description="PNG binario de la página original rasterizada y verificada, no la descripción generada por IA. "
    "Revalida sesión, pertenencia de documento/chunk, publicación y hash; fuente visual no disponible: 404/409.",
)
# con esta funcion retorno la imagen binaria en formato png de la pagina original del documento tecnico
# configuro cabeceras de respuesta estrictas para evitar almacenamiento en cache y proteger la privacidad del usuario
def imagen_original(
    document_id: str,
    chunk_id: str,
    consulta: ServicioConsultaDocumental = Depends(get_consulta),
    sesion: dict = Depends(get_sesion_actual),
) -> Response:
    try:
        imagen = consulta.imagen(sesion["token"], document_id, chunk_id)
    except (RasterError, DeadlineExcedidoError):
        raise ErrorAplicacion(
            ErrorCode.STORAGE_UNAVAILABLE, "La página original no pudo rasterizarse dentro de los límites."
        ) from None
    except IndiceInconsistenteError:
        raise ErrorAplicacion(
            ErrorCode.INVALID_STATE, "El índice documental no es consistente con la fuente."
        ) from None
    return Response(
        imagen.contenido,
        media_type=imagen.mime_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.delete(
    "/{document_id}",
    status_code=204,
    responses={
        **respuestas(401, 404, 422, 503, exito=204),
        **respuestas(
            exito=202,
            modelo=RetiradaDocumentoResponse,
            descripcion="Retirada lógica confirmada; cleanup_pending=true, limpieza física pendiente.",
        ),
    },
    openapi_extra=parametros(),
    description="204 sin cuerpo si se verifica limpieza; 202 si quedan efectos físicos pendientes. "
    "Repetir sobre un retirado devuelve 404 sin eliminar la obligación durable de limpieza. No retira demo. "
    "La precisión de retirada conserva su estado de revisión contractual.",
    summary="Retirar documento y limpiar sus recursos vinculados",
)
# aqui gestiono la eliminacion o retirada de un documento y de todos sus vectores asociados en chromadb
# si la limpieza fisica de archivos en almacenamiento remoto queda pendiente respondo con codigo 202 accepted
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
