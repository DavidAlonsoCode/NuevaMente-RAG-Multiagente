from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import AwareDatetime, BaseModel, ConfigDict

from app.api.deps import get_sesion_actual, get_session_manager, get_workspace_actual
from app.api.openapi_metadata import parametros, respuestas
from app.api.security import (
    RateLimiter,
    intento_recuperacion,
)
from app.schemas.requests import RecoverSessionRequest
from app.schemas.responses import RotatedCodeResponse, SessionResponse, WorkspaceCreatedResponse
from app.session.manager import SessionManager

# defino el enrutador de fastapi para gestionar los espacios de trabajo anonimos de los usuarios
# con estos limitadores de tasa (RateLimiter) protejo los puntos de creacion y recuperacion contra ataques de fuerza bruta
router = APIRouter(tags=["Workspaces"])
limitar_creacion = RateLimiter(key="workspace_creation", limite_global=50, limite_ip=5)
limitar_recuperacion = RateLimiter(key="workspace_recovery", limite_global=100, limite_ip=10)


# con esta clase de pydantic defino el esquema de respuesta para el estado y consumo de recursos del espacio
# utilizo tipos conscientes de zona horaria (AwareDatetime) para asegurar el manejo exacto de los vencimientos
class EstadoEspacio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workspace_id: str
    expira_en: AwareDatetime
    ultima_actividad_en: AwareDatetime | None
    uso: dict[str, int]
    almacenamiento: dict


@router.get(
    "/api/workspaces/current",
    response_model=EstadoEspacio,
    summary="Uso y vencimiento del espacio",
    responses=respuestas(401, 503),
    openapi_extra=parametros(),
)
# aqui obtengo la informacion de uso y fecha de expiracion del espacio de trabajo correspondiente a la sesion activa
# consulto tanto la base de datos operativa como el estado del presupuesto en oracle cloud infrastructure (oci)
def estado_workspace(request: Request, workspace_id: str = Depends(get_workspace_actual)):
    db, storage = request.app.state.db, request.app.state.storage_provider
    datos = db.obtener_workspace(workspace_id)
    from app.schemas.errors import ErrorAplicacion, ErrorCode

    if datos is None:
        raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "El espacio dejó de estar disponible.")
    presupuesto = getattr(storage, "presupuesto", None)
    resumen = presupuesto.resumen() if presupuesto is not None else {"alerta": False, "escrituras_bloqueadas": False}
    # Sólo agregados; nombres de objetos y datos de otros espacios son privados.
    return EstadoEspacio(
        workspace_id=workspace_id,
        expira_en=datos["expira_en"],
        ultima_actividad_en=datos["ultima_actividad_en"],
        uso=db.uso_espacio(workspace_id),
        almacenamiento={**resumen, "provider": "mock" if request.app.state.config.mock_oci else "oci"},
    )


@router.post(
    "/api/workspaces",
    response_model=WorkspaceCreatedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Crear espacio anónimo",
    dependencies=[Depends(limitar_creacion)],
    responses=respuestas(400, 429, 503, exito=201),
    openapi_extra=parametros(),
)
# en este punto de entrada creo un nuevo espacio de trabajo totalmente anonimo sin requerir correos ni contrasenias
# devuelvo por unica vez el identificador del espacio y un codigo seguro de recuperacion de alta entropia
def create_workspace(manager: SessionManager = Depends(get_session_manager)) -> WorkspaceCreatedResponse:
    """
    Crea un espacio anónimo.
    Devuelve una sola vez el workspace_id, el código de recuperación
    (≥128 bits, agrupado) y el token de sesión.
    """
    return manager.crear_espacio()


@router.post(
    "/api/sessions/recover",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Recuperar espacio anónimo",
    dependencies=[Depends(limitar_recuperacion)],
    responses=respuestas(400, 401, 409, 422, 429, 503, exito=201, recuperacion=True),
    openapi_extra=parametros(),
)
# aqui permito restaurar el acceso a un espacio de trabajo previo mediante el codigo de recuperacion
# utilizo un administrador de contexto (intento_recuperacion) para auditar intentos fallidos y mitigar accesos no autorizados
def recover_workspace(
    request: Request, recovery_request: RecoverSessionRequest, manager: SessionManager = Depends(get_session_manager)
) -> SessionResponse:
    """
    Recupera un espacio anónimo existente.
    Devuelve workspace_id y un token nuevo. No devuelve el código de recuperación
    ni prolonga tokens previos; el código se conserva hasta rotación o expiración del espacio.
    """
    with intento_recuperacion(request):
        return manager.recuperar_sesion(recovery_request.recovery_code)


@router.delete(
    "/api/sessions/current",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cerrar y revocar la sesión actual",
    responses=respuestas(401, exito=204),
    openapi_extra=parametros(),
)
# con esta ruta revoco y cierro de forma inmediata la sesion vinculada al token enviado en la cabecera
# devuelvo un codigo http 204 no content indicando que la operacion se realizo con exito sin enviar cuerpo de respuesta
def close_session(
    sesion: str = Depends(get_sesion_actual), manager: SessionManager = Depends(get_session_manager)
) -> Response:
    """
    Revoca únicamente la sesión asociada al token enviado en la cabecera.
    """
    manager.revocar_sesion(sesion["token"])
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/api/workspaces/current/recovery-code",
    response_model=RotatedCodeResponse,
    status_code=status.HTTP_200_OK,
    summary="Rotar código y revocar sesiones previas",
    responses=respuestas(401, 409, 503),
    openapi_extra=parametros(),
)
# aqui ejecuto la rotacion del codigo de recuperacion del espacio de trabajo actual
# esta accion invalida inmediatamente todas las sesiones anteriores activas para prevenir accesos indebidos
def rotate_recovery_code(
    workspace_id: str = Depends(get_workspace_actual), manager: SessionManager = Depends(get_session_manager)
) -> RotatedCodeResponse:
    """
    Invalida el código de recuperación anterior, revoca todas las sesiones
    asociadas al espacio y emite un nuevo código de recuperación y token.
    """
    nuevo_codigo, nuevo_token = manager.rotar_credenciales(workspace_id)

    return RotatedCodeResponse(recovery_code=nuevo_codigo, token=nuevo_token)


@router.delete(
    "/api/workspaces/current",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Borrar espacio y agendar limpieza",
    responses=respuestas(401, exito=204),
    openapi_extra=parametros(),
)
# con esta ruta solicito el borrado definitivo del espacio de trabajo actual y todos sus recursos
# programo la limpieza de documentos y artefactos persistidos cumpliendo las politicas de privacidad del sistema
def delete_workspace(
    workspace_id: str = Depends(get_workspace_actual), manager: SessionManager = Depends(get_session_manager)
) -> Response:
    """
    Borra el espacio de trabajo actual y agenda su limpieza.
    """
    manager.borrar_workspace(workspace_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
