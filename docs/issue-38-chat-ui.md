# Avance del chat RAG en la interfaz (#38)

Rama independiente desde develop. El PR permanece en draft; no cierra #38.

components/chat.py y chat_flow.py consumen POST /api/chat y las rutas comunes
GET /api/jobs/{id} y POST /api/jobs/{id}/cancel. Un envío sin confirmación
conserva cuerpo y clave idempotente; solamente una acción explícita lo reenvía.
El polling no vuelve a admitir preguntas. La cancelación sigue pendiente hasta
el terminal del backend: no libera el hilo ni simula terminar una llamada.

Conversaciones separadas por workspace/documento/perfil/idioma de contenido.
Cambiar idioma de interfaz no cambia una pregunta admitida. La pantalla conserva
hasta 50 turnos por contexto en la sesión Streamlit. Cerrar/cambiar sesión usa la
limpieza existente; se revalida el documento antes de mostrar su conversación.
Una pregunta activa en otro contexto bloquea otro envío dentro del mismo espacio.

Sólo se muestra un resultado completed, validado y correspondiente a la pregunta
y documento de origen. Failed/rejected_quality/cancelled no publican borradores.
La abstención se presenta separada de una respuesta aprobada y citada.

## Integración pendiente

- #34 aporta la composición final y render_fuentes(document_id, referencias).
  El fallback muestra localizadores, no un visor compartido de fuentes.
- No se modifica home.py ni el panel de fuentes previsto en #34.
- La API #37 no acepta conversation_id ni ofrece reset. Limpiar historial visible
  elimina únicamente esta vista; no se presenta como reinicio de memoria del tutor.
  El reinicio real requiere acordar su contrato con API antes de completar ese
  punto del #38. No se añade un endpoint unilateralmente.
- Historial visible de esta sesión no acredita recuperación tras F5 ni historial
  persistente/reapertura del #42.
- Falta revisión visual, teclado/320 px y recorrido manual con backend antes de
  salir de draft. La espera usa fragmentos Streamlit cada cinco segundos y un
  botón de actualización, no otro worker ni el seguimiento de generación #33.

Verificación: frontend/tests/test_chat_ui.py y
backend/tests/test_chat_ui_integration.py, con cinco turnos y admisión confirmada
cuya respuesta HTTP se pierde. Los proveedores están explícitamente simulados.

## Validación de este avance

66 pruebas enfocadas aprobadas: interfaz, cliente, idiomas, cinco turnos con
fuentes HTTP, respuesta perdida tras admisión y procesos de rasterizado.
Las pruebas restauran __main__ después de AppTest para no contaminar spawn.
Ruff/formato, pip check y git diff --check correctos. No se ejecutaron
Gemini/OCI reales ni se acredita todavía una revisión visual de la composición
final. La integración del #34 y el reinicio real de memoria siguen pendientes.
