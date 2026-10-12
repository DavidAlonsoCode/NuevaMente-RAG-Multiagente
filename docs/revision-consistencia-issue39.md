# Revisión de consistencia junto al issue 39

Base revisada: `develop` en `d9563a5` (incluye #35). Implementación y correcciones
comparten `feat/issue-39-adapted-glossary`; el PR se integra directamente en `develop`.

## Alcance y resultado

Se contrastaron decisiones, plan y contratos con los recorridos de sesiones,
admisión/cola, ingesta y cobertura, índice/retrieval, Writer/Critic/Finalizer,
generaciones, chat, glosario, quiz/progreso, almacenamiento/recuperación, cliente
HTTP y sesión de Streamlit, configuración, Compose y CI. Se verificaron sintaxis
Python, enlaces documentales locales, versiones fijadas y compatibilidad de las
dependencias instaladas. La suite completa y las imágenes se verifican en CI Linux.

## Hallazgos corregidos

| Hallazgo | Efecto antes del cambio | Corrección y verificación |
|---|---|---|
| Chat ignoraba `Idempotency-Key` | Un reintento podía intentar crear otro trabajo, contradiciendo el contrato | Admisión común serializada, conflicto 409 y recuperación de la intención; tests HTTP y concurrencia |
| Lecturas aprobadas dependían de configurar proveedores LLM | Historial, JSON exportado o una respuesta guardada podían devolver 503 sin necesitar IA | Servicios de lectura disponibles y autorización por fuente READY; nuevas generaciones siguen exigiendo proveedores; regresiones HTTP |
| Vínculo de caché del glosario se escribía después de admitir el trabajo | Un fallo SQLite entre escrituras podía dejar un trabajo aceptado sin caché | Clave, trabajo y evento se aceptan en la misma transacción; prueba con fallo SQL inyectado |
| CI admitía bases `feat/**` | Persistía una excepción del flujo de PR encadenados | Workflows limitados a `develop`/`main`, acordes a la guía |
| README refería a `AGENTS.md` inexistente | Enlace roto y fuente de autoridad equivocada | Referencias a decisiones, schemas y guía; los ejemplos de skills quedan identificados como orientativos |

## Verificación del glosario

`test_glossary.py` recorre el PDF VCN real con proveedores simulados, citas por
fragmento/página/sección, perfiles/idiomas, traducciones y nombres protegidos,
limitaciones parciales y completas, caché/versiones/modelos, idempotencia,
cancelación, cuotas y retirada documental. `test_glossary_models.py` comprueba
candidatos y ubicaciones sin definir contenido durante la ingesta.

El glosario usa la revisión existente con hasta tres redacciones. Cada definición
debe aparecer en la descomposición factual; un juez incompleto produce fallo
técnico, no aprobación. La caché no crea trabajos ni consume llamadas.

## Límites de la revisión

La UI de estudio y sus paneles siguen las tareas pendientes del plan (#36, #38,
#40, #41 y otros formatos/exportaciones). Sus placeholders no se presentan como
funcionalidades terminadas. La revisión automática con mocks no acredita cuotas,
credenciales, disponibilidad ni calidad de los proveedores reales. En Windows,
WDAC bloquea una DLL de Chroma; los flujos completos se validan en Linux CI.
