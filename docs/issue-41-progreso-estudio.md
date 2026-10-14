# Issue #41 — Progreso de estudio (draft)

`components.progress.render_progreso(api, idioma_ui)` consulta `GET /api/progress`
en cada render. Presenta conceptos, flashcards, intentos, aciertos iniciales y
posteriores, tiempo restante y versión confirmada. No conserva agregados ni
incrementa contadores en sesión. Ante un fallo oculta las métricas.

El tiempo es orientativo y no afirma dominio ni certificación. El progreso pertenece
al espacio recuperable: el flujo de sesión existente recupera el código y el panel
lee de nuevo la API, sin restaurar contadores desde el navegador.

## Integración pendiente con #34

El componente aún no está montado en home. Los renderizadores deben llamar:

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

Faltan montaje y callbacks sobre el material real del #34, recorrido manual con código
de recuperación y QA visual a 320, 768, 1024 y 1440 px. Este draft no cierra #41.

## Verificación

Pruebas de Streamlit/controlador y cliente contra FastAPI con ingesta, generación y
persistencia reales del backend, pero OCI/Gemini simulados explícitamente. Verifican
frente/dorso, reintentos sin duplicar contadores ni versión, recuperación exacta,
aislamiento de espacios, datos inválidos y métricas de quiz calculadas en servidor.
No acreditan entrega pública ni integración con proveedores reales.
