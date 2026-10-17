# Descargas multiformato — compatibilidad con los modos de generación

El PR #138 integra `develop` posterior al PR #137 en la rama del compañero.
Conserva el componente compartido `ExportDownloads`, los seis botones y sus
etiquetas ES/EN/PT. El material debe estar completado y guardado para descargarse;
el básico no necesita ni obtiene una aprobación independiente. El modo, las
métricas nulas y los avisos de su paquete 1.1 se conservan.

## Comprobaciones

- React: 78 pruebas aprobadas localmente, con build TypeScript/Vite correcto.
  Incluye paquetes 1.0 y ambos modos 1.1, compatibilidad por formato, indicador
  OCI frente a almacenamiento simulado, verificación previa y sesión inválida.
- El recorrido con Caddy/TLS locales en
  `backend/tests/integration/test_react_caddy.py` descarga los seis formatos desde
  resultado e historial, en básico y multiagente: 24 descargas desde un documento
  y dos generaciones. Abre JSON, PDF, CSV/TSV y el contenedor APKG; comprueba modo,
  etiquetas y ausencia de credenciales del espacio. Inspecciona también texto y
  metadatos PDF y archivos descomprimidos APKG. Las descargas no generan otro
  material y los botones se comprueban a 320 px.
- La evidencia saneada `generation-modes.json` de CI registra solo modos,
  cantidades y resultados de las comprobaciones; no publica credenciales ni
  archivos descargados. Los resultados finales corresponden al commit evaluado
  en los [checks del PR #138](https://github.com/No-Country-simulation/G10-Equipo38-NuevaMente/pull/138).

Gemini y OCI permanecen simulados explícitamente. El badge «Guardado en OCI» se
prueba con una respuesta sintética confirmada; eso no acredita una escritura OCI
real. La aceptación externa continúa en #133. La apertura del APKG como ZIP/SQLite
no sustituye la prueba de importación/reimportación de Anki Desktop del job de
calidad. Esta adaptación deja el PR preparado para revisión, sin merge automático.
