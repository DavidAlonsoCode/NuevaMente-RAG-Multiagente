# Ejemplos reales con fuentes corregidas

Verificación del 15/10/2026, con Gemini y OCI reales. El catálogo publicado
contiene ocho materiales aprobados, la consulta visual y el diagnóstico de rechazo.
El catálogo declara `completo=true`; todos los paquetes se recuperaron desde otra
sesión anónima, conservando su aprobación real.

| Escenario aprobado | Duración medida |
|---|---:|
| A: principiante, flashcards, español | 94,815 s |
| B: junior, tutorial, inglés | 79,624 s |
| C: ejecutivo, resumen, portugués | 113,531 s |
| Quiz VCN | 126,599 s |
| Guion de clase VCN | 50,611 s |
| Arquitecto, tutorial VCN | 67,515 s |
| Junior, tutorial de pagos | 154,508 s |
| Ejecutivo, resumen de salud | 79,682 s |

A/B/C conservan el mismo `document_id` y hash de VCN v2. La consulta del diagrama
se aprobó con revisión de la imagen original: las dos subredes aparecen dentro
de la misma VCN. El intento sobre la sección débil de costos se rechazó en 88,5 s,
sin publicar su borrador. Los intentos anteriores se conservan como históricos;
no se cambió su resultado ni se reutilizaron paquetes v1 como v2.

Desde otra sesión anónima se verificaron los ocho materiales, una cita de cada
uno, los originales y sus hashes. El quiz comenzó sin respuestas ni explicaciones;
acierto y error devolvieron corrección explicativa con citas, sin cambiar el
progreso privado. El acceso al recurso privado del preparador devolvió 404.

La fase de lectura añadió 101 solicitudes OCI y ninguna solicitud LLM. El contador
terminó en 12.811, conservando su consumo anterior, con límite preventivo 20.000
y reserva de limpieza 2.000. El consumo externo de la tenancy no está incluido.

- [Informe de verificación](informe.json), con commit y comprobaciones reales.
- [Catálogo recuperado](catalogo.json), sin tokens ni respuestas correctas del quiz.
- [Página original recuperada por la API](diagrama-original.png).

Pagos se aprobó en la tercera redacción del intento diagnóstico: el grafo revisó
respaldo y cobertura antes de persistir. No se modificaron sus umbrales ni se
repitió automáticamente un pipeline por un fallo transitorio. Con autorización
del usuario se retiraron diez intentos antiguos fallidos o rechazados ya
reemplazados, conservando registro y marcas de borrado. Los documentos, paquetes
aprobados y el rechazo válido se conservaron.

La prueba se ejecutó contra la API local con almacenamiento real. No acredita
una VM pública, una auditoría del consumo global de la cuenta ni las revisiones
humanas del #60. La comprobación desde el despliegue público del #55 sigue
pendiente; los materiales y su recuperación real ya están preparados.
