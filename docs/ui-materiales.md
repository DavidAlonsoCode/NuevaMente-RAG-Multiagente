# Materiales de estudio en la interfaz

El resultado de una generación se consulta por su identificador y sesión. La
pantalla presenta material sólo cuando el trabajo está `completed`, el paquete
coincide con su modo y la respuesta confirma su persistencia. Los multiagente
requieren aprobación; los básicos se identifican como no evaluados. «Guardado en OCI» exige
`provider=oci`; el almacenamiento de desarrollo tiene su propio rótulo.

## Vistas y reutilización

[PackageViewer](../frontend/src/components/FormatRenderers.tsx) presenta los
cinco formatos y se reutiliza en resultado, historial y comparación. Sus metadatos
provienen del paquete guardado, no de los controles actuales del sidebar.

- Flashcards: frente, pista opcional, respuesta desplegable con botón nativo y
  fuentes del dorso. La revisión queda identificada por generación/tarjeta en
  la sesión; el componente admite `on_review` para integrar progreso persistido.
- Quiz: preguntas y radios sin clave, justificación ni referencias de la
  solución. La corrección explicativa se integra con el componente del #36.
- Tutorial: pasos numerados, código, resultado esperado y comprobación.
- Resumen: impacto, puntos clave, implicaciones y acciones; las columnas se
  apilan en pantallas pequeñas.
- Guion: escenas, duración, narración, diapositivas y pregunta de discusión.

[SourcesDrawer](../frontend/src/components/SourcesDrawer.tsx) se comparte con
chat y glosario. Consulta el fragmento al solicitarlo, comprueba identidad y
localización, y limpia la evidencia visible al cambiar de documento o sesión.
Las imágenes se obtienen desde la ruta propia del backend con Bearer; nunca
se siguen URLs del modelo. Se distingue interpretación de imagen de original.

En modo multiagente, el panel de calidad explica la proporción de afirmaciones respaldadas, la
rúbrica, secciones cubiertas, notas y referencias. Los ejemplos de nicho llevan
una etiqueta ilustrativa. Los estados no completados no habilitan material,
actividades ni descargas. Reintentar persistencia reutiliza la misma generación
y clave de idempotencia. El seguimiento SSE automático de #33 informa etapas
e intentos y permite reconectar y cancelar de forma cooperativa. En básico se
muestra la ausencia de evaluación independiente, sin métricas inventadas.

## Verificación vigente

Seguir el [arranque con proveedores simulados](../frontend/README.md#verificación-sin-proveedores-reales)
y ejecutar `npm --prefix frontend test` y `npm --prefix frontend run build`.
Las comprobaciones de navegador/Caddy de la CI usan React y proveedores dobles.
La aceptación con proveedores reales y HTTPS público corresponde a #133.

## Evidencia histórica

La siguiente revisión y sus capturas pertenecen a la interfaz Streamlit retirada
en #139. Se conservan como registro de esa versión, sin acreditarlas para React.
El antiguo comando de ejecución ya no se aplica.

Revisión realizada el 14 de octubre de 2026 en Streamlit 1.51:

- Cinco formatos inspeccionados en escritorio y a 320 px. Ancho del documento:
  320 px; ancho/scroll de la zona principal: 310 px, sin desborde horizontal.
- Tab y Enter abren la respuesta de flashcards y las fuentes. Las flechas
  seleccionan opciones del quiz. Foco visible de 2 px, sin solución expuesta.
- Cambio de idioma de interfaz sin regenerar el contenido; controles ES/EN/PT
  y revisión conservada. Las pruebas automatizadas cubren selecciones y revisión.
- Código legible, columnas apiladas, fuentes textuales e imagen original. CSS
  con transiciones discretas y alternativa `prefers-reduced-motion`; la
  preferencia del sistema no se emuló durante esta sesión.

Capturas seleccionadas, todas de la vista de prueba:

- [Flashcards](evidencia/ui-34/flashcards-es.png)
- [Tutorial y fuente visual](evidencia/ui-34/tutorial-fuente.png)
- [Quiz estudiante](evidencia/ui-34/quiz-es.png)
- [Resumen en escritorio](evidencia/ui-34/resumen-es.png) y
  [resumen a 320 px](evidencia/ui-34/resumen-320.png)
- [Guion con interfaz PT a 320 px](evidencia/ui-34/guion-pt-320.png)
- [Flashcards](evidencia/ui-34/flashcards-320.png),
  [tutorial](evidencia/ui-34/tutorial-320.png) y
  [quiz](evidencia/ui-34/quiz-320.png) a 320 px
