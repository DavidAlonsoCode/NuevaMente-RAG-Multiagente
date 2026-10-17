# Verificación del complemento 33.1

## Alcance

Implementación y contrato en [modos de generación](modos-generacion.md).
Las pruebas usan Gemini y almacenamiento **simulados explícitamente**. No
acreditan conectividad con proveedores reales, ahorro medido ni URL pública.
La aceptación externa continúa en #133; #60 conserva su evaluación humana.

## Evidencia automatizada

Corridas locales completas de los grupos pertinentes: 12 casos del modo básico
(11 en lote y cancelación repetida tras corregir su expectativa HTTP a 200),
ocho de referencia API y 21 de compatibilidad histórica. Estas últimas mantienen
igualdad exacta de campos e incorporan el nuevo valor predeterminado, sin cambiar
el cliente Streamlit conservado. La corrida amplia de Windows se interrumpió y
no se considera aprobada. La suite completa y los contenedores se contrastan con
los checks del [PR #137](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/pull/137),
que identifica el commit evaluado.

- `backend/tests/test_basic_mode.py`: los cinco formatos sobre API, worker,
  recuperación RAG e índice reales con proveedores dobles; cero solicitudes al
  juez en básico. Conserva la vista estudiante del quiz y su feedback/progreso
  determinista. Cubre cambios en ambos sentidos sin reingesta, compatibilidad
  1.0, idempotencia, exportaciones, rechazo de métricas falsificadas, JSON inválido,
  cancelación cooperativa, reintento de persistencia y recuperación desde un
  almacenamiento simulado hacia SQLite vacío sin restaurar tokens.
- `frontend/src/flows/basicMode.test.tsx`: distingue 1.0/1.1 y rechaza aprobación
  o métricas falsificadas; presentación ES/EN/PT sin sello ni score para básico;
  seleccionar otro modo no genera ni vuelve a cargar el documento; progreso
  básico sin promesas de revisión. La suite React aprobó **67 pruebas** y el build
  de producción pasó TypeScript y Vite.
- `backend/tests/integration/test_react_caddy.py`: agrega el recorrido de ambos
  modos con un solo upload, resultados independientes y control de ancho 320 px.
  Requiere Caddy/TLS y navegador reales locales; Gemini/OCI siguen simulados.
  Su ejecución corresponde a los jobs Caddy de CI, no a un despliegue público.
- El snapshot OpenAPI incluye las variantes canónicas y estudiante. La referencia
  mantiene 37 operaciones y siete bloques curl; los ejemplos anteriores siguen
  solicitando el contrato 1.0.

## Comprobación manual en navegador

El 16/10/2026 se cargó un texto sintético una sola vez, se generó primero en
básico y luego en multiagente desde el mismo documento. Cambiar el selector no
transformó el resultado básico existente. El historial conservó dos materiales
con sus modos y permitió compararlos. No se observaron errores de JavaScript.
Los textos `[SOURCE_TITLE]` y `[SOURCE_CONCEPT]` pertenecen al doble de pruebas;
no son resultados educativos de Gemini.

Capturas sin tokens ni códigos de recuperación:

- [Resultado básico simulado](evidencia/modos-generacion/basico-simulado.jpg).
- [Historial con ambos modos](evidencia/modos-generacion/historial-simulado.jpg).
- [Selección de comparación](evidencia/modos-generacion/comparacion-simulada.jpg).

## Límites

La ausencia de juez en básico es una decisión del modo. Las comprobaciones
estructurales y la validez de las localizaciones de citas no demuestran respaldo
semántico ni exactitud técnica. No se ejecutaron Gemini/OCI reales ni se crearon
recursos cloud. La rama requiere revisión y merge explícitos antes de considerarse
integrada en develop.
