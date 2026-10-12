# API de generación (#31)

La API usa el StateGraph de #29 y el único `GestorTrabajos` de #20. Admite
documentos propios `ready`, con procedencia, hash, cobertura e índice confirmados.
No guarda tokens de sesión en las funciones del worker.

- `POST /api/generate`: 202 y recibo `GenerationJobResponse`. Idempotencia por
  workspace, operación y cuerpo normalizado; duplicados conservan el ID.
- `GET /api/generations/{id}`: 200 también en `rejected_quality`, sin contenido.
  El aprobado sólo se entrega con escritura confirmada. Quiz usa la vista estudiante.
- `GET /api/generations/{id}/events`: SSE del registro común, heartbeat, IDs
  monotónicos y `Last-Event-ID`. Desconectar no cancela el trabajo.
- `GET /api/generations`: keyset descendente con `limit`, `cursor`, `documento`,
  `perfil`, `formato`, `idioma`, `desde` y `hasta` (fechas con zona horaria).
  El cursor firmado está ligado al workspace y a los filtros.
- `POST /api/generations/{id}/cancel`: cancelación cooperativa del gestor.
- `POST /api/generations/{id}/persist`: mismo aprobado y objeto, cero llamadas
  LLM, retención local de hasta 24 horas desde la primera aprobación. Si se pierde
  el volumen local, este aprobado no publicado no se recupera desde OCI.
- `GET /api/exports/{id}?format=json`: canónico completo autorizado. Los demás
  formatos quedan para #43; aquí responden `EXPORT_INCOMPATIBLE`.

Los proveedores síncronos requieren cuotas explícitas de generación y revisión
del proyecto de Google. Los modelos compartidos usan un único contador y límites
coincidentes con visión. `MOCK_GEMINI` exige inyección explícita; no hay caída a
respuestas inventadas. El proveedor real conserva el control central de retries.

SQLite v9 guarda parámetros de historial y el aprobado inmutable. La fuente se
revalida antes y después de publicar/leer, incluyendo lápidas remotas. Los objetos
conflictivos no se sobrescriben. Un reinicio marca trabajos interrumpidos; nunca
declara `completed` por conservar un borrador o un aprobado local.

`trazabilidad.parser_version` identifica la configuración/versión efectivamente
registrada durante la ingesta como `config-sha256:<huella_config_parser>`. Esa
huella incluye la versión y los límites del parser; no se infiere la versión
desde el código instalado. Una ingesta sin esa identidad no habilita generación.

Verificación automatizada: `backend/tests/test_api_generate.py`, con Chroma,
StateGraph, worker y transporte HTTP reales y dobles explícitos de IA/OCI. La
corrida manual VCN con Gemini y OCI reales sigue pendiente: no se acredita con CI
simulada. Windows bloquea la DLL de una dependencia de Chroma; no se elude esa
política. Las anteriores pruebas reales del #30 no lograron respuesta de Gemini.
