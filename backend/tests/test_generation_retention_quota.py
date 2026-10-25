"""Cupo retenido: SQLite real y admisión HTTP/worker con ejecución y storage dobles."""

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.storage.object_keys import clave_export, clave_output
from test_space_idempotency import espacio as espacio
from test_space_idempotency import intencion, pedir, terminado

pytestmark = pytest.mark.integration_mock


def registro_inicial(ruta):
    registro = RegistroOperativo(ruta, limite_generaciones=1)
    for ws, ids in (("ws_a", ("a", "b", "c", "d", "e")), ("ws_b", ("x", "y"))):
        registro.crear_workspace(ws, ws + "-codigo", 30)
        registro.crear_sesion(ws + "-token", ws, 24)
        for document_id in ids:
            registro.registrar_documento(document_id, ws, document_id, DocumentStatus.READY, document_id * 64)
    return registro


@pytest.fixture
def db(tmp_path):
    registro = registro_inicial(tmp_path / "cupo.sqlite3")
    try:
        yield registro
    finally:
        registro.cerrar()


def lleno(db, ws="ws_a", usados=1, limite=None):
    with pytest.raises(ErrorAplicacion) as error:
        db.comprobar_limite_recursos(ws, "generations", limite)
    assert error.value.status_code == 429
    assert error.value.error.code == ErrorCode.RATE_LIMITED
    assert error.value.error.details == {
        "motivo": "workspace_resource_limit",
        "recurso": "generations",
        "limite": limite or db.limite_generaciones,
        "usados": usados,
    }


@pytest.mark.parametrize("retirada", ["primario", "secundaria", "generacion", "simultaneas"])
def test_lapida_libera_plaza_de_toda_generacion_inaccesible(db, retirada):
    db.registrar_generacion("gen_ab", "ws_a", "a", document_ids=["a", "b"])
    lleno(db)
    if retirada in ("primario", "simultaneas"):
        db.agendar_borrado("document", "a", "ws_a")
    if retirada in ("secundaria", "simultaneas"):
        db.agendar_borrado("document", "b", "ws_a")
    if retirada in ("generacion", "simultaneas"):
        db.agendar_borrado("generation", "gen_ab", "ws_a")
    assert db.obtener_generacion("gen_ab") is None
    assert db.uso_espacio("ws_a")["generaciones"] == 0
    assert db.listar_generaciones("ws_a") == []
    # La lápida basta; todavía existen la generación y sus vínculos físicos.
    assert db._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 1
    assert db.documentos_generacion("gen_ab") == ["a", "b"]
    db.comprobar_limite_recursos("ws_a", "generations")
    with db.transaccion():
        db.comprobar_limite_recursos("ws_a", "generations")
        db.registrar_generacion("gen_cd", "ws_a", "c", document_ids=["c", "d"])
    lleno(db)
    assert db.uso_espacio("ws_a")["generaciones"] == 1


@pytest.mark.parametrize("error_limpieza", [None, "STORAGE_UNAVAILABLE"])
def test_limpieza_pendiente_o_fallida_no_ocupa_plaza_incluso_tras_reinicio(tmp_path, error_limpieza):
    ruta = tmp_path / "reinicio.sqlite3"
    db = registro_inicial(ruta)
    try:
        db.registrar_generacion("gen_ab", "ws_a", "a", document_ids=["a", "b"])
        assert db.retirar_documento("ws_a-token", "b")
        if error_limpieza:
            db.registrar_intento_limpieza_documental("ws_a", "b", error_code=error_limpieza)
        limpieza = db.listar_limpiezas_documentales()[0]
        assert limpieza["estado"] == "pendiente" and limpieza["ultimo_error"] == error_limpieza
        db.comprobar_limite_recursos("ws_a", "generations")
    finally:
        db.cerrar()
    reiniciado = RegistroOperativo(ruta, limite_generaciones=1)
    try:
        assert reiniciado.esta_borrado("document", "b")
        assert reiniciado.obtener_generacion("gen_ab") is None
        assert reiniciado.uso_espacio("ws_a")["generaciones"] == 0
        reiniciado.comprobar_limite_recursos("ws_a", "generations")
        with reiniciado.transaccion():
            reiniciado.comprobar_limite_recursos("ws_a", "generations")
            reiniciado.registrar_generacion("gen_ac", "ws_a", "a", document_ids=["a", "c"])
        lleno(reiniciado)
        assert reiniciado._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 2
        assert reiniciado.listar_limpiezas_documentales()[0]["ultimo_error"] == error_limpieza
    finally:
        reiniciado.cerrar()


def test_compatibilidad_historica_sin_vinculos_y_aislamiento(db):
    db.registrar_generacion("gen_a_legacy", "ws_a", "a")
    with db.transaccion():
        db._conn.execute("DELETE FROM generation_documents WHERE generation_id='gen_a_legacy'")
    db.registrar_generacion("gen_xy", "ws_b", "x", document_ids=["x", "y"])
    lleno(db, "ws_a")
    lleno(db, "ws_b")
    db.agendar_borrado("document", "a", "ws_a")
    db.comprobar_limite_recursos("ws_a", "generations")
    assert db.uso_espacio("ws_a")["generaciones"] == 0
    lleno(db, "ws_b")
    assert db.obtener_generacion("gen_xy") is not None
    db.agendar_borrado("document", "y", "ws_b")
    db.comprobar_limite_recursos("ws_b", "generations")


