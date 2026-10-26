# Revisión del PR #150 — 26 de octubre de 2026

La revisión conserva las mejoras del PR y corrige sus regresiones sin cambiar
la arquitectura ni agregar dependencias. La rama es
`feature/optimizaciones-critic-y-features`, con base en `develop`.

| Pedido | Comportamiento y comprobación |
|---|---|
| Preguntas generales y glosario | La cobertura responde a la consulta o definiciones; no exige todas las secciones. Paráfrasis y síntesis son válidas si se desprenden de la evidencia. Caso unitario con inventario de 15 secciones y evidencia parcial. |
| Citas del Critic | Citas cruzadas o ausentes producen falta de respaldo y feedback, sin fabricar evidencia ni abortar por ese motivo. Quiz pide corrección cuando las opciones no tienen citas válidas. |
| Fidelidad | Mismos controles para proveedor real y doble. Se elimina la conversión automática de `sin_evidencia` a `respaldada` y el descarte de hallazgos. Se conserva el mínimo 0.85 y la corrección de errores conocidos. |
| Modo básico | Chat y glosario validan citas, revalidan fuentes, entregan `no_evaluado` y evaluación nula. La UI permite elegirlo y muestra el aviso; caché e idempotencia separan modos. |
| Cantidad y tutorial | `cantidad_items` opcional de 1 a 25 para tarjetas, preguntas o pasos; se valida al recibir el borrador. Se conserva la mejora de tono del PR. |
| Recuperación y PDF | Se conservan `k=10`, `fetch_k=25`, hasta 100 páginas visuales y los demás límites. La UI informa el límite visual actualizado. |
| Errores | El código de ingestión procede del trabajo asociado, no de un campo inexistente. No se publica texto legacy sin validar. Los rechazos transmiten motivos públicos de calidad sin hechos del documento ni feedback privado del modelo. |
| CSV y espera | Se conservan BOM UTF-8 y spinners del PR. |
| Selector documental | Casillas de 1rem sin encogimiento, tipografía uniforme y truncado de nombres largos. |
| README | Enlace a la aplicación desplegada al comienzo. |

Los fallos estructurales del evaluador (IDs incompletos, ubicaciones inventadas,
JSON inválido) mantienen su diagnóstico técnico. No se promete que un LLM
apruebe cualquier pregunta general: una respuesta concreta aún debe estar
respaldada y cumplir la consulta.

## Validación local

| Comprobación | Resultado |
|---|---|
| Suite unitaria, separando el caso lento de captura | 1.813 aprobados, 52 omitidos; captura ejecutada en la siguiente corrida. |
| Chat, glosario y captura pedagógica aislada | 41 aprobados: los 40 casos de chat/glosario y el unitario de captura. Total unitario verificado: **1.814 aprobados**. |
| Critic con ambos indicadores de proveedor, regresiones y contratos de integración | 111 aprobados. Ambos caminos usan dobles explícitos; no son llamadas a Gemini real. |
| HTTP documental: fallo de parser, título, código y privacidad | 1 aprobado. |
| Frontend, suite completa | 142 aprobados en 15 archivos. |
| Compilación frontend | `tsc -b && vite build` correcto. |
| Ruff | `ruff check .` y `ruff format --check .` correctos (274 archivos). |
| OpenAPI | Snapshot regenerado, `--check` coincidente y `--validate` válido. |
| Diff | `git diff --check` correcto. |

Los resultados de selecciones superpuestas no se suman como pruebas distintas.
Los 52 omitidos requieren Docker Compose (45) o Caddy (7), no disponibles en
este equipo. No se modificaron ni debilitaron esos tests. La prueba de captura
se aisló temporalmente en la selección de ejecución, **sin agregar un skip al
código**, y luego pasó con sus validaciones originales.

Comandos principales, desde la raíz (Python del entorno `.venv`):

```text
python -m pytest -m unit -k "not test_captura_con_api_grafo_chroma_y_storage_reales_y_gemini_simulado" -q --disable-warnings --junitxml=.data/pr150-unit-tests.xml
python -m pytest backend/tests/test_chat.py backend/tests/test_glossary.py backend/tests/test_pedagogical_evaluation.py::test_captura_con_api_grafo_chroma_y_storage_reales_y_gemini_simulado -q --disable-warnings --junitxml=.data/pr150-integration-tests.xml
python -m pytest backend/tests/test_critic_real_provider.py backend/tests/test_review_regressions.py backend/tests/integration/test_contract.py -q --disable-warnings
python -m pytest backend/tests/test_document_http.py -k fallo_parser_conserva -q --disable-warnings
```

