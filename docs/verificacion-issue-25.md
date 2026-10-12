# Verificación del flujo documental (#25)

Revisión del PR #116 realizada el 12/10/2026. La pantalla se probó en el navegador
contra FastAPI, SQLite, Chroma y el worker reales, usando almacenamiento y Gemini
simulados explícitamente. No se enviaron documentos a OCI ni Gemini reales.

## Correcciones encontradas en la revisión

- Al retirar una demo del catálogo o devolverla a `processing`, su selección
  permanecía activa. La UI ahora limpia esa selección al consultar el catálogo.
  Dos regresiones de AppTest reproducían el problema y pasan con el arreglo.
- El selector nativo anunciaba y admitía hasta 200 MB en Streamlit, aunque la API
  documental limita los PDF a 20 MiB. Se configuró `server.maxUploadSize = 20`
  tanto para el arranque desde la raíz como para el contenedor frontend.
  La validación documental conserva el límite específico de 5 MiB para MD/TXT.
- El arranque documentado `streamlit run frontend/app.py` no leía el tema de
  `frontend/.streamlit/`. La configuración de la raíz reproduce el tema y el
  límite del contenedor, sin depender del directorio de arranque.

Las dos configuraciones de Streamlit deben mantenerse alineadas; Docker copia la
que está en `frontend/.streamlit/` por su contexto de build.

## Recorrido observado

| Caso | Resultado |
|---|---|
| PDF de texto de una página | Listo, 1 de 1 páginas procesadas, selección habilitada |
| TXT UTF-8 | De Procesando a Listo automáticamente; 1 sección, 631 tokens |
| Markdown oficial de APIs de pagos | Listo; 11 secciones, 2576 tokens |
| Texto pegado con título | Listo; 1 sección, 601 tokens |
| PDF de 25 MiB | Rechazo en el selector: límite nativo de 20 MB; no se envía a la API documental |
| Biblioteca demo preparada para QA | Pagos/salud listos y seleccionables sin upload; VCN con visión pendiente deshabilitado |
| Selección con teclado | Tab recorre acciones; Enter abre el formulario y selecciona; foco visible de 2 px |
| Borrado del texto temporal seleccionado | Confirmación con consecuencias; retirada del catálogo y limpieza programada |
| Almacenamiento | La interfaz identifica el almacenamiento local de desarrollo |

El VCN de QA conserva visión pendiente en la página 3: el catálogo no la inventa
como completada. La preparación de la biblioteca en OCI/VM corresponde al #49;
las pruebas con servicios externos reales corresponden al #54.

## Diseño y evidencia

Se inspeccionaron 320, 768, 1024 y 1440 px. El ancho del documento coincidió con
el viewport en los cuatro casos, sin desborde horizontal. A 320 px las acciones
se apilan y el panel lateral se pliega. Los textos del proyecto y sus errores
están en los catálogos ES/EN/PT; el selector nativo de Streamlit conserva algunos
mensajes propios en inglés.

Las capturas muestran únicamente fuentes públicas y datos de prueba; el código
de recuperación se ocultó antes de guardarlas.

- [Móvil, 320 px](evidencia/issue-25/mobile-320.jpg)
- [Tablet, 768 px](evidencia/issue-25/tablet-768.jpg)
- [Escritorio, 1024 px](evidencia/issue-25/desktop-1024.jpg)
- [Escritorio, 1440 px](evidencia/issue-25/desktop-1440.jpg)
- [Confirmación de borrado](evidencia/issue-25/confirmacion-borrado.jpg)

## Pruebas y CI

`pytest frontend/tests`: **91 pruebas aprobadas** tras agregar las dos regresiones
de la selección demo. Ruff y formato también aprobados.

El commit original `c0d7c37` tenía los cuatro checks aprobados y **1879 tests**
en la suite completa: [CI original](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/actions/runs/38295684038).
Su job calidad tardó 6 min 43 s. El timeout de 15 minutos no oculta tests fallidos:
la ejecución previa alcanzó el límite de 10 minutos durante la verificación PDF
y no llegó a ejecutar Anki. Se conservan todos los controles y los timeouts
individuales de integración/exportaciones.

Los resultados finales de los arreglos de revisión quedan en los checks del
[PR #116](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/pull/116).

