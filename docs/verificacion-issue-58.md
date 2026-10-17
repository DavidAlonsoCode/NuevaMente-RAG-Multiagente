# Issue 58 — Mapa de fuentes, evidencias y pendientes

## Alcance de esta revisión

Registro histórico de la revisión documental del **16/10/2026**: se conservan el mapa de fuentes/evidencias
y las correcciones de la **etapa 1**, la instalación de **etapa 2**, la
**etapa 3** de [operación, backups, recuperación y diagnóstico](despliegue-oci.md#13-actualización-con-interrupción-breve-y-versiones-conservadas)
y la **etapa 4** de validación local y preparación para auditoría final.
Los procedimientos reales están redactados, sin ejecutarlos. Sólo se ensayaron
copias/restauraciones de datos sintéticos aislados, detalladas al final. No es un
ensayo de despliegue ni el cierre de #58. Los resultados de etapa 4 y los
impedimentos de aceptación se registran al final; no hay aprobación humana.

Base de código inspeccionada en esa revisión histórica:
[`f2924151302001141bec65aadd081d3247e485c4`](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/tree/f2924151302001141bec65aadd081d3247e485c4).
Antes de editar, `HEAD`, `develop` y `origin/develop` locales coincidían en esa
revisión, sin divergencia; `git ls-remote origin refs/heads/develop` confirmó la
misma referencia remota. Branch activa:
`feature/issue-58-oci-deployment-guide`. Working tree inicial limpio.
En ese momento los cambios documentales de las cuatro etapas quedaban locales, sin commit. Al comenzar
la etapa 2 se conservaron los cinco documentos modificados y esta matriz de la
etapa 1; se volvió a comprobar que rama y referencias locales/remota de `develop`
seguían en `f292415`. El diff previo no tenía errores de whitespace.
Antes de etapa 3 se comprobaron de nuevo rama, diff y referencias locales/remota:
seguían en el mismo commit, sin divergencia. Se conservaron los cinco documentos
modificados y los dos nuevos de etapas anteriores, sin cambios ajenos detectados.

La base operativa del runbook pasó a `5988244` tras el merge local del 17/10.
El [registro separado de integración y contraste documental](#integración-local-y-contraste-documental--17102026)
identifica commits, cambios y límites. Los resultados del 16/10 de las secciones
siguientes no son nuevas pruebas de la base integrada.

Se consultaron en GitHub los cuerpos vigentes de #47–#50, #58 y #133.
#47–#50 figuran cerrados; #58 y #133, abiertos. Las actualizaciones de #58 y
#133 consultadas son del 16/10/2026, `14:12:58Z` y `14:15:28Z`, respectivamente.
El estado de un issue no sustituye evidencia técnica ni revisión humana.

Fuentes de alcance: [#58](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/58),
[#133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133),
[decisiones §9 y §14](../decisiones_proyecto.md),
[ajuste del plan del 16/10](plan-implementacion.md),
[fase 5](issues/fase-5-despliegue-endurecimiento.md) y
[fase 6](issues/fase-6-demo-entrega.md).
Los planes y decisiones se conservan sin cambios; la contradicción funcional
detectada se registra abajo para decisión del equipo. #58 depende de #48/#49/#50
y entrega su guía al sucesor [#57](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/57),
que conserva sus criterios de README e instalación por terceros.

## Matriz de requisitos y fuentes

Los archivos de implementación enlazados se inspeccionaron en la base `f292415`
indicada arriba. «Implementación observada» significa lectura de código, no
ejecución. «Evidencia existente» remite a registros anteriores con su versión y
alcance propios; no se presentan como verificaciones nuevas de #58.
La existencia de un test tampoco equivale a haberlo ejecutado en esta etapa.
Los pendientes de documentación de instalación de la matriz inicial se desarrollan
ahora en el runbook de etapa 2: secciones 1–3 (operador/costos/VM/host), 4–7
(imágenes/Caddy/secretos/volumen), 8–11 (red/peer/precarga/activación) y 12
(salud/bibliotecas). Su contraste de código/sintaxis no acredita ejecución local
ni pública. La etapa 3 agrega secciones 13–19 (actualización/reinicio, monitoreo,
backup/restauración, escenarios, antigüedad y diagnóstico), con los límites y
ensayos sintéticos detallados al final. La columna pendiente de la matriz inicial
conserva sus requisitos: la redacción satisface su parte documental, no acredita
su ejecución. Las evidencias históricas no cambian de fecha ni alcance.

| Requisito | Fuente / versionado | Implementación observada | Evidencia existente | Verificaciones pendientes |
| --- | --- | --- | --- | --- |
| VM A1 Always Free, Ubuntu 24.04 ARM64 y capacidad | Decisiones §9.1; #47; [receta OCI](../infra/oci/README.md), código base `f292415` | [Aprovisionador](../infra/oci/provision_vm.py): 2 OCPU/12 GB, home region, boot 50 GB, inventario de discos/A1, guardas y diario idempotente; no cambia a una VM paga | Consultas OCI de 8 y 12/10 con `OUT_OF_HOST_CAPACITY`; registro del 12/10 refiere sincronización con `370df46`. No acredita VM; los reportes `.data` citados son locales, fuera de Git | #58: consolidar prerrequisitos y bloqueos. #133: capacidad efectiva, provisión, gratuidad, OS/arquitectura y evidencia identificada |
| SSH, firewall y puertos | Decisiones §9.2; #47/#48; código base `f292415` | [Bootstrap](../infra/oci/bootstrap.sh), [verificación de host](../infra/oci/verify_host.sh) y [listeners](../infra/oci/verify_listeners.py): SSH por clave y /32 administrativo; reglas host/Docker; 80/443 públicos y app en loopback | Tests existentes en [test_compute_provision.py](../backend/tests/test_compute_provision.py); no constituyen prueba desde otra red | #58: documentar controles y resultados esperados. #133: reglas efectivas OCI/host, listeners, SSH desde IP autorizada/no autorizada y puertos internos |
| Docker, Compose e imágenes ARM64 | Decisiones §14; [Compose base](../docker-compose.yml), [overlay OCI](../docker-compose.oci.yml) y [producción](../docker-compose.production.yml) | Dos servicios; un worker/escritor; reinicio y healthchecks. [Backend](../backend/Dockerfile) Python; [frontend](../frontend/Dockerfile) con build Node y servidor final Python. Producción usa los tres Compose, en ese orden | CI histórica de contenedores referenciada más abajo; Compose local usa mocks y ARM64 se verifica en runners, no en OCI | #58: consolidar build, identidad de imágenes y actualización. #133: funcionamiento en VM y persistencia tras reinicio |
| UI vigente y progreso SSE | Ajuste del plan del 15–16/10; PR #135/#136; código base `f292415` | [React](../frontend/src/App.tsx), [servidor estático](../frontend/entrypoint.py); SSE autenticado. En esa base permanecía el [Streamlit histórico](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/tree/f2924151302001141bec65aadd081d3247e485c4/frontend_streamlit_viejo), retirado posteriormente en #139 | [Verificación React](verificacion-frontend-react.md), 15/10, y [#33](verificacion-issue-33.md), 16/10: proveedores simulados, versiones y limitaciones declaradas | #58: usar instrucciones React. #133: generación real desde React, proxy, cancelación, reconexión y recuperación sin duplicar |
| Variables, secretos y separación operador/backend | Decisiones §9.2/§14; [template backend](../.env.example), [interpolación productiva](../.env.production.example); código base `f292415` | [Helper Caddy](../infra/caddy/verify_deployment.py): archivos canónicos, permisos, perfil técnico `DOCKER`, API key montada RO; secreto de origen separado. El operador de provisión usa credenciales diferentes | Validación con dobles en [test_caddy_deploy.py](../backend/tests/test_caddy_deploy.py); configuración/crypto offline y evidencia histórica de proveedor referida por #133 | #58: documentar archivos/valores sin secretos. #133: identidad/IAM efectivos y configuración segura de la VM. El soporte genérico de instance principal no lo convierte en alternativa admitida por este helper |
| Caddy, DNS, HTTPS y origen confiable | #48; [referencia Caddy](../infra/caddy/README.md); código base `f292415` | [Instalación](../infra/caddy/install.sh), [deploy](../infra/caddy/deploy.sh), [Caddyfile](../infra/caddy/Caddyfile): firmas/hashes, observación del peer, validación y activación; DNS no se configura automáticamente | Caddy/TLS reales en CI con proveedores dobles; registro de `42af012` y corridas posteriores separados abajo | #58: consolidar preparación y diagnóstico. #133: systemd/notify, Docker/NAT/peer, DNS/ACME público, HTTPS válido y exposición efectiva |
| Precarga de tres fuentes demo | #49; [precarga](demo-precarga.md); código base `f292415` | [CLI](../backend/app/documents/preload_cli.py) y [precargador](../backend/app/documents/preload.py): colección separada, hash, cobertura/visión, exclusividad, publicación condicional; `--check` no usa proveedores | [Tests de precarga](../backend/tests/test_demo_preload.py) y [catálogo documental](../backend/tests/test_demo_documents.py) con dobles; no ejecutados aquí | #58: procedimiento productivo y reactivación aún por consolidar. #133: activar y consultar tres fuentes vigentes en VM/URL pública |
| Generar desde una fuente demo sin subir archivo | #49; fase 5, alcance de integración del 16/10; código base `f292415` | UI y autorización del backend revisadas **no habilitan** el recorrido; detalle y referencias inmutables en la sección siguiente | La frase de fase 5 afirma puente probado; no se encontró soporte para esa afirmación en el recorrido inspeccionado. Precarga, carga privada y ejemplos guardados no lo acreditan | Decisión y seguimiento funcional del equipo pendientes. Se conserva el requisito; no se declara implementado, probado ni sustituido. Su prueba real pública continúa requerida en #133 |
| Biblioteca de resultados guardados | #55; [guía de ejemplos](ejemplos-demo.md); código base `f292415` | [Preparación](../backend/app/demo/prepare.py) y [publicación](../backend/app/demo/publish.py) de catálogo/copies independientes de sesiones privadas | #55 v2, 15/10, commit `aaed384`: ocho aprobados, fuentes/citas/quiz/imagen y aislamiento; 101 solicitudes OCI de lectura y 0 LLM durante esa lectura | #133: recuperar biblioteca desde React público y sesión independiente. No repetir generación ni publicar de nuevo por esta revisión documental |
| Costos y gobernanza | Decisiones §8.4; [gobernanza](gobernanza-costos.md), [.env.example](../.env.example), código base `f292415` | [Presupuesto](../backend/app/storage/oci_budget.py): hasta 1 GB/20.000 solicitudes, reserva 10% de solicitudes y 1% de bytes, alerta 80%; cinco documentos/veinte generaciones por espacio | #50, 12/10: inspección real del bucket y regla Budgets, con límite histórico 5.000/500; ajuste autorizado a 20.000 el 15/10. Tests [presupuestos](../backend/tests/test_budgets.py) y [API](../backend/tests/test_budgets_api.py) existentes | #58: checklist de consola/consumo agregado; no confundir presupuesto propio con franquicia Oracle. #133: asignación efectiva/$0, consumo adicional, reservas y ledger preservados |
| Retención, revocación y limpieza durable | Decisiones §8.5; #50; código base `f292415` | [Ciclo de espacios](../backend/app/session/lifecycle.py), [manifiestos](../backend/app/session/manifests.py) y [efectos documentales](../backend/app/documents/lifecycle.py): revocación, lápidas, obligaciones y reintentos | [Recuperación de espacios](../backend/tests/test_workspace_recovery.py) y [limpieza recuperada](../backend/tests/test_document_recovery_cleanup.py) con dobles | #58: documentar observabilidad y conservación del volumen. #133: verificar reinicio/recuperación con obligaciones pendientes sin reabrir acceso |
| Recuperación del índice y estado operativo | Decisiones §9.3/§14.2; [ciclo documental](../backend/app/documents/README.md); código base `f292415` | [Restauración](../backend/app/session/recovery.py), H02 en lifespan/efectos y [funciones de índice](../backend/app/core/rag/rebuild.py); precarga específica para demo | Tests existentes de [recuperación documental](../backend/tests/test_document_recovery.py), [espacios](../backend/tests/test_workspace_recovery.py) y [vector store](../backend/tests/test_vectorstore.py), con dobles | #58: distinguir backup completo y reconstrucción limitada; no inventar CLI ni restauración de contadores. #133: reproducción real de persistencia/recuperación |
| Mantenimiento, backups y rollback | #58; referencia Caddy, código base `f292415`; runbook local de etapa 3 | Helper exige imágenes previas, volumen aprobado y journal; `--recover` restaura configuración/modelo tras interrupción, no datos. No hay automatización de backup consolidada en el código; la receta manual está en secciones 16–18 | Tests de transacción/recuperación en `test_caddy_deploy.py`, con dobles; ensayo físico/SQLite sintético de etapa 3 detallado abajo, sin VM ni Chroma real | #58: revisión general de etapa 4 y humana; resolver límites operativos 17.1/17.4 con el equipo. #133: reproducción real. Custodia, frecuencia y retención requieren decisión operativa |
| Revisión independiente de la guía | #58 vigente y ajuste del 16/10 | Esta matriz es revisión automática documental; no hay constancia de revisor humano de la guía completa | Ninguna revisión humana de #58 registrada por esta etapa | #58: integrante distinto del autor, identificación, fecha, versión y observaciones resueltas. #133: reproducción desde cero por integrante distinto del autor |

## Contradicción demo-generación

En la base inspeccionada, [fase 5, línea 184](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f2924151302001141bec65aadd081d3247e485c4/docs/issues/fase-5-despliegue-endurecimiento.md#L184)
afirma que #49 conserva «precarga, catálogo y puente de generación probados
localmente». El mismo plan conserva el requisito de generar desde la app pública
sin subir nada. Esa afirmación de implementación contradice el recorrido observado:

- [React, `docSeleccionado`, línea 346](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f2924151302001141bec65aadd081d3247e485c4/frontend/src/App.tsx#L346)
  devuelve `null` para `docActivoDemo`.
- [Botón de generación, línea 678](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f2924151302001141bec65aadd081d3247e485c4/frontend/src/App.tsx#L678)
  queda deshabilitado sin `docSeleccionado`. La UI muestra además el
  [mensaje de integración pendiente](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f2924151302001141bec65aadd081d3247e485c4/frontend/src/i18n/es.ts#L50).
- [Backend, `ServicioGeneraciones.generar`, línea 244](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f2924151302001141bec65aadd081d3247e485c4/backend/app/generations/service.py#L244)
  exige `self.consulta._listo(token, solicitud.document_id)`.
- [`ServicioConsultaDocumental._listo`, línea 201](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f2924151302001141bec65aadd081d3247e485c4/backend/app/documents/query.py#L201)
  llama a `_propio`, cuya [comprobación de workspace, línea 67](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/f2924151302001141bec65aadd081d3247e485c4/backend/app/documents/query.py#L67)
  rechaza un documento ajeno al espacio de la sesión.

Conclusión limitada a esta inspección: no corresponde presentar el puente como
habilitado o probado. La selección/lectura demo, subir el original a un espacio
privado y recuperar ejemplos guardados son recorridos distintos; ninguno
sustituye «seleccionar demo → generar sin subir archivo».

**Pendiente del equipo:** determinar la causa de la discrepancia, responsable y
seguimiento funcional explícito, y registrar una decisión trazable antes de
acreditar el puente. No se creó/modificó un issue, no se alteraron planes ni
contratos y no se implementó el puente. El requisito sigue completo. Centralizar
su prueba con proveedores reales en #133 no demuestra implementación ni sustituye
las pruebas locales faltantes de ese recorrido.

## Evidencias históricas y sus límites

| Registro | Fecha y versión propias | Qué acredita / qué no acredita |
| --- | --- | --- |
| [Consultas de capacidad #47](../infra/oci/README.md) | 8 y 12/10/2026; el registro del 12/10 refiere `370df46`; no se atribuye un commit no registrado a la consulta del 8/10 | Bloqueo de capacidad observado entonces; no acredita capacidad actual, creación ni acceso a una VM. Los reportes locales citados no son artefactos versionados |
| [Caddy: evidencia Linux de `42af012`](../infra/caddy/README.md#evidencia-linux-de-42af012) | Revisión del 14/10/2026; HEAD `42af012dfb0ef23c062888e9f3179104eacee917`, merge temporal `cf48f9702fbeca297ec5aa7ce532b096a468c5c9` | Caddy 2.11.7 y Compose 2.40.3 en AMD64/ARM64, aplicación/Streamlit y proveedores dobles; no React vigente ni aceptación OCI. Se conservan recuentos y fallos históricos en la fuente |
| [Gobernanza #50](evidencia-oci-gobernanza.md) | 12/10/2026; el registro no declara commit de ejecución | Inspección OCI real y regla Budgets; límites de 5.000 solicitudes/500 reservadas de esa fecha. No acredita límites/consumo actuales ni VM |
| [Smoke #54](evidencia/issue-54/README.md) y [reporte](evidencia/issue-54/informe.json) | 15/10/2026, `03:02:25Z`; revisión `2062ef6bc325065794e0d0d455c0de5e5507d91a`; hash del comprobador en el reporte | Reutilización de generación histórica A, original/canónico/exportaciones, 86 lecturas OCI y 0 Gemini nuevas; no VCN v2, VM pública, revisión de dos integrantes ni importación manual en Anki |
| [Ejemplos #55 v2](evidencia/issue-55/v2/README.md) y [reporte](evidencia/issue-55/v2/informe.json) | 15/10/2026, `16:54:45Z`; commit `aaed384b77361465d70ab71a6cd0b1a39d5e68a7`, fuentes/hashes en reporte y catálogo | Ocho materiales reales, A/B/C con mismo ID/hash VCN v2, recuperación desde otra sesión, citas/quiz/imagen/aislamiento; 101 solicitudes OCI y 0 LLM durante la lectura. No URL pública ni puente desde IDs demo |
| [React](verificacion-frontend-react.md) y [SSE #33](verificacion-issue-33.md) | 15 y 16/10/2026; cada registro declara su base/rama y corridas | Pruebas con dobles y sus límites explícitos; no Gemini/OCI reales ni URL de entrega. No se atribuyen todas las pruebas históricas a `f292415` |
| CI consultada durante el análisis previo de #58 | PR #136, commit `7f0fdc3fab3b797c7cfaf360191f16fb31321daf`, integrado en `f292415`; [CI](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/actions/runs/38912658572), [Caddy](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/actions/runs/38912658599), [Contenedores](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/actions/runs/38912658560), consultadas el 16/10 | Tres workflows finalizados con éxito. No se relanzaron; no se atribuye una nueva corrida local ni despliegue a esta etapa |

#133 conserva también comprobaciones reales de #14/#19/#30/#37 marcadas como
trazadas. Se respeta ese registro vigente; esta etapa no las repite ni las
convierte en prueba de Compute, HTTPS público o reproducción de #58.

## Recuperación y estado durable

El volumen contiene SQLite operativo, Chroma, ledger OCI y obligaciones locales.
Recrear contenedores conservándolo no equivale a perder el disco ni a restaurar
un backup. El helper de Caddy invalida la observación del peer después de
restart/reboot y exige reactivación administrativa; `--recover` atiende un journal
pendiente y no revierte datos ni efectos externos.

La restauración desde manifiestos OCI y H02 recupera recursos confirmados y
reconstruye fuentes bajo sus verificaciones. No restaura sesiones, jobs/eventos
históricos, claves de idempotencia, consumo histórico perdido ni mutaciones que
nunca llegaron a OCI. Las lápidas y revocaciones sólo locales requieren conservar
el volumen o su respaldo; un ledger nuevo no representa consumo mensual cero.

Existen funciones de reconstrucción de índice y el precargador demo, pero no
se documenta una CLI inventada para invocarlas. Las secciones 13–19 del runbook
documentan backup, restauración del estado durable, actualización y diagnóstico.
La copia/restauración ensayada usa únicamente datos sintéticos; ningún volumen
del proyecto se respaldó ni restauró. Persisten límites funcionales: sin backend
existente, el helper no adopta un `current.json` huérfano; perder sólo Chroma no
dispara H02 si SQLite ya confirmó recuperación. Requieren seguimiento del equipo,
sin borrar estado ni inventar una CLI para eludirlos.

## Separación de aceptación

| Grupo | Estado y evidencia exigida |
| --- | --- |
| A — Documentación y comprobaciones locales de #58 | Etapas anteriores conservadas; etapa 4 registra lint, pruebas dirigidas con dobles y evidencia sintética reproducible. No se ejecutaron procedimientos reales. La suficiencia de recuperación tiene impedimentos propios de #58 en 17.1/17.4; resultados y límites abajo |
| B — Revisión humana propia de #58 | **Pendiente**: integrante distinto del autor, identificación, fecha, versión de la guía completa y observaciones resueltas. Esta revisión automática no la sustituye |
| C — Reproducción real en #133 | **Pendiente**: runbook desde cero en la VM de entrega con integrante distinto del autor, Always Free, persistencia/recuperación y evidencia externa verificable. No exige otra VM ni autoriza destruir volúmenes. #133 sigue abierto |

## Comprobaciones de la etapa 1

Se realizaron inspecciones de Git, consulta de la referencia remota de `develop`,
lectura de issues y contraste de código/documentación. No se ejecutaron comandos
de los procedimientos, tests de aplicación, builds, proveedores reales ni
operaciones de infraestructura. No hubo escrituras en GitHub ni cambios de rama.

Comprobaciones documentales ejecutadas el 16/10/2026:

- Revisión automática de los seis documentos: **81 enlaces locales** y
  **12 anclas** válidos; sin destinos locales inexistentes.
- **Siete referencias de código inmutables** verificadas contra el commit base,
  incluida la correspondencia entre líneas y condiciones de la contradicción.
- **21 bloques de comandos existentes sin cambios**, comparados con `HEAD`.
- **Seis secciones históricas sin cambios**, comparadas con `HEAD`: cuatro
  bloques plegados de Caddy, su evidencia Linux de `42af012` y las reconsultas
  OCI del 8 y 12/10. El relato de la primera consulta del 8/10 también se conserva,
  con una nota que delimita su estado histórico.
- Revisión del diff y consistencia de alcance, React/Streamlit, presupuestos
  actuales/históricos y aceptación #58/#133; `git diff --check` sin errores.
  El archivo nuevo, todavía no rastreado por Git, se comprobó además por separado
  para espacios finales y fin de archivo.

Los enlaces externos se conservan como referencias; esta comprobación local de
enlaces no es una prueba de disponibilidad de servicios ni de la URL de entrega.
No se ejecutaron Ruff ni la suite funcional: esta etapa documental no declara
terminado el issue y no avanza a la validación de procedimientos de las etapas
posteriores. Siguen pendientes la guía completa, su revisión humana y la
reproducción real en #133. El diff queda preparado para revisión del usuario.

## Etapa 2 — Instalación redactada y límites

El runbook agrega, sin ejecutarlos, procedimientos con máquina/shell,
prerrequisitos, comandos, resultado esperado, consumo posible y condición de
parada. Los bloques Python embebidos son instrucciones documentadas ejecutables
con el intérprete existente, no nuevos scripts o CLIs del repositorio.

Decisiones contrastadas con la implementación:

- Fuente desplegada mediante `git archive` de un commit explícito y SHA256 del
  tar; no se cambia ninguna rama ni se extrae encima de un checkout existente.
- Creación de volumen limitada a una instalación realmente nueva y raíz vacía.
  Un volumen existente sólo se inspecciona/aprueba; no se reemplaza ni recibe
  cambios recursivos de permisos. El helper exige identidad aprobada y un escritor.
- Caddy 2.11.7/Cosign 2.6.5 y archivos/permisos canónicos según el helper vigente;
  credenciales administrativas del operador separadas de las técnicas del backend.
- La observación inicial crea la red productiva, sin importar la aplicación;
  validación offline antes de precargar. La precarga usa imagen ARM64 por ID,
  `backend.env` + `origin.env`, credenciales/documentos RO y bind del volumen
  inspeccionado; no usa el Compose base de desarrollo ni crea volumen por copy-up.
- API/UI detenidas durante la precarga; `runtime.lock` y lock del despliegue
  conservados. Después se exige activación/reactivación por el helper; no se
  presupone que un `compose up` o restart restaure la confianza del peer.
- Fuentes demo, lectura de ejemplos guardados y generación nueva se comprueban
  por separado. La contradicción demo→generación permanece intacta y pendiente
  de decisión/seguimiento funcional del equipo; no se implementa ni recorta.

Pendientes operativos explícitos: exportación nativa/revisión completa de red a
cargo del administrador (el repo no la automatiza), disponibilidad A1, dominio y
cuotas reales, custodia de credenciales, prueba de volumen/permisos en Linux y
reactivación nativa después de precarga. No se certifica una VM, HTTPS, disponibilidad
actual de artefactos externos ni consumo efectivo. La revisión humana de la guía
y reproducción en #133 siguen sin constancia nueva.

Comprobaciones documentales de etapa 2, 16/10/2026:

- Lectura de `provision_vm.py`, bootstrap/verificador de host, los tres Compose,
  Dockerfiles, templates env, helper Caddy, CLI de precarga, esquemas y rutas API.
  Contraste de flags, paths/permisos, tags de imágenes, nombre de red/volumen,
  archivo de aprobación y condiciones de preflight/activación.
- Revisión de enlaces de los siete documentos: **94 destinos locales** y
  **15 anclas**, sin errores; siete referencias inmutables conservadas.
- Análisis sintáctico sin ejecución: **21 bloques Bash** con `bash -n`, **nueve
  PowerShell** con el parser de PowerShell, **cuatro fragmentos Python de heredoc**
  y el fragmento inline de lectura SQLite con AST; ejemplos JSON e INI parseados.
  Git Bash no pudo iniciarse dentro del sandbox Windows (`0xC0000022`); el análisis
  se repitió fuera del sandbox exclusivamente con `-n`, sin ejecutar los comandos.
- Comparación con el estado al iniciar etapa 2: los bloques de comandos de los
  cinco documentos de etapa 1 se conservan; `docs/gobernanza-costos.md` permanece
  idéntico. En cuatro documentos relacionados sólo se agregan enlaces mínimos y
  se sustituye la mención de precarga productiva pendiente por su receta redactada.
- `git diff --check` y revisión separada de whitespace/fin de archivo en los dos
  documentos nuevos, todavía no rastreados; revisión del diff limitado a Markdown.

No se ejecutaron builds, instalaciones, procedimientos, tests de aplicación ni
Ruff; no se importaron módulos de aplicación para validar los ejemplos. No hubo
proveedores reales, infraestructura, cambios de ramas, commits ni escrituras en
GitHub. La sintaxis y las referencias correctas no prueban comportamiento real,
latencia, permisos POSIX, DNS/TLS ni reactivación nativa. Se entrega el diff para
revisión y se detiene el trabajo antes de la etapa 3.

## Etapa 3 — Operación redactada y ensayos sintéticos

Registro del **16/10/2026**, base inspeccionada `f292415` indicada arriba, con
documentación local todavía sin commit. No se atribuyen estos resultados a la
CI histórica ni a una versión desplegada. La etapa 2 se conserva; en sus secciones
4 y 11 sólo se actualizan referencias a la operación antes pendiente.

| Requisito de etapa 3 | Fuente / versión contrastada | Redacción y evidencia nueva | Pendiente / límite |
| --- | --- | --- | --- |
| Actualización y rollback | `verify_deployment.py`: `freeze_model`, `deploy`, `transaction`, `restore_transaction`; `store.py`: esquema 11, base `f292415` | Sección 13: fuente/IDs anteriores, tags de conservación, espacio y compatibilidad, parada y activación mediante helper. Sin ejecutar builds ni helper | Compatibilidad real de la versión a desplegar; rollback de datos separado. No se acredita reversión de migraciones ni efectos externos |
| Reinicio y monitoreo | Bootstrap, healthchecks, fingerprint/gate, ledger y tablas de limpieza, base `f292415` | Secciones 14–15: reactivación administrativa, disco/servicios/TLS, contadores y obligaciones mediante lecturas SQLite sin importar la aplicación | Umbrales/responsable pendientes; permisos, servicios y peer nativos sin ensayar. Un health verde no certifica cuota ni limpieza |
| Backup y restauración | Layout durable observado; SQLite y funciones documentadas de 16.2/16.3 | Copia física del conjunto detenido, inventario/hash/versión/propietarios/modos, WAL/SHM, restauración aislada y custodia externa separada de secretos; ensayos abajo | Frecuencia, retención, custodio, cifrado y destino externo requieren decisión. Sin copia real, sin validación POSIX ni respaldo OCI creado |
| Escenarios y diagnóstico | Helper, `session/recovery.py`, H02, `rag/rebuild.py`, precargador, base `f292415` | Secciones 17–19: contenedores, journal, VM con backup, índice y pérdida total; límites de antigüedad/consumo/revocación | Huecos 17.1/17.4 requieren seguimiento del equipo. No hay CLI universal ni recuperación de cambios nunca sincronizados/respaldados |

Los bloques de copia/restauración son instrucciones Python estándar dentro del
runbook, no scripts nuevos del repo. Para el ensayo se extrajeron por AST sólo
`crear_copia`, `restaurar_copia` y `verificar_sqlite_restaurado`; **no** se ejecutaron
sus cuerpos operativos externos (Docker, rutas de la VM, aprobación de volumen,
lock administrativo). Tampoco se importó código de la aplicación. Se usó Python
**3.11.9**, SQLite **3.45.1**, Windows **10.0.26300**, arquitectura AMD64.

Datos exclusivos del ensayo: tres bases artificiales, un segmento binario Chroma
ficticio, catálogo/mantenimiento ficticios, lock y temporal. Un subproceso SQLite
local confirmó una transacción y terminó sin checkpoint de cierre, dejando WAL y
SHM sin escritor vivo. Ninguna credencial, volumen, servicio o base del proyecto
fue abierta. Los destinos se crearon con `tempfile.mkdtemp`, fuera del repo, bajo
el temporal aislado del sandbox; marcador `.synthetic-only`, directorio
`nuevamente-issue58-sintetico-rkljdalo`. Su `resultado.json` y fixtures no están
versionados ni se presentan como evidencia externa permanente.

| Ensayo realmente ejecutado | Resultado y alcance exactos |
| --- | --- |
| Copia completa con escritores terminados | **PASS**: 10 archivos, incluidos WAL de 4.152 bytes, SHM, ledger, SQLite/segmento Chroma ficticios, lock y temporal. Hashes de origen iguales antes/después; inventario completo |
| Restauración física | **PASS**: 10 hashes iguales al origen antes de abrir SQLite; destino nuevo. No se comprobó dueño/grupo/modo POSIX en Windows |
| Lectura SQLite con WAL | **PASS**: `integrity_check=ok` y `foreign_key_check` vacío en tres bases sintéticas; esquema ficticio 11 en operativo; conserva los dos registros, incluido el commit sólo presente en WAL |
| Copia sólo de base principal | **PASS del control negativo**: SQLite informa integridad correcta, pero devuelve sólo uno de los dos registros. Demuestra por qué la integridad aislada no prueba completitud |
| Tar alterado | **PASS**: rechazo por checksum antes de extraer archivos |
| Hash de archivo alterado en inventario | **PASS**: rechazo antes de extraer archivos |
| WAL omitido del tar, conservando inventario y recalculando checksum global | **PASS**: rechazo por conjunto de miembros incompleto antes de extraer |
| Ruta `../fuera.txt` en archivo sintético | **PASS**: rechazo antes de extraer; no se creó archivo fuera del destino |
| Miembro tar de tipo symlink | **PASS**: rechazo antes de extraer; no se creó el enlace. No prueba todos los casos de filesystem ni una auditoría de seguridad completa |
| Destino ocupado | **PASS**: rechazo; archivo sentinela previo intacto y sin elementos añadidos |
| Snapshot anterior a cambios posteriores | **PASS**: restauró dos registros y cero revocaciones frente al registro/revocación agregados después; ledger restaurado 1.234 frente a 1.434 posteriores. Ni restituye esos cambios ni implica consumo mensual cero |

Estos **11 casos** comprueban las funciones físicas y SQLite sobre fixtures
artificiales. No validan el motor Chroma, embeddings, apertura de bases reales,
consistencia funcional entre proveedores, POSIX/ACL/xattrs/UID/GID, `flock`,
systemd/Docker, VM ARM64, DNS/ACME ni recuperación pública. No se dispone aquí
de una VM Linux ARM64 aislada autorizada para esos ensayos; no se instala una
ni se sustituye esa ausencia con la prueba Windows.

Se deja expresamente pendiente:

- La reproducción real de los escenarios de persistencia/recuperación de #133.
- La decisión/seguimiento funcional del puente demo→generación ya registrada,
  sin implementar un reemplazo ni alterar el requisito.
- La integración administrativa segura para índice privado perdido con SQLite
  conservada y el estado huérfano descrito en 17.1. La lectura de las funciones
  existentes no equivale a un procedimiento admitido ni probado.
- La revisión humana independiente de #58 y la validación general de etapa 4.

Comprobaciones documentales de esta etapa:

- Lectura de implementación de despliegue/recuperación, migraciones, ledger,
  obligaciones y reconstrucción; contraste de comandos, flags, rutas y límites.
- Análisis sintáctico sin ejecución de **39 bloques Bash**, **10 PowerShell**,
  **ocho heredocs Python**, **dos fragmentos Python inline**, un JSON y un INI.
  Git Bash volvió a fallar dentro del sandbox Windows (`0xC0000022`); se repitió
  sólo el parser `bash --noprofile --norc -n` fuera del sandbox, sin ejecutar
  los procedimientos. Python se comprobó con AST y PowerShell con su parser.
- Revisión de los siete documentos: **97 destinos locales** y **17 anclas**,
  sin destinos inexistentes ni anclas inválidas. Los enlaces externos no se
  probaron como disponibilidad.
- Comparación SHA256 con el inicio de etapa 3: OCI/Caddy README, demo y Docker
  permanecen idénticos; gobernanza sólo reemplaza la mención de procedimiento
  pendiente por el enlace a su receta y límites. Las secciones 1–12 del runbook
  conservan íntegro su texto, descontadas las dos referencias actualizadas;
  sus comandos no cambian. No se modificaron decisiones, planes ni código.
- `git diff --check` sin errores y revisión separada de whitespace/fin de archivo
  en ambos documentos todavía no rastreados. Rama y referencias locales de
  `develop` conservan `f292415`, 0 commits de divergencia. La referencia remota
  se comprobó al comenzar la etapa; no se modificó ninguna referencia Git.

No se ejecutaron instalaciones, builds, aplicación, proveedores ni infraestructura;
no se relanzaron tests funcionales, Ruff o CI, que no corresponden a estos ensayos
ni al cierre de la etapa 4. No hubo commits, push, cambios de rama o escrituras en
GitHub. Se entrega el diff para revisión, sin avanzar a la etapa 4.

## Etapa 4 — Selección previa de validación

Selección registrada antes de ejecutar, **16/10/2026**, sobre `f292415` y los
cambios documentales locales de etapas 1–3. Rama y referencias locales sin
divergencia; `git ls-remote origin refs/heads/develop` confirma el mismo SHA remoto
sin actualizar refs. Los siete documentos previos se conservan para revisión.

Se seleccionan estos módulos existentes bajo `backend/tests/`:

| Módulo | Marker observado / aislamiento revisado |
| --- | --- |
| `test_compute_provision.py` | `unit`; clientes OCI/configuración reemplazados por dobles |
| `test_caddy_config.py` | `unit`; adapt/config con herramientas nativas si existen, env sintético; guards Python sin systemd |
| `test_caddy_deploy.py` | `unit`; operaciones de sistema/transacciones con dobles, algunos modelos requieren Caddy/Compose |
| `test_demo_preload.py` | `integration_mock`; `DobleGemini`, almacenamiento local y volumen temporal |
| `test_demo_documents.py` | `integration_mock`; configuración explícita mock, ASGI/TestClient y fuentes de prueba |
| `test_budgets.py` | `unit`; ledger/SQLite y almacenamiento local temporales |
| `test_budgets_api.py` | `integration_mock`; fixture de generación con los tres proveedores Gemini dobles y almacenamiento local |
| `test_document_recovery.py` | `integration_mock`; registro temporal, `mock_storage` y embeddings dobles |
| `test_document_recovery_cleanup.py` | `integration_mock`; manifiestos/obligaciones sobre almacenamiento y registro temporales |
| `test_workspace_recovery.py` | `integration_mock`; `LocalMockStorageProvider` y registro temporal |

Filtro explícito: `(unit or integration_mock) and not integration_real`.
No se incluyen `backend/tests/integration/`, `caddy_proxy`, `e2e` ni ninguna
selección real. Se revisaron `pytest.ini`, conftest raíz/backend y fixtures
compartidos antes de ejecutar. Conftest fuerza mocks y deshabilita lectura de
`.env` para la suite ordinaria; además se usará entorno del proceso saneado,
`MOCK_OCI=1`, `MOCK_GEMINI=1`, `APP_ENV=test` y temporales nuevos. No se instala
ninguna dependencia. Herramientas disponibles en `.venv`: Python 3.11.9,
pytest 9.1.1, Ruff 0.16.8, OCI SDK 2.187.2 y Chroma 1.5.9.
Caddy/Compose/Docker no están en PATH; Linux/ARM64 no están disponibles para
ejecución nativa. Las omisiones se registrarán separadas de los resultados.

## Resultados ejecutados de etapa 4

Fecha **16/10/2026**, base `f292415`, documentación sin commit. Se conserva la
distinción entre intento fallido del arnés, corrida completa y repetición aislada;
no se suman sus recuentos como identidades diferentes ni se reemplaza un fallo
de la corrida completa por un éxito posterior.

| Ejecución | Resultado real |
| --- | --- |
| `.venv/Scripts/python.exe -B -m ruff check .` | Código 0, `All checks passed!`, Ruff 0.16.8 |
| `.venv/Scripts/python.exe -B -m ruff format --check .` | Código 0, `304 files already formatted` |
| Colección de los diez módulos / filtro registrado | 438 casos, código 0; sin `integration_real` |
| Primer intento en sandbox, barrera de red demasiado amplia | Código 1: 202 aprobadas, 32 fallidas, 152 errores, 52 omitidas, 82 warnings, 142,97 s. La barrera añadida bloqueaba también loopback necesario para asyncio en Windows; no se considera una corrida funcional válida ni se oculta ese resultado |
| Precheck de asyncio y segundo intento en sandbox | `asyncio.run(asyncio.sleep(0))`, aun sin barrera, no terminó en 12 s; el intento completo se interrumpió antes de ejecutar pytest. No agrega casos aprobados/omitidos |
| Corrida completa fuera del sandbox con arnés corregido | **Código 1: 385 aprobadas, 1 fallida, 52 omitidas, 0 errores, 465 warnings, 576,10 s** |
| Repetición únicamente del caso fallido, mismo timeout y fixtures nuevas | **Código 0: 1 aprobada, 2 warnings, 9,41 s**. No demuestra que la corrida conjunta sea estable |
| Ensayo físico sintético consolidado | **11 PASS**, cero fallos; construcción y comandos completos en [evidencia #58](evidencia/issue-58/README.md). No se reutilizó exclusivamente el JSON temporal de etapa 3 |

Resultado por módulo de la **corrida completa**:

| Módulo | Aprobadas | Fallidas | Omitidas |
| --- | ---: | ---: | ---: |
| `test_compute_provision.py` | 28 | 0 | 0 |
| `test_caddy_config.py` | 42 | 0 | 47 |
| `test_caddy_deploy.py` | 137 | 0 | 5 |
| `test_demo_preload.py` | 22 | 0 | 0 |
| `test_demo_documents.py` | 73 | 0 | 0 |
| `test_budgets.py` | 10 | 0 | 0 |
| `test_budgets_api.py` | 3 | 1 | 0 |
| `test_document_recovery.py` | 45 | 0 | 0 |
| `test_document_recovery_cleanup.py` | 11 | 0 | 0 |
| `test_workspace_recovery.py` | 14 | 0 | 0 |
| **Total** | **385** | **1** | **52** |

Los **52 skips** son siete por Caddy ausente y 45 por Compose ausente (40 en
configuración, cinco en despliegue). Los 42 de configuración aprobados ejecutan
guards Python; no equivalen a `caddy adapt/validate`, Compose real ni systemd.
Los 137 de despliegue aprobados usan dobles/cripto/configuración offline. No se
instalaron binarios para eliminar skips. Linux/ARM64, Docker/NAT/peer real,
permisos POSIX, systemd/notify, ACME y HTTPS público quedan **no ejecutados**.
Las 465 advertencias de la corrida y las dos del caso aislado corresponden a
`DeprecationWarning: legacy embedding function config` de Chroma; no se ocultaron.

El fallo fue
`backend/tests/test_budgets_api.py::test_purga_real_de_original_indice_generacion_exports_y_sqlite`:
`esperar` agotó sus **15 s** con generación aún `running`, antes de llegar al
DELETE y las aserciones de purga. Se leyó únicamente la SQLite de esa fixture
artificial, en modo `ro`, para revisar tipo/estado/error/fechas: la generación
terminó `completed`, sin error, entre `20:44:18.742320Z` y `20:44:35.309627Z`
(aproximadamente **16,57 s**). En repetición aislada pasó sin cambiar el test.
Esto evidencia una finalización tardía en esa corrida; **no determina la causa**
de la variación ni demuestra un defecto de purga. Queda pendiente estabilizar o
explicar ese resultado antes de afirmar una selección completamente verde.
No se aumentó el timeout, debilitó una aserción, agregó skip/xfail ni cambió código.

Argumentos de selección reproducibles (desde la raíz; requieren entorno de test
saneado, no la configuración personal):

```powershell
.venv/Scripts/python.exe -B -m pytest -p pytest_asyncio.plugin -q -ra `
  -m '(unit or integration_mock) and not integration_real' `
  backend/tests/test_compute_provision.py backend/tests/test_caddy_config.py `
  backend/tests/test_caddy_deploy.py backend/tests/test_demo_preload.py `
  backend/tests/test_demo_documents.py backend/tests/test_budgets.py `
  backend/tests/test_budgets_api.py backend/tests/test_document_recovery.py `
  backend/tests/test_document_recovery_cleanup.py backend/tests/test_workspace_recovery.py
```

La ejecución real aplicó esos argumentos mediante `pytest.main` en un proceso
`python -B -c` con barrera de auditoría, más `--basetemp`, `--junitxml` y
`cache_dir` nuevos por corrida. Se deshabilitó autoload de plugins y se cargó
explícitamente `pytest_asyncio.plugin`; `MOCK_OCI=1`, `MOCK_GEMINI=1`,
`APP_ENV=test`, `PYTHONUTF8=1`, `PYTHONDONTWRITEBYTECODE=1`,
`ANONYMIZED_TELEMETRY=False`. Sólo se heredaron variables de sistema
`PATH`, `SYSTEMROOT`, `WINDIR`, `COMSPEC`, `PATHEXT`,
`PROCESSOR_ARCHITECTURE`, `NUMBER_OF_PROCESSORS`; HOME/USERPROFILE/TEMP/TMP
apuntaban a carpetas nuevas. No se heredaron claves ni `CADDY_BIN`/`COMPOSE_BIN`.
Los fixtures usan fuentes demo versionadas como material de prueba y datos
generados en temporales, sin leer volúmenes o estado operativo del proyecto.

La barrera corregida permite únicamente localhost/IP loopback y rechaza
`socket.connect`/`socket.getaddrinfo` externos antes de conectar/resolver; su
precheck verificó el bucle local y el rechazo de una dirección documental externa,
sin enviar solicitudes. Los subprocesos existentes de los tests usan argumentos
sintéticos/dobles revisados; esto no se presenta como un firewall general para
código arbitrario. La repetición aislada conservó ese mismo arnés/entorno.

Los reportes y fixtures de las corridas válidas quedan en directorios nuevos
ignorados `.data/issue58-auditoria-9ccec3e70fb94914bcd21e103fe2068b/` y
`.data/issue58-caso-aislado-2323733b13d14a9a9906064764d28423/`, marcados
`.synthetic-only`. Son datos artificiales y artefactos locales, no evidencia OCI;
este registro saneado conserva resultados y límites aunque esos temporales falten.
No se ejecutó la suite global, integración real, instalaciones, builds ni CI remota.

Comprobaciones documentales adicionales:

- Sintaxis del runbook y ensayo: 39 bloques Bash con `bash -n`, ocho heredocs
  Python y dos inline con AST, un JSON y un INI. Se agregó un cuerpo Python de
  ensayo con AST; los bloques PowerShell se parsearon sin ejecutarse. El comando
  de selección de arriba también se revisa al cierre documental.
- Seis consultas de monitoreo y una de drenaje ejecutadas sólo en SQLite
  `:memory:` contra las once migraciones literales (incluidos `append`/`extend`)
  y esquema del ledger leídos por AST. Sin importar app ni abrir bases reales;
  no acredita que un servicio real esté drenado.
- Enlaces/anclas, whitespace, diff y hashes finales se registran abajo, separados
  de las pruebas funcionales. No se comprobó disponibilidad de enlaces externos.

## Versión documental para la auditoría

La base de implementación es `f292415`, pero estos documentos siguen sin commit.
Identificación SHA256 de los archivos locales revisables:

| Artefacto | SHA256 |
| --- | --- |
| `docs/despliegue-oci.md` | `1f1bd6ca186db9f25375c7abe5b6feb09f69192352b72cb646cbe592fdc75ffd` |
| `docs/evidencia/issue-58/README.md` | `3218562c517f61a473529e412c6a1ec120efafb3ab40b0b412572534ac9791da` |

Un cambio posterior requiere registrar la versión nuevamente antes de atribuirle
una revisión humana. El hash no implica aprobación ni ejecución en una VM.

## Auditoría del recorrido y suficiencia de recuperación

Se volvió a leer el issue #58 completo y #133: sus cuerpos conservan las fechas
`14:12:58Z` y `14:15:28Z` del 16/10. El contraste de la guía usa la implementación
de `f292415`, los tres Compose y contratos afectados, no estados históricos de
README. Recorrido examinado: autorización/costos → provisión y bloqueo → host y
fuente exacta → imágenes → instalación Caddy → archivos técnicos → volumen →
red/DNS → observación/validación → precarga exclusiva → activación → salud/demo;
después actualización, parada, backup y recuperación por escenario.

| Tramo del runbook | Contraste efectuado / resultado documental |
| --- | --- |
| 1–3: operador, provisión y host | Flags/reportes/diario del aprovisionador; guardas de asignación agregada, A1 2/12 y boot 50; bootstrap Ubuntu 24.04 ARM64, SSH /32, Docker/Compose/firewall. Se mantiene la parada por falta de capacidad/gratuidad, no una alternativa paga |
| 4–7: imágenes, Caddy, archivos y datos | Tags/IDs esperados por `freeze_model`, hashes/artefactos y salida del instalador, rutas/modos/perfil técnico `DOCKER`, cuotas explícitas, creación sólo de raíz vacía y aprobación de identidad. Sin permisos recursivos ni normalización de datos previos |
| 8–11: red, confianza y precarga | Esquema/edad de evidencia de red, DNS sin proxy, peer literal, flags existentes del helper, un escritor con env productivos/credenciales RO y reactivación administrativa. Observación y `--validate` no acreditan un proveedor real |
| 12: salud y bibliotecas | Rutas API/UI vigentes, React, tres IDs demo y catálogo de generaciones guardadas; salud, lectura y generación nueva se mantienen separadas. Puente demo→generación pendiente funcional |
| 13–15: operación | Fuente/IDs anteriores conservados, cambio mediante helper y revisión de esquema; fingerprint tras reinicio; salud/disco/TLS/cuotas y obligaciones sin imprimir secretos. Seis consultas de monitoreo y una de drenaje comprobadas además contra esquemas SQLite en memoria |
| 16: backup | Copia física de todo el volumen sin productores, inventario/versión/hash, WAL/SHM y restauración nueva; copia externa protegida y secretos por separado. Ensayo sintético reproducible, sin atribuirlo a POSIX/Chroma/VM |
| 17–19: recuperación y diagnóstico | Journal/configuración separado de datos, VM con backup, H02 desde registro nuevo/pendiente y límites de consumo/revocación. Los dos huecos siguientes impiden acreditar suficiencia general; no se ocultan con health ni con pruebas de otros escenarios |

Correcciones documentales puntuales de etapa 4: la ruta de `documents` de la
precarga debe corresponder a la fuente desplegada después de una actualización,
no al checkout inicial antiguo; los bloques de backup con `assert` requieren
Python sin `-O`/`PYTHONOPTIMIZE`. Se actualizaron referencias de estado de etapa
4 y se agregó un enlace mínimo desde README, sin asumir la reescritura de #57.
No se alteraron decisiones, planes, código, tests, scripts ni Compose.

**17.1 afecta la suficiencia local.** Con `current.json` y backend eliminado,
[`deploy`](../infra/caddy/verify_deployment.py) consulta `operations.fingerprint()`
antes de la transacción; [`HostOperations.backend`](../infra/caddy/verify_deployment.py)
exige exactamente un contenedor (`backend-container-not-unique`). `--activate`
no tiene una rama de adopción de ese estado; `--recover` requiere journal real.
Se necesita que el equipo determine y dé soporte a una recuperación segura de
ese escenario. No se acredita como resuelto por conservar el volumen ni por una
prueba exitosa de reinicio con contenedores presentes.

**17.4 también afecta la suficiencia local.**
[`restaurar_registro`](../backend/app/session/recovery.py) retorna si el registro
ya confirmó su recuperación; `reconstrucciones_documentales_pendientes` en
[`store.py`](../backend/app/jobs/store.py) exige evidencia/estado durable pendiente,
y [`H02`](../backend/app/documents/lifecycle.py) despacha sólo esos documentos.
La pérdida de Chroma con documentos ya listos no crea por sí sola esas reservas.
Las funciones de [`rebuild.py`](../backend/app/core/rag/rebuild.py) carecen de
entrada administrativa completa para este caso. Restaurar un conjunto coherente
desde backup es una alternativa documentada cuando existe y su antigüedad es
aceptable; no cubre el caso sin backup ni equivale a reconstruir selectivamente
preservando SQLite, lápidas y contadores.

**Conclusión de aceptación:** ambos son límites de capacidad/procedimiento que
requieren decisión e implementación/integración administrativa del equipo, o una
decisión explícita y trazable sobre cómo satisfacer íntegramente el requisito.
No son meramente un ensayo externo pendiente de #133; no se trasladan allí para
dar por suficiente #58. Esta etapa documental no los implementa ni autoriza
borrar/adulterar SQLite, journals, fingerprints o reservas para sortearlos.

## Criterios de #58 auditados por separado

| Criterio / tarea del issue vigente | Evidencia local y estado | Qué falta / dónde se acepta |
| --- | --- | --- |
| Objetivo: runbook reproducible del despliegue completo Always Free | Guía redactada y recorrida estáticamente con máquinas, previos, comandos, resultados, consumo y paradas; **suficiencia aún no acreditada** | Resolver huecos locales de recuperación y revisión humana; reproducción real posterior en #133 |
| Tarea: consolidar #47/#48/#49 | Secciones 1–12; referencias operativas actuales y aislamiento de credenciales. **Documentada** | No acredita VM ni puente demo→generación: ese requisito funcional conserva decisión/seguimiento del equipo |
| Tarea: verificar Always Free/$0 | Sección 1 con consola/asignación efectiva y consumo agregado, sin autorizar gasto. **Documentada** | Verificación efectiva antes de operar y evidencia real en #133; no inferir $0 de una bandera o de la alerta Budgets |
| Tarea: recuperación de VM, índice OCI y estado operativo | Secciones 16–18 y evidencia física sintética. **Parcial / insuficiente para aceptación general** | Decisión/implementación local 17.1/17.4 y límites de datos/consumo no respaldados; luego reproducción real en #133 |
| Tarea: imágenes, backups y monitoreo | Secciones 13–16. **Documentada**, con ensayo físico sintético limitado | Compatibilidad real de versiones, Linux/ARM64 y servicios nativos; definir frecuencia/retención/custodia antes de operar |
| CA 1, revisión por quien no escribió la guía | Registro pendiente abajo. **No cumplido** | Integrante independiente, versión/fecha, observaciones y resolución; propia de #58, no sustituible por esta auditoría automática |
| CA 1, reproducción desde cero sobre VM de entrega | **No ejecutada**; sólo simulaciones/pruebas locales con sus límites | #133, con integrante identificado; no requiere otra VM ni autoriza destruir datos |
| CA 2, incluye verificación $0 | Checklist y bloqueo por incertidumbre. **Parte documental satisfecha** | No afirma gratuidad/capacidad actuales ni reemplaza evidencia OCI |
| CA 2, incluye procedimiento de recuperación suficiente | Hay escenarios y paradas explícitas, pero **pendiente** por 17.1/17.4 | Resolver dentro del alcance propio de #58 con el equipo; un bloqueo documentado no equivale a satisfacer la capacidad requerida |

**#58 no está listo para declarar todos sus criterios satisfechos.** La revisión
humana, los huecos funcionales y los resultados/limitaciones locales registrados
deben ser evaluados en la auditoría final. #133 sigue abierto y sin aceptación
nueva; no se marcaron casillas ni se escribieron issues/comentarios en GitHub.

## Registro pendiente de revisión humana independiente

Plantilla sin aprobación atribuida. Debe revisarse la versión completa y sus
impedimentos, no sólo aprobar la existencia del archivo o un resultado automático.

| Campo | Registro |
| --- | --- |
| Revisor/a, integrante distinto de quien redactó y de quien hizo el despliegue original | **Pendiente** |
| Fecha y versión revisada (commit al existir; mientras tanto SHA256 de guía/evidencia) | **Pendiente** |
| Recorrido de instalación/operación y costos revisado | **Pendiente** |
| Evaluación de 17.1/17.4 y contradicción demo→generación | **Pendiente** |
| Observaciones, responsable y resolución verificable | **Pendiente** |
| Dictamen humano explícito | **Pendiente — ninguna aprobación registrada** |
| Reproducción real y vínculo de evidencia #133, separado del dictamen documental | **Pendiente** |

## Cierre documental de esta etapa

Comprobación final del 16/10/2026: **nueve documentos**, **138 destinos locales**
y **20 anclas**, sin errores. **12 bloques PowerShell** parseados (incluido el
comando de selección), además de los 39 Bash/ocho heredocs/dos inline y el ensayo
Python ya registrados. JSON/INI válidos. No se probaron enlaces externos.

`git diff --check` pasó; se comprobaron por separado espacios finales y fin de
archivo de los tres Markdown nuevos sin rastrear. SHA256 confirma que los cinco
documentos relacionados de etapas 1–3 permanecen iguales al inicio de etapa 4.
En esta etapa sólo se modificaron README, runbook y esta matriz, y se agregó la
evidencia reproducible. Las referencias/estado de Git se conservaron, sin commits,
push, cambios de rama ni escrituras en GitHub. No hubo instalaciones, builds,
proveedores, infraestructura ni acceso a datos/credenciales reales.

Se entrega el diff local para la **auditoría final**. Permanecen pendientes la
revisión humana, la resolución propia de #58 para 17.1/17.4, explicar/estabilizar
el fallo temporal de la corrida conjunta y las comprobaciones nativas omitidas;
la reproducción real y evidencia externa continúan en #133. No se declara cerrado
#58 ni completado #133.

## Integración local y contraste documental — 17/10/2026

Este registro es posterior e independiente de las pruebas del 16/10. El checkpoint
`86a6ee801e755d7bcd4335ceb5a138bf44e580aa` conservó exclusivamente los nueve
documentos, tras respaldo privado fuera del checkout verificado con SHA256.
Se incorporó la referencia de `develop`
[`5988244a40fb1f3061c0bafc1fc106eb6e592a0a`](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/tree/5988244a40fb1f3061c0bafc1fc106eb6e592a0a),
con #137/#138 y #140, mediante merge sin commit inicial. Se resolvieron por
contenido sólo `docs/docker.md` e `infra/caddy/README.md`; README se combinó
automáticamente y se revisó. No se reintrodujo Streamlit ni se modificó código propio.

Con autorización posterior se verificaron rama, HEAD, MERGE_HEAD, ausencia de
conflictos/cambios sin preparar y diferencias propias limitadas a los nueve
documentos; `git diff --cached --check` aprobó. Se creó exclusivamente el merge
local **`8b97c6b5970cfcb0bae87cc7c7d8923baa40aa9d`**,
`chore(oci): integrar develop en la guía de despliegue #58`, con estos dos padres,
en orden:

1. `86a6ee801e755d7bcd4335ceb5a138bf44e580aa` — checkpoint documental.
2. `5988244a40fb1f3061c0bafc1fc106eb6e592a0a` — develop incorporado.

El working tree quedó limpio tras ese commit. Las correcciones documentales de
este contraste se dejan **sin preparar ni otro commit**. El archivo operativo de
fuentes pasa a `nuevamente-5988244.tar`; la versión fijada es el segundo padre,
no el antiguo `develop` local ni una referencia móvil. Los artefactos/resultados
históricos conservan nombres, fechas y versiones anteriores.

### Matriz de contraste de la base integrada

«Contrastado» significa lectura de archivos y diff entre bases, sin importar la
aplicación ni ejecutar fixtures, tests, builds, servicios o proveedores.

| Requisito / procedimiento | Fuente en la base integrada y observación | Evidencia y pendientes |
| --- | --- | --- |
| Aprovisionar, SSH, Docker/Compose, firewall, volumen y secretos | `infra/oci/`, tres Compose, configuración backend y helper Caddy sin cambios operativos desde `f292415`; sólo cambió el README Caddy | Recetas 1–11 aplicables; la evidencia anterior no acredita su ejecución sobre `5988244`. Capacidad/Always Free, host ARM64, peer y exposición real pendientes en #133 |
| Precarga demo y salud | CLI/implementación de precarga sin cambios; un escritor, configuración productiva y reactivación administrativa conservadas | Ninguna nueva precarga ni sonda de salud ejecutada. Generación demo sigue bloqueada; carga privada y ejemplos guardados no satisfacen ese requisito |
| Modos y paquetes | [Contrato/lectores](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/5988244a40fb1f3061c0bafc1fc106eb6e592a0a/backend/app/schemas/responses.py): 1.0 multiagente, 1.1 multiagente/básico. Básico `no_evaluado`/`no_realizada`, métricas/juez nulos; `completed` requiere persistencia, no evaluación independiente | Runbook 12.3 documentado. [Verificación de modos](verificacion-modos-generacion.md) conserva su versión/resultados propios; sin nuevas pruebas de #58 |
| Recuperación y lectura | [Restauración](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/5988244a40fb1f3061c0bafc1fc106eb6e592a0a/backend/app/session/recovery.py) guarda el modo 1.1; [catálogo](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/5988244a40fb1f3061c0bafc1fc106eb6e592a0a/backend/app/generations/catalog.py) valida modo/identidad/fuente. 1.0 se interpreta como multiagente | Recuperar conserva modo, no sesiones/trabajos pendientes ni datos nunca sincronizados. Comprobación funcional nueva pendiente; 17.1/17.4 siguen siendo impedimentos propios de #58 |
| Descargas por formato | [ExportDownloads](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/blob/5988244a40fb1f3061c0bafc1fc106eb6e592a0a/frontend/src/components/ExportDownloads.tsx), resultado/historial y validadores: JSON/MD/PDF para los cinco formatos; CSV/TSV/APKG sólo flashcards, sin LLM | #46 integrado. Básico descargable sin aprobación ficticia; badge OCI sólo ante proveedor OCI confirmado. [Registro #46](verificacion-issue-46.md) conserva su alcance con dobles; no se ejecutó aquí |
| Actualización, backup y rollback | SQLite conserva esquema 11, pero el lector de `f292415` no admite 1.1. Comparar también paquetes locales/OCI y exportaciones con las imágenes anteriores | Runbook 13/17.2/17.3 condicionado a lectores y modos compatibles. Restaurar datos locales no revierte objetos/efectos OCI. Ensayo físico del 16/10 sin repetir; no acredita compatibilidad de la nueva aplicación |
| UI y arnés Caddy | #140 incorpora #139: Streamlit/config/dependencias/tests retirados; `pytest.ini` sólo recoge `backend/tests`. Fixtures `caddy_fixture.py`/`caddy_browser.py` usan `frontend/dist` real, interacción/recarga y área de carga principal; no WS de UI | README Caddy ajustado; referencias a archivos retirados fijadas en `f292415`. [Registro #139](verificacion-issue-139.md) conserva sus resultados. No se ejecutó el arnés actual ni se trasladaron sus resultados a #58 |

La revisión también comprobó por lectura que el helper sigue exigiendo backend
único con `current.json` (17.1), y que H02 no se dispara simplemente al perder
Chroma con SQLite ya recuperada (17.4). No se crearon CLI ni procedimientos que
eludan esos controles. React sigue excluyendo la selección demo de
`docSeleccionado`; el backend sigue exigiendo el documento propio. La contradicción
con fase 5 conserva sus referencias históricas y requiere decisión/seguimiento
funcional del equipo. No se modificaron decisiones ni planes para resolverla.

### Alcance de las comprobaciones nuevas y aceptación

Comprobaciones documentales ejecutadas sobre los nueve documentos:

- **138 destinos locales y 23 anclas**, sin errores; cercas Markdown balanceadas.
- **14 referencias inmutables** a archivos/árboles, comprobadas contra objetos Git
  locales de sus SHA; las referencias de línea citadas caben en esos archivos.
  No se verificó disponibilidad HTTP ni se recorrieron enlaces externos.
- **Cuatro bloques modificados:** dos PowerShell aprobados mediante
  `System.Management.Automation.Language.Parser.ParseInput` y dos Bash/sh mediante
  Git Bash `--noprofile --norc -n`, sin ejecutar sus cuerpos. El primer arranque
  MSYS en sandbox falló con `NtCreateDirectoryObject`/`0xC0000022`; el análisis
  sintáctico fuera del sandbox aprobó ambos bloques. Esto no acredita sus comandos
  operativos, plataformas nativas, instalaciones ni builds.
- `git diff --check` aprobado; revisión del diff y de su alcance. Las funciones
  y el cuerpo reproducible del ensayo sintético permanecen iguales al merge;
  no se repitió el ensayo ni se atribuyeron sus 11 PASS a la nueva base.

No se ejecutaron nuevas pruebas funcionales, Ruff, fixtures de backup,
proveedores, infraestructura, instalaciones ni builds. Las correcciones se
limitan a los nueve documentos y el índice queda vacío para revisión.

Los **385 aprobados, 1 fallo y 52 omisiones** de la corrida conjunta y el caso
aislado posterior siguen perteneciendo a `f292415`. El fallo temporal conjunto
permanece abierto: la integración no demuestra su causa ni su resolución, y no
se repitieron pruebas ni se ampliaron timeouts. Tampoco se actualiza la aceptación
de #58 por conservar o integrar evidencia anterior.

Continúan abiertos 17.1, 17.4, demo→generación, el fallo temporal conjunto, la
revisión humana independiente y #133. La plantilla humana anterior sigue sin
aprobación atribuida; deberá identificar la versión documental que revise.
La reproducción real externa permanece separada de esa revisión y del contraste
documental. #58 no se declara cerrado ni #133 completado.
