"""Snapshot admitido con HTTP, SQLite y worker reales; motor pedagógico doble."""

import json
import threading
import time

import pytest
import test_api_generate as generation_cases
from app.schemas.enums import DocumentStatus
from test_space_idempotency import espacio as espacio
from test_space_idempotency import pedir, terminado

pytestmark = pytest.mark.integration_mock
entorno_nativo = generation_cases.entorno


def esperar_terminal(e, generation_id):
    limite = time.monotonic() + 5
    while time.monotonic() < limite:
        fila = e.db.job_generacion(generation_id)
        if fila["status"] not in {"queued", "running"}:
            return fila
        time.sleep(0.01)
    pytest.fail("El worker no alcanzó un estado terminal")


@pytest.mark.parametrize("modo", ["basico", "multiagente"])
@pytest.mark.parametrize("cambio", ["hash_primario", "hash_secundario", "titulo", "version", "procedencia", "retiro"])
def test_cambio_entre_admision_y_worker_no_inicia_motor(espacio, monkeypatch, modo, cambio):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    objetivo = "doc_a" if cambio == "hash_primario" else "doc_b"
    esperando, liberar = threading.Event(), threading.Event()
    original = e.servicio._trabajo
    parser = []
    monkeypatch.setattr(e.consulta, "version_parser", lambda *args: parser.append(args) or "1.0")

    def trabajo(*args):
        ejecutar = original(*args)

        def suspendido(ctx):
            esperando.set()
            assert liberar.wait(5)
            return ejecutar(ctx)

        return suspendido

    monkeypatch.setattr(e.servicio, "_trabajo", trabajo)
    respuesta = pedir(e, modo_generacion=modo)
    assert respuesta.status_code == 202, respuesta.text
    generation_id = respuesta.json()["generation_id"]
    try:
        assert esperando.wait(5)
        snapshot = e.db.snapshot_generacion(generation_id)
        if cambio.startswith("hash"):
            e.db.actualizar_documento(objetivo, DocumentStatus.READY, hash_documento="f" * 64)
        elif cambio == "retiro":
            assert e.db.retirar_documento("token-a", objetivo)
        else:
            documento_original = e.consulta.documento_grafo

            def consultar(ctx, doc_id):
                doc = documento_original(ctx, doc_id)
                if doc_id == objetivo:
                    doc = doc.model_copy(update={"fuente": doc.fuente.model_copy(update={cambio: "otra"})})
                return doc

            monkeypatch.setattr(e.consulta, "documento_grafo", consultar)
    finally:
        liberar.set()
    fila = esperar_terminal(e, generation_id)
    assert fila["status"] in {"failed", "cancelled"}, fila
    if fila["status"] == "failed":
        assert fila["error_code"] == "INVALID_STATE"
    assert not e.ejecutados and not parser
    assert e.db.snapshot_generacion(generation_id) == snapshot
    assert not any(clave.startswith("outputs/") for clave in e.storage.objetos)
    assert e.db._conn.execute("SELECT COUNT(*) FROM approved_outputs").fetchone()[0] == 0


@pytest.mark.parametrize("modo", ["basico", "multiagente"])
def test_fuentes_sin_cambios_conservan_snapshot_y_contrato(espacio, modo):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")
    respuesta = pedir(e, modo_generacion=modo)
    salida = terminado(e, respuesta)
    generation_id = respuesta.json()["generation_id"]
    assert len(e.ejecutados) == 1
    assert salida["status"] == "completed"
    assert e.db.snapshot_generacion(generation_id) == [
        e.consulta.fuente_aprobada("token-a", d).model_dump(mode="json")
        for d in e.db.documentos_generacion(generation_id)
    ]


@pytest.mark.parametrize("snapshot", [None, "{", "[]", '[{"document_id":"solo_declarado"}]'])
def test_worker_no_reconstruye_procedencia_ausente(espacio, monkeypatch, snapshot):
    e = espacio
    e.documento("doc_a")
    original = e.servicio._trabajo

    def trabajo(solicitud, generation_id, creado_en):
        ejecutar = original(solicitud, generation_id, creado_en)

        def sin_procedencia(ctx):
            with e.db.transaccion():
                e.db._conn.execute(
                    "UPDATE generations SET snapshot_fuentes_json=? WHERE generation_id=?", (snapshot, generation_id)
                )
            return ejecutar(ctx)

        return sin_procedencia

    monkeypatch.setattr(e.servicio, "_trabajo", trabajo)
    respuesta = pedir(e)
    assert respuesta.status_code == 202, respuesta.text
    fila = esperar_terminal(e, respuesta.json()["generation_id"])
    assert (fila["status"], fila["error_code"]) == ("failed", "INVALID_STATE")
    assert not e.ejecutados


