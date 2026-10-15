"""Eventos de lectura idempotentes; los agregados se consultan siempre al servidor."""

import hashlib
import json
import math

from api_client import APIError

CONTADORES = (
    "version",
    "conceptos_revisados",
    "flashcards_vistas",
    "quiz_intentos",
    "quiz_aciertos_primer_intento",
    "quiz_total_primer_intento",
    "quiz_aciertos_posteriores",
)


def consultar_progreso(api, token):
    salida = api.get_progress(token)
    try:
        valido = isinstance(salida, dict) and set(salida) == {*CONTADORES, "tiempo_estimado_restante_minutos"}
        valido = valido and all(type(salida[k]) is int and salida[k] >= 0 for k in CONTADORES)
        tiempo = salida["tiempo_estimado_restante_minutos"]
        valido = valido and type(tiempo) in (int, float) and math.isfinite(tiempo) and tiempo >= 0
        valido = (
            valido
            and salida["quiz_aciertos_primer_intento"] <= salida["quiz_total_primer_intento"] <= salida["quiz_intentos"]
        )
        valido = (
            valido
            and salida["quiz_aciertos_posteriores"] <= salida["quiz_intentos"] - salida["quiz_total_primer_intento"]
        )
        if not valido:
            raise ValueError("Agregados incoherentes")
    except (KeyError, TypeError, ValueError, OverflowError):
        raise APIError("INVALID_RESPONSE", "Progreso inválido", 502) from None
    return salida


def _estado(session, workspace_id, datos):
    # Una primera lectura completa representa la misma actividad aun tras F5.
    huella = hashlib.sha256(json.dumps([workspace_id, datos], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return session.setdefault("_progreso_eventos_ui", {}).setdefault(
        (workspace_id, huella),
        {
            "datos": datos,
            "event_id": "ui41-" + huella,
            "caras": set(),
            "listo": False,
            "intentado": False,
            "confirmado": False,
        },
    )


def concepto_revisado(session, workspace_id, document_id, concepto, generation_id=None):
    datos = {"tipo": "concepto_revisado", "document_id": document_id, "concepto": concepto.strip()}
    if generation_id is not None:
        datos["generation_id"] = generation_id
    estado = _estado(session, workspace_id, datos)
    estado["listo"] = True
    return estado


def cara_flashcard(session, workspace_id, generation_id, flashcard_id, cara):
    if cara not in {"frente", "dorso"}:
        raise ValueError("Cara de flashcard inválida")
    estado = _estado(
        session,
        workspace_id,
        {
            "tipo": "flashcard_vista",
            "generation_id": generation_id,
            "flashcard_id": flashcard_id,
        },
    )
    estado["caras"].add(cara)
    estado["listo"] = estado["caras"] == {"frente", "dorso"}
    return estado


def enviar_evento(api, token, estado):
    if not estado["listo"] or estado["confirmado"]:
        return False
    estado["intentado"] = True
    payload = {**estado["datos"], "event_id": estado["event_id"]}
    respuesta = api.record_progress(token, payload)
    if (
        not isinstance(respuesta, dict)
        or respuesta.get("event_id") != estado["event_id"]
        or type(respuesta.get("version")) is not int
        or respuesta["version"] < 1
    ):
        raise APIError("INVALID_RESPONSE", "Confirmación de progreso inválida", 502)
    estado["confirmado"] = True
    return True


def pendientes(session, workspace_id):
    return [
        e
        for (ws, _), e in session.get("_progreso_eventos_ui", {}).items()
        if ws == workspace_id and e["listo"] and not e["confirmado"]
    ]
