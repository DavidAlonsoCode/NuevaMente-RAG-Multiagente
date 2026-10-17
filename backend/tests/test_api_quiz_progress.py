"""Flujo HTTP #35 con ingestión, worker, grafo y aprobados reales; LLM simulado."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import test_api_generate as generaciones
from app.schemas.errors import ErrorCode
from app.storage.object_keys import clave_progress
from app.storage.provider import StorageUnavailable

pytestmark = pytest.mark.integration_mock
entorno = generaciones.entorno


def quiz(e):
    recibo = generaciones.generar(e, formato_salida="quiz").json()
    resultado = generaciones.esperar(e, recibo)
    estudiante = resultado["contenido"]["contenido_adaptado"]["preguntas"][0]
    assert "correct_option_id" not in estudiante and "justificacion" not in estudiante
    paquete = e.app.state.generations.canonico(e.ws["token"], recibo["generation_id"])
    pregunta = paquete.contenido_adaptado.preguntas[0]
    return recibo, pregunta


def test_feedback_http_inmediato_reintentos_sin_llm_ni_cola(entorno):
    e = entorno
    recibo, pregunta = quiz(e)
    ruta = f"/api/quizzes/{recibo['generation_id']}/answers"
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    opcion_mala = next(o.option_id for o in pregunta.opciones if o.option_id != pregunta.correct_option_id)
    body = dict(event_id="respuesta-inicial", question_id=pregunta.id, option_id=opcion_mala)
    mala = e.cliente.post(ruta, json=body, headers=e.headers)
    assert mala.status_code == 200, mala.text
    assert mala.json() == {
        "correcta": False,
        "correct_option_id": pregunta.correct_option_id,
        "justificacion": pregunta.justificacion,
        "referencias": [r.model_dump() for r in pregunta.referencias],
    }
    assert e.cliente.post(ruta, json=body, headers=e.headers).json() == mala.json()
    assert (
        e.cliente.post(ruta, json={**body, "option_id": pregunta.correct_option_id}, headers=e.headers).status_code
        == 409
    )
    buena = e.cliente.post(
        ruta, json={**body, "event_id": "segundo-intento", "option_id": pregunta.correct_option_id}, headers=e.headers
    )
    assert buena.status_code == 200 and buena.json()["correcta"] is True
    # La corrección sigue disponible sin runtime de generación ni índice.
    e.app.state.generations = None
    indice = e.app.state.documents_query.indice
    e.app.state.documents_query.indice = None
    try:
        assert e.cliente.post(ruta, json=body, headers=e.headers).status_code == 200
    finally:
        e.app.state.documents_query.indice = indice
    p = e.cliente.get("/api/progress", headers=e.headers)
    assert p.status_code == 200, p.text
    assert (
        p.json()["quiz_intentos"],
        p.json()["quiz_total_primer_intento"],
        p.json()["quiz_aciertos_primer_intento"],
    ) == (2, 1, 0)
    assert p.json()["quiz_aciertos_posteriores"] == 1
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    assert not e.db.listar_jobs_encolados()
    assert e.db.contar_activos_de_workspace(e.ws["workspace_id"]) == 0


def test_http_progreso_idempotencia_forgery_ajeno_y_borrado(entorno):
    e = entorno
    recibo = generaciones.generar(e).json()
    paquete = generaciones.esperar(e, recibo)["contenido"]
    card = paquete["contenido_adaptado"]["items"][0]["id"]
    body = dict(event_id="carta-1", tipo="flashcard_vista", generation_id=recibo["generation_id"], flashcard_id=card)
    headers = {**e.headers, "Idempotency-Key": "carta-intencion"}
    visto = e.cliente.post("/api/progress/events", json=body, headers=headers)
    assert visto.status_code == 200, visto.text
    assert e.cliente.post("/api/progress/events", json=body, headers=headers).json() == visto.json()
    assert (
        e.cliente.post("/api/progress/events", json={**body, "event_id": "carta-2"}, headers=headers).status_code == 409
    )
    assert e.cliente.post("/api/progress/events", json={**body, "correcta": True}, headers=e.headers).status_code == 422
    assert (
        e.cliente.post("/api/progress/events", json={**body, "tipo": "respuesta_quiz"}, headers=e.headers).status_code
        == 422
    )
    otro = e.cliente.post("/api/workspaces").json()
    ajeno = {"Authorization": "Bearer " + otro["token"]}
    assert e.cliente.post("/api/progress/events", json=body, headers=ajeno).status_code == 404
    assert e.cliente.get("/api/progress", headers=ajeno).json()["flashcards_vistas"] == 0
    assert e.cliente.get("/api/progress").status_code == 401
    assert e.cliente.get("/api/progress", headers=e.headers).json()["flashcards_vistas"] == 1
    borrado = e.cliente.delete(f"/api/documents/{e.doc}", headers=e.headers)
    assert borrado.status_code in {200, 202, 204}, borrado.text
    p = e.cliente.get("/api/progress", headers=e.headers).json()
    assert p["flashcards_vistas"] == p["tiempo_estimado_restante_minutos"] == 0
    estado = json.loads(e.storage.get(clave_progress(e.ws["workspace_id"])))
    assert not estado["eventos"] and not estado["claves"]


def test_http_quiz_ajeno_y_fallo_storage_sin_datos_sensibles(entorno, monkeypatch):
    e = entorno
    recibo, pregunta = quiz(e)
    ruta = f"/api/quizzes/{recibo['generation_id']}/answers"
    body = dict(event_id="respuesta", question_id=pregunta.id, option_id=pregunta.correct_option_id)
    otro = e.cliente.post("/api/workspaces").json()
    assert e.cliente.post(ruta, json=body, headers={"Authorization": "Bearer " + otro["token"]}).status_code == 404
    original = e.storage.upload

    def falla(clave, *args, **kwargs):
        if clave.startswith("progress/"):
            raise StorageUnavailable("secreto que no puede aparecer")
        return original(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", falla)
    r = e.cliente.post(ruta, json=body, headers=e.headers)
    assert r.status_code == 503 and r.json()["error"]["code"] == ErrorCode.STORAGE_UNAVAILABLE.value
    assert "secreto" not in r.text and "X-Request-ID" in r.headers
    assert e.db.obtener_progreso_restaurado(e.ws["workspace_id"]) is None
    monkeypatch.setattr(e.storage, "upload", original)
    assert e.cliente.post(ruta, json=body, headers=e.headers).json()["correcta"] is True


def test_borrado_durante_put_no_resucita_progreso(entorno, monkeypatch):
    e = entorno
    recibo, pregunta = quiz(e)
    ruta = f"/api/quizzes/{recibo['generation_id']}/answers"
    entrado, liberar = threading.Event(), threading.Event()
    original = e.storage.upload

    def pausar(clave, *args, **kwargs):
        if clave.startswith("progress/") and not entrado.is_set():
            entrado.set()
            assert liberar.wait(5)
        return original(clave, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", pausar)
    with ThreadPoolExecutor(2) as executor:
        respuesta = executor.submit(
            e.cliente.post,
            ruta,
            json=dict(event_id="en-vuelo", question_id=pregunta.id, option_id=pregunta.correct_option_id),
            headers=e.headers,
        )
        assert entrado.wait(5)
        borrar = executor.submit(e.cliente.delete, f"/api/documents/{e.doc}", headers=e.headers)
        # La lápida local se confirma antes de esperar el lock del escritor.
        limite = time.monotonic() + 5
        while not e.db.esta_borrado("document", e.doc) and time.monotonic() < limite:
            time.sleep(0.01)
        assert e.db.esta_borrado("document", e.doc)
        liberar.set()
        assert respuesta.result(10).status_code == 404
        assert borrar.result(10).status_code in {200, 202, 204}
    e.app.state.documents_effects.reconciliar(e.doc)
    estado = json.loads(e.storage.get(clave_progress(e.ws["workspace_id"])))
    assert not estado["eventos"] and not estado["claves"]


def test_perdida_del_disco_recupera_progreso_desde_oci(entorno, tmp_path):
    from app.documents.query import ServicioConsultaDocumental
    from app.generations.catalog import CatalogoGeneraciones
    from app.jobs.store import RegistroOperativo
    from app.progress.service import ServicioProgreso
    from app.session.manager import SessionManager
    from app.session.recovery import restaurar_registro

    e = entorno
    recibo, pregunta = quiz(e)
    body = dict(event_id="recuperable", question_id=pregunta.id, option_id=pregunta.correct_option_id)
    assert (
        e.cliente.post(
            f"/api/quizzes/{recibo['generation_id']}/answers",
            json=body,
            headers={**e.headers, "Idempotency-Key": "intencion-recuperable"},
        ).status_code
        == 200
    )
    nuevo = RegistroOperativo(tmp_path / "disco_nuevo" / "operativo.db")
    try:
        assert restaurar_registro(nuevo, e.storage) == 1
        assert nuevo.obtener_sesion(e.ws["token"]) is None
        sesion = SessionManager(nuevo, e.storage, 30, 24).recuperar_sesion(e.ws["recovery_code"])
        consulta = ServicioConsultaDocumental(nuevo, e.storage, None, plazo_lectura=30)
        servicio = ServicioProgreso(
            nuevo, e.storage, CatalogoGeneraciones(nuevo, e.storage, consulta, e.app.state.config.oci_bucket_name)
        )
        p = servicio.consultar(sesion.token)
        assert p.quiz_intentos == p.quiz_total_primer_intento == p.quiz_aciertos_primer_intento == 1
        restaurado = nuevo.obtener_progreso_restaurado(e.ws["workspace_id"])
        assert restaurado["eventos"][0]["event_id"] == "recuperable" and len(restaurado["claves"]) == 1
    finally:
        nuevo.cerrar()
