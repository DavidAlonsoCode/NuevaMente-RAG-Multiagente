# Validación de los diagramas Mermaid del README

21/10/2026, código base `f1f9ae61a5e52c7f0ef2411754ebd94738400318`.
Las fuentes son copias exactas de los dos bloques Mermaid del README revisado.
No se modificaron las cajas, textos ni transiciones aprobadas; se agregó el enlace
a este registro. La correspondencia con módulos reales está en
[arquitectura](../../../arquitectura.md#2-diagrama-de-contenedores) y
[flujo](../../../arquitectura.md#3-flujo-de-datos-extremo-a-extremo).

## Herramientas y comprobaciones

- Windows AMD64, Node 24.19.0, npm 12.0.2.
- Mermaid CLI **12.0.0**, motor Mermaid **12.2.0** y Puppeteer **25.15.0**.
- Chrome for Testing **155.0.8059.84**, ejecución headless con ruta explícita.
- Parser: `mermaid.parse` sobre cada `.mmd`, con DOM de jsdom **30.1.3**
  del entorno frontend ya instalado. Resultado en ambos: `flowchart-v2`, sin errores.
- Renderizador: CLI `mmdc` sobre cada fuente, salida SVG y PNG, fondo blanco.
  `--size 1800` para componentes y `--size 2400` para flujo. Exit 0 en los cuatro renders.
- Inspección visual directa de ambos PNG: etiquetas completas, sin cortes de cajas
  o textos; flechas de entrada/salida distinguibles. En flujo se siguen ambas ramas,
  los retornos de Critic a Writer/Researcher y la persistencia antes de completed.
  Las aristas que se cruzan no indican un nodo compartido. El flujo es vertical y
  requiere desplazamiento/zoom; abrir SVG permite ampliar sin perder definición.

| Diagrama | Fuente idéntica al README | Render vectorial | Imagen inspeccionada |
|---|---|---|---|
| Componentes | [componentes.mmd](componentes.mmd) | [componentes.svg](componentes.svg) | [componentes.png](componentes.png) |
| Ingestión, RAG y generación | [flujo.mmd](flujo.mmd) | [flujo.svg](flujo.svg) | [flujo.png](flujo.png) |

[Huellas y dimensiones](verificacion.json). El resultado acredita parser,
render e inspección locales con esas versiones; no afirma inspección visual del
renderizador de GitHub, de otros temas ni una aprobación humana del equipo.

## Ejecución aislada y límites

Desde la raíz, se consultó la versión en npm y se ejecutó:

```console
npm.cmd view @mermaid-js/mermaid-cli version --fetch-retries=0 --fetch-timeout=15000
npm.cmd install --prefix .data/issue57-e-tools --no-save --package-lock=false @mermaid-js/mermaid-cli@12.0.0 --fetch-retries=0 --fetch-timeout=30000
```

El intento de consulta en sandbox falló por DNS; la consulta e instalación
aprobadas terminaron correctamente (195 paquetes). npm bloqueó el postinstall de
Puppeteer. Con aprobación se ejecutó su `install.mjs`, fijando
`PUPPETEER_CACHE_DIR` a `.data/issue57-e-tools/browser-cache` y
`PUPPETEER_SKIP_CHROME_HEADLESS_SHELL_DOWNLOAD=true`: descargó solo Chrome.
No se agregaron dependencias, manifiestos o locks al proyecto.

Se creó `.data/issue57-e-tools/puppeteer.json` con `executablePath` al Chrome
descargado y `headless: true`. Comandos finales (desde raíz):

```console
node .data/issue57-e-parser.mjs
.data/issue57-e-tools/node_modules/.bin/mmdc.cmd -p .data/issue57-e-tools/puppeteer.json -i docs/evidencia/issue-57/mermaid/componentes.mmd -o docs/evidencia/issue-57/mermaid/componentes.svg --size 1800 -b white
.data/issue57-e-tools/node_modules/.bin/mmdc.cmd -p .data/issue57-e-tools/puppeteer.json -i docs/evidencia/issue-57/mermaid/flujo.mmd -o docs/evidencia/issue-57/mermaid/flujo.svg --size 2400 -b white
```

Para PNG se ejecutaron las mismas dos órdenes cambiando solo la extensión de
salida a `.png`. Los scripts de `.data` son utilitarios descartables de esta
sesión, no una funcionalidad del producto ni prerrequisito de instalación.

Incidencias registradas: CLI 12 rechaza `-w/-H` (se usó su opción `--size`);
el modo predeterminado buscaba chrome-headless-shell (se seleccionó Chrome);
el sandbox impidió iniciar Chrome (renders repetidos con aprobación). El parseo
inicial en Node sin DOM falló en DOMPurify; con jsdom y luego importación dinámica
de Mermaid aprobó. Ninguno de esos fallos se presenta como validación exitosa.
No se arrancó NuevaMente ni se consultaron proveedores durante esta comprobación.
