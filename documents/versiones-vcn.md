# Fuente VCN: versión corregida

La versión **v2** corrige los tres hallazgos del seguimiento #117: direcciones
asignables en un /24, cambios de CIDR y configuración pública/privada. Se cotejaron
con [Networking Overview](https://docs.oracle.com/en-us/iaas/Content/Network/Concepts/overview.htm)
y [Changing a VCN's IPv4 CIDR blocks](https://docs.oracle.com/en-us/iaas/Content/Network/Tasks/edit_vcn_cidr.htm).
El diagrama y la autoevaluación mantienen la misma definición que el texto.

[El registro de versión](redes_vcn_oci.version.json) fija el hash, la versión
anterior y las páginas de diagrama y sección insuficiente. Las tablas ajustan sus
textos a las celdas; el PDF se regenera de forma determinista y Git conserva sus
bytes en Windows y Linux. El diagrama sigue en la página 3. «Costos y límites»
ocupa una página separada para poder solicitar ese alcance sin incorporar el
glosario: el rechazo real todavía debe comprobarse, no está garantizado por el título.

## Compatibilidad con evidencia y biblioteca

Los resultados anteriores del #54/#55 conservan su fuente e identificadores;
**no acreditan la versión v2**. El registro distingue el hash anterior guardado en
Git del que usaron las corridas locales: faltaba la regla de PDF binario y Windows
podía cambiar los saltos de línea. No se reescribe esa evidencia histórica.

Antes de usar v2 en una nueva evaluación o demo, preparar un documento con su
hash nuevo y reconstruir sus chunks, embeddings e interpretación visual bajo los
presupuestos actuales. Reutilizar un paquete de v1 como si proviniera de v2 sería
incorrecto. El índice y los ejemplos existentes no se actualizan al cambiar el PDF.

La precarga #49 rechaza sobrescribir un ID demo existente con otro hash o
revertir una lápida. Si ya existe una publicación de v1, detener la preparación y
resolver explícitamente la transición de versión; no borrar sus manifiestos ni
forzar su reemplazo. Una carga privada nueva permite preparar v2 sin alterar v1.
La actualización en OCI y la sustitución de los ejemplos afectados siguen pendientes.
