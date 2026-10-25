# Tutor documental (#37)

`POST /api/chat` recibe `document_id` o `document_ids`, `pregunta`, `perfil_destinatario` e
`idioma_salida` y devuelve 202 con el recibo común: `job_id`, `status_url`,
`events_url` y `cancel_url`. Consulta, reconexión SSE y cancelación usan
`/api/jobs/{id}` de #20. No hay otra cola; sólo un worker pesado puede ejecutarse.

El resultado contiene `estado`, `respuesta`, `referencias`, `evaluacion_calidad`,
identidad/hash del primario, `document_ids`, snapshot `documentos_fuente` y
parámetros de la pregunta. `respondida` requiere revisión aprobada. Las citas
se consultan en `/api/documents/{doc}/sources/{chunk}`.
Una pregunta fuera de alcance devuelve una abstención fija ES/EN/PT que pide
precisar, sin afirmaciones nuevas, citas ni evaluación de material aprobado.
La respuesta sugerida por el modelo en una abstención se rechaza si contiene texto.

Cada pregunta arranca un EstadoGrafo nuevo. Researcher consulta por pregunta y
sección, con el mismo inventario, cobertura y presupuesto de evidencia de #29.
Sólo los chunks actuales son fuente; el historial se marca como diálogo no
confiable y queda fuera de los prompts de Critic y de las búsquedas de embeddings.
`CHAT_HISTORY_TURNS` (8) y `CHAT_HISTORY_MAX_TOKENS` (2000) acotan los turnos completos
más recientes del mismo workspace y conjunto exacto de fuentes/versiones.
La identidad incluye ID, hash y versión por fuente: documentos distintos con igual
contenido no comparten memoria, A+B/B+A son equivalentes y A+B/A+C no.
Los turnos unidocumentales históricos sin snapshot completo se conservan si su
ID/hash y relaciones verificables acreditan la misma fuente; no se inventan
versiones ni se reescriben registros o hashes idempotentes. Cero desactiva el historial.

El chat utiliza el mismo StateGraph Supervisor→Researcher→Writer→Critic y sus
ciclos de revisión. El adaptador interno de pregunta/respuesta usa una sola
flashcard tipada: el frente conserva la pregunta original como dato no confiable
y el dorso contiene el texto exacto de la respuesta. La revisión pedagógica mide
si esa respuesta contesta la pregunta; no exige adaptar todo el documento.
Las demás etiquetas son constantes localizadas sin hechos nuevos. No genera mazos ni cambia
los cinco formatos canónicos. La entrada real completa, incluido historial, schema
e instrucciones, se cuenta antes de reservar cuota en el contexto compartido.

Se exige la política completa: citas/ubicaciones, fuente vigente, cobertura,
fidelidad ≥0.85 y ninguna afirmación conocida sin respaldo, rúbrica pedagógica,
original visual cuando corresponde, hasta tres redacciones y veinte solicitudes
de generación/revisión (incluidos retries). La barrera determinista de Finalizer
revalida el aprobado antes de proyectar su respuesta exacta. Este flujo sólo
revisa: no llama al finalizador de persistencia ni inventa confirmación OCI.
El resultado se registra en SQLite por el gestor común, como el resto de sus
trabajos. No publica paquetes en `outputs/` ni tiene exportación pedagógica.

Las lecturas del resultado vuelven a verificar todas las fuentes guardadas contra
la autorización vigente. La ruta SSE pasa `sesion["token"]` antes de enviar headers
y durante el seguimiento, al igual que la consulta HTTP. El servicio admite además
una validación interna opcional sin token; no es el camino utilizado por esa ruta.
Un cambio o retirada de una secundaria bloquea el resultado
anterior aunque siga vigente el primario. SSE conserva su contrato de progreso y
errores; nunca transmite la respuesta educativa.
La retirada documental cancela trabajos vinculados y elimina sus resultados/eventos,
por lo que también elimina el historial del chat vinculado. Los turnos con snapshot
se excluyen si cambia la identidad, hash o versión de una fuente; el legado sin
snapshot sólo acredita su ID/hash, como se indicó antes. La vigencia del workspace
y cancelación se comprueban antes/después de I/O y de las solicitudes del proveedor.

La lista superior se normaliza ordenada y sin duplicados, con máximo cinco
fuentes. El primer ID es el primario canónico y `document_id`, si se envía,
debe coincidir. El foco visual no participa de esa identidad. La UI usa el mismo
primario al admitir, reintentar y validar resultados inmediatos o consultados;
descarta respuestas tardías de otro contexto. La clave idempotente conserva
las formas unidocumentales históricas y las ya persistidas con `document_ids`,
mediante comparación estricta, sin convertir cambios reales en duplicados.
El cuerpo de chat no admite `alcance`; no existe «todo el espacio» implícito.
Ver [reglas compartidas de la PR #146](contratos-api.md#fuentes-alcances-y-procedencia-multidocumental-pr-146).

Verificación: `backend/tests/test_chat.py` con dobles explícitos y componentes
HTTP/Chroma/LangGraph/worker reales. Incluye ES/EN/PT, cita consultable, abstención,
historial contaminado, cuota compartida, corrección de JSON, tres rechazos por
rúbrica/cita o una contradicción con score 0.90, cancelación en espera y borrado.
La preparación del #55 verificó una respuesta real sobre el diagrama de VCN v2,
con Gemini, Chroma y revisión visual contra el original. Las pruebas con dobles
siguen siendo simuladas y no sustituyen esa evidencia.

Regresiones aprobadas de la PR #146, con dobles explícitos:
[autorización de todas las fuentes](../backend/tests/test_chat_source_authorization.py),
[identidad del historial](../backend/tests/test_chat_history_identity.py) y
[primario/contexto en React](../frontend/src/flows/chatContext.test.tsx).
Estas referencias identifican cobertura automatizada; no son ejecuciones reales
nuevas ni evidencia de D.

Este PR apunta directamente a `develop`. Reutiliza el arranque de proveedores,
cuotas, consulta documental y relación documento/trabajo del #31, que debe estar
integrado en `develop` antes de incorporar el chat. Todos los PR de issues se
dirigen a `develop`, con ramas descriptivas `feat/`, `fix/` o `docs/`.
