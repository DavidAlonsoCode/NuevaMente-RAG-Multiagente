# Verificación del panel de generación (#32)

PR #118, rama `feature/issue-32-panel-parametros-generacion`, destino `develop`.

## Errores corregidos en la revisión

- Cambiar el idioma de UI reiniciaba los parámetros porque Streamlit recreaba
  los widgets al traducir sus etiquetas. La selección ahora vive en estado
  independiente del widget y se restaura antes de dibujarlo.
- Seleccionar una fuente en el catálogo del #25 no cambiaba la fuente del
  generador. Ambos controles ahora comparten la misma selección; una demo no
  puede provocar una generación silenciosa desde otro documento privado.
- Después del primer envío desaparecía el acceso al catálogo y al texto pegado.
  Se puede volver a documentos y regresar a la cabecera sin perderla ni reenviar.
- Una admisión HTTP incompleta podía producir `KeyError` en la pantalla. El
  cliente valida identificador, estado y rutas del recibo antes de aceptarlo;
  una respuesta inválida produce un error recuperable y conserva el intento.
- Se eliminó la advertencia visible por definir simultáneamente el valor del
  selector de nicho en sus argumentos y en `session_state`.
- Se aisló el indicador global de navegación que las pruebas AppTest conservaban
  entre scripts; la suite completa ahora puede mezclar la app y scripts temporales.
  Una comparación exacta de tiempos en el test de reintento de Writer se cambió a
  tolerancia de un microsegundo, manteniendo el número de intentos y el Retry-After.

## Pruebas automatizadas

```powershell
$env:MOCK_OCI='1'; $env:MOCK_GEMINI='1'
python -m pytest frontend/tests -q
python -m pytest backend/tests/test_generation_ui_integration.py -q
python -m ruff check .
python -m ruff format --check .
```

- Frontend: 155 pruebas aprobadas; incluyen los 20 perfiles/formatos, cambios de
  idioma, sincronización documental, navegación, errores 409/429/503,
  idempotencia y recibos inválidos.
- Integración: 20 casos aprobados desde AppTest/cliente HTTP hasta FastAPI,
  SQLite, Chroma, worker, grafo y almacenamiento local. Se alternan los idiomas
  ES/EN/PT y alcance completo/sección. Se comprueban los metadatos del paquete
  revisado y guardado, no sólo el HTTP 202.
- Gemini tiene un doble explícito en esas pruebas: esto no mide calidad de IA
  real. Cada caso usa almacenamiento aislado y conserva el límite de solicitudes
  del proveedor; no se aumenta el presupuesto para hacer pasar la matriz.
- Las pruebas ordinarias no usan claves ni consumen servicios de Google/OCI.

## Comprobación manual y límites de la entrega

El recorrido local usa la UI del PR con API, worker, SQLite y Chroma reales,
Gemini simulado y almacenamiento local explícito. Se comprueba la carga de una
fuente sintética, selección desde el catálogo, formato flashcards, idioma de
contenido PT y traducción de la interfaz a EN conservando los parámetros.

La corrida con Gemini real indicada en el issue queda pendiente: el `.env`
disponible tiene la clave, pero no los triples RPM/TPM/RPD que exige §7.5.
No se inventan cuotas ni se presenta el doble como evidencia real. El issue
#32 debe permanecer abierto hasta registrar esa comprobación. Seguimiento: #117
y smoke real #54.

La biblioteca demo tiene rutas de lectura, pero `POST /api/generate` todavía
admite sólo documentos del workspace. Se informa esa limitación en la UI y se
deja su integración como pendiente de #49/#117. El progreso SSE y la presentación
del material siguen correspondiendo a #33 y #34.
