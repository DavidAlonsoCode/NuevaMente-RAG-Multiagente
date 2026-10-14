# Issue #40 — Panel de glosario (draft)

Componente `components.glossary.render_glosario` sobre las rutas del #39. Hereda
documento, perfil e idioma de contenido como argumentos de la selección vigente;
el idioma de interfaz sólo cambia etiquetas.

Incluye búsqueda por término, traducción y definición, orden alfabético por término
original, referencias expandibles, cantidad de candidatos omitidos y limitaciones
cuando la fuente no define un término. Los identificadores técnicos conservan su
nombre. No se muestran resultados fallidos, rechazados ni cancelados.

La admisión indeterminada conserva cuerpo y clave de idempotencia para comprobar
la misma intención. El seguimiento consulta el job cada cinco segundos, permite
actualización manual y solicita cancelación sin darla por terminada anticipadamente.
La lectura del glosario revalida autorización en cada render.

## Integración pendiente con #34

Invocar con `document_id`, `perfil`, `idioma_salida` y el cliente autenticado.
`render_fuentes(document_id, referencias)` recibe las citas del resultado para
montar el visor autorizado de fragmentos. Sin callback se muestran únicamente
localizadores, con aviso explícito. El componente aún no está montado en home.

Falta comprobar el flujo completo desde la selección del sidebar, recarga con
recuperación de espacio, cancelación en navegador y revisión visual en 320, 768,
1024 y 1440 px. Este draft no cierra #40 ni reemplaza el plan maestro.

## Verificación

Pruebas de Streamlit y de cliente contra FastAPI, worker e ingesta reales, con
OCI/Gemini simulados explícitamente: búsqueda ES/EN/PT, limitaciones, respuestas
incoherentes, separación por documento/perfil/idioma, cancelación cooperativa,
reintento idempotente, caché aprobada y citas consultadas en la ruta de fuentes.
Estas pruebas no acreditan integraciones OCI/Gemini reales.
