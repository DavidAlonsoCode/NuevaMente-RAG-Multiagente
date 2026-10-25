"""Concurrencia real de SQLite y ciclo completo de conexiones del presupuesto OCI."""

import sqlite3
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from time import monotonic
from types import SimpleNamespace

import pytest
from app.storage import oci_budget
from app.storage.budgeted import StoragePresupuestado
from app.storage.oci_budget import PresupuestoOCI
from app.storage.oci_storage import LocalMockStorageProvider
from app.storage.provider import StorageBudgetExceeded, StorageUnavailable

pytestmark = pytest.mark.unit


def fachada_sqlite(conectar):
    return SimpleNamespace(connect=conectar, Error=sqlite3.Error)


@pytest.mark.parametrize("instancias", [1, 2])
def test_solicitudes_concurrentes_respetan_limite_y_persisten_conteo_exacto(tmp_path, instancias):
    ruta = tmp_path / "budget.sqlite3"
    presupuestos = [PresupuestoOCI(ruta, solicitudes=20) for _ in range(instancias)]
    inicio = threading.Barrier(8)

    def reservar(numero):
        inicio.wait(timeout=3)
        tipo = ("get_object", "list_objects")[numero % 2]
        try:
            presupuestos[numero % instancias].reservar_solicitud(tipo)
        except StorageBudgetExceeded:
            return None
        return tipo

    with ThreadPoolExecutor(max_workers=8) as pool:
        resultados = list(pool.map(reservar, range(40)))
    admitidas = [tipo for tipo in resultados if tipo is not None]
    assert len(admitidas) == 18
    resumen = presupuestos[0].resumen()
    assert resumen["solicitudes"] == 18
    assert resumen["operaciones"] == dict(Counter(admitidas))
    assert resumen["reservas_inciertas"] == 0
    assert PresupuestoOCI(ruta, solicitudes=20).resumen() == resumen


@pytest.mark.parametrize("instancias", [1, 2])
def test_capacidad_concurrente_no_sobrevende_y_reservas_sobreviven_reinicio(tmp_path, instancias):
    ruta = tmp_path / "budget.sqlite3"
    presupuestos = [PresupuestoOCI(ruta, bytes_maximos=10) for _ in range(instancias)]
    inicio = threading.Barrier(8)

    def reservar(numero):
        inicio.wait(timeout=3)
        token = str(numero)
        try:
            presupuestos[numero % instancias].reservar_upload(token, "demo/" + token, 4)
        except StorageBudgetExceeded:
            return None
        return token

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitidas = [token for token in pool.map(reservar, range(24)) if token is not None]
    assert len(admitidas) == 2
    reiniciado = PresupuestoOCI(ruta, bytes_maximos=10)
    assert reiniciado.resumen()["bytes_reservados"] == 8
    assert reiniciado.resumen()["reservas_inciertas"] == 2
    primero, segundo = admitidas
    reiniciado.confirmar_upload(primero, "demo/" + primero, 3)
    reiniciado.liberar_reserva(segundo)
    reiniciado.reservar_upload("restante", "demo/restante", 7)
    assert reiniciado.resumen()["bytes_reservados"] == 10
    with pytest.raises(StorageBudgetExceeded):
        reiniciado.reservar_upload("exceso", "demo/exceso", 1)


def test_una_transaccion_no_bloquea_otro_archivo_de_presupuesto(tmp_path):
    primero = PresupuestoOCI(tmp_path / "primero.sqlite3")
    segundo = PresupuestoOCI(tmp_path / "segundo.sqlite3")
    with ThreadPoolExecutor(max_workers=1) as pool:
        with primero._transaccion():
            pool.submit(segundo.reservar_solicitud, "independiente").result(timeout=3)
            assert segundo.resumen()["solicitudes"] == 1
    assert primero.resumen()["solicitudes"] == 0


def test_misma_instancia_no_solapa_conexiones_ni_su_cierre(tmp_path, monkeypatch):
    presupuesto = PresupuestoOCI(tmp_path / "budget.sqlite3")
    guardia = threading.Lock()
    activas = maximas = aperturas = cierres = 0
    inicio = threading.Barrier(8)

    class ConexionObservada(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            nonlocal activas, maximas, aperturas
            super().__init__(*args, **kwargs)
            with guardia:
                activas += 1
                aperturas += 1
                maximas = max(maximas, activas)

        def close(self):
            nonlocal activas, cierres
            super().close()
            with guardia:
                activas -= 1
                cierres += 1

    def conectar(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=ConexionObservada)

    monkeypatch.setattr(oci_budget, "sqlite3", fachada_sqlite(conectar))

    def reservar(_):
        inicio.wait(timeout=3)
        presupuesto.reservar_solicitud("get_object")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(reservar, range(32)))
    assert (maximas, activas, aperturas, cierres) == (1, 0, 32, 32)
    assert presupuesto.resumen()["operaciones"] == {"get_object": 32}


@pytest.mark.parametrize("fallo_sql", [False, True])
def test_excepcion_revierte_sql_y_libera_coordinacion_para_otro_hilo(tmp_path, fallo_sql):
    presupuesto = PresupuestoOCI(tmp_path / "budget.sqlite3")
    error_esperado = StorageUnavailable if fallo_sql else ValueError
    with pytest.raises(error_esperado):
        with presupuesto._transaccion() as conexion:
            conexion.execute("INSERT INTO solicitudes VALUES (?, 1)", (presupuesto._mes(),))
            if fallo_sql:
                conexion.execute("INSERT INTO tabla_inexistente VALUES (1)")
            else:
                raise ValueError("interrupcion controlada")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(presupuesto.reservar_solicitud, "tras_fallo").result(timeout=3)
    assert presupuesto.resumen()["solicitudes"] == 1
    assert presupuesto.resumen()["operaciones"] == {"tras_fallo": 1}


