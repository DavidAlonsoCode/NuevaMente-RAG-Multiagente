"""Tests del registro operativo SQLite (issue #08).

Verificación exacta del issue: `pytest backend/tests/test_jobs_store.py`,
que incluye el test de reinicio SIMULADO: se abre un store, se escribe
estado, se cierra (como un proceso que termina), y se abre UNA INSTANCIA
NUEVA sobre el mismo archivo — que es exactamente lo que pasa tras un
reinicio real del backend.

Criterio por criterio:

1. Reinicio conserva workspaces/sesiones/trabajos  -> test_reinicio_conserva_...
2. Misma Idempotency-Key + mismo cuerpo -> misma respuesta sin duplicar;
   cuerpo distinto -> conflicto (409)    -> test_idempotencia_...
3. Trabajo `running` al matar el proceso -> `failed` al reiniciar
                                            -> test_trabajo_running_...

Además: hashes en lugar de secretos (§7.4, verificado leyendo los bytes
crudos del archivo), validez de sesiones (expiración/revocación/borrado
del espacio), tombstones que sobreviven al reinicio, eventos monotónicos
con reconexión Last-Event-ID (§3.3) y migraciones versionadas idempotentes.

Nota para quien no conozca pytest: el fixture `store` crea la base en un
directorio temporal fresco por test (pytest la borra al terminar). La
marca `unit` indica que no hay red ni servicios externos: SQLite es un
archivo local.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.responses import DocumentoFuente

pytestmark = pytest.mark.unit


def test_persist_admite_otro_job_terminal_conservando_una_generacion(store):
    store.crear_workspace("ws_persist", "codigo", dias_validez=30)
    store.registrar_documento("doc_persist", "ws_persist", "Fuente", DocumentStatus.READY, "a" * 64)
    store.registrar_generacion("gen_persist", "ws_persist", "doc_persist")
    store.encolar_job("job_redaccion", "ws_persist", "generation", "gen_persist", "doc_persist")
    store.iniciar_job("job_redaccion")
    store.finalizar_job("job_redaccion", JobStatus.FAILED, error_code="STORAGE_UNAVAILABLE")
    assert store.obtener_documento("doc_persist")["estado"] == "ready"
    store.actualizar_generacion("gen_persist", JobStatus.QUEUED)
    store.encolar_job("job_subida", "ws_persist", "persist", "gen_persist", "doc_persist")
    assert store.job_generacion("gen_persist")["job_id"] == "job_subida"
    with pytest.raises(sqlite3.IntegrityError):
        store.encolar_job("job_duplicado", "ws_persist", "persist", "gen_persist", "doc_persist")
    assert store.obtener_job("job_duplicado") is None
    store.iniciar_job("job_subida")
    store.finalizar_job("job_subida", JobStatus.COMPLETED)
    assert store.obtener_generacion("gen_persist")["status"] == "completed"


@pytest.fixture
def ruta_db(tmp_path) -> Path:
    """Ruta del archivo de base en un directorio temporal del test."""
    return tmp_path / "registro_operativo.sqlite3"


@pytest.fixture
def store(ruta_db) -> RegistroOperativo:
    """Instancia cerrada automáticamente al terminar el test."""
    registro = RegistroOperativo(ruta_db)
    yield registro
    registro.cerrar()


def _espacio_de_ejemplo(registro: RegistroOperativo) -> str:
    """Espacio + documento + generación en estados intermedios, para los tests."""
    registro.crear_workspace("ws_1", "CODIGO-DE-RECUPERACION-1234", dias_validez=30)
    registro.registrar_documento("doc_1", "ws_1", "Manual VCN", DocumentStatus.PROCESSING)
    registro.registrar_generacion("gen_1", "ws_1", "doc_1", JobStatus.QUEUED)
    return "ws_1"


# ---------------------------------------------------------------------------
# Criterio 1: el reinicio conserva el estado
# ---------------------------------------------------------------------------


def test_reinicio_conserva_workspace_sesion_y_trabajos(ruta_db):
    """Criterio 1: cerrar y REABRIR la base conserva todo (§7.2 y §14.2)."""
    # --- "primer proceso": crea estado y termina ---
    primero = RegistroOperativo(ruta_db)
    _espacio_de_ejemplo(primero)
    primero.crear_sesion("TOKEN-SESION-CLARO", "ws_1", horas_validez=24)
    primero.actualizar_documento("doc_1", DocumentStatus.READY, hash_documento="sha256:abc")
    primero.actualizar_generacion("gen_1", JobStatus.COMPLETED)
    primero.cerrar()

    # --- "segundo proceso" (post reinicio): instancia nueva, mismo archivo ---
    segundo = RegistroOperativo(ruta_db)

    assert segundo.obtener_workspace("ws_1") is not None
    assert segundo.obtener_documento("doc_1")["estado"] == "ready"
    assert segundo.obtener_generacion("gen_1")["status"] == "completed"
    # La sesión sigue válida tras el reinicio (no se "invalida" sola).
    sesion = segundo.obtener_sesion("TOKEN-SESION-CLARO")
    assert sesion is not None and sesion["workspace_id"] == "ws_1"
    segundo.cerrar()


# ---------------------------------------------------------------------------
# Criterio 3: running interrumpido -> failed/INTERRUPTED
# ---------------------------------------------------------------------------


def test_trabajo_running_queda_failed_tras_reinicio(ruta_db):
    """Criterio 3: matar el proceso con un trabajo running deja el trabajo
    como failed con causa INTERRUPTED cuando la base se vuelve a abrir."""
    primero = RegistroOperativo(ruta_db)
    _espacio_de_ejemplo(primero)
    primero.actualizar_generacion("gen_1", JobStatus.RUNNING)
    # Simulación del "kill": NO hay cierre prolijo de negocio (el commit de
    # SQLite ya persistió el estado running; el proceso simplemente desaparece).
    primero.cerrar()

    segundo = RegistroOperativo(ruta_db)
    trabajo = segundo.obtener_generacion("gen_1")
    assert trabajo["status"] == "failed"
    assert trabajo["error_code"] == "INTERRUPTED"
    segundo.cerrar()


def test_la_recuperacion_solo_toca_los_running(ruta_db):
    """Los queued no se tocan acá (reencolar es decisión del gestor #20) y
    los terminales (completed/failed) permanecen intactos."""
    primero = RegistroOperativo(ruta_db)
    _espacio_de_ejemplo(primero)
    primero.registrar_generacion("gen_2", "ws_1", "doc_1", JobStatus.RUNNING)
    primero.registrar_generacion("gen_3", "ws_1", "doc_1", JobStatus.QUEUED)
    primero.actualizar_generacion("gen_1", JobStatus.COMPLETED)
    primero.cerrar()

    segundo = RegistroOperativo(ruta_db)
    assert segundo.obtener_generacion("gen_1")["status"] == "completed"  # terminal intacto
    assert segundo.obtener_generacion("gen_2")["status"] == "failed"  # running -> failed
    assert segundo.obtener_generacion("gen_3")["status"] == "queued"  # queued intacto
    segundo.cerrar()


# ---------------------------------------------------------------------------
# Criterio 2: idempotencia (§7.3)
# ---------------------------------------------------------------------------


def test_idempotencia_misma_clave_mismo_cuerpo_devuelve_mismo_recurso(store):
    """Criterio 2a: el reintento NO duplica el recurso; devuelve el mismo id."""
    cuerpo = '{"document_id": "doc_1", "formato_salida": "flashcards"}'

    resultado_1, recurso_1 = store.idempotencia_iniciar("ws_1", "generate", "clave-1", cuerpo, recurso_id="gen_1")
    assert resultado_1 == "creada"
    # El trabajo "real" crea el recurso y cierra la intención.
    store.idempotencia_completar("ws_1", "generate", "clave-1", "gen_1")

    # Reintento idéntico (misma clave, mismo cuerpo): MISMO identificador.
    resultado_2, recurso_2 = store.idempotencia_iniciar("ws_1", "generate", "clave-1", cuerpo)
    assert resultado_2 == "duplicada"
    assert recurso_2 == "gen_1"


def test_idempotencia_misma_clave_cuerpo_distinto_conflicto(store):
    """Criterio 2b: misma clave con otro cuerpo -> conflicto (la API: 409)."""
    store.idempotencia_iniciar("ws_1", "generate", "clave-1", '{"formato": "quiz"}', recurso_id="gen_1")
    resultado, recurso = store.idempotencia_iniciar("ws_1", "generate", "clave-1", '{"formato": "flashcards"}')
    assert resultado == "conflicto"
    assert recurso is None


def test_idempotencia_duplicada_en_curso_con_recurso_reservado(store):
    """Un reintento mientras la primera intención sigue en curso devuelve
    'duplicada' con el mismo recurso reservado: no crea otro."""
    cuerpo = "{}"
    store.idempotencia_iniciar("ws_1", "upload", "clave-up", cuerpo, recurso_id="doc_1")
    resultado, recurso = store.idempotencia_iniciar("ws_1", "upload", "clave-up", cuerpo)
    assert resultado == "duplicada"
    assert recurso == "doc_1"


def test_idempotencia_acotada_por_workspace_y_operacion(store):
    """La terna es (workspace, operación, clave): la misma clave en OTRO
    espacio u otra operación es una intención distinta (§7.3)."""
    store.crear_workspace("ws_2", "CODIGO-2", dias_validez=30)
    assert store.idempotencia_iniciar("ws_1", "generate", "k", "{}", "gen_1")[0] == "creada"
    assert store.idempotencia_iniciar("ws_2", "generate", "k", "{}", "gen_2")[0] == "creada"  # otro espacio
    assert store.idempotencia_iniciar("ws_1", "upload", "k", "{}", "doc_2")[0] == "creada"  # otra operación


def test_idempotencia_sobrevive_al_reinicio(ruta_db):
    """La clave viva tras un reinicio sigue siendo la misma intención."""
    cuerpo = '{"a": 1}'
    primero = RegistroOperativo(ruta_db)
    primero.crear_workspace("ws_1", "C", dias_validez=30)
    primero.idempotencia_iniciar("ws_1", "generate", "k", cuerpo, recurso_id="gen_9")
    primero.idempotencia_completar("ws_1", "generate", "k", "gen_9")
    primero.cerrar()

    segundo = RegistroOperativo(ruta_db)
    resultado, recurso = segundo.idempotencia_iniciar("ws_1", "generate", "k", cuerpo)
    assert (resultado, recurso) == ("duplicada", "gen_9")
    segundo.cerrar()


# ---------------------------------------------------------------------------
# §7.4: jamás secretos en claro dentro del archivo
# ---------------------------------------------------------------------------


def test_la_base_no_guarda_secretos_en_claro(ruta_db):
    """El archivo de la base NO contiene el código ni el token en claro,
    aunque sí sus hashes (verificamos los BYTES crudos del archivo)."""
    registro = RegistroOperativo(ruta_db)
    registro.crear_workspace("ws_1", "CODIGO-DE-RECUPERACION-1234", dias_validez=30)
    registro.crear_sesion("TOKEN-SESION-CLARO", "ws_1", horas_validez=24)
    registro.idempotencia_iniciar("ws_1", "generate", "k", "{}", recurso_id="gen_1")
    with pytest.raises(ValueError, match="credenciales"):
        registro.idempotencia_completar(
            "ws_1", "generate", "k", "gen_1", respuesta={"recovery_code": "CODIGO-DE-RECUPERACION-1234"}
        )
    registro.cerrar()

    bytes_crudos = ruta_db.read_bytes()
    assert b"CODIGO-DE-RECUPERACION-1234" not in bytes_crudos  # el código nunca viaja
    assert b"TOKEN-SESION-CLARO" not in bytes_crudos
    # Y el espacio sigue verificable con el código correcto (hash match).
    registro = RegistroOperativo(ruta_db)
    assert registro.obtener_workspace("ws_1") is not None
    registro.cerrar()


# ---------------------------------------------------------------------------
# Sesiones: validez, revocación y borrado del espacio
# ---------------------------------------------------------------------------


def test_sesion_expirada_y_revocada_no_sirven(store):
    from datetime import datetime, timedelta, timezone

    from app.jobs.store import _hash_de

    store.crear_workspace("ws_1", "C1", dias_validez=30)
    store.crear_sesion("token-valido", "ws_1", horas_validez=24)
    store.crear_sesion("token-viejo", "ws_1", horas_validez=1)

    # Simular que pasaron 2 horas: vencer TODAS las sesiones y re-frescar
    # solo la que el test quiere mantener vigente.
    pasado = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    futuro = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
    with store._lock:  # el test manipula la base como lo haría el tiempo
        store._conn.execute("UPDATE sessions SET expira_en = ?", (pasado,))
        store._conn.execute(
            "UPDATE sessions SET expira_en = ? WHERE token_hash = ?",
            (futuro, _hash_de("token-valido")),
        )

    assert store.obtener_sesion("token-viejo") is None  # expirada
    assert store.obtener_sesion("token-valido") is not None

    # Revocada: deja de servir aunque el token "exista".
    store.revocar_sesion("token-valido")
    assert store.obtener_sesion("token-valido") is None


def test_borrar_el_espacio_invalida_sesiones_y_filtra_recursos(store):
    """§8.5: el borrado revoca acceso en el acto; la lápida queda agendada."""

    _espacio_de_ejemplo(store)
    store.crear_sesion("token-vivo", "ws_1", horas_validez=24)

    # UNA llamada: marca borrado_en, revoca sesiones y agenda la lápida.
    store.borrar_workspace("ws_1")

    # El acceso queda bloqueado aunque la "limpieza física" sea posterior.
    assert store.obtener_workspace("ws_1") is None
    assert store.obtener_sesion("token-vivo") is None
    assert store.obtener_documento("doc_1") is None
    assert store.obtener_generacion("gen_1") is None
    assert store.listar_generaciones("ws_1") == []
    assert store.esta_borrado("workspace", "ws_1")


def test_borrar_un_documento_retira_sus_derivados_del_listado(store):
    """§8.5: "borrar un documento retira original, índice, DERIVADOS..." —
    las generaciones de un documento con lápida no aparecen en el listado
    del espacio, aunque la generación en sí no tenga lápida propia."""
    _espacio_de_ejemplo(store)  # doc_1 + gen_1 (de doc_1)
    store.registrar_documento("doc_2", "ws_1", "Otro manual", DocumentStatus.READY)
    store.registrar_generacion("gen_2", "ws_1", "doc_2", JobStatus.COMPLETED)

    # Se borra SOLO el documento 1: su derivado (gen_1) se retira del listado;
    # el del documento 2 sigue visible.
    store.agendar_borrado("document", "doc_1", "ws_1")

    ids_listados = {generacion["generation_id"] for generacion in store.listar_generaciones("ws_1")}
    assert ids_listados == {"gen_2"}
    # La generación retirada del listado tampoco se resuelve individualmente
    # si el flujo de borrado de #19 le pone lápida propia; sin ella, al menos
    # el LISTADO ya cumple §8.5 (la lápida propia la decide el orquestador).
    assert store.obtener_generacion("gen_1") is None  # la lápida del documento también protege la consulta directa


@pytest.mark.parametrize(
    "retirados",
    [("doc_a",), ("doc_b",), ("doc_a", "doc_b")],
    ids=["primaria", "secundaria", "ambas"],
)
def test_relacion_de_purga_multidocumental_conserva_fuentes_y_retiradas(store, retirados):
    store.crear_workspace("ws_1", "codigo", dias_validez=30)
    for document_id in ("doc_a", "doc_b"):
        store.registrar_documento(document_id, "ws_1", document_id, DocumentStatus.READY, document_id * 8)
    for document_id in retirados:
        store.agendar_borrado("document", document_id, "ws_1")
    documentos_previos = [tuple(fila) for fila in store._conn.execute("SELECT * FROM documents ORDER BY document_id")]
    lapidas_previas = [tuple(fila) for fila in store._conn.execute("SELECT * FROM pending_deletes ORDER BY recurso_id")]

    store.registrar_generacion_retirada("gen_purga", "ws_1", "doc_a", document_ids=["doc_a", "doc_b"])

    assert set(store.documentos_generacion("gen_purga")) == {"doc_a", "doc_b"}
    assert store._conn.execute("SELECT document_id FROM generations WHERE generation_id='gen_purga'").fetchone()[0] == (
        "doc_a"
    )
    assert store.obtener_generacion("gen_purga") is None
    assert store.listar_generaciones("ws_1") == []
    for document_id in ("doc_a", "doc_b"):
        assert store.recursos_documentales("ws_1", document_id)[0] == ["gen_purga"]
        assert store.esta_borrado("document", document_id) is (document_id in retirados)
        if document_id in retirados:
            assert store.obtener_documento(document_id) is None
        else:
            assert store.obtener_documento(document_id)["estado"] == "ready"
    assert [tuple(fila) for fila in store._conn.execute("SELECT * FROM documents ORDER BY document_id")] == (
        documentos_previos
    )
    assert [tuple(fila) for fila in store._conn.execute("SELECT * FROM pending_deletes ORDER BY recurso_id")] == (
        lapidas_previas
    )


@pytest.mark.parametrize("document_ids", [None, ["doc_a"]], ids=["legacy", "lista_explicita"])
def test_relacion_de_purga_unidocumental_conserva_compatibilidad(store, document_ids):
    store.crear_workspace("ws_1", "codigo", dias_validez=30)
    store.registrar_documento("doc_a", "ws_1", "Fuente", DocumentStatus.READY)
    store.agendar_borrado("document", "doc_a", "ws_1")

    store.registrar_generacion_retirada("gen_purga", "ws_1", "doc_a", document_ids=document_ids)

    assert store.documentos_generacion("gen_purga") == ["doc_a"]
    assert store.recursos_documentales("ws_1", "doc_a")[0] == ["gen_purga"]
    assert store.obtener_generacion("gen_purga") is None


@pytest.mark.parametrize(
    "fuentes,document_ids,lapida",
    [
        pytest.param(
            {"doc_a": "ws_2", "doc_b": "ws_1"},
            ["doc_a", "doc_b"],
            ("document", "doc_b", "ws_1"),
            id="primaria_ajena",
        ),
        pytest.param(
            {"doc_a": "ws_1", "doc_b": "ws_2"},
            ["doc_a", "doc_b"],
            ("document", "doc_a", "ws_1"),
            id="secundaria_ajena",
        ),
        pytest.param({"doc_b": "ws_1"}, ["doc_a", "doc_b"], ("document", "doc_b", "ws_1"), id="primaria_inexistente"),
        pytest.param({"doc_a": "ws_1"}, ["doc_a", "doc_b"], ("document", "doc_a", "ws_1"), id="secundaria_inexistente"),
        pytest.param(
            {"doc_a": "ws_1", "doc_b": "ws_1"},
            ["doc_b"],
            ("document", "doc_b", "ws_1"),
            id="primaria_fuera_del_conjunto",
        ),
        pytest.param({"doc_a": "ws_1"}, [], ("document", "doc_a", "ws_1"), id="conjunto_vacio"),
        pytest.param({"doc_a": "ws_1", "doc_b": "ws_1"}, ["doc_a", "doc_b"], None, id="sin_lapida"),
        pytest.param(
            {"doc_a": "ws_1", "doc_b": "ws_1"},
            ["doc_a", "doc_b"],
            ("generation", "gen_purga", "ws_1"),
            id="solo_lapida_de_generacion",
        ),
        pytest.param(
            {"doc_a": "ws_1", "doc_b": "ws_1"},
            ["doc_a", "doc_b"],
            ("workspace", "ws_1", "ws_1"),
            id="solo_lapida_de_workspace",
        ),
        pytest.param(
            {"doc_a": "ws_1", "doc_b": "ws_1"},
            ["doc_a", "doc_b"],
            ("document", "doc_b", "ws_2"),
            id="lapida_documental_de_otro_workspace",
        ),
    ],
)
def test_relacion_de_purga_rechaza_fuentes_o_lapidas_invalidas_sin_insertar(store, fuentes, document_ids, lapida):
    store.crear_workspace("ws_1", "codigo_1", dias_validez=30)
    store.crear_workspace("ws_2", "codigo_2", dias_validez=30)
    for document_id, workspace_id in fuentes.items():
        store.registrar_documento(document_id, workspace_id, document_id, DocumentStatus.READY)
    if lapida is not None:
        store.agendar_borrado(*lapida)

    with pytest.raises(ValueError):
        store.registrar_generacion_retirada("gen_purga", "ws_1", "doc_a", document_ids=document_ids)

    assert store._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM generation_documents").fetchone()[0] == 0


def _preparar_control_recuperado(registro, workspace_id="ws_1", document_id_a="doc_a", document_id_b="doc_b"):
    from app.core.rag.rebuild import ManifiestoDocumento

    registro.crear_workspace(workspace_id, f"codigo_{workspace_id}", dias_validez=30)
    fuentes = [
        DocumentoFuente(
            document_id=document_id,
            titulo=f"Fuente {document_id}",
            hash=hashlib.sha256(f"Contenido independiente de {document_id}".encode()).hexdigest(),
            version="v1",
        )
        for document_id in (document_id_a, document_id_b)
    ]
    registro.registrar_documento(document_id_b, workspace_id, fuentes[1].titulo, DocumentStatus.READY, fuentes[1].hash)
    registro.agendar_borrado("document", document_id_b, workspace_id)
    manifest = ManifiestoDocumento(
        workspace_id=workspace_id,
        document_id=document_id_a,
        source_name=f"{document_id_a}.md",
        titulo=fuentes[0].titulo,
        hash_sha256=fuentes[0].hash,
        estado=DocumentStatus.READY,
        admision_confirmada=True,
        admision_sqlite_confirmada=True,
        metadatos=None,
    )
    return fuentes, {document_id_a: {"etag": f"etag_{document_id_a}", "manifest": manifest.model_dump(mode="json")}}


@pytest.mark.parametrize("snapshot_como_dict", [False, True], ids=["modelos", "diccionarios"])
def test_control_retirado_sin_documento_persiste_procedencia_y_relaciones(ruta_db, snapshot_como_dict):
    registro = RegistroOperativo(ruta_db)
    fuentes, procedencia = _preparar_control_recuperado(registro)
    snapshot = [fuente.model_dump(mode="json") for fuente in fuentes]
    try:
        registro.registrar_generacion_retirada(
            "gen_control",
            "ws_1",
            "doc_a",
            ["doc_a", "doc_b"],
            snapshot=snapshot if snapshot_como_dict else fuentes,
            procedencia=procedencia,
        )
    finally:
        registro.cerrar()

    registro = RegistroOperativo(ruta_db)
    try:
        fila = registro._conn.execute("SELECT * FROM retired_generation_controls").fetchone()
        assert fila["generation_id"] == "gen_control"
        assert fila["workspace_id"] == "ws_1"
        assert fila["document_id"] == "doc_a"
        assert json.loads(fila["snapshot_fuentes_json"]) == snapshot
        assert json.loads(fila["procedencia_json"]) == procedencia
        assert registro.snapshot_generacion("gen_control") == snapshot
        assert set(registro.documentos_generacion("gen_control")) == {"doc_a", "doc_b"}
        for document_id in ("doc_a", "doc_b"):
            assert registro.recursos_documentales("ws_1", document_id) == (["gen_control"], [])
            assert registro.recursos_documentales("ws_ajeno", document_id) == ([], [])
        assert registro.obtener_generacion("gen_control") is None
        assert registro.listar_generaciones("ws_1") == []
        assert registro.obtener_documento("doc_a") is None
        assert not registro.esta_borrado("document", "doc_a")
        assert registro.esta_borrado("document", "doc_b")
        for tabla in ("generations", "generation_documents", "document_admissions", "jobs", "document_cleanup"):
            assert registro._conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0
        assert registro._conn.execute("SELECT document_id FROM documents").fetchone()[0] == "doc_b"
        assert registro._conn.execute("SELECT COUNT(*) FROM documents WHERE document_id='doc_a'").fetchone()[0] == 0
        assert registro._conn.execute("SELECT COUNT(*) FROM pending_deletes").fetchone()[0] == 1
    finally:
        registro.cerrar()


@pytest.mark.parametrize(
    "caso",
    [
        "sin_procedencia",
        "workspace_ajeno",
        "identidad_ajena",
        "no_confirmada",
        "sqlite_no_confirmada",
        "confirmacion_legacy",
        "sin_etag",
        "hash_manifiesto",
        "hash_fuente_existente",
        "snapshot_incompleto",
        "sin_snapshot",
        "primario_fuera",
        "sin_lapida",
        "fila_ajena",
        "procedencia_extra",
        "procedencia_existente",
    ],
)
def test_control_retirado_rechaza_procedencia_invalida_sin_publicar_ni_insertar(store, caso):
    fuentes, procedencia = _preparar_control_recuperado(store)
    store.crear_workspace("ws_2", "codigo_ajeno", dias_validez=30)
    snapshot = [fuente.model_dump(mode="json") for fuente in fuentes]
    document_id = "doc_a"
    if caso == "sin_procedencia":
        procedencia = None
    elif caso == "workspace_ajeno":
        procedencia["doc_a"]["manifest"]["workspace_id"] = "ws_2"
    elif caso == "identidad_ajena":
        procedencia["doc_a"]["manifest"]["document_id"] = "doc_ajeno"
    elif caso == "no_confirmada":
        procedencia["doc_a"]["manifest"]["admision_confirmada"] = False
    elif caso == "sqlite_no_confirmada":
        procedencia["doc_a"]["manifest"]["admision_sqlite_confirmada"] = False
    elif caso == "confirmacion_legacy":
        procedencia["doc_a"]["manifest"]["admision_confirmada"] = None
        procedencia["doc_a"]["manifest"]["admision_sqlite_confirmada"] = None
    elif caso == "sin_etag":
        procedencia["doc_a"].pop("etag")
    elif caso == "hash_manifiesto":
        procedencia["doc_a"]["manifest"]["hash_sha256"] = hashlib.sha256(b"Otro original de A").hexdigest()
    elif caso == "hash_fuente_existente":
        snapshot[1]["hash"] = hashlib.sha256(b"Otro original de B").hexdigest()
    elif caso == "snapshot_incompleto":
        snapshot = snapshot[:1]
    elif caso == "sin_snapshot":
        snapshot = None
    elif caso == "primario_fuera":
        document_id = "doc_ajeno"
    elif caso == "sin_lapida":
        store._conn.execute("DELETE FROM pending_deletes")
    elif caso == "fila_ajena":
        store.registrar_documento("doc_a", "ws_2", "Fuente ajena", DocumentStatus.READY, fuentes[0].hash)
    elif caso == "procedencia_extra":
        procedencia["doc_ajeno"] = procedencia["doc_a"]
    else:
        procedencia["doc_b"] = procedencia["doc_a"]
    documentos_previos = [tuple(fila) for fila in store._conn.execute("SELECT * FROM documents ORDER BY document_id")]
    lapidas_previas = [tuple(fila) for fila in store._conn.execute("SELECT * FROM pending_deletes ORDER BY recurso_id")]

    with pytest.raises(ValueError):
        store.registrar_generacion_retirada(
            "gen_control", "ws_1", document_id, ["doc_a", "doc_b"], snapshot=snapshot, procedencia=procedencia
        )

    for tabla in ("retired_generation_controls", "generations", "generation_documents", "document_admissions", "jobs"):
        assert store._conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] == 0
    assert [
        tuple(fila) for fila in store._conn.execute("SELECT * FROM documents ORDER BY document_id")
    ] == documentos_previos
    assert [
        tuple(fila) for fila in store._conn.execute("SELECT * FROM pending_deletes ORDER BY recurso_id")
    ] == lapidas_previas


@pytest.mark.parametrize("control_primero", [True, False], ids=["control_existe", "generacion_existe"])
def test_control_retirado_y_generacion_publica_no_comparten_identidad(store, control_primero):
    fuentes, procedencia = _preparar_control_recuperado(store)
    hash_c = hashlib.sha256(b"Fuente C independiente y vigente").hexdigest()
    store.registrar_documento("doc_c", "ws_1", "Fuente C", DocumentStatus.READY, hash_c)

    def registrar_control():
        store.registrar_generacion_retirada(
            "gen_compartida", "ws_1", "doc_a", ["doc_a", "doc_b"], snapshot=fuentes, procedencia=procedencia
        )

    if control_primero:
        registrar_control()
        with pytest.raises(ValueError):
            store.registrar_generacion("gen_compartida", "ws_1", "doc_c")
        assert store.obtener_generacion("gen_compartida") is None
        assert store._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 0
        assert store._conn.execute("SELECT COUNT(*) FROM retired_generation_controls").fetchone()[0] == 1
    else:
        store.registrar_generacion("gen_compartida", "ws_1", "doc_c")
        with pytest.raises(ValueError):
            registrar_control()
        assert store.obtener_generacion("gen_compartida")["document_id"] == "doc_c"
        assert store.documentos_generacion("gen_compartida") == ["doc_c"]
        assert store._conn.execute("SELECT COUNT(*) FROM generations").fetchone()[0] == 1
        assert store._conn.execute("SELECT COUNT(*) FROM retired_generation_controls").fetchone()[0] == 0


def test_purga_workspace_elimina_solo_sus_controles_retirados(store):
    for workspace_id, document_id_a, document_id_b, generation_id in (
        ("ws_1", "doc_a", "doc_b", "gen_control"),
        ("ws_2", "doc_otra_a", "doc_otra_b", "gen_otra"),
    ):
        fuentes, procedencia = _preparar_control_recuperado(store, workspace_id, document_id_a, document_id_b)
        store.registrar_generacion_retirada(
            generation_id,
            workspace_id,
            document_id_a,
            [document_id_a, document_id_b],
            snapshot=fuentes,
            procedencia=procedencia,
        )

    store.purgar_datos_espacio("ws_1")

    filas = store._conn.execute("SELECT generation_id, workspace_id FROM retired_generation_controls").fetchall()
    assert [tuple(fila) for fila in filas] == [("gen_otra", "ws_2")]
    assert store.documentos_generacion("gen_control") == []
    assert store.snapshot_generacion("gen_control") is None
    assert store.recursos_documentales("ws_1", "doc_b") == ([], [])
    assert store.recursos_documentales("ws_2", "doc_otra_b") == (["gen_otra"], [])
    assert not store.esta_borrado("document", "doc_a")
    assert store._conn.execute("SELECT COUNT(*) FROM documents WHERE document_id='doc_a'").fetchone()[0] == 0


# ---------------------------------------------------------------------------
# Tombstones que sobreviven al reinicio (durables a nivel local)
# ---------------------------------------------------------------------------


def test_tombstone_sobrevive_reinicio_y_bloquea_resurreccion(ruta_db):
    """§11.5: tras retirar un recurso, ni un reinicio ni una "reconstrucción"
    desde manifiestos lo hacen reaparecer mientras la lápida esté vigente.

    Dos capas de defensa, verificadas acá:
    1. Las LECTURAS respetan la lápida: obtener_documento devuelve None.
    2. Una reconstrucción respinga consultando esta_borrado ANTES de
       insertar; si alguien inserta a lo bruto con el mismo id, SQLite lo
       rechaza (la fila original nunca se borró físicamente): no existe
       "resurrección silenciosa".
    """
    import sqlite3

    primero = RegistroOperativo(ruta_db)
    primero.crear_workspace("ws_1", "C", dias_validez=30)
    primero.registrar_documento("doc_1", "ws_1", "Manual", DocumentStatus.READY)
    primero.agendar_borrado("document", "doc_1", "ws_1")
    primero.cerrar()

    segundo = RegistroOperativo(ruta_db)
    assert segundo.esta_borrado("document", "doc_1") is True
    assert segundo.obtener_documento("doc_1") is None

    # Reconstrucción CORRECTA (la que hará el flujo de #9/#14): consulta la
    # lápida y se salta el recurso retirado.
    if not segundo.esta_borrado("document", "doc_1"):
        segundo.registrar_documento("doc_1", "ws_1", "Manual", DocumentStatus.READY)
    assert segundo.obtener_documento("doc_1") is None  # sigue retirado

    # Reconstrucción INGENUA: el mismo id choca con la fila viva (UNIQUE):
    with pytest.raises(sqlite3.IntegrityError):
        segundo.registrar_documento("doc_1", "ws_1", "Manual", DocumentStatus.READY)
    segundo.cerrar()


# ---------------------------------------------------------------------------
# Eventos: id monotónico y reconexión Last-Event-ID (§3.3)
# ---------------------------------------------------------------------------


def test_eventos_monotonicos_y_reconexion(store):
    id1 = store.registrar_evento("ws_1", "supervisor", JobStatus.RUNNING, generation_id="gen_1")
    id2 = store.registrar_evento("ws_1", "writer", JobStatus.RUNNING, generation_id="gen_1", iteration=1)
    id3 = store.registrar_evento("ws_1", "critic", JobStatus.RUNNING, generation_id="gen_1", iteration=1)

    assert id1 < id2 < id3  # monotónicos

    # El cliente se reconecta habiendo visto hasta id2: recibe solo id3.
    pendientes = store.eventos_desde("gen_1", ultimo_id=id2)
    assert [evento["id"] for evento in pendientes] == [id3]
    assert pendientes[0]["step"] == "critic"


# ---------------------------------------------------------------------------
# Migraciones versionadas
# ---------------------------------------------------------------------------


def test_migraciones_aplican_una_vez(ruta_db):
    """Abrir dos veces no re-aplica migraciones ni duplica estado.

    Usa VERSION_ESQUEMA (no un número fijo) para no romperse cada vez que
    el esquema evoluciona (v2 agregó la tabla jobs del issue #20).
    """
    from app.jobs.store import VERSION_ESQUEMA

    primero = RegistroOperativo(ruta_db)
    assert primero.version_esquema() == VERSION_ESQUEMA
    _espacio_de_ejemplo(primero)
    primero.cerrar()

    segundo = RegistroOperativo(ruta_db)
    assert segundo.version_esquema() == VERSION_ESQUEMA
    assert segundo.obtener_workspace("ws_1") is not None  # el estado sigue
    segundo.cerrar()


def test_upgrade_12_a_13_preserva_documentos_generacion_y_snapshot(ruta_db, monkeypatch):
    from app.jobs import store as modulo_store

    snapshot = [
        DocumentoFuente(
            document_id=document_id,
            titulo=f"Fuente histórica {document_id}",
            hash=hashlib.sha256(f"Contenido histórico independiente de {document_id}".encode()).hexdigest(),
            version="v1",
        ).model_dump(mode="json")
        for document_id in ("doc_legacy_a", "doc_legacy_b")
    ]
    with monkeypatch.context() as parche:
        parche.setattr(modulo_store, "MIGRACIONES", [m for m in modulo_store.MIGRACIONES if m[0] <= 12])
        parche.setattr(modulo_store, "VERSION_ESQUEMA", 12)
        anterior = RegistroOperativo(ruta_db)
        try:
            assert anterior.version_esquema() == 12
            assert (
                anterior._conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE name='retired_generation_controls'"
                ).fetchone()
                is None
            )
            anterior.crear_workspace("ws_legacy", "codigo_historico", dias_validez=30)
            for fuente in snapshot:
                anterior.registrar_documento(
                    fuente["document_id"], "ws_legacy", fuente["titulo"], DocumentStatus.READY, fuente["hash"]
                )
            # La API actual consulta la tabla de v13. Sembrar su representación
            # histórica exacta permite probar una base v12 sin simular esa tabla.
            fecha = "2026-10-01T12:00:00+00:00"
            anterior._conn.execute(
                """INSERT INTO generations
                (generation_id, workspace_id, document_id, status, creado_en, actualizado_en, snapshot_fuentes_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                ("gen_legacy", "ws_legacy", "doc_legacy_a", "completed", fecha, fecha, json.dumps(snapshot)),
            )
            for fuente in snapshot:
                anterior._conn.execute(
                    """INSERT INTO generation_documents (generation_id, document_id, titulo, hash, version)
                    VALUES (?, ?, ?, ?, ?)""",
                    ("gen_legacy", fuente["document_id"], fuente["titulo"], fuente["hash"], fuente["version"]),
                )
            documentos_previos = [anterior.obtener_documento(fuente["document_id"]) for fuente in snapshot]
            generacion_previa = anterior.obtener_generacion("gen_legacy")
            migraciones_previas = [tuple(fila) for fila in anterior._conn.execute("SELECT * FROM schema_migrations")]
        finally:
            anterior.cerrar()

    actualizado = RegistroOperativo(ruta_db)
    try:
        assert actualizado.version_esquema() == 13
        assert [actualizado.obtener_documento(fuente["document_id"]) for fuente in snapshot] == documentos_previos
        assert actualizado.obtener_generacion("gen_legacy") == generacion_previa
        assert actualizado.snapshot_generacion("gen_legacy") == snapshot
        assert set(actualizado.documentos_generacion("gen_legacy")) == {"doc_legacy_a", "doc_legacy_b"}
        for fuente in snapshot:
            assert actualizado.recursos_documentales("ws_legacy", fuente["document_id"]) == (["gen_legacy"], [])
        assert actualizado._conn.execute("SELECT COUNT(*) FROM retired_generation_controls").fetchone()[0] == 0
        migraciones_actuales = [tuple(fila) for fila in actualizado._conn.execute("SELECT * FROM schema_migrations")]
        assert migraciones_actuales[:-1] == migraciones_previas
        assert migraciones_actuales[-1][0] == 13
        assert actualizado._conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert actualizado._conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        fuentes, procedencia = _preparar_control_recuperado(actualizado)
        actualizado.registrar_generacion_retirada(
            "gen_control", "ws_1", "doc_a", ["doc_a", "doc_b"], snapshot=fuentes, procedencia=procedencia
        )
    finally:
        actualizado.cerrar()

    reabierto = RegistroOperativo(ruta_db)
    try:
        assert reabierto.version_esquema() == 13
        assert [
            tuple(fila) for fila in reabierto._conn.execute("SELECT * FROM schema_migrations")
        ] == migraciones_actuales
        assert reabierto.obtener_generacion("gen_legacy") == generacion_previa
        assert reabierto.snapshot_generacion("gen_legacy") == snapshot
        assert reabierto.documentos_generacion("gen_control") == ["doc_a", "doc_b"]
        assert reabierto.obtener_generacion("gen_control") is None
    finally:
        reabierto.cerrar()


def test_registro_operativo_limites_defaults_y_personalizados(tmp_path):
    # Default retrocompatible
    store_default = RegistroOperativo(tmp_path / "default.sqlite3")
    try:
        assert store_default.limite_documentos == 5
        assert store_default.limite_generaciones == 20
        store_default.crear_workspace("ws_def", "codigo", 30)
        uso = store_default.uso_espacio("ws_def")
        assert uso["limite_documentos"] == 5
        assert uso["limite_generaciones"] == 20
    finally:
        store_default.cerrar()

    # Personalizado
    store_custom = RegistroOperativo(
        tmp_path / "custom.sqlite3",
        limite_documentos=3,
        limite_generaciones=7,
    )
    try:
        assert store_custom.limite_documentos == 3
        assert store_custom.limite_generaciones == 7
        store_custom.crear_workspace("ws_cust", "codigo", 30)
        uso = store_custom.uso_espacio("ws_cust")
        assert uso["limite_documentos"] == 3
        assert uso["limite_generaciones"] == 7
    finally:
        store_custom.cerrar()


def test_comprobar_limite_recursos_dinamico_y_override(store):
    from app.schemas.errors import ErrorAplicacion, ErrorCode

    store.crear_workspace("ws_dyn", "codigo", 30)
    store.limite_documentos = 2
    store.registrar_documento("doc_d1", "ws_dyn", "Doc 1", DocumentStatus.READY, "hash1")
    store.registrar_documento("doc_d2", "ws_dyn", "Doc 2", DocumentStatus.READY, "hash2")

    # Sin límite explícito usa el dinámico configurado (2)
    with pytest.raises(ErrorAplicacion) as exc:
        store.comprobar_limite_recursos("ws_dyn", "documents")
    assert exc.value.error.code == ErrorCode.RATE_LIMITED
    assert exc.value.error.details["limite"] == 2
    assert exc.value.error.details["usados"] == 2

    # Override explícito permite comprobar contra otro umbral
    store.comprobar_limite_recursos("ws_dyn", "documents", limite=5)

    # Validaciones defensivas del parámetro limite
    with pytest.raises(ValueError, match="entero positivo"):
        store.comprobar_limite_recursos("ws_dyn", "documents", limite=0)
    with pytest.raises(ValueError, match="entero positivo"):
        store.comprobar_limite_recursos("ws_dyn", "documents", limite=-2)
    with pytest.raises(ValueError, match="entero positivo"):
        store.comprobar_limite_recursos("ws_dyn", "documents", limite=True)
    with pytest.raises(ValueError, match="entero positivo"):
        store.comprobar_limite_recursos("ws_dyn", "documents", limite=3.14)


def test_comprobar_limite_concurrencia_hilos(tmp_path):
    import concurrent.futures

    from app.schemas.errors import ErrorAplicacion

    store = RegistroOperativo(tmp_path / "concurrente.sqlite3", limite_documentos=2)
    try:
        store.crear_workspace("ws_conc", "codigo", 30)
        exitos = 0
        bloqueados = 0

        def intentar_registrar(indice: int):
            try:
                with store.transaccion():
                    store.comprobar_limite_recursos("ws_conc", "documents")
                    store.registrar_documento(
                        f"doc_conc_{indice}", "ws_conc", f"Doc {indice}", DocumentStatus.READY, f"hash_{indice}"
                    )
                return True
            except ErrorAplicacion:
                return False

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            resultados = list(pool.map(intentar_registrar, range(10)))

        exitos = sum(1 for r in resultados if r is True)
        bloqueados = sum(1 for r in resultados if r is False)

        assert exitos == 2
        assert bloqueados == 8
        assert store.uso_espacio("ws_conc")["documentos"] == 2
    finally:
        store.cerrar()


def test_reservar_admision_documental_concurrente_con_clave_compartida_en_limite(tmp_path):
    import concurrent.futures

    from app.schemas.documents import MetadatosDocumento
    from app.schemas.errors import ErrorAplicacion, ErrorCode

    store = RegistroOperativo(tmp_path / "idemp_concurrente.sqlite3", limite_documentos=2)
    try:
        ws_id = "ws_idemp"
        token = "token_idemp_123"
        store.crear_workspace(ws_id, "codigo", 30)
        store.crear_sesion(token, ws_id, 30)

        # 1er documento existente (espacio queda en límite - 1 = 1)
        store.registrar_documento("doc_existente", ws_id, "Doc 0", DocumentStatus.READY, "hash0")
        assert store.uso_espacio(ws_id)["documentos"] == 1

        from datetime import datetime, timezone

        metadatos = MetadatosDocumento(
            extension="txt",
            tamano_bytes=100,
            creado_en=datetime.now(timezone.utc),
        )

        def intentar_admision_idempotente():
            return store.reservar_admision_documental(
                token=token,
                document_id="doc_idemp_test",
                job_id="job_idemp_test",
                titulo="Doc Compartido",
                source_name="compartido.txt",
                hash_documento="hash_comp",
                metadatos=metadatos,
                cuerpo='{"titulo": "Doc Compartido"}',
                clave="clave_compartida_limite",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f1 = pool.submit(intentar_admision_idempotente)
            f2 = pool.submit(intentar_admision_idempotente)
            res1 = f1.result()
            res2 = f2.result()

        nuevas = sum(1 for r in (res1, res2) if r[1] is True)
        duplicadas = sum(1 for r in (res1, res2) if r[1] is False)
        assert nuevas == 1
        assert duplicadas == 1
        assert res1[0]["document_id"] == res2[0]["document_id"]
        assert store.uso_espacio(ws_id)["documentos"] == 2

        # Al pasar la admisión a encolada, un tercer intento con otra clave falla con RATE_LIMITED
        store._conn.execute("UPDATE document_admissions SET estado='encolada' WHERE workspace_id=?", (ws_id,))
        with pytest.raises(ErrorAplicacion) as exc:
            store.reservar_admision_documental(
                token=token,
                document_id="doc_excedente",
                job_id="job_excedente",
                titulo="Doc Excedente",
                source_name="excedente.txt",
                hash_documento="hash_exc",
                metadatos=metadatos,
                cuerpo='{"titulo": "Doc Excedente"}',
                clave="clave_diferente",
            )
        assert exc.value.error.code == ErrorCode.RATE_LIMITED
        assert exc.value.error.details["limite"] == 2
        assert exc.value.error.details["usados"] == 2
    finally:
        store.cerrar()
