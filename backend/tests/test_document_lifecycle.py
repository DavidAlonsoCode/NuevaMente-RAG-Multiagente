"""Interrupciones reales de persistencia, reinicios y efectos durables de #19."""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.api.security import _peticiones_globales, _peticiones_ip
from app.config import Configuracion
from app.core.rag.embeddings import GeminiEmbeddings
from app.core.rag.parser import LimitesIngesta
from app.core.rag.rebuild import ManifiestoDocumento
from app.core.rag.vectorstore import VectorStoreChroma, _filtro
from app.documents.cursor import CursorDocumental
from app.documents.ingestion import crear_servicio_ingestion
from app.documents.lifecycle import EfectosDocumentales
from app.jobs.manager import CuotasModelo, CuotasProveedor, GestorTrabajos
from app.jobs.store import RegistroOperativo
from app.main import crear_app
from app.schemas.documents import EntradaArchivoDocumento
from app.schemas.enums import DocumentStatus, JobStatus
from app.session.manifests import Lapida, ManifiestoEspacio, SincronizadorManifiestos, sincronizar_manifiesto
from app.storage.object_keys import clave_documento, clave_original, clave_workspace
from app.storage.provider import StorageNotFound, StorageUnavailable
from doubles.gemini import DobleGemini
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration_mock
MODELO = "gemini-embedding-2"
TEXTO = ("# Redes\n\nLas subredes conservan rutas privadas y aislamiento entre espacios.\n" * 12).encode()


def hasta(condicion):
    limite = time.monotonic() + 10
    while time.monotonic() < limite:
        if valor := condicion():
            return valor
        time.sleep(0.005)
    pytest.fail("No se confirmó la condición")


def abrir(e):
    e.db = RegistroOperativo(e.ruta / "operativo.db")
    e.gestor = GestorTrabajos(e.db, cuotas=e.cuotas)
    e.indice = VectorStoreChroma(e.ruta / "chroma", GeminiEmbeddings(e.doble, modelo=MODELO, dimensiones=16))
    e.servicio = crear_servicio_ingestion(
        e.db, e.storage, e.gestor, e.indice, limites=replace(LimitesIngesta(), minimo_tokens_documento=3)
    )
    e.efectos = e.servicio.procesar.efectos


@pytest.fixture
def entorno(tmp_path, mock_storage):
    e = SimpleNamespace(
        ruta=tmp_path,
        storage=mock_storage,
        doble=DobleGemini(16),
        cuotas=CuotasProveedor({MODELO: CuotasModelo(1000, 1000000, 1000)}),
    )
    abrir(e)
    for ws in ("ws_a", "ws_b"):
        e.db.crear_workspace(ws, "codigo_" + ws, 30)
        e.db.crear_sesion("token_" + ws, ws, 24)
        sincronizar_manifiesto(e.db, e.storage, ws)
    yield e
    e.gestor.detener(timeout=None)
    e.indice.cerrar()
    e.db.cerrar()


def admitir(e, clave="estable", ws="ws_a"):
    return e.servicio.admitir_archivo(
        "token_" + ws, TEXTO, EntradaArchivoDocumento(source_name="redes.md"), clave=clave
    )


def terminal(e, job_id):
    # El terminal SQL precede al observador. Esperar también su finalización
    # antes de simular pérdida de otro proceso evita solapar dos escenarios.
    return hasta(
        lambda: (
            (j := e.db.obtener_job(job_id))
            and j["status"] not in ("queued", "running")
            and job_id not in e.gestor._ejecutables
            and e.gestor._observadores_en_vuelo == 0
            and j
        )
    )


def mf(e, doc):
    return ManifiestoDocumento.model_validate_json(e.storage.get(clave_documento("ws_a", doc)))


def debido(e):
    with e.db.transaccion():
        e.db._conn.execute("UPDATE document_cleanup SET proximo_intento_en=NULL")


