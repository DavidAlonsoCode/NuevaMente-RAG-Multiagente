"""Etiquetas fijas de procedencia; conserva bytes de exportaciones históricas 1.0."""

from app.schemas.responses import modo_paquete

_ETIQUETAS = {
    "es": ("Multiagente · Revisión automática aprobada", "Básico · Sin evaluación independiente"),
    "en": ("Multi-agent · Automated review passed", "Basic · No independent evaluation"),
    "pt": ("Multiagente · Revisão automática aprovada", "Básico · Sem avaliação independente"),
}


def etiqueta_generacion(paquete):
    if paquete.schema_version == "1.0":
        return None
    return _ETIQUETAS[paquete.metadatos.idioma_salida.value][modo_paquete(paquete) == "basico"]
