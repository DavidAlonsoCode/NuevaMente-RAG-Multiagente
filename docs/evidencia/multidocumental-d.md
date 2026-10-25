# Escenario D: bytes históricos y evidencia pendiente

Revisión documental local de la PR #146, 24/10/2026. Se conserva sin modificación
el [JSON histórico simulado](salida-multidocumental-d.json), cuyo campo `created_at`
declara `2026-10-22T23:17:35.110550Z`. Esa fecha y los nombres de modelos guardados
no acreditan una ejecución con proveedores reales ni la versión del código usada.
La ejecución real de D requerida por §8.2 del
[plan correctivo](../../planCorrectivo/plan_implementacion_limites_y_multidocumental_revisado%20%281%29.md#82-agregar-un-escenario-d-multidocumental)
**sigue pendiente**, de forma independiente de #133. No se regeneró la salida,
no se consumieron proveedores y no se sustituyeron informes históricos.

## Qué bytes están disponibles

| Dato | Valor observado en el archivo conservado |
|---|---|
| SHA-256 calculado sobre los bytes completos | `0c1f0d62a4090bdef4ffaacfa6db2a50421ffae81c5108a8143465b15b2527dc` |
| `generation_id` | `gen_7c318db5dfdb422f861b3a88ad0155a3` |
| Contrato declarado | `schema_version=1.0`, `status=aprobado` |
| Primario declarado | `doc_a9bfa1fc026e4a269b3ca3a2df427a40`, título «Arquitectura B» |
| Otra fuente declarada | `doc_f72afc95d7944044bb76190ad4a1ecd5`, título «Arquitectura A» |
| Fuentes de todas las citas presentes | Sólo `doc_a9bfa1fc026e4a269b3ca3a2df427a40`: lista superior y única tarjeta |

La huella anterior del README
`9806ff5ddec0e919cb4f10213cfa92cdfe53575700183d8bc296585341c77107`
no corresponde a estos bytes. Se corrige la referencia documental; no se afirma
la existencia de otro archivo que explique esa discrepancia.

## Qué no demuestra

El título contiene `[SOURCE_TITLE]`; aparecen `[SOURCE_CONCEPT]`,
`[LEARNING_OBJECTIVE_FROM_SOURCE]`, `[SOURCE_EXPLANATION]` y `[LABELED_ANALOGY]`.
La única tarjeta no aporta una contribución identificable de Arquitectura A.
Enumerar dos `documentos_fuente` y guardar score 1.0/evaluación aprobada no prueba
que ambas hayan respaldado afirmaciones del contenido. Tampoco un nombre de
modelo, bucket u objeto equivale a constancia de ejecución o escritura real.

La validación del JSON contra el modelo tipado comprueba su estructura; no
ejecuta Critic, no verifica los originales/chunks ausentes y no acredita respaldo
factual, complementariedad, autorización actual, calidad humana ni persistencia OCI.
Por eso este archivo no cierra D, aunque sea legible y estructuralmente válido.

## Cobertura automatizada con dobles

Las siguientes pruebas aprobadas localmente ejercitan controles del código
actual. Sus entradas y respuestas son sintéticas; algunos componentes de
SQLite/HTTP/Chroma/grafo son reales según la fixture de cada suite. Los dobles de
proveedores y de índice/juez se identifican en los propios tests. No constituyen
una ejecución real de D ni una comprobación retrospectiva de su contenido:

| Suite | Comprobación relevante |
|---|---|
| [Integración multidocumental](../../backend/tests/test_multidocument_generation.py) | Paquete con ambas fuentes, citas dentro del contenido y contribuciones respaldadas por ambas según el doble; salida temporal, sin sobrescribir D. |
| [Critic multidocumental](../../backend/tests/test_critic_multidocument.py) | Valida secciones distintas/homónimas y contribuciones A+B; rechaza contenido sólo A y referencias B decorativas. Cubre retirada/cambio de fuente y rechazo terminal sin publicar. |
| [Retrieval multidocumental](../../backend/tests/test_multidocument_retrieval.py) | Base pertinente por fuente, alternativas que caben, deduplicación con procedencia y presupuesto serializado compartido. |
| [Researcher multidocumental](../../backend/tests/test_researcher_multidocument.py) | Cobertura A+B cuando hay evidencia pertinente, rechazo si falta B y acumulación entre rondas dentro del presupuesto. |

Los tests aprobados se reutilizan como cobertura existente: este lote documental
no vuelve a ejecutar la integración ni atribuye sus resultados a proveedores reales.
Los dos timeouts de la suite global de recuperación siguen abiertos, sin causa
demostrada; no se repitió esa suite ni se declararon resueltos.

## Qué falta para una evidencia real

Falta una ejecución autorizada con al menos dos fuentes distintas y pertinentes,
solicitud que requiera contribuciones de ambas, y registro verificable de fecha,
versión de código, modo, proveedores/modelos y resultado. Deben conservarse sus
bytes originales con SHA-256, fuentes y citas comprobables de ambas, más la
visualización de citas y una exportación compatible. Una ejecución nueva requiere
su propia identidad/huella; no debe reemplazar silenciosamente este registro.

A/B/C mantienen un estado diferente: hay informes y
[entradas reconstruidas verificables](issue-57/README.md#entradas-reconstruidas-y-salidas-que-faltan),
pero faltan sus tres salidas originales. Sus hashes esperados son referencias
históricas, no verificaciones de bytes actualmente disponibles. No se reconstruyen
salidas para completar ninguno de estos pendientes.

Las tres entradas disponibles usan LF. Sus SHA-256 calculados en el
[README actual](../../README.md#escenarios-de-evidencia-abc-unidocumentales-y-d-multidocumental)
difieren de `entrada_reconstruida_sha256` del inventario histórico. Se comprobó
que estos últimos coinciden al representar los mismos textos con CRLF, y que el
JSON leído coincide exactamente con `parametros_registrados_en_catalogo`.
Es una comparación local de variantes de bytes, sin modificar los archivos ni
los hashes del informe histórico. No acredita las salidas que faltan.
