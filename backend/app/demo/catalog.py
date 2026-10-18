"""Lectura de snapshots públicos explícitos; nunca accede a espacios privados."""

import hashlib
import re

from app.demo.models import CatalogoEjemplos, FuenteDemoGuardada
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.progress import QuizAnswerResponse
from app.schemas.responses import PedagogicalStudentOutput, ReviewedPedagogicalStudentOutputV11, cargar_paquete_json
from app.storage.object_keys import clave_demo
from app.storage.provider import StorageNotFound, StorageUnavailable

CLAVE_CATALOGO = clave_demo("examples/catalog.json")


def clave_blob(huella):
    if not re.fullmatch(r"[a-f0-9]{64}", huella):
        raise StorageUnavailable("Referencia demo inválida")
    return clave_demo("examples/blobs/" + huella)


def guardar_blob(storage, contenido, mime="application/json"):
    huella = hashlib.sha256(contenido).hexdigest()
    clave = clave_blob(huella)
    try:
        existente = storage.get(clave)
    except StorageNotFound:
        storage.upload(clave, contenido, content_type=mime, crear_solo=True)
        existente = storage.get(clave)
    if existente != contenido:
        raise StorageUnavailable("El snapshot demo no fue confirmado")
    return huella


class CatalogoResultadosDemo:
    def __init__(self, storage, sesiones):
        self.storage, self.sesiones = storage, sesiones

    def leer(self, token):
        self.sesiones.sesion(token)
        try:
            contenido = self.storage.get(CLAVE_CATALOGO)
        except StorageNotFound:
            return None
        if len(contenido) > 1_000_000:
            raise StorageUnavailable("Catálogo demo demasiado grande")
        try:
            catalogo = CatalogoEjemplos.model_validate_json(contenido)
        except ValueError:
            raise StorageUnavailable("El catálogo demo no es verificable") from None
        self.sesiones.sesion(token)
        return catalogo

    def blob(self, huella, *, limite=25_000_000):
        try:
            contenido = self.storage.get(clave_blob(huella))
        except StorageNotFound:
            raise StorageUnavailable("El snapshot demo está incompleto") from None
        if len(contenido) > limite or hashlib.sha256(contenido).hexdigest() != huella:
            raise StorageUnavailable("El snapshot demo no conserva su huella")
        return contenido

    def buscar(self, token, generation_id):
        catalogo = self.leer(token)
        ejemplo = next((e for e in catalogo.items if e.generation_id == generation_id), None) if catalogo else None
        if ejemplo is None or ejemplo.status != "completed":
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Ejemplo no encontrado.")
        return ejemplo

    def cargar(self, token, generation_id):
        ejemplo = self.buscar(token, generation_id)
        try:
            paquete = cargar_paquete_json(self.blob(ejemplo.paquete_sha256))
            fuente = FuenteDemoGuardada.model_validate_json(self.blob(ejemplo.fuente_sha256))
        except ValueError:
            raise StorageUnavailable("Ejemplo demo incompatible") from None
        if (
            paquete.status != "aprobado"
            or paquete.generation_id != generation_id
            or paquete.documento_fuente.document_id != ejemplo.parametros.document_id
            or paquete.documento_fuente.hash != ejemplo.document_hash
            or fuente.document_id != paquete.documento_fuente.document_id
            or fuente.document_hash != ejemplo.document_hash
        ):
            raise StorageUnavailable("El ejemplo no corresponde a su fuente")
        originales = {c.chunk_id: c for c in fuente.chunks}
        if any(
            r.chunk_id not in originales
            or (r.pagina is not None and r.pagina != originales[r.chunk_id].pagina)
            or (r.seccion is not None and r.seccion != originales[r.chunk_id].seccion)
            for r in paquete.referencias
        ):
            raise StorageUnavailable("Las citas demo no son verificables")
        self.sesiones.sesion(token)
        return paquete, fuente

    def estudiante(self, token, generation_id):
        paquete = self.cargar(token, generation_id)[0]
        modelo = ReviewedPedagogicalStudentOutputV11 if paquete.schema_version == "1.1" else PedagogicalStudentOutput
        return modelo.desde_canonico(paquete)

    def fuente(self, token, generation_id, chunk_id):
        paquete, fuentes = self.cargar(token, generation_id)
        fuente = next((c for c in fuentes.chunks if c.chunk_id == chunk_id), None)
        if fuente is None:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Fuente no encontrada.")
        return fuente

    def imagen(self, token, generation_id, chunk_id):
        self.fuente(token, generation_id, chunk_id)
        _, fuentes = self.cargar(token, generation_id)
        if chunk_id not in fuentes.imagenes:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Imagen no encontrada.")
        imagen = self.blob(fuentes.imagenes[chunk_id], limite=8_000_000)
        if not imagen.startswith(b"\x89PNG\r\n\x1a\n"):
            raise StorageUnavailable("Imagen demo incompatible")
        self.sesiones.sesion(token)
        return imagen

    def responder(self, token, generation_id, solicitud):
        paquete, _ = self.cargar(token, generation_id)
        quiz = paquete.contenido_adaptado
        if quiz.tipo != "quiz":
            raise ErrorAplicacion(ErrorCode.VALIDATION_ERROR, "El ejemplo no es un quiz.")
        pregunta = next((p for p in quiz.preguntas if p.id == solicitud.question_id), None)
        if pregunta is None:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Pregunta no encontrada.")
        if solicitud.option_id not in {o.option_id for o in pregunta.opciones}:
            raise ErrorAplicacion(ErrorCode.VALIDATION_ERROR, "Opción no válida.")
        return QuizAnswerResponse(
            correcta=solicitud.option_id == pregunta.correct_option_id,
            correct_option_id=pregunta.correct_option_id,
            justificacion=pregunta.justificacion,
            referencias=pregunta.referencias,
        )
