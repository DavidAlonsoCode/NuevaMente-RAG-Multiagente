# Issue #57 — Registro de trabajo y auditoría documental

Registro del 21/10/2026. B, C y D fueron aprobadas para continuar y se conservan
como historia de trabajo, incluidos los pendientes que tenían en cada entrega.
La auditoría E está al final; su alcance autorizado es README, este registro y
documentación pertinente en `evidencia/issue-57/`. Arquitectura conserva la versión
aprobada en B. **#57 incompleto, preparado para revisión humana**.
Consultar la [matriz final documental](#matriz-final-documental-del-57) y
los [faltantes concretos](#faltantes-y-aportes-necesarios).

## Base y trazabilidad

Antes de editar: rama `feature/issue-57-readme-final`, working tree e índice
limpios. HEAD, `develop` y `origin/develop` locales coinciden en
`f1f9ae61a5e52c7f0ef2411754ebd94738400318`; comparación `develop...HEAD`: 0/0,
sin diff contra `develop`. No se sincronizaron referencias por operaciones de escritura.

- [README](../README.md): presentación, capacidades, stack y dos diagramas
  resumidos; las secciones C/D/E están identificadas como pendientes.
- [Arquitectura](arquitectura.md): correspondencia de cajas/transiciones con
  módulos y pruebas existentes. Se contrastaron `main.py`, admisión/ingestión,
  RAG, gestor/registro, generación, grafo, básico, Critic, persistencia y React.
- Ambos modos reutilizan documento e índice; Básico no invoca Critic ni recibe
  score/aprobación. Multiagente conserva sus cinco nodos, retornos y máximo de
  tres redacciones. Ambos exigen persistencia confirmada antes de `completed`.
- Se retiró del README el bloque de comandos anterior, pendiente de revisión
  en C, y los escenarios hipotéticos, pendientes de sustituir en C/D. No se
  redactó una instalación nueva ni se incorporaron paquetes/capturas.

## Contradicciones y pendientes conservados

Los estados GitHub siguientes proceden de la consulta de diagnóstico previa,
del 21/10/2026; no se ejecutaron nuevas consultas en esta etapa.

| Tema | Fuentes en tensión | Tratamiento en esta etapa |
|---|---|---|
| #58 / #133 | [#58](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/58) está cerrado; [#133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133) marca la reproducción del runbook, pero mantiene pendientes VM/HTTPS. El [runbook](despliegue-oci.md#estado-para-auditoría-final-y-aceptación) y su [auditoría](verificacion-issue-58.md#capacidades-actuales-y-aceptación-pendiente) declaran reproducción/revisión humana pendientes y huecos 17.1/17.4 | No afirmar VM/URL ni recuperación integral verificadas. El equipo debe conciliar evidencia y alcance; no se alteran informes ni casillas |
| #60 | [Issue cerrado](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/60), mientras la [evidencia archivada](evidencia/issue-60/README.md) y el [reporte provisional](evidencia/issue-60/reporte-provisional.md) declaran muestra comparable 0, sin anotaciones humanas | No atribuir resultados pedagógicos, dos revisores ni métricas; localizar el reporte final o aclarar el cierre con el equipo |
| Simulación local | La [guía Docker](docker.md) permite arrancar servicios; [main.py](../backend/app/main.py) no inyecta dobles al activar `MOCK_GEMINI=1` | No equiparar health con carga/generación funcional; el [helper existente](../backend/tests/integration/react_backend.py) inyecta los dobles |
| Demo compartida | [Guía frontend](../frontend/README.md) y [ejemplos](ejemplos-demo.md) declaran pendiente generar desde IDs compartidos | Se conserva esa limitación, sin ampliar aplicación |

[La evidencia #54](evidencia/issue-54/README.md) es real local e histórica;
[#55 v2](evidencia/issue-55/v2/README.md) conserva los escenarios vigentes con sus
hashes. No se amplía su alcance a UI pública, capturas nuevas o revisión humana.
El [catálogo v2](evidencia/issue-55/v2/catalogo.json) no contiene los paquetes
completos A/B/C: su incorporación continúa pendiente. #56 permanece abierto como
coordinación recomendada; no bloquea B.

## Verificaciones de B

- Inspección previa de rama, estado, referencias y diff: base coincidente y sin
  cambios ajenos. Inspección final: solo los tres documentos autorizados, sin
  cambios en el índice ni en HEAD.
- Comprobación local de los tres Markdown mediante Python estándar: **141 enlaces
  locales y seis anclas válidos**, bloques cerrados, sin espacios finales y con
  salto final. No se verificaron URLs externas por red.
- Dos bloques Mermaid en README: lectura de cajas/transiciones contra código,
  comillas/delimitadores/subgraphs equilibrados. Lectura AST de `graph.py` confirma
  los cinco nodos documentados, sin importar ni ejecutar la aplicación.
  **No se ejecutó un parser ni un renderizador Mermaid**: no están disponibles en
  las dependencias locales consultadas; el render visual sigue pendiente.
- `.venv/Scripts/python.exe -m ruff check . --no-cache`: aprobado.
- `.venv/Scripts/python.exe -m ruff format --check . --no-cache`: aprobado,
  249 archivos ya formateados.
- `git diff --check`: aprobado; revisión adicional del archivo nuevo y del diff
  documental. No se ejecutaron tests de aplicación ni suite completa por tratarse
  de documentación; los enlaces a tests existentes no afirman una corrida nueva.

Sin instalaciones, builds, ejecución de servicios, lectura de `.env` personal,
llamadas a proveedores, cambios de rama ni escrituras en GitHub. No se valida
instalación, aceptación pública ni calidad pedagógica humana.

## Pendientes al entregar B

- **C:** prerrequisitos, variables, comandos completos y recorridos local/Docker/real;
  uso inicial y prueba de instalación limpia por una persona ajena al equipo.
- **D:** capturas actuales, paquetes A/B/C completos, matriz de ocho mínimos,
  cinco diferenciales y extras, y créditos nominales; repaso de enlaces con #56.
- **E:** validación final del conjunto, revisión humana y conciliación de las
  contradicciones anteriores. B no acredita instalación ni cumplimiento del #57.

No se autoriza por este registro consumo de proveedores, aprovisionamiento ni
contacto con integrantes. No se modifican código, configuración, contratos,
dependencias o evidencias históricas.

## Etapa C — Instalación y uso inicial

21/10/2026. Antes de editar se confirmaron la misma rama/HEAD, comparación 0/0
con `develop`, índice vacío y únicamente los tres documentos aprobados de B.
No se sincronizó ni cambió la rama. C modifica solo README y este registro.
`docs/arquitectura.md` conserva SHA-256
`f4c616a72df3f60a7f53b79f5e7c2e397ed7e4124e379c082d396ed982af4a74`.

### A. Corrección técnica documental

El README selecciona `develop` al clonar y separa helper funcional, nativo real y
Compose con mocks. Los comandos indican cwd/terminales, puertos y proxy. Python
3.11 y Node 24 se contrastaron con requirements, lock, Dockerfiles y CI.
El recorrido real usa un `.env` nuevo en `.data/local-real`, cargado desde ese
directorio, con `DATA_DIR=./state`; documenta prioridad de variables exportadas,
modelos/cuotas y credenciales. Su arranque **no se ejecutó**: inicializar OCI ya
contacta al proveedor. Se contrastaron `Configuracion`, `cuotas_configuradas`,
`main.py`, `oci_storage.py`, `oci_real.py`, Compose y `vite.config.ts`.

Se añadió uso inicial, con creación automática del espacio y resguardo del código,
carga privada de fuente pública/simulada, `ready`, modos, fuentes y descargas.
Se conserva pendiente la generación desde IDs demo compartidos. No se incorporan
capturas, paquetes A/B/C, créditos nominales ni matriz final de D.

### B. Comandos efectivamente ejecutados

Entorno: Windows AMD64 / PowerShell, Git **2.53.0.windows.2**, Python **3.11.9**,
pip **24.0**, Node **24.19.0**, npm **12.0.2**, Vite **8.3.4**.
La copia descartable está en `.data/issue57-c-20261021/checkout`, extraída mediante
`git archive --format=tar --output=.data/issue57-c-20261021/source.tar HEAD`.
Se verificó que no contenía `.env`; no es un clone de red ni una instalación por
terceros. La venv nueva no incluye site-packages del sistema.

Los comandos siguientes se ejecutaron desde esa raíz salvo indicación. Los
requisitos/manifiestos del snapshot son los de `f1f9ae6`; B solo cambia documentos.

| Comando / comprobación | Resultado y límite |
|---|---|
| `python --version`, `node --version`, `npm.cmd --version`, `git --version` | Versiones anteriores. `py -3.11` no encontró instalación registrada; se documenta `python` y se comprueba su versión |
| `python -m venv .venv` | Exit 0 en el directorio nuevo `.data/issue57-c-20261021/venv-literal`. La venv del snapshot se creó con el mismo Python 3.11 mediante `-m venv .data/issue57-c-20261021/checkout/.venv`; sin activación ni site-packages heredados |
| `.venv/Scripts/python.exe -m pip install -r backend/requirements.txt -r requirements-dev.txt` | Falló leyendo requirements UTF-8 como CP1252, antes de resolver paquetes |
| `.venv/Scripts/python.exe -X utf8 -m pip install -r backend/requirements.txt -r requirements-dev.txt` | El primer intento quedó bloqueado por DNS del sandbox; repetido con aprobación de red, instaló ambos requirements sin cambiar manifiestos. `-X utf8` resolvió la lectura |
| `npm.cmd --prefix frontend ci` | Primer intento: DNS bloqueado y advertencia EPERM de limpieza en el directorio nuevo. Repetido con aprobación, exit 0: 98 paquetes; el comando informó 0 vulnerabilidades. SHA-256 del lock conservado: `6d67d059d5b28228581994a36612eef733156e75b1a544341a4781329e6f3af3` |
| `.venv/Scripts/python.exe -m pip check` | Exit 0, sin dependencias incompatibles informadas |
| `npm.cmd --prefix frontend run build` | Exit 0; TypeScript y build de Vite aprobados en la copia |
| `.venv/Scripts/python.exe -m backend.tests.integration.react_backend --data-dir .data/primer-recorrido --port 18080` | En sandbox creó solo estado descartable, sin confirmar disponibilidad HTTP. Se interrumpió; sus datos se conservaron |
| Misma orden con `--data-dir .data/primer-recorrido-2` | Ejecutada fuera del sandbox con aprobación para sockets locales. Arranque completado; Uvicorn 127.0.0.1:18080, proveedores dobles y DNS externo bloqueado por el helper |
| `$env:API_URL = 'http://127.0.0.1:18080'` y `npm.cmd --prefix frontend run dev -- --port 18501` | Ejecutados desde raíz; primer arranque en sandbox no resultó accesible desde la consulta. Repetidos fuera del sandbox con aprobación; Vite disponible en 127.0.0.1:18501 |
| `Invoke-RestMethod http://127.0.0.1:18501/api/health -TimeoutSec 10` | Exit 0, `status=ok`, versión API 0.1.0. Intentos previos entre entornos restringidos fallaron por permisos/timeout; no se cuentan como éxito |
| Recorrido HTTP adicional por 18501, cliente Python/urllib | HTML React servido; espacio creado, TXT hasta `ready`; Básico flashcards `no_evaluado` sin score y Multiagente tutorial `aprobado`, ambos `completed` con `provider=mock`; fuentes resolubles, JSON/MD/PDF descargables y recuperación/historial. No se imprimieron tokens/códigos; solo se conectó a loopback |
| Ctrl+C sobre helper y Vite | Procesos de prueba detenidos; helper confirmó shutdown. Se conservan directorios y datos, sin borrar volúmenes |

Se descargaron dependencias con las aprobaciones correspondientes. No se ejecutó
el clone remoto, Docker/Compose (no disponible en PATH), el recorrido real,
aprovisionamiento ni proveedores reales. La prueba fue HTTP, sin interacción ni
inspección visual de React en navegador; no se atribuyen a C capturas o revisión
humana. Bash Linux/macOS no se ejecutó: los tres bloques Bash se analizaron con
`bash --noprofile --norc -n` de Git, con aprobación tras bloqueo MSYS del sandbox;
ocho bloques PowerShell se analizaron con su parser, sin ejecutar el recorrido real.

Verificaciones documentales finales de C: **143 enlaces locales y ocho anclas**
válidos; bloques cerrados, whitespace y `git diff --check` aprobados.
`ruff check . --no-cache` y `ruff format --check . --no-cache` aprobaron (249
archivos ya formateados). No se repitió la suite completa: se verificaron los
comandos de instalación y el recorrido HTTP pertinente en el entorno nuevo.
El lock no cambió y arquitectura/ambos diagramas conservan el contenido de B.
La validación con parser/renderizador Mermaid permanece pendiente para antes de
la auditoría final; los dos diagramas aprobados de B no se modifican en C.

### C. Instalación limpia por una persona ajena al equipo

**Pendiente.** El snapshot/venv nueva y el recorrido simulado de este agente no
sustituyen el criterio del #57 de un voluntario que siga solo el README. Faltan
su registro de versión, entorno, pasos, resultados y revisión del equipo.

### D. Aceptación pública

**Pendiente en #133.** C no acredita VM, URL/HTTPS, SSE público, OCI/Gemini reales
ni revisión humana. Las contradicciones registradas en B siguen sin resolverse
por inferencia. Continúan D (evidencias/matriz/créditos), el repaso con #56 y E
(auditoría final, incluida Mermaid). No se declara cumplido el #57.

## Etapa D — Evidencias, escenarios, cumplimiento y créditos

21/10/2026. Se comprobaron antes de editar rama `feature/issue-57-readme-final`,
HEAD/develop/origin-develop locales en `f1f9ae6`, 0/0 commits de divergencia e
índice vacío. Solo estaban los tres documentos aprobados de B/C. D modifica
README y este registro y agrega archivos en `docs/evidencia/issue-57/`.
Arquitectura conserva el hash registrado en C; instalación/uso y diagramas B/C
se preservan. Sin operaciones de sincronización o escritura Git/GitHub.

### Evidencia incorporada y límites

- Tabla README de **ocho mínimos, cinco diferenciales y doce extras**. Fuentes:
  enunciado PDF/TXT versionados, decisiones §20 y plan §7. Se desglosa comparación
  respecto de historial y se mantiene recuperación por código para cubrir ambos
  documentos; no se agregan requisitos. Cada fila distingue implementación,
  evidencia con su alcance y aceptación pendiente, sin inferirla del cierre.
- [Inventario A/B/C v2](evidencia/issue-57/escenarios-vcn-v2.json): hash del PDF,
  mismos IDs y tiempos contrastados con catálogo/informe de #55. **Faltan seis
  JSON originales**, entrada y salida de A/B/C. Se buscaron IDs en JSON locales
  (incluidos ignorados, excluidos venv/node_modules/.git/snapshot C) y en 592
  bases operativas mediante consultas específicas de solo lectura, sin encontrar
  solicitudes ni paquetes. No se consultó OCI ni se reconstruyeron paquetes;
  el inventario se identifica expresamente como metadatos, no entrada/salida.
- Cuatro [capturas React actuales, simuladas](evidencia/issue-57/README.md#capturas-actuales-de-react-simuladas):
  escritorio, móvil y resultados Básico/Multiagente. Se generaron por UI con el
  helper de C y datos nuevos; no son VCN v2 ni evidencia de Gemini. Inspección
  visual realizada por el agente, sin tokens/códigos visibles, sin edición raster
  y sin atribuir certificación WCAG o firmas humanas.
- [Procedencia de capturas](evidencia/issue-57/capturas.json): commit, configuración
  no secreta, comandos, fuente sintética, estados de ambos modos, dimensiones y
  hashes. Los marcadores SOURCE_TITLE/SOURCE_CONCEPT son respuestas del doble y
  se conservan. La revisión/score Multiagente es sintética; Básico carece de score.
- Créditos: se conserva Grupo 10 / Equipo 38 / ONE / Alura. No se localizó nómina
  explícita con nombres/usuarios/roles en las fuentes del equipo consultadas; se
  registra qué información debe aportar el equipo, sin deducirla de commits.
- Se reserva evidencia final de #56 mediante enlace a su issue, sin archivos o
  enlaces ficticios. #58/#133 y #60 mantienen las contradicciones registradas.

### Ejecución de capturas

Entorno instalado de C, código `f1f9ae6`. Se compararon 295 archivos versionados
de backend/frontend/.github con el snapshot: cero diferencias. No se instalaron
dependencias en D ni se modificaron código o configuración versionados.

En `.data/issue57-c-20261021/checkout`, con aprobación para sockets locales:

```console
.venv/Scripts/python.exe -m backend.tests.integration.react_backend --data-dir .data/issue57-d-capturas --port 18080
```

En otra terminal, `API_URL=http://127.0.0.1:18080` y
`npm.cmd --prefix frontend run dev -- --port 18501`. Se usó CUA en el navegador
integrado para cargar texto sintético desde UI, esperar ready, confirmar y abrir
ambos resultados. El helper fija mocks, desactiva `.env` y bloquea DNS externo.

Se ocultó el código antes de capturar. Viewports solicitados 1440×1000 y 390×844;
JPEG entregados 1425×990 y 375×811, registrados por separado. Una apertura de tab
antes del arranque dio conexión rechazada; luego se abrió un tab nuevo. Dos
acciones con el navegador oculto agotaron el plazo de despacho; mostrarlo permitió
continuar. No se guardaron páginas de error como evidencia ni se alteró la app.
La comparación de resultados lee estados SQLite y objetos mock propios: ambos
completed, Básico no_evaluado/score nulo, Multiagente aprobado/score simulado 1.0.

Al terminar se cerró el tab de captura, se restableció el viewport y se detuvieron
helper/Vite con Ctrl+C; se conservan sus datos descartables, sin borrar directorios
ni volúmenes.

### Verificaciones D y pendientes

Se aprobaron las comprobaciones de cuatro Markdown: **236 enlaces locales y
25 anclas**, bloques cerrados y sin espacios finales. Dos JSON nuevos válidos;
PDF/catálogo/informe y tres escenarios concordantes; cuatro JPEG con formato,
dimensiones y hashes iguales al manifiesto. La fuente sintética coincide por hash
con su documento local. Los hashes esperados de salidas reales no se presentan
como verificados contra bytes ausentes.

Sin hallazgos en el control de patrones de claves/API privadas, Bearer largos o
códigos de recuperación sobre textos nuevos y diff; sin claves sensibles en los
JSON nuevos. Los JPEG se revisaron visualmente, sin OCR ni atribuir una garantía
absoluta del escáner. No se leyeron `.env` personales ni credenciales.
`ruff check . --no-cache`, `ruff format --check . --no-cache` (249 archivos) y
`git diff --check` aprobaron. Se revisó también el contenido nuevo sin rastrear;
la suite completa no se repitió, sin cambios de aplicación. Las imágenes se
obtuvieron en navegador real contra dobles; no se ejecutaron proveedores reales.

No se deduce render Mermaid del balance de delimitadores: sigue pendiente para
antes de la auditoría E. La instalación
por una persona ajena al equipo y la aceptación pública #133 continúan pendientes;
no se declara cumplido el #57.

## Etapa E — Preparación final y auditoría

21/10/2026. Se preservaron B/C/D y sus registros históricos. Antes de editar:
misma rama `feature/issue-57-readme-final`, índice vacío, HEAD/develop/origin-develop
locales en `f1f9ae6`, divergencia local 0/0. Únicos cambios existentes: README,
arquitectura, este registro y evidencia #57. No aparecieron cambios ajenos.
Arquitectura conserva SHA-256 `f4c616a72df3f60a7f53b79f5e7c2e397ed7e4124e379c082d396ed982af4a74`.
Sus referencias a etapas pendientes describen el alcance histórico de B; la
situación actual se registra aquí, sin reescribir ese documento aprobado.

### A. Corrección técnica del README

Se incorporó navegación a instalación, arquitectura, uso y evidencia. Se retiró
el lenguaje interno de etapas del README, conservando pendientes comprensibles
y enlaces al historial especializado. Comandos y recorridos B/C se conservan;
solo cambia el texto de seguimiento. El README diferencia local, simulación y
aceptación pública, y mantiene la limitación demo compartida→generación.

**Mermaid validado localmente:** parser Mermaid 12.2.0 y renderer CLI 12.0.0,
Puppeteer 25.15.0 / Chrome 155.0.8059.84. Ambos `flowchart-v2`, cuatro renders
SVG/PNG exit 0 e inspección visual de los dos PNG. No hubo cambios semánticos
en diagramas. [Herramientas, fuentes, renders, incidencias y límites](evidencia/issue-57/mermaid/README.md).
No se afirma render visual en GitHub ni revisión humana por esta comprobación.

**Aclaración A/B/C:** el faltante de seis JSON originales registrado en D describía
lo no localizado, pero no implica que #57 exija capturas HTTP originales. Bajo el
ajuste aprobado se agregaron tres cuerpos de entrada reconstruidos, idénticos a
los parámetros históricos del catálogo, marcados como reconstrucciones. Se validó
cada campo contra `GenerateRequest`/`AlcanceSolicitud` y se cotejó con escenarios,
catálogo e informe. No falta ningún campo obligatorio; no se insertó
`modo_generacion` ausente, cuyo default actual es Multiagente. **Quedan tres
salidas canónicas faltantes**, no reconstruibles. [Procedencia y hashes](evidencia/issue-57/README.md#entradas-reconstruidas-y-salidas-que-faltan).
No se repitieron búsquedas masivas ni se accedió a proveedores.

### Referencias locales y comprobación directa en GitHub

- **Local:** enunciado PDF/TXT, decisiones §20, plan §7, código/contratos,
  manifests/CI/Compose/Vite, informes #53/#54/#55/#58/#59/#60/#139 y evidencia D.
  Se revisaron especialmente `config.py`, `main.py`, helper, rutas/servicio de
  generación, `basic.py`, `graph.py`, `critic.py`, persistencia y los enlaces a
  módulos de arquitectura. No se importó la aplicación ni se leyó `.env` personal.
- **GitHub directo, solo lectura:** `compare_commits` con base SHA `f1f9ae6…`
  y head remoto `develop` devolvió `identical`, 0 ahead/behind, 0 commits y archivos.
  Esto confirma coincidencia en el momento de consulta; **no se usa
  `origin/develop` local como prueba de actualidad remota**. No se ejecutó fetch/pull.
- `fetch_issue` leyó cuerpos/estados de #57, #56, #58, #60 y #133. #57/#56/#133
  abiertos; #58/#60 cerrados. En #57 siguen sin marcar los tres criterios;
  #133 mantiene VM/HTTPS/protocolo público pendientes pese a marcar reproducción
  del runbook. Los informes locales siguen declarando huecos y revisión pendiente.
  #60 conserva criterios humanos sin marcar y el reporte local provisional no
  acredita la muestra. Se ratifican las contradicciones de la tabla histórica,
  sin resolverlas por inferencia ni atribuir firmas. #56 abierto no bloquea E.
- [Registro de consulta GitHub](evidencia/issue-57/github-verificacion.json).
  No se comprobaron por red todas las demás URLs externas; los enlaces locales
  y anclas se auditaron contra archivos existentes.

### B. Comandos ejecutados y límites

No se repitieron instalación de NuevaMente, build, helper, capturas ni suites de
B/C/D: no hubo cambios de aplicación o comandos de instalación que lo justificaran.
Sus resultados y versiones se conservan en los registros anteriores, sin convertir
el snapshot de C en instalación por un tercero.

En E se ejecutaron inspección Git, comprobaciones documentales y los utilitarios
aislados de parser/render. Node 24.19.0, npm 12.0.2, Python 3.11.9 y los paquetes
existentes del entorno local se usaron para validar JSON/contrato/hashes/enlaces.
Se descargaron únicamente herramientas Mermaid/Chrome, con las aprobaciones
registradas. [Comandos e incidencias de Mermaid](evidencia/issue-57/mermaid/README.md#ejecución-aislada-y-límites).
No se agregaron dependencias al proyecto, no se arrancaron proveedores ni servicios
NuevaMente, no se borraron datos o volúmenes y no se escribieron referencias Git.

La comprobación documental de E incluye: igualdad de fuentes `.mmd` con ambos
bloques del README; parseo JSON y validación Pydantic de las tres entradas;
hashes de fuente/catálogo/informe, entradas, capturas y renders; concordancia de
IDs/parámetros/tiempos históricos; enlaces/anclas locales, fences y whitespace;
patrones de secretos en textos nuevos/diff y revisión de SVG. Los hashes esperados
de salidas **no se verificaron contra bytes ausentes**. El escáner de patrones y
la revisión visual previa de JPEG no equivalen a una garantía absoluta de ausencia
de secretos ni a OCR. El resultado final de la auditoría se consigna debajo.

### C. Instalación por una persona ajena al equipo

**Pendiente.** Se preparó una [plantilla sin completar](evidencia/issue-57/protocolo-voluntario.md)
con versión/hash de README, entorno, recorrido, comandos/cwd, resultados,
dificultades y confirmación identificada. Una persona ajena al equipo debe seguir
solo el README y registrar cualquier ayuda recibida. Docker con mocks por sí solo
no prueba carga/generación; el helper acredita un recorrido simulado, no Gemini.
La aprobación del usuario para continuar etapas no se presenta como esa prueba.

### D. Aceptación pública de #133

**Pendiente y separada.** Ni la corrección documental, ni Mermaid, ni las capturas
simuladas, ni la instalación local acreditan VM/Always Free/HTTPS, SSE público,
constancia humana o reproducción del runbook. Las casillas declaradas en GitHub
se distinguen de la evidencia consultada. No se ejecutó una nueva comprobación
OCI/Gemini, ni se aprovisionó ni se contactó integrantes.

## Matriz final documental del #57

Estados: **auditado localmente** indica comprobación técnica del agente con límites;
**parcial** indica evidencia/ejecución incompleta; **pendiente humano/externo**
requiere el aporte indicado. Ningún estado representa cierre o aceptación de #57.

| Criterio/tarea de #57 | Sección del README | Evidencia o comprobación | Estado | Pendiente humano/externo |
|---|---|---|---|---|
| Producto, problema y capacidades | [Portada y capacidades](../README.md#qué-permite-hacer) | Enunciado, decisiones, frontend actual y límites demo contrastados | Auditado localmente | Revisión final del equipo |
| Capturas de la UI vigente | [React simulado](../README.md#react-actual-con-proveedores-simulados) | [Cuatro JPEG y procedencia](evidencia/issue-57/capturas.json), inspeccionados en D, hashes conservados | Auditado localmente, simuladas | Evidencias humanas/recorrido real de #56/#133 |
| Stack y responsabilidades | [Stack](../README.md#stack-y-responsabilidades) | Manifests y [mapa de módulos](arquitectura.md#2-diagrama-de-contenedores) | Auditado localmente | Revisión del equipo |
| Arquitectura sin componentes ficticios | [Arquitectura](../README.md#arquitectura-implementada) | [Fuente, parser, SVG/PNG](evidencia/issue-57/mermaid/README.md); tabla de módulos de arquitectura | Auditado localmente | Confirmación final del equipo sobre versión revisada |
| Flujo RAG/agentes, modos, retornos y persistencia | [Documento→estudio](../README.md#del-documento-al-material-de-estudio) | Grafo/básico/Critic/servicio contrastados, [render de flujo](evidencia/issue-57/mermaid/flujo.svg) inspeccionado | Auditado localmente | Revisión del equipo; evaluación humana de calidad en #60 |
| Prerrequisitos, clone develop y configuración inicial | [Instalación](../README.md#prerrequisitos-y-descarga) | Código/CI/manifests; versiones y [ejecución C](#b-comandos-efectivamente-ejecutados) | Auditado localmente con límites | Clone de red y ejecución por voluntario; Bash no ejecutado en Linux/macOS |
| Local sin credenciales OCI | [Helper](../README.md#1-recorrido-funcional-simulado) | Helper aislado, TXT→ready, ambos modos, fuentes/descargas/recuperación ejecutados en C/D | Auditado localmente con dobles | Reproducción siguiendo solo README por tercero |
| Modo nativo real OCI+Gemini | [Arranque real](../README.md#2-arranque-nativo-con-oci-y-gemini-reales) | Configuración, cwd/env_file, DATA_DIR, modelos/cuotas/credenciales cotejados; no ejecutado | Parcial: contraste estático | Operador autorizado configura cuenta y registra ejecución/consumo |
| Docker y alcance de mocks | [Docker](../README.md#3-docker-con-mocks-arranque-y-conectividad) | Compose, Dockerfiles, proxy y health cotejados; Docker no disponible | Parcial: no ejecutado | Persona con Docker ejecuta comandos y documenta arranque; mocks no prueban generación |
| Variables y carga efectiva de configuración | [Configuración real](../README.md#2-arranque-nativo-con-oci-y-gemini-reales) | Configuracion/OCI/cuotas/helper; sin leer .env personal | Auditado estáticamente | Validar valores propios sin divulgar secretos; runbook real sigue #133 |
| Uso inicial, estudio, fuentes, exportaciones e historial | [Uso](../README.md#uso-inicial) | Recorridos locales C y capturas D, enlaces SSE/estudio/exportación; límite demo conservado | Auditado localmente, simulado | Voluntario y protocolos humanos #56; recorrido público #133 |
| Tres escenarios con JSON de entrada/salida reales | [A/B/C VCN v2](../README.md#escenarios-abc-vigentes-misma-fuente-vcn-v2) | [Tres entradas reconstruidas](evidencia/issue-57/README.md#entradas-reconstruidas-y-salidas-que-faltan), contrato validado y PDF/hash concordante; salidas ausentes | Parcial | Custodio de #55 aporta tres canónicos originales con IDs/procedencia; cotejar bytes/hash/contrato |
| Ocho mínimos, cinco diferenciales y extras con evidencia | [Cumplimiento](../README.md#cumplimiento-y-evidencia-disponible) | 25 filas contrastadas con enunciado/decisiones/plan, separan implementación/evidencia/aceptación | Parcial: evidencia incompleta explícita | Salidas A/B/C, reporte #60, acta #56 y aceptación #133; cierre de issues no basta |
| Créditos del equipo | [Equipo](../README.md#equipo-y-evidencia-final-de-demo) | Solo Grupo 10/Equipo 38/ONE/Alura en fuentes explícitas | Parcial | Equipo aporta nómina pública y rol confirmado de cada integrante |
| Instalación por una persona fuera del equipo | [Revisiones pendientes](../README.md#api-calidad-y-revisiones-pendientes) | [Protocolo vacío](evidencia/issue-57/protocolo-voluntario.md); ejecución del agente no lo sustituye | Pendiente humano | Voluntario identificado: versión, entorno, pasos, resultados, dificultades y confirmación |
| Evidencia enlazada y revisión final del equipo | [Evidencias y revisión](../README.md#api-calidad-y-revisiones-pendientes) | Auditoría local de enlaces/seguridad/alcance; [lectura GitHub](evidencia/issue-57/github-verificacion.json) | Pendiente humano | Equipo revisa README completo y matriz con versión/fecha y conclusión identificada |
| Coordinación con #56/#58/#60 y publicación #133 | [Equipo y estado](../README.md#equipo-y-evidencia-final-de-demo) | #56 abierto no bloquea; [contradicciones conservadas](#contradicciones-y-pendientes-conservados), consultadas de nuevo en E | Pendiente humano/externo | Evidencia final #56; conciliar #58/#133 y #60; protocolo público identificado en #133 |

## Faltantes y aportes necesarios

| Faltante concreto | Quién / qué debe aportar para resolverlo |
|---|---|
| Tres salidas canónicas A/B/C VCN v2 | Persona que conserva los objetos de #55: los tres JSON originales correspondientes a los generation_id y SHA-256 del [inventario](evidencia/issue-57/escenarios-vcn-v2.json), con procedencia. Luego validar bytes/hash/contrato sin regenerar contenido |
| Créditos nominales | Equipo: nombre público, usuario/enlace y rol confirmado de cada integrante; no inferirlos de commits |
| Instalación por tercero | Persona ajena al equipo: completar [plantilla](evidencia/issue-57/protocolo-voluntario.md) sobre la versión exacta del README, con resultados reales y confirmación identificada |
| Docker y recorrido real no ejecutados aquí | Operador autorizado: entorno/versiones, configuración saneada, comandos/resultados y límites. Docker requiere instalación disponible; proveedores reales requieren autorización y cuotas propias |
| Evidencia final #56 | Responsables de demo: guion/ensayo medido/video y protocolo M1–M5 local firmado por dos personas. Incorporar enlaces solo cuando existan; no bloquea esta auditoría |
| Contradicción #58/#133 | Responsable de despliegue y revisor distinto del autor: versión/acta/evidencias de reproducción, persistencia y recuperación; aclarar huecos 17.1/17.4 frente a casilla marcada |
| Contradicción #60 | Responsables de evaluación: informe final y anotaciones de 12 casos por dos revisores ES/EN/PT, correcciones/reevaluaciones, o aclaración explícita del cierre; el provisional no acredita esos resultados |
| Aceptación pública #133 | Operador y dos revisores: URL/VM/Always Free/HTTPS, protocolo público, versiones/proveedores/consumo y evidencias exigidas, incluida trazabilidad de casillas ya marcadas |
| Revisión final del equipo | Persona(s) del equipo: confirmación identificada/fechada sobre README/matriz exactos y limitaciones aceptadas; esta auditoría del agente no la sustituye |

No se declara cerrado ni cumplido #57. Se detiene el trabajo para revisión, sin
commit, push, merge, rebase, reset, cambio de rama ni escrituras en GitHub.

### Resultado de las comprobaciones finales de E

`.venv/Scripts/python.exe .data/issue57-e-auditar.py` terminó con exit 0:
**seis Markdown, 298 enlaces locales, 60 anclas y siete JSON**; tres entradas
Pydantic válidas y equivalentes al catálogo/escenarios. PDF/catálogo/informe,
entradas, cuatro capturas y seis artefactos de diagramas concordantes por SHA-256;
dimensiones PNG/JPEG concordantes con sus manifiestos. Dos `.mmd` idénticos al
README y versiones de herramientas cotejadas. SVG sin scripts, manejadores de
eventos ni recursos remotos; fuentes embebidas y referencias internas permitidas.
Control de patrones de secretos en textos nuevos/diff y claves sensibles JSON:
sin hallazgos, con los límites indicados. `git diff --check` aprobó.

Se ejecutaron `node .data/issue57-e-parser.mjs` (dos `flowchart-v2`, exit 0),
`mmdc --version` (12.0.0) y los cuatro renders reseñados. Los intentos fallidos de
herramientas se conservan en su registro, sin contarlos como aprobados.
No se volvieron a ejecutar comandos de instalación ni suites de NuevaMente.

Estado final: misma rama y HEAD; índice vacío. `git status --short` conserva
README y arquitectura modificados, y este registro/carpeta de evidencia #57 sin
rastrear. **Arquitectura no cambió en E**; su diff procede de B. El diff acumulado
de archivos rastreados contra develop tiene 586 inserciones y 495 eliminaciones
(README: 435/215; arquitectura: 151/280). Git no incluye en ese stat los archivos
nuevos: registro de verificación y 20 archivos bajo evidencia #57. En E se editaron
README, este registro, índice documental de evidencia e inventario de escenarios;
se agregaron tres entradas reconstruidas, lectura GitHub, plantilla del voluntario
y ocho archivos de Mermaid. Cuatro capturas y su manifiesto se preservaron.
Los utilitarios y herramientas aisladas quedan en `.data`, ignorados por Git;
no forman parte de las dependencias ni del entregable versionable.
