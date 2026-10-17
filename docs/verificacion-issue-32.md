# Verificación del panel de generación (#32)

> **Evidencia histórica:** este registro corresponde a la versión y fecha
> indicadas. #139 retira Streamlit; las pruebas y capturas de esa interfaz no
> acreditan React. Ver [cobertura vigente](verificacion-issue-139.md).


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

La corrida real se completó el **12/10/2026**, después de consultar las cuotas
del proyecto de la clave local en AI Studio. El usuario eligió
`gemini-3.5-flash-lite`: 15 RPM, 250.000 TPM y 500 RPD compartidos por redacción,
revisión y visión. Embeddings mantiene `gemini-embedding-2`: 100 RPM, 30.000 TPM
y 1.000 RPD. La cuenta muestra Pro y el proyecto de API, nivel gratuito.

Se corrigieron los errores revelados por esa verificación:

- Gemini devuelve HTTP 400 con el `maxItems: 256` del esquema de extracción.
  La misma solicitud sin ese límite terminó con HTTP 200. Se retira únicamente
  del esquema enviado; Pydantic sigue rechazando más de 256 afirmaciones o
  textos de más de 2.000 caracteres.
- Las ubicaciones del extractor se restringen a las claves reales de segmentos
  mediante `enum`. Se mantiene la validación de pertenencia posterior.
- La instrucción distingue las proposiciones técnicas de descripciones del
  borrador como «el título es…», y separa hechos de analogías ficticias explícitas.
  Los títulos que contienen afirmaciones técnicas siguen siendo evaluables.
  La versión de prompts pasa a `nm-prompts-1.1.1`.

El recorrido usa Streamlit AppTest y APIClient contra FastAPI TestClient, con
SQLite, Chroma, worker, LangGraph y **Gemini reales**, y **storage local explícito**.
Un único envío de flashcards/principiante/general/didáctico/ES/completo terminó
en `completed`, con revisión automática aprobada: 14 afirmaciones, 14 respaldadas,
anclaje 1,0. Usó 8 solicitudes de redacción/revisión, 30.600 tokens de entrada y
7.367 de salida, tras dos redacciones; el contador reservó 25 solicitudes de
embeddings. Son cifras de esta corrida, no del consumo global de la cuenta.

La [evidencia reproducible](issue-32-gemini-real.json) contiene la fuente sintética,
su hash, parámetros, trazabilidad, evaluación y consumo, sin claves ni tokens de
sesión. El resultado de Gemini es una evaluación automática, no una revisión
humana. Las 20 combinaciones y ES/EN/PT siguen acreditadas con dobles; esta
evidencia real corresponde a un caso en español. La evaluación humana #60, el
smoke con OCI real #54 y la entrega pública conservan sus verificaciones propias.

La biblioteca demo tiene rutas de lectura, pero `POST /api/generate` todavía
admite sólo documentos del workspace. Se informa esa limitación en la UI y se
deja su integración como pendiente de #49/#117. El progreso SSE y la presentación
del material siguen correspondiendo a #33 y #34.