def sin_chunks(e, doc):
    assert e.indice._activo(e.indice._privada, "ws_a", doc) is None
    for col in e.indice._cliente.list_collections():
        if col.name.startswith("embeddings-"):
            assert not e.indice._cliente.get_collection(col.name, embedding_function=None).get(
                where=_filtro("ws_a", doc), include=[]
            )["ids"]


@pytest.mark.parametrize("aplicada", [False, True])
def test_admision_incierta_reconcilia_identidades_sin_reencolar(entorno, monkeypatch, aplicada):
    e = entorno
    upload = e.storage.upload

    def perdida(clave, contenido, **kw):
        if clave.endswith("manifest.json") and json.loads(contenido).get("admision_sqlite_confirmada") is True:
            if aplicada:
                upload(clave, contenido, **kw)
            raise StorageUnavailable("respuesta privada perdida")
        return upload(clave, contenido, **kw)

    monkeypatch.setattr(e.storage, "upload", perdida)
    with pytest.raises(Exception):
        admitir(e)
    fila = dict(e.db._conn.execute("SELECT * FROM document_admissions").fetchone())
    terminal(e, fila["job_id"])
    hasta(lambda: e.gestor._observadores_en_vuelo == 0)
    assert fila["estado"] == "encolada"
    assert (
        e.db.listar_efectos_documentales()
        or e.db._conn.execute("SELECT 1 FROM document_cleanup WHERE estado='reservada'").fetchone()
    )
    llamadas = e.doble.llamadas_embeddings
    monkeypatch.setattr(e.storage, "upload", upload)
    debido(e)
    assert e.efectos.reconciliar() == 1
    recuperado = admitir(e)
    assert (recuperado.document_id, recuperado.job_id) == (fila["document_id"], fila["job_id"])
    assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert e.doble.llamadas_embeddings == llamadas
    assert mf(e, recuperado.document_id).admision_sqlite_confirmada is True
    assert mf(e, recuperado.document_id).estado == DocumentStatus.FAILED
    assert e.db.obtener_documento(recuperado.document_id)["estado"] == "failed"
    sin_chunks(e, recuperado.document_id)
    assert e.efectos.reconciliar() == 0


def test_final_oci_sin_commit_terminal_se_compensa_despues_de_reinicio(entorno, monkeypatch):
    e = entorno
    finalizar = e.db.finalizar_job

    def sin_sqlite(*a, **kw):
        raise sqlite3.OperationalError("fallo de commit")

    monkeypatch.setattr(e.db, "finalizar_job", sin_sqlite)
    recibo = admitir(e)
    hasta(lambda: not e.gestor._hilo.is_alive())
    assert mf(e, recibo.document_id).estado == DocumentStatus.READY
    assert e.db.obtener_documento(recibo.document_id)["estado"] == "processing"
    assert e.db.obtener_job(recibo.job_id)["status"] == "running"
    assert e.indice._activo(e.indice._privada, "ws_a", recibo.document_id) is not None
    llamadas = e.doble.llamadas_embeddings
    monkeypatch.setattr(e.db, "finalizar_job", finalizar)
    e.gestor.detener()
    e.indice.cerrar()
    e.db.cerrar()
    abrir(e)
    assert e.db.obtener_job(recibo.job_id)["error_code"] == "INTERRUPTED"
    assert e.efectos.reconciliar() == 1
    assert mf(e, recibo.document_id).estado == DocumentStatus.FAILED
    sin_chunks(e, recibo.document_id)
    assert e.db.obtener_documento(recibo.document_id)["estado"] == "failed"
    assert admitir(e).job_id == recibo.job_id
    assert e.doble.llamadas_embeddings == llamadas
    assert e.db.eventos_desde_job(recibo.job_id)[-1]["status"] == "failed"


