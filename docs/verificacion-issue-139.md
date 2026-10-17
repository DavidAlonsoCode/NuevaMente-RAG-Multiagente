# #139 — Retiro de Streamlit y carga visible en React

El frontend vigente es `frontend/` (React, TypeScript y Vite). Se retiran
`frontend_streamlit_viejo/`, la configuración raíz `.streamlit/`, sus dependencias
de CI y sus pruebas exclusivas. El historial de Git conserva el código anterior.
Las copias personales ignoradas por Git no forman parte del despliegue.

La carga PDF/MD/TXT se encuentra en el área principal, a la derecha del sidebar
en escritorio. En móvil sigue el apilado del layout existente. Texto pegado,
biblioteca, límites, permisos, claves idempotentes y seguimiento documental
conservan el flujo previo. Desde el resultado se vuelve a Documentos para cargar
otra fuente. El sidebar mantiene los parámetros de la próxima adaptación.

## Cobertura vigente

| Control anterior | Verificación de la implementación actual |
|---|---|
| Carga, formatos, permisos y reintento | `frontend/src/flows/documentUpload.test.tsx`: carga principal ES/EN/PT, archivo inválido y misma intención tras fallo de almacenamiento |
| Catálogos traducidos | `frontend/src/i18n/catalogs.test.ts`: paridad de claves y variables |
| Veinte cruces de perfil y formato | `backend/tests/test_generation_contract_integration.py`: HTTP, worker, grafo, Chroma y almacenamiento, alternando idioma/alcance e idempotencia |
| Quiz con respuesta perdida | `frontend/src/flows/quizRetry.test.tsx`; corrección y métricas en `backend/tests/test_api_quiz_progress.py` y `test_quiz_progress.py` |
| Tutor, glosario, historial y fuentes | `frontend/src/flows/regressions.test.tsx`, `backend/tests/test_chat.py`, `test_glossary.py` y `test_api_generate.py` |
| Progreso y estudio | Estado de estudio y renderizadores React; idempotencia, recuperación y agregados en las pruebas de API/quiz/progreso del backend |
| Generación, SSE, cancelación y persistencia | `frontend/src/flows/generationProgress.test.tsx`, `api/sse.test.ts` y pruebas de API/trabajos |
| Proxy y navegador | `backend/tests/integration/test_caddy_proxy.py` y `test_react_caddy.py`: React compilado, TLS local, HTTP/1.1 y HTTP/2, SSE, permisos, secretos y descargas |

El harness de Caddy usa únicamente React. La comprobación del WebSocket de
Streamlit se reemplaza por interacción, recarga e idioma conservado, mientras las
pruebas de SSE verifican reconexión y cursor. Se mantienen la prueba de silencio
mayor de 120 segundos, subida lenta, auditoría de cabeceras/logs y diagnósticos
cerrados. No se cambian los timeouts ni las defensas del proxy de producción.

Los tests exclusivos de widgets/controladores retirados dejan de contarse en
pytest. Esa reducción no representa fallos corregidos ni valida React por sí
sola: las pruebas de la tabla cubren las fronteras y el frontend vigentes.

## Validación local

El 17/10 se aprobaron 90 pruebas React, su build de producción, 134 pruebas
dirigidas de backend y tres casos multipart contra la API. Ruff, formato,
versiones fijadas y comprobaciones OpenAPI/referencia HTTP pasaron. La colección
ordinaria de pytest no presenta errores de importación. Estos resultados no
afirman una corrida completa local de pytest.

## Reproducción y límites

Desde la raíz, con las dependencias instaladas:

```powershell
npm --prefix frontend test
npm --prefix frontend run build
.venv\Scripts\python.exe -m pytest backend/tests/test_generation_contract_integration.py backend/tests/test_caddy_smoke.py backend/tests/test_smoke.py --basetemp=.data/139-verificacion-nueva
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe .github/scripts/check_pinned_requirements.py
.venv\Scripts\python.exe .github/scripts/export_openapi.py --check
.venv\Scripts\python.exe .github/scripts/export_openapi.py --validate
```

Las pruebas ordinarias aíslan `.env` e inyectan proveedores simulados. Caddy y
los contenedores se verifican con sus herramientas reales en CI, con proveedores
dobles; no equivalen a OCI/Gemini reales, VM ni HTTPS público. Esa aceptación
permanece en #133. Las evidencias antiguas conservan fecha, hashes y alcance.
No se agregan los comentarios explicativos opcionales del issue.
