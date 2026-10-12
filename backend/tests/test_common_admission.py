"""Intenciones comunes: un solo trabajo, transacciones cortas y reservas recuperables."""

import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.jobs.admission import admitir_trabajo
from app.jobs.manager import GestorTrabajos
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode

pytestmark = pytest.mark.unit


@pytest.fixture
def pila(tmp_path):
    db = RegistroOperativo(tmp_path / "registro.db")
    db.crear_workspace("ws", "codigo", dias_validez=30)
    db.registrar_documento("doc", "ws", "Fuente", DocumentStatus.READY, "a" * 64)
    gestor = GestorTrabajos(db)
    yield db, gestor
    gestor.detener(timeout=None)
    db.cerrar()


def test_misma_intencion_concurrente_ejecuta_solo_una_vez(pila):
    db, gestor = pila
    llamadas = []

    def ejecutar(ctx):
        llamadas.append(1)
        return {"resultado": "validado"}

    def admitir():
        return admitir_trabajo(gestor, "ws", "doc", "chat", {"pregunta": "VCN"}, ejecutar, clave="intencion")

    with ThreadPoolExecutor(2) as pool:
        a, b = [pool.submit(admitir) for _ in range(2)]
        x, y = a.result(5), b.result(5)
    assert x["job_id"] == y["job_id"]
    limite = time.monotonic() + 5
    while db.obtener_job(x["job_id"])["status"] in {"queued", "running"} and time.monotonic() < limite:
        time.sleep(0.01)
    assert len(llamadas) == 1 and admitir()["status"] == "completed"
    with pytest.raises(ErrorAplicacion) as error:
        admitir_trabajo(gestor, "ws", "doc", "chat", {"pregunta": "Otra"}, ejecutar, clave="intencion")
    assert error.value.error.code == ErrorCode.IDEMPOTENCY_CONFLICT


def test_rechazo_de_preparacion_no_deja_intencion_huerfana(pila):
    db, gestor = pila

    def preparar():
        assert not db._conn.in_transaction
        raise ErrorAplicacion(ErrorCode.PROVIDER_UNAVAILABLE, "Sin proveedor")

    with pytest.raises(ErrorAplicacion):
        admitir_trabajo(gestor, "ws", "doc", "chat", {}, lambda ctx: {}, clave="intencion", preparar=preparar)
    assert db.idempotencia_consultar("ws", "chat", "intencion", "{}") is None
    job = admitir_trabajo(gestor, "ws", "doc", "chat", {}, lambda ctx: {}, clave="intencion")
    assert job["job_id"]


def test_cache_aprobada_no_prepara_ni_crea_otro_job(pila):
    db, gestor = pila
    job = admitir_trabajo(gestor, "ws", "doc", "glosario", {}, lambda ctx: {"verificado": True}, cache_key="cache")
    limite = time.monotonic() + 5
    while db.obtener_job(job["job_id"])["status"] in {"queued", "running"} and time.monotonic() < limite:
        time.sleep(0.01)

    def prohibido():
        pytest.fail("Una caché aprobada no solicita un proveedor")

    cached = admitir_trabajo(gestor, "ws", "doc", "glosario", {}, None, cache_key="cache", preparar=prohibido)
    assert cached["job_id"] == job["job_id"] and cached["status"] == "completed"


@pytest.mark.parametrize("clave", ["", " ", "a" * 129])
def test_limites_header(pila, clave):
    _, gestor = pila
    with pytest.raises(ErrorAplicacion) as error:
        admitir_trabajo(gestor, "ws", "doc", "chat", {}, lambda ctx: {}, clave=clave)
    assert error.value.error.code == ErrorCode.INVALID_REQUEST


def test_fallo_sqlite_de_clave_cache_no_admite_trabajo_a_medias(pila):
    db, gestor = pila
    db._conn.execute("""CREATE TEMP TRIGGER falla_cache BEFORE UPDATE OF cache_key ON jobs
        WHEN NEW.cache_key IS NOT NULL BEGIN SELECT RAISE(ABORT, 'fallo simulado'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        admitir_trabajo(gestor, "ws", "doc", "glosario", {}, lambda ctx: {}, clave="intencion", cache_key="cache")
    assert not db.listar_jobs_encolados() and db.contar_activos_de_workspace("ws") == 0
    assert db.idempotencia_consultar("ws", "glosario", "intencion", "{}") is None
    assert db.job_cache_glosario("ws", "cache") is None
