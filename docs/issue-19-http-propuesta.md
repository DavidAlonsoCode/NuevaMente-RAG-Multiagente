# Issue 19 — etapas 4 HTTP, 5B DELETE y H03 demo; trazabilidad contract-change

## H03: decisiones delegadas y aprobadas técnicamente por el equipo

Las tres rutas GET `/api/demo/documents`, `/{document_id}` y
`/{document_id}/sources/{chunk_id}` están implementadas localmente con Bearer,
ámbito `biblioteca-demo`, inventario OCI y schemas públicos existentes. El detalle
incluye secciones estables; las lecturas no crean embeddings, jobs ni escrituras.
Cursor demo firmado por ID, separado del privado; disponibilidad exige original,
metadata/cobertura y evidencia `publicacion_demo` compatible con Chroma íntegro.
El esquema interno opcional permite que #49 exprese su publicación confirmada;
H03 no prepara la biblioteca desplegada ni ejecuta mantenimiento.

La autorización del usuario comunica delegación vigente de decisiones técnicas y
contractuales. Las precisiones están en la sección «Catálogo demo de solo lectura»
de contratos-api.md y conservan la trazabilidad `contract-change` API/UI/AGT/RAG
para integración. No modifica el contrato de generación ni implementa #25/#30/#49.
Excepciones explícitas: GET demo no renueva retención; no se exige admisión privada
para una fuente compartida; el cursor demo es compartible entre sesiones válidas.

---

Dirección vigente: respuesta del equipo comunicada por el usuario y autorización
de registrar upload, listado, detalle y fuentes. Las cuatro rutas están registradas
en `crear_app`; desde etapa 5B también está registrado DELETE, según la precisión
al final de este documento. Las secciones de resultados de etapa 4 conservan
su carácter histórico. Esta implementación local no sustituye
el PR `contract-change` y revisión API+UI+AGT+RAG: no se modificó GitHub.

## Contrato previo y precisiones nuevas

Ya establecidos en `contratos-api.md`: rutas, sesión Bearer, ownership por
workspace, 202 processing con seguimiento de jobs, JSON de upload, multipart
file/título opcional, límites, envolvente, códigos existentes e idempotencia.

Las precisiones implementadas para revisión están documentadas completas en la
sección «Issue 19 — precisiones HTTP propuestas» de ese contrato:

| Método/ruta | Request | Respuesta | Errores previstos |
|---|---|---|---|
| POST /api/documents/upload | JSON documento_titulo/documento_contenido o multipart file/titulo; Idempotency-Key opcional | 202 document_id/status/job_id/status_url/events_url/cancel_url | 400,401,409,413,422,429,503; 500 inesperado seguro |
| GET /api/documents | limit=20 (1..100), cursor firmado opcional | 200 items/next_cursor | 400 cursor,401,422,503; 500 inesperado seguro |
| GET /api/documents/{document_id} | ID; sesión Bearer | 200 document_id/titulo/status/metadata/cobertura/error | 401,404,503; 500 inesperado seguro |
| GET /api/documents/{document_id}/sources/{chunk_id} | IDs; sesión Bearer; un fragmento | 200 document_id/chunk_id/texto/pagina/seccion/seccion_id | 401,404,409,503; 500 inesperado seguro |

Todos exigen sesión vigente; recursos ajenos y retirados responden 404. No hay
rutas públicas adicionales para secciones/chunks, progreso o visión. Campos,
nulabilidad, nombre multipart `titulo`, cursor, límites de transporte, tiempos
y errores técnicos de lectura deben revisarse mediante `contract-change`.

Ejemplo JSON:

```json
{"documento_titulo":"Redes","documento_contenido":"Contenido textual del documento..."}
```

Multipart: enviar un archivo `file` y opcionalmente `titulo`, con Bearer y la
misma Idempotency-Key para reintentos de una intención. Ejemplo de fuente:

```json
{"document_id":"doc_ejemplo","chunk_id":"chunk_ejemplo","texto":"Una VCN conecta subredes.","pagina":null,"seccion":"Redes","seccion_id":"seccion:1"}
```

El ID es ilustrativo; se usa el ID estructural real del chunk, nunca un título.
Los ejemplos mínimos no acreditan el umbral de extracción del parser. El detalle
distingue null desconocido de cero o [] conocidos, y no publica configuración
interna del parser ni filas SQL.

## Decisiones internas y garantías comprobables