@pytest.mark.parametrize("falla", ["oci", "chroma", "ack_sqlite"])
def test_compensacion_durable_reintenta_efectos_reales_despues_de_reinicio(entorno, monkeypatch, falla):
    e = entorno
    recibo = admitir(e)
    assert terminal(e, recibo.job_id)["status"] == "completed"
    # Reproduce pérdida del observador: commit terminal real, sin callback.
    with e.db.transaccion():
        e.db.actualizar_documento(recibo.document_id, DocumentStatus.FAILED, detalle_error="Interrumpido")
        e.db._conn.execute("UPDATE jobs SET status='failed' WHERE job_id=?", (recibo.job_id,))
    llamadas = e.doble.llamadas_embeddings
    objetivo, nombre = (
        (e.storage, "upload")
        if falla == "oci"
        else (type(e.indice._privada), "delete")
        if falla == "chroma"
        else (e.db, "registrar_resultado_efecto_documental")
    )
    original = getattr(objetivo, nombre)

    def error(*a, **kw):
        raise StorageUnavailable("diagnostico privado")

    monkeypatch.setattr(objetivo, nombre, error)
    if falla == "ack_sqlite":
        with pytest.raises(StorageUnavailable):
            e.efectos.reconciliar()
    else:
        assert e.efectos.reconciliar() == 0
    pendiente = e.db._conn.execute("SELECT * FROM document_cleanup").fetchone()
    assert pendiente["estado"] == "reservada" and pendiente["completado_en"] is None
    if falla == "oci":
        sin_chunks(e, recibo.document_id)  # OCI caído no conserva un índice fallido activo.
    if falla == "chroma":
        assert e.indice._privada.get(where=_filtro("ws_a", recibo.document_id), include=[])["ids"]
    monkeypatch.setattr(objetivo, nombre, original)
    e.gestor.detener()
    e.indice.cerrar()
    e.db.cerrar()
    abrir(e)
    debido(e)
    assert e.efectos.reconciliar() == 1
    sin_chunks(e, recibo.document_id)
    assert mf(e, recibo.document_id).estado == DocumentStatus.FAILED
    assert e.db._conn.execute("SELECT estado FROM document_cleanup").fetchone()[0] == "completada"
    assert e.doble.llamadas_embeddings == llamadas


def test_admision_fallida_y_compensaciones_secundarias_se_purgan_tras_reinicio(entorno, monkeypatch):
    e = entorno
    persistir, fallar, cancelar = e.servicio._persistir_confirmado, e.db.fallar_admision_documental, e.gestor.cancelar

    def original_sin_manifest(clave, contenido, **kw):
        if clave.endswith("manifest.json"):
            raise StorageUnavailable("primario privado")
        return persistir(clave, contenido, **kw)

    def secundario(*a, **kw):
        raise sqlite3.OperationalError("secundario privado")

    monkeypatch.setattr(e.servicio, "_persistir_confirmado", original_sin_manifest)
    monkeypatch.setattr(e.db, "fallar_admision_documental", secundario)
    monkeypatch.setattr(e.gestor, "cancelar", secundario)
    with pytest.raises(Exception) as exc:
        admitir(e)
    assert exc.value.__cause__.__notes__
    doc = e.db._conn.execute("SELECT document_id FROM document_admissions").fetchone()[0]
    assert e.storage.get(clave_original("ws_a", doc)) == TEXTO
    monkeypatch.setattr(e.db, "fallar_admision_documental", fallar)
    monkeypatch.setattr(e.gestor, "cancelar", cancelar)
    e.gestor.detener()
    e.indice.cerrar()
    e.db.cerrar()
    abrir(e)
    assert e.db._conn.execute("SELECT estado FROM document_admissions").fetchone()[0] == "fallida"
    assert e.efectos.reconciliar() == 1
    for key in (clave_original("ws_a", doc), clave_documento("ws_a", doc)):
        with pytest.raises(StorageNotFound):
            e.storage.get(key)
    assert e.db._conn.execute("SELECT document_id FROM document_admissions").fetchone()[0] == doc
    assert e.doble.llamadas_embeddings == 0


