# Issue #40 — Panel de glosario

> **Registro histórico de Streamlit.** #139 retira esa implementación y sus
> pruebas exclusivas. El detalle inferior describe la versión original y conserva
> sus resultados; no es una guía de ejecución ni evidencia de React.
> La implementación vigente está en [App.tsx](../frontend/src/App.tsx),
> [FormatRenderers](../frontend/src/components/FormatRenderers.tsx),
> [vistas de estudio e historial](../frontend/src/components/StudyAndHistoryViews.tsx)
> y [estado de estudio](../frontend/src/flows/studyState.ts).
> La cobertura actual y la equivalencia de pruebas se documentan en
> [verificación de #139](verificacion-issue-139.md).

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

## Integración con #34

Montado en una pestaña de la pantalla principal y en las herramientas del
documento privado seleccionado. Los parámetros del material visible prevalecen
sobre cambios posteriores del sidebar. El visor de fuentes del #34 tiene claves
estables por término: filtrar o repetir una cita no mezcla controles ni evidencia.

La definición se presenta escapada. El renderizador conserva su callback previo
y admite `render_sources(referencias, prefijo)` para componer el visor compartido.
Integrar después de #38; no reemplaza el plan maestro.

## Verificación

Pruebas de Streamlit y de cliente contra FastAPI, worker e ingesta reales, con
OCI/Gemini simulados explícitamente: búsqueda ES/EN/PT, limitaciones, respuestas
incoherentes, separación por documento/perfil/idioma, cancelación cooperativa,
reintento idempotente, caché aprobada y citas consultadas en la ruta de fuentes.
Estas pruebas no acreditan integraciones OCI/Gemini reales.