def test_lectura_posterior_a_parser_sigue_vinculada_al_snapshot(espacio, monkeypatch):
    """La guarda del grafo también protege la I/O posterior al preflight."""
    from app.generations import service

    e = espacio
    e.documento("doc_a")
    e.documento("doc_b")

    def parser(*args):
        e.db.actualizar_documento("doc_b", DocumentStatus.READY, hash_documento="b" * 64)
        return "1.0"

    def motor(estado, deps):
        deps.grafo.obtener_documento(estado.workspace_id, "doc_b")
        pytest.fail("Se aceptó otra versión después de I/O")

    monkeypatch.setattr(e.consulta, "version_parser", parser)
    monkeypatch.setattr(service, "ejecutar_en_worker", motor)
    respuesta = pedir(e)
    assert respuesta.status_code == 202, respuesta.text
    fila = esperar_terminal(e, respuesta.json()["generation_id"])
    assert (fila["status"], fila["error_code"]) == ("failed", "INVALID_STATE")
    assert not e.ejecutados


def test_snapshot_de_otro_workspace_no_autoriza_ejecucion(espacio, monkeypatch):
    e = espacio
    e.documento("doc_a")
    e.documento("doc_x", workspace="ws_b")
    original = e.servicio._trabajo

    def trabajo(solicitud, generation_id, creado_en):
        ejecutar = original(solicitud, generation_id, creado_en)

        def adulterado(ctx):
            fuente = e.db.snapshot_generacion(generation_id)[0]
            fuente["document_id"] = "doc_x"
            with e.db.transaccion():
                e.db._conn.execute(
                    "UPDATE generations SET snapshot_fuentes_json=? WHERE generation_id=?",
                    (json.dumps([fuente]), generation_id),
                )
            return ejecutar(ctx)

        return adulterado

    monkeypatch.setattr(e.servicio, "_trabajo", trabajo)
    respuesta = pedir(e)
    assert respuesta.status_code == 202, respuesta.text
    fila = esperar_terminal(e, respuesta.json()["generation_id"])
    assert (fila["status"], fila["error_code"]) == ("failed", "INVALID_STATE")
    assert not e.ejecutados


@pytest.mark.parametrize("modo", ["basico", "multiagente"])
def test_snapshot_con_consulta_indice_y_worker_reales_no_consume_proveedor(entorno_nativo, monkeypatch, modo):
    """Únicamente OCI/Gemini son simulados; no se sustituye el motor ni la consulta."""
    e = entorno_nativo
    servicio = e.app.state.generations
    original = servicio._trabajo
    esperando, liberar = threading.Event(), threading.Event()
    llamadas = (e.doble.llamadas_embeddings, e.doble.llamadas_generacion, e.doble.llamadas_verificacion)

    def trabajo(*args):
        ejecutar = original(*args)

        def suspendido(ctx):
            esperando.set()
            assert liberar.wait(5)
            return ejecutar(ctx)

        return suspendido

    monkeypatch.setattr(servicio, "_trabajo", trabajo)
    res = e.cliente.post("/api/generate", json={**e.solicitud, "modo_generacion": modo}, headers=e.headers)
    assert res.status_code == 202, res.text
    generation_id = res.json()["generation_id"]
    try:
        assert esperando.wait(5)
        snapshot = e.db.snapshot_generacion(generation_id)
        e.db.actualizar_documento(e.doc, DocumentStatus.READY, hash_documento="f" * 64)
    finally:
        liberar.set()
    fila = esperar_terminal(e, generation_id)
    assert fila["status"] in {"failed", "cancelled"}, fila
    assert (e.doble.llamadas_embeddings, e.doble.llamadas_generacion, e.doble.llamadas_verificacion) == llamadas
    assert not e.storage.list("outputs/").items
    assert e.db.snapshot_generacion(generation_id) == snapshot
