# Issue #41 — Progreso de estudio

> **Registro histórico de Streamlit.** #139 retira esa implementación y sus
> pruebas exclusivas. El detalle inferior describe la versión original y conserva
> sus resultados; no es una guía de ejecución ni evidencia de React.
> La implementación vigente está en [App.tsx](../frontend/src/App.tsx),
> [FormatRenderers](../frontend/src/components/FormatRenderers.tsx),
> [vistas de estudio e historial](../frontend/src/components/StudyAndHistoryViews.tsx)
> y [estado de estudio](../frontend/src/flows/studyState.ts).
> La cobertura actual y la equivalencia de pruebas se documentan en
> [verificación de #139](verificacion-issue-139.md).

`components.progress.render_progreso(api, idioma_ui)` consulta `GET /api/progress`
en cada render. Presenta conceptos, flashcards, intentos, aciertos iniciales y
posteriores, tiempo restante y versión confirmada. No conserva agregados ni
incrementa contadores en sesión. Ante un fallo oculta las métricas.

El tiempo es orientativo y no afirma dominio ni certificación. El progreso pertenece
al espacio recuperable: el flujo de sesión existente recupera el código y el panel
lee de nuevo la API, sin restaurar contadores desde el navegador.

## Integración con #34

La pantalla principal muestra la pestaña de progreso y los renderizadores llaman:

- `registrar_concepto_revisado` al abrir o marcar un concepto concreto, con
  documento y generación opcional. `render_marca_concepto` ofrece un botón explícito.
- `registrar_cara_flashcard` con generación, tarjeta y cara `frente` o `dorso`,
  únicamente después de mostrar ese lado. Sólo ambas caras habilitan el registro.
- `render_progreso` después de los eventos para consultar los agregados actuales.

Una actividad conserva su ID determinista por espacio y recurso, incluso tras F5.
La API recibe la misma intención al reintentar una respuesta perdida. Las confirmaciones
pendientes tienen reintento explícito, uno por clic; se puede elegir cuál comprobar. No se repiten escrituras en cada
rerun. `session_state` sólo conserva caras consultadas e intenciones de presentación.
El quiz registra sus intentos en su endpoint de respuestas; este panel no los duplica.

Revelar una tarjeta registra ambas caras mostradas; ocultar, volver a revelar o
cambiar idioma no vuelve a enviar el evento confirmado. Los conceptos se marcan
con botones explícitos dentro de la preparación del material. También se puede
consultar el progreso desde la pantalla de documentos, sin generación activa.
Integrar después de #40. La validación de proveedores reales pertenece a la entrega.

## Verificación

Pruebas de Streamlit/controlador y cliente contra FastAPI con ingesta, generación y
persistencia reales del backend, pero OCI/Gemini simulados explícitamente. Verifican
frente/dorso, reintentos sin duplicar contadores ni versión, recuperación exacta,
aislamiento de espacios, datos inválidos y métricas de quiz calculadas en servidor.
No acreditan entrega pública ni integración con proveedores reales.
