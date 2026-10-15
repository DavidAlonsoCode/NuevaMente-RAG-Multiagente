# Ejemplos generados previamente

La biblioteca permite abrir adaptaciones ya revisadas y guardadas en OCI sin
esperar una generación nueva. Conserva fuente, fecha, parámetros y duración del
trabajo. Comparte los renderizadores del material y las fuentes de la aplicación.
El quiz empieza sin soluciones; la práctica demo no modifica el progreso privado.

## Preparación administrativa

Detener primero la API que use el mismo `DATA_DIR`. Desde la raíz, con las
credenciales existentes y ambos mocks desactivados:

```powershell
$env:PYTHONPATH = "backend"
.venv\Scripts\python.exe -m app.demo.prepare --check
.venv\Scripts\python.exe -m app.demo.prepare
```

`--check` sólo comprueba los tres archivos y la matriz, sin proveedores. La
ejecución real ingiere esos originales mediante el worker y solicita las tres
adaptaciones oficiales del mismo VCN, sus complementos y casos de pagos/salud.
No crea infraestructura ni aumenta presupuestos. Conserva el presupuesto OCI del
`DATA_DIR` habitual; reserva `.data/issue55-real` para el registro de preparación.

El checkpoint conserva IDs y mediciones, nunca tokens, API keys ni códigos de
recuperación. Una reanudación utiliza las mismas claves de idempotencia. No
repite automáticamente pipelines fallidos o rechazados. Sólo después de resolver
una interrupción del proceso puede recuperarse ese intento explícitamente:

```powershell
.venv\Scripts\python.exe -m app.demo.prepare --generate-only --solo vcn-a --recuperar-interrumpidos
.venv\Scripts\python.exe -m app.demo.prepare --publish-only
```

Los tres originales se limitan a los nombres de `documents/`. La publicación
verifica sus hashes, las fuentes y los paquetes canónicos aprobados. Copia bytes
sin reescribir contenido educativo a `demo/examples/blobs/{sha256}` y confirma
`demo/examples/catalog.json` al final, con escritura condicional. Una publicación
interrumpida no entrega paquetes parciales. Reanudar una publicación no llama al LLM.
Los ejemplos públicos son copias expresamente publicadas, independientes de los
espacios privados; nunca se entrega la sesión del preparador.

Una preparación incompleta publica sólo aprobados y diagnósticos de rechazos,
declara `completo=false` y termina con código 2. Los fallos técnicos no se
convierten en rechazos ni en ejemplos aprobados. Un catálogo completo existente
no puede sustituirse por uno parcial.

## Verificación realizada

Consultar [el registro de la ejecución real](evidencia/issue-55/ejecucion.json)
y [la captura de las flashcards](evidencia/issue-55/flashcards-reales.png).
La revisión de interfaz se hizo desde otra sesión anónima usando OCI real.

La primera preparación **no completa el issue 55**: A y C se aprobaron; otros
escenarios fueron rechazados o fallaron técnicamente. El ensayo de tutorial
profundo sobre el glosario de pagos fue aprobado, por lo que no demuestra el
rechazo intencional por evidencia insuficiente. No se alteró su resultado.
Los documentos actuales no identifican una sección débil diseñada para ese
ensayo. Quedan por obtener los demás aprobados, completar los complementos y
verificar la publicación en el despliegue público. Los umbrales siguen vigentes.

Los snapshots de resultados no habilitan generar contenido nuevo desde los
IDs compartidos de la biblioteca documental del #49. Esa integración continúa
fuera de este cambio.
