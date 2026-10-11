# Ciclo de vida documental interno — Issue 19, etapas 3–5B

`crear_servicio_ingestion(db, storage, gestor, indice)` compone la admisión
confirmada con el procesador textual. Recibe las instancias existentes; no
crea SDKs, gestores, colas ni ejecutores adicionales. Los routers de etapa 4
reutilizan estos servicios; sus precisiones públicas requieren contract-change.

El procesador conserva la configuración del manifiesto y usa `parsear_archivo`,
`trocear`, `GeminiEmbeddings` y `VectorStoreChroma`. Los embeddings pasan por las
cuotas y el contexto del worker. La reutilización mantiene identidades distintas
y exige compatibilidad de contenido, trazabilidad y colección en el mismo espacio.

Antes de publicar se verifican ownership, trabajo vigente, lápidas, ETag, SHA-256
del original e inventario completo de chunks. Las escrituras del manifiesto usan
`if_match` y lectura de comprobación. Chroma confirma una versión completa antes
de activar su puntero; su algoritmo no cambia.

SQLite conserva `processing` hasta confirmar, en una misma transacción,
`completed`, resultado, evento y estado documental. El resultado interno validado
es evidencia del procesador, no una nueva respuesta documental HTTP. La extracción
con visión pendiente termina su trabajo textual y conserva el documento
`processing`, el original, configuración, metadata y cobertura para Issue 30.

Ante fallo o cancelación, el mismo commit SQLite del terminal reserva sus efectos
en `document_cleanup`. El observador síncrono ejecuta I/O fuera del lock del
gestor y sin transacciones SQLite abiertas. El sincronizador de manifiestos ya
existente reintenta los efectos pendientes cada 60 segundos y al iniciar la app;
las compensaciones no ejecutan embeddings; los nuevos trabajos de
reconstrucción H02 se despachan al mismo gestor y usan las cuotas del worker.

`reservada` identifica compensación del índice y sincronización del diagnóstico;
en H02 puede conservarse un índice anterior compatible.
`pendiente` identifica una admisión fallida o una retirada cuyo original y manifiesto se purgan.
Un ack condicional usa `solicitado_en` para no completar una solicitud posterior.
Una retirada solo se confirma después de comprobar catálogo, chunks físicos y,
si corresponde, ausencia de los objetos. Los fallos conservan intentos y próximo
reintento, sin registrar mensajes privados del proveedor.

Con SQLite conservada, las admisiones encoladas incompletas se acreditan desde
su intención local y el hash del original. Un trabajo de ingestión ordinaria
fallido/interrumpido sigue fallido aunque OCI haya publicado ready: se retira su
índice y se sincroniza failed. H02 conserva la fuente recuperable y, si es válido,
el índice anterior compatible, según las garantías descritas más abajo.
Un reinicio conserva la política del gestor: queued/running pasan a INTERRUPTED;
no se recuperan funciones ejecutables ni se infiere éxito desde OCI.
Las lápidas locales o remotas impiden reactivación; una versión OCI más reciente
no se reemplaza para efectuar limpieza. Sin un índice configurado, los efectos
quedan pendientes y nunca se acredita una limpieza que no se pudo verificar.

El cierre espera al worker y observadores antes de cerrar sus recursos. Si stop
falla, conserva SQLite, storage e índice abiertos; no mata hilos en vuelo. La
espera no tiene límite en lifespan: depende de que las llamadas en vuelo retornen.

## Revisión pendiente y límites vigentes antes de cerrar Issue 19

- Revisión contract-change de las precisiones HTTP de etapa 4: 503 no acredita
  fracaso definitivo ni recuperación sin clave. Las precisiones de DELETE de
  etapa 5B (202 pendiente, 204 verificado, repetición 404) también requieren revisión.
- Verificación y aprobación del contrato público antes de cerrar el issue.
- Tras pérdida completa de SQLite, H02 restaura identidad y fuente desde OCI
  mediante procedencia verificada, que autoriza listado/detalle bajo sesión y
  workspace válidos. Fuentes y ready requieren la confirmación de un nuevo
  trabajo real de reconstrucción; visión pendiente conserva processing.
  No se recuperan jobs/eventos históricos, claves de idempotencia, contadores
  perdidos ni mutaciones que nunca alcanzaron OCI. No confundir esta recuperación
  mínima con un reinicio con SQLite conservada ni con una transacción distribuida.
- Si SQLite no permite registrar el fallo secundario de una admisión en curso,
  la reserva conserva seguimiento; su clasificación como interrumpida exige
  reinicio. No se declara fallido al propietario mientras pueda seguir activo.

La cancelación es cooperativa entre etapas y llamadas: el parser conserva su
presupuesto de extracción y no se introduce un ejecutor alternativo para abortarlo.
Issue 30 sigue fuera del alcance: no se interpreta ni rasteriza contenido visual.

## Retirada de etapa 5B

