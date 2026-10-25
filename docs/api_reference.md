# Referencia de API — NuevaMente (#59)

Referencia del runtime actual, basada en el [OpenAPI versionado](openapi.json), las
[rutas FastAPI](../backend/app/api/routes/) y sus servicios. Cubre 37 operaciones.
No aprueba las precisiones `contract-change` pendientes en [contratos-api.md](contratos-api.md).

## Versiones y fuente técnica

| Identificador | Valor del snapshot | Significado |
|---|---|---|
| `info.version` | `0.1.0` | Versión declarada de la aplicación FastAPI; no es un prefijo de URL |
| `openapi` | `3.1.0` | Versión de la especificación que describe la API |
| `PedagogicalOutput.schema_version` | `1.0` | Versión del paquete pedagógico JSON, distinta de las anteriores |
| Paquetes con modo explícito | `1.1` | Multiagente aprobado o básico sin evaluación independiente; ver [modos](modos-generacion.md) |

`POST /api/generate` admite `modo_generacion=multiagente|basico`. Omitirlo conserva el modo y paquete anteriores; explicitarlo solicita 1.1. Los ejemplos curl de esta referencia omiten el campo y siguen comprobando 1.0. En básico, `completed` acredita guardado confirmado, el artefacto tiene `status=no_evaluado` y sus métricas son nulas. Una clave ya usada para otro modo devuelve 409. Los errores de evaluación del multiagente no activan el básico.

Desde la raíz, con las dependencias Python del proyecto instaladas:

```console
python .github/scripts/export_openapi.py
python .github/scripts/export_openapi.py --check
python .github/scripts/export_openapi.py --validate
python .github/scripts/verify_api_reference.py --check
python .github/scripts/verify_api_reference.py --local
```

Exportar genera desde `app.openapi()` en un proceso aislado. `--check` genera de
nuevo y falla ante drift, archivo ausente o JSON inválido sin sobrescribirlo.
`--validate` comprueba estructura OpenAPI 3.1 y referencias locales sin red;
no acredita por sí solo fidelidad de comportamiento. La última orden ejecuta
los bloques Bash de esta página por TCP local con los dobles existentes, nunca
contra un servicio público. Requiere también las herramientas indicadas abajo.

FastAPI ofrece `/docs` y `/openapi.json` directamente en el backend. Esto no
afirma que Caddy los publique. `/docs` describe la app viva: para revisar el
archivo versionado hay que cargar **este `openapi.json`** en un viewer compatible
con OpenAPI 3.1, por ejemplo Swagger UI 5, y comprobar que no haya errores de carga.

### Correspondencia con los ejemplos del enunciado

Los ejemplos de entrada/salida del [enunciado original, páginas 4–5](<../Enunciado/Enunciado - NuevaMente.pdf>)
y su [transcripción](<../Enunciado/Enunciado - Nuevamente.txt>) ilustran el escenario VCN.
La entrada combinada se adapta al flujo actual: `documento_titulo` y
`documento_contenido` van a `POST /api/documents/upload`; después de comprobar
el documento `ready`, `POST /api/generate` recibe su `document_id` y los
parámetros de adaptación, sin reenviar el texto.

| Campo | Etiqueta del ejemplo original | Valor máquina de la petición actual |
|---|---|---|
| `perfil_destinatario` | `Principiante` | `principiante` |
| `formato_salida` | `Flashcards` | `flashcards` |
| `nicho_sector` | `General` | `general` |
| `nivel_detalle` | `Didactico` | `didactico` |
| `idioma_salida` | No figura en el ejemplo | `es`, seleccionado explícitamente para este recorrido |

`status: "exito"` del ejemplo original no es un estado del contrato actual:
202 sólo admite el trabajo; su estado `completed` confirma la finalización.
El paquete multiagente usa `status: "aprobado"`; el básico 1.1, `no_evaluado`. La confirmación de escritura
se consulta como `persistencia.status_upload: "completado"` en la respuesta
del trabajo, fuera del canónico; no como `almacenamiento_oci.status_upload`.

Se validó la entrada separada y normalizada con `UploadRequest` y
`GenerateRequest`; los ejemplos JSON de [contratos-api.md](contratos-api.md)
se contrastan con los modelos mediante `test_schemas.py`. Los JSON originales
no son payloads literales del contrato actual. Por HTTP se ejecutaron los siete
bloques publicados aquí, con la fixture VCN `txt_ejemplo` y proveedores dobles:
no se enviaron literalmente los JSON originales ni se obtuvo su respuesta textual.

## Convenciones transversales

- Las rutas públicas son `GET /api/health`, `POST /api/workspaces` y
  `POST /api/sessions/recover`. Las demás requieren
  `Authorization: Bearer <token_de_sesion>`. Un recurso ajeno se oculta como 404.
  Los tokens son opacos: no decodificarlos como JWT ni enviarlos por query.
- Crear espacio devuelve una vez `workspace_id`, `token` y `recovery_code`.
  Recuperar devuelve **sólo `workspace_id` y `token`**, sin prolongar tokens previos.
  Rotar devuelve código/token nuevos y revoca sesiones previas. Cerrar sesión
  devuelve 204 sin cuerpo. Guardar las credenciales fuera de logs y del repositorio.
- `X-Request-ID` es opcional en la petición: se conserva si cumple
  `[A-Za-z0-9_.-]{1,128}`; si falta o es inválido se genera otro. Se devuelve en
  las respuestas y en el cuerpo de error. No es una clave de idempotencia.
- `Accept-Language` localiza los errores HTTP en `es`, `en` o `pt`, admite
  preferencias `q` y usa español por defecto. No modifica `idioma_salida`.
  Un `event: error` SSE ya iniciado no pasa por el localizador HTTP.
- `Content-Type: application/json` corresponde a los bodies JSON. En multipart
  dejar que curl genere el boundary; no fijar ese Content-Type manualmente.
  No hay body JSON en un 204. No confiar en la extensión de una descarga: validar
  código HTTP, Content-Type y contenido antes de darle su nombre final.
- Los modelos completos y sus enums, defaults y límites se enlazan en el índice
  al JSON Pointer del snapshot. Las validaciones semánticas están además en los
  [modelos de entrada](../backend/app/schemas/requests.py). Campos extra y textos
  vacíos pueden ser rechazados aunque no toda regla sea expresable en JSON Schema.

### Idempotencia y límites según servicio

| Operación | `Idempotency-Key` opcional | Semántica |
|---|---|---|
| Upload | Texto no vacío; sin máximo implementado de caracteres | Misma intención conserva documento/job; otro cuerpo, 409. Una confirmación indeterminada 503 no prueba fracaso definitivo |
| Generar / persistir | 1..200 caracteres, no sólo espacios | Clave por espacio/operación/cuerpo; conserva ID. No promete exactamente una llamada al proveedor |
| Chat / glosario | 1..128 caracteres, no sólo espacios | Reusar intención; otro cuerpo, 409 |
| Respuesta de quiz / evento de progreso | 1..128 caracteres, no sólo espacios | También requieren `event_id` estable, global al espacio; no duplicar métricas ni reutilizarlo con otro cuerpo |

