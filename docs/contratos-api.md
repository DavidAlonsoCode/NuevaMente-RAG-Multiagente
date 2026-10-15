# 🔌 Contratos API v1 — NuevaMente

> **Estado**: contrato v1 **CONGELADO** por el issue `Issue 03`. Implementación viva: [`backend/app/schemas/`](../backend/app/schemas/) (Pydantic v2); ejemplos ejecutables: `backend/tests/test_schemas.py` (round-trip + rechazo de inválidos). Cambios posteriores requieren PR etiquetado `contract-change` con revisión API+UI+AGT+RAG y este documento actualizado en el mismo PR.
> **Fuente**: `decisiones_proyecto.md` §3.3, §7, §16, §17. La referencia navegable por HTTP será el OpenAPI en `/docs` (issue `Issue 59`).
> **Uso**: backend implementa contra este documento; frontend construye contra este documento. Ambos lo tratan como la interfaz compartida.

---

## Convenciones generales

- **Base**: `/api` (sin `/v1`; el versionado vive en `schema_version=1.0` de los paquetes).
- **Autenticación**: `Authorization: Bearer <token_de_sesion>` en todas las rutas salvo creación de workspace, recuperación y `/health`.
- **Autorización**: todo recurso se resuelve desde la sesión validada; un ID ajeno responde **404** (no 403).
- **Idempotencia**: `POST` de upload, generación, persist y eventos de progreso aceptan header `Idempotency-Key`. Duplicado en curso → misma respuesta; mismo key con otro cuerpo → **409**.
- **Idioma de UI**: Accept-Language es/en/pt (default es) controla mensajes; idioma_salida controla contenido.
- **Trazabilidad**: toda respuesta incluye cabecera X-Request-ID; los errores JSON también incluyen request_id. No modificar cuerpos binarios de exportación ni el artefacto canónico para añadirlo.

### Envolvente de error (única para toda la API)

```json
{
  "error": {
    "code": "DOCUMENT_TOO_LARGE",
    "message": "El archivo supera el límite de 20 MB para PDF.",
    "details": { "limite_mb": 20, "recibido_mb": 25.4 }
  },
  "request_id": "req_8f14e45f"
}
```

| HTTP | code (ejemplos estables) | Significado |
|---|---|---|
| 400 | `INVALID_REQUEST` | Petición mal formada |
| 401 | `SESSION_INVALID` | Token/código inválido o sesión vencida |
| 404 | `NOT_FOUND` | Recurso inexistente **o ajeno** |
| 409 | `IDEMPOTENCY_CONFLICT` / `INVALID_STATE` | Estado incompatible o key reutilizada con otro cuerpo |
| 413 | `DOCUMENT_TOO_LARGE` | Archivo o contenido sobredimensionado |
| 422 | `EXPORT_INCOMPATIBLE` / `VALIDATION_ERROR` | Parámetros o combinación inválida |
| 429 | `QUEUE_FULL` / `RATE_LIMITED` / `RECOVERY_LOCKED` | Límite de uso o cola completa |
| 500 | `INTERNAL` | Error interno sin detalles sensibles |
| 503 | `STORAGE_UNAVAILABLE` / `PROVIDER_UNAVAILABLE` | Dependencia no disponible |

Los `code` son estables y no se traducen; los `message` se localizan según idioma de UI.

### Enums (valores de máquina estables)

| Enum | Valores |
|---|---|
| `perfil_destinatario` | `principiante` · `junior_ssr` · `lider_tecnico` · `ejecutivo` |
| `formato_salida` | `tutorial` · `flashcards` · `quiz` · `resumen_ejecutivo` · `guion_clase` |
| `nicho_sector` | `fintech` · `salud` · `ecommerce` · `general` |
| `nivel_detalle` | `didactico` · `practico` · `tecnico_profundo` |
| `idioma_salida` | `es` · `en` · `pt` |
| `status` (trabajo) | `queued` · `running` · `completed` · `rejected_quality` · `failed` · `cancelled` |
| `status` (documento) | `processing` · `ready` · `failed` |

> Las etiquetas visibles (p. ej. «Principiante / Transición de Carrera») viven en los catálogos i18n, no en el contrato.

---

## Rutas

### Espacios y sesiones (issues `Issue 09`, `Issue 16`)

| Método y ruta | Descripción |
|---|---|
| `POST /api/workspaces` | Crea espacio anónimo. **Devuelve una sola vez** `workspace_id`, `recovery_code` (≥128 bits, agrupado) y `token`. Expiración a 30 días de inactividad. |
| `POST /api/sessions/recover` | Canjea `recovery_code` por `token` nuevo. Máx. 5 intentos fallidos/min por origen. |
| `DELETE /api/sessions/current` | Cierra y revoca la sesión actual. |
| `POST /api/workspaces/current/recovery-code` | Rota el código (revoca sesiones previas) y devuelve el código nuevo una sola vez, junto con un token nuevo. |
| `DELETE /api/workspaces/current` | Bloquea el acceso y agenda el borrado físico de recursos. |

#### Precisiones de acceso, actividad y reconstrucción

