# Smoke manual con OCI y Gemini reales

Esta comprobación acredita integración con los proveedores usando los mismos
adaptadores síncronos, gestor de cuotas y worker de la aplicación. Puede hacerse
localmente; no necesita VM. Los mocks son rechazados y la CI pública no la ejecuta.

## Ejecutar una generación nueva

Detener la API local. Con las credenciales actuales, los presupuestos ya
configurados y autorización para procesar el PDF público VCN:

```powershell
$env:PYTHONPATH = "backend"
$env:MOCK_OCI = "0"
$env:MOCK_GEMINI = "0"
.venv\Scripts\python.exe -m app.smoke_real
```

El comando admite únicamente el original VCN del conjunto documental conocido.
Lo ingiere, espera su trabajo, genera flashcards con el grafo normal y exige
aprobación y persistencia OCI confirmadas. Recupera el original y JSON canónico,
comprueba sus hashes y descarga MD/PDF/APKG mediante el servicio habitual de
exportaciones. Abre el PDF y el mazo SQLite de Anki, verifica sus páginas y
tarjetas y comprueba que Markdown conserva las referencias.

Usa `.data/smoke-real` como registro separado y `.data/smoke-real-evidencia` como
salida. Conserva **el mismo presupuesto OCI del DATA_DIR habitual** y bloquea
ejecuciones simultáneas con la API. No crea recursos cloud ni sube límites.
Un rechazo o fallo técnico termina la prueba: no repite el pipeline entero.

## Reutilizar una generación real ya aprobada

Para evitar nuevas llamadas al modelo, se acepta un archivo local `--reuse`
con sólo `workspace_id` y `generation_id`, junto al directorio operativo que
contiene esos registros. No guardar tokens, claves ni códigos de recuperación.
El comando revalida autorización, original, paquete y exportaciones en OCI.
La evidencia debe identificar dónde se ejecutaron la ingesta y generación
originales; una corrida en modo reutilización no acredita una generación nueva.

```powershell
.venv\Scripts\python.exe -m app.smoke_real --reuse .data/smoke-reuse.json --data-dir .data/registro-preparacion
```

La prueba `integration_real` exige selección y habilitación explícitas:

```powershell
$env:NUEVAMENTE_REAL_SMOKE = "1"
.venv\Scripts\python.exe -m pytest backend/tests/integration/test_smoke_real.py -m integration_real -q --basetemp=.data/smoke-pytest
```

Para reutilización, definir además `NUEVAMENTE_REAL_SMOKE_REUSE`,
`NUEVAMENTE_REAL_SMOKE_DATA` y, opcionalmente, `NUEVAMENTE_REAL_SMOKE_OUTPUT`.
Si no se habilita explícitamente, el test real queda omitido; una omisión no
demuestra integración. No activar estas variables en PRs de terceros ni agregar
credenciales a Actions. Restaurar los modos del entorno de trabajo al terminar.

## Evidencia y cierre humano

El informe guarda versión, hash del comprobador, estado, modelos, política de
timeout/reintentos observada, hashes de artefactos y diferencias de consumo OCI
por operación. Los contadores distinguen la fase medida del acumulado mensual;
no atribuyen al smoke llamadas anteriores. Se guardan el recibo y el JSON
aprobado, nunca headers de autenticación ni respuestas técnicas brutas del SDK.

La [evidencia de esta ejecución](evidencia/issue-54/README.md) reutiliza la
ingesta y generación real de A del #55, realizada mediante el worker normal.
No se repite IA por cada issue. La apertura estructural del APKG verifica cuatro
notas y cuatro tarjetas; la importación manual en Anki Desktop es una revisión
humana adicional.

La aceptación final del #133 conserva la constancia de dos integrantes que
presencien/revisen la ejecución y la captura real de consola OCI mostrando los
objetos. El ajuste autorizado del 15/10 permite cerrar #54 con la evidencia
automatizada real registrada; estas comprobaciones externas siguen pendientes. No hay una sesión de consola OCI disponible en el navegador conectado,
por lo que no se fabrica esa captura ni se sustituye por una imagen simulada.
La inspección visual del PDF y sus cuatro páginas sí fue realizada.