def test_ack_anterior_no_completa_solicitud_concurrente(entorno):
    e = entorno
    r = admitir(e)
    terminal(e, r.job_id)
    e.db.agendar_efecto_documental("ws_a", r.document_id)
    anterior = dict(e.db._conn.execute("SELECT * FROM document_cleanup").fetchone())
    e.db.agendar_efecto_documental("ws_a", r.document_id)
    assert e.db.registrar_resultado_efecto_documental(anterior) is False
    assert e.db._conn.execute("SELECT estado FROM document_cleanup").fetchone()[0] == "reservada"


@pytest.mark.parametrize("lapida", ["local", "remota", "workspace"])
def test_reconciliacion_respeta_lapidas_y_no_resucita(entorno, lapida):
    e = entorno
    r = admitir(e)
    terminal(e, r.job_id)
    e.db.actualizar_documento(r.document_id, DocumentStatus.FAILED)
    e.db._conn.execute("UPDATE jobs SET status='failed' WHERE job_id=?", (r.job_id,))
    e.db.agendar_efecto_documental("ws_a", r.document_id)
    if lapida == "workspace":
        e.db.borrar_workspace("ws_a")
    elif lapida == "local":
        e.db.agendar_borrado("document", r.document_id, "ws_a")
    else:
        key = clave_workspace("ws_a")
        previo = ManifiestoEspacio.model_validate_json(e.storage.get(key))
        ahora = datetime.now(timezone.utc)
        remoto = previo.model_copy(
            update={
                "version": previo.version + 100,
                "recursos_borrados": [
                    Lapida(
                        recurso_tipo="document",
                        recurso_id=r.document_id,
                        solicitado_en=ahora,
                        purga_despues_en=ahora + timedelta(days=30),
                    ),
                    Lapida(
                        recurso_tipo="document",
                        recurso_id="otra_lapida",
                        solicitado_en=ahora,
                        purga_despues_en=ahora + timedelta(days=30),
                    ),
                ],
            }
        )
        e.storage.upload(key, remoto.model_dump_json().encode())
        assert not e.db.esta_borrado("document", r.document_id)
    assert e.efectos.reconciliar() == 1
    assert e.db.obtener_documento(r.document_id) is None
    assert e.indice._retirado("ws_a", r.document_id)
    sin_chunks(e, r.document_id)
    with pytest.raises(StorageNotFound):
        e.storage.get(clave_original("ws_a", r.document_id))
    assert e.efectos.reconciliar() == 0
    if lapida == "remota":
        assert ManifiestoEspacio.model_validate_json(e.storage.get(key)) == remoto
        assert e.db.esta_borrado("document", r.document_id)
        assert e.db.esta_borrado("document", "otra_lapida")


def test_sin_indice_no_acredita_limpieza_y_conserva_seguimiento(entorno):
    e = entorno
    r = admitir(e)
    terminal(e, r.job_id)
    e.db._conn.execute("UPDATE jobs SET status='failed' WHERE job_id=?", (r.job_id,))
    e.db.actualizar_documento(r.document_id, DocumentStatus.FAILED)
    e.db.agendar_efecto_documental("ws_a", r.document_id)
    assert EfectosDocumentales(e.db, e.storage, None).reconciliar() == 0
    assert e.db._conn.execute("SELECT completado_en FROM document_cleanup").fetchone()[0] is None