- La recuperación reserva como máximo cinco plazas por origen, contando fallos del último minuto e intentos en vuelo. Un código válido no borra los fallos previos del minuto. Los fallos técnicos liberan su plaza; un conflicto de versión devuelve `409 INVALID_STATE`. Un bloqueo devuelve `429 RECOVERY_LOCKED` con `Retry-After`.
- La actividad autenticada renueva la retención del espacio con escrituras agrupadas cada cinco minutos. No prolonga el token. Los manifiestos pendientes se sincronizan al arrancar y cada minuto; la confirmación de una versión vieja no borra una actividad posterior.
- El manifiesto privado conserva hash, versión, expiración, última actividad y lápidas. Las escrituras usan creación exclusiva o `if_match`; un manifiesto retirado no se reactiva. Si OCI falla al borrar, el acceso local queda bloqueado y la confirmación durable permanece pendiente, con diagnóstico y reintento. Se debe preservar SQLite hasta confirmar esa lápida: un borrado que nunca llegó a storage no puede reconstruirse después de perder también el volumen local.
- `session/recovery.py` reconstruye el índice hash→espacio al arrancar un registro nuevo y retoma reconstrucciones interrumpidas. Pagina manifiestos, verifica identidad/ETag/hash y restaura documentos, registros de paquetes canónicos aprobados y el agregado privado de progreso; omite recursos retirados. No restaura tokens, colas, borradores ni invoca Gemini. Los documentos quedan `processing` hasta reconstruir índices (#17) y, cuando corresponda, visión (#30). La API de progreso corresponde a #35, su presentación a #41 y la gestión visual del espacio a #42.
- `Accept-Language` selecciona mensajes ES/EN/PT; los códigos, detalles e idioma del contenido conservan su contrato. El cliente Streamlit envía el idioma de interfaz vigente.

**Origen detrás de Streamlit/Caddy**: producción requiere `TRUSTED_ORIGIN_SECRET`, aleatorio, de al menos 32 caracteres, compartido solamente por Caddy y backend. El proxy sobrescribe `X-NuevaMente-Client-IP` con la IP de conexión y `X-NuevaMente-Origin-Key` con esa clave. Streamlit transmite ambas cabeceras al backend desde su contexto del servidor. La API autentica la clave antes de usar el origen; rechaza cabeceras incompletas/falsificadas e ignora `X-Forwarded-For` aportado por el cliente. Sin esas cabeceras usa la IP de conexión directa (desarrollo/API directa).

En cada `reverse_proxy` de UI y API del despliegue #49, incorporar:

```caddyfile
header_up X-NuevaMente-Client-IP {remote_host}
header_up X-NuevaMente-Origin-Key {env.TRUSTED_ORIGIN_SECRET}
header_down -X-NuevaMente-Origin-Key
```

Esto supone Caddy como entrada pública directa, según la arquitectura vigente. Si se incorpora otro proxy anterior, su cadena de confianza debe definirse explícitamente. La clave nunca se envía al navegador ni se registra. Referencias: [cabeceras de Caddy](https://caddyserver.com/docs/caddyfile/directives/reverse_proxy#headers) y [placeholders](https://caddyserver.com/docs/caddyfile/concepts#placeholders). La configuración documentada debe verificarse en el despliegue real; las pruebas locales no acreditan OCI/Caddy públicos.

### Documentos (issues `Issue 19`, `Issue 11`)

| Método y ruta | Descripción |
|---|---|
| `POST /api/documents/upload` | multipart/form-data (campo file y título opcional) o application/json (`documento_titulo` + `documento_contenido` → TXT). Valida firma real y límites (20 MB PDF · 5 MB MD/TXT · 100 págs · 100k tokens). Responde **202** `{document_id, status: "processing"}`. |
| `GET /api/documents` | Lista paginada de documentos del espacio. |
| `GET /api/documents/{id}` | Estado + metadatos + **cobertura** (páginas/secciones procesadas y omitidas). |
| `DELETE /api/documents/{id}` | Borra original, índice, derivados, chat y progreso vinculados. |
| `GET /api/documents/{id}/sources/{chunk_id}` | Fragmento/página original autorizado (base de «Ver la fuente»; imágenes para diagramas). |

#### Issue 19 — precisiones HTTP propuestas bajo dirección del equipo (etapa 4)

**Estado: implementadas localmente para revisión; requieren `contract-change`
con revisión API+UI+AGT+RAG antes de su aprobación como contrato congelado.**
La autorización de implementar no sustituye esa revisión. Rutas, autenticación,
límites, estados, envolvente de errores e idempotencia ya estaban determinados
arriba. Las formas completas, nulabilidad, cursor y traducción de incertidumbre
son precisiones nuevas. No se añade `WORKSPACE_BUSY` al enum vigente.

- Upload JSON conserva `documento_titulo` / `documento_contenido`. Multipart
  propone un único archivo `file` y campo opcional `titulo` (1..300 caracteres
  con texto). Campos/archivos extra o repetidos: 422 `VALIDATION_ERROR`.
  MIME no acredita formato; el parser valida firma y contenido. El nombre es
  etiqueta, nunca ruta ni clave de storage. El límite del archivo es 20 MiB PDF
  o 5 MiB MD/TXT; transporte acotado a 20 MiB + 64 KiB multipart y
  6 × 5 MiB + 64 KiB JSON para escapes y estructura. Exceso: 413, sin truncar.
  El conteo de páginas/tokens y parseo completo ocurren en el job: un rechazo
  posterior se informa por sus endpoints, no convierte el 202 previo en éxito
  del procesamiento. El 202 acredita original, manifiesto, commit de admisión
  SQLite y evidencia final OCI; nunca basta una reserva.
- Respuesta 202: `{document_id, status: "processing", job_id, status_url,
  events_url, cancel_url}`. URLs de `/api/jobs/{job_id}`, `/events` y `/cancel`;
  no se duplica la API de progreso. Reintento confirmado devuelve el mismo
  cuerpo, incluso si el trabajo ya terminó. IDs distintos pueden compartir
  contenido; solo reutilizan embeddings compatibles dentro del mismo workspace.
- Listado: `{items: [DocumentoResponse], next_cursor: string | null}`;
  `limit=20`, rango 1..100. Orden **ascendente por fecha de creación UTC e ID**.
  Cursor opaco firmado y ligado al workspace; 400 `INVALID_REQUEST` si inválido,
  alterado o de otro espacio. No requiere que exista su documento ancla.
  Se filtran lápidas y admisiones incompletas antes de contar elementos; el
  lookahead no se consume. Retiradas concurrentes pueden acortar una página
  con cursor válido. Es keyset, no snapshot: elementos creados después del
  ancla pueden aparecer; cambios posteriores no resucitan recursos filtrados.
- Detalle/listado: `{document_id, titulo, status, metadata, cobertura, error}`,
  mediante schemas públicos. `metadata` contiene `extension`, `tamano_bytes`,
  `creado_en`, `cantidad_paginas` y `cantidad_tokens`; extracción desconocida
  usa null, nunca cero inventado. Metadata legacy ausente: null.
  `cobertura` desconocida: null; conocida contiene `paginas_procesadas`,
  `paginas_omitidas`, `paginas_visuales_pendientes`, `secciones_procesadas`
  (ID, título y líneas), `secciones_omitidas`, `secciones_visuales_pendientes`.
  No se conocen los IDs/títulos de secciones visuales: sus inventarios son
  null mientras haya páginas pendientes; [] solo si se conoce su ausencia.
  `error` es null salvo failed, donde lleva `{code: null, message: diagnóstico
  seguro}`; el diagnóstico terminal detallado se consulta en el job existente.
  No expone filas, hash/configuración interna del parser, tokens, claves OCI
  ni caché de idempotencia. Visión pendiente: job textual completed, documento
  processing; cantidad_tokens puede ser cero real y la cobertura se conserva
  cuando no está configurada visión. #30 completa interpretación y cobertura
  antes de ready; una omisión termina failed y se informa en cobertura.
- Fuentes: un fragmento por request, sin paginación ni rutas públicas nuevas
  de secciones/chunks: `{document_id, chunk_id, texto, pagina, seccion,
  seccion_id, source_type, interpretacion_modelo, imagen_url}`. #30 agrega los
  tres últimos campos: tipo de fuente, indicador de interpretación del modelo
  y URL autenticada de la página original para chunks `image_description`.
  En texto, imagen_url=null. Ubicación no disponible: null. El título no identifica la
  sección. Se exige ready **SQLite y OCI**, admisión explícitamente confirmada
  y cobertura/metadata coherentes y completas. Legacy sin evidencia suficiente
  conserva listado/detalle, pero no habilita contenido. Se
  revalida sesión, ownership, lápidas, hash y publicación después del I/O.
  Processing/failed o índice inconsistente: 409 `INVALID_STATE`; recurso
  inexistente/ajeno/retirado: 404. Storage inválido/no verificable o deadline
  de lectura: 503 `STORAGE_UNAVAILABLE`; índice no configurado: 503
  `PROVIDER_UNAVAILABLE`. Chroma por sí solo jamás habilita contenido.
- Toda admisión con fila en `document_admissions` exige ambos marcadores
  `admision_confirmada` y `admision_sqlite_confirmada` exactamente true y
  estado encolada. Compatibilidad heredada: sin fila y ambos marcadores
  ausentes/null. Los manifiestos nuevos reconstruidos sin su registro de
  admisión no se tratan como legacy; quedan bloqueados para reconciliación.
  **Precisión H02 propuesta, sujeta a revisión `contract-change`:** después
  de perder SQLite, una procedencia local restaurada desde OCI puede autorizar
  listado/detalle sin fabricar una admisión histórica. Exige workspace vigente,
  identidad canónica, original presente y SHA-256 verificado, metadata válida,
  ambos marcadores exactamente true y ausencia de lápidas locales/remotas.
  Esa evidencia no autoriza fuentes ni generación: un nuevo trabajo real de
  reconstrucción debe confirmar extracción, cobertura, índice completo y
  manifiesto OCI antes del commit terminal local. Con visión pendiente se
  conserva processing. Failed histórico no se reprocesa automáticamente.
  Las sesiones se recuperan con el código; no se restauran tokens, jobs/eventos
  históricos, Idempotency-Key ni respuestas de upload perdidas. Las rutas,
  schemas y códigos HTTP permanecen iguales; esta excepción de acceso requiere
  revisión del equipo antes de merge/publicación del contrato.
- Un trabajo activo/encolado o admisión en curso en el workspace: 429
  **`RATE_LIMITED`**, código ya vigente. La idempotencia se reconoce primero;
  clave incompatible: 409 `IDEMPOTENCY_CONFLICT`. **`WORKSPACE_BUSY` continúa
  como propuesta pendiente de revisión**, sin cambio unilateral del enum.
- Duplicado aún sin confirmación: espera acotada (10 segundos internos por
  defecto, sin mantener transacciones SQLite durante storage/espera); al
  agotarse, 503 `STORAGE_UNAVAILABLE`, sin recibo ni IDs. Details:
  `{confirmacion: "indeterminada", idempotency_key_presente: boolean}`.
  Fallos de storage de upload: mismo código, confirmacion "no_verificable".
  503 no afirma fracaso definitivo; conserva intención y estado. Con clave,
  reutilizarla evita una segunda intención, pero **no garantiza reconciliación
  ni éxito posterior**. Sin clave no se promete recuperación idempotente:
  falta una decisión pública para identificar esa intención perdida.
- Activación: cuotas compartidas explícitas por inyección o configuración
  conjunta `EMBEDDING_QUOTA_RPM`, `EMBEDDING_QUOTA_TPM`, `EMBEDDING_QUOTA_RPD`,
  positivas y para `GEMINI_EMBEDDING_MODEL`. No hay valores universales ni
  fallback a un proveedor mock; mock exige doble explícito. Sin cuotas,
  listado/detalle están disponibles; upload responde 503 `PROVIDER_UNAVAILABLE`
  sin reservar documentos. La cuenta del equipo debe determinar los valores
  de producción; no se modifica despliegue #49/#50 aquí.

Ejemplo de aceptación confirmada:

```json UploadDocumentoResponse
{"document_id":"doc_ejemplo","status":"processing","job_id":"job_ejemplo","status_url":"/api/jobs/job_ejemplo","events_url":"/api/jobs/job_ejemplo/events","cancel_url":"/api/jobs/job_ejemplo/cancel"}
```

Ejemplo de detalle aún sin extracción:

```json DocumentoResponse
{"document_id":"doc_ejemplo","titulo":"Redes","status":"processing","metadata":{"extension":"txt","tamano_bytes":800,"creado_en":"2026-10-10T12:00:00Z","cantidad_paginas":null,"cantidad_tokens":null},"cobertura":null,"error":null}
```

#### DELETE documental — precisión propuesta, pendiente de revisión `contract-change`

`DELETE /api/documents/{document_id}` está registrado localmente en etapa 5B.
Requiere sesión vigente y ownership del workspace. Sin body ni otra cola.
La lápida local y su obligación de limpieza se confirman en una transacción antes
de responder; listado, detalle, fuentes, generaciones y trabajos vinculados dejan
de autorizar acceso. Se solicita cancelación cooperativa, sin matar llamadas en vuelo.

- **202**, `{"cleanup_pending":true}`: retirada lógica confirmada, quedan efectos
  físicos pendientes. No afirma que el original o índice ya fueron eliminados.
- **204**, sin body: limpieza verificada, incluido Chroma y objetos vinculados.
- **404**: inexistente, ajeno o retirado previamente. Repetir DELETE no cancela
  la obligación durable ni genera otra identidad; puede responder 404 aun si
  sigue pendiente la limpieza. Los GET documentales posteriores responden 404.
- **401**: sesión inválida. **503** con envolvente existente y
  `STORAGE_UNAVAILABLE`: no se pudo acreditar el commit de retirada.

La limpieza reutiliza `document_cleanup`, `EfectosDocumentales` y el sincronizador.
Confirma lápidas privadas con ETag/versiones antes de eliminar evidencia; conserva
las lápidas remotas más recientes. Retira catálogo/chunks de Chroma, original,
manifiesto documental, output/exports de generaciones realmente relacionadas,
resultados/eventos operativos vinculados y referencias explícitas en progreso.
No borra el agregado completo de progreso ni recursos de otros documentos; no
hay persistencia independiente de chat integrada. Conserva identidades, estados
terminales, idempotencia y lápidas para protección y reintentos. No borra demo.
Una respuesta perdida no acredita limpieza física: se verifica ausencia/estado y
se reintenta. Productores en vuelo mantienen el efecto pendiente hasta detenerse.

Los status 202/204, JSON pendiente y repetición 404 precisan el contrato anterior
según dirección del equipo, pero requieren revisión `contract-change`. La pérdida
completa del volumen SQLite antes de sincronizar una lápida sigue pendiente de
decisión del equipo. Esta implementación local no declara cerrado el Issue 19.

### Catálogo demo de solo lectura (Issue 19, H03)

**Dirección vigente: decisiones técnicas y contractuales aprobadas por delegación
explícita del equipo. Implementación local; la integración conserva la trazabilidad
`contract-change` y revisión API+UI+AGT+RAG.** No cambia las rutas privadas ni el
contrato de generación. Issue 25 consume estas lecturas, Issue 49 prepara y mantiene
la biblioteca desplegada y Issue 30 completa visión.

| Método y ruta | Respuesta 200 |
|---|---|
| `GET /api/demo/documents?limit=20&cursor=…` | `DocumentosResponse`: `{items, next_cursor}` |
| `GET /api/demo/documents/{document_id}` | `DocumentoResponse`: `{document_id, titulo, status, metadata, cobertura, error}` |
| `GET /api/demo/documents/{document_id}/sources/{chunk_id}` | `FuenteDocumentoResponse`: `{document_id, chunk_id, texto, pagina, seccion, seccion_id}` |

- Bearer obligatorio; token ausente/inválido/revocado/vencido o workspace retirado:
  401 `SESSION_INVALID`. Cada solicitud revalida sesión. Cualquier sesión válida
  puede leer la demo compartida. Estas lecturas no renuevan actividad/retención:
  excepción explícita a la renovación agrupada, para conservar GET sin escrituras.
- Resolución exclusivamente por claves canónicas `demo/{document_id}/manifest.json`
  y `demo/{document_id}/original`, ámbito fijo `biblioteca-demo` y colección demo.
  No se acepta workspace del cliente ni se consulta un documento privado como
  fallback. Un ID coincidente en ambos ámbitos resuelve solamente su fuente demo.
  Los resultados futuros de generación pertenecen al workspace del usuario;
  autorización y generación demo no se implementan mediante estas rutas.
- OCI es el inventario autoritativo; un original o chunks sin manifiesto no crean
  una tarjeta. Catálogo accesible sin manifiestos: 200 `{items: [], next_cursor: null}`.
  Documento/chunk inexistente, privado o retirado: 404 `NOT_FOUND`. Lápidas demo
  remotas y del catálogo local bloquean el acceso; DELETE privado no retira demo.
- Paginación keyset por `document_id` ASCII ascendente, sensible a mayúsculas:
  `limit=20`, rango 1..100; inválido: 422 `VALIDATION_ERROR`. Cursor opaco firmado
  con dominio `biblioteca-demo`, distinto del cursor privado y sin fecha inventada.
  Cursor inválido/alterado/privado: 400 `INVALID_REQUEST`. Es compartible entre
  sesiones demo válidas; no autentica ni otorga permisos. No tiene expiración
  temporal. El ancla puede desaparecer. Retiradas concurrentes pueden acortar una
  página; el cursor no salta el lookahead. Inserciones anteriores al ancla se ven
  al reiniciar el recorrido: no se ofrece un snapshot inmutable.
- Metadata/cobertura desconocida: null, nunca conteos o fechas fabricados. Las
  secciones MD/TXT conservan ID, título y líneas, ordenadas por ubicación; títulos
  repetidos no se colapsan. PDF conserva páginas y `pagina:N` en los chunks; no
  fabrica líneas ni temas. Un inventario textual de secciones vacío puede coexistir
  con páginas PDF procesadas. No se añade una ruta pública de secciones.
- `status` es disponibilidad comprobada, no promesa de un job activo. Processing
  y failed persisten aunque existan chunks; el diagnóstico failed público es
  seguro y no remite a un job histórico inexistente. Visión pendiente conserva
  processing y bloquea fuentes. La lectura textual no interpreta imágenes.
- Ready exige original con SHA-256 verificado, manifiesto vigente sin lápidas,
  metadata coherente, cobertura completa y evidencia interna `publicacion_demo`
  compatible con la versión activa completa de Chroma. Se comprueban modelo,
  dimensión, preparación, cantidad/huella de chunks, orden e identidades físicas,
  pertenencia y ubicación. No basta `estado=ready`, un booleano ni chunks sueltos.
  No se fabrican admisiones ni confirmaciones SQLite privadas.
- Evidencia ausente o índice comprobablemente ausente/incompleto/incompatible:
  listado/detalle 200 processing; fuente 409 `INVALID_STATE`. Configuración de
  índice ausente cuando se necesita comprobar ready, o Chroma temporalmente
  inaccesible: 503 `PROVIDER_UNAVAILABLE`, sin convertir el fallo en estado durable.
  OCI inaccesible, original ausente/hash incorrecto o metadata/manifiesto inválidos:
  503 `STORAGE_UNAVAILABLE`, sin simular catálogo vacío. Cambio de ETag durante
  lectura: 409 `INVALID_STATE`; se debe repetir la lectura. Se revalidan original,
  manifiesto, lápidas, sesión y versión Chroma antes de entregar contenido.
- Extensión interna mínima y retrocompatible del manifiesto: `publicacion_demo`
  opcional, exclusiva de `biblioteca-demo`. Versión 1 contiene `hash_sha256`,
  `version_indice`, `cantidad_chunks`, `huella_chunks`, `huella_config_chunker`, `huella_documental`,
  `modelo_embeddings`, `dimensiones_embeddings`, `preparacion_embeddings`.
  La huella de chunks usa la firma del catálogo de #17; la configuración usa
  `ConfigChunker.huella(parser)`. `huella_documental_demo` liga metadata y cobertura
  exactas, incluyendo fecha, conteos y huella de parser, a esa publicación.
  Las interpretaciones visuales demo conservan procedencia y tipo
  `image_description`. El lector verifica el raster de la página original,
  versiones de prompt/raster, cobertura completa y chunks derivados exactos;
  una interpretación alterada no habilita fuentes ni `ready`. No agrega visión
  ni embeddings durante GET. La precarga administrativa de #49 se ejecuta
  offline con el gestor común y exclusividad de volumen con la API;
  ver [runbook de precarga](demo-precarga.md). No crea adaptaciones del #55
  ni habilita la generación demo todavía pendiente en #31/#32.
  Issue 49 debe emitir esta evidencia solo después
  de verificar su publicación, con ETag vigente. H03 únicamente valida y consume
  el bloque: no implementa precarga, ejecución de rebuild, embeddings ni runbook.
- GET no crea embeddings, trabajos, índices ni escrituras SQLite/OCI. Conserva
  schemas Pydantic y envolvente de errores, sin publicar configuración, hash,
  ETags, versión Chroma, claves OCI ni tokens. Todas estas precisiones y las tres
  rutas deben identificarse en la revisión de integración `contract-change`.

Ejemplo de fuente textual (ilustrativo, no contenido desplegado):

```json
{"document_id":"demo-textual","chunk_id":"chunk-ilustrativo","texto":"Configurar destinos autorizados.","pagina":null,"seccion":"Configuración","seccion_id":"seccion:1"}
```

### Generación (issues `Issue 31`, `Issue 29`, `Issue 32`, `Issue 33`)

#### `POST /api/generate`

```json
{
  "document_id": "doc_01HX…",
  "perfil_destinatario": "principiante",
  "formato_salida": "flashcards",
  "nicho_sector": "general",
  "nivel_detalle": "didactico",
  "idioma_salida": "es",
  "alcance": { "tipo": "documento_completo" }
}
```

Respuesta **202**:

```json
{
  "generation_id": "gen_01HX…",
  "status": "queued",
  "status_url": "/api/generations/gen_01HX…",
  "events_url": "/api/generations/gen_01HX…/events"
}
```

`alcance` alternativo: `{ "tipo": "seccion", "seccion_id": "sec_3" }`.

#### `GET /api/generations/{id}`

Estado del trabajo con generation_id, status, contenido, persistencia y error. contenido solo existe cuando completed; error identifica el diagnóstico terminal. Para quiz, vista=estudiante es la predeterminada y omite claves/justificaciones no respondidas; el canónico completo se descarga por exportación autorizada. Un `rejected_quality` se consulta con **HTTP 200** y diagnóstico, sin `contenido_adaptado`.

#### `GET /api/generations/{id}/events` (SSE)

`text/event-stream` con heartbeat. Formato de evento:

```text
id: 42
event: progress
data: {"generation_id":"gen_01HX…","step":"critic","status":"running","iteration":2}
```

Soporta `Last-Event-ID` mientras exista registro; cerrar el stream **no** cancela el trabajo. Estados terminales: `completed` (solo tras persistencia confirmada) · `rejected_quality` · `failed` · `cancelled`.

#### `POST /api/generations/{id}/cancel` · `POST /api/generations/{id}/persist`

Cancelación explícita; reintento de **solo persistencia** de un aprobado retenido (≤24 h, mismo `objeto_id`, sin llamadas LLM).

#### `GET /api/generations`

Listado paginado con filtros: `documento`, `perfil`, `formato`, `idioma`, `desde`, `hasta`.

### Quiz y progreso (issues `Issue 35`, `Issue 36`, `Issue 41`)

| Método y ruta | Descripción |
|---|---|
| `POST /api/quizzes/{generation_id}/answers` | Body `{question_id, option_id, event_id}` → `{correcta, correct_option_id, justificacion, referencias}`. Determinista, sin LLM. La vista inicial del estudiante nunca recibe la clave. |
| `POST /api/progress/events` | Eventos idempotentes (`event_id` estable): `concepto_revisado`, `flashcard_vista`, el evento respuesta_quiz es interno y lo crea el endpoint de respuestas; primer_intento y acierto los calcula exclusivamente el backend. |
| `GET /api/progress` | Agregados del espacio: conceptos, flashcards, aciertos de primer intento, tiempo restante estimado. |

Precisiones de implementación del Issue 35 (revisión `contract-change`):

- Los tres endpoints requieren Bearer y autorizan el espacio, documento y
  generación. Un quiz/flashcard ajeno o retirado devuelve **404**; un paquete
  no completado devuelve **409**, un formato/opción incompatible **422**.
  El feedback contiene exclusivamente los cuatro campos de la tabla, para la
  pregunta respondida. No crea un trabajo ni requiere un proveedor Gemini.
- `event_id` es global dentro del espacio y admite hasta 128 caracteres.
  `question_id`/`flashcard_id`/`option_id` admiten hasta 64; `concepto`, hasta
  500. El cliente emite `flashcard_vista` después de consultar **frente y dorso**.
  Los campos de puntaje, `primer_intento` y el tipo `respuesta_quiz` se rechazan
  en el endpoint genérico. No se aceptan campos de otro tipo de evento.
- `POST /api/progress/events` responde **200** con `{event_id, version}`;
  `version` identifica el registro original y se conserva al repetirlo.
  Los dos POST aceptan `Idempotency-Key` (1–128 caracteres). Reutilizar el
  mismo evento o header con otro cuerpo devuelve **409 IDEMPOTENCY_CONFLICT**.
  El header se guarda únicamente como hash, sin tokens de sesión.
- `GET /api/progress` responde **200** con `version`, `conceptos_revisados`,
  `flashcards_vistas`, `quiz_intentos`, `quiz_aciertos_primer_intento`,
  `quiz_total_primer_intento`, `quiz_aciertos_posteriores` y
  `tiempo_estimado_restante_minutos`. Sin eventos, versión y contadores son 0.
  Conceptos se cuentan por documento, generación opcional y nombre; tarjetas
  por generación e ID. Primer intento se calcula por generación y pregunta,
  según el orden confirmado en storage. Otros intentos no cambian ese resultado.
- El tiempo es una orientación: para cada generación aprobada cuya fuente
  está READY, se reparte su estimación entre preguntas, tarjetas o conceptos
  (otros formatos), y se suma la proporción pendiente, redondeada a dos decimales.
  Una pregunta deja de estar pendiente con cualquier respuesta; una tarjeta,
  con su evento frente/dorso; un concepto marcado a nivel documento aplica a
  sus adaptaciones. No implica dominio ni certificación. Material retirado
  o todavía en reconstrucción no participa de la estimación.
- `progress/{workspace_id}/state.json` guarda versión, eventos mínimos y
  hashes de claves; no incluye claves de respuesta, justificaciones ni tokens.
  Escrituras usan creación condicional/ETag y confirmación de lectura; conflictos
  de versión permiten hasta tres combinaciones. Un fallo OCI devuelve **503**
  sin prometer un registro local; repetir el mismo ID recupera un PUT confirmado
  cuya respuesta se perdió. SQLite replica el estado confirmado y OCI permite
  recuperarlo aun perdiendo el disco. La purga documental retira eventos y claves.
  El límite es 10.000 eventos y 10.000 claves por espacio (**429** al alcanzarlo).

Verificación: `pytest backend/tests/test_quiz_progress.py backend/tests/test_api_quiz_progress.py`.

### Contrato de ejecución de proveedores (issues 13 y 20)

- Las llamadas a Gemini usan adaptadores **síncronos** dentro del worker dedicado. Los endpoints HTTP pueden devolver 202 y procesar en segundo plano; esto no exige usar `async` en el SDK. El stream SSE sí espera sin bloquear la API.
- Cada solicitud real a Gemini pasa por `ctx.llamar(funcion, modelo=..., tokens_estimados=...)`; `funcion` recibe `timeout` en segundos y devuelve el resultado resuelto, nunca una corutina. El adaptador convierte el timeout a las unidades del SDK y traduce fallos transitorios a `ReintentableError`, conservando `Retry-After` cuando exista. Una cuota diaria agotada se propaga sin reintentar.
- El gestor es el único dueño de los reintentos de Gemini: hasta dos adicionales, tres intentos técnicos en total. Desactivar reintentos automáticos del SDK y evitar bucles adicionales del wrapper. Cada intento reserva cuota y consume el presupuesto que corresponda. Las tres redacciones pedagógicas son otro límite; no se repite el pipeline por un fallo técnico.
- Los presupuestos RPM/TPM/RPD se comparten por modelo; el límite de 20 solicitudes de generación/redacción/revisión no convierte 100 embeddings en 20. El grafo aplica su presupuesto además de las cuotas del modelo, incluidos reintentos y verificaciones visuales de la generación.
- Writer pasa también `presupuesto=PresupuestoLlamadas` a `ctx.llamar`; los nodos
  de revisión deberán reutilizarlo:
  verifica disponibilidad antes de reservar cuota e incrementa `usadas` por solicitud
  admitida, incluidos retries. Una cuota denegada no consume este contador.
  La firma sigue siendo compatible para consumidores que omiten este presupuesto;
  embeddings de Issue 13 no usa el presupuesto de redacción/revisión.
- `DobleGemini` conserva `embed`, `generar` y `verificar_afirmacion` asíncronos. Agregar `embed_sync` en Issue 13 y las entradas síncronas de generación/verificación al implementar sus consumidores; reutilizar la misma lógica, respuestas y contadores. Los dobles se habilitan explícitamente solo en desarrollo/CI.
- OCI mantiene la política propia de Issue 14: escrituras idempotentes, hasta tres intentos totales y contabilización de solicitudes de storage. No usa cuotas Gemini ni añade otro bucle a `ctx.llamar`.
- Los endpoints comunes `GET /api/jobs/{id}`, `GET /api/jobs/{id}/events` y `POST /api/jobs/{id}/cancel` pertenecen a **Issue 20**. Issue 19 los consume, sin volver a implementarlos. El gestor se inicia y detiene en el lifespan de la API; al detenerse se espera su worker antes de cerrar SQLite.

### Almacenamiento real (Issue 14)

- `OCIObjectStorageProvider` implementa el mismo `StorageProvider` que el mock:
  upload/get/get_as_text/list/delete, condiciones ETag y cursor exclusivo del último
  objeto. Los constructores de claves canónicas viven en `storage/object_keys.py`;
  el nombre original se conserva en el manifiesto, no en la clave de almacenamiento.
- La fábrica usa OCI solo con `MOCK_OCI=0`, sin fallback. Credenciales por archivo
  (`OCI_CONFIG_PROFILE`) o identidad de instancia (`OCI_AUTH_TYPE`); consulta la home
  region y namespace y verifica compartimento, bucket privado Standard, cifrado Oracle,
  sin versionado, replicación ni auto-tiering. La API no crea buckets.
- El SDK lleva `NoneRetryStrategy`. Hasta tres intentos por operación; PUT conserva
  objeto/bytes/nonce/condiciones y reconcilia respuestas perdidas contra sus bytes y nonce.
  Verificaciones GET son solicitudes independientes, contabilizadas, sin repetir PUT
  por un fallo posterior de confirmación. OCI no utiliza cuotas Gemini ni `ctx.llamar`.
- `OCI_ALWAYS_FREE_CONFIRMED=1` declara que el operador verificó asignación/consumo
  agregado de la tenancy. No verifica facturación automáticamente. Un ledger SQLite
  en DATA_DIR reserva antes de cada intento, incluidos fallidos, con máximos iniciales
  de 5.000 solicitudes/mes y 1 GB; conserva reservas de escrituras inciertas. Issue 50
  completa auditoría agregada/conciliación/retención. `StorageBudgetExceeded` es un
  `StorageUnavailable`: no admite enviar otra solicitud por encima del presupuesto.
  En HTTP responde `503 / STORAGE_UNAVAILABLE`; en el worker termina `failed` con
  ese código y sin resultado, sin repetir el pipeline ni exponer mensajes del SDK.
- Guía, IAM mínimo, configuración Windows/Docker y smoke manual:
  `docs/oci-storage.md`. `app.tools.verify_oci` prueba originales/JSON técnicos y los
  limpia; `--create-bucket` permite provisionamiento manual explícito, con permisos
  separados. Tests SDK/HTTP simulados no acreditan una tenancy real ni Gemini.

### Índice vectorial local (Issue 17)

- `core/rag/vectorstore.py`: `VectorStoreChroma(Path(config.data_dir) / "chroma", embeddings)`
  usa Chroma embebido persistente, sin servidor externo ni modelo descargado. `embeddings`
  es el `GeminiEmbeddings` de Issue 13. El backend monta `DATA_DIR=/app/.data` en
  `backend_data`; el índice y su catálogo SQLite WAL son datos derivados.
- `indexar`, `buscar`, `contar` y `borrar` reciben `workspace_id`, `document_id` y
  `contexto=ctx`. El espacio sale de la sesión y debe coincidir con el contexto;
  el consumidor valida ownership/estado del documento en el registro operativo.
  Todas las lecturas, búsquedas y eliminaciones de chunks filtran **ambos IDs**;
  no existe una consulta global ni filtros libres. `buscar` devuelve chunk completo,
  distancia coseno y vector para el MMR de Issue 18. Su similitud no es un score de fidelidad.
- Colecciones `embeddings-{modelo}-{dimensiones}` y
  `demo-embeddings-{modelo}-{dimensiones}` con preparación y schema verificados.
  `buscar_demo` resuelve el espacio lógico público `biblioteca-demo` y filtra además
  el documento; el contexto del usuario sigue vigente y consume sus cuotas compartidas.
  Solo `reconstruir_demo`, con contexto de mantenimiento de ese espacio reservado,
  escribe la demo. No se expone una ruta pública de escritura.
- Se generan embeddings síncronos por chunk a través de `ctx.llamar`. Chroma recibe
  lotes de hasta 64 registros, acotados además por su límite nativo. Un intento tiene
  un UUID; SQLite publica su versión solo después de confirmar todos sus chunks.
  Fallos/cancelación no hacen visible un índice parcial. Reindexar el mismo contenido
  reutiliza vectores únicamente dentro del mismo espacio/modelo/preparación; los nuevos
  IDs y citas se conservan. Borrar confirma primero una lápida local y elimina chunks
  de todas las colecciones privadas de modelos/dimensiones de ese espacio/documento.
- `core/rag/rebuild.py` reconstruye desde `StorageProvider`, sin crear SDKs ni añadir
  retries: `reconstruir_workspace` pagina los documentos; `reconstruir_indice` recorre
  contextos autorizados en serie y opcionalmente la demo. Los contextos comparten cuotas
  y deadline del worker. Se verifica vigencia del workspace, SHA-256 del original y ETag
  del manifiesto antes de publicar. Las fallas OCI/Gemini se propagan como fallas técnicas.
- **Contrato interno para la ingesta Issue 19:** guardar primero el original en
  `source_documents/{workspace_id}/{document_id}/original` y su `ManifiestoDocumento`
  v1 en `.../manifest.json`: `workspace_id`, `document_id`, `source_name`, `hash_sha256`,
  `language`, `estado`, `borrado`, `parser` y `chunker`. Los valores por defecto de
  idioma/configuración están en el modelo y deben serializarse mediante `model_dump_json()`.
  Demo usa `demo/{document_id}/original` y `demo/{document_id}/manifest.json` con espacio
  lógico `biblioteca-demo`. El nombre es una etiqueta, nunca una ruta local.
- Antes de limpiar físicamente una fuente, Issue 19 debe confirmar `borrado=true` en
  su manifiesto OCI y la lápida operativa. Un original remanente no autoriza recuperarlo.
  La reconstrucción omite lápidas locales/manifiestos borrados y retira índices de
  documentos sin manifiesto. No restaura sesiones ni garantiza continuidad del SQLite
  operativo tras perder la VM. PDFs con visión necesaria permanecen `processing`, incluso
  si el manifiesto anterior decía `ready`; Issue 30 reconstruye sus interpretaciones.
- Verificación local: `pytest backend/tests/test_vectorstore.py` usa Chroma nativo
  persistente y dobles explícitos de OCI/Gemini. No acredita servicios reales ni una
  ingesta HTTP completa, que corresponde a Issue 19.

### Trabajos comunes (Issue 20)

GET /api/jobs/{id}, GET /api/jobs/{id}/events y POST /api/jobs/{id}/cancel permiten consultar, seguir y cancelar ingestión/chat/glosario.
Todos exigen sesión y ownership. Son vistas del mismo gestor, no otra cola.
Los POST asíncronos devuelven 202 con job_id, status_url, events_url y cancel_url.
El resultado de consulta contiene status, result (solo aprobado y disponible), error y posición si está en cola.
SSE usa job_id para trabajos comunes y generation_id para generaciones; id monotónico y heartbeat con línea vacía al cerrar cada evento.
Cerrar la conexión no cancela. Cancelar, fallar o rechazar nunca entrega borrador.
Chat/glosario usan Idempotency-Key por intención; caché aprobada puede responder 200.
Upload conserva document_id y agrega las URLs del trabajo de ingestión.

- Consulta y cancelación responden 200 con `TrabajoComunResponse`: `job_id`, `status`, `status_url`, `events_url`, `cancel_url`, `posicion_cola`, `result` y `error`. `result` solo aparece con valor en `completed`; los demás estados no entregan borradores. `cancel_url` queda nulo al terminar y la posición solo aplica a `queued`.
- Cancelar en cola devuelve `cancelled`; en ejecución puede devolver `running` hasta que el worker observe el flag. Repetir una cancelación ya completada como `cancelled` es idempotente. Otro estado terminal devuelve 409 `INVALID_STATE`.
- Trabajo inexistente o ajeno devuelve 404 `NOT_FOUND`; sesión inválida devuelve 401. Los trabajos de generación usan sus rutas propias y su vista estudiante; `/api/jobs` no expone el paquete canónico de un quiz.
- SSE: `event: progress`, `id: <entero>` y `data: {job_id, step, status, iteration}`, terminado por una línea vacía. Heartbeat `: heartbeat` al menos cada 15 s mientras esté activo; `Last-Event-ID` es un entero entre 0 y 2^63−1. El stream entrega eventos posteriores al cursor y cierra al estado terminal. Revalida la sesión y el espacio durante el seguimiento; si se revocan después de enviar headers, emite `event: error` y cierra. Nunca transmite resultados ni tokens por SSE.

### Chat y glosario (issues `Issue 37`, `Issue 39`, `Issue 38`, `Issue 40`)

| Método y ruta | Descripción |
|---|---|
| `POST /api/chat` | `{document_id, pregunta, perfil_destinatario, idioma_salida}` → 202 y URLs del trabajo; su result contiene respuesta revisada con citas o abstención explícita. |

Precisión #37 (`contract-change`): el recibo es `TrabajoComunResponse`; consulta,
SSE/reconexión y cancelación se realizan en `/api/jobs/{job_id}`. `result` es
`ChatAnswer`: `document_id`, `document_hash`, `pregunta`, `perfil_destinatario`,
`idioma_salida`, `estado`, `respuesta`, `referencias` y `evaluacion_calidad`.
`estado=respondida` requiere revisión completa aprobada; `fuera_de_alcance` sólo
entrega una abstención fija que pide precisar, con referencias vacías y evaluación
nula. `rejected_quality` no entrega result. El historial usa únicamente turnos
completed del mismo workspace/documento/hash, acotados por
`CHAT_HISTORY_TURNS`/`CHAT_HISTORY_MAX_TOKENS`; no es evidencia ni se envía al Critic.
El resultado usa SQLite común, sin crear artefactos/exportaciones en `outputs/`.
Su fuente se revalida al entregar el resultado y su retirada purga el historial.
Detalles de revisión y adaptador interno: [issue-37-document-chat.md](issue-37-document-chat.md).

Corrección de consistencia junto a #39: chat acepta `Idempotency-Key` (1–128
caracteres); repetir una intención conserva el trabajo y no repite llamadas.
Mismo header con otra pregunta/parámetros devuelve **409**. Consultar respuestas
aprobadas y repetir su intención funcionan sin proveedores LLM configurados;
una intención nueva sin proveedores devuelve **503**. Historial, consulta y
exportación JSON de generaciones aprobadas también son lecturas independientes
de los proveedores. Las fuentes conservan sus comprobaciones READY y de acceso.

| Método y ruta | Descripción |
|---|---|
| `POST /api/glossaries` | `{document_id, perfil_destinatario, idioma_salida}` → 202 con glossary_id y URLs del trabajo, o 200 con resultado de caché verificado. |
| `GET /api/glossaries/{id}` | Glosario generado con definiciones citadas. |

Precisión #39 (`contract-change`):

- La ingesta detecta candidatos y sus ubicaciones sin generar definiciones ni
  llamar a Gemini para esta detección. Guarda `terminos_candidatos`,
  `detector_terminos_version` y `candidatos_omitidos` en el manifiesto privado.
  La extracción es heurística: siglas, identificadores, productos con proveedor,
  títulos, texto destacado y expresiones de definición. Selecciona hasta 64
  términos y hasta 8 ubicaciones por término; los candidatos restantes se cuentan
  explícitamente. Documentos anteriores se detectan desde sus chunks autorizados.
- `POST /api/glossaries` recibe `GlossaryRequest`, con documento READY autorizado,
  perfil e idioma. `Idempotency-Key` (1–128 caracteres) conserva la intención;
  mismo header con otro cuerpo devuelve **409 IDEMPOTENCY_CONFLICT**. Un trabajo
  activo de la misma combinación devuelve el mismo recibo. Un nuevo glosario
  devuelve **202 GlossaryJobResponse**: campos de `TrabajoComunResponse` y
  `glossary_id` (el mismo ID que el trabajo común). Una caché verificada devuelve
  **200** con el mismo recibo y `result`, sin crear trabajos ni llamadas.
- `GET /api/glossaries/{glossary_id}` devuelve **200 GlossaryOutput**:
  `schema_version`, `glossary_id`, `documento_fuente`, `perfil_destinatario`,
  `idioma_salida`, `estado`, `terminos`, `candidatos_omitidos`,
  `evaluacion_calidad`, `trazabilidad`, `prompt_version` y `created_at`.
  Una consulta antes de completarse devuelve **409**; un recurso ajeno/retirado,
  **404**. `/api/jobs/{id}` entrega el mismo resultado autorizado; SSE y cancelación
  conservan el contrato común.
- Cada término contiene `id`, `termino_original`, `es_identificador`,
  `traduccion`, `estado`, `definicion`, `referencias` y `limitacion`. El estado
  `definido` requiere definición citada y revisión completa aprobada. Productos,
  comandos y APIs conservan su nombre original sin traducción. Definición y
  traducción pasan juntas por el Critic; el extractor factual debe cubrir cada
  definición. No se publican borradores rechazados ni incompletos.
- Un término sin respaldo usa `estado=sin_definicion_suficiente`, definición y
  traducción nulas y una limitación fija en el idioma solicitado. Sus citas las
  asigna el backend para mostrar dónde aparece el término; no prueban una definición.
  Si no hay definiciones, el glosario declara `sin_definiciones` con evaluación
  nula; sin candidatos declara `sin_terminos`. Estas limitaciones no reciben
  un score ficticio. El estado `verificado` requiere definiciones y calidad aprobada.
- La clave de caché incluye espacio, documento/hash/versión, configuración del
  parser, modelos de generación/verificación/embeddings, perfil, idioma, versiones
  del prompt de glosario/base y detector. SQLite conserva trabajos y resultados
  verificados; fallos y cancelaciones no son caché reutilizable. Un reinicio
  normal conserva esa caché; tras perder el disco se puede volver a generar el
  glosario sobre la fuente recuperada. El borrado documental purga sus resultados.
- Generación, juez y embeddings usan adaptadores síncronos, cuotas compartidas,
  deadline/cancelación y hasta tres redacciones de la política existente.
  La consulta y caché de un resultado verificado funcionan sin proveedores LLM.

Verificación: `pytest backend/tests/test_glossary.py backend/tests/test_glossary_models.py`.

### Exportaciones (issues `Issue 43`–`Issue 45`, `Issue 46`)

`GET /api/exports/{generation_id}?format=json|md|pdf|csv|tsv|apkg`

- Solo sobre trabajos `completed` (si no → **409**).
- `csv|tsv|apkg` solo para `formato_salida=flashcards` (si no → **422** `EXPORT_INCOMPATIBLE`).
- Descarga pasa por la API con verificación de ownership; sin secretos en el contenido exportado.

**Implementado:** `json` (predeterminado), `md` (#43) y `pdf` (#44) para los cinco
formatos; `csv|tsv|apkg` para flashcards (#45).
Un nombre desconocido o una combinación incompatible también devuelve 422.
El espacio y el estado se autorizan antes de acceder a derivados: recurso ajeno
o retirado → 404; generación propia no `completed` → 409.

| Descarga | Content-Type | Contenido |
|---|---|---|
| `json` | `application/json` | Bytes exactos del objeto `outputs/{ws}/{gen}/content.json`; sin reserializar ni añadir estado de transporte |
| `md` | `text/markdown; charset=utf-8` | Plantilla determinista del canónico tipado, con etiquetas en su `idioma_salida` |
| `pdf` | `application/pdf` | A4 con fuentes Unicode locales embebidas, código paginado, tarjetas imprimibles y soluciones de quiz separadas al final |
| `csv` | `text/csv; charset=utf-8` | Flashcards con cabeceras Anki y columnas Front/Back/Tags/GUID/Deck |
| `tsv` | `text/tab-separated-values; charset=utf-8` | Las mismas columnas separadas por tabuladores |
| `apkg` | `application/octet-stream` | Colección genanki con modelo/mazo estables, sin media |

Todas usan `Content-Disposition: attachment; filename="{generation_id}.{format}"`,
`Cache-Control: private, no-store`, `X-Content-Type-Options: nosniff` y `X-Request-ID`.
El nombre del archivo usa el ID generado; los títulos no se interpolan en headers.
No se redirige a URLs públicas ni se incrustan tokens/códigos de recuperación.
El JSON completo conserva las soluciones del quiz; Markdown coloca todas las
preguntas antes de una sección separada de soluciones y justificaciones.

Markdown incluye título, audiencia, idioma, tiempo estimado, objetivos,
prerrequisitos, conceptos, cuerpo específico de cada formato y referencias por
fragmento/página/sección. El código del tutorial es un bloque literal con fence
seguro; el resto del texto se escapa para impedir HTML o markup activo inyectado.
Ninguna exportación vuelve a llamar al LLM ni transforma un tutorial en flashcards.

Al persistir una generación aprobada se confirman todos los formatos compatibles en
`exports/{workspace_id}/{generation_id}/{format}` usando el `StorageProvider`
existente, incluidas sus reservas de capacidad/cuota en OCI real. Un fallo de
derivados deja el trabajo `failed/STORAGE_UNAVAILABLE`; el aprobado se conserva
según el plazo de 24 horas para `/persist`, que completa la escritura sin IA.
Para aprobados anteriores sin caché, la primera descarga crea únicamente el
formato pedido desde el canónico. Una caché presente pero distinta de la vista
esperada devuelve 503 `STORAGE_UNAVAILABLE`; nunca entrega contenido alterado.
Las lecturas siguen disponibles sin configurar proveedores LLM ni Chroma.
La autorización se revalida después de I/O y la exclusión compartida con la
retirada documental evita recrear exports tras su limpieza. El borrado y la
retención existentes abarcan el prefijo de derivados.

Anki es una exportación opcional; estudiar tarjetas dentro de NuevaMente corresponde
a la interfaz del #34. La importación, identidad estable y neutralización de fórmulas,
HTML y directivas de media se documentan en [Importación Anki](importacion-anki.md).

PDF no interpreta HTML/imágenes/enlaces de la fuente ni ejecuta código. Se
documentan presentación, fuentes, determinismo y evidencia en
[Exportación PDF](exportacion-pdf.md). Los botones de descarga de la UI son #46.

### Uso y vencimiento del espacio (#50)

`GET /api/workspaces/current` → `workspace_id`, `expira_en`, `ultima_actividad_en`,
`uso` (documentos/generaciones y límites 5/20), `almacenamiento` (agregados del
ledger, límites/margen, alerta 80%, bloqueo y proveedor). Requiere la sesión;
no devuelve contenido ni nombres de objetos. Es una adición de lectura al contrato.
Los detalles y el diagnóstico administrativo están en [Gobernanza de costos](gobernanza-costos.md).

### Salud

`GET /api/health` → `{status, version}`. Sin secretos ni llamadas LLM.

---

## Paquete de salida: `PedagogicalOutput` (schema_version 1.0)

Estructura canónica del campo `contenido` cuando `status=completed` (decisión §16.3; la unión `contenido_adaptado` discrimina por `tipo`):

```json
{
  "schema_version": "1.0",
  "generation_id": "gen_01HX…",
  "status": "aprobado",
  "metadatos": {
    "perfil_aplicado": "principiante",
    "formato_generado": "flashcards",
    "nicho_sector": "general",
    "nivel_detalle": "didactico",
    "idioma_origen": "es",
    "idioma_salida": "es",
    "conceptos_clave": ["VCN", "Subredes", "Internet Gateway", "Security Lists"],
    "prerrequisitos": [],
    "objetivos_aprendizaje": ["Explicar qué es una VCN y para qué sirve"],
    "tiempo_estimado_estudio_minutos": 5,
    "alcance": { "tipo": "documento_completo", "secciones_cubiertas": ["Conceptos"] }
  },
  "documento_fuente": {
    "document_id": "doc_01HX…",
    "titulo": "Introducción a la Arquitectura de Redes VCN en OCI",
    "hash": "sha256:…",
    "version": "v1"
  },
  "contenido_adaptado": {
    "tipo": "flashcards",
    "titulo": "Dominando Redes en la Nube (VCN) desde Cero",
    "introduccion_contextualizada": "…",
    "items": [
      {
        "id": "fc_001",
        "frente": "¿Qué es una VCN en Oracle Cloud?",
        "dorso": "…",
        "pista_didactica": "…",
        "etiquetas": ["redes"],
        "referencias": [{ "chunk_id": "chk_…", "pagina": 2 }]
      }
    ]
  },
  "evaluacion_calidad": {
    "anclaje_fuente_score": 1.0,
    "cantidad_afirmaciones": 12,
    "cantidad_respaldadas": 12,
    "estado_evaluacion": "aprobada",
    "claridad_pedagogica": "alta",
    "adecuacion_perfil": "alta",
    "cobertura_objetivos": "completa",
    "coherencia_didactica": "alta",
    "verificacion_visual": "no_aplica",
    "observaciones": "…"
  },
  "referencias": [ { "chunk_id": "chk_…", "pagina": 2, "seccion": "Conceptos" } ],
  "created_at": "2026-10-01T12:00:00Z",
  "trazabilidad": {
    "modelo_generacion": "gemini-2.5-flash",
    "modelo_verificacion": "gemini-2.5-flash",
    "modelo_embeddings": "gemini-embedding-2",
    "prompt_version": "1.0",
    "parser_version": "1.0",
    "retrieval": { "k": 5, "fetch_k": 15, "lambda_mult": 0.7 }
  },
  "almacenamiento_oci": {
    "bucket": "nuevamente-contenidos-educativos",
    "objeto_id": "outputs/{ws}/gen_…/content.json"
  }
}
```

Notas de contrato:

- El score **solo** vive en `evaluacion_calidad.anclaje_fuente_score` (no en la raíz).
- `contenido_adaptado` es unión discriminada por `tipo` con los 5 modelos pedagógicos (`flashcards`, `quiz`, `tutorial`, `resumen_ejecutivo`, `guion_clase`); cada uno con sus validaciones (IDs únicos, `correct_option_id ∈ options`, listas no vacías, duraciones positivas).
- En quiz, la vista de estudiante omite `correct_option_id` y `justificacion` hasta responder; el paquete exportado sí las incluye (sección de soluciones separada en PDF).
- El canónico solo contiene bucket/objeto_id; no confirma su propia escritura. persistencia.status_upload se añade exclusivamente en la respuesta del trabajo tras verificar el objeto.
- alcance conserva la estructura de entrada y agrega secciones cubiertas; no alterna objeto/string según formato.
- La respuesta HTTP del trabajo agrega `persistencia.status_upload` en el nivel del trabajo cuando `completed`.

### Ejemplos válidos de `contenido_adaptado` por formato

Estos JSON validan contra la implementación congelada; `backend/tests/test_schemas.py` los usa como fixtures, de modo que documento y código no pueden divergir en silencio. El ejemplo de `flashcards` es el del paquete completo de arriba.

#### `quiz`

```json
{
  "tipo": "quiz",
  "titulo": "Quiz: fundamentos de VCN",
  "preguntas": [
    {
      "id": "q_001",
      "enunciado": "¿Qué delimita una VCN dentro de OCI?",
      "opciones": [
        { "option_id": "A", "texto": "Un rango CIDR elegido al crearla" },
        { "option_id": "B", "texto": "El nombre de la región" },
        { "option_id": "C", "texto": "La lista de usuarios" },
        { "option_id": "D", "texto": "El tamaño del bucket" }
      ],
      "correct_option_id": "A",
      "justificacion": "La VCN se define por su bloque CIDR; B, C y D no delimitan redes.",
      "referencias": [{ "chunk_id": "chk_001", "pagina": 2, "seccion": "Conceptos" }]
    }
  ]
}
```

#### `tutorial`

```json
{
  "tipo": "tutorial",
  "titulo": "Tu primera VCN en 3 pasos",
  "audiencia": "Principiantes en cloud",
  "prerrequisitos": ["Cuenta de OCI"],
  "pasos": [
    {
      "id": "paso_1",
      "instruccion": "Abre el menú de redes y elige 'Virtual Cloud Networks'.",
      "resultado_esperado": "Ves la lista de VCNs del compartment.",
      "verificacion": "El botón 'Create VCN' está habilitado.",
      "codigo": null,
      "referencias": [{ "chunk_id": "chk_001", "pagina": 2, "seccion": "Conceptos" }]
    }
  ]
}
```

#### `resumen_ejecutivo`

```json
{
  "tipo": "resumen_ejecutivo",
  "titulo": "VCN para decisiones de negocio",
  "puntos_clave": ["Una VCN aísla y organiza los recursos de red."],
  "impacto_cualitativo": "Reduce riesgo de exposición sin costo adicional en Always Free.",
  "implicaciones": ["Toda arquitectura nueva debe nacer dentro de una VCN."],
  "acciones": ["Definir convención de rangos CIDR por ambiente."],
  "referencias": [{ "chunk_id": "chk_001", "pagina": 2, "seccion": "Conceptos" }]
}
```

#### `guion_clase`

```json
{
  "tipo": "guion_clase",
  "titulo": "Clase introductoria: redes en la nube",
  "objetivos": ["Explicar el concepto de red virtual privada."],
  "escenas": [
    {
      "id": "esc_1",
      "duracion_min": 2.5,
      "narracion": "Comencemos con una analogía: la ciudad y sus barrios...",
      "puntos_diapositiva": ["VCN = barrio privado", "Aislamiento por diseño"],
      "pregunta_interactiva": "¿Qué separaría un barrio de otro?",
      "referencias": [{ "chunk_id": "chk_001", "pagina": 2, "seccion": "Conceptos" }]
    }
  ]
}
```

---

### Recuperación, rotación y borrado

La UI permite copiar/descargar el código una vez, rotarlo, cerrar sesión y borrar el espacio con consecuencias visibles (Issue 16). Borrar revoca acceso inmediatamente; la respuesta confirma recepción y el backend mantiene el diagnóstico de limpieza sin reabrir una sesión revocada. La prueba de borrado usa observabilidad administrativa, no acceso del usuario borrado.

## Reglas transversales que el contrato asume

1. Errores de trabajo ≠ errores HTTP: consultar un trabajo `rejected_quality` es 200 + estado; exportar algo no aprobado es 409.
2. `completed` implica persistencia confirmada. Si falla, el estado es failed/STORAGE_UNAVAILABLE con persist disponible mientras dure la retención, nunca completed. En desarrollo, persistencia indica provider=mock y no afirma completado en OCI.
3. Límites operativos (§7.5) se expresan vía 429 con `code` específico y mensaje con posición/expectativa.
4. Todo listado es paginado (`limit`, `cursor`).
5. Los nombres internos de clases pueden diferir; este JSON es el contrato público y sus claves no cambian sin `contract-change`.

### Precisiones de validación de v1 (revisión de Issue 03)

- `alcance` omitido selecciona `documento_completo`. `secciones_cubiertas` pertenece exclusivamente a la salida.
- `concepto_revisado` exige `document_id` y `concepto`; `flashcard_vista` exige `generation_id` y `flashcard_id` (el `id` del ítem). Ambos requieren `event_id`.
- `no_evaluable` admite cero afirmaciones y exige score nulo. En evaluaciones completas, el score es respaldadas/total. No se aprueban afirmaciones sin respaldo conocido, bloqueos ni verificaciones insuficientes (§19).
- El paquete canónico exige evaluación aprobada y coincidencia entre formato declarado y contenido. Un trabajo solo entrega contenido en `completed`, con el mismo `generation_id` y persistencia confirmada. `persistencia.provider` es obligatorio: `mock` identifica almacenamiento local, sin acreditar OCI.
- La reserva SQLite de idempotencia exige un `recurso_id` desde la primera llamada; los reintentos en curso reutilizan ese ID y completar la operación no puede cambiarlo. No se cachean credenciales.

Estos ajustes de contrato deben conservar la revisión API/UI/AGT/RAG y la etiqueta `contract-change` al abrir el PR.


### Precisiones de implementación tras la revisión de la base

- `GenerationJobResponse.contenido` utiliza `PedagogicalStudentOutput`. Para quiz,
  `contenido_adaptado` conserva `tipo: "quiz"`, título, introducción y preguntas
  con ID/enunciado/opciones; no incluye la clave correcta ni justificaciones.
  `PedagogicalStudentOutput.desde_canonico(paquete)` construye esa proyección.
  El canónico `PedagogicalOutput` conserva todas las respuestas para almacenamiento
  y exportación autorizada. Los otros cuatro formatos mantienen su estructura.
- Los textos declarados y los elementos textuales de listas no admiten valores
  vacíos ni compuestos solo por espacios. Las duraciones deben ser finitas y positivas.
  `GenerateRequest.formato_salida` reutiliza `PedagogicalFormat` sin cambiar valores JSON.
- Las rutas pueden lanzar `ErrorAplicacion(ErrorCode.QUEUE_FULL, mensaje, detalles)`
  para conservar el código específico, su HTTP y `X-Request-ID`. `HTTPException`
  conserva el mapeo genérico; no usar su `detail` para transportar códigos de dominio.
- Esta precisión de esquemas públicos debe revisarse como `contract-change` en el PR.

### Biblioteca de prompts (Issue 22)

- `core/agents/prompts.py` compone Writer/Critic para los cuatro perfiles, cinco
  formatos, cuatro nichos y tres idiomas desde matrices comunes; el detalle es
  independiente del perfil. `cargar_plantilla(GenerateRequest)` entrega los dos
  roles y `prompt_version=nm-prompts-1.1.1`, sin usar proveedores.
- `preparar_prompts(..., workspace_id, source_hash, idioma_origen, evidencia)`
  separa `system_instruction` de `datos_json`. Documento, consultas, feedback y
  borrador nunca se interpolan en el sistema. Rechaza evidencia vacía, ajena,
  de otra versión y colisiones de chunk_id; deduplica el mismo fragmento.
  Espacio/hash provienen del contexto y registro autorizados, no del cliente.
- Los ejemplos few-shot contienen marcadores de contenido y frases de tono,
  sin hechos técnicos externos. IDs, citas, metadata y duraciones del ejemplo
  son ilustrativos: no se copian al resultado. El schema de Writer se obtiene
  de los modelos vigentes; produce contenido y metadatos antes de Critic.
- Idioma de salida: es latinoamericano, inglés técnico o pt-BR. Se conservan
  citas originales e identificadores técnicos; las traducciones se etiquetan.
  Critic revisa metadatos, distractores, cobertura y todos los bloqueos de §19.
  Usa el resultado factual del backend, sin inventar score o conteos.
- `PromptsGeneracion.registrar_trazabilidad(Trazabilidad)` devuelve una copia
  con la versión utilizada, que los consumidores conservan en el paquete
  canónico. No almacena prompts completos ni cambia el contrato HTTP v1.
- Writer y Critic (issues 23/28) usan estas plantillas con `ctx.llamar`. Issue 29
  conectará las aristas; Issue 30 suministrará imágenes originales al juez visual.
  Las instrucciones reducen riesgo de injection, pero no sustituyen autorización,
  validación ni evaluación.
  Evidencia automatizada: `backend/tests/test_prompts.py`; los ejemplos requieren
  revisión de pares al revisar el PR.

### Writer (Issue 23)

- `writer(EstadoGrafo, DependenciasWriter)` entrega un update con borrador Pydantic
  y referencias canónicas para Critic. Requiere parámetros/restricciones del Supervisor,
  evidencia autorizada, cobertura recuperada y trazabilidad de ingesta/recuperación.
  No aprueba ni persiste contenido ni marca `completed`.
- Revalida el documento antes de llamar y antes de devolver el borrador. Rechaza
  evidencia ajena/antigua, cambios de fuente, citas inventadas y ubicaciones falsas.
  Completa ubicaciones omitidas desde el chunk autorizado, sin agregar hechos nuevos.
  Metadatos deben coincidir con perfil, formato, nicho, detalle, idiomas y alcance;
  no se puede inventar cobertura. Critic revisa su respaldo factual y pedagógico.
- Parseo JSON estricto, sin cercas Markdown, claves duplicadas ni valores no finitos.
  Una corrección de estructura/citas consume otra redacción del contador global, hasta
  tres contando las revisiones previas de Critic. Al agotarlas sin borrador válido:
  `failed/VALIDATION_ERROR`. Una nueva invocación tras tres redacciones válidas pero
  no aprobadas termina `rejected_quality`, sin conservar el borrador anterior.
- `ClienteGeminiGeneracion` usa `google-genai==2.28.0`, cliente síncrono, JSON Schema
  del formato y llamadas mediante `ctx.llamar`. Desactiva retries del SDK (un intento)
  y llamadas automáticas a funciones. Convierte timeout a ms; respeta Retry-After,
  distingue cuota diaria de 429 transitorio y falla sin fallback ni payloads sensibles.
  Respuestas bloqueadas, vacías o truncadas no se aceptan como borradores.
- `GENERATION_MAX_OUTPUT_TOKENS=8192` es configurable. La reserva TPM estima entrada
  con el BPE offline e incluye el máximo de salida. Modelo de generación: configuración
  vigente, sin cambio automático. Se conserva `prompt_version` de la biblioteca y se
  registra el modelo realmente solicitado en la trazabilidad.
- `DependenciasWriter` mantiene configuración/proveedor/contexto fuera del estado y
  exige coherencia con `MOCK_GEMINI`; producción sigue rechazando mocks.
  `DobleGemini.generar_sync` comparte cola y contadores con `generar` async.
- Errores, cancelación o deadline eliminan borrador/referencias y evaluaciones previas
  del update. La integración de estados terminales con el registro, las aristas y los
  consumidores finales pertenece a Issue 29. Pruebas: `backend/tests/test_writer.py`,
  con doble y transporte HTTP simulado del SDK; no acreditan Gemini/OCI reales.

### Critic (Issue 28)

- `critic(EstadoGrafo, DependenciasCritic)` revisa el borrador tipado y sus metadatos;
  reutiliza la validación de Writer para alcance y citas canónicas. Revalida ownership,
  hash y disponibilidad documental antes/después de cada llamada y entre reintentos.
  No modifica el contenido ni persiste ni marca `completed`.
- Separa extracción de afirmaciones, juicios factuales y rúbrica pedagógica. La extracción
  no recibe evidencia que pueda sesgarla a omitir errores. El backend asigna IDs y exige
  ubicaciones reales, un juicio por afirmación y referencias autorizadas para cada juicio.
  Calcula score/conteos con Faithfulness; el modelo no puede emitir score ni aprobación.
- Aplica las tres bandas de §19.3 y todos los bloqueos de §16.4: una afirmación sin respaldo,
  una contradicción, evaluación incompleta, cobertura parcial o visión insuficiente impiden
  aprobar aunque el promedio sea alto. Una dimensión `baja` bloquea; `media` conserva la
  semántica del contrato existente. Una extracción vacía es `no_evaluable`, con score nulo.
- Entrega afirmaciones fallidas con texto, ubicación, motivo y referencias a Writer. Si falta
  contexto, emite `solicitudes_evidencia` para Researcher. Los campos internos añadidos
  `destino_revision` y `solicitudes_evidencia` tienen defaults y no cambian HTTP v1.
  Una aprobación indica `finalizer`; Issue 29 conectará estos destinos con las aristas.
  Tras la tercera redacción no aprobada: `rejected_quality`, sin borrador ni evaluaciones.
- Quiz: excluye distractores del promedio factual y revisa todas las opciones en una solicitud
  separada. Exige una única respuesta defendible en el contexto del enunciado y explicación
  que refute las incorrectas; IDs ausentes/duplicados o citas inválidas son fallo técnico.
- El loader visual recibe workspace/documento/hash/chunk y devuelve `ImagenOriginal` con
  esos identificadores, bytes, MIME y reserva de tokens. Critic comprueba esa procedencia
  y adjunta la imagen original al SDK. Sin original/loader o con revisión insuficiente no
  aprueba; no reutiliza una aprobación visual anterior. La obtención/rasterización real
  se implementa en Issue 30 mediante `consulta.loader_critic(contexto)`: verifica
  fuente, índice y hash del PNG contra el manifest, revalida acceso y devuelve
  la página original rasterizada. Una interpretación ambigua bloquea aprobación.
- Jueces textuales/visuales usan el modelo de verificación configurado y transporte síncrono
  de `ClienteGeminiGeneracion`, siempre mediante `ctx.llamar`. Cada intento reserva TPM,
  cuota y presupuesto; el SDK mantiene un intento, sin otros bucles. Cancelación, deadline,
  cuota, respuesta inválida o indisponibilidad eliminan borrador y score del update terminal.
  El doble conserva entradas async y comparte lógica/contadores con las nuevas sync.
- Evidencia: `backend/tests/test_critic.py`, junto con Writer, Supervisor, prompts y Faithfulness.
  Son pruebas con dobles/SDK simulado; no acreditan ejecuciones Gemini/OCI reales.

### Integración interna del registro y el ejecutor

- SQLite migra automáticamente a v3: `jobs.generation_id` es opcional, único y
  referencia una generación. `enqueue(..., generation_id=...)` vincula ambas
  entidades; `running` y los estados terminales actualizan ambas en una transacción,
  junto con el evento. El resultado canónico sigue perteneciendo a Object Storage.
- `eventos_desde_job(job_id, ultimo_id)` recupera eventos comunes; `eventos_desde`
  conserva el filtro por generación. La ruta debe comprobar ownership antes de leer.
- Trabajo y evento inicial se aceptan atómicamente. Un fallo del ejecutor detiene
  nuevas admisiones y marca los pendientes como interrumpidos cuando SQLite está
  disponible; si no lo está, la recuperación ocurre al reiniciar el servicio.
- Los reintentos de Gemini ocurren en `ctx.llamar`, reservando cuota en cada
  intento y respetando cancelación, deadline y Retry-After. Un `ReintentableError`
  fuera de esa llamada falla el trabajo sin repetir el pipeline ni sus efectos.
- Las lápidas bloquean acceso mientras existan. `purga_despues_en` habilita limpieza,
  no restaura recursos; solo retirar la lápida tras purgar todos sus derivados y
  referencias reconstruibles. Las consultas también rechazan espacios vencidos.
- `get_storage_provider(configuracion=ajustes)` consume la configuración validada
  de la aplicación; sin inyección usa la misma carga de entorno y `.env`.
- El parser v3 cuenta tokens con el BPE local e interpreta form feed como LF para
  citas. La detección visual es heurística: ignora reglas/rellenos simples de tablas,
  pero conserva imágenes y diagramas aun cuando coexistan con texto seleccionable.
  La demo VCN mantiene pendiente su página 3 hasta integrar visión.
- Faithfulness admite `rangos_excluidos=[(inicio, fin), ...]` sobre el texto original
  (caracteres, fin exclusivo). Las exclusiones textuales ambiguas producen
  `no_evaluable`; el caller debe suministrar posiciones exactas para evitar alterar
  explicaciones que comparten palabras con un distractor.

### Estado y Supervisor del grafo (Issue 21, contrato interno 1.0)

- `core/agents/graph_state.py` define `EstadoGrafo`, modelo Pydantic compatible
  con el esquema de estado de LangGraph. Reutiliza `GenerateRequest`, evidencia
  y referencias tipadas, formatos pedagógicos, metadatos y evaluaciones existentes.
  Incluye IDs, hash de fuente, idiomas, alcance solicitado en `parametros.alcance`,
  cobertura, borrador, feedback, intento, presupuesto, deadline, estado, diagnóstico
  y referencias de persistencia. No cambia el contrato HTTP v1.
- `DependenciasGrafo` es contexto de runtime: contiene `ContextoEjecucion` y
  el lector de metadata documental autorizado. No pertenece a `EstadoGrafo` ni
  a un checkpoint; tokens, clientes SDK y locks tampoco son campos admitidos.
- `crear_estado_inicial(solicitud, generation_id=..., dependencias=...)` valida
  la entrada y aplica `supervisor(estado, dependencias)`. El nodo devuelve un
  update sin mutar el estado ni generar contenido. La consulta documental usa
  siempre el espacio del contexto de ejecución, con revalidación de cancelación.
- Parámetros/secciones/idiomas inválidos y documentos no `ready` dejan `failed`
  con diagnóstico. Inexistente y ajeno comparten `NOT_FOUND`; visión pendiente
  bloquea aunque la metadata diga `ready`. Cancelación y deadline siguen al worker.
  El idioma de origen normaliza variantes regionales y nunca pisa `idioma_salida`.
- Restricciones y rúbrica fijan formato, orientación del perfil, idiomas, citas,
  ejemplos etiquetados, tres redacciones y hasta veinte solicitudes. Score ≥0.85
  solo es un mínimo: todos los bloqueos y dimensiones deben superar la revisión.
- Issue 29 integrará estos updates con `runtime.context`, las aristas terminales
  y el registro operativo. Debe terminar ante `failed/cancelled`, sincronizar el
  contador con solicitudes reales/reintentos mediante `ctx.llamar` y persistir
  únicamente contenido aprobado. El contador serializado no aplica cuotas.
  `deadline` es monotónico del proceso; tras reinicio el trabajo se interrumpe.
- Evidencia: `backend/tests/test_supervisor.py` y snapshot JSON versionado.


### Recuperación MMR (Issue 18, contrato interno)

- `RetrieverMMR.recuperar(workspace_id, document_id, consulta, contexto=...)`
  delega en Chroma y en los embeddings síncronos de Issue 13. La consulta conserva
  su idioma y usa la preparación asimétrica compatible con el índice; no traduce
  mediante otra llamada generativa. `recuperar_demo` usa la colección de lectura aparte.
- `RETRIEVAL_K=5`, `RETRIEVAL_FETCH_K=15`, `RETRIEVAL_LAMBDA_MULT=0.7` y
  `RETRIEVAL_MAX_TOKENS=12000` son configurables. Se exige fetch_k >= k y un
  presupuesto positivo que no supere el límite de 12.000 tokens por borrador.
- Si el ámbito filtrado contiene como máximo los candidatos solicitados, se leen
  todos sus vectores con `get` y se ordenan por distancia coseno exacta. No se
  delega esa selección completa a HNSW, que podría omitir vecinos bajo filtros
  selectivos. Ámbitos mayores conservan la búsqueda aproximada. Ambos caminos
  mantienen los filtros previos y usan sólo el embedding ya calculado de consulta.
- MMR diversifica con los vectores de Chroma y devuelve `EvidenciaRecuperada`;
  score representa relevancia coseno, nunca fidelidad. Se deduplican IDs y textos
  equivalentes por espacios. Procedencia, hash y vectores inválidos son fallo técnico.
- `ResultadoRecuperacion` informa evidencia, tokens reales de su JSON con metadatos,
  chunks_omitidos y avisos. Un fragmento que no cabe se omite completo; no se recorta
  texto conservando una cita que aparentaría corresponder a otro contenido.
- El filtro opcional `seccion` se aplica antes de buscar candidatos, dentro del
  workspace/documento/versión autorizados, también sobre índices existentes de #17.
  `Chunk.seccion_id` distingue el ID del título visible. El chunker conserva
  `seccion:N` (línea del encabezado original) en MD/TXT y `pagina:N` en PDF.
  Una página es una ubicación, no un tema. Los encabezados repetidos tienen IDs
  distintos. Un índice heredado de MD ambiguo exige reconstrucción para acotar
  por sección; no se mezclan títulos iguales ni se adivinan sus límites.
  La referencia conserva sus valores originales (página y sección), sin inventar títulos.
- El llamador puede reducir presupuesto_tokens y exigir source_hash. El contexto
  se revalida tras la consulta y durante selección; fallos/cuotas/cancelación se propagan.
  Estas pruebas con Chroma nativo y embeddings simulados no acreditan Gemini real.


### Researcher y cobertura (Issue 27, contrato interno)

- `VectorStoreChroma.listar_chunks(..., contexto, source_hash)` devuelve el
  inventario privado de la versión autorizada, sin consumir Gemini. Comprueba
  hash, procedencia, lápidas, cancelación y cambios del índice durante la lectura.
- `DependenciasResearcher` inyecta DependenciasGrafo, RetrieverMMR y la versión
  del parser registrada por la ingestión. No se infiere esa versión desde el
  código instalado ni se serializan clientes en EstadoGrafo.
- `researcher` deriva el índice de secciones desde Chunk.seccion_id; exige que
  el registro preparado por Supervisor enumere los mismos IDs. Encabezados
  repetidos son secciones diferentes. `pagina:N` solo delimita ubicación de PDF,
  sin afirmar que una página sea un tema. El lector documental de Issue 19 debe
  usar estos mismos IDs al enumerar alcances.
- Planifica búsquedas deterministas con el título como datos y el foco de
  perfil/formato en el idioma de origen. La consulta registrada en cada evidencia
  indica ese foco de uso. No interpreta instrucciones del documento ni llama
  al LLM para redactar consultas. Las búsquedas conservan ContextoEjecucion.
- Prioriza un fragmento por sección solicitada; luego completa con diversidad.
  **Una sola unión deduplicada** comparte el máximo de 12.000 tokens por borrador,
  contado sobre JSON real. Cada consulta recibe el presupuesto restante. No se
  otorgan 12.000 tokens independientes a cada sección ni se confía en contadores
  de tokens aportados por un proveedor de retrieval.
- Una consulta sin cobertura puede ampliarse una vez. Las solicitudes de Critic
  se atienden antes de extras opcionales, reutilizando evidencia vigente y los
  contadores de redacciones/LLM; embeddings mantienen sus propias cuotas por modelo.
- Cobertura insuficiente o presupuesto incompatible producen rejected_quality
  con las secciones faltantes y la opción de acotar. Fallos de proveedor/cuota,
  índice inconsistente, fuente modificada, deadline o cancelación son técnicos.
  El update terminal elimina evidencia y borradores; nunca implica aprobación.
- Publica secciones_cubiertas y trazabilidad para Writer sin completar el grafo
  ni persistir. Issue 29 integrará el nodo y sus aristas terminales. Las pruebas
  usan Chroma nativo y dobles explícitos; el caso PDF VCN simula metadata ready
  solo para aislar el nodo y no acredita visión completada, OCI ni Gemini reales.
