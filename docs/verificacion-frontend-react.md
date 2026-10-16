# Verificación de la migración a React

Fecha: 15 de octubre de 2026. Rama local: `feature/frontend-react-redwood`,
con correcciones sobre `8602096` e integración de develop `b95c0ec`. No acredita un despliegue público ni el cierre
de los issues de entrega.

## Correcciones verificadas

- Sesión en memoria, creación única bajo StrictMode y rechazo de respuestas
  tardías de otro espacio. El idioma es la única preferencia en `sessionStorage`.
- Intenciones de carga identificadas por contenido y título; seguimiento de
  ingestión y de visión pendiente sin dar el documento por terminado antes de tiempo.
- Chat y glosario conservan su estado al navegar, aislados por contexto. Los
  resultados inmediatos y consultados pasan por la misma validación.
- Historial y comparación comprueban identidad y persistencia; una respuesta
  lenta no reemplaza la página correspondiente al filtro actual. Las tarjetas y
  las fuentes no reutilizan estado de otra generación o documento.
- Identificadores de progreso compatibles con la serialización del backend,
  timeout que cubre el cuerpo HTTP, etiquetas de controles y foco de modales.
- Proxy estático con contención de rutas, cuerpos acotados y entrega SSE sin
  esperar un bloque mínimo de bytes. Dependencias npm fijadas y actualizadas.
- Rutas de pruebas y CI adaptadas al archivo histórico `frontend_streamlit_viejo/`.
  Las pruebas ordinarias aíslan el `.env` personal; los proveedores simulados
  siguen requiriendo inyección explícita.
- Compose sirve el build React y verifica sus assets y proxy API; se retiran las
  variables de Streamlit del overlay de producción. La configuración histórica
  de `.streamlit/` sigue alineada con la carpeta anterior para sus pruebas.
- Inter y JetBrains Mono se sirven desde dependencias npm fijadas, bajo la CSP
  local, sin Google Fonts. La CI añade navegador React con carga documental
  detrás de Caddy y TLS locales, además de conservar los checks históricos.

## Pruebas automáticas

| Comprobación | Resultado local |
| --- | --- |
| React/Vitest: formatos, sesión, cargas, chat, glosario, fuentes, progreso, filtros y timeout | 18 aprobadas |
| Servidor estático/proxy: rutas, límites y entrega inmediata SSE | 5 aprobadas |
| Verificador público Caddy, incluidos plazos de sockets | 91 aprobadas |
| TypeScript y build Vite de producción | Aprobados |
| Auditoría npm tras actualizar dependencias | 0 vulnerabilidades informadas |
| Ruff, formato Python y `git diff --check` | Aprobados |

La corrida general ordinaria produjo **2.618 aprobadas, 5 fallidas, 7 omitidas y
10 deseleccionadas**. Las cinco fallidas pasaron al repetirlas aisladamente:
historial de generaciones, dos plazos nativos SSE y dos exportaciones. Tres
alcanzaban el límite de espera de 15 segundos del test mientras el worker seguía
ejecutando; no se reprodujeron en la repetición aislada. No se ampliaron esos
límites ni se omitieron las pruebas.

Para SSE se comprobó que Python 3.11 en este Windows usa `GetTickCount64` para
`monotonic` (resolución de 15,625 ms). Un socket puede agotar su timeout antes del
siguiente tick. El verificador ahora contrasta también el tiempo de alta
resolución de la lectura, conservando como error un timeout realmente prematuro.
Sus 91 pruebas pasaron después de la corrección. **No se repitió toda la suite
general después de esa corrección**; estos resultados no equivalen a una corrida
general completamente verde.

Las comprobaciones dirigidas de los grupos originalmente señalados también
pasaron: 53 pruebas de jobs, generación, salud, chat e historial, y 192 de
recuperación documental, errores HTTP y regresiones. Ninguna de estas corridas
consumió proveedores reales.

Tras incorporar develop `b95c0ec`, la corrida dirigida de gateway, jobs,
OpenAPI/referencia HTTP, despliegue y plazos Caddy dio **280 aprobadas y 3
fallidas**. El ejemplo curl falló porque Git Bash no recibió el directorio de
`jq` en su PATH; pasó al configurar ese entorno desde el proceso Python. Los
dos casos TLS pasaron aislados sin cambios ni ampliación de plazos. TypeScript,
las 18 regresiones React, Ruff y OpenAPI sin drift también pasaron.

## Navegador y evidencias

Se utilizó `backend.tests.integration.react_backend` con directorio nuevo en
`.data/`, sin cargar `.env`, con Gemini y almacenamiento simulados explícitamente
y resolución DNS externa bloqueada. SQLite, Chroma, worker y grafo fueron los
del proyecto. El contenido educativo producido es sintético.

Se comprobó carga de texto hasta `ready`, generación y apertura de los cinco
formatos, reverso de flashcards, corrección inmediata del quiz, citas, progreso,
recuperación del espacio, historial y comparación de adaptaciones. La interfaz
ES/EN/PT se cambió independientemente del idioma del contenido. Se descargaron
PDF y APKG desde el historial: el PDF se pudo leer y el APKG era un ZIP íntegro
con una colección SQLite. Esto **no acredita importación manual en Anki Desktop**.

Se inspeccionaron anchos de 320, 768, 1024 y 1440 píxeles. Se corrigió el
desbordamiento encontrado a 320 px. Las capturas corresponden a datos simulados:

- [Escritorio](evidencia/frontend-react/desktop-simulado.jpg).
- [Móvil](evidencia/frontend-react/movil-simulado.jpg).

Además del servidor de desarrollo se abrió el build compilado a través de
`frontend/entrypoint.py`: recuperó el espacio y mostró sus cinco generaciones,
sin errores ni advertencias en la consola del navegador.

## Límites y pendientes

- Docker Desktop no tenía un motor disponible. No se ejecutaron localmente
  build de contenedores, ARM64 ni Compose. Los cambios de CI requieren su corrida.
- No hubo OCI/Gemini reales, VM, DNS, HTTPS público, interpretación visual real
  ni evaluación humana. Las pruebas históricas Caddy/Streamlit se conservan;
  la nueva prueba React/Caddy local tampoco constituye aceptación pública.
- El seguimiento de generación sigue consultando estado periódicamente; no
  completa SSE, reconexión y recuperación de #33. Las descargas comprobadas no
  cierran por sí solas todos los criterios de #46.
- El catálogo demo sigue siendo de lectura; no se habilitó generación directa
  desde fuentes compartidas. No se certificó una biblioteca OCI real desde React.
- La integración se entrega mediante PR a develop. `.env` y el material del
  usuario en `scripts/` se conservan; no forman parte del commit.

Para arquitectura y arranque local, ver la [guía del frontend](../frontend/README.md).
