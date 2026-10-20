# Criterios E2E — issue #53

Base: `decisiones_proyecto.md` §12.2, 14 criterios. La suite usa FastAPI,
worker, LangGraph, SQLite y Chroma reales con Gemini y almacenamiento
explícitamente simulados. No mide calidad de Gemini ni acredita OCI público.
La interfaz vigente es React; no se reutiliza evidencia de Streamlit como si
fuera una prueba de esta interfaz.

## Matriz de trazabilidad

Las rutas de prueba siguientes son relativas a `backend/tests/`, salvo indicación.
`integration/` reutiliza el harness de #52 y el aislamiento ampliado por #51.

| §12.2 | Prueba automatizada existente o añadida | Comprobación pendiente |
|---|---|---|
| 1. PDF/MD/TXT, corrupción y límites | `integration/test_frontend_upload.py`; `test_document_http.py::{test_archivo_invalido_no_admitido,test_limites_antes_de_storage_y_trabajo,test_fallo_parser_trabajo_failed_y_error_seguro}` | Repetición pública en #133 |
| 2. Evidencia textual y visual localizada | `integration/test_e2e_critical.py::test_tres_adaptaciones_multilingues_recuperan_historial_citas_y_progreso`; `test_multimodal.py::test_vcn_ready_solo_con_vision_fuente_original_filtros_y_aislamiento` | Contraste humano de imagen y fuente en #56; proveedor real en #133 |
| 3. Cinco formatos y contrato | `integration/test_contract.py::test_upload_ready_generacion_exportacion_y_sse` (cinco casos); `test_generation_contract_integration.py` (20 cruces ya existentes) | Legibilidad en #56; ejecución real en #133 |
| 4. Tres adaptaciones de una fuente, ≥2 perfiles y formatos | `integration/test_e2e_critical.py::test_tres_adaptaciones_multilingues_recuperan_historial_citas_y_progreso` (mismo ID/hash, tres perfiles/formatos/idiomas) | Recorrido humano M1 en #56 |
| 5. Recuperar historial/progreso por código | Mismo recorrido: revocación, token nuevo, tres contenidos iguales, progreso conservado y cero llamadas adicionales | M2 en #56 |
| 6. Aislamiento por ID manipulado | `integration/test_contract.py::{test_dos_espacios_recursos_ajenos_404,test_endpoints_comunes_consultan_sse_citas_y_aislamiento}`; fuentes/imágenes en `test_multimodal.py` | Aceptación pública en #133 |
| 7. Tercera redacción, evaluador insuficiente/caído | `integration/test_contract.py::test_terminales_no_aprobados_no_publican`; `integration/test_e2e_critical.py::test_evaluador_insuficiente_no_publica_ni_reinicia_redaccion` | Anotación pedagógica independiente en #60; proveedor real en #133 |
| 8. Quiz y feedback | `integration/test_contract.py::test_quiz_y_progreso_repetidos_sin_cuota_ni_duplicados`; recorrido nuevo de recuperación; `frontend/src/flows/quizRetry.test.tsx` | Plausibilidad real de distractores en #60; interacción M1 en #56 |
| 9. Exportaciones abribles | `integration/test_contract.py::test_upload_ready_generacion_exportacion_y_sse`; CI `verify_pdf_exports.py` y `verify_anki_desktop.py`; navegador `integration/test_react_caddy.py::test_basic_and_multiagent_reuse_document_without_relabeling_results` | Legibilidad PDF/importación manual M4 en #56; consumo real en #133 |
| 10. OCI real, original y paquete recuperables, sin fallback | `integration/test_contract.py::test_persist_503_concurrente_recupera_aprobado_sin_llm` verifica recuperación ante fallo con doble; `integration/test_smoke_real.py` está separado con `integration_real` | **OCI real se acepta en #133**; el doble no satisface este criterio |
| 11. SSE e idempotencia | `integration/test_sse_tcp.py`; `integration/test_contract.py::{test_idempotencia_concurrente_en_curso_y_conflicto,test_quiz_y_progreso_repetidos_sin_cuota_ni_duplicados}`; reconexión por cursor en el recorrido de cinco formatos | Proxy público/proveedores reales en #133 |
| 12. Cancelar/reiniciar/borrar sin resurrección | `integration/test_contract.py::test_cancelacion_no_publica_ni_repite_proveedor`; `test_workspace_recovery.py::test_lapidas_de_documento_generacion_y_progreso_no_reaparecen` (nueva SQLite, storage conservado) | Reinicio de servicios/VM en #133 |
| 13. ES/EN/PT, acentos y citas | Recorrido nuevo de tres adaptaciones: parámetros, contrato, título UTF-8 y ubicación acentuada recuperada; `frontend/src/flows/documentUpload.test.tsx` y `frontend/src/i18n/catalogs.test.ts` | Traducción/calidad real en #60; M1/M3 en #56 |
| 14. Teclado/foco/contraste/pantalla estrecha | Navegador Caddy: recorrido de descargas a 320 px; pruebas React de etiquetas y navegación | **Inspección humana completa M3 en #56**; no se deduce contraste/foco de JSDOM |

La extracción/jueces de los dobles emiten respuestas programadas. Un contrato
válido, una cita resoluble y un juicio simulado no demuestran exactitud factual,
traducción correcta ni utilidad pedagógica. Los casos de negación, unidades,
contradicciones y contexto insuficiente siguen en `test_faithfulness.py`;
no se duplican ni sustituyen las anotaciones de #60.

## Ejecución reproducible sin credenciales

Desde la raíz, con las dependencias fijadas instaladas:

```powershell
.venv\Scripts\python.exe -m pytest -m "e2e and not integration_real" --basetemp=.data/issue53-corrida-nueva --junitxml=.data/issue53-e2e.xml
```

Usar una carpeta temporal nueva por corrida. `conftest.py` aísla el `.env` y
fuerza mocks antes de importar la aplicación; cada fixture inyecta sus dobles.
Cada E2E declara `e2e` y exactamente una marca de proveedor. La colección falla
si falta esa declaración o se mezclan `integration_mock` e `integration_real`.

CI ordinaria ejecuta estos casos por `integration_mock` y archiva el JUnit de
la suite general junto al de integración #52, en `integration-mock-report`.
No se agrega otra ejecución completa: se reutilizan las selecciones existentes.
Los navegadores reales/Caddy y PDF/Anki Desktop conservan sus jobs o pasos
específicos y sus dependencias; no son prerrequisitos del comando local anterior.

Los casos nuevos añaden tres adaptaciones persistidas de una fuente y tres
fallos del evaluador: sin afirmaciones (tres redacciones, rechazo), respuesta
vacía (fallo técnico), indisponibilidad (tres intentos técnicos, una redacción).
Todos comprueban que no se publica ni exporta un borrador, y que repetir la
intención terminal no vuelve a consumir el proveedor.

## Validación local del 18/10/2026

Windows, Python 3.11, base `95bba4b` de `origin/develop`: 45 E2E aprobados,
0 fallos/errores/omitidos, 174,68 s. JUnit local en
`.data/revision-53-20261018/e2e.xml`; la CI conserva su propio reporte del
commit del PR. Ruff pasó. Dos sondas de colección independientes confirmaron
exit 4 al omitir la marca de proveedor o declarar ambas. No se ejecutaron
proveedores reales, revisión visual ni firmas humanas en esta corrida.

## Protocolo manual transferido a #56

**Pendiente de ejecución y firma humana.** Traslado autorizado el 18/10/2026:
#53 entrega protocolo, trazabilidad y automatización; #56 acepta las capturas o
video y las firmas de dos personas. #133 acepta la repetición pública/real.
#60 conserva su muestra pedagógica de 12 casos y sus dos revisores.

Preparación local aislada, usando el servidor de verificación existente:

```powershell
# Terminal 1, raíz. El directorio debe ser nuevo y permanecer dentro de .data/.
.venv\Scripts\python.exe backend/tests/integration/react_backend.py --data-dir .data/issue56-revision-nueva --port 18080 --delay-seconds 1
# Terminal 2, carpeta frontend/; API_URL sólo en este proceso.
$env:API_URL = 'http://127.0.0.1:18080'
npm.cmd run dev
```

Abrir `http://127.0.0.1:8501`. El servidor inyecta dobles, desactiva la carga de
`.env` y bloquea DNS externo. Sus resultados son sintéticos; la firma evalúa el
recorrido de interfaz, no la validez del contenido del LLM. Para un rechazo
controlado, detener el servidor y abrir otro directorio nuevo con
`--reject-quality`; no modificar los umbrales. Detener ambos procesos al terminar.

| Paso | Acción y aceptación | Evidencia requerida en #56 |
|---|---|---|
| M1 | Cargar una fuente pública/sintética; generar tres materiales del mismo ID/hash con ≥2 perfiles y ≥2 formatos, incluidos quiz y flashcards. Alternar ES/EN/PT de interfaz y contenido de forma independiente. Abrir citas y responder el quiz; feedback explicativo inmediato. | Capturas de parámetros, resultados y fuente; registrar ID/hash sin tokens ni códigos. |
| M2 | Guardar el código en privado, cerrar sesión y recuperar por código. Reabrir los tres materiales desde historial y confirmar el progreso anterior. | Capturas antes/después, sin el código ni el token. |
| M3 | Recorrer por Tab/Shift+Tab/Enter/Espacio: carga, parámetros, confirmación, cancelación, resultado, citas, historial y descargas. Verificar etiquetas, orden, foco visible y retorno al cerrar diálogos. Repetir a 320 px sin contenido inaccesible; medir contraste WCAG AA (texto normal ≥4,5:1; grande y controles ≥3:1). | Capturas de foco/móvil, valores de contraste y controles medidos; fallo y repetición tras corregir si corresponde. |
| M4 | Abrir PDF y revisar código, tablas, listas, fuentes y cortes. Importar CSV/TSV/APKG en Anki Desktop y reimportar sin duplicaciones; estudiar flashcards en NuevaMente sin Anki. | Capturas de PDF/Anki, versión del consumidor y resultado de reimportación. |
| M5 | Distinguir multiagente revisado, básico no evaluado, ejemplo precargado y trabajo rechazado/fallido. Para precargados usar catálogo identificado con versión/hash; si no está disponible, registrar pendiente. Verificar que un rechazo nunca muestra el borrador. | Capturas de cada estado y procedencia del ejemplo; no etiquetar un doble como Gemini real. |

Archivar en `docs/evidencia/issue-56/` únicamente artefactos saneados y un acta:
commit probado, fecha, navegador/viewport, sistema, fuente/hash, proveedores,
pasos M1–M5 y resultados/limitaciones, rutas y SHA-256 de capturas/video,
nombre/usuario y fecha de cada uno de los dos revisores. Las autorías Git o
una aprobación automática del PR no constituyen esas firmas.

La repetición en #133 agrega URL/HTTPS, configuración no secreta, OCI/Gemini
reales y consumo autorizado. No ejecutar esa variante sólo por correr #53.
