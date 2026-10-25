"""La autorización de todo el espacio no inmoviliza al gestor de otros espacios."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
import test_space_idempotency as space_cases
from app.generations import service as generation_service
from app.generations.service import ServicioGeneraciones
from app.jobs.manager import EspacioOcupadoError
from app.schemas.requests import GenerateRequest
from test_space_idempotency import INTENCION, intencion, pedir, terminado

pytestmark = pytest.mark.integration_mock
espacio = space_cases.espacio


def solicitud():
    return GenerateRequest.model_validate(json.loads(INTENCION))


def bloquear_autorizacion(e, monkeypatch, metodo):
    esperando = threading.Event()
    liberar = threading.Event()
    original = getattr(e.consulta, metodo)

    def autorizar(*args, **kwargs):
        esperando.set()
        assert liberar.wait(10), "El test no liberó la autorización documental"
        return original(*args, **kwargs)

    monkeypatch.setattr(e.consulta, metodo, autorizar)
    return esperando, liberar


def trabajo_ajeno(e):
    iniciado = threading.Event()
    terminar = threading.Event()
    finalizado = threading.Event()
    contextos = []

    def ejecutar(ctx):
        contextos.append(ctx)
        iniciado.set()
        assert terminar.wait(10), "El test no liberó el trabajo de ws_b"
        ctx.chequear()
        return {"workspace": "ws_b"}

    job_id = e.gestor.enqueue("ws_b", "chat", ejecutar, al_terminar=lambda status: finalizado.set())
    assert iniciado.wait(2), "El worker de ws_b no comenzó"
    return job_id, contextos, terminar, finalizado


def consultar_y_cancelar(e, job_id):
    estado = e.gestor.estado(job_id)
    assert estado["status"] == "running"
    assert e.gestor.cancelar(job_id)
    return estado


@pytest.mark.parametrize("clave", [None, "todo"])
@pytest.mark.parametrize("metodo", ["_listo", "documento_grafo"])
def test_admision_bloqueada_no_impide_estado_y_cancelacion_ajenos(espacio, monkeypatch, clave, metodo):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    job_b, contextos, terminar_b, finalizado_b = trabajo_ajeno(e)
    esperando, liberar = bloquear_autorizacion(e, monkeypatch, metodo)
    with ThreadPoolExecutor(2) as executor:
        try:
            admision = executor.submit(e.servicio.generar, "token-a", solicitud(), clave)
            assert esperando.wait(2), "La admisión no llegó a la autorización"
            operacion_b = executor.submit(consultar_y_cancelar, e, job_b)
            assert operacion_b.result(timeout=2)["workspace_id"] == "ws_b"
            assert contextos[0].cancelado(), "La cancelación debe activarse antes de liberar la autorización de ws_a"
            assert not liberar.is_set()
            assert not admision.done()
            terminar_b.set()
            assert finalizado_b.wait(2), "La finalización de ws_b quedó detrás de la autorización de ws_a"
            assert e.db.obtener_job(job_b)["status"] == "cancelled"
        finally:
            liberar.set()
            terminar_b.set()
        fila = admision.result(timeout=2)
    assert fila["workspace_id"] == "ws_a"
    if clave:
        assert intencion(e, clave)["recurso_id"] == fila["generation_id"]
    assert e.db._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 1


@pytest.mark.parametrize("finalizar", [False, True], ids=["cancelar", "completar"])
def test_reintento_reconocido_autoriza_sin_bloquear_al_gestor(espacio, monkeypatch, finalizar):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    original = terminado(e, pedir(e))
    intencion_original = intencion(e)
    job_b, contextos, terminar_b, finalizado_b = trabajo_ajeno(e)
    esperando, liberar = bloquear_autorizacion(e, monkeypatch, "detalle")
    with ThreadPoolExecutor(2) as executor:
        try:
            repeticion = executor.submit(e.servicio.generar, "token-a", solicitud(), "todo")
            assert esperando.wait(2), "El reintento no llegó a la autorización del material"
            if finalizar:
                terminar_b.set()
                assert finalizado_b.wait(2), "El worker no pudo confirmar su resultado durante la autorización"
                estado = executor.submit(e.gestor.estado, job_b).result(timeout=2)
                assert estado["status"] == "completed"
                assert json.loads(estado["resultado"]) == {"workspace": "ws_b"}
            else:
                executor.submit(consultar_y_cancelar, e, job_b).result(timeout=2)
                assert contextos[0].cancelado()
                terminar_b.set()
                assert finalizado_b.wait(2)
                assert e.db.obtener_job(job_b)["status"] == "cancelled"
            assert not liberar.is_set()
            assert not repeticion.done()
        finally:
            liberar.set()
            terminar_b.set()
        fila = repeticion.result(timeout=2)
    assert fila["generation_id"] == original["generation_id"]
    assert intencion(e) == intencion_original
    assert len(e.ejecutados) == 1
    assert e.db._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 1


def test_finalizacion_ajena_progresa_durante_autorizacion_de_admision_nueva(espacio, monkeypatch):
    e = espacio
    e.documento("doc_a")
    job_b, _, terminar_b, finalizado_b = trabajo_ajeno(e)
    esperando, liberar = bloquear_autorizacion(e, monkeypatch, "_listo")
    with ThreadPoolExecutor(1) as executor:
        try:
            admision = executor.submit(e.servicio.generar, "token-a", solicitud(), "todo")
            assert esperando.wait(2)
            terminar_b.set()
            assert finalizado_b.wait(2), "La finalización del worker quedó bloqueada por I/O de otra admisión"
            assert e.db.obtener_job(job_b)["status"] == "completed"
            assert not liberar.is_set()
            assert not admision.done()
        finally:
            liberar.set()
            terminar_b.set()
        admision.result(timeout=2)


def test_concurrentes_de_la_misma_intencion_resuelven_y_ejecutan_una_vez(espacio, monkeypatch):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    resolver = e.db.documentos_ready
    resoluciones = []
    barrera = threading.Barrier(3)
    esperando, liberar = bloquear_autorizacion(e, monkeypatch, "_listo")
    otro_servicio = ServicioGeneraciones(e.db, e.storage, e.consulta, e.gestor, e.cfg, e.proveedor, e.proveedor)
    otro_servicio.retriever = e.servicio.retriever

    def resolver_una_vez(workspace_id):
        resoluciones.append(workspace_id)
        return resolver(workspace_id)

    def enviar(servicio):
        barrera.wait(2)
        return servicio.generar("token-a", solicitud(), "todo")

    monkeypatch.setattr(e.db, "documentos_ready", resolver_una_vez)
    with ThreadPoolExecutor(3) as executor:
        pendientes = [executor.submit(enviar, servicio) for servicio in (e.servicio, otro_servicio, e.servicio)]
        try:
            assert esperando.wait(2)
            assert resoluciones == ["ws_a"]
        finally:
            liberar.set()
        filas = [pendiente.result(timeout=2) for pendiente in pendientes]
    assert len({fila["generation_id"] for fila in filas}) == 1
    assert resoluciones == ["ws_a"]
    terminado(e, pedir(e))
    assert e.db._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 1
    assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert len(e.ejecutados) == 1
    assert (e.gestor, "ws_a") not in generation_service._COORDINACIONES_ESPACIO


@pytest.mark.parametrize("metodo", ["autorizacion", "enqueue"])
def test_excepcion_libera_coordinacion_y_permite_reintentar_sin_reservas(espacio, monkeypatch, metodo):
    e = espacio
    e.documento("doc_a")
    objeto, nombre = (e.consulta, "_listo") if metodo == "autorizacion" else (e.gestor, "enqueue")
    original = getattr(objeto, nombre)

    def fallar(*args, **kwargs):
        raise EspacioOcupadoError("ws_a")

    monkeypatch.setattr(objeto, nombre, fallar)
    with pytest.raises(EspacioOcupadoError):
        e.servicio.generar("token-a", solicitud(), "todo")
    assert (e.gestor, "ws_a") not in generation_service._COORDINACIONES_ESPACIO
    assert intencion(e) is None
    for tabla in ("generations", "generation_documents", "jobs", "job_documents"):
        assert e.db._conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0
    monkeypatch.setattr(objeto, nombre, original)
    # Otro hilo debe poder entrar: un RLock abandonado por el caller original
    # quedaría oculto si la repetición se hiciera en ese mismo hilo.
    with ThreadPoolExecutor(1) as executor:
        fila = executor.submit(e.servicio.generar, "token-a", solicitud(), "todo").result(timeout=2)
    assert intencion(e)["recurso_id"] == fila["generation_id"]
    terminado(e, pedir(e))
    assert e.db._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 1
    assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert len(e.ejecutados) == 1
    assert (e.gestor, "ws_a") not in generation_service._COORDINACIONES_ESPACIO
