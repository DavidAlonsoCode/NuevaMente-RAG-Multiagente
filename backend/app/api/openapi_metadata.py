"""Metadatos OpenAPI reutilizables; no valida peticiones ni serializa respuestas."""

from copy import deepcopy

from app.schemas.errors import ErrorResponse

REQUEST_ID = {
    "description": "Identificador de trazabilidad; también aparece como request_id en el JSON de error.",
    "schema": {"type": "string"},
}
ERRORES = {
    400: "INVALID_REQUEST: formato, cursor, fechas o cabecera inválidos según la operación.",
    401: "SESSION_INVALID: Bearer ausente/inválido, sesión vencida/revocada o recuperación inválida.",
    404: "NOT_FOUND: recurso inexistente, ajeno o retirado, sin revelar su existencia.",
    409: "INVALID_STATE o IDEMPOTENCY_CONFLICT según la operación: estado incompatible o clave con otro cuerpo.",
    413: "DOCUMENT_TOO_LARGE: contenido o transporte por encima de los límites documentales.",
    422: "VALIDATION_ERROR: la petición no respeta los tipos o reglas semánticas de la operación.",
    429: "RATE_LIMITED, QUEUE_FULL o RECOVERY_LOCKED según la operación; no garantiza Retry-After.",
    500: "INTERNAL: fallo técnico no manejado; mensaje seguro y request_id para diagnóstico.",
    503: "STORAGE_UNAVAILABLE o PROVIDER_UNAVAILABLE según la dependencia requerida por la operación.",
}


def parametros():
    """Cabeceras leídas por middleware/handlers; sólo documentación, sin Depends/Header nuevos."""
    return {
        "parameters": [
            {
                "name": "X-Request-ID",
                "in": "header",
                "required": False,
                "schema": {"type": "string"},
                "description": "Se respeta si cumple [A-Za-z0-9_.-]{1,128}; ausente o inválido se reemplaza por uno nuevo.",
            },
            {
                "name": "Accept-Language",
                "in": "header",
                "required": False,
                "schema": {"type": "string"},
                "description": "Mensajes de error es/en/pt con preferencias q; por defecto es. No cambia idioma_salida.",
            },
        ]
    }


def respuestas(
    *estados,
    exito=200,
    modelo=None,
    contenido=None,
    cabeceras=None,
    descripcion="Operación realizada.",
    error_422=None,
    recuperacion=False,
):
    """Cada decorador elige sus errores; model en responses sólo se usa para OpenAPI."""
    resultado = {}
    for estado in (*estados, 500):
        headers = {"X-Request-ID": deepcopy(REQUEST_ID)}
        if estado == 401:
            headers["WWW-Authenticate"] = {
                "description": "Bearer cuando lo incluye el autenticador; puede faltar para una sesión inválida.",
                "schema": {"type": "string"},
            }
        if estado == 429 and recuperacion:
            headers["Retry-After"] = {
                "description": "60 segundos para RECOVERY_LOCKED; no se garantiza para RATE_LIMITED.",
                "schema": {"type": "string"},
            }
        resultado[estado] = {
            "model": ErrorResponse,
            "description": error_422 if estado == 422 and error_422 else ERRORES[estado],
            "headers": headers,
        }
    resultado[exito] = {
        "description": descripcion,
        "headers": {"X-Request-ID": deepcopy(REQUEST_ID), **deepcopy(cabeceras or {})},
    }
    if modelo is not None:
        resultado[exito]["model"] = modelo
    if contenido is not None:
        resultado[exito]["content"] = contenido
    return resultado


IDEMPOTENCIA_UPLOAD = (
    "Opcional; conservar por intención y reintentos. Debe contener texto no vacío; este servicio no impone "
    "un máximo de caracteres. Conflicto de cuerpo: 409. Confirmación indeterminada: 503 no prueba fracaso definitivo."
)
IDEMPOTENCIA_GENERACION = (
    "Opcional, 1..200 caracteres y no sólo espacios. La misma intención conserva el ID; otro cuerpo con la misma "
    "clave produce 409 IDEMPOTENCY_CONFLICT. No garantiza exactamente una llamada al proveedor."
)
IDEMPOTENCIA_COMUN = (
    "Opcional, 1..128 caracteres y no sólo espacios. Conservar por intención; reutilizar con otro cuerpo "
    "produce 409 IDEMPOTENCY_CONFLICT. Ausencia de clave no promete recuperación de una respuesta perdida."
)
CURSOR_SSE = (
    "Último id SSE recibido: entero 0..9223372036854775807, default 0. Entrega sólo eventos posteriores "
    "mientras exista el registro; no crea otra ejecución. El id viaja fuera de data."
)


def stream(identificador):
    """El cuerpo es texto encuadrado SSE, no una lista JSON de modelos internos."""
    return {
        "descripcion": (
            f"SSE autenticado de progreso; data identifica {identificador}, step, status e iteration (puede ser null). "
            "Cada evento contiene líneas id: <entero>, event: progress y data: <JSON>, seguidas de una línea vacía. "
            "id es monotónico y NO forma parte del JSON data. Heartbeat como comentario ': heartbeat' y línea vacía, "
            "comprobado cada 15 segundos mientras siga activo. Last-Event-ID permite reconexión; desconectar no cancela. "
            "Cierra al terminar el trabajo. No transmite contenido generado ni resultados. Antes de enviar headers, "
            "los fallos usan HTTP y ErrorResponse. Después, ante un error de acceso/storage manejado, envía "
            "event: error y data: <ErrorResponse>, sin id, y cierra conservando el HTTP 200 ya enviado."
        ),
        "contenido": {
            "text/event-stream": {
                "schema": {"type": "string"},
                "example": (
                    'id: 42\nevent: progress\ndata: {"' + identificador + '":"ejemplo","step":"critic",'
                    '"status":"running","iteration":2}\n\n: heartbeat\n\n'
                ),
            }
        },
        "cabeceras": {
            "Cache-Control": {"schema": {"type": "string", "const": "no-cache"}},
            "X-Accel-Buffering": {"schema": {"type": "string", "const": "no"}},
        },
    }


RESULTADOS_JOBS = (
    "Trabajos comunes de ingestión, chat y glosario; las generaciones usan /api/generations. "
    "result conserva su contrato dict|null: ingestión devuelve workspace_id, document_id, hash_sha256, estado, "
    "cantidad_chunks, reutilizado, metadatos y cobertura (completed del job no siempre implica documento ready); "
    "chat devuelve ChatAnswer: document_id/document_hash, pregunta, perfil_destinatario, idioma_salida, estado, "
    "respuesta, referencias y evaluacion_calidad; fuera_de_alcance es abstención, sin citas ni evaluación. "
    "Glosario devuelve GlossaryOutput con terminos, limitaciones, fuente, evaluación y trazabilidad. "
    "result sólo tiene valor en completed. error del job es dict|null con diagnóstico terminal; sus códigos "
    "(p. ej. DEADLINE o INGESTION_INDICE) no son el catálogo estable de errores HTTP. cancel_url es null al terminar."
)