En `frontend/`: `npm test` y `npm run build`. Las pruebas HTTP necesitaron salir
del sandbox de Windows porque su conexión local `socketpair` quedaba detenida
al iniciar TestClient; no se cambió la aplicación para sortearlo.

### Límites reales

- Se intentó `pytest -q` global, pero no se completó esa corrida por duración.
  Sí se completaron todos los unitarios ejecutables y las selecciones anteriores;
  esto no acredita toda la suite de integración del repositorio.
- La captura aislada emitió un volcado diagnóstico de `faulthandler` a los 90 s,
  con esperas en SQLite/presupuesto operativo, y después terminó aprobada.
  No se diagnosticó su causa definitiva ni se cambió la concurrencia del sistema.
- La comprobación visual en navegador quedó pendiente: la política del navegador
  rechazó abrir el archivo local de prueba (`file://`). No se intentó eludir ese
  bloqueo. La corrección del selector se verificó mediante revisión de CSS y la
  suite funcional; no se atribuye una comprobación visual realizada.
- No se ejecutaron Gemini/OCI reales, despliegues ni aceptación pública. El enlace
  de Render fue el proporcionado por el usuario. No se actualizaron capturas.
- La implementación y documentación se mantienen en la misma rama del PR #150.

## Archivos de la revisión

- [.env.example](../.env.example)
- [README.md](../README.md)
- [backend/app/api/routes/documents.py](../backend/app/api/routes/documents.py)
- [backend/app/chat/service.py](../backend/app/chat/service.py)
- [backend/app/core/agents/critic.py](../backend/app/core/agents/critic.py)
- [backend/app/core/agents/draft_validation.py](../backend/app/core/agents/draft_validation.py)
- [backend/app/core/agents/finalizer.py](../backend/app/core/agents/finalizer.py)
- [backend/app/core/agents/graph.py](../backend/app/core/agents/graph.py)
- [backend/app/core/agents/graph_state.py](../backend/app/core/agents/graph_state.py)
- [backend/app/core/agents/prompts.py](../backend/app/core/agents/prompts.py)
- [backend/app/core/agents/researcher.py](../backend/app/core/agents/researcher.py)
- [backend/app/generations/service.py](../backend/app/generations/service.py)
- [backend/app/glossary/service.py](../backend/app/glossary/service.py)
- [backend/app/jobs/manager.py](../backend/app/jobs/manager.py)
- [backend/app/jobs/store.py](../backend/app/jobs/store.py)
- [backend/app/schemas/chat.py](../backend/app/schemas/chat.py)
- [backend/app/schemas/glossary.py](../backend/app/schemas/glossary.py)
- [backend/app/schemas/responses.py](../backend/app/schemas/responses.py)
- [backend/tests/integration/test_contract.py](../backend/tests/integration/test_contract.py)
- [backend/tests/test_chat.py](../backend/tests/test_chat.py)
- [backend/tests/test_critic.py](../backend/tests/test_critic.py)
- [backend/tests/test_critic_real_provider.py](../backend/tests/test_critic_real_provider.py)
- [backend/tests/test_document_http.py](../backend/tests/test_document_http.py)
- [backend/tests/test_glossary.py](../backend/tests/test_glossary.py)
- [backend/tests/test_review_regressions.py](../backend/tests/test_review_regressions.py)
- [backend/tests/test_writer.py](../backend/tests/test_writer.py)
- [decisiones_proyecto.md](../decisiones_proyecto.md)
- [docs/api_reference.md](../docs/api_reference.md)
- [docs/contratos-api.md](../docs/contratos-api.md)
- [docs/issues/fase-3-multiagente-fidelidad.md](../docs/issues/fase-3-multiagente-fidelidad.md)
- [docs/modos-generacion.md](../docs/modos-generacion.md)
- [docs/openapi.json](../docs/openapi.json)
- [docs/plan-implementacion.md](../docs/plan-implementacion.md)
- [docs/revision-pr-150.md](../docs/revision-pr-150.md)
- [frontend/src/App.tsx](../frontend/src/App.tsx)
- [frontend/src/api/types.ts](../frontend/src/api/types.ts)
- [frontend/src/components/StudyAndHistoryViews.tsx](../frontend/src/components/StudyAndHistoryViews.tsx)
- [frontend/src/flows/review150.test.tsx](../frontend/src/flows/review150.test.tsx)
- [frontend/src/flows/validators.ts](../frontend/src/flows/validators.ts)
- [frontend/src/i18n/en.ts](../frontend/src/i18n/en.ts)
- [frontend/src/i18n/es.ts](../frontend/src/i18n/es.ts)
- [frontend/src/i18n/pt.ts](../frontend/src/i18n/pt.ts)
- [frontend/src/styles/oracle-theme.css](../frontend/src/styles/oracle-theme.css)
