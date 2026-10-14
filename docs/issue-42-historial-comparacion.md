# Issue #42 — Historial, comparación y recuperación (draft)

El expander del sidebar abre el historial completo y permite consultar las últimas
cinco generaciones. La vista completa consume páginas de `GET /api/generations`,
con filtros de documento, perfil, formato, idioma y fechas inclusivas en UTC.
El selector documental recorre el catálogo autorizado; las páginas del historial
se consultan de nuevo, sin guardar paquetes en sesión.

Se puede reabrir un aprobado y preparar su exportación JSON/Markdown/PDF; las
flashcards permiten además CSV/TSV/APKG. La descarga vuelve a validar el estado
y usa la ruta existente de exportaciones. Los trabajos fallidos, cancelados o
rechazados muestran un diagnóstico localizado y no ofrecen borradores ni descargas.

La comparación mantiene dos selecciones incluso entre páginas, consulta ambos
aprobados y exige coincidencia de documento, hash y versión. Usa la vista estudiante
del quiz y no llama al LLM. Se informa la retención de 30 días de inactividad y la
expiración que devuelve el servidor. El código de recuperación reutiliza el flujo
de sesión existente y vuelve a consultar el historial.

## Integración pendiente

`render_historial_completo(api, idioma, render_resultado=...)` está montado en home.
El callback del #34 recibe `(paquete_estudiante, idioma, key_prefix=...)`, con un
prefijo distinto por generación/contexto para evitar colisiones de widgets.
Sin él se muestran fuente, fecha y parámetros, con aviso de visor pendiente.

`render_etiqueta_ejemplo` está preparado para un catálogo demo explícito del #55.
La API actual del historial no aporta procedencia demo ni resultados públicos
precalculados: no se infiere por títulos/IDs y no se inventan ejemplos guardados.
Faltan esa conexión, el renderer del #34 y QA manual/visual a 320/768/1024/1440 px.

## Verificación

Pruebas de Streamlit/controlador y cliente contra FastAPI con proveedores
OCI/Gemini simulados explícitamente. Cubren comparación A/C de la misma fuente,
paginación/filtros, exportación, recuperación, rechazo de hashes/versiones
distintos y ocultamiento de resultados no aprobados. No acreditan proveedores reales.

La rama incluye el arreglo de ámbitos pequeños de Chroma del PR #123 como
dependencia compartida; el cambio es idéntico y no incluye su panel de glosario.
