# Modos de generación — complemento 33.1

Decisión autorizada el 16/10/2026. Amplía el plan existente y sus contratos;
conserva los 60 issues originales, el multiagente y los controles compartidos.

## Comportamiento acordado

El documento se prepara una vez: extracción, fragmentación, embeddings e
interpretación visual necesaria. Ambos modos reutilizan su índice compatible.
La elección se aplica a cada nueva adaptación y no modifica el documento ni
los materiales anteriores. Cambiar el selector no llama al generador.

- **Multiagente**, predeterminado: Supervisor → Researcher → Writer → Critic,
  hasta tres redacciones, y Finalizer determinista. Conserva todos sus bloqueos
  factuales, pedagógicos y visuales. Un fallo del evaluador nunca activa el básico.
- **Básico**: validación/autorización, recuperación RAG y una redacción del Writer,
  sin invocar LangGraph ni Critic. Valida JSON, formato, metadatos y localización
  de citas; eso no demuestra respaldo semántico ni calidad pedagógica. Un material
  inválido falla sin exponer el borrador. Nunca inventa un score ni una aprobación.

En ambos sentidos, cambiar de modo y pulsar Generar crea otro `generation_id`
con su propia clave idempotente. Se repiten recuperación y generación, no la
ingesta completa. Abrir un resultado guardado no vuelve a llamar al LLM.
Revisar/mejorar un básico existente queda fuera de este complemento.

## Contrato HTTP y compatibilidad

`POST /api/generate` admite `modo_generacion`: `multiagente` o `basico`.
Se conservan endpoint, 202, URLs de estado/eventos/cancelación y autorización.

- Omitir el campo mantiene el comportamiento multiagente y el paquete **1.0**.
  El cuerpo canónico de idempotencia omite el valor multiagente predeterminado,
  por lo que explicitarlo no rompe claves anteriores. Una repetición puede
  recuperar el paquete 1.0 existente: siempre representa el mismo modo.
- Explicitar un modo produce un paquete **1.1** en una nueva generación.
  `modo_generacion` identifica el modo; los cinco formatos pedagógicos conservan
  su estructura. Los lectores admiten 1.0 y las dos variantes 1.1 estrictas.
- Multiagente 1.1 conserva `status=aprobado`, evaluación aprobada y modelo juez.
- Básico 1.1 usa `status=no_evaluado`,
  `evaluacion_calidad.estado_evaluacion=no_realizada`. Score, cantidades,
  dimensiones pedagógicas, comprobación visual y modelo de verificación son
  `null`. `no_realizada` es una elección explícita, distinta de `no_evaluable`
  por fallo técnico. No admite datos de aprobación ni métricas inventadas.
- `completed` continúa exigiendo escritura confirmada. En básico significa
  generación estructuralmente válida y guardada, sin evaluación independiente.
  El quiz sigue proyectando la vista estudiante sin claves ni justificaciones.
- Mismo contenido/parámetros y clave recuperan la misma intención; cambiar de
  modo con esa clave devuelve 409. La interfaz crea otra clave para otra petición.

Historial, comparación, recuperación con código y recuperación desde almacenamiento
conservan el modo. Los paquetes 1.0 se reconocen como multiagente. No se restauran
tokens ni trabajos pendientes desde OCI. Las demos publicadas siguen exigiendo
material aprobado; el básico no se publica como ejemplo aprobado.

## Presentación y exportaciones

Selector ES/EN/PT junto a los parámetros, con multiagente predeterminado.
El básico informa que no tiene evaluación independiente; no muestra sello de
revisado, score ni cantidades ficticias. El resultado usa el modo guardado,
independientemente del selector actual. El historial y la comparación lo muestran.

JSON conserva la variante completa. Markdown/PDF 1.1 incluyen la etiqueta de
generación; CSV/TSV/APKG la incluyen en el frente de cada tarjeta. Los bytes de
las vistas derivadas 1.0 se conservan para no invalidar exportaciones históricas
almacenadas. Cada generación mantiene su identidad Anki independiente.

## Límites y verificación

Se reutilizan worker, permisos, cobertura RAG, presupuesto de evidencia,
cancelación cooperativa, deadlines, cuotas por modelo, reintentos técnicos
centrales y controles de almacenamiento. El básico limita a una redacción;
los reintentos técnicos y embeddings siguen pudiendo consumir solicitudes.
Menos agentes no garantiza un porcentaje fijo de ahorro ni una latencia concreta.

El guardado fallido retiene el material estructuralmente válido hasta 24 horas;
reintentar persistencia no vuelve a llamar al LLM. La tabla histórica
`approved_outputs` conserva su nombre y aloja ambas variantes validadas, ligadas
al modo de su solicitud. No se relajan los requisitos de aprobación multiagente.

Pruebas con dobles explícitos: cinco formatos, ambos sentidos de cambio de modo,
contrato anterior, idempotencia, ocultación de soluciones del quiz, falsificación
de métricas, cancelación, persistencia y recuperación. #51/#53 deben contemplar
ambos recorridos; #60 conserva sus 12 casos y revisores, y cualquier comparación
adicional debe identificar el modo. #46/#58 pueden continuar en paralelo.

La comprobación comparativa con Gemini/OCI reales, consumo y latencia se realiza
en #133 con autorización específica. Esta implementación y sus pruebas simuladas
no acreditan ese resultado ni el despliegue público.


## Extensión a chat y glosario — 26/10/2026

`POST /api/chat` y `POST /api/glossaries` aceptan `modo_generacion` opcional,
con `multiagente` predeterminado. El selector de cada panel permite elegirlo.
El básico ejecuta Supervisor → Researcher → Writer, valida estructura y citas,
y revalida las fuentes antes de entregar. Una respuesta o definición con contenido
usa `estado=no_evaluado` y `evaluacion_calidad=null`; el glosario además conserva
`trazabilidad=null`. La UI muestra el aviso de ausencia de revisión independiente.
Las abstenciones conservan sus estados anteriores. No se atribuye aprobación ni
score al básico. La caché del glosario e idempotencia incluyen el modo; cambiarlo
crea otra solicitud. El contrato de paquetes pedagógicos 1.0/1.1 no cambia por
esta extensión. [Revisión y validación](revision-pr-150.md).
