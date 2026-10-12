# Integración de API con dependencias simuladas (#52)

```bash
pytest -m integration_mock backend/tests/integration/
python .github/scripts/verify_integration_faults.py
```

La suite usa la fábrica de app, rutas FastAPI, SQLite temporal, Chroma, el gestor
de un único worker, StateGraph, validadores, persistencia local y exportadores
reales. Sólo se simulan respuestas/fallos de Gemini y OCI. El conftest raíz activa
mocks antes de importar configuración, para que un `.env` personal no inicialice
por accidente el proveedor real. Las pruebas reales siguen requiriendo selección
explícita `-m integration_real` y ambos mocks desactivados.

| Capacidad | Comprobación |
|---|---|
| Ingestión y generación | Fuente `ready`, cinco formatos, `completed`, canónico y descargas; flashcards incluyen CSV/TSV/APKG del #45 |
| Terminales | Rechazo tras tres revisiones, fallo técnico, cancelación en cola/en ejecución, ausencia de contenido/exports no aprobados |
| Idempotencia | Seis solicitudes simultáneas de generación con una clave, conflicto 409 por otro cuerpo; quiz/progreso repetidos sin duplicados ni IA |
| Persistencia | Fallo storage, aprobado retenido, cuatro `/persist` concurrentes y confirmación exacta sin repetir IA |
| SSE | Encuadre/id monotónico, cursor/reconexión, sin nuevas llamadas; heartbeat y desconexión real por TCP local conservan trabajo y `status_url` |
| Errores | 400/401/404/409/413/422/429/500/503 en rutas reales; envoltorio exacto, mensaje seguro y request_id de cuerpo/cabecera |
| Aislamiento | Segundo espacio no accede a fuente, quiz, estado, stream ni export; chat/glosario usan endpoints comunes de #20 |
| Proveedor síncrono | Timeout propagado, tres intentos como máximo, cuota por intento, cuota agotada antes de otra llamada; worker común y generación consultados por API |

Las fixtures de chat/glosario comparten los dobles semánticos existentes para
reutilizar criterios de citación; se seleccionan por escenario sin mezclar proveedores.
Todos los servidores de prueba son locales y efímeros, y se cierran en `finally`.

## Fallos sembrados

`NM_INTEGRATION_FAULT` existe sólo en el conftest de esta suite. El verificador
ejecuta dos casos aislados: elimina `X-Request-ID` del transporte de test y cambia
el techo del worker a cinco intentos. Exige salida pytest 1, `AssertionError`,
el caso fallido y la marca de inyección; un error de colección/configuración o
una prueba ausente no cuentan como detección. El segundo fallo también tiene un
límite finito: no se simula un bucle infinito. No se modifica código productivo
ni se exige inventar un defecto real para demostrar sensibilidad de la suite.

CI ejecuta la suite completa, luego el comando explícito de integración (techo
120 s) y las dos mutaciones (techo 60 s cada una). `calidad` mantiene techo total
10 min. Publica `integration-mock-report` (JUnit) durante 14 días, además de la
evidencia Desktop del #45. La corrida local inicial: 34 escenarios en 41 s,
con ambos fallos sembrados detectados. Los tiempos Linux quedan en Actions.
