"""Matriz de §18: tres adaptaciones de la misma fuente y complementos."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Escenario:
    id: str
    fuente: int
    perfil: str
    formato: str
    nicho: str = "general"
    idioma: str = "es"

    def solicitud(self, document_id):
        return {
            "document_id": document_id,
            "perfil_destinatario": self.perfil,
            "formato_salida": self.formato,
            "nicho_sector": self.nicho,
            "idioma_salida": self.idioma,
            "nivel_detalle": "didactico",
            "alcance": {"tipo": "documento_completo"},
        }


ESCENARIOS = (
    Escenario("vcn-a", 0, "principiante", "flashcards"),
    Escenario("vcn-b", 0, "junior_ssr", "tutorial", "ecommerce", "en"),
    Escenario("vcn-c", 0, "ejecutivo", "resumen_ejecutivo", "fintech", "pt"),
    Escenario("vcn-quiz", 0, "principiante", "quiz"),
    Escenario("vcn-guion", 0, "principiante", "guion_clase"),
    Escenario("vcn-arquitecto", 0, "lider_tecnico", "tutorial"),
    Escenario("pagos-tutorial", 1, "junior_ssr", "tutorial", "fintech"),
    Escenario("salud-resumen", 2, "ejecutivo", "resumen_ejecutivo", "salud"),
)
PREGUNTA_DIAGRAMA = (
    "Según el diagrama de la página 3, ¿qué relaciones se muestran entre la VCN, "
    "sus subredes y los gateways? Fundamentá la respuesta en la imagen original."
)