def test_timeout_local_no_abre_conexion_y_libera_al_finalizar_tenedor(tmp_path, monkeypatch):
    presupuesto = PresupuestoOCI(tmp_path / "budget.sqlite3")
    adquirido, liberar = threading.Event(), threading.Event()
    conexiones = []

    def conectar(*args, **kwargs):
        conexiones.append(kwargs)
        return sqlite3.connect(*args, **kwargs)

    def retener():
        with presupuesto._lock:
            adquirido.set()
            assert liberar.wait(timeout=3)

    monkeypatch.setattr(oci_budget, "_TIMEOUT_SQLITE", 0.05)
    monkeypatch.setattr(oci_budget, "sqlite3", fachada_sqlite(conectar))
    with ThreadPoolExecutor(max_workers=1) as pool:
        tenedor = pool.submit(retener)
        assert adquirido.wait(timeout=3)
        try:
            with pytest.raises(StorageUnavailable):
                presupuesto.reservar_solicitud("agotada")
            assert conexiones == []
        finally:
            liberar.set()
        tenedor.result(timeout=3)
    presupuesto.reservar_solicitud("posterior")
    assert presupuesto.resumen()["operaciones"] == {"posterior": 1}


def test_espera_local_descuenta_tiempo_de_espera_sqlite_y_conserva_full(tmp_path, monkeypatch):
    presupuesto = PresupuestoOCI(tmp_path / "budget.sqlite3")
    marcas = iter([100.0, 107.25])
    tiempos = []

    def conectar(*args, **kwargs):
        tiempos.append(kwargs["timeout"])
        return sqlite3.connect(*args, **kwargs)

    monkeypatch.setattr(oci_budget, "monotonic", lambda: next(marcas, 107.25))
    monkeypatch.setattr(oci_budget, "sqlite3", fachada_sqlite(conectar))
    with presupuesto._transaccion() as conexion:
        assert conexion.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert conexion.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert tiempos == [pytest.approx(22.75)]


def test_plazo_agotado_tras_adquirir_no_abre_sqlite_y_libera_lock(tmp_path, monkeypatch):
    presupuesto = PresupuestoOCI(tmp_path / "budget.sqlite3")
    marcas = iter([100.0, 131.0])
    conexiones = []

    def conectar(*args, **kwargs):
        conexiones.append(kwargs)
        return sqlite3.connect(*args, **kwargs)

    monkeypatch.setattr(oci_budget, "monotonic", lambda: next(marcas, 131.0))
    monkeypatch.setattr(oci_budget, "sqlite3", fachada_sqlite(conectar))
    with pytest.raises(StorageUnavailable):
        presupuesto.reservar_solicitud("agotada")
    assert conexiones == []
    monkeypatch.setattr(oci_budget, "monotonic", monotonic)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(presupuesto.reservar_solicitud, "posterior").result(timeout=3)
    assert presupuesto.resumen()["operaciones"] == {"posterior": 1}


def test_error_al_conectar_libera_lock_para_otro_hilo(tmp_path, monkeypatch):
    presupuesto = PresupuestoOCI(tmp_path / "budget.sqlite3")

    def fallar(*args, **kwargs):
        raise sqlite3.OperationalError("fallo de apertura controlado")

    monkeypatch.setattr(oci_budget, "sqlite3", fachada_sqlite(fallar))
    with pytest.raises(StorageUnavailable):
        presupuesto.reservar_solicitud("fallida")
    monkeypatch.setattr(oci_budget, "sqlite3", sqlite3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(presupuesto.reservar_solicitud, "posterior").result(timeout=3)
    assert presupuesto.resumen()["operaciones"] == {"posterior": 1}


def test_error_al_cerrar_libera_lock_para_otro_hilo(tmp_path, monkeypatch):
    presupuesto = PresupuestoOCI(tmp_path / "budget.sqlite3")

    class CierreFallido(sqlite3.Connection):
        def close(self):
            super().close()
            raise sqlite3.OperationalError("fallo de cierre controlado")

    def conectar(*args, **kwargs):
        return sqlite3.connect(*args, **kwargs, factory=CierreFallido)

    monkeypatch.setattr(oci_budget, "sqlite3", fachada_sqlite(conectar))
    with pytest.raises(sqlite3.OperationalError):
        presupuesto.reservar_solicitud("confirmada_antes_del_cierre")
    monkeypatch.setattr(oci_budget, "sqlite3", sqlite3)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(presupuesto.reservar_solicitud, "posterior").result(timeout=3)
    assert presupuesto.resumen()["operaciones"] == {"confirmada_antes_del_cierre": 1, "posterior": 1}


def test_reserva_no_mantiene_lock_durante_io_del_proveedor(tmp_path):
    presupuesto = PresupuestoOCI(tmp_path / "budget.sqlite3")
    entro, liberar = threading.Event(), threading.Event()

    class ProveedorBloqueado(LocalMockStorageProvider):
        def get(self, object_name, *, if_match=None):
            entro.set()
            assert liberar.wait(timeout=3)
            return super().get(object_name, if_match=if_match)

    proveedor = ProveedorBloqueado(tmp_path / "storage")
    storage = StoragePresupuestado(proveedor, presupuesto)
    storage.upload("demo/material", b"contenido")
    with ThreadPoolExecutor(max_workers=2) as pool:
        lectura = pool.submit(storage.get, "demo/material")
        assert entro.wait(timeout=3)
        try:
            pool.submit(presupuesto.reservar_solicitud, "independiente").result(timeout=3)
        finally:
            liberar.set()
        assert lectura.result(timeout=3) == b"contenido"
    assert presupuesto.resumen()["operaciones"] == {"put_object": 1, "get_object": 1, "independiente": 1}