- Consulta autoriza ownership antes de storage. Nueva admisión: fila encolada
  y ambos flags explícitamente true. Legacy: sin fila y ambos flags null. H02
  permite listado/detalle de documentos restaurados desde OCI con procedencia
  verificada, sin fabricar una admisión histórica ni tratarlos como legacy.
  Fuentes y ready exigen confirmación de un nuevo trabajo real de reconstrucción;
  marcadores ambiguos, fuentes inconsistentes o lápidas bloquean el acceso.
  No se recuperan jobs/eventos históricos, idempotencia, contadores perdidos ni
  mutaciones nunca sincronizadas; véase la sección H02 del README documental.
- Fuentes: ready local/remoto, metadata y cobertura coherentes/completas,
  inventario del hash autorizado y revalidación después de Chroma. El índice
  no acredita publicación; no se calculan embeddings al leer.
  Legacy sin metadata/cobertura verificable conserva lecturas de estado,
  pero no habilita contenido. Marcadores booleanos estrictos, sin coerción
  de strings/números; en PDF se comprueba cobertura de todas las páginas.
- Paginación HTTP keyset ascendente por fecha UTC e ID. Firma con clave aleatoria
  independiente de sesiones, en `DATA_DIR/document-cursor.key`, mismo volumen
  local que SQLite. No se exporta ni registra. Una clave inválida falla visible,
  no se rota silenciosamente. Perder el volumen invalida cursores anteriores.
  El ancla retirada no invalida una página. Filtrado previo y lookahead conservan
  elementos; retiradas durante revalidación pueden producir páginas cortas con
  cursor. No es snapshot. La interfaz interna anterior por ID se conserva;
  HTTP usa `pagina` y no expone ese cursor interno.
- Admisión conserva esperas finitas sin transacciones SQLite durante I/O.
  Reintentos conservan IDs y no duplican escrituras/trabajos. Si el commit se
  aplicó pero su retorno falló, se verifica el registro antes de compensar;
  si no es verificable, no se sobrescribe como fallido. Se conserva diagnóstico
  primario y errores secundarios seguros. EfectosDocumentales y el
  sincronizador existentes reconcilian estados indeterminados y reintentan
  efectos durables con SQLite conservada; no acreditan confirmación ni limpieza
  física sin verificarla.
- Multipart usa parser streaming Starlette, máximo un archivo y un campo;
  cierre de temporales incluso ante errores. Transporte y archivo tienen límites
  separados; JSON permite escapes Unicode y valida bytes UTF-8 reales.
  `python-multipart==0.0.32` fijado; no cambia parser/chunker/Chroma.
- Runtime reutiliza SQLite, StorageProvider, gestor, cuotas e índice existentes.
  Cuotas explícitas conjuntas EMBEDDING_QUOTA_RPM/TPM/RPD o inyección del contador
  común; sin cuotas hay metadata y upload 503 sin reserva. Mock exige doble
  explícito. No se eligen cuotas de producción.
  Al cerrar, se espera al worker y se libera el cliente Chroma mediante su
  interfaz pública close, sin reset, borrado ni cambio del algoritmo. El cierre
  repetido es seguro; al reabrir se conservan documentos, chunks y cursores.

## Decisiones pendientes reales

1. Revisión `contract-change` de estas precisiones; autorización para implementar
   no las declara automáticamente congeladas por el equipo.
2. Código nuevo `WORKSPACE_BUSY`: pendiente, **no implementado**; HTTP usa 429
   RATE_LIMITED vigente. La interfaz interna conserva 409 con motivo específico
   que el adaptador HTTP traduce, sin romper consumidores.
3. Identificar/reanudar admisión incierta cuyo request original no tenía clave:
   sin política pública nueva. 503 informa si había clave, no entrega IDs ni
   promete reintento seguro o fracaso definitivo.
4. Cuotas verificadas de la cuenta/modelo del equipo y activación por el operador.
   No se amplía despliegue #49/#50 ni se inventan límites.
5. DELETE etapa 5B: implementado localmente según dirección 202 cleanup_pending
   si resta limpieza, 204 si terminó; sujeto a revisión de contrato.

## Pendientes registrados al terminar etapa 4 (histórico)

- Reconciliación durable de admisiones indeterminadas: especialmente SQLite
  encolada con manifiesto true/false; 503 no lo resuelve. Intención, IDs, caché y
  evidencia se conservan; puede seguir dando 503 hasta reconciliarse.
