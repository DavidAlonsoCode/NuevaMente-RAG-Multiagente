# Evidencias y auditoría del #57

Revisión del 21/10/2026 sobre código `f1f9ae61a5e52c7f0ef2411754ebd94738400318`.
Estado: **parcial, preparado para revisión; no acredita cierre del #57**.
Se separan los escenarios reales históricos de #55 de las capturas nuevas con
dobles. No hubo acceso a OCI/Gemini, nuevas adaptaciones reales ni contacto con integrantes.

## Fuentes de cumplimiento

- [Enunciado original PDF](../../../Enunciado/Enunciado%20-%20NuevaMente.pdf) y
  [extracción de texto versionada](../../../Enunciado/Enunciado%20-%20Nuevamente.txt):
  checklist de ocho mínimos en páginas 5–6; cinco diferenciales en página 6;
  tres escenarios sobre un mismo documento en página 4.
- [Decisiones §20](../../../decisiones_proyecto.md#20-checklist-de-cumplimiento-de-requisitos):
  alcance de mínimos, diferenciales y extras; distingue previsto de demostrado.
- [Plan §7](../../plan-implementacion.md#7-matriz-de-trazabilidad-requisitos--issues):
  correspondencia con implementaciones y extras. El README desglosa historial y
  comparación y conserva el acceso por código: doce filas de extras para cubrir
  la unión de decisiones y plan, sin sumar requisitos nuevos.

La [tabla del README](../../../README.md#cumplimiento-y-evidencia-disponible)
separa implementación, evidencia y aceptación pendiente. Un enlace a un test
identifica cobertura existente; no afirma que se ejecutó en D. Los informes
conservan versión, proveedor y alcance original. El cierre de un issue no se usa
como comprobación. Las sugerencias históricas de Streamlit/Gradio del enunciado
no sustituyen la decisión vigente de interfaz React.

## Escenarios reales A/B/C: VCN v2

Fuente: [redes_vcn_oci.pdf](../../../documents/redes_vcn_oci.pdf),
[versión v2](../../../documents/redes_vcn_oci.version.json), diez páginas.
SHA-256 del PDF verificado localmente:
`fd57e3548ca593f167c297e2ad9bbcf2297bdb87985d003037d4afc5b5cc4d2e`.
Los tres escenarios comparten `document_id=doc_77474e4eb7834d0bb2d67c79b4e7e9db`.
Detalle machine-readable: [inventario de escenarios](escenarios-vcn-v2.json),
**que no es una solicitud ni un paquete educativo**.

| Escenario | Parámetros registrados | Generación real histórica | Tiempo registrado |
|---|---|---|---:|
| A | `principiante`, `flashcards`, `general`, `es` | `gen_66279cac6a8748fc91926203198838a1` | 94,815 s |
| B | `junior_ssr`, `tutorial`, `ecommerce`, `en` | `gen_5733436c8ddf4dfeb09ae96ac5c7c724` | 79,624 s |
| C | `ejecutivo`, `resumen_ejecutivo`, `fintech`, `pt` | `gen_92d7ca76b771469a9054e76ff30acc91` | 113,531 s |

Todos usan detalle `didactico` y alcance `documento_completo`. Se contrastaron
parámetros, ID de documento/generación, hash, estado y tiempos entre
[catálogo](../issue-55/v2/catalogo.json), [informe](../issue-55/v2/informe.json)
y [registro real #55 v2](../issue-55/v2/README.md). El informe identifica el commit
histórico `aaed384b77361465d70ab71a6cd0b1a39d5e68a7`. Su evidencia real es local,
no una aceptación de la URL pública ni una revisión pedagógica humana.

`document_hash` es la huella del PDF. `fuente_sha256` del catálogo es la huella
del snapshot JSON de fuente, chunks e imágenes publicado por
[publish.py](../../../backend/app/demo/publish.py); no se confunde con los bytes
del PDF. `paquete_sha256` solo identifica la salida esperada hasta obtener sus bytes.

### Entradas reconstruidas y salidas que faltan

El issue exige JSON de entrada/salida reales; no exige que la entrada sea una
captura HTTP original. Se incorporan tres **reconstrucciones trazables**, copiadas
sin agregar campos desde `parametros` del catálogo histórico:

| Escenario | Entrada reconstruida, sin captura HTTP original | Salida canónica |
|---|---|---|
| A | [entrada-vcn-a.reconstruida.json](entrada-vcn-a.reconstruida.json) | No localizada |
| B | [entrada-vcn-b.reconstruida.json](entrada-vcn-b.reconstruida.json) | No localizada |
| C | [entrada-vcn-c.reconstruida.json](entrada-vcn-c.reconstruida.json) | No localizada |

Se validaron directamente con Pydantic `GenerateRequest`: campos obligatorios,
enums, alcance y prohibición de campos extra; se cotejaron todos los valores con
el catálogo y su equivalencia con `ESCENARIOS.solicitud` normalizada por el mismo
modelo. Contrato: [requests.py](../../../backend/app/schemas/requests.py) y
[AlcanceSolicitud](../../../backend/app/schemas/pedagogical.py).
No falta ningún campo obligatorio del cuerpo. `modo_generacion` no aparece en
los parámetros históricos y se omite: el contrato actual aplica `multiagente`
por defecto. `alcance.seccion_id` nulo sí está registrado. No se reconstruyen
cabeceras de autenticación/idempotencia, una sesión actual ni una captura original.
El inventario guarda procedencia y hash de cada entrada nueva.

**Faltan las tres salidas canónicas A/B/C**, que no pueden reconstruirse. La
búsqueda exhaustiva anterior no se repitió en la auditoría: se buscaron IDs exactos
en JSON del repositorio, incluidos archivos
ignorados y excluyendo venv/node_modules/.git y el snapshot de C, y en 592 bases
`operativo.db` locales abiertas con `mode=ro` y `query_only`. No hubo coincidencias
en parámetros de `generations` ni en `approved_outputs`. No se leyeron tokens,
códigos de recuperación ni configuración privada para esa búsqueda.

Las huellas esperadas de las salidas, tomadas del catálogo, son:

| Escenario | SHA-256 esperado del paquete; bytes aún no disponibles |
|---|---|
| A | `83c654ea61ade9b3ce2dd1dbb92650b95e1eadaf9d6be729bc532dede1ed3ae9` |
| B | `6528583a9bdd023db6a477353612b442af2f86c6370ef089d0e535419e65e57d` |
| C | `ea0ec80d992bae489564baa236acb176f5b5b3ffdc618782722d9c575317161b` |

La persona que custodia los artefactos de #55 debe aportar los **bytes originales
de esos tres paquetes**, sus IDs y procedencia. Luego se debe cotejar SHA-256 y
contrato antes de enlazarlos; cambiar espacios/serialización puede alterar el hash.
Los hashes esperados no acreditan disponibilidad ni validación de esos bytes.
No se crean enlaces a JSON inexistentes. El [paquete histórico de #54](../issue-54/resultado.json)
corresponde a VCN v1 y queda fuera de A/B/C v2. Las capturas y salidas sintéticas
de esta etapa tampoco cubren ese hueco.

## Capturas actuales de React: simuladas

Se reutilizó el entorno instalado y aislado de C, con un directorio de datos
nuevo. Los 295 archivos versionados de backend, frontend y `.github` coinciden
byte a byte con el snapshot. [capturas.json](capturas.json) registra configuración
no secreta, comandos, fuente sintética, hashes de paquetes locales y de imágenes.

| Captura | Procedencia y encuadre | JPEG guardado |
|---|---|---|
| [Escritorio simulado](escritorio-simulado.jpg) | Carga en área principal, documento sintético listo, parámetros a la izquierda | 1425 × 990 |
| [Multiagente simulado](multiagente-simulado.jpg) | Flashcards, revisión/score del doble y almacenamiento local | 1425 × 990 |
| [Básico simulado](basico-simulado.jpg) | Mismo documento; ausencia de score y advertencia de evaluación independiente | 1425 × 990 |
| [Móvil simulado](movil-simulado.jpg) | Resultado básico desplazado hasta tarjeta, advertencia y fuentes | 375 × 811 |

Viewport solicitado: 1440 × 1000 para escritorio/resultados y 390 × 844 para
móvil. El navegador entregó los tamaños JPEG de la tabla; se registran ambos
sin afirmar una comprobación a 320 px. Se restableció el viewport al terminar.

Recorrido por UI: ocultar el código con «Ya guardé mi código» antes de capturar;
cargar TXT sintético y esperar `ready`; generar Multiagente; cambiar a Básico y
confirmar otra generación sobre el mismo documento; inspeccionar móvil. Ambos
trabajos terminaron `completed`, con objetos mock locales: Multiagente `aprobado`
y score simulado 1.0; Básico `no_evaluado`, score nulo. Se contrastaron los objetos
mock con estados/parámetros SQLite, en modo lectura, sin archivar los paquetes.

Las cuatro imágenes se inspeccionaron visualmente: no muestran tokens ni códigos
de recuperación. No hubo edición raster, montaje ni reemplazo de texto. Los
marcadores `[SOURCE_TITLE]` y `[SOURCE_CONCEPT]` proceden del doble y se conservan.
La revisión visible es sintética: no demuestra respaldo factual, calidad Gemini,
visión real, aceptación pública ni conformidad WCAG. La inspección del agente
no sustituye firmas humanas de #56 o la evaluación de #60.

## Créditos y evidencia final de demo

Identificación explícita conservada del README de la base: **Grupo 10 · Equipo 38**,
Hackathon Oracle Next Education / Alura Latam. La [guía del equipo](../../guia-trabajo-equipo.md)
describe carriles API/RAG/AGT/UI/INF/QAD, pero no asigna allí nombres/roles personales.
No se encontró una nómina confirmada en las fuentes documentales consultadas.
Falta nombre público, usuario/enlace y rol confirmado por el equipo para cada
integrante. No se deducen créditos del número de commits ni de alias Git.

La evidencia final de guion, ensayo, grabación y protocolo firmado se incorporará
cuando exista y sea revisada. Por ahora se enlaza únicamente el
[seguimiento #56](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/56),
sin rutas a archivos pendientes. Las contradicciones #58/#133 y #60 siguen en
[verificación #57](../../verificacion-issue-57.md#contradicciones-y-pendientes-conservados).
La instalación por un tercero, la revisión final del equipo y la aceptación pública de
[#133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133)
siguen pendientes.

## Auditoría y revisión humana

La [matriz final documental](../../verificacion-issue-57.md#matriz-final-documental-del-57)
identifica comprobaciones, límites y qué debe aportar cada persona.
El [protocolo del voluntario](protocolo-voluntario.md) es una plantilla pendiente,
sin resultados ni firmas. La validación de los dos diagramas se registra en
[Mermaid](mermaid/README.md), con fuentes extraídas del README y renders inspeccionados.
