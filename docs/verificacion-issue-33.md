# Issue 33 — Progreso de generación en React

Fecha: 16/10/2026. Rama: `feature/issue-33-react-sse`, desde develop `4c61545`
(migración React del PR #135). No modifica contratos HTTP ni el worker.

## Implementación

- Fetch SSE con Bearer, `Accept-Language`, cursor `Last-Event-ID`, UTF-8
  incremental, delimitadores CR/LF, comentarios y data multilínea. Se acotan los
  frames y se rechaza progreso de otra generación o con contenido de borrador.
- Al desconectarse consulta el estado confirmado y retoma el mismo stream con
  backoff y `Retry-After`. No vuelve a ejecutar `POST /api/generate`. Un 401
  invalida la sesión; un recurso inexistente o respuesta incompatible detiene
  la reconexión automática. El lector se cancela al cambiar de espacio o salir.
- Etapas y redacciones proceden de eventos reales del backend. No se inventan
  porcentajes. Los heartbeats permiten actualizar la posición de cola por HTTP.
- Después de recuperar el espacio, descubre la generación activa por el
  historial propio y conserva sus parámetros y fuente. No guarda tokens,
  códigos de recuperación ni cursores en WebStorage.
- Cancelar conserva el estado en ejecución hasta la confirmación del worker.
  Rechazados, fallidos y cancelados no muestran borradores. El rechazo explica
  que se agotaron las redacciones y ofrece acotar sección o aportar otra fuente.
- El indicador de contenido revisado exige el paquete validado y persistencia
  confirmada. El reintento de persistencia conserva su clave idempotente y no
  llama a generación.
- Textos ES/EN/PT y región de estado accesible. El cambio de idioma conserva
  el cursor y reabre la suscripción sin cambiar el idioma del material.

## Evidencia local

`npm test`: **54 pruebas aprobadas** (18 regresiones conservadas y 36 nuevas).
TypeScript y build de producción aprobados. Ruff y formato Python aprobados.
OpenAPI se contrasta con el snapshot existente; no se introduce otro contrato.

Las 31 pruebas dirigidas de generación, jobs y gateway dieron 30 aprobadas y un
timeout de espera a persistencia (15 s) con el worker todavía `running`. Ese caso
pasó aislado en 12,21 s, sin ampliar el plazo ni cambiar código de producción.

La comprobación manual usó `react_backend` con directorio nuevo, `.env` aislado,
Gemini/almacenamiento simulados y DNS externo bloqueado. SQLite, Chroma, worker
y LangGraph son los del proyecto. Con una demora de seis segundos por redacción:

- Se observó Writer y redacción 1 por SSE.
- Cancelar mostró primero la solicitud pendiente y después el estado cancelado.
  El documento siguió disponible y no hubo errores de consola.
- Se refrescó durante otra generación y se recuperó el espacio con su código.
  Reaparecieron el trabajo activo, su fuente y Writer/redacción 1. La base de
  prueba conservó tres generaciones tras tres admisiones: una cancelada y dos
  completadas; recuperar no añadió una cuarta generación.
- Con `--reject-quality`, la interfaz mostró el diagnóstico y las acciones
  después del rechazo, sin material ni errores de consola. Acotar llevó el foco
  al selector de sección. A 320 px no hubo desbordamiento horizontal.

Capturas sin código de recuperación:

- [Cancelación confirmada](evidencia/issue-33/cancelado-simulado.jpg).
- [Trabajo activo recuperado](evidencia/issue-33/recuperado-simulado.jpg).
- [Rechazo de calidad](evidencia/issue-33/rechazado-simulado.jpg).

La CI añade navegador con Caddy/TLS locales para reconectar al cambiar idioma,
comprobar cursor, refrescar/recuperar, cancelar con proveedor en vuelo y
conservar el documento. Otro caso fuerza tres redacciones rechazadas y verifica
diagnóstico sin borrador. Sus resultados deben consultarse en el PR de esta rama.

## Límites

No se consumieron Gemini/OCI reales ni se aprovisionaron recursos. Por autorización
del responsable del 16/10, el criterio de generación con proveedores reales y el
recorrido público se aceptan en #133. El cierre de #33 acredita la implementación
y los cuatro criterios locales ajustados, sin completar esa aceptación externa.
#51 y #53 conservan sus propios criterios y no se cierran por este merge. La
interfaz Streamlit anterior fue retirada en #139; sus evidencias históricas no
sustituyen las de React.