@pytest.mark.parametrize("estado", list(JobStatus))
def test_generacion_visible_cuenta_una_vez_con_cinco_relaciones(db, estado):
    db.registrar_generacion("gen_cinco", "ws_a", "a", status=estado, document_ids=["a", "b", "c", "d", "e"])
    assert db.uso_espacio("ws_a")["generaciones"] == 1
    db.comprobar_limite_recursos("ws_a", "generations", limite=2)
    lleno(db)
    db.registrar_generacion("gen_ac", "ws_a", "a", document_ids=["a", "c"])
    lleno(db, usados=2, limite=2)
    db.agendar_borrado("document", "e", "ws_a")
    assert db.obtener_generacion("gen_cinco") is None
    assert db.obtener_generacion("gen_ac") is not None
    assert db.uso_espacio("ws_a")["generaciones"] == 1
    db.comprobar_limite_recursos("ws_a", "generations", limite=2)
    lleno(db)


def test_dos_conexiones_no_reservan_dos_veces_la_ultima_plaza_liberada(db):
    db.registrar_generacion("gen_ab", "ws_a", "a", document_ids=["a", "b"])
    db.agendar_borrado("document", "b", "ws_a")
    otra = RegistroOperativo(db.ruta_db, limite_generaciones=1)
    barrera = threading.Barrier(2)

    def admitir(registro, generation_id):
        barrera.wait(5)
        try:
            with registro.transaccion():
                registro.comprobar_limite_recursos("ws_a", "generations")
                registro.registrar_generacion(generation_id, "ws_a", "a", document_ids=["a", "c"])
            return "admitida"
        except ErrorAplicacion as error:
            assert error.error.code == ErrorCode.RATE_LIMITED
            assert error.error.details["usados"] == 1
            return "lleno"

    try:
        with ThreadPoolExecutor(2) as pool:
            intentos = [pool.submit(admitir, registro, gen) for registro, gen in ((db, "gen_1"), (otra, "gen_2"))]
            assert sorted(intento.result(timeout=5) for intento in intentos) == ["admitida", "lleno"]
        assert db._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 2
        assert db.uso_espacio("ws_a")["generaciones"] == 1
        lleno(db)
    finally:
        otra.cerrar()


@pytest.mark.parametrize("misma_clave", [True, False])
def test_admision_http_tras_retirada_conserva_cupo_idempotencia_y_derivados(espacio, misma_clave):
    e = espacio
    e.cfg = e.cfg.model_copy(update={"workspace_max_generations": 1})
    e.servicio.cfg = e.cfg
    e.db.limite_generaciones = 1
    for doc in ("doc_a", "doc_b", "doc_c"):
        e.documento(doc)
    e.db.registrar_generacion("gen_retirada", "ws_a", "doc_a", document_ids=["doc_a", "doc_b"])
    output = clave_output("ws_a", "gen_retirada")
    export = clave_export("ws_a", "gen_retirada", "md")
    e.storage.objetos.update({output: b"paquete pendiente de limpiar", export: b"export pendiente de limpiar"})
    assert e.db.retirar_documento("token-a", "doc_b")
    e.db.registrar_intento_limpieza_documental("ws_a", "doc_b", error_code="STORAGE_UNAVAILABLE")
    e.manifest()
    e.bloquear = True
    barrera = threading.Barrier(2)

    def solicitar(clave):
        barrera.wait(5)
        return pedir(e, clave)

    claves = ["uno", "uno" if misma_clave else "dos"]
    try:
        with ThreadPoolExecutor(2) as pool:
            respuestas = list(pool.map(solicitar, claves))
        assert sorted(r.status_code for r in respuestas) == ([202, 202] if misma_clave else [202, 429])
        aceptada = next(r for r in respuestas if r.status_code == 202)
        generation_id = aceptada.json()["generation_id"]
        clave_aceptada = claves[respuestas.index(aceptada)]
        assert e.iniciado.wait(5)
        if misma_clave:
            assert {r.json()["generation_id"] for r in respuestas} == {generation_id}
        else:
            rechazo = next(r for r in respuestas if r.status_code == 429)
            assert rechazo.json()["error"]["details"]["usados"] == 1
            assert intencion(e, claves[respuestas.index(rechazo)]) is None
    finally:
        e.liberar.set()
    terminado(e, aceptada)
    huella = intencion(e, clave_aceptada)
    repetida = pedir(e, clave_aceptada)
    assert repetida.status_code == 202 and repetida.json()["generation_id"] == generation_id
    assert intencion(e, clave_aceptada) == huella
    nuevo = pedir(e, "tercera")
    assert nuevo.status_code == 429 and nuevo.json()["error"]["details"]["usados"] == 1
    assert intencion(e, "tercera") is None
    assert len(e.ejecutados) == 1
    assert e.db._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 2
    assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
    assert e.db.documentos_generacion(generation_id) == ["doc_a", "doc_c"]
    assert e.db.uso_espacio("ws_a")["generaciones"] == 1
    assert e.db.obtener_generacion("gen_retirada") is None
    assert e.storage.objetos[output] == b"paquete pendiente de limpiar"
    assert e.storage.objetos[export] == b"export pendiente de limpiar"
    assert e.db.listar_limpiezas_documentales()[0]["ultimo_error"] == "STORAGE_UNAVAILABLE"