DELETE confirma lápida local y obligación en una transacción. El mismo efecto
durable incluye documentos ready/completed y heredados sin job. Invalida un ack
anterior y solicita cancelación en el gestor existente. Una admisión con escritura
en curso (migración 7) o un trabajo activo impide acreditar limpieza completa;
el reinicio libera la marca del productor muerto sin volver a encolar.

Antes de purgar fuentes se conserva la lápida privada del workspace y, si existe,
se confirma borrado=true en el manifiesto documental mediante ETag. Las lápidas
remotas más recientes no se descartan. Se verifican catálogo/chunks y ausencia
de originales, manifiestos, outputs y exports de generaciones vinculadas por
SQLite. Se eliminan payloads/eventos vinculados y referencias explícitas de
progreso, conservando otros documentos y el agregado compartido. No existe
persistencia de chat independiente integrada; jobs sin relación documental
persistida no se atribuyen arbitrariamente. Se conservan registros de control,
idempotencia y lápidas; no se borra demo ni el manifiesto del workspace.

Los fallos de sincronización de workspaces no evitan intentar los efectos
documentales del mismo sincronizador. Sin índice configurado la retirada lógica
sigue siendo válida, pero la limpieza permanece pendiente hasta poder verificarlo.

## Recuperación documental H02

Tras pérdida completa de SQLite, `session/recovery.py` restaura identidad y
fuente desde OCI. Solo acredita documentos nuevos con ambos marcadores de
admisión exactamente true, original de hash correcto, metadata válida y
workspace vigente sin lápidas. La evidencia local distingue procedencia de
procesamiento confirmado; no recrea `document_admissions` ni idempotencia.
Si sobrevive el catálogo Chroma, sus lápidas se leen en modo SQLite read-only,
incluso sin cuotas, y se incorporan al control local antes de restaurar documentos.
Las retiradas aún no reflejadas en OCI usan la sincronización de lápidas existente;
el lector no modifica el catálogo ni abre un cliente Chroma.

Las lápidas también restauran filas de control y la obligación de purga, aunque
el manifiesto documental ya haya sido borrado. Estos registros no habilitan
lecturas ni nuevas ingestiones. Los paquetes canónicos verificables conservan
únicamente su relación con el documento retirado para limpiar outputs, exports
y referencias de progreso. Sin índice configurado la obligación sigue pendiente;
se reintenta al disponer del índice, sin llamar a Gemini ni restaurar jobs.

`EfectosDocumentales`, ejecutado por el sincronizador/lifespan existentes,
reserva durablemente el ID de un nuevo intento antes de despacharlo en
`GestorTrabajos`. Encolar job/evento y activar el intento comparten commit.
Una reserva sin despacho conserva el ID y no consume intentos. Solo existe
un intento en curso por documento y se mantienen los límites del gestor.

Fases: pendiente → reservada → en_curso → confirmada/fallida. Confirmada
significa reconstrucción textual completada por el worker; si hay visión
pendiente el documento continúa processing. Ready exige fuente, hash,
metadata, cobertura, manifiesto OCI y todos los chunks Chroma confirmados.
El documento, el job, el evento terminal y la fase se confirman juntos.

Máximo dos intentos reales por documento con SQLite conservada. Solo
INTERRUPTED puede habilitar el segundo, después de confirmar la compensación.
Cancelación, deadline, cuota agotada y demás fallos no repiten automáticamente
el pipeline completo. Los retries por llamada siguen siendo los del proveedor
y contexto común. Una nueva pérdida total de SQLite también pierde este contador.

Una reconstrucción fallida/cancelada retira derivados incompletos o incompatibles
sin borrar el original ni degradar el manifiesto recuperable a un fallo histórico
del upload. El diagnóstico actual queda localmente en el documento/job. DELETE
tiene prioridad: conserva tombstones y aplica la limpieza documental existente.

No se recuperan jobs/eventos anteriores, sesiones, claves de idempotencia ni
intenciones/cancelaciones/lápidas que nunca alcanzaron OCI. Failed histórico
solo recupera detalle/diagnóstico. Marcadores ambiguos o fuentes inconsistentes
no autorizan acceso. Legacy conserva sus reglas y no recibe acreditación nueva.
Sin cuotas configuradas puede recuperarse listado/detalle; la reconstrucción
queda pendiente. Una colección Chroma de preparación incompatible bloquea su
apertura: no se migra ni cambia silenciosamente en H02.
H02 no reconstruye el consumo histórico de cuotas; solo las nuevas llamadas
pasan por los controles compartidos configurados. La operación debe considerar
el presupuesto restante del proveedor, además del límite de intentos local.

La excepción contractual para listado/detalle restaurados está documentada como
propuesta `contract-change` en `docs/contratos-api.md`; no agrega endpoints ni
campos HTTP. OCI no conserva una transacción distribuida ni un terminal histórico:
un estado remoto ready jamás sustituye la confirmación del nuevo worker.