def test_sincronizador_existente_reintenta_sin_segunda_cola(entorno, monkeypatch):
    e = entorno
    r = admitir(e)
    terminal(e, r.job_id)
    e.db._conn.execute("UPDATE jobs SET status='failed' WHERE job_id=?", (r.job_id,))
    e.db.actualizar_documento(r.document_id, DocumentStatus.FAILED)
    e.db.agendar_efecto_documental("ws_a", r.document_id)
    original = e.efectos.reconciliar
    invocada = threading.Event()

    def primer_fallo():
        if not invocada.is_set():
            invocada.set()
            raise sqlite3.OperationalError("fallo temporal")
        return original()

    sincronizador = SincronizadorManifiestos(e.db, e.storage, intervalo=0.01, efectos_documentales=primer_fallo)
    sincronizador.iniciar()
    try:
        hasta(lambda: e.db._conn.execute("SELECT estado FROM document_cleanup").fetchone()[0] == "completada")
        assert invocada.is_set() and sincronizador._thread.is_alive()
        sin_chunks(e, r.document_id)
    finally:
        sincronizador.detener()


def test_observador_fuera_del_lock_y_stop_espera_callback_externo(entorno, tmp_path):
    e = entorno
    iniciada, liberar_worker, observando, liberar_obs = (threading.Event() for _ in range(4))

    def ocupar(ctx):
        iniciada.set()
        assert liberar_worker.wait(10)
        ctx.chequear()

    primero = e.gestor.enqueue("ws_b", "chat", ocupar)
    assert iniciada.wait(3)

    def observar(estado):
        observando.set()
        assert liberar_obs.wait(10)
        (tmp_path / "efecto.txt").write_text(estado.value)

    segundo = e.gestor.enqueue("ws_a", "chat", lambda ctx: None, al_terminar=observar)
    with ThreadPoolExecutor() as pool:
        cancelado = pool.submit(e.gestor.cancelar, segundo)
        try:
            assert observando.wait(3)
            assert pool.submit(e.gestor.estado, primero).result(1)["status"] == "running"
            assert e.db.obtener_job(segundo)["status"] == "cancelled"
            liberar_worker.set()
            hasta(lambda: e.gestor.estado(primero)["status"] == "completed")
            with pytest.raises(RuntimeError, match="observador"):
                e.gestor.detener(timeout=0.05)
            assert not (tmp_path / "efecto.txt").exists()
        finally:
            liberar_worker.set()
            liberar_obs.set()
        assert cancelado.result(3)
    e.gestor.detener(timeout=None)
    assert (tmp_path / "efecto.txt").read_text() == "cancelled"


def test_cursor_se_publica_completo_entre_procesos_sin_rotacion(tmp_path, monkeypatch):
    destino = tmp_path / "document-cursor.key"
    escribiendo, liberar = threading.Event(), threading.Event()
    fsync = os.fsync

    def detener_publicacion(fd):
        escribiendo.set()
        assert liberar.wait(10)
        fsync(fd)

    monkeypatch.setattr(os, "fsync", detener_publicacion)
    with ThreadPoolExecutor() as pool:
        primera = pool.submit(CursorDocumental, destino)
        try:
            assert escribiendo.wait(3)
            assert not destino.exists()
            entorno_hijo = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            resultado = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-c",
                    "import sys,hashlib; from pathlib import Path; from app.documents.cursor import CursorDocumental; "
                    "c=CursorDocumental(Path(sys.argv[1])); print(hashlib.sha256(c._clave).hexdigest())",
                    str(destino),
                ],
                env=entorno_hijo,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            assert resultado.returncode == 0, resultado.stderr
            assert len(destino.read_bytes()) == 32
        finally:
            liberar.set()
        cursor = primera.result(3)
    assert hashlib.sha256(cursor._clave).hexdigest() == resultado.stdout.strip()
    assert CursorDocumental(destino)._clave == cursor._clave
    assert not list(tmp_path.glob(".document-cursor-*"))


