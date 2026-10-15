"""Intenciones del tutor, aisladas por espacio/documento/perfil/idioma."""

import re
from dataclasses import dataclass, field
from uuid import uuid4

from api_client import IDIOMAS_SALIDA, PERFILES, APIError


def _invalida():
    return APIError("INVALID_RESPONSE", "Respuesta de chat inválida.", 502)


def validar_trabajo(datos, job_id=None):
    if not isinstance(datos, dict):
        raise _invalida()
    identificador = datos.get("job_id")
    if not isinstance(identificador, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", identificador):
        raise _invalida()
    if job_id is not None and identificador != job_id:
        raise _invalida()
    if datos.get("status") not in ("queued", "running", "completed", "failed", "rejected_quality", "cancelled"):
        raise _invalida()
    return datos


def validar_respuesta(datos, turno):
    if not isinstance(datos, dict) or any(datos.get(k) != v for k, v in turno.payload().items()):
        raise _invalida()
    if not isinstance(datos.get("document_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", datos["document_hash"]):
        raise _invalida()
    if not isinstance(datos.get("respuesta"), str) or len(datos["respuesta"]) > 5000:
        raise _invalida()
    refs, calidad = datos.get("referencias"), datos.get("evaluacion_calidad")
    if datos.get("estado") == "fuera_de_alcance":
        if refs != [] or calidad is not None:
            raise _invalida()
    elif datos.get("estado") == "respondida":
        if (
            not datos["respuesta"].strip()
            or not isinstance(refs, list)
            or not refs
            or len(refs) > 100
            or any(not isinstance(r, dict) or not isinstance(r.get("chunk_id"), str) or not r["chunk_id"] for r in refs)
            or not isinstance(calidad, dict)
            or calidad.get("estado_evaluacion") != "aprobada"
            or calidad.get("razones_bloqueo")
            or calidad.get("cobertura_objetivos") != "completa"
            or calidad.get("verificacion_visual") not in ("no_aplica", "aprobada")
            or any(
                calidad.get(k) not in ("alta", "media")
                for k in ("claridad_pedagogica", "adecuacion_perfil", "coherencia_didactica")
            )
            or type(calidad.get("anclaje_fuente_score")) not in (int, float)
            or not 0.85 <= calidad["anclaje_fuente_score"] <= 1
            or type(calidad.get("cantidad_afirmaciones")) is not int
            or calidad["cantidad_afirmaciones"] <= 0
            or type(calidad.get("cantidad_respaldadas")) is not int
            or calidad["cantidad_respaldadas"] != calidad["cantidad_afirmaciones"]
        ):
            raise _invalida()
    else:
        raise _invalida()
    return datos


@dataclass
class TurnoChat:
    document_id: str
    pregunta: str
    perfil_destinatario: str
    idioma_salida: str
    clave: str = field(default_factory=lambda: uuid4().hex)
    job_id: str | None = None
    status: str = "admitting"
    respuesta: dict | None = None
    error: APIError | None = None
    cancelacion_solicitada: bool = False

    @property
    def activo(self):
        return self.status in ("admitting", "queued", "running")

    def payload(self):
        return {k: getattr(self, k) for k in ("document_id", "pregunta", "perfil_destinatario", "idioma_salida")}

    def recibir(self, datos):
        datos = validar_trabajo(datos, self.job_id)
        if datos["status"] == "completed":
            # Nunca cambiar a completed antes de validar el resultado autorizado.
            self.respuesta = validar_respuesta(datos.get("result"), self)
        self.job_id, self.status = datos["job_id"], datos["status"]
        self.error = None

    def enviar(self, api, token):
        if self.status != "admitting":
            raise ValueError("Una admisión confirmada no se reenvía")
        try:
            self.recibir(api.create_chat(token, self.payload(), idempotency_key=self.clave))
        except APIError as error:
            self.error = error
            if error.status_code in (401, 404):
                self.status = "unavailable"
                self.respuesta = None

    def consultar(self, api, token):
        if self.job_id is None or not self.activo:
            return
        try:
            self.recibir(api.get_job(token, self.job_id))
        except APIError as error:
            self.error = error
            if error.status_code in (401, 404):
                self.status, self.respuesta = "unavailable", None

    def cancelar(self, api, token):
        if self.job_id is None or not self.activo or self.cancelacion_solicitada:
            return
        try:
            datos = api.cancel_job(token, self.job_id)
            self.recibir(datos)
            self.cancelacion_solicitada = self.activo
        except APIError as error:
            self.error = error
            if error.status_code in (401, 404):
                self.status, self.respuesta = "unavailable", None


@dataclass
class Conversacion:
    document_id: str
    perfil: str
    idioma: str
    turnos: list[TurnoChat] = field(default_factory=list)

    @property
    def pendiente(self):
        return self.turnos[-1] if self.turnos and self.turnos[-1].activo else None

    def iniciar(self, pregunta):
        if self.pendiente or not isinstance(pregunta, str) or not pregunta.strip() or len(pregunta.strip()) > 2000:
            raise ValueError("Pregunta o conversación no disponible")
        turno = TurnoChat(self.document_id, pregunta.strip(), self.perfil, self.idioma)
        self.turnos.append(turno)
        self.turnos[:] = self.turnos[-50:]
        return turno

    def limpiar_vista(self):
        if self.pendiente:
            raise ValueError("Primero debe terminar o cancelarse la pregunta pendiente")
        self.turnos.clear()


def conversacion_actual(conversaciones, workspace_id, document_id, perfil, idioma):
    if perfil not in PERFILES or idioma not in IDIOMAS_SALIDA:
        raise ValueError("Perfil o idioma inválido")
    clave = (workspace_id, document_id, perfil, idioma)
    return conversaciones.setdefault(clave, Conversacion(document_id, perfil, idioma))


def pendiente_del_espacio(conversaciones, workspace_id):
    return next((c.pendiente for k, c in conversaciones.items() if k[0] == workspace_id and c.pendiente), None)
