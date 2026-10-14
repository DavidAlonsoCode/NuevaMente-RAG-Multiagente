"""Estado del quiz por espacio/generación; los reintentos conservan la intención."""

import hashlib
import json
from dataclasses import dataclass, field
from uuid import uuid4

from api_client import APIError


def _invalida():
    return APIError("INVALID_RESPONSE", "Respuesta de quiz inválida.", 502)


def validar_quiz(recibo, generation_id):
    if not isinstance(recibo, dict) or recibo.get("generation_id") != generation_id:
        raise _invalida()
    if recibo.get("status") != "completed":
        raise APIError("INVALID_STATE", "El quiz todavía no está disponible.", 409)
    contenido = recibo.get("contenido")
    if not isinstance(contenido, dict):
        raise _invalida()
    fuente = contenido.get("documento_fuente", {})
    quiz = contenido.get("contenido_adaptado", {})
    if not isinstance(fuente, dict) or not isinstance(quiz, dict):
        raise _invalida()
    if not isinstance(fuente.get("document_id"), str) or not fuente["document_id"] or quiz.get("tipo") != "quiz":
        raise _invalida()
    preguntas = quiz.get("preguntas")
    if not isinstance(preguntas, list) or not preguntas:
        raise _invalida()
    ids = set()
    for pregunta in preguntas:
        if not isinstance(pregunta, dict) or any(k in pregunta for k in ("correct_option_id", "justificacion")):
            raise _invalida()
        identificador = pregunta.get("id")
        opciones = pregunta.get("opciones")
        if not isinstance(identificador, str) or not identificador or identificador in ids:
            raise _invalida()
        ids.add(identificador)
        if not isinstance(pregunta.get("enunciado"), str) or not pregunta["enunciado"].strip():
            raise _invalida()
        if not isinstance(opciones, list) or len(opciones) != 4:
            raise _invalida()
        opciones_ids = set()
        for opcion in opciones:
            if not isinstance(opcion, dict) or not isinstance(opcion.get("option_id"), str) or not opcion["option_id"]:
                raise _invalida()
            if not isinstance(opcion.get("texto"), str) or not opcion["texto"].strip():
                raise _invalida()
            opciones_ids.add(opcion["option_id"])
        if len(opciones_ids) != 4:
            raise _invalida()
    return fuente["document_id"], preguntas


def validar_feedback(datos, pregunta, seleccion):
    if not isinstance(datos, dict) or type(datos.get("correcta")) is not bool:
        raise _invalida()
    correcta = datos.get("correct_option_id")
    if not isinstance(correcta, str) or correcta not in {o["option_id"] for o in pregunta["opciones"]}:
        raise _invalida()
    if datos["correcta"] != (seleccion == correcta):
        raise _invalida()
    if not isinstance(datos.get("justificacion"), str) or not datos["justificacion"].strip():
        raise _invalida()
    refs = datos.get("referencias")
    if (
        not isinstance(refs, list)
        or not refs
        or any(not isinstance(r, dict) or not isinstance(r.get("chunk_id"), str) or not r["chunk_id"] for r in refs)
    ):
        raise _invalida()
    return datos


@dataclass
class IntentoQuiz:
    question_id: str
    option_id: str
    event_id: str = field(default_factory=lambda: uuid4().hex)
    feedback: dict | None = None

    def payload(self):
        return {"question_id": self.question_id, "option_id": self.option_id, "event_id": self.event_id}


@dataclass
class EstadoQuiz:
    document_id: str
    preguntas: list[dict]
    indice: int = 0
    intentos: dict[str, list[IntentoQuiz]] = field(default_factory=dict)
    nuevos: set[str] = field(default_factory=set)
    selecciones: dict[str, str | None] = field(default_factory=dict)
    error: APIError | None = None
    invalidado: bool = False

    def preparar(self, pregunta, opcion):
        if self.invalidado or opcion not in {o["option_id"] for o in pregunta["opciones"]}:
            raise ValueError("Opción o quiz no disponible")
        historial = self.intentos.setdefault(pregunta["id"], [])
        if historial and historial[-1].feedback is None:
            if historial[-1].option_id != opcion:
                raise ValueError("La respuesta pendiente no puede cambiar")
            return historial[-1]
        if historial and pregunta["id"] not in self.nuevos:
            raise ValueError("Un nuevo intento debe ser explícito")
        intento = IntentoQuiz(pregunta["id"], opcion)
        historial.append(intento)
        self.nuevos.discard(pregunta["id"])
        return intento

    def enviar(self, api, token, generation_id, pregunta, opcion):
        intento = self.preparar(pregunta, opcion)
        self.error = None
        try:
            datos = api.answer_quiz(token, generation_id, intento.payload())
            intento.feedback = validar_feedback(datos, pregunta, intento.option_id)
        except APIError as error:
            self.error = error
            if error.status_code in (401, 404):
                self.intentos.clear()
                self.invalidado = True
        return intento.feedback

    def nuevo_intento(self, pregunta_id):
        historial = self.intentos.get(pregunta_id, [])
        if not historial or historial[-1].feedback is None or self.invalidado:
            raise ValueError("Primero debe confirmarse el intento anterior")
        self.nuevos.add(pregunta_id)
        self.selecciones.pop(pregunta_id, None)

    def resumen(self):
        confirmados = [[i.feedback for i in items if i.feedback is not None] for items in self.intentos.values()]
        return {
            "respondidas": sum(bool(items) for items in confirmados),
            "primeros": sum(bool(items) and items[0]["correcta"] for items in confirmados),
            "posteriores": sum(i["correcta"] for items in confirmados for i in items[1:]),
        }


def estado_quiz(estados, workspace_id, generation_id, document_id, preguntas):
    clave = (workspace_id, generation_id)
    huella = hashlib.sha256(json.dumps(preguntas, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    anterior = estados.get(clave)
    if anterior is None or anterior[0] != huella:
        estados[clave] = (huella, EstadoQuiz(document_id, preguntas))
    return estados[clave][1]
