# Avance del quiz interactivo (#36)

Rama independiente desde develop. El PR permanece en draft; no cierra #36.

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
Los reruns y cambios de idioma conservan la intención; cerrar o recuperar
otra sesión elimina el estado mediante el mecanismo existente.

## Integración pendiente con #34

- El contenedor de resultados debe llamar a render_quiz_interactivo con la
  sesión activa y generation_id, después de seleccionar el formato quiz.
- Inyectar render_fuentes(document_id, referencias), el visor compartido del
  #34. Sin ese callback solo se muestran localizadores, no fuentes abiertas.
- No se modifica components/quiz.py ni la composición de home: pertenecen #34.
- Verificar recarga completa del navegador y recuperación del espacio; este
  avance acredita reruns, no persistencia de la sesión Streamlit en el navegador.
- Completar revisión visual/teclado/320 px y recorrido manual E2E antes de salir
  de draft. Pruebas simuladas no acreditan Gemini/OCI reales.

Verificación enfocada: frontend/tests/test_quiz_interactive.py y
backend/tests/test_quiz_ui_integration.py. La integración usa API, registro,
worker y almacenamiento locales con proveedores explícitamente simulados.

## Validación de este avance

53 pruebas enfocadas aprobadas (quiz, cliente, i18n e integración UI/API),
Ruff/formato y pip check correctos. La corrida ampliada registró 172 aprobadas
y cuatro fallos en test_upload_e2e.py. El primer fallo de upload se reprodujo
con el api_client.py original de develop, en un directorio temporal nuevo;
no se alteraron esos tests ni el backend. El diagnóstico del harness TCP
local permanece separado; no se afirma que toda la suite local haya pasado.
