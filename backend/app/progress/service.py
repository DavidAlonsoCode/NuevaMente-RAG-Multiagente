"""Eventos idempotentes con CAS en OCI; SQLite replica únicamente lo confirmado.

La escritura condicional decide el orden de intentos entre pestañas. Un fallo
después del PUT se recupera leyendo el mismo event_id: no se aplica otra vez.
Los conflictos de versión se resuelven con hasta tres lecturas nuevas; los
reintentos de transporte continúan siendo responsabilidad del proveedor OCI.
"""

import hashlib
import json
import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.pedagogical import FlashcardDeck, InteractiveQuiz
from app.schemas.progress import ProgressEventResponse, ProgressResponse, QuizAnswerResponse
from app.storage.object_keys import clave_progress
from app.storage.provider import StorageConflict, StorageNotFound, StorageUnavailable

MAX_EVENTOS = 10_000


class EventoGuardado(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    event_id: str = Field(min_length=1, max_length=128)
    solicitud_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    version_registro: int = Field(ge=1)
    tipo: Literal["concepto_revisado", "flashcard_vista", "respuesta_quiz"]
    document_id: str = Field(min_length=1, max_length=128)
    generation_id: str | None = Field(default=None, min_length=1, max_length=128)
    concepto: str | None = Field(default=None, min_length=1, max_length=500)
    flashcard_id: str | None = Field(default=None, min_length=1, max_length=64)
    question_id: str | None = Field(default=None, min_length=1, max_length=64)
    option_id: str | None = Field(default=None, min_length=1, max_length=64)
    correcta: bool | None = None

    @model_validator(mode="after")
    def referencias_del_tipo(self):
        if self.tipo == "respuesta_quiz":
            if not self.generation_id or not self.question_id or not self.option_id or self.correcta is None:
                raise ValueError("Respuesta de quiz incompleta")
        elif self.tipo == "flashcard_vista":
            if not self.generation_id or not self.flashcard_id:
                raise ValueError("Flashcard incompleta")
        elif not self.concepto:
            raise ValueError("Concepto incompleto")
        return self


class ClaveGuardada(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    clave_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_id: str
    document_id: str
    generation_id: str | None = None


class EstadoProgreso(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"] = "1.0"
    workspace_id: str | None = None
    version: int = Field(ge=0)
    eventos: list[EventoGuardado] = Field(default_factory=list, max_length=MAX_EVENTOS)
    claves: list[ClaveGuardada] = Field(default_factory=list, max_length=MAX_EVENTOS)

    @model_validator(mode="after")
    def consistencia(self):
        ids = [e.event_id for e in self.eventos]
        versiones = [e.version_registro for e in self.eventos]
        if len(ids) != len(set(ids)) or versiones != sorted(set(versiones)):
            raise ValueError("Eventos duplicados o fuera de orden")
        if versiones and versiones[-1] > self.version:
            raise ValueError("Evento posterior al agregado")
        hashes = [c.clave_hash for c in self.claves]
        if len(hashes) != len(set(hashes)) or any(c.event_id not in ids for c in self.claves):
            raise ValueError("Claves idempotentes inconsistentes")
        return self


class ServicioProgreso:
    def __init__(self, db, storage, catalogo, *, guardia=None):
        self.db, self.storage, self.catalogo = db, storage, catalogo
        self._lock = guardia if guardia is not None else threading.RLock()

    def _leer(self, ws):
        clave = clave_progress(ws)
        pagina = self.storage.list(clave, limit=1)
        objeto = next((o for o in pagina.items if o.object_name == clave), None)
        if objeto is None:
            return EstadoProgreso(workspace_id=ws, version=0), None
        try:
            estado = EstadoProgreso.model_validate_json(self.storage.get(clave, if_match=objeto.etag))
        except ValidationError:
            raise StorageUnavailable("El progreso guardado no es verificable") from None
        except StorageNotFound:
            raise StorageConflict("El progreso cambió durante la lectura") from None
        if estado.version < 1 or estado.workspace_id not in (None, ws):
            raise StorageUnavailable("Progreso sin identidad o versión válida")
        estado.workspace_id = ws
        return estado, objeto.etag

    def _vigente(self, token, estado):
        ws, manifest = self.catalogo.espacio(token)
        if ws != estado.workspace_id:
            raise StorageUnavailable("Progreso de otro espacio")
        retirados = {(r.recurso_tipo, r.recurso_id) for r in manifest.recursos_borrados}

        def visible(e):
            doc = self.db.obtener_documento(e.document_id)
            gen = self.db.obtener_generacion(e.generation_id) if e.generation_id else None
            return (
                doc is not None
                and doc["workspace_id"] == ws
                and ("document", e.document_id) not in retirados
                and (
                    e.generation_id is None
                    or (
                        gen is not None
                        and gen["workspace_id"] == ws
                        and gen["document_id"] == e.document_id
                        and ("generation", e.generation_id) not in retirados
                    )
                )
            )

        estado.eventos = [e for e in estado.eventos if visible(e)]
        ids = {e.event_id for e in estado.eventos}
        estado.claves = [c for c in estado.claves if c.event_id in ids]
        return estado

    def _registrar(self, token, datos, cuerpo, revalidar, idempotency_key):
        if idempotency_key is not None and not 1 <= len(idempotency_key.strip()) <= 128:
            raise ErrorAplicacion(ErrorCode.INVALID_REQUEST, "Idempotency-Key debe tener entre 1 y 128 caracteres.")
        solicitud_hash = hashlib.sha256(
            json.dumps(cuerpo, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
        ).hexdigest()
        clave_hash = hashlib.sha256(idempotency_key.encode()).hexdigest() if idempotency_key is not None else None
        with self._lock:
            ws, _ = self.catalogo.espacio(token)
            for _ in range(3):
                revalidar()
                try:
                    estado, etag = self._leer(ws)
                    self._vigente(token, estado)
                    previo = next((e for e in estado.eventos if e.event_id == datos["event_id"]), None)
                    alias = next((c for c in estado.claves if c.clave_hash == clave_hash), None)
                    if (previo and previo.solicitud_hash != solicitud_hash) or (
                        alias and alias.event_id != datos["event_id"]
                    ):
                        raise ErrorAplicacion(
                            ErrorCode.IDEMPOTENCY_CONFLICT, "Identificador idempotente reutilizado con otro cuerpo."
                        )
                    nuevo_evento = previo is None
                    nuevo_alias = clave_hash is not None and alias is None
                    if nuevo_evento or nuevo_alias:
                        if (nuevo_evento and len(estado.eventos) >= MAX_EVENTOS) or (
                            nuevo_alias and len(estado.claves) >= MAX_EVENTOS
                        ):
                            raise ErrorAplicacion(ErrorCode.RATE_LIMITED, "Límite de eventos de estudio alcanzado.")
                        estado.version += 1
                        if nuevo_evento:
                            previo = EventoGuardado(
                                **datos, solicitud_hash=solicitud_hash, version_registro=estado.version
                            )
                            estado.eventos.append(previo)
                        if nuevo_alias:
                            estado.claves.append(
                                ClaveGuardada(
                                    clave_hash=clave_hash,
                                    event_id=previo.event_id,
                                    document_id=previo.document_id,
                                    generation_id=previo.generation_id,
                                )
                            )
                        revalidar()
                        contenido = estado.model_dump_json(exclude_none=True).encode()
                        guardado = self.storage.upload(
                            clave_progress(ws),
                            contenido,
                            content_type="application/json",
                            crear_solo=etag is None,
                            if_match=etag,
                        )
                        try:
                            confirmado = self.storage.get(clave_progress(ws), if_match=guardado.etag)
                        except StorageNotFound:
                            raise StorageUnavailable("El progreso escrito dejó de estar disponible") from None
                        if confirmado != contenido:
                            raise StorageUnavailable("El progreso escrito no fue confirmado")
                    revalidar()
                    self.catalogo.espacio(token)
                    self.db.confirmar_progreso(ws, estado.model_dump(exclude_none=True))
                    return ProgressEventResponse(event_id=previo.event_id, version=previo.version_registro)
                except StorageConflict:
                    # CAS: releer y combinar, sin repetir un PUT de transporte.
                    continue
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El progreso cambió; reintentar con el mismo event_id.")

    def responder(self, token, generation_id, solicitud, idempotency_key=None):
        paquete = self.catalogo.canonico(token, generation_id)
        quiz = paquete.contenido_adaptado
        if not isinstance(quiz, InteractiveQuiz):
            raise ErrorAplicacion(ErrorCode.VALIDATION_ERROR, "La generación no contiene un quiz.")
        pregunta = next((p for p in quiz.preguntas if p.id == solicitud.question_id), None)
        if pregunta is None:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Pregunta no encontrada.")
        if solicitud.option_id not in {o.option_id for o in pregunta.opciones}:
            raise ErrorAplicacion(ErrorCode.VALIDATION_ERROR, "Opción no válida para esta pregunta.")
        correcta = solicitud.option_id == pregunta.correct_option_id

        def revalidar():
            self.catalogo.validar_fuente(token, paquete)

        self._registrar(
            token,
            dict(
                event_id=solicitud.event_id,
                tipo="respuesta_quiz",
                document_id=paquete.documento_fuente.document_id,
                generation_id=generation_id,
                question_id=solicitud.question_id,
                option_id=solicitud.option_id,
                correcta=correcta,
            ),
            {"operacion": "quiz", "generation_id": generation_id, **solicitud.model_dump()},
            revalidar,
            idempotency_key,
        )
        return QuizAnswerResponse(
            correcta=correcta,
            correct_option_id=pregunta.correct_option_id,
            justificacion=pregunta.justificacion,
            referencias=pregunta.referencias,
        )

    def registrar_evento(self, token, solicitud, idempotency_key=None):
        datos = solicitud.model_dump(exclude_none=True)
        paquete = self.catalogo.canonico(token, solicitud.generation_id) if solicitud.generation_id else None

        def revalidar():
            if solicitud.generation_id:
                self.catalogo.validar_fuente(token, paquete)
                document_id = paquete.documento_fuente.document_id
                if solicitud.document_id and solicitud.document_id != document_id:
                    raise ErrorAplicacion(ErrorCode.VALIDATION_ERROR, "La generación pertenece a otro documento.")
                datos["document_id"] = document_id
                if solicitud.tipo == "flashcard_vista":
                    cartas = paquete.contenido_adaptado
                    if not isinstance(cartas, FlashcardDeck):
                        raise ErrorAplicacion(ErrorCode.VALIDATION_ERROR, "La generación no contiene flashcards.")
                    if solicitud.flashcard_id not in {c.id for c in cartas.items}:
                        raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Flashcard no encontrada.")
            else:
                self.consulta_documento(token, solicitud.document_id)
            if solicitud.tipo == "concepto_revisado":
                datos["concepto"] = solicitud.concepto.strip()

        revalidar()
        return self._registrar(
            token, datos, {"operacion": "progreso", **solicitud.model_dump()}, revalidar, idempotency_key
        )

    def consulta_documento(self, token, document_id):
        self.catalogo.espacio(token)
        return self.catalogo.consulta.fuente_aprobada(token, document_id)

    def consultar(self, token):
        ws, _ = self.catalogo.espacio(token)
        for _ in range(3):
            try:
                estado, _ = self._leer(ws)
                break
            except StorageConflict:
                continue
        else:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El progreso cambió; reintentar la consulta.")
        self._vigente(token, estado)
        conceptos = {
            (e.document_id, e.generation_id, e.concepto) for e in estado.eventos if e.tipo == "concepto_revisado"
        }
        cartas = {(e.generation_id, e.flashcard_id) for e in estado.eventos if e.tipo == "flashcard_vista"}
        quiz = [e for e in estado.eventos if e.tipo == "respuesta_quiz"]
        primeras, posteriores = {}, []
        for e in quiz:
            clave = (e.generation_id, e.question_id)
            if clave in primeras:
                posteriores.append(e)
            else:
                primeras[clave] = e
        restante = 0.0
        for fila in self.db.listar_generaciones(ws):
            if fila["status"] != "completed":
                continue
            try:
                paquete = self.catalogo.canonico(token, fila["generation_id"])
            except ErrorAplicacion as error:
                if error.error.code in {ErrorCode.NOT_FOUND, ErrorCode.INVALID_STATE}:
                    continue
                raise
            gid, doc = paquete.generation_id, paquete.documento_fuente.document_id
            contenido = paquete.contenido_adaptado
            if isinstance(contenido, InteractiveQuiz):
                pendientes = sum((gid, p.id) not in primeras for p in contenido.preguntas)
                total = len(contenido.preguntas)
            elif isinstance(contenido, FlashcardDeck):
                pendientes = sum((gid, c.id) not in cartas for c in contenido.items)
                total = len(contenido.items)
            else:
                conceptos_paquete = set(paquete.metadatos.conceptos_clave)
                pendientes = sum(
                    (doc, gid, c) not in conceptos and (doc, None, c) not in conceptos for c in conceptos_paquete
                )
                total = len(conceptos_paquete)
            restante += paquete.metadatos.tiempo_estimado_estudio_minutos * pendientes / total
        # Las métricas también se revalidan si la retirada ocurrió durante I/O.
        vigente = self._vigente(token, estado.model_copy(deep=True))
        if vigente != estado:
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "El progreso cambió durante la consulta.")
        if estado.version:
            self.db.confirmar_progreso(ws, estado.model_dump(exclude_none=True))
        return ProgressResponse(
            version=estado.version,
            conceptos_revisados=len(conceptos),
            flashcards_vistas=len(cartas),
            quiz_intentos=len(quiz),
            quiz_aciertos_primer_intento=sum(e.correcta for e in primeras.values()),
            quiz_total_primer_intento=len(primeras),
            quiz_aciertos_posteriores=sum(e.correcta for e in posteriores),
            tiempo_estimado_restante_minutos=round(restante, 2),
        )
