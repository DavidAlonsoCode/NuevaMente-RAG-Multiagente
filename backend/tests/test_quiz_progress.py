"""Issue #35: SQLite y storage reales locales, sin proveedores LLM ni Chroma."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from types import SimpleNamespace

import pytest
from app.generations.catalog import CatalogoGeneraciones
from app.jobs.store import RegistroOperativo
from app.progress.service import ServicioProgreso
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.requests import ProgressEvent, QuizAnswerRequest
from app.schemas.responses import PedagogicalOutput
from app.session.manifests import sincronizar_manifiesto
from app.storage.object_keys import clave_output, clave_progress, clave_workspace
from app.storage.oci_storage import LocalMockStorageProvider
from app.storage.provider import StorageConflict, StorageUnavailable
from pydantic import ValidationError
from test_schemas import FLASHCARDS, QUIZ, RESUMEN, salida_valida

pytestmark = pytest.mark.unit


@pytest.fixture
def estudio(tmp_path):
    db = RegistroOperativo(tmp_path / "registro.db")
    storage = LocalMockStorageProvider(tmp_path / "storage")
    db.crear_workspace("ws_estudio", "codigo", dias_validez=30)
    db.crear_sesion("token-estudio", "ws_estudio", horas_validez=24)
    sincronizar_manifiesto(db, storage, "ws_estudio")
    paquetes = {}

    def documento(token, doc):
        sesion = db.obtener_sesion(token)
        fila = db.obtener_documento(doc)
        if sesion is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "Sesión inválida")
        if fila is None or fila["workspace_id"] != sesion["workspace_id"]:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no encontrado")
        if fila["estado"] != "ready":
            raise ErrorAplicacion(ErrorCode.INVALID_STATE, "Documento no listo")
        return fila

    def fuente(token, doc):
        documento(token, doc)
        return next(p.documento_fuente for p in paquetes.values() if p.documento_fuente.document_id == doc)

    consulta = SimpleNamespace(detalle=documento, fuente_aprobada=fuente)
    catalogo = CatalogoGeneraciones(db, storage, consulta, "nuevamente-contenidos-educativos")

    def publicar(gid="gen_quiz", contenido=QUIZ, doc="doc_fuente"):
        datos = salida_valida(contenido).model_dump(mode="json")
        datos.update(generation_id=gid)
        datos["documento_fuente"].update(document_id=doc, hash="a" * 64)
        datos["almacenamiento_oci"]["objeto_id"] = clave_output("ws_estudio", gid)
        paquete = PedagogicalOutput.model_validate(datos)
        if db.obtener_documento(doc) is None:
            db.registrar_documento(doc, "ws_estudio", paquete.documento_fuente.titulo, DocumentStatus.READY, "a" * 64)
        db.registrar_generacion(gid, "ws_estudio", doc)
        db.actualizar_generacion(gid, JobStatus.COMPLETED)
        paquetes[gid] = paquete
        storage.upload(clave_output("ws_estudio", gid), paquete.model_dump_json())
        return paquete

    servicio = ServicioProgreso(db, storage, catalogo)
    e = SimpleNamespace(
        db=db, storage=storage, catalogo=catalogo, servicio=servicio, publicar=publicar, token="token-estudio"
    )
    e.paquete = publicar()
    e.pregunta = e.paquete.contenido_adaptado.preguntas[0]
    yield e
    e.db.cerrar()


def respuesta(e, event_id="respuesta-1", correcta=True, **kwargs):
    pregunta = e.pregunta
    opcion = (
        pregunta.correct_option_id
        if correcta
        else next(o.option_id for o in pregunta.opciones if o.option_id != pregunta.correct_option_id)
    )
    solicitud = QuizAnswerRequest(question_id=pregunta.id, option_id=opcion, event_id=event_id)
    return e.servicio.responder(e.token, e.paquete.generation_id, solicitud, **kwargs)


def concepto(e, event_id="concepto-1", **kwargs):
    return ProgressEvent.model_validate(
        dict(event_id=event_id, tipo="concepto_revisado", document_id="doc_fuente", concepto="VCN") | kwargs
    )


def test_feedback_correcto_incorrecto_solo_pregunta_y_primer_intento(estudio):
    e = estudio
    inicial = e.servicio.consultar(e.token)
    assert inicial.quiz_intentos == 0 and inicial.tiempo_estimado_restante_minutos == 5
    mala = respuesta(e, correcta=False)
    assert mala.correcta is False
    assert mala.correct_option_id == e.pregunta.correct_option_id
    assert mala.justificacion == e.pregunta.justificacion and mala.referencias == e.pregunta.referencias
    buena = respuesta(e, "respuesta-2")
    assert buena.correcta is True
    assert set(buena.model_dump()) == {"correcta", "correct_option_id", "justificacion", "referencias"}
    assert respuesta(e, correcta=False) == mala
    p = e.servicio.consultar(e.token)
    assert (p.quiz_intentos, p.quiz_total_primer_intento, p.quiz_aciertos_primer_intento) == (2, 1, 0)
    assert p.quiz_aciertos_posteriores == 1 and p.tiempo_estimado_restante_minutos == 0


def test_event_id_y_header_idempotentes_con_conflicto_global(estudio):
    e = estudio
    primera = e.servicio.registrar_evento(e.token, concepto(e), "intencion")
    assert e.servicio.registrar_evento(e.token, concepto(e), "intencion") == primera
    assert e.servicio.registrar_evento(e.token, concepto(e)) == primera
    for solicitud, clave in [(concepto(e, concepto="Otro"), None), (concepto(e, "evento-otro"), "intencion")]:
        with pytest.raises(ErrorAplicacion) as error:
            e.servicio.registrar_evento(e.token, solicitud, clave)
        assert error.value.error.code == ErrorCode.IDEMPOTENCY_CONFLICT
    # El event_id pertenece al espacio, no a un endpoint particular.
    with pytest.raises(ErrorAplicacion) as error:
        respuesta(e, "concepto-1")
    assert error.value.error.code == ErrorCode.IDEMPOTENCY_CONFLICT
    assert e.servicio.consultar(e.token).conceptos_revisados == 1


def test_duplicar_actividad_con_otro_evento_no_infla_conceptos_ni_tarjetas(estudio):
    e = estudio
    paquete = e.publicar("gen_cartas", FLASHCARDS)
    card = paquete.contenido_adaptado.items[0]
    for i in range(2):
        e.servicio.registrar_evento(e.token, concepto(e, f"concepto-{i}"))
        e.servicio.registrar_evento(
            e.token,
            ProgressEvent(
                event_id=f"carta-{i}", tipo="flashcard_vista", generation_id="gen_cartas", flashcard_id=card.id
            ),
        )
    p = e.servicio.consultar(e.token)
    assert p.conceptos_revisados == p.flashcards_vistas == 1
    assert p.tiempo_estimado_restante_minutos == 5  # Sólo queda el quiz.
    e.servicio.registrar_evento(e.token, concepto(e, "concepto-idioma", generation_id="gen_cartas"))
    assert e.servicio.consultar(e.token).flashcards_vistas == 1


def test_multiples_preguntas_y_generaciones_no_comparten_primer_intento(estudio):
    e = estudio
    otra = e.publicar("gen_otro_idioma")
    respuesta(e)
    e.servicio.responder(
        e.token,
        otra.generation_id,
        QuizAnswerRequest(question_id=e.pregunta.id, option_id=e.pregunta.correct_option_id, event_id="otro-idioma"),
    )
    p = e.servicio.consultar(e.token)
    assert p.quiz_total_primer_intento == p.quiz_aciertos_primer_intento == 2


def test_tiempo_pendiente_por_pregunta_tarjeta_y_concepto(estudio):
    e = estudio
    contenido = deepcopy(QUIZ)
    contenido["preguntas"].append({**deepcopy(contenido["preguntas"][0]), "id": "pregunta-2"})
    e.publicar("gen_dos", contenido)
    e.publicar("gen_resumen", RESUMEN)
    # 5 minutos del quiz inicial, 5 del nuevo quiz y 5 del resumen.
    assert e.servicio.consultar(e.token).tiempo_estimado_restante_minutos == 15
    e.servicio.responder(
        e.token,
        "gen_dos",
        QuizAnswerRequest(event_id="pregunta-1", question_id=e.pregunta.id, option_id=e.pregunta.correct_option_id),
    )
    assert e.servicio.consultar(e.token).tiempo_estimado_restante_minutos == 12.5
    e.servicio.registrar_evento(e.token, concepto(e))
    assert e.servicio.consultar(e.token).tiempo_estimado_restante_minutos == 7.5


def test_reintento_con_nuevo_header_no_duplica_evento(estudio):
    e = estudio
    respuesta(e)
    respuesta(e, idempotency_key="header-agregado")
    assert e.servicio.consultar(e.token).quiz_intentos == 1
    estado = json.loads(e.storage.get(clave_progress("ws_estudio")))
    assert estado["version"] == 2 and len(estado["claves"]) == 1
    assert "header-agregado" not in json.dumps(estado)


def test_reinicio_y_reconstruccion_no_pierden_idempotencia(estudio, tmp_path):
    e = estudio
    respuesta(e, correcta=False, idempotency_key="intencion")
    estado = json.loads(e.storage.get(clave_progress("ws_estudio")))
    assert e.db.obtener_progreso_restaurado("ws_estudio") == estado
    e.db.cerrar()
    e.db = RegistroOperativo(tmp_path / "registro.db")
    e.catalogo.db = e.db
    # El lector simulado conserva su conexión; esta prueba del estado de progreso
    # usa el aprobado en memoria. La recuperación documental real se prueba por API.
    e.catalogo.canonico = lambda token, gid: e.paquete
    e.catalogo.validar_fuente = lambda token, paquete: None
    e.servicio = ServicioProgreso(e.db, e.storage, e.catalogo)
    assert e.servicio.consultar(e.token).quiz_aciertos_primer_intento == 0
    assert respuesta(e, correcta=False, idempotency_key="intencion").correcta is False
    assert e.servicio.consultar(e.token).quiz_intentos == 1
    # Aun sin el caché local de progreso, OCI conserva eventos y claves.
    with e.db.transaccion():
        e.db._conn.execute("DELETE FROM workspace_progress")
    assert e.servicio.consultar(e.token).quiz_intentos == 1
    assert e.db.obtener_progreso_restaurado("ws_estudio") == estado


def test_storage_falla_no_promete_progreso_y_retry_mismo_id_recupera(estudio, monkeypatch):
    e = estudio
    original = e.storage.upload

    def fallar(clave, *args, **kwargs):
        if clave.startswith("progress/"):
            raise StorageUnavailable("detalle secreto")
        return original(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", fallar)
    with pytest.raises(StorageUnavailable):
        respuesta(e)
    assert e.db.obtener_progreso_restaurado("ws_estudio") is None
    monkeypatch.setattr(e.storage, "upload", original)
    assert respuesta(e).correcta
    assert e.servicio.consultar(e.token).quiz_intentos == 1


def test_put_confirmado_con_respuesta_perdida_no_se_aplica_dos_veces(estudio, monkeypatch):
    e = estudio
    original = e.storage.upload
    perdido = False

    def perder_respuesta(clave, *args, **kwargs):
        nonlocal perdido
        resultado = original(clave, *args, **kwargs)
        if clave.startswith("progress/") and not perdido:
            perdido = True
            raise StorageUnavailable("timeout después del PUT")
        return resultado

    monkeypatch.setattr(e.storage, "upload", perder_respuesta)
    with pytest.raises(StorageUnavailable):
        respuesta(e, correcta=False)
    assert e.db.obtener_progreso_restaurado("ws_estudio") is None
    assert respuesta(e, correcta=False).correcta is False
    assert e.servicio.consultar(e.token).quiz_intentos == 1


def test_cas_combina_eventos_de_dos_pestanas_y_un_solo_primer_intento(estudio, monkeypatch):
    e = estudio
    otro = ServicioProgreso(e.db, e.storage, e.catalogo)
    original = e.storage.upload
    barrera = threading.Barrier(2)
    lock = threading.Lock()
    llamados = 0

    def simultaneo(clave, *args, **kwargs):
        nonlocal llamados
        if clave.startswith("progress/"):
            with lock:
                llamados += 1
                esperar = llamados <= 2
            if esperar:
                barrera.wait(5)
        return original(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", simultaneo)
    solicitud = QuizAnswerRequest(question_id=e.pregunta.id, option_id=e.pregunta.correct_option_id, event_id="dos")
    with ThreadPoolExecutor(2) as executor:
        a = executor.submit(respuesta, e, "uno")
        b = executor.submit(otro.responder, e.token, e.paquete.generation_id, solicitud)
        assert a.result(10).correcta and b.result(10).correcta
    p = e.servicio.consultar(e.token)
    assert (p.quiz_intentos, p.quiz_total_primer_intento, p.quiz_aciertos_primer_intento) == (2, 1, 1)
    assert p.quiz_aciertos_posteriores == 1 and p.version == 2


def test_cas_conflicto_persistente_es_acotado_y_no_confirma_sqlite(estudio, monkeypatch):
    e = estudio
    llamadas = []

    def conflicto(*args, **kwargs):
        llamadas.append(1)
        raise StorageConflict("cambio concurrente")

    monkeypatch.setattr(e.storage, "upload", conflicto)
    with pytest.raises(ErrorAplicacion) as error:
        respuesta(e)
    assert error.value.error.code == ErrorCode.INVALID_STATE
    assert len(llamadas) == 3 and e.db.obtener_progreso_restaurado("ws_estudio") is None


@pytest.mark.parametrize("caso", ["ajeno", "pregunta", "opcion", "formato", "no_aprobado", "retirado_remoto"])
def test_quiz_invalido_no_escribe_progreso(estudio, caso):
    e = estudio
    token, gid = e.token, e.paquete.generation_id
    qid, opcion = e.pregunta.id, e.pregunta.correct_option_id
    esperado = ErrorCode.NOT_FOUND
    if caso == "ajeno":
        e.db.crear_workspace("ws_otro", "otro", dias_validez=30)
        e.db.crear_sesion("token-otro", "ws_otro", horas_validez=24)
        token = "token-otro"
    elif caso == "pregunta":
        qid = "desconocida"
    elif caso == "opcion":
        opcion, esperado = "X", ErrorCode.VALIDATION_ERROR
    elif caso == "formato":
        gid, esperado = e.publicar("gen_fc", FLASHCARDS).generation_id, ErrorCode.VALIDATION_ERROR
    elif caso == "no_aprobado":
        e.db.actualizar_generacion(gid, JobStatus.REJECTED_QUALITY)
        esperado = ErrorCode.INVALID_STATE
    else:
        manifest = json.loads(e.storage.get(clave_workspace("ws_estudio")))
        manifest["recursos_borrados"] = [
            {
                "recurso_tipo": "generation",
                "recurso_id": gid,
                "solicitado_en": "2026-10-01T00:00:00Z",
                "purga_despues_en": "2026-11-01T00:00:00Z",
            }
        ]
        e.storage.upload(clave_workspace("ws_estudio"), json.dumps(manifest))
    with pytest.raises(ErrorAplicacion) as error:
        e.servicio.responder(token, gid, QuizAnswerRequest(question_id=qid, option_id=opcion, event_id="rechazado"))
    assert error.value.error.code == esperado
    assert not e.storage.list(clave_progress("ws_estudio")).items


def test_retirada_local_y_remota_filtran_metricas_y_tiempo(estudio):
    e = estudio
    respuesta(e)
    e.servicio.registrar_evento(e.token, concepto(e))
    e.db.retirar_documento(e.token, "doc_fuente")
    p = e.servicio.consultar(e.token)
    assert p.quiz_intentos == p.conceptos_revisados == p.tiempo_estimado_restante_minutos == 0


@pytest.mark.parametrize(
    "datos",
    [
        {"tipo": "respuesta_quiz", "correcta": True},
        {"tipo": "concepto_revisado", "concepto": "VCN", "document_id": "doc_fuente", "primer_intento": True},
        {"tipo": "concepto_revisado", "concepto": " ", "document_id": "doc_fuente"},
        {"tipo": "flashcard_vista", "generation_id": "gen", "flashcard_id": "fc", "concepto": "VCN"},
    ],
)
def test_cliente_no_puede_fabricar_puntajes_ni_eventos_invalidos(datos):
    with pytest.raises(ValidationError):
        ProgressEvent.model_validate({"event_id": "falso", **datos})


def test_oci_ajeno_o_corrupto_no_se_convierte_en_cero(estudio):
    e = estudio
    for estado in ({"version": 1, "workspace_id": "ws_otro"}, {"version": 1, "eventos": [{}]}):
        e.storage.upload(clave_progress("ws_estudio"), json.dumps(estado))
        with pytest.raises(StorageUnavailable):
            e.servicio.consultar(e.token)


def test_cache_sqlite_no_retrocede_version_ni_acepta_secretos(estudio):
    e = estudio
    e.db.confirmar_progreso("ws_estudio", {"version": 4})
    e.db.confirmar_progreso("ws_estudio", {"version": 2})
    assert e.db.obtener_progreso_restaurado("ws_estudio") == {"version": 4}
    with pytest.raises(ValueError):
        e.db.confirmar_progreso("ws_estudio", {"version": 5, "token": "no guardar"})


def test_progreso_confirmado_desaparecido_no_se_reporta_como_cero(estudio):
    e = estudio
    respuesta(e)
    e.storage.delete(clave_progress("ws_estudio"))
    with pytest.raises(StorageUnavailable):
        e.servicio.consultar(e.token)
    with pytest.raises(StorageUnavailable):
        respuesta(e)


@pytest.mark.parametrize("clave", ["", " ", "a" * 129, " " + "a" * 128])
def test_header_idempotente_limites(estudio, clave):
    with pytest.raises(ErrorAplicacion) as error:
        respuesta(estudio, idempotency_key=clave)
    assert error.value.error.code == ErrorCode.INVALID_REQUEST


def test_concepto_suma_un_repaso_por_dia_y_expone_su_ultima_fecha(estudio):
    # Repasar el mismo concepto otro día suma un repaso; repetir el mismo día no.
    e = estudio
    for event_id, fecha in (("c-27", "2026-10-27"), ("c-27-otra-pestana", "2026-10-27"), ("c-28", "2026-10-28")):
        e.servicio.registrar_evento(e.token, concepto(e, event_id, fecha=fecha))
    e.servicio.registrar_evento(e.token, concepto(e, "c-sin-fecha"))
    p = e.servicio.consultar(e.token)
    assert p.conceptos_revisados == 1
    assert p.conceptos_repasos == 2  # 27/10, 28/10 y las marcas sin fecha cuentan como tres días.
    [detalle] = p.conceptos
    assert (detalle.concepto, detalle.veces, detalle.ultima_fecha) == ("VCN", 3, "2026-10-28")


@pytest.mark.parametrize("fecha", ["27/10/2026", "2026-10-7", ""])
def test_fecha_de_concepto_invalida_se_rechaza(fecha):
    with pytest.raises(ValidationError):
        ProgressEvent.model_validate(
            dict(event_id="c", tipo="concepto_revisado", document_id="d", concepto="VCN", fecha=fecha)
        )


def test_flashcard_no_admite_fecha():
    with pytest.raises(ValidationError):
        ProgressEvent.model_validate(
            dict(event_id="c", tipo="flashcard_vista", generation_id="g", flashcard_id="f", fecha="2026-10-27")
        )
