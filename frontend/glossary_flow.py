"""Estado de presentación del glosario; autorización y contenido permanecen en la API."""

from typing import get_args
from uuid import uuid4

from api_client import APIError, JobStatus

TERMINALES = {"completed", "failed", "rejected_quality", "cancelled", "unavailable"}
ESTADOS = set(get_args(JobStatus)) | {"unavailable"}


def estado_glosario(session, workspace_id, document_id, perfil, idioma_salida):
    scope = (workspace_id, document_id, perfil, idioma_salida)
    return session.setdefault("_glosarios_ui", {}).setdefault(
        scope,
        {
            "payload": {"document_id": document_id, "perfil_destinatario": perfil, "idioma_salida": idioma_salida},
            "pending": None,
            "job_id": None,
            "status": None,
            "cancel_requested": False,
        },
    )


def admitir_glosario(api, token, estado):
    if estado["job_id"] and estado["status"] not in TERMINALES:
        return
    if estado["pending"] is None:
        estado["pending"] = str(uuid4())
    recibo = api.create_glossary(token, dict(estado["payload"]), idempotency_key=estado["pending"])
    job_id = recibo.get("job_id")
    if (
        not isinstance(job_id, str)
        or not job_id
        or recibo.get("glossary_id") != job_id
        or recibo.get("status") not in ESTADOS - {"unavailable"}
    ):
        raise APIError("INVALID_RESPONSE", "Recibo de glosario inválido", 502)
    estado.update(job_id=job_id, status=recibo["status"], pending=None, cancel_requested=False)


def consultar_glosario(api, token, estado):
    """GET en cada render: nunca mostrar una copia sin revalidar el acceso."""
    if not estado["job_id"]:
        return None
    if estado["status"] not in TERMINALES:
        recibo = api.get_job(token, estado["job_id"])
        if recibo.get("job_id") != estado["job_id"] or recibo.get("status") not in ESTADOS - {"unavailable"}:
            raise APIError("INVALID_RESPONSE", "Estado de glosario inválido", 502)
        estado["status"] = recibo["status"]
    if estado["status"] != "completed":
        return None
    salida = api.get_glossary(token, estado["job_id"])
    validar_glosario(salida, estado)
    return salida


def cancelar_glosario(api, token, estado):
    api.cancel_glossary(token, estado["job_id"])
    # Aceptar la intención no acredita que la llamada en vuelo haya terminado.
    estado["cancel_requested"] = True


def validar_glosario(salida, estado):
    def exigir(condicion):
        if not condicion:
            raise ValueError("Glosario incoherente")

    try:
        exigir(isinstance(salida, dict))
        exigir(salida["glossary_id"] == estado["job_id"])
        exigir(salida["documento_fuente"]["document_id"] == estado["payload"]["document_id"])
        exigir(all(salida[k] == estado["payload"][k] for k in ("perfil_destinatario", "idioma_salida")))
        terminos = salida["terminos"]
        exigir(isinstance(terminos, list) and len(terminos) <= 64)
        exigir(len({t["id"] for t in terminos}) == len(terminos))
        exigir(type(salida["candidatos_omitidos"]) is int and salida["candidatos_omitidos"] >= 0)
        definidos = [t for t in terminos if t["estado"] == "definido"]
        exigir(salida["estado"] in {"verificado", "sin_definiciones", "sin_terminos"})
        exigir((salida["estado"] == "sin_terminos") == (not terminos))
        exigir((salida["estado"] == "verificado") == bool(definidos))
        for termino in terminos:
            exigir(isinstance(termino["termino_original"], str) and termino["termino_original"])
            exigir(type(termino["es_identificador"]) is bool)
            exigir(not termino["es_identificador"] or termino.get("traduccion") is None)
            exigir(termino["estado"] in {"definido", "sin_definicion_suficiente"})
            exigir(
                termino["referencias"]
                and all(isinstance(r["chunk_id"], str) and r["chunk_id"] for r in termino["referencias"])
            )
            if termino["estado"] == "definido":
                exigir(isinstance(termino["definicion"], str) and termino["definicion"])
                exigir(not termino.get("limitacion"))
            else:
                exigir(termino["limitacion"] and not termino.get("definicion") and not termino.get("traduccion"))
        if definidos:
            calidad = salida["evaluacion_calidad"]
            exigir(calidad["estado_evaluacion"] == "aprobada" and calidad["anclaje_fuente_score"] >= 0.85)
            exigir(calidad["cantidad_afirmaciones"] > 0)
            exigir(calidad["cantidad_respaldadas"] == calidad["cantidad_afirmaciones"])
            exigir(calidad["cobertura_objetivos"] == "completa" and not calidad.get("razones_bloqueo"))
            exigir(calidad["verificacion_visual"] in {"aprobada", "no_aplica"})
            exigir(
                all(
                    calidad[k] in {"alta", "media"}
                    for k in ("claridad_pedagogica", "adecuacion_perfil", "coherencia_didactica")
                )
            )
            exigir(isinstance(salida["trazabilidad"], dict))
        else:
            exigir(salida["evaluacion_calidad"] is None)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise APIError("INVALID_RESPONSE", "Glosario inválido", 502) from None


def filtrar_terminos(terminos, consulta):
    consulta = consulta.strip().casefold()
    return sorted(
        (
            t
            for t in terminos
            if consulta
            in " ".join(str(t.get(k) or "") for k in ("termino_original", "traduccion", "definicion")).casefold()
        ),
        key=lambda t: (t["termino_original"].casefold(), t["id"]),
    )