Una nueva intención usa otra clave; un reintento de la misma conserva clave y
cuerpo. Sin clave no se promete recuperar una respuesta perdida. Los recorridos
no reintentan escrituras automáticamente. Si falla el transporte, conservar los
archivos/claves y resolver el resultado antes de enviar otra escritura.
Creación/recuperación/rotación/borrado no obtienen idempotencia por añadir ese header.

### Selección de fuentes y alcances

Generación, chat y glosario aceptan `document_id` histórico o una lista superior
`document_ids` de hasta cinco fuentes propias listas y vigentes. La normalización
ordena y deduplica; el primer ID es el primario canónico. Enviar también un
`document_id` diferente produce **422**, aunque la UI tenga el foco en él.
El orden A+B/B+A es equivalente para idempotencia; A+B/A+C no lo es.
El contrato de chat utilizado por la ruta es
[`schemas/chat.py`](../backend/app/schemas/chat.py).

Ejemplo **ilustrativo** de generación explícita A+B en Básico 1.1. Los IDs no
representan documentos de una sesión disponible; no es una captura de ejecución:

```json
{
  "document_id": "doc-a",
  "document_ids": ["doc-b", "doc-a"],
  "perfil_destinatario": "principiante",
  "formato_salida": "flashcards",
  "nicho_sector": "general",
  "nivel_detalle": "didactico",
  "idioma_salida": "es",
  "modo_generacion": "basico",
  "alcance": {"tipo": "multi_documento"}
}
```

| Alcance de generación | Comportamiento |
|---|---|
| `documento_completo` | Una fuente; es el default. Con varias fuentes superiores se normaliza a `multi_documento`. |
| `seccion` | Una fuente con `seccion_id` válido dentro de ella. |
| `multi_documento` | Dos a cinco fuentes explícitas. |
| `espacio_completo` | El servidor fija al admitir todas las fuentes propias, listas y vigentes; una sola es válida, vacío da 404 y más de cinco da 422, sin truncar. |

Ejemplo **ilustrativo** de `espacio_completo` sin IDs ni modo explícito (Multiagente
1.0). También se admite la forma histórica con `document_id`; ese campo no limita
la resolución a ese documento:

```json
{
  "perfil_destinatario": "principiante",
  "formato_salida": "flashcards",
  "nicho_sector": "general",
  "nivel_detalle": "didactico",
  "idioma_salida": "es",
  "alcance": {"tipo": "espacio_completo"}
}
```

La admisión persiste primario, vínculos y snapshot completo de las fuentes
(ID, título, hash, versión, procedencia). El worker contrasta esa procedencia con
la autorización vigente, en ambos modos; una fuente cambiada antes de ejecutar
impide consumir proveedores con otra versión. El snapshot no concede acceso por
sí mismo. Los resultados y sus citas usan las fuentes guardadas y se revalidan.

Un reintento `espacio_completo` equivalente con la misma clave se reconoce antes
de consultar el conjunto actual o exigir proveedores/cuotas de un trabajo nuevo.
Conserva generación, primario, relaciones, snapshot y hash, sin otra reserva ni
ejecución, aunque se haya añadido C o se superen cinco documentos. Una clave nueva
resuelve el estado actual; retirar una fuente original sigue denegando el acceso.
Las formas históricas se contrastan con parámetros y vínculos originales, sin
reescribir hashes. Cambios reales de perfil, idioma, formato, detalle, nicho, modo
o alcance mantienen 409. También se preservan estrictamente las representaciones
unidocumentales anteriores y las ya guardadas con `document_ids` por esta PR.
Omitir modo conserva 1.0; explicitarlo solicita 1.1 en una intención nueva, sin
actualizar el paquete de una intención anterior equivalente.

Chat y glosario no reciben `alcance`. Ejemplo **ilustrativo** de chat A+B con
primario A, independientemente del foco visual:

```json
{
  "document_id": "doc-a",
  "document_ids": ["doc-b", "doc-a"],
  "pregunta": "¿Cómo se relacionan los conceptos de ambas fuentes?",
  "perfil_destinatario": "principiante",
  "idioma_salida": "es"
}
```

El mismo conjunto y perfil/idioma pueden enviarse a glosario omitiendo `pregunta`.
Su caché incluye todas las fuentes/versiones y la configuración pertinente;
no sustituye la comparación idempotente por clave/cuerpo.

