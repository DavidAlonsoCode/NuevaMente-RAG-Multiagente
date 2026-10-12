# Grafo de generación revisada (#29)

`core/agents/graph.py` compila un `StateGraph` síncrono con el estado Pydantic existente y dependencias de ejecución externas. La ruta es Supervisor → Researcher → Writer → Critic. Critic vuelve a Writer para correcciones, a Researcher si falta evidencia, o a Finalizer únicamente tras aprobar. Una salida inválida termina en fallo explícito; el límite de recursión es una segunda barrera, no el contador pedagógico.

Cada nodo comparte el mismo `ContextoEjecucion` del worker #20. Writer conserva el límite acumulado de tres redacciones, incluso cuando corrige formato. Writer y Critic consumen el mismo presupuesto de hasta 20 solicitudes mediante `ctx.llamar`, incluidos los reintentos técnicos. Los embeddings conservan sus cuotas independientes, conforme al contrato de proveedores. Deadline, cancelación y fallos técnicos no se convierten en veredictos de calidad.

`finalizer.py` copia el contenido y la metadata ya revisados, con sus citas verificadas. Agrega únicamente versión del schema, IDs, fecha, trazabilidad y referencia canónica del objeto. Revalida acceso, versión, alcance y revisión antes de persistir. El callback síncrono de persistencia debe confirmar la escritura; una respuesta pendiente o un fallo de storage produce `failed/STORAGE_UNAVAILABLE`. Solo la confirmación habilita `completed` y el paquete del resultado.

`ejecutar_en_worker` traduce los terminales del grafo al gestor común: rechazo de calidad, cancelación y fallo técnico conservan su significado y no devuelven borradores. El consumidor #31 conserva el aprobado para reintentar únicamente persistencia. El gestor registra eventos de paso e intento mediante el contexto; Writer emite uno por redacción real, sin confundir retries técnicos con redacciones.

La política de `decisiones_proyecto.md` §5.3 y §19.3 prevalece sobre el ejemplo antiguo de la skill pedagógica que proponía publicar un borrador con advertencia después de tres fallos. Aquí tres intentos sin aprobación terminan en `rejected_quality` sin contenido ni exportaciones.

Verificación: `pytest backend/tests/test_graph.py backend/tests/test_writer.py backend/tests/test_critic.py backend/tests/test_jobs_manager.py`. Incluye los cinco formatos, copia exacta del contenido revisado, ciclos de corrección y ampliación, score alto con una falsedad conocida, JSON inválido, presupuesto/retries, deadline, cancelación, cambio de fuente y persistencia no confirmada. La integración HTTP y la retención para reintentar escritura pertenecen al #31.

Referencia de la dependencia: [StateGraph y contexto de ejecución](https://docs.langchain.com/oss/python/langgraph/graph-api). Se fija LangGraph 1.2.14, compatible con Python 3.11 y el contrato Pydantic existente.