def test_lifespan_no_cierra_recursos_si_stop_falla_con_worker_activo(tmp_path, monkeypatch):
    _peticiones_globales.clear()
    _peticiones_ip.clear()
    ajustes = Configuracion(
        app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path), embedding_dimensions=16
    )
    app = crear_app(
        ajustes,
        proveedor_embeddings=DobleGemini(16),
        cuotas_proveedor=CuotasProveedor({MODELO: CuotasModelo(1000, 1000000, 1000)}),
    )
    iniciada, liberar = threading.Event(), threading.Event()
    gestor = indice = None
    detener = None

    def ocupar(ctx):
        iniciada.set()
        assert liberar.wait(10)
        ctx.chequear()

    try:
        with pytest.raises(RuntimeError, match="llamada en vuelo"):
            with TestClient(app) as cliente:
                ws = cliente.post("/api/workspaces").json()["workspace_id"]
                gestor, indice = app.state.jobs_manager, app.state.vectorstore
                detener = gestor.detener
                gestor.enqueue(ws, "chat", ocupar)
                assert iniciada.wait(3)
                monkeypatch.setattr(gestor, "detener", lambda **kw: detener(timeout=0))
        assert gestor._hilo.is_alive()
        assert app.state.db.obtener_workspace(ws) is not None
        assert indice._cliente._closed is False
    finally:
        liberar.set()
        if detener is not None:
            detener(timeout=None)
        if indice is not None:
            indice.cerrar()
        app.state.db.cerrar()


def test_terminal_y_efecto_pendiente_comparten_commit_sqlite(tmp_path):
    db = RegistroOperativo(tmp_path / "atomic.db")
    try:
        db.crear_workspace("ws", "codigo", 30)
        db.registrar_documento("doc", "ws", "Título", DocumentStatus.PROCESSING)
        db.encolar_job("job", "ws", "ingestion", document_id="doc")
        db._conn.execute("""CREATE TRIGGER fallo_efecto BEFORE INSERT ON document_cleanup
            BEGIN SELECT RAISE(ABORT, 'fallo durable'); END""")
        with pytest.raises(sqlite3.IntegrityError, match="fallo durable"):
            db.finalizar_job("job", JobStatus.FAILED, error_code="INTERRUPTED")
        assert db.obtener_job("job")["status"] == "queued"
        assert db.obtener_documento("doc")["estado"] == "processing"
        assert len(db.eventos_desde_job("job")) == 1
        assert db._conn.execute("SELECT COUNT(*) FROM document_cleanup").fetchone()[0] == 0
        db._conn.execute("DROP TRIGGER fallo_efecto")
        db.finalizar_job("job", JobStatus.FAILED, error_code="INTERRUPTED")
        assert db.obtener_documento("doc")["estado"] == "failed"
        assert db._conn.execute("SELECT estado FROM document_cleanup").fetchone()[0] == "reservada"
        assert [e["status"] for e in db.eventos_desde_job("job")] == ["queued", "failed"]
    finally:
        db.cerrar()


@pytest.mark.parametrize("respuesta_perdida", [False, True])
def test_purga_no_se_confirma_sin_verificar_objetos(entorno, monkeypatch, respuesta_perdida):
    e = entorno
    r = admitir(e)
    terminal(e, r.job_id)
    e.db.fallar_admision_documental(r.document_id, "STORAGE_UNAVAILABLE", "Admisión fallida")
    borrar = e.storage.delete

    def sin_confirmacion(key):
        if respuesta_perdida:
            borrar(key)
            raise StorageUnavailable("respuesta perdida")

    monkeypatch.setattr(e.storage, "delete", sin_confirmacion)
    assert e.efectos.reconciliar() == 0
    assert e.db._conn.execute("SELECT estado FROM document_cleanup").fetchone()[0] == "pendiente"
    if not respuesta_perdida:
        assert e.storage.get(clave_original("ws_a", r.document_id)) == TEXTO
    monkeypatch.setattr(e.storage, "delete", borrar)
    debido(e)
    assert e.efectos.reconciliar() == 1
    for key in (clave_original("ws_a", r.document_id), clave_documento("ws_a", r.document_id)):
        with pytest.raises(StorageNotFound):
            e.storage.get(key)
    assert e.db._conn.execute("SELECT estado FROM document_cleanup").fetchone()[0] == "completada"


