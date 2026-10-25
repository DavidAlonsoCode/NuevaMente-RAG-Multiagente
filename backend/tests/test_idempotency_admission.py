"""Reservas reales SQLite/worker, sin storage ni proveedores."""

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.jobs.admission import admitir_trabajo
from app.jobs.manager import EspacioOcupadoError, GestorTrabajos
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode

pytestmark = pytest.mark.unit

HISTORICO = {"document_id": "doc_a", "idioma_salida": "es", "perfil_destinatario": "principiante"}
PR_146 = {
    "document_id": "doc_a",
    "document_ids": ["doc_a"],
    "idioma_salida": "es",
    "perfil_destinatario": "principiante",
}


@pytest.fixture
def sistema(tmp_path):
    db = RegistroOperativo(tmp_path / "admisiones.sqlite3")
    db.crear_workspace("ws_a", "codigo", 30)
    db.registrar_documento("doc_a", "ws_a", "Fuente A", DocumentStatus.READY, "a" * 64)
    gestor = GestorTrabajos(db)
    try:
        yield db, gestor
    finally:
        gestor.detener()
        db.cerrar()


def intencion(db, tipo):
    fila = db._conn.execute(
        "SELECT rowid,* FROM idempotency_keys WHERE workspace_id='ws_a' AND operacion=? AND clave='clave'", (tipo,)
    ).fetchone()
    return dict(fila) if fila else None


def esperar_terminal(db, job_id):
    limite = time.monotonic() + 10
    while time.monotonic() < limite:
        job = db.obtener_job(job_id)
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.005)
    pytest.fail("El ejecutable local no terminó")


@pytest.mark.parametrize("tipo", ["chat", "glosario"])
def test_admisiones_concurrentes_de_formas_equivalentes_no_duplican_ni_reescriben(sistema, tipo):
    db, gestor = sistema
    iniciado, liberar = threading.Event(), threading.Event()
    preparados, ejecutados = [], []

    def ejecutar(ctx):
        ejecutados.append(ctx.job_id)
        iniciado.set()
        assert liberar.wait(10)
        ctx.chequear()

    def admitir(cuerpo, alternativo):
        return admitir_trabajo(
            gestor,
            "ws_a",
            "doc_a",
            tipo,
            cuerpo,
            ejecutar,
            clave="clave",
            cuerpos_alternativos=(alternativo,),
            preparar=lambda: preparados.append(True),
        )

    job_id = None
    try:
        with ThreadPoolExecutor(2) as executor:
            resultados = list(executor.map(lambda formas: admitir(*formas), [(HISTORICO, PR_146), (PR_146, HISTORICO)]))
        assert {job["job_id"] for job in resultados} == {resultados[0]["job_id"]}
        job_id = resultados[0]["job_id"]
        assert iniciado.wait(5)
        assert preparados == [True] and ejecutados == [job_id]
        assert db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        original = intencion(db, tipo)
        assert original["hash_cuerpo"] in {
            hashlib.sha256(json.dumps(cuerpo, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            for cuerpo in (HISTORICO, PR_146)
        }
        assert admitir(HISTORICO, PR_146)["job_id"] == job_id
        assert admitir(PR_146, HISTORICO)["job_id"] == job_id
        assert intencion(db, tipo) == original
        cambiado = {**HISTORICO, "idioma_salida": "en"}
        with pytest.raises(ErrorAplicacion) as exc:
            admitir(cambiado, {**PR_146, "idioma_salida": "en"})
        assert exc.value.error.code == ErrorCode.IDEMPOTENCY_CONFLICT
        assert intencion(db, tipo) == original
        assert preparados == [True] and ejecutados == [job_id]
    finally:
        if job_id:
            gestor.cancelar(job_id)
        liberar.set()
        if job_id:
            esperar_terminal(db, job_id)


@pytest.mark.parametrize("tipo", ["chat", "glosario"])
@pytest.mark.parametrize("fallo", ["preparar", "enqueue_ocupado"])
def test_fallo_de_admision_elimina_solo_su_reserva_y_permite_reintentar(sistema, tipo, fallo):
    db, gestor = sistema
    iniciado, liberar = threading.Event(), threading.Event()
    bloqueador = None

    def ejecutar_bloqueador(ctx):
        iniciado.set()
        assert liberar.wait(10)
        ctx.chequear()

    def preparar():
        if fallo == "preparar":
            raise RuntimeError("Fallo de preparación simulado")

    if fallo == "enqueue_ocupado":
        bloqueador = gestor.enqueue("ws_a", "chat", ejecutar_bloqueador, document_id="doc_a")
        assert iniciado.wait(5)
    try:
        with pytest.raises(RuntimeError if fallo == "preparar" else EspacioOcupadoError):
            admitir_trabajo(
                gestor,
                "ws_a",
                "doc_a",
                tipo,
                HISTORICO,
                lambda ctx: {"ok": True},
                clave="clave",
                cuerpos_alternativos=(PR_146,),
                preparar=preparar,
            )
        assert intencion(db, tipo) is None
        assert db._conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 0
        assert db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == (bloqueador is not None)
    finally:
        if bloqueador:
            gestor.cancelar(bloqueador)
        liberar.set()
        if bloqueador:
            esperar_terminal(db, bloqueador)
    reintento = admitir_trabajo(
        gestor,
        "ws_a",
        "doc_a",
        tipo,
        HISTORICO,
        lambda ctx: {"ok": True},
        clave="clave",
        cuerpos_alternativos=(PR_146,),
    )
    assert esperar_terminal(db, reintento["job_id"])["status"] == "completed"
    assert intencion(db, tipo)["recurso_id"] == reintento["job_id"]
    assert (
        intencion(db, tipo)["hash_cuerpo"]
        == hashlib.sha256(json.dumps(HISTORICO, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    )
