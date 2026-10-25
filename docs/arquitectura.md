# Arquitectura implementada de NuevaMente

Base contrastada: `develop` / HEAD `f1f9ae6`, 21/10/2026. Esta descripción de
componentes corresponde a la etapa B del #57; no acredita una instalación limpia,
proveedores ejecutados de nuevo ni despliegue público. Las decisiones de alcance
están en [decisiones_proyecto.md](../decisiones_proyecto.md) y los datos de intercambio
en [contratos](contratos-api.md) y [modos de generación](modos-generacion.md).

Los dos diagramas resumidos se mantienen en el README:
[componentes](../README.md#arquitectura-implementada) y
[ingestión, RAG y generación](../README.md#del-documento-al-material-de-estudio).
Las tablas siguientes vinculan sus cajas y transiciones con código existente.

## 1. Contexto del sistema

El navegador ejecuta React y se comunica por HTTP/SSE con FastAPI. Los documentos
admitidos son públicos o simulados: PDF, Markdown, TXT y texto pegado. El backend
controla acceso por espacio, procesamiento, proveedores y persistencia. El código
de recuperación permite volver al espacio sin registro por correo; los tokens
permanecen en memoria en React. Ver [guía del frontend](../frontend/README.md).

La ejecución local implementada puede usar Gemini/OCI reales con configuración
explícita o dobles inyectados para pruebas. `MOCK_OCI=1` selecciona almacenamiento
local en desarrollo/CI. `MOCK_GEMINI=1` requiere además inyectar los proveedores;
no crea un simulador funcional por sí solo. Véanse la [configuración](../backend/app/config.py),
el [ensamblado de la API](../backend/app/main.py), la
[factoría de embeddings](../backend/app/core/rag/embeddings.py) y el
[helper de verificación aislada](../backend/tests/integration/react_backend.py).

## 2. Diagrama de contenedores

El diagrama de componentes del README representa responsabilidades de ejecución,
no contenedores adicionales: worker, RAG y agentes pertenecen al backend. Compose
contiene dos servicios, `backend` y `frontend`; solo el backend monta `backend_data`.

| Caja del diagrama | Implementación | Responsabilidad |
|---|---|---|
| React / TypeScript / Vite (`UI`) | [App.tsx](../frontend/src/App.tsx), [cliente HTTP](../frontend/src/api/client.ts), [Vite](../frontend/vite.config.ts), [servidor del build](../frontend/entrypoint.py) | UI; proxy `/api` al backend en desarrollo o build local |
| FastAPI (`API`) | [main.py](../backend/app/main.py), [rutas](../backend/app/api/routes/), [sesiones](../backend/app/session/manager.py), [seguridad](../backend/app/api/security.py) | Componer servicios, validar sesión/ownership y admitir solicitudes |
| Worker común (`W`) | [manager.py](../backend/app/jobs/manager.py), [admisión](../backend/app/jobs/admission.py), [cuotas](../backend/app/jobs/quotas.py) | Cola compartida por ingestión, generación, chat y glosario; cancelación, deadlines y cuotas |
| Ingestión y RAG (`RAG`) | [runtime documental](../backend/app/documents/runtime.py), [ingestión](../backend/app/documents/ingestion.py), [retriever](../backend/app/core/rag/retriever.py) | Preparar documentos y recuperar evidencia autorizada |
| Generación (`GEN`) | [servicio](../backend/app/generations/service.py), [grafo](../backend/app/core/agents/graph.py), [básico](../backend/app/core/agents/basic.py) | Elegir el recorrido de cada nueva adaptación |
| Chroma (`C`) | [vectorstore.py](../backend/app/core/rag/vectorstore.py) | Índice persistente local y catálogo auxiliar; filtros por espacio/documento |
| SQLite operativo (`DB`) | [store.py](../backend/app/jobs/store.py) | Sesiones, trabajos, eventos, idempotencia y efectos durables de limpieza |
| StorageProvider (`S`, `OCI`, `MOCK`) | [interfaz](../backend/app/storage/provider.py), [selección y mock](../backend/app/storage/oci_storage.py), [OCI real](../backend/app/storage/oci_real.py) | Selección al arranque; nunca fallback ante fallos de OCI |
| Gemini (`AI`, `LLM`) | [embeddings](../backend/app/core/rag/embeddings.py), [multimodal](../backend/app/core/rag/multimodal.py), [generación/verificación](../backend/app/core/agents/gemini_generation.py) | Adaptadores síncronos usados bajo controles del worker |

Las cajas Gemini separan funciones, no exigen cuentas o modelos distintos.
Modelos, dimensiones y cuotas dependen de la configuración. El Critic hace llamadas
separadas del Writer aunque compartan modelo. En pruebas los adaptadores reciben
dobles explícitos. Contenedores y puertos efectivos: [Compose](../docker-compose.yml).

## 3. Flujo de datos extremo a extremo

| Transición del diagrama de flujo | Módulos y condición real |
|---|---|
| Entrada → original confirmado (`D`, `O`) | [ruta documental](../backend/app/api/routes/documents.py) y [admission.py](../backend/app/documents/admission.py): validación, intención idempotente, original/manifiestos verificados antes de procesar |
| Ingestión → índice → `ready` (`I`, `C`, `READY`) | [ingestion.py](../backend/app/documents/ingestion.py): extracción, cobertura textual/visual, inventario indexado y manifiesto confirmado; páginas visuales pendientes conservan `processing` |
| Nueva solicitud → modo (`N`, `M`) | [ruta de generación](../backend/app/api/routes/generations.py), [ServicioGeneraciones](../backend/app/generations/service.py): nueva intención y trabajo en el gestor existente; `ready` no dispara generación automática |
| Básico → paquete no evaluado (`B`, `V`) | [basic.py](../backend/app/core/agents/basic.py), [draft_validation.py](../backend/app/core/agents/draft_validation.py), [variantes de respuesta](../backend/app/schemas/responses.py) |
| Multiagente → revisión/finalización (`S`, `R`, `W`, `CR`, `F`, `REJ`) | [graph.py](../backend/app/core/agents/graph.py) y nodos detallados en sección 5 |
| Paquete → persistencia → `completed` (`P`, `DONE`) | `_persistir` en [service.py](../backend/app/generations/service.py): guarda el canónico, contrasta bytes, prepara exportaciones y revalida acceso/fuente; ambos recorridos exigen confirmación |
| Fallo técnico (`ERR`) | [gestor](../backend/app/jobs/manager.py), [grafo](../backend/app/core/agents/graph.py) y [básico](../backend/app/core/agents/basic.py): diagnóstico seguro sin paquete publicable |

El diagrama resume el recorrido exitoso y los fallos principales. Cancelación,
deadlines, cuota y errores de validación/autorización también pueden interrumpirlo;
no se infiere aprobación ni se entrega un borrador por esas salidas. Los eventos
y las lecturas de estado permiten distinguir `failed`, `cancelled` y `rejected_quality`.

## 4. Pipeline RAG

La extracción textual corresponde a [parser.py](../backend/app/core/rag/parser.py).
Las páginas visuales se rasterizan en [raster.py](../backend/app/core/rag/raster.py)
y sus interpretaciones se mantienen separadas del texto en
[multimodal.py](../backend/app/core/rag/multimodal.py), con acceso a la página original.
[Ingestión](../backend/app/documents/ingestion.py) coordina la cobertura antes de `ready`.

[chunker.py](../backend/app/core/rag/chunker.py) segmenta respetando estructura y
ubicaciones; [tokenizer.py](../backend/app/core/rag/tokenizer.py) mide los presupuestos.
Los embeddings se calculan después para indexar en
[Chroma](../backend/app/core/rag/vectorstore.py). La recuperación
[MMR](../backend/app/core/rag/retriever.py) filtra espacio/documento, deduplica y
acota la evidencia. [Researcher](../backend/app/core/agents/researcher.py) busca por
objetivos y cobertura dentro del presupuesto. Ver pruebas de
[ingestión](../backend/tests/test_document_ingestion.py),
[segmentación](../backend/tests/test_chunker.py), [índice](../backend/tests/test_vectorstore.py)
y [recuperación](../backend/tests/test_retriever.py).

## 5. Grafo multi-agente (LangGraph)

[graph.py](../backend/app/core/agents/graph.py) conecta los nodos y ejecuta el grafo
síncrono dentro del worker; no crea otra cola.

| Nodo | Módulo | Función y salida |
|---|---|---|
| Supervisor | [supervisor.py](../backend/app/core/agents/supervisor.py) | Determinista: autorización, versión de fuente, parámetros, restricciones y rúbrica |
| Researcher | [researcher.py](../backend/app/core/agents/researcher.py) | Recupera evidencia y comprueba cobertura |
| Writer | [writer.py](../backend/app/core/agents/writer.py) | Redacta salida tipada; máximo tres redacciones totales, incluida la inicial |
| Critic | [critic.py](../backend/app/core/agents/critic.py) | Revisión factual, pedagógica y visual; vuelve a Writer para corregir o a Researcher si pide evidencia y quedan redacciones/presupuesto |
| Finalizer | [finalizer.py](../backend/app/core/agents/finalizer.py) | Empaqueta el contenido aprobado sin nuevas afirmaciones educativas |

Al agotarse las redacciones sin aprobación, termina `rejected_quality`. Un fallo
técnico del evaluador es `failed`, sin sustituir el juicio ni activar Básico.
La función `_finalizar` del grafo revalida la fuente con Supervisor, llama al
Finalizer, persiste y exige la confirmación antes de `completed`.
Cobertura existente: [test_graph.py](../backend/tests/test_graph.py) y
[test_critic.py](../backend/tests/test_critic.py).

El [recorrido básico](../backend/app/core/agents/basic.py) reutiliza Supervisor,
Researcher y Writer como funciones, sin invocar LangGraph ni Critic. Limita a una
redacción, valida estructura y citas y construye `BasicPedagogicalOutput` con
`status=no_evaluado`, evaluación `no_realizada` y métricas nulas. No ejecuta el
Finalizer multiagente ni atribuye aprobación independiente. Comparte persistencia
y controles del worker; pruebas en [test_basic_mode.py](../backend/tests/test_basic_mode.py).

## 6. Verificación de fidelidad

[faithfulness.py](../backend/app/core/faithfulness/faithfulness.py) implementa la
metodología inspirada en Ragas: descompone afirmaciones y contrasta su respaldo
con evidencia. Critic integra ese resultado con cobertura, rúbrica y revisión
visual cuando corresponde. El score mínimo no permite publicar errores conocidos;
la aprobación exige superar todos los controles. Una evaluación incompleta o
vacía no recibe un score inventado. Política: [decisiones §19](../decisiones_proyecto.md#19-mecanismo-anti-alucinación).
El básico no realiza esta evaluación. Pruebas: [test_faithfulness.py](../backend/tests/test_faithfulness.py).

## 7. Persistencia: OCI Object Storage + estado local

| Capa | Responsabilidad y límite |
|---|---|
| OCI privado o mock explícito | Originales, manifiestos, paquetes de ambos modos, progreso recuperable y exportaciones; `persistencia.provider` distingue `oci` de `mock` |
| SQLite operativo local | Sesiones, trabajos, eventos, idempotencia y obligaciones durables; conservar el volumen no equivale a reconstruirlo íntegramente desde OCI |
| Chroma local | Índice de búsqueda; existen funciones de reconstrucción, con límites operativos para pérdida selectiva registrados en #58 |

Las claves las genera [object_keys.py](../backend/app/storage/object_keys.py).
[ServicioGeneraciones._persistir](../backend/app/generations/service.py) devuelve
`status_upload=completado` también con mock, acompañado de `provider=mock`: nunca
acredita una escritura OCI. Un fallo de guardado impide `completed`; el reintento
de persistencia utiliza el material retenido sin volver a redactarlo.
Los paquetes básicos guardados conservan su ausencia de evaluación.

La selección del proveedor y sus controles están en [oci_storage.py](../backend/app/storage/oci_storage.py),
[oci_real.py](../backend/app/storage/oci_real.py) y [oci_budget.py](../backend/app/storage/oci_budget.py).
Cuotas, costos y operación se mantienen en [gobernanza](gobernanza-costos.md) y el
[runbook](despliegue-oci.md); no se deduce costo cero de una bandera.
Los huecos 17.1/17.4 y la revisión humana del runbook constan en
[verificación #58](verificacion-issue-58.md#capacidades-actuales-y-aceptación-pendiente).

## 8. Progreso en vivo (SSE) y trabajos

[GestorTrabajos](../backend/app/jobs/manager.py) comparte admisión y controles de
trabajos intensivos. Las [rutas comunes](../backend/app/api/routes/jobs.py) y las
[de generación](../backend/app/api/routes/generations.py) exponen estado, eventos y
cancelación. React usa [fetch SSE autenticado](../frontend/src/api/sse.ts) y
[reconciliación de progreso](../frontend/src/flows/generationProgress.ts), sin
reenviar la generación al reconectar. Detalle y límites: [verificación #33](verificacion-issue-33.md).

El material usa [renderizadores de estudio](../frontend/src/components/FormatRenderers.tsx)
y [vistas de estudio/historial](../frontend/src/components/StudyAndHistoryViews.tsx).
El [progreso](../backend/app/progress/service.py) y el
[catálogo de generaciones](../backend/app/generations/catalog.py) pertenecen al
espacio recuperable. [ExportDownloads](../frontend/src/components/ExportDownloads.tsx)
consume el [servicio de exportaciones](../backend/app/exports/router.py): deriva
JSON/MD/PDF y CSV/TSV/APKG para flashcards del contenido guardado, conservando el modo.
Ver [cobertura React vigente](verificacion-issue-139.md) y [exportaciones](verificacion-issue-46.md).

## 9. Despliegue en OCI Compute Always Free

[Compose](../docker-compose.yml) sirve API/UI en loopback. La configuración de
[Caddy](../infra/caddy/Caddyfile) y el [runbook #58](despliegue-oci.md) describen la
publicación HTTPS con SSE. Su existencia no acredita VM, DNS o URL pública
operativos. La aceptación externa continúa en [#133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133).
Las contradicciones entre casillas de GitHub e informes versionados se conservan
explícitas en [trazabilidad #57](verificacion-issue-57.md).

## 10. Alcance y pendientes documentales

La [biblioteca de ejemplos](ejemplos-demo.md) permite consultar materiales ya
publicados; no implementa generar directamente desde las fuentes compartidas.
Ese puente sigue pendiente y no se sustituye por carga privada ni por un snapshot.

Esta etapa no valida instalación, captura nuevas pantallas ni completa la matriz
de cumplimiento. C/D/E y la revisión humana siguen pendientes; #56 abierto no
bloquea esta descripción. La evaluación humana de #60 no se infiere de sus
herramientas ni del estado cerrado del issue: ver [registro #57](verificacion-issue-57.md).