def test_lifespan_espera_worker_antes_de_cerrar_indice_y_sqlite(tmp_path):
    _peticiones_globales.clear()
    _peticiones_ip.clear()
    app = crear_app(
        Configuracion(app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path), embedding_dimensions=16),
        proveedor_embeddings=DobleGemini(16),
        cuotas_proveedor=CuotasProveedor({MODELO: CuotasModelo(1000, 1000000, 1000)}),
    )
    cliente = TestClient(app)
    cliente.__enter__()
    ws = cliente.post("/api/workspaces").json()["workspace_id"]
    gestor, indice, db = app.state.jobs_manager, app.state.vectorstore, app.state.db
    iniciada, liberar = threading.Event(), threading.Event()

    def ocupar(ctx):
        iniciada.set()
        assert liberar.wait(10)
        ctx.chequear()

    gestor.enqueue(ws, "chat", ocupar)
    assert iniciada.wait(3)
    with ThreadPoolExecutor() as pool:
        cierre = pool.submit(cliente.__exit__, None, None, None)
        try:
            with pytest.raises(TimeoutError):
                cierre.result(0.1)
            assert gestor._hilo.is_alive()
            assert db.obtener_workspace(ws) is not None
            assert indice._cliente._closed is False
        finally:
            liberar.set()
        cierre.result(5)
    assert indice._cliente._closed is True
    with pytest.raises(sqlite3.ProgrammingError):
        db.obtener_workspace(ws)


def test_compensacion_no_retira_otro_documento_con_mismo_hash_en_otro_workspace(entorno):
    e = entorno
    a = admitir(e)
    terminal(e, a.job_id)
    b = admitir(e, ws="ws_b")
    terminal(e, b.job_id)
    with e.db.transaccion():
        e.db.actualizar_documento(a.document_id, DocumentStatus.FAILED)
        e.db._conn.execute("UPDATE jobs SET status='failed' WHERE job_id=?", (a.job_id,))
    assert e.efectos.reconciliar() == 1
    sin_chunks(e, a.document_id)
    assert e.db.obtener_documento(b.document_id)["estado"] == "ready"
    assert e.indice._activo(e.indice._privada, "ws_b", b.document_id) is not None
    assert e.indice._privada.get(where=_filtro("ws_b", b.document_id), include=[])["ids"]
    assert (
        ManifiestoDocumento.model_validate_json(e.storage.get(clave_documento("ws_b", b.document_id))).estado
        == DocumentStatus.READY
    )


def test_lapida_concurrente_al_diagnostico_conserva_purga_pendiente(entorno, monkeypatch):
    e = entorno
    r = admitir(e)
    terminal(e, r.job_id)
    with e.db.transaccion():
        e.db.actualizar_documento(r.document_id, DocumentStatus.FAILED)
        e.db._conn.execute("UPDATE jobs SET status='failed' WHERE job_id=?", (r.job_id,))
    upload = e.storage.upload
    retirado = threading.Event()

    def retirar_durante_oci(key, contenido, **kw):
        if key == clave_documento("ws_a", r.document_id) and not retirado.is_set():
            retirado.set()
            e.db.agendar_borrado("document", r.document_id, "ws_a")
        return upload(key, contenido, **kw)

    monkeypatch.setattr(e.storage, "upload", retirar_durante_oci)
    assert e.efectos.reconciliar() == 0
    assert retirado.is_set()
    assert e.db.obtener_documento(r.document_id) is None
    assert e.db._conn.execute("SELECT completado_en FROM document_cleanup").fetchone()[0] is None
    debido(e)
    assert e.efectos.reconciliar() == 1
    sin_chunks(e, r.document_id)
    assert e.indice._retirado("ws_a", r.document_id)
    with pytest.raises(StorageNotFound):
        e.storage.get(clave_documento("ws_a", r.document_id))
