# Revisión de Seguridad y Hardening (Issue #51)

**Fecha de revisión:** 2026-10-18
**Referencia:** decisiones_proyecto.md §11 (PR #128)

Revisión de `feature/issue-51-hardening` sobre `develop` `5988244`.
Se conserva el informe original en el commit `629795b`; las comprobaciones
inferiores corresponden a la aplicación React/FastAPI y ambos modos actuales.
Los proveedores se simulan explícitamente y `.env` no se carga en las pruebas.

---

## Tabla de Evaluación de Hardening

| Categoría | Criterio (§11) | Comprobación Realizada | Resultado (OK / Hallazgo) | Observaciones / Acción |
| :--- | :--- | :--- | :---: | :--- |
| **Acceso / Sesiones** | Material público o simulado (§11.1) | Aviso antes de archivo y texto, ES/EN/PT; `frontend/src/flows/documentUpload.test.tsx` | **Corregido** | Se explicita el envío posible de fragmentos/imágenes al proveedor de IA y se excluyen secretos, documentos confidenciales y datos sensibles. La autorización declarada no vuelve público un archivo. |
| **Acceso / Sesiones** | Tokens, expiración y revocación (§11.2) | `session/manager.py`, `jobs/store.py`; `test_workspaces.py`, dos casos de secreto/expiración en `test_jobs_store.py` y `test_review_regressions.py` | **OK** | Tokens opacos aleatorios de hasta 24 h; códigos de recuperación almacenados como hash. Cierre, rotación y borrado revocan acceso. React conserva el token en memoria. No se usan JWT. |
| **Acceso / Sesiones** | Límites de recuperación (§11.2) | `api/security.py`, `api/routes/workspaces.py`; `test_bloqueo_fuerza_bruta_recuperacion` y regresiones de origen/recuperación | **OK** | La API aplica 100 solicitudes/min globales, 10/min por IP y cinco fallos o intentos en vuelo por IP. Caddy autentica la IP de conexión; no se confía en `X-Forwarded-For` del cliente. |
| **Acceso / Sesiones** | Ownership documental, SSE, exportación y progreso (§11.2) | Doce casos de `test_dos_espacios_recursos_ajenos_404` en `backend/tests/integration/test_contract.py` | **OK** | Dos espacios y modos básico/multiagente: estado, SSE, exportación, quiz, documento y escritura de progreso ajenos devuelven 404, sin otras llamadas LLM. GET de progreso devuelve el propio espacio vacío (200), no el del otro usuario. |
| **Archivos / Contenido** | Firma, tamaño, parseo y nombres (§11.3) | `core/rag/parser.py`, `documents/admission.py`, `api/routes/documents.py`; `test_parser.py` y pruebas de upload | **OK** | Extensión y MIME declarados no acreditan formato. Firma PDF, texto válido, tamaño, límites de páginas/tokens y nombre como etiqueta; no se abre una ruta suministrada por el usuario. |
| **Archivos / Contenido** | Aislamiento y recursos (§11.3) | `_pdf_aislado`, `_worker_pdf`; pruebas de timeout, corruptos, cifrados y límites en `test_parser.py` | **OK** | Proceso descartable, timeout de extracción de 30 s y límite de memoria de 512 MiB en Linux. No se ejecutan macros, adjuntos, snippets ni URLs remotas. Windows no acredita `RLIMIT_AS`; Linux se verifica en CI. |
| **Archivos / Contenido** | Prompt injection y salida tipada (§11.4) | `prompts.py`, `writer.py`, `critic.py`, `basic.py`; `test_prompts.py`, pruebas Writer/Critic y `test_basic_mode.py` | **OK** | Instrucciones del sistema separadas del JSON de evidencia; sólo chunks autorizados y sin herramientas arbitrarias. Pydantic/citas se validan antes de publicar. Básico conserva sus controles y se rotula no evaluado; no recibe un score ni revisión independiente. Los delimitadores no garantizan inmunidad a inyección. |
| **Archivos / Contenido** | Escape y referencias remotas (§11.4) | `frontend/src/flows/security.test.tsx`, `SourcesDrawer.tsx`, `FormatRenderers.tsx` | **OK** | Seis casos de texto hostil: cinco formatos y fuente recuperada. HTML/JS se muestran como texto; no se crean scripts, iframes, enlaces `javascript:` ni cargas remotas. La imagen original usa API autenticada y un Blob PNG validado. |
| **Archivos / Contenido** | CSV/TSV/APKG (§11.4) | `exports/anki.py`; `test_exports_anki.py`, controles de exportación en API/CI | **OK** | Campos HTML escapados y encapsulados, CSV con comillas, etiquetas controladas y neutralización de fórmulas, `[sound:]` y `[latex]`. Exportar no ejecuta el código educativo. |
| **Secretos / Logs** | Sin cuerpos, tokens, códigos o URL privada (§11.5) | `test_security_hardening.py`, `test_api_health.py`, `test_review_regressions.py`; auditoría de canarios en Caddy | **Corregido** | Se reprodujo el volcado de parámetros de URL al activar INFO. Ahora se registra la plantilla de ruta, método estándar y request_id; una ruta desconocida usa un rótulo fijo. Tres regresiones incluyen parámetros, query, cabeceras, body y método arbitrario. El contenedor base desactiva el access log crudo de Uvicorn, como ya hacía producción. Otra prueba con Uvicorn/TCP real reproduce un error 500: el log del servidor conserva el tipo y elimina mensaje/traceback privado; el log de aplicación mantiene el request_id. Los errores de proveedor no se exponen al cliente. |
| **Secretos / Logs** | Escaneo del historial (§11.2) | Gitleaks 8.30.1 oficial, SHA-256 comprobado, `git --all --full-history` | **OK, con triaje** | Cero detecciones pendientes tras revisar cuatro falsos positivos. `.gitleaksignore` limita cada excepción a commit/archivo/regla/línea, sin excluir tests ni carpetas. Metadatos en `evidencia-seguridad-51.json`; no se publican coincidencias ni valores de secretos. |
| **Config. / Recursos** | Backend, Git y almacenamiento privado (§11.2) | `.gitignore`, Compose/Docker y `storage/oci_storage.py`; configuración estricta y evidencia OCI previa | **OK local** | UI sin credenciales del proveedor; `.env`, claves, SQLite, índices y uploads excluidos. Los `.env.*.example` se versionan deliberadamente como plantillas. El proveedor OCI valida bucket privado y región; se reutiliza la evidencia previa de gobernanza, no se afirma una inspección OCI nueva. |
| **Config. / Recursos** | CORS, cabeceras y proxy (§11.2) | `test_api_health.py`, `test_review_regressions.py`; suites Caddy x86/ARM64 de CI | **OK local** | CORS deshabilitado por defecto y lista explícita al habilitarlo, sin cookies entre orígenes. Caddy configura CSP/HSTS y sobrescribe cabeceras privadas de origen; nunca se envía su secreto a React. La aceptación pública pertenece a #133. |
| **Config. / Recursos** | Retención, limpieza y tombstones (§11.5) | `session/lifecycle.py`, `documents/lifecycle.py`; `test_workspaces.py`, `test_workspace_recovery.py`, pruebas documentales/progreso de la suite | **OK local** | Borrados revocan acceso y conservan obligaciones durables; los fallos de almacenamiento no simulan limpieza exitosa ni permiten resurrección. Perder SQLite antes de confirmar una revocación remota mantiene la limitación documentada: se debe conservar el volumen. |


## Validación y reproducción

Validación local: 412 pruebas de seguridad/componentes relacionados aprobadas,
doce casos de aislamiento entre espacios aprobados, cinco regresiones de
privacidad/despliegue y 96 pruebas React con build aprobado. Son pruebas de software con proveedores simulados, sin inspección
visual manual ni consumo Gemini/OCI. Se comprobó además la sensibilidad de Gitleaks con una credencial sintética,
sin consultas externas. La suite completa y el proxy con TLS local
se verifican en los checks del PR. Issue #53 reutiliza la cobertura; no se cierra
por esta revisión. El trabajo de evaluación humana de #60 conserva su alcance.

```powershell
.venv\Scripts\python.exe -m pytest backend/tests/test_security_hardening.py backend/tests/test_workspaces.py backend/tests/test_review_regressions.py backend/tests/test_parser.py backend/tests/test_prompts.py backend/tests/test_exports_anki.py backend/tests/test_api_health.py backend/tests/test_jobs_store.py::test_la_base_no_guarda_secretos_en_claro backend/tests/test_jobs_store.py::test_sesion_expirada_y_revocada_no_sirven --basetemp=.data/51-revision-nueva
.venv\Scripts\python.exe -m pytest -m integration_mock backend/tests/integration/
npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend audit
```

Para repetir el escaneo, usar [Gitleaks 8.30.1](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1)
verificado contra su manifiesto oficial y ejecutar desde la raíz:

```powershell
gitleaks git . --log-opts="--all --full-history" --redact=100 --ignore-gitleaks-allow --no-banner --report-format=json --report-path=.data/51-escaneo-redactado.json
```

No se reescribe historia para resolver falsos positivos. El escaneo no lee `.env`
ni `scripts/` sin seguimiento. Cada nuevo commit se vuelve a escanear antes de
dar el PR por listo. Los reportes temporales permanecen fuera de Git.

## Aceptación externa

Esta revisión cubre el alcance local de #51. VM, HTTPS público, configuración
efectiva de OCI/IAM y ejecución conjunta con proveedores reales se aceptan en
[#133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133).
Se conserva la evidencia real previa en [gobernanza OCI](evidencia-oci-gobernanza.md).
Una checklist marcada o una CI verde no acredita esa aceptación externa.