- Recuperación y compensación durable del pipeline, incluida confirmación
  perdida entre manifiesto final y terminal SQLite y fallos de compensación.
- Limpieza reintentable de originales, manifiestos e índices tras reinicio.
- DELETE con tombstones, cancelación y protección contra republicación.
- Revisión de todo cambio público mediante contract-change.
- Evaluación del I/O externo del observador terminal bajo el lock del gestor.

Al cierre de etapa 4 no estaban resueltas la reconciliación, la recuperación de
documentos nuevos sin fila ni la limpieza física. Las etapas 5A/5B y H02 posteriores
implementan esas garantías en su alcance documentado, sin transacción distribuida
ni recuperación de historia perdida. Esta sección conserva el registro histórico
de etapa 4; no se usaron OCI/Gemini reales ni operaciones Git/GitHub de publicación.

## Validación de etapa 4 (histórico)

Pruebas previas de admisión/preparación: 108 aprobadas inicialmente. Nuevas pruebas
HTTP en `backend/tests/test_document_http.py`: TestClient, storage mock, DobleGemini
y Chroma temporal. Solo se actualiza la prueba anterior de ausencia de rutas, con
autorización explícita: ahora verifica publicación, ausencia de DELETE y exclusión
de campos internos. El resto no se modifica.

Los resultados finales se informan al terminar; referencia comparable anterior:
1.234 aprobados y 273 warnings. Ejecuciones mediante `baseline.py`, almacenamiento
fuera del repositorio y mocks explícitos.

La primera suite completa detectó siete fallos: un clasificador anterior que
confundía error=null del detalle con una envolvente y seis errores por archivos
abiertos acumulados en lifespans Chroma. Los ejemplos nuevos declaran su schema
en la etiqueta del bloque JSON y tienen validación propia; no se modifica el
test anterior. Se corrige el cierre real del cliente y se comprueba reapertura
sin pérdida ni nuevos embeddings. La tanda focalizada posterior aprobó 149 tests
(HTTP, schemas y Writer con transporte simulado). El primer resultado fallido
permanece registrado; no se ocultan fallos ni se modifican tests para pasarlo.

### Resultado final de esta etapa

- Suite comparable: **1.317 aprobados, 0 fallos, 0 errores y 0 skips**, 278,15 s.
- Conserva todos los IDs de los 1.086 casos de línea base y los 1.234 de
  preparación previa; suma **83 casos HTTP nuevos**, incluidos ejemplos y
  ciclo de vida. Solo cambió el test de ausencia de rutas autorizado.
- 435 warnings: **162 adicionales** respecto de 273; todos los adicionales
  son el aviso conocido de configuración legacy de embeddings de Chroma.
  La deprecación Starlette/httpx preexistía. No se silencian warnings.
- Ruff check, Ruff format --check (135 archivos) y git diff --check aprobados.
- Ejecución final: `baseline.py pytest stage4-http-full-confirmed -q`, con
  SQLite/storage/Chroma temporales fuera del repositorio y mocks explícitos.
  Logs y JUnit permanecen en el directorio externo de la línea base.
- Tandas focalizadas: 215 tests documentales aprobados; 149 HTTP/schemas/Writer
  después de corregir el cierre; cuatro casos finales de acceso/reapertura
  aprobados, antes de la suite completa final.
- La comparación con las auditorías preservadas confirma que los tests de
  admisión e ingestión, el pipeline ingestion.py y los schemas internos
  documents.py no cambiaron; tampoco sus hashes.
- Git sigue en feature/issue-19-document-endpoints, HEAD/develop/origin-develop
  0a379f8e5a89eb86aed833b31155ed5fbd22b3dc. Sin staging, commits ni cambios remotos.
- Se detiene la implementación antes de DELETE y reconciliación; todos los
  pendientes obligatorios anteriores conservan su estado pendiente.
## Etapa 5B: DELETE registrado localmente, pendiente de contract-change

La dirección vigente del equipo autoriza DELETE autenticado por workspace. Se
propone 202 `{"cleanup_pending":true}` tras commit de retirada con efectos
pendientes, 204 sin body tras limpieza verificada y 404 para ajenos/inexistentes
o repetición de retirada. Los GET del documento retirado devuelven 404. Esta
precisión requiere revisión `contract-change`; no implica purga inmediata ni
recuperación integral tras pérdida de SQLite. El detalle de garantías y recursos
está en `docs/contratos-api.md`, sección DELETE documental de etapa 5B.
