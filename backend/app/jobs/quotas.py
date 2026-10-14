"""Límites por modelo compartidos por la API y el mantenimiento offline."""

from app.jobs.manager import CuotasModelo


def cuotas_configuradas(configuracion) -> dict[str, CuotasModelo]:
    cuotas = {}
    for nombre, modelo in (
        ("embedding", configuracion.gemini_embedding_model),
        ("vision", configuracion.gemini_generation_model),
        ("generation", configuracion.gemini_generation_model),
        ("verification", configuracion.gemini_verification_model),
    ):
        if getattr(configuracion, nombre + "_quota_rpm") is None:
            continue
        limites = CuotasModelo(*(getattr(configuracion, nombre + "_quota_" + v) for v in ("rpm", "tpm", "rpd")))
        if modelo in cuotas and cuotas[modelo] != limites:
            raise ValueError("Un modelo compartido requiere los mismos límites de cuota")
        cuotas[modelo] = limites
    return cuotas
