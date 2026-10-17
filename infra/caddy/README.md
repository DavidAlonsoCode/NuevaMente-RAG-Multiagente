# Proxy del host — Issue 48, implementación y protocolo de aceptación

> **Frontend vigente — #139, 17/10/2026:** sólo se despliega y prueba React.
> El harness de proxy usa el build real para interacción, recarga, carga en el
> área principal y auditoría de secretos; conserva HTTP/1.1, HTTP/2, SSE,
> cancelación, subidas lentas y diagnósticos cerrados. No hay WebSocket de UI
> ni configuración CORS/XSRF de Streamlit que activar. Las corridas anteriores
> que mencionan Streamlit/WebSocket son evidencia histórica de sus commits,
> no controles ejecutables del frontend actual. Para arranque y cobertura vigente,
> ver [frontend](../../frontend/README.md) y [#139](../../docs/verificacion-issue-139.md).

La revisión documental de #58 del **16/10/2026**, sobre `develop` `f292415`,
registró #48 integrado y cerrado, React (PR #135) y SSE de #33 (PR #136).
Ese registro y las corridas Streamlit conservan fecha, versión y alcance
histórico; no acreditan esta integración ni una VM pública. VM, DNS/HTTPS público,
red y SSE con proveedores reales se aceptan en
[#133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133).
Ver el [mapa de fuentes y evidencias de #58](../../docs/verificacion-issue-58.md).
La [instalación consolidada](../../docs/despliegue-oci.md) documenta los pasos
operativos para una ejecución futura autorizada, sin acreditar su reproducción.

## Guía principal: instalar → arrancar → comprobar

Esta guía sigue el alcance vigente de [#48](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/48)
y el ajuste de [#128](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/pull/128).
Es una receta para una ejecución futura autorizada; no concede permiso para
instalar, activar servicios, contactar proveedores ni cambiar DNS/firewall.

1. **Instalar sin publicar.** En Ubuntu 24.04 **ARM64** con #47 comprobado,
   seguir [preparación de Caddy](#preparar-caddy-sin-publicar-un-sitio),
   [archivos canónicos y evidencia de red](#preparar-archivos-canónicos-y-evidencias-de-47)
   y [volumen e imágenes existentes](#volumen-existente-e-imágenes).
   `install.sh` verifica procedencia y prepara la unidad sin iniciarla.
   Sigstore requiere Rekor/TUF con caché nueva: no suponer verificación offline.
2. **Arrancar explícitamente.** Completar la [observación inicial del peer](#observación-inicial-antes-de-confiar-en-headers)
   en cuarentena, configurar sólo la IP comprobada y seguir
   [validación y activación](#validar-sin-publicar-activar-explícitamente).
   El orden es `--observe-peer`, completar la confianza, `--validate` y, sólo con
   autorización operativa, `--activate`. El overlay productivo fuerza ambos mocks
   a 0; esta operación no es un arnés con proveedores dobles. Ante fallo usar
   [recuperación](#fallos-y-recuperación), conservando journal, datos y evidencia.
3. **Comprobar y documentar.** Acreditar primero permisos, unidad efectiva/notify,
   socket, Docker/NAT y recuperación nativos. Después, con autorización pública
   separada, completar los [tres criterios](#criterio-1-https-público-y-ui-usable)
   desde el dominio y una red externa. Para exposición correlacionar todas las
   reglas OCI/firewall, interfaces/listeners y sondas externas de web, SSH y
   8000/8501 en cada dirección publicada. El [escaneo completo](#criterio-3-exposición-tcp-y-acceso-administrativo)
   es diagnóstico opcional; una contradicción observada impide aprobar y un
   timeout no demuestra puerto cerrado.

Una misma sesión autorizada en la VM de entrega puede compartir evidencia con
#53/#54; no se exige una segunda VM ni repetir toda la suite en ella. Si falta
capacidad Always Free, registrar intentos manuales acotados y el bloqueo. Una
demo local con Gemini/OCI reales requiere su autorización y no acredita Compute
desplegado. No comprar recursos ni debilitar HTTPS, origen o permisos.

## Evidencia Linux de 42af012

Estado revisado el **14/10/2026**, sin ejecutar nuevamente esos workflows:

| Ejecución | Evidencia observada y alcance |
| --- | --- |
| [Caddy 38626809239](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/actions/runs/38626809239) | **354 aprobados por arquitectura**, Ubuntu 24.04 AMD64/ARM64, sin fallos/errores/skips. Cobertura 89 configuración + 142 despliegue + 113 reporte/diagnóstico + 10 proxy; cuatro huellas esperadas/observadas iguales, sin duplicados. Caddy 2.11.7/Compose 2.40.3 reales, diagnóstico desactivado, backend/gestor/SQLite/Streamlit reales y proveedores dobles. |
| [CI 38626809237](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/actions/runs/38626809237) | Python 3.11/Linux: **2372 aprobados, 52 skips, 10 deseleccionados**; Ruff/formato aprobados. Los skips son 47 de configuración y cinco de despliegue sin Caddy/Compose en ese job: las mismas identidades pasan en Caddy. Los 10 proxy se seleccionan expresamente allí. La tanda adicional de 37 integraciones se solapa con la ordinaria. |
| [Contenedores 38626810038](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/actions/runs/38626810038) | Tres jobs aprobados: Compose base con mocks, aislamiento y persistencia SQLite/volumen tras recreación; ambas imágenes construidas/ejecutadas en ARM64 nativo con UID 10001 y Chroma nativo. No acredita activación del overlay productivo. |

HEAD de las tres ejecuciones: `42af012dfb0ef23c062888e9f3179104eacee917`.
El checkout efectivo fue el merge temporal
`cf48f9702fbeca297ec5aa7ce532b096a468c5c9`, con base
`5289ed8e3470c6e923e9addd1d1d83e1fc9df1b3`. El `develop` revisado después,
`8f5449045e8b8e80656dd6da514952ced0e61054`, incorpora #34: esa combinación
posterior requiere los checks del siguiente push manual. No sumar los casos
solapados ni las dos arquitecturas como identidades independientes.

Las sondas registraron interacción/reconexión de Chromium 153.0.8010.12 fijado,
WS durante 130,33–130,42 s, SSE H1/H2 con silencio de 134,24–134,43 s y upload
progresivo de 72 s. Preparación, integridad, permisos y carga/confirmación del
perfil AppArmor de CI pasaron en ambas arquitecturas, conservando el sandbox.
Esto acredita compatibilidad de la intervención en esos runners, sin demostrar
retrospectivamente la causa única de sus fallos anteriores ni aceptar OCI público.

**Pendientes actuales:** instalación/idempotencia/permisos de despliegue,
systemd/notify/socket y recuperación nativos Ubuntu ARM64; Docker/NAT/peer del
modelo productivo; los tres criterios públicos. CI no los sustituye. Los timeouts
y fallos `frontend-start`/`reload` de Windows conservan sus reportes y causa no
demostrada; no quedan resueltos retrospectivamente ni obligan a nueva investigación
sin un riesgo vigente demostrado. El reporte público describe sus observaciones,
sin incorporar estados fijos de CI ni bloqueos históricos. Los registros fechados
y plegados más abajo conservan el estado de cada revisión, no el estado actual.

## Referencia de configuración y procedimientos

Este directorio define configuración, contratos y herramientas de instalación,
despliegue y recuperación para **Caddy 2.11.7** en Ubuntu 24.04 ARM64.
Las herramientas requieren ejecución explícita por un administrador en un host
autorizado; escribirlas o probarlas con dobles no instala ni activa servicios.
La Etapa 3 conserva el arnés de integración Caddy/SSE/Streamlit histórico.
La verificación de React se registra por separado en
[verificación del frontend](../../docs/verificacion-frontend-react.md) y
[verificación de #33](../../docs/verificacion-issue-33.md).
La aceptación pública descrita en la Etapa 4 permanece en #133; ejecutar sus
procedimientos requiere autorización operativa.

## Entrada y contratos

| Entrada | Destino/comportamiento |
| --- | --- |
| HTTP TCP/80, hostname explícito | ACME HTTP-01 nativo y redirección 308 a `https://hostname{uri}` |
| HTTPS TCP/443, `/api` o `/api/*` | `127.0.0.1:8000`, sin retirar prefijo ni reescribir rutas |
| HTTPS TCP/443, resto | `127.0.0.1:8501`, build estático de React |
| Administración | `unix//run/caddy/admin.sock|0200`, sin listener TCP administrativo |

El nombre DNS se carga como `NUEVAMENTE_DOMAIN`: hostname ASCII/punycode en
minúsculas, con al menos dos etiquetas, sin esquema, puerto, path, wildcard,
IP ni punto final. Caddy usa HTTPS automático público; no hay certificados
internos, on-demand, fallback HTTP para la aplicación ni proxy anterior de
confianza. `strict_sni_host on` exige coherencia SNI/Host. El sitio HTTP explícito
recibe la misma política de servidor; las rutas ACME nativas preceden al redirect.
El redirect usa el hostname configurado y conserva path/query, sin reflejar un
Host arbitrario. OpenAPI `/docs` y `/openapi.json` no son rutas API públicas en
esta topología; su política pertenece a #59.

El emisor ACME se fija a Let's Encrypt público. Las guardias rechazan sufijos
locales `.localhost`, `.local`, `.internal` y `.home.arpa`; no se permite activar
la selección automática de CA interna por un hostname mal configurado.

Ambos upstreams reciben `Host` y `X-Forwarded-Host` públicos,
`X-Forwarded-Proto: https` y `X-Forwarded-For` sobrescrito con la IP de conexión.
Se descartan `Forwarded` y `X-Real-IP` del cliente. Se conserva `Origin`.
React solicita `/api` al mismo origen; el navegador no recibe la clave privada
del proxy. El servidor estático agrega CSP y sirve las tipografías localmente.
El overlay no configura variables de Streamlit; las evidencias históricas de sus pruebas
de WebSocket se conservan y no acreditan la nueva interfaz.
No se alteran `Authorization`, `Idempotency-Key`, `Last-Event-ID`, `X-Request-ID`,
`Retry-After`, `Content-Type`, `Cache-Control` ni `X-Accel-Buffering` del contrato.

En API y UI se **sobrescriben**, nunca se agregan a la cadena
del cliente, `X-NuevaMente-Client-IP` y `X-NuevaMente-Origin-Key`. Se retiran ambas
de respuestas, incluida protección global sobre respuestas HTTP locales.
En producción React solicita `/api` al mismo origen y Caddy enruta esas
solicitudes directamente al backend; el navegador no debe recibir el secreto.
La transmisión desde `st.context.headers` y las pruebas de handshake/mensajes
WebSocket pertenecen al frontend Streamlit histórico de
`frontend_streamlit_viejo/`. No describen el transporte de React.

## Streaming y timeouts

Caddy 2.11.7 reconoce `text/event-stream` y aplica flush inmediato nativo.
Se mantiene ese comportamiento: sin `encode`, `flush_interval` artificial,
buffers de request/response, timeout total de stream o compresión en el
transporte a upstream. El contrato SSE #31 conserva heartbeat cada 15 s,
cursor `Last-Event-ID` y trabajo independiente de la conexión.

| Parámetro | Valor | Motivo |
| --- | --- | --- |
| Dial upstream | 3 s | Servicios locales; detectar indisponibilidad rápidamente |
| Headers públicos | 15 s | Acotar conexiones incompletas antes de recibir body |
| Inactividad de lectura de body | 60 s | Upload lento permitido mientras avance; sin límite total de lectura |
| Headers iniciales API | Sin límite del proxy | OCI tiene llamadas/deadline y reintentos; no existe presupuesto total HTTP común a todos los endpoints |
| Headers iniciales UI | 60 s | Inicio de respuesta del servidor de archivos React |
| Escritura bloqueada | 60 s de inactividad de escritura | Caddy 2.11.7 limita escrituras bloqueadas, no espera entre eventos |
| Conexión HTTP keep-alive ociosa | 120 s | Conexiones sin solicitudes; no streams activos |

Sin timeout total de respuesta/escritura ni `stream_timeout`. Público HTTP/1.1
y HTTP/2, sin HTTP/3/UDP; transporte local HTTP/1.1 y Upgrade nativo. La prueba
temporal de eventos pequeños, heartbeat con trabajo activo, reconexión/cursor,
desconexión sin cancelación ni duplicación y WS interactivo real queda en Etapa 3.

## Archivos privados y permisos futuros

| Ruta del host | Dueño/modo | Uso |
| --- | --- | --- |
| `/etc/caddy/Caddyfile` | root:caddy 0640 | Copia de esta configuración, sin clave literal |
| `/etc/caddy/caddy.env` | root:root 0600 | Copia completada de `caddy.env.example`; variables públicas |
| `/etc/nuevamente/secrets` | root:root 0700 | Directorio de la fuente canónica |
| `/etc/nuevamente/secrets/origin.env` | root:root 0600 | **Solo** `TRUSTED_ORIGIN_SECRET=<64 hex minúsculas>` |
| `/etc/nuevamente/backend.env` | root:root 0600 | Gemini, parámetros backend y datos OCI no secretos duplicados |
| `/etc/nuevamente/deployment.env` | root:root 0600 | Interpolación Compose basada en `.env.production.example` |
| `/etc/nuevamente/oci` | acceso mínimo a UID/GID 10001 | Usuario técnico #14, bind `/run/oci` RO, perfil `DOCKER` |
| `/run/caddy` | caddy:caddy 0700 | Socket administrativo caddy:caddy 0200 explícito; umask 0077 |
| `/var/lib/caddy` | caddy:caddy 0700 | Persistencia de certificados, claves y cuenta ACME |
| `/var/log/caddy` | caddy:caddy 0750 | Access/operational separados, archivos 0600 |

Generar la clave con 32 bytes criptográficamente aleatorios y representación
hexadecimal; este repositorio no contiene una clave de despliegue. Caddy carga
la misma `origin.env` mediante `EnvironmentFile` de systemd y backend mediante
`env_file` de Compose. systemd lee el archivo privado antes de ejecutar como
`caddy`; no hace falta concederle lectura directa de ese archivo.
`backend.env` se prepara desde `.env.example`, **quitando**
`TRUSTED_ORIGIN_SECRET`: esa variable solo pertenece a `origin.env`. Completar
Gemini, cuotas verificadas y OCI reales; el overlay fuerza `APP_ENV=production`
y ambos mocks a 0, y la validación crítica existente rechaza placeholders.
No copiar las credenciales del operador OCI #47 al backend. El perfil técnico
OCI mantiene permisos mínimos del bucket y lectura solo para UID 10001;
el frontend no monta `/run/oci` ni el volumen de datos.

El preflight de Etapa 2 comprueba rutas canónicas, dueños/permisos,
archivos con claves permitidas (sin duplicaciones), configuración completa y
hostname idéntico en `caddy.env` y `deployment.env`, además de
que Caddy/backend reciben el mismo secreto, comparándolo en memoria y mostrando
únicamente éxito/fallo. No imprimir valor ni hash. La rotación requiere reinicio
coordinado de Caddy y backend: una recarga no cambia el entorno del proceso Caddy
que resuelve `{env.TRUSTED_ORIGIN_SECRET}`. No ofrecer rotación parcial en vivo.

El drop-in reemplaza `ExecStart`/`ExecReload`, no utiliza `--environ`, debug ni
`--resume`; conserva la unidad oficial tipo notify. Crea directorios protegidos,
reduce capabilities a bind de puertos bajos y comprueba entorno y versión antes
de validar/iniciar. Vacía primero las listas de capabilities heredadas para
evitar que systemd las acumule con las de la unidad base. Recarga con guardia,
validación previa y envío al socket;
ninguna modificación debe aplicarse si la validación falla. Caddy no persiste
autosave (`persist_config off`); certificados/cuenta sí persisten. En Etapa 2
hay que impedir el arranque de la unidad/configuración por defecto durante la
instalación, preparar todo primero y activar solo tras preflight satisfactorio.
Esta etapa no instala ni habilita esa unidad.

## Compose efectivo y confianza Uvicorn

Requiere Compose **>= 2.24.4**, por `!override`; la validación local de esta etapa
usa 2.40.3. Orden obligatorio: `docker-compose.yml`, `docker-compose.oci.yml`,
`docker-compose.production.yml`. Un `--env-file` explícito evita interpolación
implícita del `.env` de desarrollo. `env_file: !override` reemplaza la lista
heredada: backend recibe exactamente `backend.env` y `origin.env`, en ese orden;
frontend recibe ninguno. Los archivos son obligatorios y las variables públicas
vacías causan error de Compose. Validación de sintaxis del hostname, clave y
peers ocurre además **antes** de importar/iniciar Uvicorn mediante la guardia del
`command`; Caddy tiene guardias equivalentes en el drop-in. `compose config -q`
por sí solo no prueba esa sintaxis ni las credenciales de aplicación.

La confianza en `X-Forwarded-*` de Uvicorn es distinta del contrato autenticado
de origen NuevaMente. **Prerrequisito pendiente del host real**: observar desde
el contenedor backend la IP TCP de una conexión iniciada por Caddy mediante el
binding loopback de Docker, sin confiar primero en headers reenviados. Registrar
red/interface/ruta de origen y repetir la observación tras recrear la red.
Configurar solo esas IPs literales comprobadas en `FORWARDED_ALLOW_IPS`, separadas
por coma; no `*`, CIDR, hostname, IP indefinida/multicast ni gateway supuesta.
No ejecutar la aplicación pública hasta completar esa comprobación. Verificar
después que redirects FastAPI mantienen HTTPS/hostname/ruta y que headers
falsificados no alteran el origen. Ese procedimiento se automatiza en Etapas 2–3.

La composición mantiene un worker Uvicorn, `--proxy-headers`, desactiva su access
log para evitar URI/query crudos y conserva el log middleware existente basado
en path. Mantiene UID 10001 de las imágenes, filesystem RO, tmpfs restringido,
capabilities vacías, no-new-privileges, healthchecks, gracia 70 s y red bridge con
salida a Gemini/OCI. No `internal: true`, modo host ni bindings públicos.
`nuevamente_backend_data` es el nombre estable del volumen. Antes de instalar,
identificar cualquier volumen con datos previo: no sustituirlo silenciosamente
por uno vacío. No ejecutar dos proyectos con ese volumen compartido (SQLite y
Chroma requieren un único escritor). Recreación/persistencia se prueban después.

#47 permite TCP/80 y TCP/443, y SSH solo desde el CIDR administrativo; no ampliar
esas reglas. Docker mantiene exclusivamente `127.0.0.1:8000/8501`; no depende del
firewall para ocultar las aplicaciones. No agregar UDP/443 ni abrir administración
Caddy. IPv6 también debe revisarse en aceptación; las imágenes conservan su
índice oficial multiarch de #26, sin fijar amd64. La construcción/ejecución en
ARM64 sigue necesitando Docker/runner Linux, independientemente de OCI.

## DNS, ACME y procedencia

Usar un dominio existente del equipo o un hostname gratuito aprobado; ninguna
compra o recurso se crea automáticamente. Una vez disponible la VM, resolver el
registro A a su IPv4 pública estable. Publicar AAAA solo si existe IPv6 verificada
en host/rutas/firewall. Revisar CAA, propagación y acceso externo TCP/80/443 para
HTTP-01/TLS-ALPN-01. Sin CDN/proxy intermedio ni ACME DNS plugin en este contrato.
Los certificados públicos los gestiona Caddy; no hay fallback a certificados
internos ni evidencia pública derivada de una CA de staging.

Fuente: [release oficial v2.11.7](https://github.com/caddyserver/caddy/releases/tag/v2.11.7).
Para ARM64: `caddy_2.11.7_linux_arm64.tar.gz`, SHA256
`d8fc6d179a5d283028a472a5618564f6ad8a86fed513e64f032b3b0b7cc45e42`.
Comparar también con el manifiesto oficial `caddy_2.11.7_checksums.txt` (SHA512
por archivo; SHA256 del manifiesto
`5d27b76b95f496638d79208c55250317b8755fd9e90005f7466aa32956944a90`).
La futura instalación verifica además firma Sigstore del artefacto con identidad
`https://github.com/caddyserver/caddy/.github/workflows/release.yml@refs/tags/v2.11.7`
e issuer `https://token.actions.githubusercontent.com`, según
[verificación oficial](https://caddyserver.com/docs/signature-verification),
y comprueba `caddy version`. No ejecutar un artefacto cuyo hash/firma no coincida.

Para estas comprobaciones se usó temporalmente el ZIP oficial Windows amd64:
SHA256 `0a1edc0b799512051c57071ce0e798d3f2cf67dc98171366f1d2b326072e3b06`,
SHA512 contrastado con el manifiesto verificado. Compose standalone oficial
2.40.3 Windows x86_64: SHA256
`4c864dd7f879dd40366e087e68a8a02cbcf018be0128867b13369898e67e1532`, publicado
en [release oficial](https://github.com/docker/compose/releases/tag/v2.40.3).
Esta comprobación de hashes local **no** acredita firma Sigstore ni ejecución
ARM64. Los binarios temporales están fuera de los archivos versionados.

## Logging y diagnósticos

Access (`access.json`): se eliminan request completo (URI/query, IP, headers,
cookies, TLS) y response headers; se conservan status, duración, tamaños, nivel,
timestamp, componente fijo `api/ui`, método solo si pertenece a la lista estándar
y UUID generado por Caddy. No se registra cuerpo. La solicitud original no se
modifica por estos filtros. El logger operativo excluye access para no duplicarlo.

Operativo (`operational.json`, INFO): se conservan nivel, timestamp, logger,
server, puertos, identificadores TLS/ACME, status/`err_id`/`err_trace` cuando el
emisor los aporta; se eliminan request, headers, URI/URL, bodies, error/err libres,
config y stacktrace. `msg` no es filtrable por el encoder de Caddy: se omite con
`message_key ""` para evitar que un error refleje texto no confiable. Esto reduce
el detalle textual de diagnóstico; correlacionar namespace, status, traza e ID
con access y backend. La configuración fija modo 0600, rotación 10 MiB, cinco
archivos y siete días en ambos logs. El socket administrativo solo admite acceso
local privilegiado. No habilitar `log_credentials`, trace ni debug.

Validación/adaptación CLI emiten mensajes anteriores al logger configurado:
solo usar secretos sintéticos para diagnósticos reproducibles. En producción,
el helper captura la salida CLI en memoria y entrega códigos de fase/herramienta
y exit status, sin salida cruda. El drop-in usa ese helper para validate/reload
y un supervisor para run que descarta stdout/stderr de Caddy antes del logger
configurado. Los logs operativos filtrados conservan diagnóstico de runtime.
Restringir el journal por sí solo no evita una filtración y no es este control.
El secreto usa
placeholder runtime, no expansión `{$TRUSTED_ORIGIN_SECRET}`, y no se incluye en
el JSON adaptado. Aun así, `compose config` **resuelto**, `docker inspect`, entorno
de procesos, `/config` administrativo y archivos privados pueden revelar
credenciales: acceso administrativo, archivos 0600 y nunca adjuntarlos a issues.
Usar `config --quiet` con datos reales y pruebas con datos sintéticos para
inspección. La ausencia de canarios en respuestas/WS/logs bajo tráfico, errores,
arranque y recarga se demuestra en Etapa 3; inspeccionar filtros no sustituye esa
evidencia. La sanitización de logging de proveedores fuera del proxy no se amplía
en esta etapa.

## Verificación local repetible

Las pruebas usan Caddy y Compose reales, sin activar servidores, Docker Engine
o proveedores. Solo requieren Python de tests y binarios verificados. Seleccionar
`CADDY_BIN`/`COMPOSE_BIN` cuando no estén en PATH; el archivo de tests informa
**skip** si falta una herramienta, nunca aprobación de la comprobación omitida.
Ejemplo PowerShell desde la raíz, con herramientas temporales ya verificadas:

```powershell
$env:CADDY_BIN = 'C:/ruta/verificada/caddy.exe'
$env:COMPOSE_BIN = 'C:/ruta/verificada/docker-compose.exe'
$env:APP_ENV = 'ci'
$env:MOCK_OCI = '1'
$env:MOCK_GEMINI = '1'
.venv/Scripts/python.exe -m pytest backend/tests/test_caddy_config.py -q
.venv/Scripts/ruff.exe check .
.venv/Scripts/ruff.exe format --check .
.venv/Scripts/python.exe -m pytest -q
```

`test_caddy_config.py` ejecuta `caddy fmt --diff`, adaptación JSON y validación
2.11.7 con hostname/secreto sintéticos y rutas efímeras, y casos inválidos mediante
el validador nativo. Inspecciona ambos servidores, rutas, cabeceras, protocolos,
timeouts, logging, socket y persistencia. Ejecuta Compose sobre **los tres
archivos** y comprueba modelo combinado y entorno resuelto sin cargar `.env`:
base apunta deliberadamente a un archivo de desarrollo inexistente. En Compose
2.40.3 `--no-env-resolution` sobre el modelo interpolado no conserva `env_file`;
las pruebas inspeccionan además `--no-interpolate --no-path-resolution` generado
por Compose para acreditar la lista efectiva y el booleano `create_host_path`.
No interpretan YAML/Caddyfile con un parser que replique sus reglas de merge.
Las guardias reales se ejecutan también con casos positivos/negativos, sin
arrancar Uvicorn, Caddy ni contactar proveedores.

En Linux, con unidad oficial 2.11.7 y este drop-in preparados en un entorno de
verificación, ejecutar `systemd-analyze verify caddy.service` y revisar unidad
efectiva, escaping, capacidades, EnvironmentFiles, directorios, permisos y
socket. La extracción/ejecución local del argumento Python prueba la guardia,
**no** el parser systemd. Windows sin systemd no permite acreditar esta revisión.
Los tests de generación de configuración tampoco acreditan permisos POSIX,
ACME/DNS públicos, WS/SSE temporal ni arranque de imágenes ARM64.

<details>
<summary>Historial local — Etapa 1, 12/10/2026 (estado de esa revisión)</summary>

### Resultado local observado — 12 de octubre de 2026

| Comprobación | Resultado observado |
| --- | --- |
| Tests de configuración, binarios Caddy 2.11.7/Compose 2.40.3 verificados | **89 passed**, sin skips; incluye fmt/diff, adapt/validate, merge/entorno Compose y casos negativos |
| Regresiones de origen, health, SSE TCP directo, API client, sesión UI y entrypoint | **70 passed**, dos warnings de deprecación Chroma |
| `ruff check .` / `ruff format --check .` / `git diff --check` | Aprobados; 220 archivos Python formateados |
| Suite ordinaria completa | **Sin aprobación**: 1.998 pruebas recolectadas; recorrido interrumpido tras observar tres fallos y seis aprobaciones en integración existente |
| Reproducción aislada de primer fallo | `test_upload_ready_generacion_exportacion_y_sse[flashcards]`: **1 failed**, generación todavía `running` al vencer los 15 s de `esperar`; causa interna sin resolver |
| Parser/unidad efectiva systemd, permisos/socket POSIX, imágenes ARM64 | **No ejecutado**: sin systemd ni Docker Engine/WSL Linux en este host Windows |
| Tráfico mediante Caddy, WS/SSE temporal y canarios en logs | **No ejecutado**, corresponde a Etapa 3 |
| Aceptación pública #48 | **Pendiente**, sin VM/dominio reales |

Los primeros intentos de suite fallaron en colección por `langgraph`, `genanki`
y `anki` ausentes. Se completó únicamente `.venv` con sus versiones fijadas en
el repositorio; `pip check` pasó. El sandbox bloqueó `asyncio.socketpair` en los
tests de API; las regresiones se ejecutaron con autorización de sockets locales,
mocks de Gemini/OCI y tracing desactivado. La suite completa se intentó con ese
mismo entorno y registró fallos; no se presenta el resto interrumpido como
ejecutado ni se aumentaron plazos/relajaron assertions para obtener verde.
El fallo aislado no utiliza esta configuración Caddy/Compose. No se alteraron
los módulos de aplicación ni sus tests. Los reportes locales están en
`.data/issue48-tools/` (ignorados): `stage1-reviewed.txt`,
`regressions-final.txt`, `full-suite-diagnostic.txt` y
`full-suite-first-failure.txt`. No acreditan producción ni una ejecución CI.

## Evidencia y puntos de parada

| Estado | Evidencia requerida |
| --- | --- |
| A. Implementación Etapa 1 | Estos templates, drop-in, overlay, tests y contratos listos para revisión; sin instalación |
| B. Validación local/automatizada | Resultados observados de herramientas reales, regresiones, Ruff y suite; skips/fallos identificados por separado |
| C. Aceptación pública pendiente | VM/dominio reales: UI usable con certificado confiable desde red externa; progreso SSE mediante proxy con generación real; solo 80/443 visibles desde IP no administrativa y SSH restringido. Sin sustitución por mocks/staging/CI |

Al cierre de Etapa 1 quedaban pendientes instalación reproducible, firma/verificación
ARM64, prevención de publicación predeterminada, permisos/consistencia y pruebas
de recuperación/idempotencia (Etapa 2); aplicación/gestor reales con dobles
explícitos de proveedores, stream temporal, reconexión, WS interactivo y canarios
de logging (Etapa 3). Las herramientas de Etapa 2 se implementan abajo; sus ensayos
reales Ubuntu/ARM64 siguen pendientes. Los dobles no sustituyen esos ensayos.

En Etapa 4: DNS A/AAAA/CAA desde resolvers externos; cadena y hostname del
certificado sin `-k`; HTTP→HTTPS con rutas/query; UI y API públicas; SSE con
eventos pequeños/heartbeat durante trabajo activo, cursor y desconexión; WS
interactivo/reconexión; verificación de origen y redirects. Escanear desde fuera
una superficie suficiente de puertos TCP y revisar IPv6/UDP para sostener que
solo 80/443 son accesibles públicamente; probar rechazo SSH desde IP no
administrativa y acceso desde la autorizada. Complementar con listeners,
bindings Docker, firewall host y reglas OCI #47. Registrar fecha, comandos,
resultados y límites sin secretos. La falta de VM no reduce esos requisitos:
los tres criterios públicos de #48 permanecen pendientes hasta evidencia real.

**Etapa 2 autorizada para implementación. Detenerse antes de Etapas 3–4.**

</details>

## Etapa 2 — preparación, validación, activación y recuperación

Los siguientes procedimientos son para una futura ejecución **específicamente
autorizada** en Ubuntu 24.04 ARM64. No ejecutarlos contra la estación Windows ni
la VM real como parte de la revisión de archivos. Ningún script cambia DNS,
firewall, recursos OCI, workflows o branches. No descarga/instala paquetes,
construye imágenes, genera secretos, crea volúmenes ni ejecuta migraciones.
La única red utilizada por `install` es la verificación pública Sigstore/TUF/Rekor;
los artefactos y el verificador se entregan previamente y se verifican antes de
ejecutar Caddy. `deploy --activate` sí inicia los servicios, y Caddy puede solicitar
certificados ACME: requiere autorización operativa aparte de esta implementación.

Requisitos del host: #47 completo, Python 3 del sistema, systemd con
`systemd-analyze`, Docker Engine ARM64 y Compose >=2.24.4, cloud-init terminado,
OpenSSH, iproute2, iptables/nftables y actualizaciones automáticas. El preflight comprueba
estos requisitos y no intenta repararlos. No ejecutar `verify_host.sh` como
subproceso: su `docker run hello-world` descargaría una imagen; se reutilizan sus
comprobaciones de estado, más la política efectiva y las restricciones de #48.

### Preparar Caddy sin publicar un sitio

El prerrequisito del verificador es **Cosign v2.6.5 linux-arm64**, obtenido de la
[release oficial de Sigstore](https://github.com/sigstore/cosign/releases/tag/v2.6.5).
Su SHA256, fijado antes de su ejecución, es
`426193b4c5da4d4d643e822f48fe0cc8a476ca1782a272704831f5a0cef716d7`.
Se comprueba además `cosign version --json`: `gitVersion == v2.6.5`.
No se usa un ejecutable encontrado en PATH ni se instala el verificador. El
[workflow oficial de Caddy v2.11.7](https://github.com/caddyserver/caddy/blob/v2.11.7/.github/workflows/release.yml)
usa esa misma versión por el formato de certificados/firmas separados.
El origen de confianza inicial es el hash revisado del artefacto oficial, no una
firma verificada circularmente por un binario todavía desconocido.

Preparar en un directorio root:root 0700, bajo padres root sin escritura de grupo
u otros (por ejemplo `/root/issue48-artifacts`), estos seis assets oficiales:

- `caddy_2.11.7_linux_arm64.tar.gz`
- `caddy_2.11.7_linux_arm64.pem` (sin `.tar.gz` en el nombre del certificado)
- `caddy_2.11.7_linux_arm64.tar.gz.sig`
- `caddy_2.11.7_checksums.txt`
- `caddy_2.11.7_checksums.txt.pem`
- `caddy_2.11.7_checksums.txt.sig`

Todos root:root 0600; el Cosign entregado root:root 0755 bajo padres protegidos.
Descargar únicamente desde las releases oficiales mediante HTTPS, sin `curl | sh`
ni desactivar validación TLS. El instalador exige SHA256/SHA512 del tar, hash
del manifiesto, su entrada SHA512 exacta y **ambas firmas**. Fija identidad de
workflow/issuer ya documentados, Rekor público, `--insecure-ignore-tlog=false`
y `--insecure-ignore-sct=false`. TUF/cache se guardan en un directorio temporal
privado y se eliminan; no reutiliza una raíz/caché del entorno del operador.
Si faltan red, firma, inclusión/transparencia o versión, se detiene antes de
ejecutar/publicar el binario. No hay fallback offline que omita transparencia.

Ejemplo de invocación futura con los archivos revisados:

```bash
sudo bash infra/caddy/install.sh \
  --artifacts /root/issue48-artifacts \
  --cosign /root/issue48-verifiers/cosign
```

Después de verificar el artefacto se ejecuta su `version` en staging privado.
Se preparan `/usr/bin/caddy`, usuario/grupo de sistema `caddy`, unidad base notify,
drop-in y `/usr/local/lib/nuevamente/verify_deployment.py`, más directorios ACME/logs
y estado privado. Solo se hace `daemon-reload`; no start, enable, sitio por
defecto, Caddyfile de ejemplo ni autosave. La unidad tiene
`ConditionPathExists=/etc/caddy/Caddyfile` y las fuentes de entorno obligatorias.
La umask permanece en 0077. Cada directorio recién creado recibe chown y chmod
explícitos, seguidos de verificación del dueño, grupo, modo, tipo, symlinks y ACLs.
Así quedan el helper bajo root:root 0755 y `/etc/caddy` bajo root:caddy 0750,
atravesables por Caddy, sin depender del modo que dejó mkdir bajo umask.
Un directorio existente se verifica y se rechaza si es incompatible; no se corrigen
silenciosamente permisos o propietarios de una instalación ajena o parcial.
El socket fija `|0200` explícitamente: Caddy aplica chmod al listener Unix y no
se debe inferir su modo a partir de umask. Conserva el acceso exclusivo del dueño
y root, dentro del directorio 0700, según sus
[convenciones oficiales](https://caddyserver.com/docs/conventions#network-addresses).
Este ajuste mínimo de Etapa 1 hace que la verificación POSIX compruebe el modo
efectivo correcto, sin ampliar permisos.
El supervisor mantiene el proceso Caddy hijo, retransmite SIGTERM/SIGINT y usa
`NotifyAccess=all`; unidad efectiva/notify requieren prueba real con systemd.

`/var/lib/nuevamente-deploy/installation.json` registra únicamente versión y hashes
de binario/unidad/drop-in/helper, nunca hashes de secretos. Repetir una instalación
terminada verifica identidad/contenido y no reescribe archivos ni recarga/activa
servicios. Rechaza paquetes, usuarios, unidades, binarios ajenos, archivos
modificados y estados parciales. Una interrupción durante preparación puede dejar
un estado parcial que requiere inspección administrativa; no lo adopta ni
sobrescribe automáticamente. No elimina secretos, configuración ni certificados.

### Preparar archivos canónicos y evidencias de #47

El marcador `/opt/nuevamente/issue47-ready` tiene una comprobación exclusiva:
archivo regular root:root 0644 dentro de `/opt/nuevamente`, directorio ubuntu:ubuntu
0750 tal como lo crea `infra/oci/bootstrap.sh`. Los demás ancestros deben ser
directorios de root:root sin escritura de grupo/otros; se rechazan symlinks y ACLs
en todos los componentes. No se cambian propietarios del host ni se extiende esta
excepción a `secure_path`, secretos, configuración o estado privado. El marcador
no sustituye las comprobaciones de cloud-init, servicios, firewall y red.

Completar los archivos privados y permisos de la tabla anterior. Las asignaciones
son literales `CLAVE=valor`, con comentarios en líneas separadas: no `export`,
quotes, escapes, expansión `$`, backticks, `#` dentro del valor, espacios exteriores,
duplicados ni claves desconocidas. Esta restricción evita interpretaciones
distintas entre systemd, dotenv y Compose. No guardar archivos privados en el
checkout. `backend.env` admite variables documentadas en `.env.example`, sus
cuotas comentadas, `MOCK_GEMINI` y `CORS_ORIGINS`; excluye la clave de origen.
Exigir explícitamente producción, ambos mocks 0, perfil DOCKER/api_key,
`OCI_ALWAYS_FREE_CONFIRMED=1`, modelos/proveedor y todas las cuotas reales.
Las triples de cuotas compartidas por un mismo modelo deben coincidir.

La carpeta OCI técnica contiene **solo** `config` y `private_key.pem`, UID/GID
10001, directorio 0700 y archivos 0600, sin ACL extendida ni symlinks. El config
tiene solo perfil DOCKER y claves user/tenancy/fingerprint/key_file/region;
`key_file=/run/oci/private_key.pem`. Se valida con el SDK instalado en la imagen,
se carga la clave y se comprueba su fingerprint en memoria, sin llamadas OCI.
Esto acredita configuración/consistencia local; identidad, IAM, bucket, capacidad,
cuotas y disponibilidad reales aún requieren evidencia autorizada del equipo.

Preparar `/etc/nuevamente/issue47-network.json`, root:root 0600, como evidencia
**reciente y revisada por el administrador**, sin credenciales. Formato:

```json
{
  "schema": 1,
  "all_attached_reviewed": true,
  "reviewed_at": 0,
  "host_machine_id": "",
  "instance_id": "",
  "admin_cidr": "",
  "public_ipv4": "",
  "security_lists": [],
  "nsg_rules": []
}
```

Este ejemplo vacío **no pasa**. `reviewed_at` es epoch UTC, con antigüedad máxima
de una hora; `host_machine_id` coincide con `/etc/machine-id`; instance/public IPv4
identifican la VM real. `security_lists` contiene objetos **nativos** exportados
de todas las listas asociadas, incluida la default; `nsg_rules` todas las reglas
nativas de todos los NSG asociados. No resumir solamente las reglas favorables.
La completitud es una declaración administrativa, no una consulta OCI hecha por
el script: no demuestra que la nube no cambió después de la revisión. La revisión
de asociaciones y la prueba externa siguen siendo obligatorias en aceptación.

Se exige TCP/22 exclusivamente del /32 administrativo, TCP/80 y /443 desde IPv4
pública y el ICMP tipo 3/código 4 de #47 para Path MTU. Se rechazan reglas amplias,
UDP, rangos 80–443, SSH global y alternativas IPv6. En el host se verifican los
jumps iniciales y **todas** las reglas NUEVAMENTE-IN/FWD en el orden de #47,
servicios activos/habilitados, SSH sin contraseña/root, listeners y Docker.
IPv6 global, policy routing no estándar o reglas por UID/cgroup requieren una
revisión específica: no se asume que el peer observado representa otra ruta.
No se corrige/elimina ninguna regla para hacer pasar el preflight.

### Volumen existente e imágenes

Identificar el volumen de #26 y sus datos **antes** de preparar la aprobación.
El nombre canónico es `nuevamente_backend_data`; si el existente tiene otro nombre,
se requiere una adopción/migración revisada aparte, con respaldo y preservación de
datos. El script no renombra, copia, crea, vacía ni elimina volúmenes. No colocar
un volumen vacío con el nombre nuevo para ocultar que había datos en el anterior.

Después de revisar el volumen real, guardar `/etc/nuevamente/volume.json`, root:root
0600, con estos campos de su `docker volume inspect` nativo: `Name`, `Driver`,
`Mountpoint`, `CreatedAt`, `Scope`. Capturar/filtrar privadamente, sin publicar
inspect crudo. Se compara identidad en cada preflight y contra el último despliegue.
Volumen ausente/reemplazado, volúmenes legacy `*_backend_data`, otro proyecto
asociado o más de un contenedor escritor causan rechazo. Incluso un contenedor
detenido de otro proyecto exige revisión, porque podría reactivarse.
La raíz de datos debe ser UID/GID 10001 y 0700; se verifica en un contenedor de
solo lectura, sin copy-up, red ni escritor de datos. Ese check usa un bind RO del
Mountpoint local ya inspeccionado: un mount por nombre podría crear un volumen
vacío si desapareciera entre preflight y check. Driver local con opciones remotas
se rechaza. La identidad se vuelve a comprobar antes de recrear backend y antes
de liberar la compuerta; hasta entonces no se inicia el escritor de aplicación.

Las imágenes `nuevamente-backend` y `nuevamente-frontend` deben estar construidas
y revisadas previamente en ARM64. No se construyen/descargan durante despliegue.
Se comprueban Linux/ARM64 y UID 10001 y se fijan por ID inmutable `sha256:...` en
el modelo privado de despliegue/recuperación. Conservar esas imágenes anteriores:
no ejecutar prune durante una ventana de recuperación. El proyecto es siempre
`nuevamente`; no usar `COMPOSE_PROJECT_NAME` alternativo.

### Observación inicial antes de confiar en headers

Con `FORWARDED_ALLOW_IPS` todavía vacío y sin despliegue previo de ese proyecto:

```bash
sudo bash infra/caddy/deploy.sh --checkout /opt/nuevamente/checkout --observe-peer
```

Esta operación explícita prepara un backend **en cuarentena** usando el modelo
nativo base+OCI+producción y las imágenes existentes. El sentinel no-IP `unverified`
solo permite interpolar ese modelo temporal: nunca puede pasar la guardia de
Uvicorn ni liberar la compuerta. No inicia Caddy, frontend, proveedores o aplicación;
no escribe datos. El proceso escucha TCP/8000 en su binding loopback, devuelve el
peer de `accept()` junto con un nonce aleatorio y espera una autorización root.
La conexión de observación se abre en el namespace del host bajo UID/GID Caddy;
no usa headers reenviados ni supone una gateway. Se verifica que la red no cambió
entre la conexión y la inspección. El peer queda en
`/var/lib/nuevamente-deploy/observed-peer.json` 0600; no se imprime.
Después se detiene y elimina solo ese contenedor de prueba, conservando red y
volumen. Una interrupción de esta preparación exige inspección de su contenedor
y `peer-probe-compose.json`; no se adopta como aplicación sana.

El administrador lee el registro privado y completa **esa IP literal** en
`deployment.env`. Actualmente hay una sola ruta/binding; se rechazan IPs adicionales
no observadas. La activación recrea y observa nuevamente: el registro inicial no
es autorización perpetua ni evidencia de la red del nuevo contenedor.
No ejecutar directamente `compose up` con los templates de Etapa 1 para omitir
esta compuerta; usar el helper que construye y valida el modelo de activación.

### Validar sin publicar; activar explícitamente

```bash
sudo bash infra/caddy/deploy.sh --checkout /opt/nuevamente/checkout --validate
# Solo tras autorización operativa específica:
sudo bash infra/caddy/deploy.sh --checkout /opt/nuevamente/checkout --activate
```

Sin flag, `--validate` es el comportamiento por defecto. Usa Compose real para
inspeccionar la lista nativa de env_file y el modelo resuelto, sin incorporar el
`.env` de desarrollo. Exige fuentes canónicas exactas, hostname único, restricciones,
loopback, salida bridge, healthchecks, worker único y entorno de UI sin credenciales.
Ejecuta la guardia Uvicorn con solo `execvp` sustituido y `Configuracion.validar_critico`
**dentro de la imagen construida**, en un contenedor efímero RO, sin red, sin volumen
de datos y sin logs Docker. Comprueba credenciales OCI localmente y luego permisos
del volumen en otro contenedor RO; estos checks no llaman a Gemini/OCI.
Valida/adapta Caddy nativamente como usuario Caddy en staging privado, sin reemplazar
el archivo activo; el JSON adaptado queda en memoria y exige placeholder del secreto.

Todas las operaciones comparten un `flock` no bloqueante root:root 0600 en
`/run/lock/nuevamente-caddy.lock`. Una segunda ejecución se rechaza. Antes de
mutar se publica un journal privado `pending.json`. El modelo generado agrega
compuerta TCP previa a la guardia existente, logging Docker `none`, IDs de imagen,
volumen **external + nocopy** y conserva el resto del contrato efectivo.
No cambia los overlays fuente ni da confianza a una IP sin verificarla.

La activación recrea backend, observa peer con nonce, comprueba fingerprint de
red/namespace y solo entonces publica mediante `docker exec --user 0` un marcador
0600 temporal de creación y 0644 final, root:root, **sin secreto**. La compuerta exige
ese dueño/modo, nonce y peer exactos antes de importar Uvicorn. Después inicia UI,
espera salud, publica atómicamente el Caddyfile validado (0640 root:caddy, fsync y
rename) e inicia/recarga Caddy. Comprueba procesos wrapper/hijo de Caddy, backend
`/proc/1/environ`, secreto canónico, socket Unix 0200 y `/config/` efectivo, imágenes,
comandos, entornos, mounts, bindings y restricciones reales de contenedores.
Un exit 0 de start/reload no basta. Solo después publica `current.json` y retira
el journal. `current.json` y modelos Compose contienen credenciales: 0600 dentro
de un directorio root 0700, nunca adjuntar ni mostrar por consola.

La espera de salud conserva el plazo total de 90 segundos para HTTP **y** Docker
healthy. Docker starting es transitorio; se vuelve a inspeccionar la configuración
efectiva en cada intento, sin reintentar estados unhealthy/detenidos ni fallos de
seguridad. Se conservan las identidades, arranques y namespaces/red de ambos
contenedores y el fingerprint de backend/rutas; cualquier cambio invalida la espera.
Los comandos y peticiones reciben como máximo el presupuesto restante. Antes de
publicar, se exige otra verificación efectiva estricta con healthy, identidad y red
coherentes. El estado starting y su transición son los
[estados definidos por Docker](https://docs.docker.com/reference/dockerfile/#healthcheck).

Se registran ID y StartedAt del contenedor, SandboxID, attachments (IP/gateway),
ID/driver/IPAM/opciones de red y tablas de rutas/reglas IPv4 del host.
Restart, reboot, cambio de endpoint/ruta o recreación
invalidan la observación. `--validate` lo informa como fallo; `--activate` explícito
suspende Caddy y vuelve a observar antes de liberar Uvicorn. El tmpfs se limpia al
reiniciar el contenedor: queda en cuarentena hasta ejecutar la activación revisada.
No hay reapertura automática confiando en una observación vieja. Si cambia el peer,
el despliegue falla; revisar la nueva red y su IP con autorización administrativa
antes de cambiar confianza. No ampliar a CIDR ni `*` para recuperar servicio.

Esto implica una ventana de indisponibilidad/reconexión al recrear servicios,
y una recuperación administrativa tras restart/reboot; no se promete zero downtime
ni continuidad de trabajos en curso. El diseño privilegia comprobación previa de
origen y escritor único. Los ensayos reales de ese comportamiento son pendientes.

### Fallos y recuperación

La validación inválida conserva configuración/procesos previos. Si falla arranque,
peer, salud, publicación, reload o comprobación efectiva después de mutar, intenta
restaurar Caddyfile y modelo anterior fijado por imagen, observa el peer del backend
restaurado y verifica salud/configuración. Sin despliegue previo detiene los servicios
nuevos y retira la configuración publicada; conserva volumen, datos y ACME.
**No revierte datos, migraciones, resultados de trabajos ni efectos externos.**

Tras interrupción, el journal pendiente bloquea otro despliegue ordinario:

```bash
sudo bash infra/caddy/deploy.sh --checkout /opt/nuevamente/checkout --recover
```

No borrar `pending.json` para forzar verde. La recuperación explícita repite preflight
y restauración. Si también falla, intenta detener Caddy, conserva el journal,
informa `caddy_active`/cantidad de contenedores observada (o `unknown` si la inspección
falló) y termina con error. Puede haber servicios detenidos o contenedores en
cuarentena; nunca anuncia restauración exitosa sin verificarla. Investigar la fase
y exit status publicados y los logs filtrados, sin imprimir salidas privadas.
Mantener las imágenes previas y reparar prerrequisitos antes de repetir recuperación.

La rotación de clave de origen está fuera del deploy normal. Se rechaza divergencia
con procesos o el modelo previo incluso si Caddy estaba detenido. No se genera ni
regenera una clave, no se actualiza parcialmente por reload y no se restaura un
archivo de secreto desde un backup. Requiere procedimiento coordinado y aprobación
aparte. El resto de archivos canónicos tampoco se modifica automáticamente.

### Diagnóstico y pruebas de esta etapa

Los subprocesos capturan stdout/stderr en memoria; excepciones no imprimen argv,
entorno, contenido, valores o hashes de secretos. Solo códigos constantes,
herramienta/operación permitida y exit status numérico. Los archivos privados,
staging, modelos y journal rechazan symlinks, dueños/modos inesperados y ACLs;
no se habilitan tracing, debug ni `--environ`. El supervisor de Caddy descarta
stdio antes de configurar logging. Docker usa `none` en el modelo generado para
que arranques/validaciones de proveedores no emitan datos al journal/daemon log;
el diagnóstico de proxy permanece en access/operational filtrados y el de despliegue
en fase, estado, salud y configuración efectiva. La revisión de diagnósticos de
aplicación y canarios bajo tráfico pertenece a Etapa 3; no afirmar que esta medida
acredita todo el logging interno de terceros.

`test_caddy_deploy.py` cubre fallos y restauración, repetición, exclusión concurrente,
hash/firma/procedencia inválidos, secreto/permisos/config inválidos, peer sin verificar,
cambios de red, volumen sustituido y escritores de otros proyectos. Usa dobles de
Docker/systemd/flock/metadata POSIX y archivos de prueba para demostrar decisiones
y conservación. Además valida modelos con Compose real. **Los dobles no prueban
instalación, recuperación, permisos ni NAT reales.** No son evidencia pública.

Antes de acreditar operación real, en Ubuntu 24.04 ARM64 aislado y autorizado:
comprobar `systemd-analyze verify`, `systemctl show` efectivo/notify, permisos/ACLs,
socket, instalación repetida y rechazo ajeno, peer TCP antes de Uvicorn, cambios de
red/restart, salud y entorno cargado, fallos de start/reload y recuperación fallida.
Usar copias/datos sintéticos; comprobar que datos y estado ACME sobreviven, y que
canarios no aparecen en stdout/stderr/journal/archivos. No hacer estas inyecciones
de fallos contra datos de usuarios. Sin ese entorno las verificaciones quedan
**no ejecutadas**, y los ensayos públicos, SSE/WS y generación real siguen pendientes.

<details>
<summary>Historial local — Etapa 2, 12–13/10/2026 (estado de esas revisiones)</summary>

### Suite ordinaria y evidencia previa

El supervisor comunicó reproducción del fallo de generación en Windows y localizó
worker/consulta de estado en operaciones SQLite de `PresupuestoOCI`; la causa
continúa pendiente. También comunicó CI Linux verde en el commit base
`008ee4d5eaed8df29bf12f73b941936afe775482`. Es evidencia aportada por el supervisor,
no una ejecución Linux realizada aquí ni aprobación de la suite de este cambio.
No se modifican esos módulos, plazos ni assertions para obtener verde.
La suite del cambio permanece pendiente hasta completar una ejecución satisfactoria;
si Linux también falla, presentar diagnóstico y propuesta acotada antes de ampliar
el alcance de #48.

### Resultados locales observados de Etapa 2

Registro anterior a las correcciones de revisión del 13/10/2026. Esa revisión
identificó los tres casos sin cobertura descritos abajo; los tests aprobados de
este registro no acreditaban su comportamiento.

Verificaciones del checkout de esta etapa en Windows, con herramientas temporales
verificadas; no se ejecutaron `install.sh` ni `deploy.sh` contra el host:

| Verificación | Resultado observado | Alcance de la evidencia |
| --- | --- | --- |
| Tests específicos finales | **Aprobado: 183**, en 27,99 s | 94 de despliegue y los 89 de configuración de Etapa 1; dobles explícitos para Docker/systemd/POSIX |
| Regresiones relacionadas | **Aprobado: 98** | #47, origen, health, SSE TCP directo y frontend; ejecución conjunta anterior: 273 passed, dos warnings deprecados de Chroma |
| Caddy 2.11.7: fmt, adaptación y validación | **Aprobado** | Binario oficial Windows, secreto sintético y configuración nativa; no prueba servicio Linux ni TLS público |
| Compose 2.40.3: overlays y modelo generado | **Aprobado** | Configuración efectiva nativa, entornos, bindings, restricciones e imágenes fijadas; sin Docker Engine |
| Hashes y Sigstore | **Aprobado** | Archivo ARM64 y manifiesto oficiales verificados con Cosign Windows 2.6.5, identidad/issuer exactos y transparencia/SCT; blob alterado rechazado. No se ejecutó el binario ARM64 |
| `bash -n` de ambos scripts | **Aprobado** | Bash existente; solo análisis de sintaxis |
| `ruff check .`, `ruff format --check .`, `git diff --check` | **Aprobado** | 222 archivos formateados; HEAD/develop/origin/develop siguen en el commit base indicado |
| Suite ordinaria `pytest -q -x --tb=short` | **Fallo: 1**, en 28,30 s | `test_upload_ready_generacion_exportacion_y_sse[flashcards]`: estado `running` al vencer la espera existente. Detención al primer fallo; resto no ejecutado |
| Ubuntu ARM64, `systemd-analyze verify`, unidad efectiva/notify y permisos reales | **No ejecutado** | No hay Ubuntu/systemd disponible; no se acredita instalación ni recuperación nativa |
| Docker Engine, imágenes ARM64, NAT/peer y fallos reales de start/reload | **No ejecutado** | No hay Engine disponible; las pruebas con dobles no sustituyen estos ensayos |
| DNS, ACME/HTTPS público, firewall externo y SSE/WebSocket por Caddy | **Pendiente** | Requiere VM autorizada y Etapas 3–4 |

Reportes locales ignorados, sin credenciales reales:
`.data/issue48-tools/stage2-ready.txt` (183 tests),
`.data/issue48-tools/stage2-validation.txt` (273 tests con regresiones) y
`.data/issue48-tools/stage2-ordinary-suite.txt` (fallo ordinario).
Estos resultados no constituyen aceptación pública ni aprobación de la suite completa.

### Correcciones de revisión de Etapa 2 — 13/10/2026

Se corrigieron únicamente los modos finales de directorios nuevos, la comprobación
específica del marcador de #47 y la espera conjunta HTTP/Docker descritas arriba.
El helper, `backend/tests/test_caddy_deploy.py` y este runbook son los únicos archivos
modificados durante esta corrección. No cambian Caddyfile, unidad ni overlays.

Los casos de reproducción iniciales fallaron antes de modificar el helper: faltaba
la finalización/verificación de chmod, se rechazaba el marcador válido de #47 y
Docker starting se rechazaba inmediatamente aunque HTTP estuviera listo.
Las pruebas ejecutan `install()`, `host_preflight()`, `health()` y su comprobación
efectiva; los límites de permisos/umask, propietarios, Docker, HTTP y reloj se
inyectan explícitamente. No prueban permisos o instalación POSIX nativos.

| Verificación repetida | Resultado observado |
| --- | --- |
| Configuración y despliegue específicos | **Aprobado: 227**, en 17,36 s: 138 de Etapa 2 (44 casos nuevos) + 89 de Etapa 1 |
| Regresiones de #47, origen, health, SSE TCP directo y frontend | **Aprobado: 98**, en 32,82 s, dos warnings deprecados de Chroma; proveedores simulados |
| Caddy 2.11.7 y Compose 2.40.3 nativos Windows | **Aprobado**, incluidos en los tests específicos; formato/adaptación/validación y composición efectiva con datos sintéticos |
| `ruff check .`, `ruff format --check .`, `git diff --check` | **Aprobado**, 222 archivos formateados |
| Suite ordinaria completa | **Pendiente**, conserva el fallo conocido documentado arriba; no se repitió ni se cambiaron plazos, assertions, pipeline o `PresupuestoOCI` |
| Ubuntu ARM64, systemd, permisos POSIX y Docker/NAT nativos | **No ejecutado**, herramientas/entorno no disponibles |
| Criterios públicos y Etapas 3–4 | **Pendiente**, sin VM autorizada ni evidencia de producción |

La ejecución combinada en sandbox se interrumpió al dejar de avanzar; no se presenta
como aprobada. La tanda específica se completó por separado y las regresiones se
repitieron fuera del sandbox para permitir los sockets locales, sin instalar ni
activar servicios. Reportes ignorados:
`.data/issue48-tools/review-red.txt` (reproducción previa),
`.data/issue48-tools/review-specific-final.txt` (227 tests) y
`.data/issue48-tools/review-regressions-local.txt` (98 regresiones).

**Finalizar Etapa 2 con archivos/resultados para revisión. No comenzar Etapas 3–4.**

</details>

### Etapa 3 — integración local y automatización (sin despliegue)

La autorización posterior habilita únicamente esta etapa. El smoke requerido está
en `.github/scripts/smoke_caddy.py` y las pruebas en
`backend/tests/integration/test_caddy_proxy.py`. Los fixtures `caddy_backend.py`,
`caddy_fixture.py`, `caddy_ui.py` y `caddy_browser.py` no se instalan ni agregan
rutas a la aplicación de producción. Corren procesos temporales sobre TCP real:
Caddy **2.11.7**, Uvicorn, aplicación/gestor/SQLite/Chroma reales, Streamlit y
Chromium mediante Playwright **1.63.0**. Gemini y OCI usan dobles explícitos;
el proceso backend rechaza conexiones/DNS externos mediante un audit hook.

Los procesos arrancan en un directorio temporal, sin leer el `.env` del checkout.
Datos, tokens, clave de origen y certificado autofirmado para localhost son
sintéticos. El navegador tiene un perfil temporal propio. Se bloquean y cuentan
explícitamente las fuentes remotas de la UI existente; su apariencia no se valida.
Los demás intentos de red externos del navegador hacen fallar la prueba.

El fixture parte del Caddyfile de producción. Adapta puertos (incluidos los puertos
HTTP/HTTPS globales), bind loopback, rutas de archivos temporales y certificado TLS
local. Solo su administración usa un puerto TCP efímero en loopback: **no acredita
el socket Unix protegido de producción**. Compara rutas, headers, transporte,
streaming, logging, protocolos y timeouts en el JSON generado por Caddy real.
Normaliza únicamente hostname/puertos y nombres de grupos autogenerados.
La confianza Uvicorn literal `127.0.0.1` corresponde al peer TCP observado en este
fixture; no resuelve ni sustituye el prerrequisito Docker/NAT de producción.

Pruebas requeridas:

- Redirect HTTP→HTTPS, normalización FastAPI con hostname/esquema/prefijo `/api`,
  `/api` exacto, auth/idempotencia/cursor y headers funcionales; sobrescritura de
  forwarding/origen falsificados en API y en el contexto privado de Streamlit.
- SSE de trabajos y generaciones reales en HTTP/1.1 y HTTP/2: recepción
  incremental temporizada, heartbeat con trabajo activo, propietario, reconexión
  y continuidad sin ejecución duplicada. Los retardos están en los dobles de
  proveedores; no se reemplaza el pipeline ni su almacenamiento. Un wrapper
  test-only introduce buffering deliberado: el mismo detector de entrega temprana
  debe rechazarlo, aun cuando todos los eventos lleguen finalmente.
- Silencio SSE superior a 120 s, sin cambiar los timeouts de Caddy: solo se inyecta
  un intervalo de heartbeat de 135 s en la aplicación del fixture, dentro del
  deadline ordinario de 300 s. Upload real con chunks progresivos durante más de
  60 s y WebSocket con interacción/reconexión y espera de 130 s. Se registran tiempos
  de chunks, eventos y contadores, nunca payloads ni tokens.
- Streamlit real: mensajes WS en ambos sentidos, interacción antes/después de
  reconectar, CORS/XSRF activos, Host/Origin coherentes y observación de origen solo
  del lado servidor. Se auditan headers HTTP/handshakes WS, HTML, frames y storage
  del navegador para ausencia de la clave privada. La comprobación no se limita a 101.
- Canarios en Authorization, cookies, URI/query, cuerpos y headers privados:
  auditoría de todos los archivos reales de acceso/operación (incluidas rotaciones
  comprimidas), stdout/stderr de validación, adaptación, arranque, recarga,
  upstream caído y shutdown. El canal `pw:browser` reenvía también los diagnósticos
  nativos de Chromium para auditarlos; no activa debug de Caddy. Se exigen campos
  útiles de status/duración/tamaño, identificador, componente y errores operativos.

**Defecto de #48 reproducido y corrección mínima:** al desconectar SSE, dos logs
con status 200 conservaron duración/tamaño pero perdieron los campos añadidos por
`log_append`. Sin SSE, el mismo ensayo de headers/logs pasó. Caddy 2.11.7 aborta
copias incompletas mediante `http.ErrAbortHandler`; el handler de `log_append`
normal agrega sus campos después del proxy y no llega a ejecutarse en ese camino.
Se usa el modo nativo temprano (`<key`) únicamente para `proxy_request_id`, método
permitido y componente. No añade información sensible ni buffers. La regresión
inspecciona `early: true` en el JSON nativo y exige los campos también en los logs
de desconexión. Referencias:
[log_append](https://caddyserver.com/docs/caddyfile/directives/log_append),
[implementación fijada 2.11.7](https://github.com/caddyserver/caddy/blob/v2.11.7/modules/caddyhttp/logging/logappend.go).

Reproducción con dependencias del repositorio ya disponibles:

```sh
python -m pip install --require-hashes --only-binary=:all: -r .github/requirements-caddy.lock
# En Linux efímero, descarga herramientas oficiales con hashes fijados:
python .github/scripts/smoke_caddy.py --prepare-tools /ruta/temporal/caddy-tools
export CADDY_BIN=/ruta/temporal/caddy-tools/caddy
export COMPOSE_BIN=/ruta/temporal/caddy-tools/docker-compose
python -m playwright install chromium
python .github/scripts/smoke_caddy.py
# Selección de proxy independiente, deliberadamente fuera de la suite ordinaria:
pytest -o addopts= -m caddy_proxy backend/tests/integration/test_caddy_proxy.py
```

`--prepare-tools` exige Linux amd64/arm64 y verifica hashes del manifiesto oficial:
Caddy SHA512 (manifiesto firmado de 2.11.7 ya verificado en Etapa 2) y Compose SHA256
2.40.3. Solo extrae el ejecutable esperado. Las herramientas Python tienen versión
y hashes PyPI fijados; Playwright fija la revisión de Chromium de CI. Puede usarse
un navegador existente indicando `NM_BROWSER_EXECUTABLE`, con su versión observada
en el reporte; no constituye evidencia de la revisión Chromium de CI.

`.github/workflows/caddy.yml` define jobs nativos Ubuntu 24.04 amd64 y ARM64,
`contents: read`, sin secretos ni permisos de escritura. La selección revisada
antes de corregir la guardia consta de **270 casos: 89 de configuración, 142 de
despliegue, 29 de reporte/diagnóstico y 10 de proxy**. Con las 14 regresiones de
esta corrección, la selección pasó a **284: 89 + 142 + 43 + 10**. Las 30
regresiones posteriores del diagnóstico de navegador llevan la selección vigente
a **314: 89 + 142 + 73 + 10**, conservando las 284 identidades anteriores. La
clasificación acotada de lanzamiento y la cobertura en rojo añaden otras 34:
selección **348: 89 + 142 + 107 + 10**, conservando las 314 anteriores. La
corrección del encuadre XServer añade 6 regresiones: selección vigente
**354: 89 + 142 + 113 + 10**, conservando las 348 anteriores. El smoke
exige cada familia e identidad parametrizada: comprueba el recuento exacto,
ausencia de duplicados y SHA256 de los nombres completos ordenados (JSON ASCII
compacto) contra el manifiesto revisado `REQUIRED_FAMILIES`. No obtiene ese
manifiesto del reporte que evalúa. Una identidad sustituida, pruebas adicionales
en otra familia o una familia ausente no compensan cobertura faltante. Cambiar
la colección requiere revisar explícitamente las identidades y actualizar el
manifiesto; el orden de ejecución no afecta la comprobación. Selección vacía,
herramientas/versiones ausentes, skips o fallos dan resultado rojo. Los filtros
incluyen Caddy, OCI, overlays, env templates, backend/frontend, scripts, requisitos
y selección pytest. Se amplían también los filtros de contenedores; sus checks
existentes permanecen. **Workflow escrito no equivale a workflow ejecutado.**

Solo se publican `observations.json` y JUnit saneado: tiempos, contadores, flags y
resultados. El JUnit privado original conserva capturas/trazas completas; el público
conserva cada resultado (incluidos fallos, errores y skips), nombre de función y
protocolo, sin parámetros privados ni capturas/locals. Esto no descarta ningún
canal de la auditoría del fixture ni convierte un resultado rojo en verde.
Los archivos de TLS, configuración, application-data, logs crudos, paquetes WS y
perfiles de navegador nunca son artifacts de CI. Los procesos temporales se cierran
en teardown incluso tras fallo. Un fallo del pipeline real queda visible: no se
amplía el plazo de 15 s, no se debilitan assertions ni se modifica `PresupuestoOCI`.

Compatibilidad experimental del sandbox en CI: la corrida 38615063540 sobre
`744264c` obtuvo 352 aprobados y 2 fallidos por arquitectura, con las 354
identidades completas. Las cuatro sondas fallaron en `browser_launch` con firma
genérica de sandbox. El inspector capturó los lanzamientos, pero no denegaciones
correlacionadas del kernel: **AppArmor sigue siendo una hipótesis**; los
descendientes no quedaron acreditados. Se conservan esos artifacts históricos.

La compatibilidad implementada usa la [autorización específica documentada por Ubuntu](https://documentation.ubuntu.com/release-notes/24.04/#unprivileged-user-namespace-restrictions)
y [Chromium](https://chromium.googlesource.com/chromium/src/+/main/docs/security/apparmor-userns-restrictions.md):
un perfil nuevo `nm48-chromium-ci`, `flags=(unconfined) { userns, }`, asociado
únicamente a la ruta exacta del headless shell fijado. Autoriza namespaces de
usuario para ese programa y su contexto heredado; no constituye un confinamiento
AppArmor de archivos/red. Amplía para ese contexto el acceso a superficie del
kernel restringida por Ubuntu, para que Chromium construya su propio sandbox.
No concede privilegios root en el host ni garantiza aislar código que ya tenga
sudo. No cambia sysctl, otros perfiles ni instala helpers setuid.

Solo se prepara en la VM GitHub-hosted efímera de cada job. El bundle completo se
copia a inodos nuevos bajo `/nm48-playwright-ci/browsers`: root:root,
directorios/ejecutables 0555, otros archivos 0444, raíz 0755 y ancestros verificados
sin escritura de grupo/otros. Se rechazan enlaces y tipos especiales; el perfil
0644 se crea exclusivamente y se carga con `--add`, nunca reemplazando otro.
El inventario SHA256 completo debe coincidir antes/después; acredita preservación
de bytes, no una firma independiente del navegador descargado. La procedencia
sigue siendo Playwright 1.63.0 verificado por el lock y su descarga oficial HTTPS.
`PLAYWRIGHT_BROWSERS_PATH` conserva la selección por revisión y la assertion de
versión; no se usa `NM_BROWSER_EXECUTABLE` ni se cambia `chromium_sandbox=True`.
Las imágenes usadas en la corrida 38623397761 configuran `/opt` como 0777,
incompatible con la guardia estricta de ancestros ([AMD64](https://github.com/actions/runner-images/blob/ubuntu24/20261004.327/images/ubuntu/scripts/build/configure-system.sh#L15),
[ARM64](https://github.com/actions/runner-images/blob/ubuntu24-arm64/20261004.142/images/ubuntu/scripts/build/configure-system.sh#L15)).
La nueva ubicación evita ese árbol sin modificar `/opt` ni relajar comprobaciones.
El perfil sigue generándose desde `bundle / relative`, bajo la nueva raíz exacta;
se conserva el permiso `userns` autorizado. Su funcionamiento en ambas
arquitecturas está acreditado por la ejecución Linux 38626809239 indicada arriba.

El smoke se invoca directamente una sola vez, con la misma guardia de 354
identidades, assertions y deadlines. Se retiran el inspector temporal y
`runner-sandbox.json` de nuevas publicaciones, sin borrar evidencia anterior.
La ejecución 38626809239 completó parser/carga/permisos y navegador en ambas
arquitecturas. Esa evidencia acredita compatibilidad en CI, sin confirmar una
causa única de los fallos históricos. La suite ordinaria Linux también completó
su selección según la tabla inicial; las corridas incompletas de Windows quedan
como historial. La aceptación pública continúa pendiente.

Pendientes que estas pruebas no sustituyen: instalación/permisos POSIX, unidad
systemd/notify/recuperación reales, Docker/NAT y peer comprobado, VM OCI Ubuntu ARM64,
DNS, ACME/cadena pública, red/firewall externos y los tres criterios públicos del
issue. **Detenerse para revisión antes de Etapa 4.**

<details>
<summary>Historial local — Etapa 3 y cierre diagnóstico, 13/10/2026</summary>

### Resultados observados de Etapa 3 — corrida verde histórica, 13/10/2026

Plataforma local: Windows AMD64 (build 26300), Python 3.11.9, Caddy 2.11.7 y
Compose 2.40.3 oficiales temporales; Streamlit 1.51.0, Playwright 1.63.0,
HTTPX 0.28.1 / h2 4.4.1 y Chrome existente **155.0.8059.40** con perfil temporal.
CI exige el Chromium de Playwright (revisión 1243, 153.0.8010.12), cuya versión se
compara con el manifiesto incluido en el paquete verificado. Ese navegador de CI
no se ejecutó localmente. No se instalaron servicios ni herramientas persistentes.

| Verificación | Resultado observado | Límite de la evidencia |
| --- | --- | --- |
| Etapas 1–2, Caddy fmt/adapt/validate y Compose combinado | **Aprobado: 227**, 19,27 s; repetidos dentro del smoke final | Herramientas nativas Windows; instalación/systemd/POSIX/Docker con dobles donde corresponden |
| Regresiones #47, origen, health, SSE TCP directo y frontend | **Aprobado: 98**, 31,69 s, 2 warnings deprecados de Chroma | Proveedores simulados; no VM |
| Saneamiento del JUnit | **Aprobado: 4**, 0,19 s; incluidos en smoke | Conserva failure/error/skipped y protocolo; elimina captura/locals y parámetros privados del artifact |
| Smoke completo requerido | **Aprobado: 241**, 306,66 s; **0 fallos, 0 skips** | 227 configuración/despliegue + 4 reporte + 10 proxy; no equivale a suite ordinaria completa |
| SSE jobs y generaciones H1/H2 | **Aprobado** en smoke final | Jobs: eventos pequeños 0,015–0,828 s y heartbeat ~15 s; generaciones: eventos antes de completar, heartbeat ~15 s y un writer por ejecución |
| Buffering sembrado | **Aprobado: detectado** | El detector rechaza entrega tardía aunque termine llegando el cuerpo completo |
| Silencio SSE H1/H2, upload lento y WS | **Aprobado** en smoke y repetición acotada | Silencio 135,03/135,05 s; upload 13 chunks durante 72,00 s (75,11 s total); interacción WS después de 131,94 s |
| Navegador y privacidad | **Aprobado** | 2 handshakes/reconexión, mensajes en ambos sentidos, CORS/XSRF/contexto privado; 24 canales auditados, 250 registros de acceso y 44 operativos sin canarios |
| Hashes fijados de herramientas | **Aprobado: coinciden con manifiestos oficiales** | SHA512 Caddy amd64/arm64 y SHA256 Compose; bootstrap de herramientas Linux no ejecutado en Windows |
| Sintaxis Python, YAML de workflows y wrappers Bash | **Aprobado** | py_compile, parser YAML existente y bash -n; Bash requirió salir del sandbox tras un rechazo MSYS, sin ejecutar wrappers |
| Ruff y formato; diff --check | **Aprobado** | 229 archivos Python; sin cambios de branch/HEAD/base |
| Suite ordinaria completa | **Pendiente**, conserva el fallo conocido | No se repitió completa; no se modificaron pipeline, PresupuestoOCI, assertions ni plazo de finalización de 15 s |
| Workflows Linux/ARM64, Chromium fijado de CI | **No ejecutado** | YAML/receta escritos; no ejecución remota autorizada |
| Ubuntu ARM64, systemd, permisos/instalación POSIX y Docker/NAT | **No ejecutado** | Los dobles de Etapas 1–2 y Windows no los acreditan |
| VM, DNS, ACME/HTTPS/firewall y aceptación pública | **Pendiente** | Exclusivamente Etapa 4, sin evidencia inventada de producción |

Antes del smoke final, una corrida completa de 237 casos dio **236 aprobados y 1
fallo**, sin skips: primera generación HTTP/1.1 seguía `running` al vencer el plazo
existente. Una repetición aislada de ese caso pasó (51,52 s), y la última corrida
completa específica pasó ambos protocolos. Ese resultado no demuestra que el fallo
ordinario conocido esté resuelto ni permite cerrar su pendiente. Se agregó una
observación privada test-only que, si reaparece, compara el estado directo del
backend y captura únicamente función/archivo/línea del worker, sin source ni locals.
No usa el acceso directo como fallback exitoso: el test permanece rojo.

Otros fallos de desarrollo del fixture quedaron corregidos y se repitieron:
comparación bytes/texto de versión, nombres de grupos autogenerados, puertos HTTP
globales, perfil de usuario ficticio de Windows y SSLContext compartido que
introducía una carrera ALPN entre H1/H2. La UI existente solicita una fuente remota;
se bloqueó explícitamente (1 solicitud por sesión, informada en evidencia). No se
presenta esa tipografía como validada. El defecto real de logging de desconexiones
se corrigió con `log_append` temprano, manteniendo la exigencia de campos completos.
No hubo evidencia que requiriera cambiar timeouts, compresión o flush de producción.

Evidencia saneada local, ignorada por Git:
`.data/caddy-stage3-reviewed-evidence/observations.json` y `required-tests.xml`.
El `required-tests.private.xml` permanece privado y no es artifact del workflow.
Resultados anteriores: `.data/caddy-stage3-final-evidence/` (236/1),
`.data/caddy-stage3-browser-long-final/` (3 aprobados, ensayo final de chunks),
`.data/caddy-stage3-generation-diagnosis/` (HTTP/1 aislado aprobado) y
`.data/caddy-stage3-browser-final/` (navegador posterior a las últimas observaciones).

Los cambios de esta etapa son los fixtures/tests de proxy y reporte, smoke,
requisitos de herramientas, workflow específico, filtros del workflow de
contenedores, registro de marca pytest, regresión de configuración y este runbook.
El único ajuste de configuración de producción es el modo temprano de cuatro
campos seguros de logging. Se preservaron todos los cambios de Etapas 1–2.
**Working tree para revisión humana; detenerse antes de Etapa 4.**

Inventario exacto de archivos de esta etapa:

| Archivo | Acción / propósito |
| --- | --- |
| `backend/tests/integration/test_caddy_proxy.py` | Nuevo: los 10 casos de proxy real |
| `backend/tests/integration/caddy_fixture.py` | Nuevo: procesos/TCP/TLS/configuración/evidencia aislados |
| `backend/tests/integration/caddy_backend.py` | Nuevo: aplicación real, dobles explícitos y controles solo de prueba |
| `backend/tests/integration/caddy_ui.py` | Nuevo: frontend real con observación privada del servidor |
| `backend/tests/integration/caddy_browser.py` | Nuevo: navegador, WS, privacidad y procedencia |
| `backend/tests/test_caddy_smoke.py` | Nuevo: 4 casos de saneamiento sin perder resultados |
| `backend/tests/test_caddy_config.py` | Ajuste: regresión `early` con JSON generado por Caddy |
| `.github/scripts/smoke_caddy.py` | Nuevo: herramientas verificadas, pruebas obligatorias y reporte saneado |
| `.github/requirements-caddy.txt` | Nuevo: versiones exactas de herramientas Python |
| `.github/requirements-caddy.lock` | Nuevo: mismos pins con hashes oficiales PyPI |
| `.github/workflows/caddy.yml` | Nuevo: configuración/proxy/smoke nativos Linux y ARM64 |
| `.github/workflows/containers.yml` | Ajuste acotado de filtros, checks existentes conservados |
| `pytest.ini` | Registro de marca separada `caddy_proxy` |
| `infra/caddy/Caddyfile` | Cuatro campos de logging temprano, sin cambios de seguridad/streaming/timeouts |
| `infra/caddy/README.md` | Procedimiento, límites, resultados e inventario |

Después del smoke final se comprobaron también el navegador/procedencia y teardown
(**1 aprobado**, 17,85 s) y el diagnóstico privado del worker con SSE activo H1/H2
(**2 aprobados**, 47,88 s). Ruff/formato/diff y compilación Python se repitieron tras
esos ajustes. Ninguna comprobación adicional cierra el pendiente de suite ordinaria.

### Revisión posterior previa al cierre diagnóstico: reproducción fallida

El supervisor reprodujo **240 aprobados, 1 fallo, 0 skips** en el smoke completo:
`test_real_generations_sse_idempotency_and_disconnect[http1]` venció el plazo de
15 s. Se conservan `.data/caddy-supervisor-stage3-evidence/observations.json` y
`required-tests.xml`. La corrida verde histórica anterior no anula esta
reproducción. El estado directo `completed` y el worker esperando trabajo se
observaron después del último estado `running` vía proxy, sin tiempos suficientes
para atribuir ese fallo a Caddy, buffering, SQLite o una consulta obsoleta.

El fixture de instalación preserva por componentes las rutas bajo su sandbox,
incluidas las ya mapeadas y las temporales de staging. La regresión con
`PurePosixPath` falló con el adaptador anterior mostrando exactamente la ruta
duplicada. Los casos de staging ejecutan `TemporaryDirectory` y `secure_path()`
reales; se conserva `install()` real con metadatos POSIX simulados explícitamente.
El adaptador temporal acepta directorio original/convertido y argumentos
posicionales, con directorio por defecto dentro del sandbox. El adaptador #47
preserva rutas temporales y convierte exclusivamente `/opt` y sus descendientes,
sin confundir `/optional`. Windows y PurePosixPath **no acreditan Linux**.

El diagnóstico de esa revisión existía exclusivamente en fixtures. `observations.json`
conserva reloj monotónico compartido del host, sin URLs, IDs de trabajos,
tokens, payloads, source ni locals:

- publicación del archivo de liberación (inicio/fin), observación por el proveedor
  doble e inicio/fin del writer y de cada verificación;
- inicio/fin de cada consulta, índice sintético, protocolo, código HTTP, estado
  permitido y si la respuesta cruzó el deadline existente;
- inicio/fin de las consultas en el backend y muestras del worker durante ellas
  (solo función, archivo basename y línea; últimas 16 entradas);
- tiempos separados de la consulta directa y del muestreo posteriores al timeout.

El middleware mide la devolución de headers de la aplicación, sin consumir ni
acumular cuerpos; el cliente mide la recepción de la respuesta. Los probes
aceptan únicamente GET de estado de jobs **y generaciones**, origen sintético
autenticado e índices acotados. Excluyen SSE, cancelación y otras rutas. El journal
privado solicita modo 0600, publica atómicamente campos/eventos permitidos y
falla explícitamente si excede su capacidad. Se audita junto con todos los
stdout/stderr y logs usados, incluyendo el archivo pendiente si existe.
Comparar tiempos dentro de la misma corrida y ordenar por `at`: el orden de
posiciones en el JSON no establece orden temporal entre threads/procesos.
La instrumentación añade muestras y escritura local; no mide costos sin
instrumentación ni sustituye un perfilado de la aplicación.

El acceso directo nunca es fallback exitoso. No cambiaron los 15 s, los timeouts
de request existentes, el pipeline ni `PresupuestoOCI`. Los tests con dobles
ejecutan `wait_terminal()` real: timeout permanece rojo aunque luego el backend
directo indique `completed`; cubren HTTP fallido, fallo de aplicación, transporte,
timestamps y ausencia de datos sensibles, además del selector de rutas.

Una única corrida diagnóstica de jobs/generaciones H1/H2 dio **3 aprobados, 1
fallo, 0 skips** en 127,90 s (`.data/caddy-stage3-corrections-diagnosis/`). En el
fallo H1, el proveedor observó la liberación a los 0,015 s y terminó a los 0,437 s;
la última de tres verificaciones terminó a los 5,797 s. Las 50 consultas
devolvieron `running` (latencias 0,062–0,953 s); la última terminó a los 15,000 s
y el acceso directo posterior observó `completed` a los 15,922 s. Ningún request
cruzó el deadline. La primera instrumentación omitía `/api/generations/*`: esa
corrida tiene hitos de proveedor, pero carece de muestras del worker durante
la espera de generación. Se conserva el resultado rojo y se corrige la omisión.

La comprobación adicional única de H1 tras corregir el selector dio **1 fallo,
0 skips** en 56,35 s (`.data/caddy-stage3-corrections-worker-route/`): no se repitió
hasta conseguir verde. El proveedor observó la liberación a los 0,031 s y terminó
a los 0,484 s; la última verificación terminó a los 4,343 s. Hubo 41 consultas
`running` (0,125–0,546 s) y **82 muestras del backend**, con inicio y fin
correlacionados para cada consulta. Entre 13,609 y 15,109 s, las muestras ubican
al worker en `_vigente` / `_persistir`, `documento_grafo`, operaciones presupuestadas
y `_transaccion`; después del plazo sigue activo en `_finalizar` / `_persistir`.
La consulta directa todavía devolvió `running` a los 16,046 s. El último request
comenzó dentro del plazo y terminó a los 15,156 s; esto se registra explícitamente
sin modificar el comportamiento existente. El diagnóstico posterior termina a
los 16,187 s y no amplía la ventana de polling de 15 s.

**Conclusión acotada:** en esta última reproducción el worker todavía ejecutaba
finalización de aplicación; el proveedor ya había terminado y las consultas
respondían. Las muestras no demuestran si el costo procede de espera de locks,
commit/fsync, I/O de archivos, cantidad de validaciones o su interacción con
polling. Tampoco prueban que esta sea la causa exacta del fallo histórico del
supervisor. No se cambian Caddy, flush, buffering, timeouts ni SQLite.

Propuesta fuera de esta corrección, pendiente de autorización: perfilado acotado
de la finalización y consultas de estado, con tiempos separados de adquisición,
ejecución y cierre de transacciones y costos de comprobaciones de vigencia,
preferentemente también en Linux aislado. Medir primero; mantener todas las
autorizaciones, vigencia, cuotas y durabilidad de `PresupuestoOCI`, sin ampliar
plazos ni modificar la aplicación para obtener verde. **No implementado.**

La plataforma continúa siendo Windows AMD64/Python 3.11.9, con Caddy 2.11.7 y
Compose 2.40.3 oficiales temporales. WSL informa que no está instalado y no hay
Docker disponible: fixture y smoke obligatorio nativos Linux/ARM64 **no ejecutados**.
Los resultados anteriores de redirects, WS, privacidad, silencio de 135 s y
upload lento se conservan; no se presentan como repetidos por esta tanda parcial.
La auditoría al cierre de la reproducción con worker inspeccionó 19 canales,
66 registros de acceso y 27 operativos sin canarios.

Cambios de esta corrección: `test_caddy_deploy.py`, `test_caddy_smoke.py`,
`integration/caddy_fixture.py`, `integration/caddy_backend.py`,
`integration/test_caddy_proxy.py`, nuevo `integration/caddy_diagnostics.py` y este
runbook. Los demás cambios existentes se preservan. Suite ordinaria completa y
su fallo conocido, permisos/instalación POSIX, systemd, Docker/NAT, VM Ubuntu ARM64,
workflows Linux y aceptación pública continúan pendientes. Detenerse antes de
Etapa 4; ninguna prueba con dobles sustituye esas comprobaciones.

Validaciones locales de esta corrección (cada corrida se conserva separada):

| Verificación | Resultado observado |
| --- | --- |
| Regresión POSIX con mapeo anterior | **1 fallo esperado**, detecta ruta duplicada; no es Linux nativo |
| Primera tanda configuración/despliegue/reporte, sandbox Windows | **224 aprobados, 16 fallos**, rechazos de reemplazo atómico/procesos; `.data/caddy-stage3-corrections-unit.xml` |
| Misma tanda fuera del sandbox | **240 aprobados, 0 skips**, 25,08 s; `.data/caddy-stage3-corrections-unit-native.private.xml` |
| Tanda final, con 14 casos adicionales del selector de rutas | **253 aprobados, 1 fallo, 0 skips**, 25,30 s; `.data/caddy-stage3-corrections-final-unit.private.xml` |
| Proxy jobs/generaciones H1/H2, primera instrumentación | **3 aprobados, 1 fallo, 0 skips**, 127,90 s |
| Generación H1 con muestras del worker corregidas | **1 fallo, 0 skips**, 56,35 s |

El fallo adicional de la última tanda fue
`test_activation_failure_restores_previous_configuration[frontend-start]`:
la recuperación devolvió `deployment-and-recovery-failed` en lugar de
`prior-service-state-restored`. En el sandbox preservado
`.data/caddy-stage3-corrections-final-unit-temp/test_activation_failure_restor2/`
permanecen `pending.json` y `Caddyfile.test-pending`; este último contiene la
configuración anterior, y tanto Caddyfile como current.json siguen en el estado
anterior. La publicación de recuperación no terminó. El helper suprime por
contrato los detalles de la excepción original; esta evidencia no permite
confirmar su causa (un rechazo del reemplazo Windows es una hipótesis).
**Fallo abierto y no repetido para conseguir verde.** No se cambió el helper,
la recuperación, las assertions ni sus dobles para evitarlo. Hace falta una
observación acotada de la excepción de publicación, sin secretos, en una revisión
posterior; el snapshot de fallo permanece intacto.

Ruff check, formato (**230 archivos**), compilación Python de fixtures/tests y
smoke, `git diff --check` y comprobación de whitespace de los siete archivos
añadidos: **aprobados**. En los archivos untracked, `git diff --no-index --check`
devuelve 1 por la adición y no emite diagnósticos de whitespace. La tanda de
configuración volvió a usar adaptación/validación/fmt reales de Caddy 2.11.7 y
configuración combinada real de Compose 2.40.3; no cambiaron sus contratos.
Se publican JUnit saneados `.xml` separados junto a los `.private.xml`,
conservando cada fallo y sin capturas privadas. Branch, HEAD y base se mantienen
en `feature/issue-48-caddy-proxy` / `008ee4d5eaed8df29bf12f73b941936afe775482`.
Los fallos abiertos impiden presentar esta validación como completamente aprobada.

### Cierre diagnóstico acotado: perfil opcional y publicación observada

El supervisor obtuvo después **254 aprobados** con Ruff/formato correctos. Eso
no explica ni resuelve los fallos históricos anteriores. Se preservaron todos sus
reportes y el snapshot de recuperación fallida; no se avanzó a Etapa 4.

**Modo actual:** el smoke normal usa `NM_CADDY_DIAGNOSTIC=off` por defecto.
No envía probes de consultas, no toma muestras del worker, no instala wrappers
de perfil y no exporta timelines. Las llamadas `Timeline.record()` del proveedor
son no-ops sin reloj, locks ni I/O en ese modo. Se conserva el polling, todas sus
assertions y el plazo de 15 s. Solo se registra un resultado global al salir del
wait, fuera de la ventana de polling. `NM_CADDY_DIAGNOSTIC=profile` es opt-in
explícito para la corrida diagnóstica; otros valores fallan.

En modo perfil, Timeline y Profile almacenan observaciones acotadas en memoria
(4.096 eventos / 50.000 spans, exceso rechazado explícitamente). No hay
reescrituras de JSON ni I/O de exportación desde cada consulta o desde el worker.
La exportación autenticada se solicita después de terminar el wait —incluyendo
su fallo— y antes de terminar los procesos. Los spans aún activos se marcan
`in-progress`, con `end=null` y `observed_until`, sin inventar un fin. Un error de
exportación queda explícito en evidencia y no sustituye el fallo original ni
se acepta como perfil válido. La serialización y escritura del artifact ocurren
fuera del tramo medido. Los contadores y gates del proveedor de prueba preexistentes
siguen iguales en ambos modos; no son escrituras del perfil.

`caddy_profile.py` envuelve temporalmente `_vigente`, `_persistir` y `estado`
del servicio real. Para el presupuesto, un facade exclusivo del fixture delega
a `sqlite3.connect` real con una subclase de Connection que delega sus llamadas
nativas. No sustituye `_transaccion`, ni sus PRAGMA, BEGIN IMMEDIATE, consultas,
commit, cierre, autorizaciones, cuotas o manejo de errores. No se modifica ningún
archivo de aplicación ni de `PresupuestoOCI`; las referencias originales se
restauran al finalizar. Se miden por separado conexión, PRAGMA, BEGIN, execute,
commit y close. Execute incluye la llamada nativa, **excluye fetch/iteración del
cursor**; los commits implícitos de inicialización no están desglosados.
No se captura SQL, parámetros, rutas privadas, payloads, secretos ni locals.

Los tiempos son **inclusivos de pared**. Los scopes `generation`, `status` y
`other` separan el contexto de los wrappers; `other` no identifica un thread.
Las estadísticas recortan al intervalo observado y unen intervalos superpuestos;
no suman persistencia + vigencia ni mezclan duraciones concurrentes. El reloj
monotónico local es GetTickCount64, resolución **15,625 ms**: cifras aproximadas,
intervalos cortos no resueltos y acumulaciones por fase sujetas a cuantización.
Un valor de 0 no prueba costo cero. La instrumentación en memoria todavía añade
overhead; una comparación de una muestra por modo no lo estima causalmente.

Se ejecutó exactamente **una H1 normal y una H1 con perfil**, con procesos,
directorios/datos nuevos, mismos proveedores dobles, mismo test y contenido,
configuración equivalente y dos workspaces. En ambas, el contador inicial de
generaciones fue 0; no hubo estado previo de jobs/generaciones. Los tiempos de
ejecución y cachés del sistema operativo no se consideran controlados.

| Comprobación | Resultado observado |
| --- | --- |
| Configuración/despliegue/reporte y tests del diagnóstico | **260 aprobados, 0 skips**, 25,79 s |
| H1 normal sin diagnóstico | **1 aprobado, 0 skips**, 51,48 s total; upload wait 2,515 s, generación wait **13,782 s** |
| H1 con perfil en memoria | **1 aprobado, 0 skips**, 63,99 s total; upload wait 4,672 s, generación wait **14,484 s** |
| Publicación durante `frontend-start` | **No reproducido**; no excepciones de publicación observadas |

La diferencia de 12,51 s entre totales incluye preparación, exportaciones y
teardown; **no mide overhead dentro de la generación**. Las exportaciones del
perfil tardaron ~2,2 s cada una y empezaron después de sus ventanas medidas.
En la generación perfilada, el proveedor terminó a los 0,437 s y la última
verificación a los 4,922 s; se observaron 46 consultas de estado y 366 transacciones
presupuestadas. Datos aproximados de esa ventana de 14,484 s:

| Componente | Cantidad | Cobertura temporal por unión, segundos |
| --- | --- | --- |
| `_persistir` | 1 | 8,328 (desde 6,000 hasta 14,328) |
| `_vigente` | 27 | 6,592, contenida/superpuesta con persistencia |
| Unión persistencia/vigencia | — | **8,328** |
| `estado` | 46 | 9,488, concurrente con el worker |
| Budget connect / PRAGMA / BEGIN / execute / commit / close | 366 / 366 / 366 / 1.098 / 366 / 366 | 0,454 / 5,584 / 3,744 / 0,032 / 7,110 / 1,217; **no sumar entre threads** |
| Unión de todas las llamadas medidas del budget | — | **12,613** |

En scope `status` hubo 145 transacciones y cobertura del budget ~8,958 s; en
scope `generation`, 169 y ~6,841 s; en `other`, 52 y ~3,784 s. Esas coberturas
también se superponen. El código real de `estado` llama `_propia`, que revalida
detalle y workspace mediante storage presupuestado: el perfil muestra costo de
las **consultas de estado además del trabajo de finalización**. No demuestra
si la latencia proviene de locks, durabilidad/fsync, sistema de archivos/antivirus,
frecuencia de validaciones, instrumentación o su combinación. No se deduce
contención a partir del tiempo de BEGIN/PRAGMA ni se cambia SQLite para obtener verde.

Para recuperación, la observación se limita al fixture `portable_atomic`:
staging-write / atomic-replace, destino categórico, resultado, tipo de excepción,
errno y winerror. No captura contenido, nombres de archivos privados, rutas ni
mensajes de excepción; vuelve a lanzar exactamente la excepción original y no
reintenta. El reporte se exporta después del test aun si el helper suprime el
error original. La prueba de excepción inyectada 13/5 es un doble explícito, no
evidencia de que el fallo histórico tuviera esos códigos. El caso real
`frontend-start` de la tanda se ejecutó una vez y pasó, sin errores observados:
**histórico no reproducido, pendiente de explicación**, no resuelto por verde.

Evidencia nueva, saneada y separada (todo ignorado por Git):

- `.data/caddy-stage3-closure-h1-normal/`: observaciones y JUnit de la única H1 normal.
- `.data/caddy-stage3-closure-h1-profile/`: observaciones/perfil y JUnit de la única H1 perfilada.
- `.data/caddy-stage3-closure-summary.json`: comparación y uniones calculadas fuera del tramo medido.
- `.data/caddy-stage3-closure-unit.xml` / `.private.xml`: tanda de 260 casos, captura privada separada.
- `.data/caddy-stage3-closure-publication/`: observaciones de publicación, incluido `frontend-start`.

Las auditorías de cierre revisaron todos los 18 canales usados en cada corrida:
normal, 78 accesos / 27 operativos; perfil, 87 / 27, sin canarios. Las muestras
en memoria solo contienen etiquetas y metadatos permitidos; el test nativo de
budget conserva cuotas/durabilidad y prueba excepciones y ausencia de datos
sensibles. El smoke largo completo y la suite ordinaria **no se repitieron**.

**Conclusión y próximo paso propuesto:** ambos fallos históricos siguen abiertos.
El perfil limita la zona a operaciones de vigencia/persistencia y lecturas de
estado presupuestadas, pero no prueba el mecanismo del costo ni la causa de los
timeouts históricos. Investigar esa interacción con mayor precisión de reloj y
observaciones de I/O/locks, con una nueva autorización acotada; no eliminar
revalidaciones, cuotas, durabilidad, ni ampliar plazos. Para publicación, conservar
el observador hasta capturar una recurrencia autorizada con etapa/códigos reales,
sin repeticiones indefinidas. **No se implementó ninguna corrección de aplicación.**
Linux/ARM64, suite completa, instalación/systemd/Docker/NAT y aceptación pública
continúan pendientes, y la aprobación no depende solamente de una VM real.

Ruff y formato (**231 archivos**), compilación Python y `diff --check` (también
whitespace de los siete archivos untracked afectados): **aprobados**. Se verificó
que los exports empezaran después del wait, que no tuvieran fallos y que sus
campos fueran permitidos, sin secretos/canarios. Caddy fmt/adapt/validate y Compose
combinado se ejecutaron nativamente dentro de la tanda de configuración.
No se cambiaron Caddy, overlays productivos ni archivos de aplicación.
Archivos de este cierre: `integration/caddy_diagnostics.py`, nuevo
`integration/caddy_profile.py`, `integration/caddy_backend.py`,
`integration/caddy_fixture.py`, `test_caddy_smoke.py`, `test_caddy_deploy.py` y este
runbook. Branch/HEAD/base y todos los cambios anteriores se conservan.

</details>

### Etapa 4 — archivos y protocolo preparados; ejecución NO autorizada

Este apartado prepara la revisión. **No autoriza despliegue, tráfico público,
escaneos, llamadas a proveedores ni cambios de DNS/firewall.** La evidencia Linux
del HEAD 42af012 y sus límites constan al inicio. Los incidentes Windows conservan
su causa no demostrada y evidencia histórica, sin convertirlos en bloqueos fijos
del colector. Siguen pendientes instalación/permisos/systemd y Docker/NAT del
despliegue real, además de los tres criterios públicos. Ninguna aprobación de CI
autoriza por sí sola estas ejecuciones.

#### Verificador y frontera de autorización

`verify_public.py` utiliza solamente la biblioteca estándar de Python. Sin
`--execute`, valida hostname ASCII explícito (sin URL/query/credenciales), todas
las IP públicas de destino, IP de salida externa declarada y todos los CIDR
administrativos. No resuelve DNS, abre sockets ni pide credenciales. Ejemplo de
**plan sin red** (variables completadas por el operador, sin secretos):

```bash
python infra/caddy/verify_public.py \
  --domain "$DOMINIO" --target-ip "$IP_VM" \
  --source-ip "$IP_SALIDA_EXTERNA" --admin-cidr "$CIDR_ADMIN"
```

Repetir `--target-ip` por cada A/AAAA publicado y `--admin-cidr` por cada rango
administrativo autorizado. El operador debe verificar que la enumeración es
completa. Una IP privada/local/reservada, ID mal formado, origen incluido en un
CIDR administrativo o argumentos incompletos se rechazan. La validación offline
no acredita DNS, propiedad del dominio/IP ni el origen real detrás de NAT/VPN.

**Solo después de nueva autorización expresa**, el operador podrá añadir uno de:

| Selección futura | Operaciones exactas | Límites de la evidencia |
| --- | --- | --- |
| `--execute web --operator-authorized` | Resolver DNS y exigir coincidencia exacta con IP autorizadas; GET `/` HTTPS y HTTP por cada IP | TLS estricto del almacén del sistema, SNI/Host del dominio y 308 exacto a HTTPS; no acredita CA pública ni UI usable |
| `--execute sse --operator-authorized --generation-id "$ID_PROPIO"` | GET de estado y SSE H1 por cada IP; desconectar, consultar estado, reconectar con `Last-Event-ID` | No crea/sube/persiste/cancela trabajos; mide recepción, no emisión ni tiempo de proveedores; no acredita H2 ni ejecución real por sí solo |
| `--execute tcp --operator-authorized` (diagnóstico opcional) | Conexiones TCP a **1–65535 inclusive** por cada IP, 16 conexiones concurrentes como máximo, timeout de sonda 1 s | Puede tardar más de una hora por IP; timeout = `no_response`, nunca cerrado. Rango parcial/error/silencio queda inconcluso; una contradicción observada conserva el fallo |

Estas opciones son una declaración del operador sobre una autorización **futura**,
no una autorización otorgada por este documento. No ejecutar ninguna ahora.
El permiso deberá especificar dominio, IP de destino, red de origen, ventana y
alcance de escaneo/lecturas/generación/proveedores. Usar un equipo externo y el
Python verificado de su sistema; registrar versión, OS/arquitectura y procedencia
de las herramientas. El colector no instala herramientas ni servicios.

El Bearer se introduce únicamente en el prompt oculto de una terminal interactiva;
stdin redirigido o fallback de `getpass` con eco se rechazan. Nunca pasarlo como
argumento, URL, variable de comando, cookie copiada, captura de consola ni archivo
de reporte. No usar `curl -H` con credenciales, verbose, tracing, HAR ni grabación
de sesión. No capturar comandos con headers/secretos. No hay `--insecure`, CA local,
redirecciones automáticas ni fallback a backend directo. Se rechaza
`SSLKEYLOGFILE` antes de crear el contexto TLS para impedir logs de claves de
sesión; no usar captura/descifrado TLS durante estas sondas. Las conexiones HTTPS se
fijan a la IP aprobada, conservando validación de hostname/SNI y cadena TLS.

Solo salen códigos constantes, status HTTP numéricos, tiempos relativos, IDs de
evento enteros, contadores y puertos; nunca texto de excepciones, headers, cuerpos
de respuesta, contenido SSE, generación ID ni Bearer. Se rechazan headers de
origen privados y cualquier header que refleje el Bearer. No exportar memoria,
locals o trazas privadas. Los certificados/headers/cuerpos que se inspeccionen
manualmente se resumen, no se vuelcan al informe.

El JSON lleva UTC, plataforma, destino público y origen **declarado, no probado**.
Cada comprobación hereda ese contexto; `target_index` identifica su dirección en
`target.addresses`. Separa implementación (`collector`/`runbook`), tipo de prueba,
ejecución, resultado y observaciones. `not_executed`, `failed` e `inconclusive` son
resultados distintos. Una comprobación automática aprobada deja el criterio
inconcluso mientras falte evidencia humana/host. Cada check declara `required`;
`full_tcp_range` es el único opcional y no figura en `pending`, aunque no se ejecute
o resulte inconcluso. Sus observaciones y resultado se conservan: un fallo también
hace fallar el criterio y la ejecución, sin poder compensarlo con otra IP aprobada.
`nonadmin_ssh_unreachable` sigue siendo obligatorio, de verificación humana/host;
si se usa el escáner, éste aporta además su observación de TCP/22. El colector no
incorpora nuevas sondas ni recibe/aprueba fichas externas. No exporta
`historical_pending` ni estados universales de suite/CI: esa evidencia se revisa
por HEAD/plataforma en este runbook. El colector **siempre** emite
`acceptance: not_approved`; no acepta JSON de terceros para convertirlo en verde.
Código de salida: 0 plan válido sin red; 1 entrada/sonda fallida; 2 ejecución con
evidencia parcial/inconclusa. El 0 de planificación no es aceptación pública.

Guardar stdout saneado en un artefacto nuevo con acceso restringido, nunca
sobrescribir corridas previas. No versionar IP de salida, CIDR administrativos,
sesiones o material privado. El informe deliberadamente omite origen IP exacto;
el revisor debe conservar su comprobación en un anexo restringido.

#### Prerrequisitos y comprobaciones del host (sin ejecutarlas en esta etapa)

1. Revisión y autorización independiente del despliegue, con los pendientes de
   Etapa 3 explícitos. Registrar branch/HEAD, imagen/digests, Caddy 2.11.7 real,
   Ubuntu ARM64 y herramientas verificadas. Completar instalación, permisos,
   unidad systemd y ciclo de recuperación nativos; los dobles Windows no sirven.
2. Desde acceso administrativo por clave, inspeccionar Compose **combinado**,
   mounts/env_file, un worker, volumen estable, peers reales y trust gate, sockets
   8000/8501 exclusivamente loopback y salida autorizada hacia proveedores.
   Revisar firewall del host, servicio de #47 y NSG/security lists OCI tanto IPv4
   como IPv6. No registrar valores de env/secretos ni modificar reglas para pasar.
3. Enumerar A/AAAA/CAA desde resolvers externos y registrar todas las direcciones
   publicadas. Compararlas con la VM y lista autorizada. AAAA sin cobertura
   verificada es inconcluso/fallido; no omitirlo del ensayo. Confirmar dominio
   autorizado sin compra automática y ACME de producción (sin staging/CA interna).
4. Correlacionar reloj UTC y referencias de evidencia restringidas con alias
   locales del ensayo; registrar origen externo, ausencia de VPN/túnel hacia la
   red administrativa y IP de salida comprobada por un medio aprobado. No añadir
   consultas a servicios de IP pública no autorizados. El flag del colector no
   prueba la red de origen. Revisar los logs filtrados sin volcar contenido.

#### Criterio 1: HTTPS público y UI usable

**Automático futuro:** sonda `web` en cada A/AAAA autorizado. Exigir TLS estricto,
hostname/fechas válidos, HTTP 308 al mismo dominio HTTPS y UI HTTP 200. Un fallo
de TLS/HTTP queda fallido; no reintentar hasta obtener verde ni usar `-k`.

**Humano desde red externa:** navegador sin excepción TLS ni CA de prueba.
Inspeccionar la cadena hasta una raíz pública reconocida (el almacén local puede
tener raíces privadas), issuer, vigencia, SAN y advertencias. Registrar sólo
metadatos públicos permitidos. Abrir la UI, crear/acceder al espacio de prueba,
realizar una interacción que produzca cambio visible y navegar por contenido.
Observar las solicitudes de React a `/api` y el efecto visible de la interacción,
sin exportar tokens ni cuerpos privados. La UI React no usa el WebSocket de
Streamlit. El seguimiento SSE de #33 está integrado; su aceptación con
proveedores reales y desde la URL pública sigue pendiente en #133.
Interrumpir la red brevemente,
restaurarla y confirmar reconexión SSE y una nueva interacción funcional.
Una respuesta HTTP aislada no acredita ese recorrido. Anotar navegador/versión,
UTC, acciones y resultados saneados.

**Host y privacidad:** comprobar confianza de origen entre Caddy y backend,
autenticación de API, CSP del servidor estático y ausencia de clave de origen en
headers públicos, HTML y estado enviado al navegador. CORS del backend está
deshabilitado por defecto; los controles CORS/XSRF y el contexto privado de
Streamlit documentados en las pruebas históricas no se atribuyen a React.
Usar comprobaciones booleanas en
memoria o inspección restringida, sin mostrar/renderizar la clave ni exportar
HTML/estado/HAR. Resumir sólo hallazgos y contadores. Sin esta inspección el
criterio permanece inconcluso, aunque la UI responda 200.

#### Criterio 2: generación real, progreso SSE y reconexión

**Host:** acreditar configuración efectiva sin mocks de Gemini/OCI ni sustitución
local, credenciales autorizadas, cuotas/presupuesto y almacenamiento real.
No bastan flags declarados, `completed` ni `persistencia.provider=oci`: correlacionar
la intención con ejecución del worker, solicitudes reales Gemini y confirmación
de escritura/lectura OCI del objeto del mismo ensayo. Conservar únicamente UTC,
contadores de ejecuciones, operaciones/resultados y alias de correlación; sin
prompts, fuentes, SQL, cuerpos, tokens, claves OCI, rutas/objetos privados o locals.
Los reintentos/nodos legítimos de IA no son una segunda ejecución de la intención.

**Humano/cliente autorizado:** crear dos espacios propios A y B con sesiones
independientes. Subir una fuente sintética no sensible en A y esperar su admisión.
Iniciar por el dominio `POST /api/generate` con el contrato actual, Bearer de A y
una `Idempotency-Key` propia estable; conservar la solicitud sólo en memoria.
Usar este cuerpo, sustituyendo únicamente el ID del documento admitido propio:

```json
{
  "document_id": "<ID_DOCUMENTO_PROPIO>",
  "perfil_destinatario": "principiante",
  "formato_salida": "flashcards",
  "nicho_sector": "general",
  "nivel_detalle": "didactico",
  "idioma_salida": "es"
}
```

Registrar privadamente el ID propio retornado y verificar que status/events URL
mantienen `/api`, mismo hostname y HTTPS; nunca seguir URLs recibidas hacia otro
origen con el Bearer. Empezar el seguimiento mientras está activo. La creación
real y esas llamadas requieren autorización adicional, no las hace el colector.

Para repetición exacta de la intención usar un cliente aprobado con credencial
interactiva oculta, TLS estricto, redirects desactivados y sin impresión de
request/response. Puede usarse `getpass` + un cliente HTTP Python existente con
payload en memoria; no improvisar comandos que incluyan el Bearer. Reenviar el
mismo cuerpo y `Idempotency-Key` por el dominio tras desconectar el SSE: debe
retornar el mismo ID. Con B, consultar estado/SSE de A y comprobar 404 sin revelar
resultados. Registrar sólo status y booleanos de igualdad/aislamiento.

**Automático futuro:** sonda `sse` H1 con ID de A y Bearer oculto; conserva tiempos
de recepción de eventos, separación, heartbeat, estado antes/después de
desconectar y cursor de reconexión. La ventana de lectura del cuerpo SSE por
conexión es 90 s (configurable 20–300 como ventana del colector, no del pipeline).
Su inicio es el instante monotónico al entrar a `events()`, después de recibir y
validar los headers HTTP; el deadline absoluto es inicio + ventana. Tanto la
conexión inicial como la reconexión tienen su propia ventana. No es un plazo
total del comando, de las consultas de estado ni de la generación.

`DeadlineHTTPResponse` conserva el parser HTTP de Python y aplica el tiempo
**restante** del mismo deadline en **cada** lectura raw del socket TCP/SSL bajo
su buffer. Cubre silencio, líneas SSE incompletas, fragmentos periódicos y tamaños,
delimitadores o trailers HTTP chunked incompletos. No utiliza un timeout de
inactividad constante que se reinicie por cada fragmento ni threads lectores.
También comprueba el reloj después de `readline()` para excluir datos tardíos,
incluidos los ya presentes en el buffer. Un evento/heartbeat sólo cuenta con su
delimitador de bloque completo antes del deadline (**igualdad queda excluida**).
EOF no convierte una línea/bloque incompleto en evidencia.

El informe conserva eventos válidos parciales y agrega `window.end`:
`deadline` (plazo agotado), `eof` (fin del stream) o `sufficient` (mínimo observado
antes del plazo), junto con límite y duración observada. Los tiempos de recepción
de eventos conservan precisión monotónica sin redondearlos fuera de la ventana;
la duración resumida se redondea a milisegundos. Al agotarse la ventana se cierra
la respuesta y la conexión se cierra al salir de su contexto, sin tareas de
lectura pendientes. El timeout del socket se restaura antes del cierre; los
timeouts ajenos a esta ventana y fallos TLS/protocolo no se convierten en éxito.
La expiración no cancela el trabajo. Una reconexión que agote la ventana conserva
sus IDs válidos, pero queda inconclusa; ausencia de cursor no se considera éxito.
El SO puede devolver el control con una pequeña latencia de planificación, pero
ningún bloque completado en/plazo vencido se contabiliza.

Sin dos
eventos separados >=0,25 s, heartbeat, estado activo antes/después y eventos
posteriores al cursor, la evidencia es incompleta/inconclusa o fallida. Una
generación que termina antes de observarlo no debe usarse para afirmar progreso
en vivo. Eventos históricos/replay temporizados tampoco acreditan esa propiedad.

**Humano y host:** correlacionar recepción antes del fin real del worker, heartbeat
mientras sigue activo, Last-Event-ID sin repetir eventos y desconexión sin
cancelación. Hacer una observación equivalente H2 con cliente aprobado que
acredite ALPN/protocolo negociado y capture únicamente IDs/tiempos/contadores
(sin dumps de headers/data). El colector estándar sólo implementa H1; H2 queda
pendiente expresamente. Por ejemplo, con HTTPX/h2 de procedencia/versiones
verificadas, usar `Client(http2=True, verify=True, follow_redirects=False,
trust_env=False)`, Bearer de `getpass` en memoria y `stream("GET", ...)` sobre
el dominio; exigir `response.http_version == "HTTP/2"`, status 200 y
`text/event-stream` antes de contar bloques SSE. Repetir GET con el último ID
mediante `Last-Event-ID`, exigir todos los IDs posteriores y tiempos de recepción
antes del fin del worker. No activar logs/debug de HTTPX ni imprimir excepciones,
headers o data; registrar sólo esas comprobaciones y IDs/tiempos. Sin HTTP/2
negociado, mantener pendiente esa observación, sin fallback H1 aprobado.
Comparar contadores de admisión/ejecución antes y después
de reconectar y de repetir la misma intención: un solo trabajo/ejecución; comparar
también ID y resultado final autorizado. Confirmar contenido usable y persistencia
real con evidencia de proveedor independiente. No usar acceso directo como
fallback exitoso ni repetir hasta conseguir verde. Si termina demasiado rápido
o falta trazabilidad disponible, registrar inconcluso y proponer un ensayo
adicional sujeto a aprobación; no introducir retardos en producción.

#### Criterio 3: exposición TCP y acceso administrativo

**Comprobación principal humana/host y externa correlacionada:** revisar todas
las NSG/security lists OCI asociadas, firewall efectivo del host, interfaces,
rutas y listeners/bindings. Enumerar todas las direcciones publicadas A/AAAA y
correlacionar esa política con sondas externas autorizadas de web 80/443, SSH 22
y puertos internos 8000/8501 desde una red no administrativa comprobada. Exigir
aplicación web accesible, puertos internos inaccesibles y SSH restringido, sin
otras aperturas en reglas/listeners. Registrar UTC, dirección/interface revisada,
origen y resultado saneado por comprobación; una dirección/interfaz/regla omitida
deja evidencia incompleta. Una inconsistencia impide aprobar el criterio. Estas
observaciones se adjuntan como fichas revisadas; el colector no agrega sondas
selectivas ni las da por ejecutadas.

**Diagnóstico automático opcional:** si se autoriza `tcp`, escanear 1–65535 sin
exclusiones por cada IP autorizada. Conservar rango/cantidad efectiva, conteos,
puertos abiertos, fallos e inconclusos. Su ausencia no es un requisito incumplido.
Si revela un puerto adicional abierto (incluidos 22, 8000 o 8501) o una contradicción
con las comprobaciones principales, conservar el fallo: no omitirlo por ser opcional
ni compensarlo con otra dirección aprobada. Un rango parcial/error/silencio no
acredita ese diagnóstico. `no_response` queda inconcluso hasta revisar origen,
conectividad y políticas; un timeout nunca demuestra puerto cerrado. El rechazo
explícito de SSH desde esa red es evidencia de inaccesibilidad, sin acreditar
autenticación ni la política de origen por sí solo.

**Host y humano:** correlacionar silencios/rechazos con políticas efectivas de #47
y OCI, revisar IPv6 y rutas alternativas. Confirmar SSH rechazado desde la red
externa no administrativa y permitido desde el CIDR administrativo aprobado.
Desde éste realizar autenticación por clave con `ssh -o BatchMode=yes
-o PasswordAuthentication=no -o StrictHostKeyChecking=yes ubuntu@<dominio>` usando
clave ya instalada/agente y host key previamente verificada por canal confiable;
no aceptar automáticamente claves nuevas ni registrar privada/passphrase.
Inspeccionar configuración efectiva de sshd: acceso por clave y contraseña
deshabilitada según #47. El verificador no usa SSH ni cambia permisos/firewall.
Registrar ambos orígenes, métodos y resultados en evidencia restringida saneada.

#### Evidencia y decisión humana por criterio

Adjuntar los JSON del colector **y** una ficha por observación humana/host:

| Campo | Contenido permitido/requerido |
| --- | --- |
| Implementación | Archivo/versión/HEAD que ofrece la comprobación; nunca confundir implementado con ejecutado |
| Comprobación ejecutada | Identificador de la tabla CHECKS o paso del protocolo, acción concreta y UTC |
| Resultado | **No ejecutado**, **fallido**, **inconcluso** o comprobación **aprobada**; motivo saneado y alcance exacto |
| Plataforma/origen | OS/arch/herramienta/versión; red externa o host/admin, origen comprobado vs declarado, A/AAAA ensayados |
| Evidencia | Referencia a artefacto saneado nuevo, tiempos/IDs de evento/contadores/booleanos, sin datos privados |
| Pendientes | Comprobaciones obligatorias faltantes del criterio; diagnóstico opcional separado y evidencia nativa faltante. Incidentes históricos y CI se revisan por fecha/HEAD/plataforma, sin bloqueo ni aprobación hardcodeados |

Una ficha sin ejecución debe decir **no ejecutado**, no "aprobado por tests".
Dobles de TLS/HTTP/socket/reloj de `test_caddy_public.py` validan localmente el
verificador; no acreditan certificados, proveedores, usabilidad o exposición
pública. No existe aprobación global automática. El revisor sólo podrá aceptar
los tres criterios con todos los pasos obligatorios reales y trazables; deberá
mantener por separado el estado de Etapa 3 y la suite ordinaria. Cada fallo se
conserva con su evidencia, incluso si una corrida posterior resulta verde.

**Estado de esta preparación:** archivo, protocolo y tests locales preparados;
ninguna sonda pública, generación real, escaneo, observación humana externa o
comprobación nueva del host ejecutada. Detenerse para revisión antes de todo ello.

<details>
<summary>Historial local y diagnóstico de CI — 13–14/10/2026, anterior a la evidencia Linux de 42af012</summary>

#### Validación local de esta preparación — 13/10/2026

Windows AMD64, Python 3.11.9 y Ruff 0.16.8 de `.venv`; sin nuevas dependencias, instalación ni
servicios. Los tests prohíben DNS/conexiones nativas y sustituyen explícitamente
transporte, reloj y sondas de puertos. Sí se comprueban nativamente el contexto
TLS estricto sin conexión, sintaxis Python y ausencia de creación del key log.
Eso no es evidencia de negociación TLS/certificado público, POSIX o Docker.

| Verificación efectivamente ejecutada | Resultado observado |
| --- | --- |
| `test_caddy_public.py`, versión final | **67 aprobados, 0 fallos, 0 skips**, 5,43 s; `.data/caddy-stage4-public-final-safe.xml` |
| Verificador + regresiones `test_caddy_smoke.py`, antes de los casos adicionales de estado terminal/key log | **92 aprobados, 0 fallos, 0 skips**, 12,03 s; 63 del verificador y 29 de reporte/diagnóstico. `.data/caddy-stage4-final-unit.xml`; los tests adicionales se comprobaron en la tanda final de 67 |
| CLI predeterminada, entradas sintéticas | **Plan válido sin red**, criterios no ejecutados y `acceptance: not_approved`; `.data/caddy-stage4-offline-plan-example.json`. Las direcciones del ejemplo no son destinos autorizados para ejecutar sondas |
| `ruff check .` / `ruff format --check .` | **Aprobados**, 233 archivos Python formateados |
| Compilación Python de verificador/tests | **Aprobada** |
| `git diff --check` y whitespace de los tres archivos de esta preparación | **Aprobados**; `--no-index --check NUL` devuelve 1 por la adición, sin diagnósticos de whitespace |
| Sondas públicas, Gemini/OCI reales, escaneo y observaciones externas/host nuevas | **No ejecutados**, no autorizados |
| Smoke largo y suite ordinaria completa | **No ejecutados** en esta preparación; resultados y fallos históricos conservados |
| Nueva validación Caddy/Compose productivos y workflows remotos | **No ejecutados**; contratos/configuración productiva sin cambios y workflows remotos no autorizados |

Sólo se crearon `infra/caddy/verify_public.py`, `backend/tests/test_caddy_public.py`
y se actualizó este runbook. Branch `feature/issue-48-caddy-proxy`, HEAD/base
`008ee4d5eaed8df29bf12f73b941936afe775482` y cambios anteriores conservados.
**A:** preparación implementada. **B:** verificador probado localmente con dobles,
sin aceptación pública. **C:** los tres criterios públicos siguen no ejecutados;
Etapa 3, suite ordinaria y Linux/ARM64 siguen pendientes. Revisión humana antes
de cualquier autorización para desplegar o ejecutar tráfico real.

#### Corrección del deadline SSE — revisión posterior, 13/10/2026

La revisión independiente reprodujo 96 tests aprobados y checks de estilo, pero
detectó que la versión anterior comprobaba el reloj únicamente antes de una
`readline()` bloqueante. Esas corridas verdes se conservan como históricas: no
demostraban el límite temporal. Se corrigieron únicamente `verify_public.py`,
`test_caddy_public.py` y este runbook, sin cambiar aplicación, Caddy, overlays,
plazos del pipeline, credenciales, TLS estricto o autorización de sondas.

| Verificación efectivamente ejecutada | Resultado/evidencia conservada |
| --- | --- |
| Reproducción determinista antes de corregir: ventana 20 s, eventos a 2/4 s, heartbeat a 5 s, delimitador a 121 s | **Fallido esperado:** contó 1 heartbeat tardío; `.data/caddy-stage4-deadline-red.xml` (1 fallo, 67 casos no seleccionados) |
| Primera tanda específica con sandbox, versión corregida | **7 aprobados, 8 fallidos**, sin skips; conexiones loopback bloqueadas por `WinError 10013` y errores de limpieza derivados. `.data/caddy-stage4-deadline-specific.xml`; no se presenta como comprobación nativa aprobada |
| Misma tanda específica fuera del sandbox, sólo loopback autorizado | **15 aprobados, 0 fallos/skips**, 6,51 s; `.data/caddy-stage4-deadline-specific-native.xml` |
| Verificador final + regresiones de reporte, fuera del sandbox para los casos loopback | **113 aprobados, 0 fallos/errores/skips**, 16,38 s; 84 del verificador y 29 regresiones, `.data/caddy-stage4-deadline-final.xml` |
| Ruff, formato y compilación Python | **Aprobados**, 233 archivos Python; Python 3.11.9 / Ruff 0.16.8, Windows AMD64 |
| Whitespace/diff y conservación Git | **Aprobados**; revisión de los tres archivos y cambios previos conservados, sin operaciones Git de escritura |

Los 8 casos con transporte real cubren delimitador tardío, silencio, línea con
bytes periódicos y encabezado chunked incompleto, tanto por TCP loopback como TLS
loopback con confianza de prueba aislada (cryptography 50.0.2 ya disponible).
Para una ventana de fixture de **0,6 s**, retornaron en **0,609–0,610 s**, todos
con `window.end=deadline`: el caso de delimitador conserva 2 eventos y 0
heartbeats; los otros, 0/0. El emisor retenía el delimitador/silencio o seguía
fragmentando hasta 3 s; margen de assertion 0,5–1,5 s, sin ampliar plazos para
obtener verde. Los tests comprueban cierre del lector/socket y finalización del
thread emisor temporal. El verificador no crea threads para la lectura SSE.
Las propiedades del XML registran sólo alcance local, tiempos y contadores.

Los dobles deterministas comprueban además bloques completados exactamente a/en
exceso de 20 s, exclusión del heartbeat a 121 s, presupuesto restante decreciente,
EOF incompleto, timestamps cercanos al límite y resultado inconcluso ante silencio.
TLS local **no** acredita HTTPS público, ACME, Ubuntu/ARM64 ni aceptación real.
La planificación sigue sin red y `acceptance: not_approved` sigue incondicional.

Se conserva también `.data/caddy-stage4-deadline-before/` como copia previa al
cambio de implementación/documentación (el test rojo ya estaba agregado), y
toda la evidencia anterior. No se repitió el smoke largo ni la suite ordinaria;
no se ejecutaron sondas públicas, escaneos, proveedores o infraestructura.
Los timeouts históricos, recuperación `frontend-start`, Etapa 3 pendiente,
suite ordinaria y Linux/ARM64/systemd/Docker/NAT mantienen su estado anterior.
**Detenerse para revisión, sin despliegue ni nueva aceptación pública.**

#### Guardia del smoke e invocación temporal — auditoría posterior, 13/10/2026

La guardia anterior aceptaba 241 casos sin los 29 de reporte/diagnóstico porque
sumaba familias y sólo exigía diez casos proxy. Las nuevas regresiones ejecutan
`run()` con dobles explícitos de proceso, herramientas y JUnit; no ejecutan un
smoke real. Rechazan familias ausentes con total suficiente, sustituciones de
identidades manteniendo recuentos, duplicados, fallos/errores/skips aun con salida
cero y salida no cero con reporte completo. Comprueban que el reporte público
conserva los resultados rojos y que las capturas privadas no se publican.

La colección real de pytest, sin ejecutar fixtures ni tests, confirmó por módulo
las identidades completas y las cuatro huellas fijadas en el manifiesto:
**89 configuración + 142 despliegue + 43 reporte/guardia/diagnóstico + 10 proxy =
284**. Los **270** son la selección revisada previa a estas 14 regresiones; los
**241** y **227** de tablas anteriores corresponden a corridas históricas. Una
colección válida no prueba que se hayan ejecutado esos 284 casos: en esta revisión
se ejecutó únicamente la tanda acotada indicada debajo.

La invocación temporal usa una lista de argumentos de proceso, elimina
`PYTEST_ADDOPTS` heredado y pasa `--basetemp` como un único argumento con ruta
absoluta y separadores `/`. Antes de ejecutar, el parser real de pytest y
`TempPathFactory.from_config()` verifican el basetemp efectivo, absoluto, dentro
de `.data`, todavía inexistente y coincidente con el solicitado; no se llama
`getbasetemp()` durante esa comprobación. Los directorios nuevos y cortos
`.data/t48g-067f-r0` y `.data/t48g-067f-f1` son distintos entre corridas. No se
reutilizó, borró ni trasladó evidencia anterior. Esta corrección de invocación
queda en el supervisor temporal, sin cambiar fixtures ni configuración pytest.

| Verificación efectivamente ejecutada | Resultado/evidencia conservada |
| --- | --- |
| Regresiones de guardia antes de la corrección | **10 fallidos esperados, 4 aprobados, 29 no seleccionados**; `.data/g48-067f22a7/red/required-tests.xml`. Incluye aceptación indebida de 241 casos sin reporte/diagnóstico y de failure/error con salida cero |
| Colección real de los cuatro módulos, sin ejecución | **284 identidades**, huellas coincidentes; `.data/g48-067f22a7/final/collection.json` |
| Reporte/guardia/diagnóstico y dos casos de recuperación explícitos | **45 aprobados, 0 fallos/errores/skips**; 43 + 2, supervisor 5,203 s, Windows AMD64 build 26300 / Python 3.11.9; `.data/g48-067f22a7/final/required-tests.xml` |
| Guardia final, después de retirar del doble la compatibilidad temporal con la guardia antigua usada para reproducir el rojo | **14 aprobados, 29 no seleccionados**, 0,55 s; `.data/g48-067f22a7/guard-final/required-tests.xml`. No repite recuperación ni amplía la selección; esos 14 ya están incluidos en los 43 anteriores |
| `frontend-start` y `reload`, una ejecución por caso | Fallos históricos **no reproducidos**, causas pendientes; dos observaciones en `.data/g48-067f22a7/final/publication/`, ambas `no-publication-errors-observed` |
| Ruff 0.16.8, formato y whitespace | **Aprobados**: `ruff check .`, `ruff format --check .` (233 archivos), `git diff --check` y revisión `--no-index --check` de los tres archivos nuevos; `.data/g48-067f22a7/checks.json`. Exit 1 de `--no-index` corresponde a archivos agregados, sin diagnósticos de whitespace |

La publicación usa el observador existente y operaciones de archivo Windows;
servicios y metadatos POSIX siguen siendo dobles explícitos. Se conservan etapa
y categoría de destino; una excepción conservaría sólo tipo, errno/winerror,
nunca contenido ni rutas. No hubo excepción de publicación en estos dos casos.
Se preservaron las assertions, el helper, los dobles, el snapshot anterior y las
evidencias rojas de `frontend-start` y `reload`. Esta corrida no explica ni
resuelve esos fallos históricos ni acredita instalación/recuperación Ubuntu.

No se repitieron generación H1, smoke completo, suite ordinaria, sondas públicas,
escaneos o proveedores reales. No se amplió el perfilado. Siguen pendientes los
timeouts históricos, recuperación histórica, suite ordinaria incompleta,
Linux/ARM64/systemd/Docker/NAT y los tres criterios públicos. **A:** guardia y
documentación corregidas. **B:** validación local acotada, con evidencia roja y
posterior conservadas. **C:** validación nativa y aceptación pública pendientes.
Detenerse para revisión; no hay autorización de despliegue.

#### Diagnóstico de navegador — ejecución 38595531809, 14/10/2026

La ejecución sobre `7d18bd9` produjo en ambas arquitecturas **282 aprobados y
2 fallidos de 284, sin skips**. Fallaron las sondas de interacción/reconexión y
de conexiones largas, ambas con `real-browser-probe-failed`. Los artifacts
saneados carecen de observaciones del navegador porque el arnés anterior sólo
las agregaba después de una salida exitosa. Los logs publicados no identifican
la excepción del hijo ni su etapa: **la causa sigue desconocida**. No permiten
atribuirla a Caddy, WebSocket o al sandbox.

El hijo ahora escribe un diagnóstico acotado por sonda, con schema, resultado,
última etapa alcanzada, tipo de excepción permitido y código fijo de fallo. Las
etapas distinguen inicio de Playwright, lanzamiento/versionado de Chromium,
contexto/página, navegación, interacciones, reconexión, espera, auditoría privada,
cierre y escritura del resultado. No se serializan mensajes, argumentos de
excepción, URLs, rutas, páginas ni perfiles. Tipos no reconocidos se representan
como `OtherError`. Los logs existentes siguen privados y auditados; no se agregan
a los artifacts.

El padre incorpora el diagnóstico a `observations.json` antes de exigir salida
cero, conservando el fallo rojo. También distingue fallo de creación del proceso
y timeout del padre. Reportes ausentes, malformados, mayores de 4096 bytes o fuera
del esquema cerrado producen evidencia no disponible con código fijo; una salida
cero sin diagnóstico aprobado no acredita éxito. Las dos sondas usan archivos
distintos. El sandbox, las assertions funcionales, el plazo de reconexión y los
timeouts existentes se conservan.

Las regresiones del protocolo y de exportación usan dobles explícitos de
Playwright/proceso/infraestructura. Ejecutan la lógica real de reporte, espera y
exportación durante el fallo; **no reproducen Chromium ni el runner Linux**.
La guardia fija las 314 identidades revisadas, incluyendo las 30 nuevas, sin
sustituir ninguna anterior. No se repite el smoke ni la suite ordinaria para
validar este diagnóstico. Una nueva ejecución Linux requerirá autorización; no
hay cambios de seguridad, runner o producción aprobados por esta investigación.

#### Firmas de lanzamiento y cobertura en rojo — sobre HEAD 9f49186

La ejecución 38600882605 sobre ese HEAD registró en ambas arquitecturas 312
aprobados, 2 fallidos, 0 errores y 0 skips. Las dos sondas de cada arquitectura
informaron `browser_launch`, `TargetClosedError`, `BROWSER_LAUNCH_FAILED`.
Eso ubica el fallo antes de navegar, pero no demuestra su causa.

El campo adicional `launch_signature` sólo existe en fallos de `browser_launch`.
Se examina la excepción en memoria, sin exportar su texto. Se reconocen tres
categorías cerradas:

| Firma reconocida | Código exportado | Límite de interpretación |
| --- | --- | --- |
| Registro stderr nativo con `No usable sandbox!`, o reescritura exacta `Chromium sandboxing failed!` | `CHROMIUM_SANDBOX_STARTUP_FAILED` | Fallo genérico de inicio del sandbox; **no demuestra una denegación de AppArmor** |
| Registro stderr nativo con `Missing X server or $DISPLAY`, o reescritura exacta de Playwright sobre ausencia de XServer | `CHROMIUM_X_SERVER_MISSING` | Ausencia de servidor gráfico informada; no cambia `headless` ni configura uno |
| En un mismo registro stderr, `error while loading shared libraries:` y `cannot open shared object file: No such file or directory` | `CHROMIUM_SHARED_LIBRARY_LOAD_FAILED` | Mensaje de carga de biblioteca; no publica su nombre/ruta ni instala dependencias |

La procedencia de las firmas se contrastó con el código disponible del paquete
oficial fijado Playwright **1.63.0**, `driver/package/lib/coreBundle.js`:
`doRewriteStartupLog` de Chromium reconoce/reformula los mensajes de sandbox y
XServer; el lanzador incluido reconoce el error de shared object del cargador
ELF. No se efectuaron consultas externas para esta comprobación. El formato
nativo reconocido es `[pid=…][err]` de los errores de lanzamiento de Playwright,
también cuando aparece en su call log. Una cadena aislada dentro de una URL o
un canal stdout no cuenta como ese registro.

Sin firma reconocida, con categorías distintas coexistiendo, texto mayor de
65536 caracteres o excepción no convertible a texto, se conserva `unknown`.
La repetición de una misma categoría no crea una segunda causa. Textos genéricos
como `Operation not permitted` o una mención aislada de AppArmor no se clasifican.
El lector del padre acepta únicamente los códigos cerrados; nunca propaga campos
arbitrarios. El código principal sigue siendo `BROWSER_LAUNCH_FAILED`, el hijo
sigue saliendo con error y no cambian sandbox, runner, assertions ni deadlines.

El JUnit saneado ya publicado incluye ahora la propiedad **`caddy.coverage`**:
schema, total, cantidad de casos de familia inesperada y, por cada familia
obligatoria, recuentos esperado/observado/único, duplicados y SHA256
esperado/observado de las identidades completas ordenadas. No exporta identidades
parametrizadas, nombres de familias inesperadas, capturas ni mensajes privados.
También se escribe cuando hay fallos, errores, skips o cobertura incorrecta.
Una identidad de formato inválido se representa con nombre fijo en el reporte y
sigue rechazándose después de escribirlo. No se agregan artifacts ni se cambia
el workflow.

La guardia verifica cobertura **antes** de rechazar resultados rojos. El resumen
`complete: true` acredita únicamente cobertura; no aprueba tests fallidos,
errores, skips ni una salida de proceso no cero. Una sustitución de parámetro
cambia la huella aunque mantenga el total. Las 34 regresiones nuevas ejecutan
estas decisiones con dobles y canarios sintéticos, sin Chromium real ni red.
La colección revisada preserva las 314 identidades anteriores. La próxima
ejecución Linux deberá aportar la firma o mantener `unknown`; no se corrige
seguridad ni configuración del runner sin revisión de esa evidencia.

El mensaje XServer de Playwright 1.63.0 se reescribe realmente mediante
`wrapInASCIIBox(kNoXServerRunningError, 1)`. Se reconoce también ese bloque
completo de **6 líneas de 98 caracteres**, con bordes `╔═╗`, `║` y `╚═╝`,
padding y texto exactos, delimitado por líneas completas. El literal y la fixture
se contrastaron con la salida de la función original del paquete fijado,
ejecutando sólo su formatter JavaScript; no se inició Chromium. No se eliminan
bordes de frases arbitrarias para intentar clasificarlas. Fragmentos, contenido
HTML que envuelve el bloque y bloques modificados permanecen en `unknown`.
Si el bloque coexiste con otra categoría reconocida, se conserva `unknown` por
ambigüedad. Se reutiliza `CHROMIUM_X_SERVER_MISSING`, sin categorías nuevas ni
cambios de sandbox, runner, assertions o deadlines.

</details>
