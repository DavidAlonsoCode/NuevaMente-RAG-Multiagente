# Materiales de estudio en la interfaz

El resultado de una generación se consulta por su identificador y sesión. La
pantalla presenta material sólo cuando el trabajo está `completed`, el paquete
está aprobado y la respuesta confirma su persistencia. «Guardado en OCI» exige
`provider=oci`; el almacenamiento de desarrollo tiene su propio rótulo.

## Vistas y reutilización

`components.result.render_paquete(paquete, idioma, api_client=api,
key_prefix="...")` presenta los cinco formatos y puede reutilizarse en historial
o comparación. Cada contexto debe tener un prefijo distinto. Sus metadatos
provienen del paquete guardado, no de los controles actuales de la sidebar.

- Flashcards: frente, pista opcional, respuesta desplegable con botón nativo y
  fuentes del dorso. La revisión queda identificada por generación/tarjeta en
  la sesión; el componente admite `on_review` para integrar progreso persistido.
- Quiz: preguntas y radios sin clave, justificación ni referencias de la
  solución. La corrección explicativa se integra con el componente del #36.
- Tutorial: pasos numerados, código, resultado esperado y comprobación.
- Resumen: impacto, puntos clave, implicaciones y acciones; las columnas se
  apilan en pantallas pequeñas.
- Guion: escenas, duración, narración, diapositivas y pregunta de discusión.

`components.sources.render_fuentes(document_id, referencias, idioma,
api_client=api, key_prefix="...")` se comparte con chat y glosario. Consulta el
fragmento al solicitarlo, comprueba su identidad/localización y revalida el
acceso en cada renderizado abierto. No guarda texto ni imágenes entre sesiones.
Las imágenes se obtienen desde la ruta propia del backend con Bearer; nunca
se siguen URLs del modelo. Se distingue interpretación de imagen de original.

El panel de calidad explica la proporción de afirmaciones respaldadas, la
rúbrica, secciones cubiertas, notas y referencias. Los ejemplos de nicho llevan
una etiqueta ilustrativa. Los estados no completados no habilitan material,
actividades ni descargas. Reintentar persistencia reutiliza la misma generación
y clave de idempotencia. El seguimiento SSE automático corresponde al #33;
esta vista permite consultar y cancelar el trabajo usando la API vigente.

## Revisión visual reproducible

Desde la raíz, con las dependencias de desarrollo instaladas:

```powershell
.venv\Scripts\python.exe -m streamlit run frontend/tests/visual_material.py --theme.base dark --theme.backgroundColor "#010102" --theme.secondaryBackgroundColor "#0f1011" --theme.primaryColor "#5e6ad2" --theme.textColor "#f7f8f8"
```

Esta vista usa los fixtures del contrato #03, un cliente simulado y la página 3
del PDF VCN para inspeccionar la imagen. Está rotulada como prueba y no llama a
Gemini/OCI. Sus scores y estados simulados no acreditan integración real; la
evidencia de proveedores/despliegue corresponde a #53/#54.

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
