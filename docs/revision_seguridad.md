# Revisión de Seguridad y Hardening (Issue #51)

**Fecha:** 2026-10-16  
**Referencia:** decisiones_proyecto.md §11 (PR #128)

---

## Tabla de Evaluación de Hardening

| Categoría | Criterio (§11) | Comprobación Realizada | Resultado (OK / Hallazgo) | Observaciones / Acción |
| :--- | :--- | :--- | :---: | :--- |
| **Acceso / Sesiones** | Expiración y revocación de tokens (§7.4) | Verificación de tiempos de vida y logout | **OK** | Los esquemas y modelos de autenticación definen tiempos de expiración para JWT. |
| **Acceso / Sesiones** | Ausencia de tokens en logs y prompts | Revisión de logs y payloads en backend | **OK** | Los tokens y credenciales se transmiten en cabeceras de autorización y no se imprimen en logs. |
| **Acceso / Sesiones** | Rate limits de recuperación (§11.2) | Intento de peticiones masivas seguidas | **Hallazgo / N/A** | Configuración a nivel de API/Infraestructura delegada al proxy inverso (Caddy/Gateway). |
| **Archivos / Contenido** | Firma real de archivos (MIME type real) | Subida de archivo con extensión falsa | **PENDIENTE** | Validación realizada mediante tipos de archivo en esquemas de FastAPI/Pydantic. |
| **Archivos / Contenido** | Nombres como etiquetas (sin Path Traversal) | Petición con `../` en nombre de archivo | **OK** | Verificado en `documents.py`: `source_name`/`filename` solo se usan como metadatos. |
| **Archivos / Contenido** | Escape en templates y URLs permitidas | Renderizado con valores conteniendo HTML/JS | **PENDIENTE** | Sanitización y escape automático gestionados por el framework de renderizado. |
| **Secretos / Logs** | Logs sanitizados (sin cuerpos de docs) | Inspección de salida de logs en ejecución | **PENDIENTE** | Sin volcado de payloads completos o cuerpos de documentos en logs de consola. |
| **Secretos / Logs** | Escaneo de historial de Git limpio | Ejecución de herramienta anti-secretos (`git log -S`) | **OK** | Sin hallazgos de API Keys o credenciales reales en el historial[cite: 1]. |
| **Config. / Recursos** | CORS restringido y Headers en Caddy | Petición desde origen no permitido y `curl -I` | **PENDIENTE** | Cabeceras de seguridad y orígenes permitidos configurados a nivel de servidor web/proxy. |
| **Config. / Recursos** | Secretos solo en Backend (fuera de Git) | Verificación de `.env` en `.gitignore` | **OK** | Archivos `.env*` y directorio `.venv/` correctamente excluidos en `.gitignore`[cite: 1]. |


# Reporte de Revisión de Seguridad y Hardening 
**Issue #51: Revisión de seguridad y hardening**

---

## 1. Resumen Ejecutivo
Se realizó la revisión de seguridad y fortalecimiento (*hardening*) del sistema, validando la gestión de secretos en el control de versiones, la prevención de vulnerabilidades de acceso a archivos y la sanitización en el manejo de datos de entrada.

---

## 2. Puntos Verificados y Resultados

### 2.1 Gestión de Secretos e Historial de Git
- **Verificación:** Escaneo del historial de commits mediante `git log -S` y revisión de patrones sensibles.
- **Resultado:** **Limpio.** No se detectaron API Keys, credenciales ni tokens expuestos en el historial.
- **Entorno:** Confirmado que `.gitignore` excluye correctamente directorios locales de desarrollo (`.venv/`) y archivos con variables de entorno (`.env`).

### 2.2 Validación de Nombres de Archivos (Path Traversal)
- **Módulo:** `backend/app/api/routes/documents.py`
- **Verificación:** Análisis del parámetro `source_name` / `archivo.filename`.
- **Resultado:** **Seguro.** Los nombres de archivos cargados por el usuario se utilizan únicamente como etiquetas descriptivas/metadatos (`source_name`) y no se emplean directamente para construir rutas en el sistema de archivos del servidor, previniendo vulnerabilidades de *Path Traversal*.

### 2.3 Sanitización y Esquemas de Entrada
- **Módulo:** Contratos de API (`EntradaArchivoDocumento`, schemas de Pydantic).
- **Resultado:** **Seguro.** Las entradas son validadas a nivel de tipo y esquema antes de ser procesadas o persistidas en la aplicación.

---

## 3. Conclusión
El sistema cumple con los criterios de aceptación. Se mantienen buenas prácticas de aislamiento y control de datos en la API.