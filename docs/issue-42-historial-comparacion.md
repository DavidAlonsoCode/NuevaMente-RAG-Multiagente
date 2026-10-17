# Issue #42 — Historial, comparación y recuperación

> **Registro histórico de Streamlit.** #139 retira esa implementación y sus
> pruebas exclusivas. El detalle inferior describe la versión original y conserva
> sus resultados; no es una guía de ejecución ni evidencia de React.
> La implementación vigente está en [App.tsx](../frontend/src/App.tsx),
> [FormatRenderers](../frontend/src/components/FormatRenderers.tsx),
> [vistas de estudio e historial](../frontend/src/components/StudyAndHistoryViews.tsx)
> y [estado de estudio](../frontend/src/flows/studyState.ts).
> La cobertura actual y la equivalencia de pruebas se documentan en
> [verificación de #139](verificacion-issue-139.md).

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

## Integración

`render_historial_completo(api, idioma, render_resultado=...)` está montado en home.
El callback del #34 recibe `(paquete_estudiante, idioma, key_prefix=...)`, con un
prefijo distinto por generación/contexto para evitar colisiones de widgets.
Sin él se muestran fuente, fecha y parámetros, con aviso de visor pendiente.

`render_etiqueta_ejemplo` está preparado para un catálogo demo explícito del #55.
La API actual del historial no aporta procedencia demo ni resultados públicos
precalculados: no se infiere por títulos/IDs y no se inventan ejemplos guardados.
El catálogo de ejemplos precalculados pertenece al #55. El historial privado ya
usa el renderizador completo del #34, con fuentes, quiz interactivo y revisión
de flashcards/conceptos. Integrar después del #41.

## Verificación

Pruebas de Streamlit/controlador y cliente contra FastAPI con proveedores
OCI/Gemini simulados explícitamente. Cubren comparación A/C de la misma fuente,
paginación/filtros, exportación, recuperación, rechazo de hashes/versiones
distintos y ocultamiento de resultados no aprobados. No acreditan proveedores reales.

La rama incluye el arreglo de ámbitos pequeños de Chroma del PR #123 como
dependencia compartida; también integra los paneles previos según el orden
#36 → #38 → #40 → #41 → #42, conservando la base develop de los PR.