React ofrece casillas de selección explícita, documento completo/sección para una
fuente y `multi_documento` para varias. El foco es independiente y la recuperación
de un trabajo activo conserva la selección original frente a páginas tardías.
Al llegar a cinco se pide desmarcar una antes de añadir otra; «Seleccionar todos»
se deshabilita con explicación si hay más de cinco documentos listos.
**No hay selector UI `espacio_completo`**: seleccionar todos envía IDs explícitos.
Más detalle: [contrato de procedencia e idempotencia](contratos-api.md#fuentes-alcances-y-procedencia-multidocumental-pr-146).

### Éxitos asíncronos, errores y persistencia

202 confirma admisión, **no éxito**. `queued` y `running` requieren seguimiento.
`completed` de generación entrega contenido sólo tras confirmar persistencia;
`failed`, `rejected_quality` y `cancelled` son terminales sin contenido aprobado.
`GenerationJobResponse` conserva la vista estudiante del quiz, sin soluciones.
El export autorizado entrega el canónico completo, incluidas soluciones; no se
debe usar esa descarga como vista estudiante.

`TrabajoComunResponse` conserva `result: dict|null` y `error: dict|null`:
ingestión devuelve identidad, hash, estado, chunks, reutilización, metadata y
cobertura; chat devuelve `ChatAnswer` (respuesta citada o abstención
`fuera_de_alcance`); glosario devuelve `GlossaryOutput`. `result` sólo tiene valor
en `completed`. Un job de ingestión `completed` puede dejar un documento
`processing` por visión pendiente: consultar además el documento y exigir `ready`.
Los jobs de generación no se consultan por `/api/jobs`.

Los errores HTTP usan [ErrorResponse](openapi.json#/components/schemas/ErrorResponse).
Ejemplo **ilustrativo**, no evidencia de ejecución ni credencial real:

```json
{"error":{"code":"EXPORT_INCOMPATIBLE","message":"El formato de descarga es incompatible con el contenido.","details":null},"request_id":"traza-ilustrativa"}
```

| HTTP | Códigos estables posibles, según operación |
|---|---|
| 400 | `INVALID_REQUEST` (cabecera, cursor, fechas o transporte inválidos) |
| 401 | `SESSION_INVALID` (Bearer/código inválido, sesión expirada o revocada) |
| 404 | `NOT_FOUND` (incluye ajeno o retirado) |
| 409 | `INVALID_STATE`, `IDEMPOTENCY_CONFLICT` |
| 413 | `DOCUMENT_TOO_LARGE` |
| 422 | `VALIDATION_ERROR`; en exports también `EXPORT_INCOMPATIBLE` para desconocido/incompatible |
| 429 | `QUEUE_FULL`, `RATE_LIMITED`, `RECOVERY_LOCKED` |
| 500 | `INTERNAL` (mensaje seguro; usar request_id para diagnóstico) |
| 503 | `STORAGE_UNAVAILABLE`, `PROVIDER_UNAVAILABLE` |

El índice indica qué HTTP aplican a cada operación; la tabla anterior no asigna
todos los códigos a todas las rutas. `WWW-Authenticate: Bearer` aparece cuando
lo incorpora el autenticador; puede faltar para una sesión inválida.
`RECOVERY_LOCKED` incluye `Retry-After: 60`; otros 429 no lo garantizan.
Los diagnósticos terminales dentro de una consulta HTTP 200 no son un error de
esa consulta. Los jobs comunes pueden usar códigos como `DEADLINE` o
`INGESTION_INDICE`, que no pertenecen al enum estable HTTP. Las generaciones
proyectan su diagnóstico a `ErrorBody`, con la causa en `details.causa`.

## Índice completo de operaciones

`path`, `query` y `header` indican la ubicación. Todas admiten los headers
transversales anteriores; el índice sólo repite los específicos. Todos los
errores listados tienen cuerpo JSON `ErrorResponse`. `format` sigue siendo un
string sin enum de validación: desconocido/incompatible se resuelve con 422,
después de comprobar acceso y estado. Los enlaces de modelos usan JSON Pointer
para localizarlos en el snapshot o en un viewer que lo soporte.

<!-- operations:start -->
| Operación | Auth | Entradas | Éxitos y contenido | Errores HTTP |
|---|---|---|---|---|
| `POST /api/chat` | Bearer | header `Idempotency-Key`: string / null<br>body `application/json` obligatorio: [ChatRequest](openapi.json#/components/schemas/ChatRequest) | **202** `application/json` [TrabajoComunResponse](openapi.json#/components/schemas/TrabajoComunResponse) | 400, 401, 404, 409, 422, 429, 500, 503 |
| `GET /api/demo/documents` | Bearer | query `limit`: integer; default `20`; minimum `1`; maximum `100`<br>query `cursor`: string / null | **200** `application/json` [DocumentosResponse](openapi.json#/components/schemas/DocumentosResponse) | 400, 401, 409, 422, 500, 503 |
| `GET /api/demo/documents/{document_id}` | Bearer | path `document_id`: string obligatorio | **200** `application/json` [DocumentoResponse](openapi.json#/components/schemas/DocumentoResponse) | 401, 404, 409, 422, 500, 503 |
| `GET /api/demo/documents/{document_id}/sources/{chunk_id}` | Bearer | path `document_id`: string obligatorio<br>path `chunk_id`: string obligatorio | **200** `application/json` [FuenteDocumentoResponse](openapi.json#/components/schemas/FuenteDocumentoResponse) | 401, 404, 409, 422, 500, 503 |
| `GET /api/demo/generations` | Bearer | Sin body/parámetros específicos | **200** `application/json` [CatalogoEjemplos](openapi.json#/components/schemas/CatalogoEjemplos) / [CatalogoVacio](openapi.json#/components/schemas/CatalogoVacio) | 401, 422, 500, 503 |
| `GET /api/demo/generations/{generation_id}` | Bearer | path `generation_id`: string obligatorio | **200** `application/json` [PedagogicalStudentOutput](openapi.json#/components/schemas/PedagogicalStudentOutput) / [ReviewedPedagogicalStudentOutputV11](openapi.json#/components/schemas/ReviewedPedagogicalStudentOutputV11) | 401, 404, 422, 500, 503 |
| `POST /api/demo/generations/{generation_id}/answers` | Bearer | path `generation_id`: string obligatorio<br>body `application/json` obligatorio: [QuizAnswerRequest](openapi.json#/components/schemas/QuizAnswerRequest) | **200** `application/json` [QuizAnswerResponse](openapi.json#/components/schemas/QuizAnswerResponse) | 401, 404, 422, 500, 503 |
| `GET /api/demo/generations/{generation_id}/sources/{chunk_id}` | Bearer | path `generation_id`: string obligatorio<br>path `chunk_id`: string obligatorio | **200** `application/json` [FuenteDocumentoResponse](openapi.json#/components/schemas/FuenteDocumentoResponse) | 401, 404, 422, 500, 503 |
| `GET /api/demo/generations/{generation_id}/sources/{chunk_id}/image` | Bearer | path `generation_id`: string obligatorio<br>path `chunk_id`: string obligatorio | **200** `image/png` string (binary) | 401, 404, 422, 500, 503 |
| `GET /api/documents` | Bearer | query `limit`: integer; default `20`; minimum `1`; maximum `100`<br>query `cursor`: string / null | **200** `application/json` [DocumentosResponse](openapi.json#/components/schemas/DocumentosResponse) | 400, 401, 409, 422, 500, 503 |
| `POST /api/documents/upload` | Bearer | header `Idempotency-Key`: string / null<br>body `application/json` obligatorio: object<br>body `multipart/form-data` obligatorio: `file`: string (binary) obligatorio; `titulo`: string opcional; minLength `1`; maxLength `300` | **202** `application/json` [UploadDocumentoResponse](openapi.json#/components/schemas/UploadDocumentoResponse) | 400, 401, 409, 413, 422, 429, 500, 503 |
| `DELETE /api/documents/{document_id}` | Bearer | path `document_id`: string obligatorio | **202** `application/json` [RetiradaDocumentoResponse](openapi.json#/components/schemas/RetiradaDocumentoResponse)<br>**204** sin cuerpo | 401, 404, 422, 500, 503 |
| `GET /api/documents/{document_id}` | Bearer | path `document_id`: string obligatorio | **200** `application/json` [DocumentoResponse](openapi.json#/components/schemas/DocumentoResponse) | 401, 404, 409, 422, 500, 503 |
| `GET /api/documents/{document_id}/sources/{chunk_id}` | Bearer | path `document_id`: string obligatorio<br>path `chunk_id`: string obligatorio | **200** `application/json` [FuenteDocumentoResponse](openapi.json#/components/schemas/FuenteDocumentoResponse) | 401, 404, 409, 422, 500, 503 |
| `GET /api/documents/{document_id}/sources/{chunk_id}/image` | Bearer | path `document_id`: string obligatorio<br>path `chunk_id`: string obligatorio | **200** `image/png` string (binary) | 401, 404, 409, 422, 500, 503 |
| `GET /api/exports/{generation_id}` | Bearer | path `generation_id`: string obligatorio<br>query `format`: string; default `"json"` | **200** `application/json` [PaqueteExportado](openapi.json#/components/schemas/PaqueteExportado); `application/octet-stream` string (binary); `application/pdf` string (binary); `text/csv` string; `text/markdown` string; `text/tab-separated-values` string | 401, 404, 409, 422, 500, 503 |
| `POST /api/generate` | Bearer | header `Idempotency-Key`: string / null<br>body `application/json` obligatorio: [GenerateRequest](openapi.json#/components/schemas/GenerateRequest) | **202** `application/json` [GenerationJobResponse](openapi.json#/components/schemas/GenerationJobResponse) | 400, 401, 404, 409, 422, 429, 500, 503 |
| `GET /api/generations` | Bearer | query `limit`: integer; default `20`; minimum `1`; maximum `100`<br>query `cursor`: string / null<br>query `documento`: string / null<br>query `perfil`: [RecipientProfile](openapi.json#/components/schemas/RecipientProfile) / null<br>query `formato`: [PedagogicalFormat](openapi.json#/components/schemas/PedagogicalFormat) / null<br>query `idioma`: [OutputLanguage](openapi.json#/components/schemas/OutputLanguage) / null<br>query `desde`: string (date-time) / null<br>query `hasta`: string (date-time) / null | **200** `application/json` [PaginaGeneraciones](openapi.json#/components/schemas/PaginaGeneraciones) | 400, 401, 409, 422, 500, 503 |
| `GET /api/generations/{generation_id}` | Bearer | path `generation_id`: string obligatorio | **200** `application/json` [GenerationJobResponse](openapi.json#/components/schemas/GenerationJobResponse) | 401, 404, 409, 422, 500, 503 |
| `POST /api/generations/{generation_id}/cancel` | Bearer | path `generation_id`: string obligatorio | **200** `application/json` [GenerationJobResponse](openapi.json#/components/schemas/GenerationJobResponse) | 401, 404, 409, 422, 500, 503 |
| `GET /api/generations/{generation_id}/events` | Bearer | path `generation_id`: string obligatorio<br>header `Last-Event-ID`: integer; default `0`; minimum `0`; maximum `9223372036854775807` | **200** `text/event-stream` string | 401, 404, 409, 422, 500, 503 |
| `POST /api/generations/{generation_id}/persist` | Bearer | path `generation_id`: string obligatorio<br>header `Idempotency-Key`: string / null | **202** `application/json` [GenerationJobResponse](openapi.json#/components/schemas/GenerationJobResponse) | 400, 401, 404, 409, 422, 429, 500, 503 |
| `POST /api/glossaries` | Bearer | header `Idempotency-Key`: string / null<br>body `application/json` obligatorio: [GlossaryRequest](openapi.json#/components/schemas/GlossaryRequest) | **200** `application/json` [GlossaryJobResponse](openapi.json#/components/schemas/GlossaryJobResponse)<br>**202** `application/json` [GlossaryJobResponse](openapi.json#/components/schemas/GlossaryJobResponse) | 400, 401, 404, 409, 422, 429, 500, 503 |
| `GET /api/glossaries/{glossary_id}` | Bearer | path `glossary_id`: string obligatorio | **200** `application/json` [GlossaryOutput](openapi.json#/components/schemas/GlossaryOutput) | 401, 404, 409, 422, 500, 503 |
| `GET /api/health` | Pública | Sin body/parámetros específicos | **200** `application/json` object | 500 |
| `GET /api/jobs/{job_id}` | Bearer | path `job_id`: string obligatorio | **200** `application/json` [TrabajoComunResponse](openapi.json#/components/schemas/TrabajoComunResponse) | 401, 404, 409, 422, 500, 503 |
| `POST /api/jobs/{job_id}/cancel` | Bearer | path `job_id`: string obligatorio | **200** `application/json` [TrabajoComunResponse](openapi.json#/components/schemas/TrabajoComunResponse) | 401, 404, 409, 422, 500 |
| `GET /api/jobs/{job_id}/events` | Bearer | path `job_id`: string obligatorio<br>header `Last-Event-ID`: integer; default `0`; minimum `0`; maximum `9223372036854775807` | **200** `text/event-stream` string | 401, 404, 409, 422, 500, 503 |
| `GET /api/progress` | Bearer | Sin body/parámetros específicos | **200** `application/json` [ProgressResponse](openapi.json#/components/schemas/ProgressResponse) | 401, 409, 500, 503 |
| `POST /api/progress/events` | Bearer | header `Idempotency-Key`: string / null<br>body `application/json` obligatorio: [ProgressEvent](openapi.json#/components/schemas/ProgressEvent) | **200** `application/json` [ProgressEventResponse](openapi.json#/components/schemas/ProgressEventResponse) | 400, 401, 404, 409, 422, 429, 500, 503 |
| `POST /api/quizzes/{generation_id}/answers` | Bearer | path `generation_id`: string obligatorio<br>header `Idempotency-Key`: string / null<br>body `application/json` obligatorio: [QuizAnswerRequest](openapi.json#/components/schemas/QuizAnswerRequest) | **200** `application/json` [QuizAnswerResponse](openapi.json#/components/schemas/QuizAnswerResponse) | 400, 401, 404, 409, 422, 429, 500, 503 |
| `DELETE /api/sessions/current` | Bearer | Sin body/parámetros específicos | **204** sin cuerpo | 401, 500 |
| `POST /api/sessions/recover` | Pública | body `application/json` obligatorio: [RecoverSessionRequest](openapi.json#/components/schemas/RecoverSessionRequest) | **201** `application/json` [SessionResponse](openapi.json#/components/schemas/SessionResponse) | 400, 401, 409, 422, 429, 500, 503 |
| `POST /api/workspaces` | Pública | Sin body/parámetros específicos | **201** `application/json` [WorkspaceCreatedResponse](openapi.json#/components/schemas/WorkspaceCreatedResponse) | 400, 429, 500, 503 |
| `DELETE /api/workspaces/current` | Bearer | Sin body/parámetros específicos | **204** sin cuerpo | 401, 500 |
| `GET /api/workspaces/current` | Bearer | Sin body/parámetros específicos | **200** `application/json` [EstadoEspacio](openapi.json#/components/schemas/EstadoEspacio) | 401, 500, 503 |
| `POST /api/workspaces/current/recovery-code` | Bearer | Sin body/parámetros específicos | **200** `application/json` [RotatedCodeResponse](openapi.json#/components/schemas/RotatedCodeResponse) | 401, 409, 500, 503 |
<!-- operations:end -->

### Precisiones por familia

- **Documentos:** upload JSON acepta texto plano; multipart admite un `file`
  PDF/MD/TXT y `titulo` opcional no vacío. Límites: PDF 20 MiB, MD/TXT 5 MiB,
  100 páginas y 100k tokens; los últimos se comprueban al procesar el job.
  Multipart permite 64 KiB adicionales de transporte y JSON se acota a
  `6 × 5 MiB + 64 KiB`. Campos repetidos/extra se rechazan. Listado ascendente
  por creación UTC e ID; cursor firmado por espacio, sin snapshot de paginación.
  Fuentes exigen documento ready. `source_type=image_description` es interpretación
  del modelo; la ruta `/image` devuelve PNG original rasterizado con
  `Cache-Control: no-store` y `X-Content-Type-Options: nosniff`.
  DELETE devuelve 204 si se verifica limpieza o 202 con `cleanup_pending=true`
  si quedan efectos físicos; repetir sobre retirado devuelve 404. Estas precisiones
  siguen pendientes de revisión contractual, como señala `contratos-api.md`.
- **Documentos demo:** catálogo compartido de sólo lectura, Bearer vigente sin renovar
  actividad; no admite `workspace_id` (422), no resuelve privados ni habilita
  generación demo. Cursor firmado; orden `document_id` ASCII ascendente.
- **Ejemplos demo de #55:** `/api/demo/generations` ofrece un catálogo finito
  de hasta doce ejemplos publicados previamente, sin paginación. Requiere Bearer
  vigente, no renueva actividad ni admite `workspace_id`. Catálogo ausente devuelve
  sólo `items=[]`; errores de integridad/storage siguen siendo 503. Las entradas
  `rejected_quality` sólo exponen diagnóstico y no permiten leer un paquete (404).
  La vista estudiante oculta soluciones iniciales del quiz; `/answers` devuelve
  feedback sin escribir progreso privado ni llamar al LLM. Usa `QuizAnswerRequest`,
  pero `event_id` no registra progreso ni se usa `Idempotency-Key`. Repetir la misma
  respuesta devuelve el mismo feedback. Las fuentes e imágenes provienen de
  snapshots verificados, sin fallback privado. `/image` entrega PNG con
  `Cache-Control: no-store`. `provider=mock` no acredita preparación real.
- **Generaciones:** todas las fuentes propias ready, con snapshot de admisión
  contrastado con su autorización actual. Cualquier fuente retirada impide servir
  la generación, sus materiales y sus exports. Historial descendente por creación e
  ID, cursor ligado a filtros; `desde`/`hasta` con zona horaria y `desde <= hasta`.
  Cancelación cooperativa: puede seguir `running` hasta que el worker la observe;
  repetir `cancelled` conserva estado, otro terminal produce 409. `/persist`
  reintenta el aprobado retenido hasta 24 h, mismo objeto y sin LLM; devuelve 202
  incluso si ya estaba completed. Perder el aprobado local no permite reconstruirlo.
- **Chat y glosario:** requieren todas las fuentes ready propias. Chat devuelve siempre 202,
  también para una intención repetida completada. Glosario devuelve 202 en curso
  o 200 para caché aprobada verificada, ambos `GlossaryJobResponse`, con
  `glossary_id = job_id`. Consultar un glosario aún no completado devuelve 409.
  Un resultado de chat se autoriza contra todas sus fuentes guardadas, también
  secundarias: un cambio o retirada bloquea su entrega por GET y su seguimiento
  SSE. El historial se liga al workspace y al conjunto de IDs/hashes/versiones:
  documentos distintos con igual hash no se mezclan. Los turnos unidocumentales
  históricos se reutilizan sólo si su resultado y vínculos acreditan la misma
  identidad/hash; no se reconstruye procedencia ausente ni se reescribe idempotencia.
- **Cupos de retención:** `WORKSPACE_MAX_DOCUMENTS` y `WORKSPACE_MAX_GENERATIONS`
  son configurables al iniciar; no cambian el máximo de cinco fuentes de una
  solicitud. Cada generación ocupa una plaza, aunque tenga varias relaciones.
  Retirarla directamente o retirar cualquiera de sus fuentes libera esa plaza
  antes de terminar la limpieza física, incluso si está pendiente o falla.
  Se conservan los vínculos de purga y el aislamiento del workspace; admisiones
  concurrentes compiten atómicamente por las plazas restantes. Al alcanzar el
  límite se devuelve 429 `RATE_LIMITED`, con `motivo=workspace_resource_limit`.
- **Quiz y progreso:** `question_id` y `option_id` se obtienen de la vista
  estudiante. La respuesta devuelve sólo el feedback de esa pregunta, calculado
  por el backend sin LLM. `concepto_revisado` exige documento/concepto;
  `flashcard_vista` exige generación/tarjeta. El cliente no envía puntajes ni
  `respuesta_quiz`. Agregados sin eventos empiezan en cero; tiempo restante es
  orientativo, no una medida de dominio.
- **Exports:** `json`, `md`, `pdf` admiten los cinco formatos pedagógicos;
  `csv`, `tsv`, `apkg` sólo flashcards. JSON conserva los bytes del canónico.
  Markdown/CSV/TSV son UTF-8. Descargas usan `Content-Disposition: attachment`
  con `{generation_id}.{format}`, `Cache-Control: private, no-store` y `nosniff`.
  Ajeno devuelve 404, no aprobado 409, formato desconocido/incompatible 422.
  No hay redirección a un URL público ni una llamada LLM para descargar.

## Transporte SSE

Las URLs son `/api/jobs/{job_id}/events` para ingestión/chat/glosario y
`/api/generations/{generation_id}/events` para generaciones. Se toman de
`events_url`, con Bearer también en reconexión. HTTP 200 usa `text/event-stream`,
`Cache-Control: no-cache` y `X-Accel-Buffering: no`.

Encuadre **ilustrativo** (no lista JSON ni ID ejecutable):

```text
id: 42
event: progress
data: {"generation_id":"identidad-ilustrativa","step":"critic","status":"running","iteration":2}

: heartbeat

```

En jobs comunes, `data` contiene `job_id` en lugar de `generation_id`. `id` es
entero monotónico fuera de `data`; `iteration` puede ser null. Un comentario
`: heartbeat` y una línea vacía mantienen el stream activo (comprobación cada
15 s mientras siga activo). No transmite contenido aprobado ni resultados.
`Last-Event-ID` acepta enteros 0..9223372036854775807, default 0; entrega sólo
eventos posteriores mientras exista el registro. Reconectar no crea otra ejecución.
Desconectar no cancela; el cierre del stream tampoco prueba éxito: consultar estado.

Antes de headers, los errores son HTTP con `ErrorResponse`. Después de headers,
un error de acceso/storage manejado envía `event: error` y
`data: <ErrorResponse>`, sin `id`, y cierra manteniendo HTTP 200. El recorrido
detecta ese evento y aborta; siempre comprueba después estado y contenido.
La autorización incluye las fuentes vinculadas y las versiones guardadas del
resultado del chat; una secundaria retirada o modificada no queda autorizada por
la vigencia del primario. Antes de headers se deniega por HTTP; después se cierra
con `event: error`. La respuesta educativa se obtiene sólo por consulta autorizada.

## Recorridos curl ejecutables

Prerrequisitos: **Bash** (en Windows, Git Bash), **curl**, **jq 1.6 o posterior**,
`awk`, `sed`, `grep`, `head`, `tail`, `cut`, `tr`, `mktemp`, `mv`, `mkdir`, `chmod`, `sleep` y
**unzip** para comprobar APKG. Son herramientas reales; no se reemplaza jq por
una implementación Python. jq se obtiene de su [distribución oficial](https://jqlang.org/download/).
Definir en el entorno `BASE_URL` con el origen autorizado, sin `/api` al final,
y `INPUT_FILE` con un archivo **TXT UTF-8 propio no vacío** sobre el tema a adaptar.
En esta etapa ejecutar sólo contra el backend local con proveedores configurados.
El harness asigna un origen loopback efímero y usa la fixture `txt_ejemplo` de VCN.

En Windows ejecutar el verificador desde Git Bash, con las herramientas anteriores
en `PATH`. Si se invoca desde PowerShell y `bash` resuelve al lanzador WSL sin
distribución instalada, seleccionar Git Bash explícitamente para esa terminal:

```powershell
$env:NM_API_REFERENCE_BASH = "C:\Program Files\Git\bin\bash.exe"
$env:PATH = "C:\Program Files\Git\usr\bin;C:\Program Files\Git\mingw64\bin;$env:PATH"
```

jq debe estar instalado y disponible en ese mismo `PATH`; no se instala ni se
descarga automáticamente al ejecutar la verificación.

Ejecutar los siete bloques **en orden en la misma sesión Bash**. El primero
crea un directorio privado para respuestas, claves de intención y descargas.
Las variables de credenciales/IDs se extraen de respuestas, nunca se copian de
un ejemplo ficticio. No activar `set -x`, compartir `workspace.json` ni versionar
el directorio de salida. Conservarlo si una escritura queda indeterminada.

### 1. Configuración y comprobaciones comunes

```bash
# api-example: setup
set -euo pipefail
umask 077
: "${BASE_URL:?Definí BASE_URL con el origen local autorizado}"
: "${INPUT_FILE:?Definí INPUT_FILE con un TXT UTF-8 propio}"
BASE_URL="${BASE_URL%/}"
for tool in curl jq awk sed grep head tail cut tr mktemp mv mkdir chmod sleep unzip; do
  command -v "$tool" >/dev/null || { echo "Falta herramienta: $tool" >&2; exit 1; }
done
test -s "$INPUT_FILE"
# curl nativo de Windows necesita una ruta C:/... dentro de file=@...;
# MSYS no convierte automáticamente ese argumento multipart compuesto.
FORM_FILE="$INPUT_FILE"
if command -v cygpath >/dev/null; then FORM_FILE="$(cygpath -m "$INPUT_FILE")"; fi
RUN_DIR="${RUN_DIR:-$(mktemp -d)}"
mkdir -p "$RUN_DIR"
chmod 700 "$RUN_DIR"
WAIT_SECONDS=360
HTTP_SECONDS=30
TOKEN=''
content_type() {
  awk 'tolower($1)=="content-type:" {gsub("\r", ""); print tolower($2)}' "$1" | tail -n 1 | cut -d ';' -f 1
}
request() {
  local expected="$1" method="$2" path="$3" name="$4"
  shift 4
  local auth=()
  if [[ -n "$TOKEN" ]]; then auth=(-H "Authorization: Bearer $TOKEN"); fi
  HTTP=$(curl -q -sS --connect-timeout 5 --max-time "$HTTP_SECONDS" \
    -X "$method" "${BASE_URL}${path}" "${auth[@]}" \
    -H 'Accept-Language: es' -H 'X-Request-ID: referencia-59' \
    -D "$RUN_DIR/$name.headers" -o "$RUN_DIR/$name.part" -w '%{http_code}' "$@") || {
      echo "Fallo de transporte: $name; no se reintentó la escritura" >&2; return 1;
    }
  case "|$expected|" in *"|$HTTP|"*) ;; *) echo "$name: HTTP $HTTP inesperado" >&2; return 1 ;; esac
  grep -iq '^x-request-id:' "$RUN_DIR/$name.headers"
  printf 'OK %s HTTP %s\n' "$name" "$HTTP"
}
json_request() {
  local name="$4"
  request "$@"
  [[ "$(content_type "$RUN_DIR/$name.headers")" == application/json ]]
  jq -e 'type == "object"' "$RUN_DIR/$name.part" >/dev/null
  mv "$RUN_DIR/$name.part" "$RUN_DIR/$name.json"
}
wait_job() {
  local path="$1" name="$2" deadline=$((SECONDS + WAIT_SECONDS)) state
  while (( SECONDS < deadline )); do
    json_request 200 GET "$path" "$name"
    state=$(jq -er '.status' "$RUN_DIR/$name.json")
    case "$state" in
      completed) return 0 ;;
      failed|rejected_quality|cancelled) echo "$name terminó en $state" >&2; return 1 ;;
      queued|running) sleep 1 ;;
      *) echo "$name: estado desconocido $state" >&2; return 1 ;;
    esac
  done
  echo "$name: espera agotada; consultar la misma status_url" >&2
  return 1
}
stream() {
  local path="$1" name="$2" identity="$3" cursor="${4:-0}"
  HTTP=$(curl -q -N -sS --connect-timeout 5 --max-time "$WAIT_SECONDS" \
    -H "Authorization: Bearer $TOKEN" -H "Last-Event-ID: $cursor" \
    -H 'X-Request-ID: referencia-59' -D "$RUN_DIR/$name.headers" \
    -o "$RUN_DIR/$name.sse" -w '%{http_code}' "$BASE_URL$path")
  [[ "$HTTP" == 200 && "$(content_type "$RUN_DIR/$name.headers")" == text/event-stream ]]
  if grep -q '^event: error' "$RUN_DIR/$name.sse"; then
    echo "$name: error dentro del stream HTTP 200" >&2; return 1
  fi
  awk -v cursor="$cursor" '$1=="id:" {if ($2<=cursor) exit 1; cursor=$2}' "$RUN_DIR/$name.sse"
  sed -n 's/^data: //p' "$RUN_DIR/$name.sse" | jq -es --arg field "$identity" \
    'length>0 and all(.[]; has($field) and has("step") and has("status") and has("iteration") and (has("id") | not))' >/dev/null
  printf 'OK %s SSE\n' "$name"
}
json_request 200 GET /api/health health
```

`curl -q` evita cargar `.curlrc`, incluidos reintentos configurados allí.
Las esperas son finitas: cada petición JSON/descarga, como máximo 30 s; cada
stream, 360 s; el polling termina al llegar a su plazo (una petición en curso
puede consumir hasta 30 s adicionales). Agotar el plazo no cancela el trabajo.

### 2. Crear y usar espacio

```bash
# api-example: workspace
json_request 201 POST /api/workspaces workspace
TOKEN=$(jq -er '.token | select(type=="string" and length>0)' "$RUN_DIR/workspace.json")
WORKSPACE_ID=$(jq -er '.workspace_id' "$RUN_DIR/workspace.json")
RECOVERY_CODE=$(jq -er '.recovery_code' "$RUN_DIR/workspace.json")
jq -n --arg code "$RECOVERY_CODE" '{recovery_code:$code}' > "$RUN_DIR/recover-request.json"
json_request 201 POST /api/sessions/recover recovered \
  -H 'Content-Type: application/json' --data-binary "@$RUN_DIR/recover-request.json"
jq -e --arg ws "$WORKSPACE_ID" '.workspace_id==$ws and (has("recovery_code") | not)' "$RUN_DIR/recovered.json" >/dev/null
TOKEN=$(jq -er '.token' "$RUN_DIR/recovered.json")
json_request 200 GET /api/workspaces/current workspace-current
```

### 3. Subir texto, seguir el job común y comprobar ready

```bash
# api-example: upload
UPLOAD_KEY="upload-$WORKSPACE_ID"
jq -n --rawfile text "$INPUT_FILE" \
  '{documento_titulo:"Redes VCN",documento_contenido:$text}' > "$RUN_DIR/upload-request.json"
json_request 202 POST /api/documents/upload upload -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $UPLOAD_KEY" --data-binary "@$RUN_DIR/upload-request.json"
DOCUMENT_ID=$(jq -er '.document_id' "$RUN_DIR/upload.json")
JOB_STATUS_URL=$(jq -er '.status_url' "$RUN_DIR/upload.json")
JOB_EVENTS_URL=$(jq -er '.events_url' "$RUN_DIR/upload.json")
stream "$JOB_EVENTS_URL" upload-events job_id
wait_job "$JOB_STATUS_URL" upload-status
json_request 200 GET "/api/documents/$DOCUMENT_ID" document
jq -e '.status=="ready"' "$RUN_DIR/document.json" >/dev/null
json_request 200 GET '/api/documents?limit=20' documents
```

Si el documento no está ready, el bloque aborta aunque el job esté completed.
Esto evita generar sobre una fuente todavía pendiente de visión.

Respuesta 202 **ilustrativa** de upload, con identidades ficticias sólo para
mostrar la forma (no pegar estas URLs en curl; el bloque obtiene las reales):

```json
{"document_id":"doc_ilustrativo","status":"processing","job_id":"job_ilustrativo","status_url":"/api/jobs/job_ilustrativo","events_url":"/api/jobs/job_ilustrativo/events","cancel_url":"/api/jobs/job_ilustrativo/cancel"}
```

### 4. Generar quiz, SSE y reconexión; responder con IDs reales

```bash
# api-example: quiz
QUIZ_KEY="quiz-$WORKSPACE_ID"
jq -n --arg doc "$DOCUMENT_ID" '{document_id:$doc,perfil_destinatario:"principiante",
  formato_salida:"quiz",nicho_sector:"general",nivel_detalle:"didactico",idioma_salida:"es"}' > "$RUN_DIR/quiz-request.json"
json_request 202 POST /api/generate quiz -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $QUIZ_KEY" --data-binary "@$RUN_DIR/quiz-request.json"
QUIZ_ID=$(jq -er '.generation_id' "$RUN_DIR/quiz.json")
QUIZ_STATUS_URL=$(jq -er '.status_url' "$RUN_DIR/quiz.json")
QUIZ_EVENTS_URL=$(jq -er '.events_url' "$RUN_DIR/quiz.json")
stream "$QUIZ_EVENTS_URL" quiz-events generation_id
CURSOR=$(awk '$1=="id:" {gsub("\r", ""); print $2; exit}' "$RUN_DIR/quiz-events.sse")
[[ "$CURSOR" =~ ^[0-9]+$ ]]
stream "$QUIZ_EVENTS_URL" quiz-reconnect generation_id "$CURSOR"
wait_job "$QUIZ_STATUS_URL" quiz-status
jq -e '.status=="completed" and .persistencia.status_upload=="completado" and
  .contenido.contenido_adaptado.tipo=="quiz" and (.contenido.contenido_adaptado.preguntas | length>0)' "$RUN_DIR/quiz-status.json" >/dev/null
QUESTION_ID=$(jq -er '.contenido.contenido_adaptado.preguntas[0].id' "$RUN_DIR/quiz-status.json")
OPTION_ID=$(jq -er '.contenido.contenido_adaptado.preguntas[0].opciones[0].option_id' "$RUN_DIR/quiz-status.json")
EVENT_ID="answer-$QUIZ_ID-$QUESTION_ID"
jq -n --arg question "$QUESTION_ID" --arg option "$OPTION_ID" --arg event "$EVENT_ID" \
  '{question_id:$question,option_id:$option,event_id:$event}' > "$RUN_DIR/answer-request.json"
json_request 200 POST "/api/quizzes/$QUIZ_ID/answers" answer -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $EVENT_ID" --data-binary "@$RUN_DIR/answer-request.json"
jq -e '(.correcta | type)=="boolean" and (.correct_option_id | type)=="string" and (.referencias | length>0)' "$RUN_DIR/answer.json" >/dev/null
json_request 200 GET /api/progress progress
```

Elegir la primera opción no promete acierto; `correcta` la calcula el backend.
La reconexión reproduce sólo eventos posteriores al primer cursor recibido,
sin iniciar otra generación. Un SSE sin error tampoco sustituye el GET final.

### 5. Descargar canónico, Markdown y PDF del quiz

```bash
# api-example: exports
download() {
  local gid="$1" format="$2" mime="$3" name="$4"
  request 200 GET "/api/exports/$gid?format=$format" "$name"
  [[ "$(content_type "$RUN_DIR/$name.headers")" == "$mime" ]]
  grep -iq '^content-disposition: attachment;' "$RUN_DIR/$name.headers"
  test -s "$RUN_DIR/$name.part"
  case "$format" in
    json) jq -e --arg gid "$gid" '.generation_id==$gid and .status=="aprobado" and .schema_version=="1.0"' "$RUN_DIR/$name.part" >/dev/null ;;
    md) grep -q '^# ' "$RUN_DIR/$name.part" ;;
    pdf) [[ "$(head -c 5 "$RUN_DIR/$name.part")" == '%PDF-' ]] ;;
    csv) grep -q '^#separator:Comma' "$RUN_DIR/$name.part"; grep -q '^"<span>' "$RUN_DIR/$name.part" ;;
    tsv) grep -q '^#separator:Tab' "$RUN_DIR/$name.part"; grep -q '^"<span>' "$RUN_DIR/$name.part" ;;
    apkg) unzip -t "$RUN_DIR/$name.part" >/dev/null; unzip -Z1 "$RUN_DIR/$name.part" | grep -qx 'collection.anki2' ;;
    *) return 1 ;;
  esac
  mv "$RUN_DIR/$name.part" "$RUN_DIR/$name.$format"
}
download "$QUIZ_ID" json application/json quiz-canonical
download "$QUIZ_ID" md text/markdown quiz-markdown
download "$QUIZ_ID" pdf application/pdf quiz-pdf
jq -e '.contenido_adaptado.preguntas[0] | has("correct_option_id") and has("justificacion")' "$RUN_DIR/quiz-canonical.json" >/dev/null
```

Sólo después de validar se renombra `.part` a la extensión final. Una respuesta
HTTP de error no se guarda como PDF/APKG válido. CSV no es compatible con quiz.

### 6. Flashcards para CSV, TSV y APKG

```bash
# api-example: flashcards
FLASHCARDS_KEY="flashcards-$WORKSPACE_ID"
jq '.formato_salida="flashcards"' "$RUN_DIR/quiz-request.json" > "$RUN_DIR/flashcards-request.json"
json_request 202 POST /api/generate flashcards -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $FLASHCARDS_KEY" --data-binary "@$RUN_DIR/flashcards-request.json"
FLASHCARDS_ID=$(jq -er '.generation_id' "$RUN_DIR/flashcards.json")
FLASHCARDS_STATUS_URL=$(jq -er '.status_url' "$RUN_DIR/flashcards.json")
wait_job "$FLASHCARDS_STATUS_URL" flashcards-status
jq -e '.contenido.contenido_adaptado.tipo=="flashcards" and
  .persistencia.status_upload=="completado" and (.contenido.contenido_adaptado.items | length>0)' "$RUN_DIR/flashcards-status.json" >/dev/null
download "$FLASHCARDS_ID" csv text/csv flashcards-csv
download "$FLASHCARDS_ID" tsv text/tab-separated-values flashcards-tsv
download "$FLASHCARDS_ID" apkg application/octet-stream flashcards-apkg
```

CSV/TSV contienen directivas de importación Anki, campos HTML escapados,
etiquetas, GUID y mazo. APKG es un ZIP con colección y media; esta comprobación
no acredita importación en Anki Desktop, cubierta separadamente por #45.

### 7. Variante multipart del documento

```bash
# api-example: multipart
MULTIPART_KEY="multipart-$WORKSPACE_ID"
json_request 202 POST /api/documents/upload multipart \
  -H "Idempotency-Key: $MULTIPART_KEY" -F "file=@\"$FORM_FILE\";type=text/plain" -F 'titulo=Redes VCN multipart'
MULTIPART_DOCUMENT_ID=$(jq -er '.document_id' "$RUN_DIR/multipart.json")
MULTIPART_STATUS_URL=$(jq -er '.status_url' "$RUN_DIR/multipart.json")
wait_job "$MULTIPART_STATUS_URL" multipart-status
json_request 200 GET "/api/documents/$MULTIPART_DOCUMENT_ID" multipart-document
jq -e '.status=="ready"' "$RUN_DIR/multipart-document.json" >/dev/null
printf 'Recorridos completados; respuestas y descargas privadas en %s\n' "$RUN_DIR"
```

El mismo contenido puede reutilizar documento/job por deduplicación: no se
presupone que multipart produzca otra identidad. Para PDF o MD propios cambiar
archivo y tipo de la parte según su contenido real; esa variante no forma parte
del recorrido TXT comprobado aquí.

## Evidencia y alcance de la comprobación

Ver [registro de revisión integrada](verificacion-issue-59.md) y su captura del snapshot vigente.

La actualización documental de la PR #146 contrasta los ejemplos JSON nuevos con
los esquemas vigentes y revisa enlaces/huellas sin llamadas externas. No vuelve a
ejecutar los siete recorridos curl ni el viewer. Los resultados, fechas y huellas
de esas ejecuciones conservados debajo son registros históricos, no mediciones de
la entrega multidocumental. La evidencia D real sigue pendiente; ver
[bytes históricos y cobertura con dobles](evidencia/multidocumental-d.md).

`verify_api_reference.py --check` contrasta las 37 filas (método, ruta,
parámetros, modelos, HTTP y tipos de contenido), enlaces de schemas y los siete
bloques ordenados. `--local` extrae sus cuerpos literalmente y los ejecuta en una
sola sesión Bash contra la app real por TCP, reutilizando la fixture de #31/#52
con Chroma, SQLite, StateGraph y storage local. No reimplementa las peticiones en
Python ni consulta un servicio público. Los datos de entrada y las respuestas
educativas provienen de fixtures/dobles, no acreditan calidad pedagógica real,
OCI, Gemini ni disponibilidad de producción.

La fixture prepara además su propio espacio/documento como parte de su setup;
no se cuentan como peticiones curl. Los bloques crean su propio espacio y suben
la entrada por curl, obteniendo sus nuevas credenciales e identidades por HTTP.

Comprobación local de esta etapa (2026-10-15): los siete bloques se ejecutaron
por TCP con Bash 5.2.37, curl 8.18.0 y jq 1.8.2. Se verificaron ambos SSE,
reconexión, documento ready, quiz, feedback, progreso, JSON/MD/PDF,
CSV/TSV/APKG y multipart. Un fallo inicial de ruta multipart MSYS para curl
nativo Windows se corrigió en el bloque publicado, sin alterar el runtime.

Viewer: **Swagger UI 5.33.2**, servido localmente con assets descargados y una
copia byte a byte del archivo versionado, no `/docs`. Tras sincronizar con #55,
el navegador mostró `NuevaMente API 0.1.0`, `OAS 3.1` y las **37 operaciones**,
incluidas las cinco de ejemplos demo, sin errores de carga ni de resolución.
SHA-256 del snapshot visualizado:
`d8f388b4e55b1ccb129ca4c3563aadc0c1fabf2be30969a20880044c84badca6`.

Los ejemplos de error y SSE anteriores son ilustrativos; los resultados reales
se comprueban durante la ejecución y las credenciales no se publican. La
referencia cubre también operaciones que no se ejecutan en estos recorridos,
como rotación/borrado, cancelación, demo, chat y glosario; siguen cubiertas por sus
tests de API existentes. No se atribuye ejecución curl a esas filas.

La sincronización con `develop` `7fd1d5afe59fc557d48935a32f283f1c0844ee02`
incorpora las cinco operaciones de ejemplos demo de #55; el snapshot actual
contiene 37 operaciones. La tanda afectada aprobó 73 tests: 29 de #59,
cinco de ejemplos demo y 39 de schemas/health, incluidos los siete bloques
curl literales. No se repitió la suite general tras esta sincronización.

### Criterios vigentes de #59 y seguimiento público

El [issue #59](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/59)
exige validar el snapshot en un viewer estándar y ejecutar literalmente los
ejemplos contra una instancia local de la API integrada, con URL base configurable
y requisitos explícitos. Las comprobaciones anteriores acreditan esos criterios
con proveedores simulados; no acreditan integración con OCI/Gemini reales.

La comprobación de los mismos ejemplos contra la URL HTTPS de entrega se trasladó
al [seguimiento #133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133).
Sigue pendiente y es obligatoria para aceptar el despliegue; no bloquea el cierre
local de #59. El viewer acredita carga del snapshot, no ejecución de la API.

### Regresión general y límites

La suite ordinaria completa ejecutada en Windows el 2026-10-15 terminó con
**2574 aprobados, 8 fallos, 52 omisiones y 10 excluidos**. No se declara aprobada:
dos fallos provinieron de resolver recursos Markdown desde un cwd vacío; los
seis restantes fueron timeouts de generación. Una comparación posterior desde
copias aisladas con cwd en sus raíces aprobó los dos casos Markdown en ambas
versiones, sin modificar el parser ni los tests. Los otros casos seleccionados
que pasaron en esa comparación no acreditan la suite completa.

El timeout de `test_historial_keyset_y_filtros` se reprodujo tanto en `develop`
(`c095db267d5a3468cf187fb87f1c5528225d6b33`) como en el working tree de #59.
Esto descarta que sea exclusivo del diff, sin determinar su causa ni la de los
otros cinco timeouts de la corrida completa. Las 52 omisiones corresponden a
Caddy (7) y docker-compose (45) no disponibles: son cobertura no ejecutada.
La CI de la PR y la revisión del equipo siguen siendo requisitos de integración.

Quedan separadas las precisiones contractuales pendientes y la cobertura de
escenarios de las 37 operaciones: los curl locales sólo acreditan los recorridos
indicados con dobles. No se alteraron timeouts, expectativas ni skips para
obtener estos resultados.
