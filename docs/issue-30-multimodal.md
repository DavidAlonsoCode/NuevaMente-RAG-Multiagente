# Issue 30: ingestión y verificación visual

Implementa §4.2 y §19.2 de decisiones_proyecto.md sobre el worker y los contratos de #19/#28.

## Contrato y recorrido

1. El parser existente detecta imágenes, gráficos vectoriales y escaneos, con un máximo de 20 páginas visuales y 100 páginas totales. El original permanece inmutable.
2. PDFium rasteriza cada página en un proceso acotado en tiempo, memoria (Linux), píxeles y bytes. Cancelación/deadline terminan ese proceso; no se ejecuta otro trabajo pesado de la cola.
3. Gemini interpreta la página mediante el transporte síncrono existente y `ctx.llamar`, con cuotas centrales por modelo, timeout y reintentos del gestor. La respuesta estricta distingue interpretación, ambigüedad, ilegibilidad y cobertura incompleta.
4. Las interpretaciones se persisten como datos separados en el manifest: documento/hash, página, referencia a la página completa, hash del PNG, modelo y versiones. Chroma recibe chunks de tipo `image_description`, distintos del texto PDF, con citas a la página original. La reconstrucción reutiliza únicamente interpretaciones cuya procedencia y configuración vuelven a verificarse.
5. Una omisión conserva cobertura y diagnóstico, termina en `failed` y permite una nueva carga. Sin configuración visual explícita, el documento sigue `processing`. Sólo cobertura completa e índice confirmado permiten `ready`.
6. «Ver la fuente» distingue interpretación del modelo y devuelve una URL autenticada a la imagen. El servidor vuelve al original, verifica su hash, rasteriza y revalida acceso antes de entregar PNG sin caché.
7. El loader síncrono de Critic usa el mismo original autorizado y queda ligado al contexto del trabajo. Una interpretación ambigua o una afirmación sin respaldo visual produce `verificacion_visual=insuficiente` aunque la evaluación textual sea alta. Las comprobaciones consumen el presupuesto existente del grafo.

## Compatibilidad y configuración

Se conservan IDs/chunks y respuestas textuales existentes. Los campos públicos nuevos son aditivos. No se cambia el presupuesto de 20 revisiones/redacciones ni se inventan cuotas de Gemini. La visión usa `GEMINI_GENERATION_MODEL` y `VISION_QUOTA_RPM/TPM/RPD`; si comparte modelo con otros consumidores, comparte el contador y sus límites.

`GET /api/documents/{id}/sources/{chunk_id}` agrega `source_type`, `interpretacion_modelo` e `imagen_url`. El tipo `image_description` conserva `pagina` y `seccion_id=pagina:N`; una referencia nula no autoriza inventar una ubicación. `GET …/sources/{chunk_id}/image` devuelve PNG autenticado con `Cache-Control: no-store`; otro espacio o recurso retirado recibe 404. Un chunk textual no habilita una imagen. La UI debe solicitar la imagen con la misma autenticación que la fuente.

Los tokens documentales incluyen el texto extraído y las interpretaciones indexadas; el límite combinado sigue siendo 100.000 BPE. El original y las interpretaciones tienen procedencia diferenciada, aunque ambos alimenten el mismo índice filtrable por `tipo_fuente`. El filtro mantiene workspace, documento y versión del índice.

`GENERATION_THINKING_LEVEL=low|medium|high` es opcional y se envía sólo a modelos Gemini 3; conserva compatibilidad con transportes de modelos anteriores. El modelo 2.5 configurado por defecto puede devolver 404 para proyectos nuevos: verificar disponibilidad de generación en la cuenta, no solamente `models.list`. Para una configuración nueva se puede seleccionar un modelo vigente explícitamente, con sus cuotas verificadas.

El bootstrap de producción crea un cliente real sólo cuando existen cuotas visuales. En tests se inyecta `proveedor_vision` síncrono y `mock_gemini=True`; no hay proveedor falso implícito. El grafo #29 recibe `consulta.loader_critic(contexto)` en `DependenciasCritic.obtener_imagen`.

## Verificación

`pytest backend/tests/test_multimodal.py backend/tests/test_critic.py` cubre PDF vectorial y escaneado, omisiones, procedencia, filtros, permisos, cancelación, cuotas, recuperación y bloqueo visual. La CI completa y ARM64 verifican la integración. La corrida real usa `documents/redes_vcn_oci.pdf` y credenciales locales ignoradas por Git; sus resultados se registran separadamente de las pruebas simuladas.

Referencias técnicas: [PDFium y cierre de recursos](https://pypdfium2.readthedocs.io/en/stable/python_api.html), [imágenes y reserva estimada de tokens Gemini](https://ai.google.dev/gemini-api/docs/image-understanding).

## Corrida real del 11 de octubre de 2026

PDFium produjo la página 3 de `redes_vcn_oci.pdf`: PNG de 1415×2000 píxeles y 209.669 bytes; la topología original es legible. La clave local pudo consultar modelos. Las dos solicitudes autorizadas con 2.5 Flash devolvieron 404. Los dos intentos autorizados con 3.8 Flash y pensamiento bajo no completaron una respuesta válida por indisponibilidad/timeout del proveedor (503); el contador central detuvo nuevos intentos. No se acredita una descripción real ni una ingestión real completada. La corrida Gemini debe repetirse cuando el proveedor esté disponible; las credenciales y artefactos de ejecución permanecen fuera de Git.
