# Quiz interactivo (#36)

> **Registro histórico de Streamlit.** #139 retira esa implementación y sus
> pruebas exclusivas. El detalle inferior describe la versión original y conserva
> sus resultados; no es una guía de ejecución ni evidencia de React.
> La implementación vigente está en [App.tsx](../frontend/src/App.tsx),
> [FormatRenderers](../frontend/src/components/FormatRenderers.tsx),
> [vistas de estudio e historial](../frontend/src/components/StudyAndHistoryViews.tsx)
> y [estado de estudio](../frontend/src/flows/studyState.ts).
> La cobertura actual y la equivalencia de pruebas se documentan en
> [verificación de #139](verificacion-issue-139.md).

Integrado con el contenedor de resultados del #34 en la rama del #36.

Se implementa `components/quiz_interactive.py` y el controlador `quiz_flow.py`.
El componente obtiene únicamente GET /api/generations/{id}, cuya vista es
estudiante, y responde con POST /api/quizzes/{id}/answers. Cada envío conserva
event_id e Idempotency-Key después de un timeout o una respuesta inválida.
Cambiar la opción pendiente está bloqueado; otro intento requiere una acción
explícita y usa otra identidad. El backend registra el intento, sin un POST
adicional a progreso ni nuevas llamadas LLM.

Estado aislado por espacio/generación, radios nativos, navegación, feedback
textual de acierto/error, justificación y resumen. El resumen describe los
envíos observados en esta sesión: no inventa historial de otras sesiones.
Los reruns y cambios de idioma conservan la intención y la opción sin enviar; cerrar o recuperar
otra sesión elimina el estado mediante el mecanismo existente.

## Integración con #34

- El contenedor llama a render_quiz_interactivo con la sesión activa y el recibo
  ya consultado; no duplica el GET de la generación en cada renderizado.
- Las referencias del feedback abren el visor compartido del #34. El texto de
  preguntas, opciones y justificaciones se presenta escapado.
- El renderizador estático conserva su uso sin cliente API. La vista conectada
  permite responder, recibir la corrección y consultar la fuente original.
- Verificar recarga completa del navegador y recuperación del espacio; este
  avance acredita reruns, no persistencia de la sesión Streamlit en el navegador.
- La vista de revisión manual incluye corrección simulada para probar teclado
  y presentación. Pruebas simuladas no acreditan Gemini/OCI reales.

Verificación enfocada: frontend/tests/test_quiz_interactive.py y
backend/tests/test_quiz_ui_integration.py. La integración usa API, registro,
worker y almacenamiento locales con proveedores explícitamente simulados.

## Validación de este avance

Corrida ampliada: 223 pruebas aprobadas de frontend completo, quiz UI/API, corrección/progreso,
rasterizado, Researcher y retrieval. Ruff/formato, pip check y git diff --check
correctos. AppTest restaura __main__ después de cada prueba para que procesos
spawn de parser/PDFium no ejecuten el script de interfaz. El harness además
protege sus referencias a session_state durante un import sin sesión.

En Windows usar un basetemp único y corto dentro de .data/ (por ejemplo .data/t36s1
fue el directorio de esta corrida). Un nombre largo hizo fallar os.replace en el
mock de storage; al acortarlo los cuatro casos de upload E2E pasaron sin cambiar
producción ni sus pruebas. No se ejecutaron Gemini/OCI reales.

La última mejora conserva la opción todavía no enviada al cambiar ES/EN/PT.
Se verificaron 63 pruebas enfocadas después de ese cambio, incluidos el cliente,
i18n, respuestas perdidas y rasterizado; sin nuevas llamadas a servicios reales.

Después de integrar #34: 68 pruebas de quiz, resultado, idiomas y API/UI
aprobadas. El recorrido integrado comprueba feedback, apertura de evidencia y
una sola consulta inicial de la generación. Ruff y formato correctos.
