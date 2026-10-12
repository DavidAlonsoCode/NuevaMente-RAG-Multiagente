# Exportación opcional de flashcards a Anki (#45)

La experiencia principal de flashcards se realiza dentro de NuevaMente (interfaz
pendiente del #34). Anki es una aplicación externa de estudio con repetición
espaciada; estos archivos permiten llevarse el mazo. No es un requisito del sistema.

## Descargar e importar

1. Generar flashcards y esperar `completed`. Descargar con
   `GET /api/exports/{generation_id}?format=apkg` (o `csv|tsv`), con la sesión propietaria.
2. En Anki Desktop moderno (cabeceras de texto compatibles desde 2.1.54), elegir
   **Archivo → Importar** y seleccionar el archivo. Confirmar la importación.
3. APKG crea el modelo y mazo `NuevaMente::{generation_id}`. CSV/TSV detectan
   separador, HTML, etiquetas, GUID y mazo mediante cabeceras. En una colección nueva
   los primeros dos campos se asignan a anverso/reverso del tipo básico automáticamente.
   Si se eligió previamente un tipo de nota personalizado, seleccionar uno de dos
   campos para el texto; APKG evita depender del tipo de nota de la colección.
4. Abrir el mazo para estudiar. Volver a importar el mismo archivo conserva las
   tres tarjetas del ejemplo, sin crear copias. Los tres formatos comparten GUID
   y la misma identidad de mazo; APKG mantiene modelo/mazo estables por generación.

Las etiquetas con espacios se convierten a guiones bajos y los caracteres activos
se normalizan (por ejemplo `redes OCI` → `redes_OCI`); se conservan Unicode y acentos.
Todos los campos de pregunta/respuesta se exportan como HTML escapado dentro de
`<span>`, con saltos de línea `<br>`. Esto muestra el texto literal en Anki y evita
que una planilla ejecute fórmulas que empiezan con `=`, `+`, `-` o `@`. Las directivas
`[sound:...]` y `[latex]` se codifican como texto; el paquete no incorpora media.
CSV/TSV están orientados a importación Anki: una planilla puede mostrar las etiquetas
HTML. Para lectura humana utilizar Markdown.

## Verificación reproducible

- `pytest backend/tests/test_exports_anki.py`: importador oficial `anki==26.9.3`
  en colecciones temporales; importación/reimportación, cruces de formato, dos
  generaciones, etiquetas, HTML/fórmulas y bytes APKG reproducibles.
- `pytest backend/tests/test_exports_anki_api.py`: caché de cinco formatos antes
  de `completed`, descarga HTTP y `/persist` tras fallar APKG sin repetir IA.
- CI ejecuta Anki Desktop `aqt[qt610]==26.9.3` bajo Xvfb, con perfil temporal y
  sincronización deshabilitada. El script usa el importador oficial sobre la
  colección abierta por Desktop y la detección automática de cabeceras; captura
  los mazos y el explorador real. No automatiza el selector de archivos.
  El artifact `anki-desktop-evidence` contiene CSV/TSV/APKG, explorador y resultado
  JSON (seis importaciones, tres notas/tarjetas). Se conserva 14 días por corrida.

Referencias: [manual oficial de importación de texto](https://docs.ankiweb.net/manual/importing/text-files)
y [genanki](https://github.com/kerrickstaley/genanki).
