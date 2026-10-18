# Evidencia real del smoke #54

Ejecución: **15/10/2026, 03:02 UTC** (15/10, 00:02 de Buenos Aires).
El test `integration_real` pasó: **1 passed**, con mocks desactivados.
El [informe](informe.json) identifica commit, hash del comprobador y árbol limpio.

Se reutilizó la ingesta y generación real del escenario A de la preparación
del [PR #130](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/pull/130):
76,491 segundos, 39/39 afirmaciones respaldadas y revisión visual aprobada.
La generación usó el worker y grafo normales con Gemini; no es JSON redactado
manualmente. Esta corrida posterior volvió a leer original, canónico y
exportaciones reales desde OCI; **no ejecutó otra generación**.

| Comprobación | Resultado |
|---|---|
| Original OCI | Bytes iguales al PDF VCN autorizado; SHA-256 en el informe |
| JSON aprobado | Igual al canónico recuperado de OCI |
| Markdown | UTF-8 con referencias verificadas, incluidos IDs escapados |
| PDF | Cuatro páginas abiertas y renderizadas; revisión visual realizada |
| APKG | ZIP/SQLite legible, cuatro notas y cuatro tarjetas |
| Gemini adicional en esta corrida | 0 solicitudes |
| OCI en la fase medida | 86 `get_object`, 0 escrituras |
| Acumulado mensual OCI al finalizar | 4.025 reservas; límite de usuario 4.500 |

Las validaciones internas vuelven a comprobar permisos y fuente durante las
descargas; esas lecturas también cuentan. Las 86 solicitudes excluyen el
arranque del proveedor y las operaciones de preparación previas. El acumulado
mensual no se presenta como consumo exclusivo de esta prueba. No se alteraron
los límites para ejecutarla. La primera comprobación local detectó un error del
comprobador al interpretar guiones bajos escapados en Markdown; se corrigió y
sus lecturas anteriores permanecen en el acumulado.

Artefactos recuperados:

- [Respuesta de estado](respuesta.json) y [paquete JSON](resultado.json).
- [Markdown](resultado.md), [PDF](resultado.pdf) y [mazo APKG](resultado.apkg).
- [Captura de la revisión del PDF](pdf-verificado.png).

El PDF se abrió con PyPDF y se renderizó con PDFium. El APKG se abrió como
ZIP/SQLite; no se afirma que se haya importado manualmente en Anki Desktop.
La prueba real se hizo localmente; no acredita despliegue en una VM.

**Pendiente de aceptación final (#133)**: captura de los objetos en la consola OCI
y constancia de dos integrantes que presencien/revisen la ejecución. Por decisión
del responsable del proyecto del 15/10/2026, esos puntos se trasladan al
[seguimiento #133](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/issues/133) y no bloquean integrar/cerrar #54 con su smoke automatizado real. No se presentan como ejecutados. No había una sesión OCI en el navegador conectado. Los archivos no contienen API keys,
tokens, códigos de recuperación ni URLs firmadas; sólo identificadores técnicos
de recursos del espacio anónimo de demostración.
