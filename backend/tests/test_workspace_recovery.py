"""Pérdida de volumen, lápidas y actividad con storage explícitamente simulado."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from app.core.rag.rebuild import ManifiestoDocumento, _workspace_vigente
from app.jobs import store
from app.jobs.manager import TrabajoCanceladoError
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.responses import PedagogicalOutput
from app.session.manager import SessionManager
from app.session.manifests import sincronizar_pendientes
from app.session.recovery import restaurar_registro
from app.storage.object_keys import clave_documento, clave_original, clave_output, clave_progress, clave_workspace
from app.storage.oci_storage import LocalMockStorageProvider
from app.storage.provider import StorageConflict, StorageUnavailable
from test_schemas import FLASHCARDS, salida_valida

pytestmark = pytest.mark.integration_mock


@pytest.fixture
def pila(tmp_path):
    db = RegistroOperativo(tmp_path / "original.sqlite3")
    storage = LocalMockStorageProvider(tmp_path / "oci")
    manager = SessionManager(db, storage, 30, 24)
    yield db, storage, manager
    db.cerrar()


def fuente(storage, workspace_id, document_id="doc_01HX"):
    original = b"Las redes VCN permiten organizar subredes privadas."
    manifest = ManifiestoDocumento(
        workspace_id=workspace_id,
        document_id=document_id,
        source_name="redes.txt",
        hash_sha256=hashlib.sha256(original).hexdigest(),
        estado=DocumentStatus.READY,
    )
    storage.upload(clave_original(workspace_id, document_id), original)
    storage.upload(clave_documento(workspace_id, document_id), manifest.model_dump_json())
    return manifest


def paquete(storage, workspace_id, doc, generation_id="gen_01HX", *, documentos=None):
    datos = salida_valida(FLASHCARDS).model_dump(mode="json")
    datos["generation_id"] = generation_id
    datos["documento_fuente"]["document_id"] = doc.document_id
    datos["documento_fuente"]["hash"] = doc.hash_sha256
    if documentos is not None:
        datos["documentos_fuente"] = [
            {**datos["documento_fuente"], "document_id": fuente.document_id, "hash": fuente.hash_sha256}
            for fuente in documentos
        ]
    datos["almacenamiento_oci"]["objeto_id"] = clave_output(workspace_id, generation_id)
    output = PedagogicalOutput.model_validate(datos)
    storage.upload(clave_output(workspace_id, generation_id), output.model_dump_json())
    return output


@pytest.mark.parametrize(
    "retiradas,lapida_generacion",
    [
        (("doc_b",), False),
        (("doc_a",), False),
        (("doc_a", "doc_b"), False),
        (("doc_b",), True),
        ((), False),
    ],
    ids=["secundaria", "primaria", "ambas", "documento-y-generacion", "sin-retiradas"],
)
def test_recuperacion_multidocumental_conserva_relaciones_y_estados(pila, tmp_path, retiradas, lapida_generacion):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    ws = creado.workspace_id
    documentos = [fuente(storage, ws, "doc_a"), fuente(storage, ws, "doc_b")]
    output = paquete(storage, ws, documentos[0], documentos=documentos)
    for doc_id in retiradas:
        db.agendar_borrado("document", doc_id, ws)
    if lapida_generacion:
        db.agendar_borrado("generation", output.generation_id, ws)
    sincronizar_pendientes(db, storage)
    storage.upload(
        clave_progress(ws),
        json.dumps({"version": 1, "eventos": [{"generation_id": output.generation_id}]}),
    )
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        assert nuevo.registro_vacio()
        assert restaurar_registro(nuevo, storage) == 1
        assert not nuevo.recuperacion_pendiente()
        assert nuevo.documentos_generacion(output.generation_id) == ["doc_a", "doc_b"]
        for doc in documentos:
            assert nuevo.recursos_documentales(ws, doc.document_id)[0] == [output.generation_id]
            assert nuevo.esta_borrado("document", doc.document_id) == (doc.document_id in retiradas)
            if doc.document_id in retiradas:
                assert nuevo.obtener_documento(doc.document_id) is None
            else:
                assert nuevo.obtener_documento(doc.document_id)["estado"] == "processing"
        assert {efecto["document_id"] for efecto in nuevo.listar_efectos_documentales()} == set(retiradas)
        assert nuevo.esta_borrado("generation", output.generation_id) == lapida_generacion
        if retiradas:
            assert nuevo.obtener_generacion(output.generation_id) is None
            assert nuevo.listar_generaciones(ws) == []
            assert nuevo.obtener_progreso_restaurado(ws)["eventos"] == []
        else:
            assert nuevo.obtener_generacion(output.generation_id)["status"] == "completed"
            assert [g["generation_id"] for g in nuevo.listar_generaciones(ws)] == [output.generation_id]
            assert nuevo.snapshot_generacion(output.generation_id) == [
                f.model_dump(mode="json") for f in output.documentos_fuente
            ]
            assert nuevo.obtener_progreso_restaurado(ws)["eventos"] == [{"generation_id": output.generation_id}]
    finally:
        nuevo.cerrar()


@pytest.mark.parametrize(
    "caso,mensaje",
    [
        ("hash_retirada", "otra versión del documento retirado"),
        ("hash_vigente", "otra versión del documento"),
        ("fuente_ajena", "ausente del espacio recuperado"),
        ("primario_ausente", "primario no coincide"),
        ("primario_incoherente", "primario no coincide"),
        ("identidad", "identidad ajena o no canónica"),
        ("almacenamiento", "almacenamiento ajeno"),
    ],
)
def test_recuperacion_multidocumental_retirada_rechaza_paquetes_incoherentes(pila, tmp_path, caso, mensaje):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    ws = creado.workspace_id
    documentos = [fuente(storage, ws, "doc_a"), fuente(storage, ws, "doc_b")]
    output = paquete(storage, ws, documentos[0], documentos=documentos)
    clave = clave_output(ws, output.generation_id)
    datos = json.loads(storage.get(clave))
    if caso == "hash_retirada":
        datos["documentos_fuente"][1]["hash"] = "c" * 64
    elif caso == "hash_vigente":
        datos["documento_fuente"]["hash"] = datos["documentos_fuente"][0]["hash"] = "c" * 64
    elif caso == "fuente_ajena":
        otro = manager.crear_espacio()
        ajena = fuente(storage, otro.workspace_id, "doc_ajeno")
        datos["documentos_fuente"].append(
            {**datos["documento_fuente"], "document_id": ajena.document_id, "hash": ajena.hash_sha256}
        )
    elif caso == "primario_ausente":
        datos["documentos_fuente"] = datos["documentos_fuente"][1:]
    elif caso == "primario_incoherente":
        datos["documentos_fuente"][0]["hash"] = "c" * 64
    elif caso == "identidad":
        datos["generation_id"] = "gen_ajena"
    else:
        datos["almacenamiento_oci"]["objeto_id"] = clave_output("otro_workspace", output.generation_id)
    storage.upload(clave, json.dumps(datos))
    db.agendar_borrado("document", "doc_b", ws)
    sincronizar_pendientes(db, storage)
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        with pytest.raises(ValueError, match=mensaje):
            restaurar_registro(nuevo, storage)
        assert nuevo.workspace_para_manifest(ws) is None
        assert nuevo.documentos_generacion(output.generation_id) == []
        assert nuevo.recuperacion_pendiente()
        # Rechazar el paquete no elimina la evidencia necesaria para su revisión.
        assert storage.get(clave)
    finally:
        nuevo.cerrar()


@pytest.mark.parametrize(
    "caso,error",
    [
        ("manifest_ausente", ValueError),
        ("manifest_ajeno", ValueError),
        ("clave_no_canonica", ValueError),
        ("hash_incompatible", ValueError),
        ("admision_no_confirmada", ValueError),
        ("etag_cambiado", StorageConflict),
    ],
)
def test_procedencia_residual_multidocumental_rechaza_evidencia_invalida(pila, tmp_path, monkeypatch, caso, error):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    ws = creado.workspace_id
    documentos = []
    for doc_id, contenido in (
        ("doc_a", b"Fuente A confirmada pero sin original recuperable."),
        ("doc_b", b"Fuente B retirada y distinta a las otras fuentes."),
        ("doc_c", b"Fuente C vigente con su original verificable."),
    ):
        manifest = ManifiestoDocumento(
            workspace_id=ws,
            document_id=doc_id,
            source_name=f"{doc_id}.txt",
            hash_sha256=hashlib.sha256(contenido).hexdigest(),
            estado=DocumentStatus.READY,
            **({"admision_confirmada": True, "admision_sqlite_confirmada": True} if doc_id == "doc_a" else {}),
        )
        storage.upload(clave_documento(ws, doc_id), manifest.model_dump_json())
        storage.upload(clave_original(ws, doc_id), contenido)
        documentos.append(manifest)
    a, b, c = documentos
    assert len({doc.hash_sha256 for doc in documentos}) == 3
    output = paquete(storage, ws, a, "gen_ab_residual", documentos=[a, b])
    paquete(storage, ws, c, "gen_c_vigente")
    db.agendar_borrado("document", b.document_id, ws)
    sincronizar_pendientes(db, storage)
    clave_a = clave_documento(ws, a.document_id)
    clave_ab = clave_output(ws, output.generation_id)
    storage.delete(clave_original(ws, a.document_id))
    if caso == "manifest_ausente":
        storage.delete(clave_a)
    elif caso == "manifest_ajeno":
        storage.upload(clave_a, a.model_copy(update={"workspace_id": "ws_ajeno"}).model_dump_json())
    elif caso == "clave_no_canonica":
        storage.upload(clave_documento(ws, "doc_alias"), storage.get(clave_a))
        storage.delete(clave_a)
    elif caso == "hash_incompatible":
        datos = json.loads(storage.get(clave_ab))
        distinto = hashlib.sha256(b"Version de A sin respaldo documental").hexdigest()
        datos["documento_fuente"]["hash"] = datos["documentos_fuente"][0]["hash"] = distinto
        storage.upload(clave_ab, json.dumps(datos))
    elif caso == "admision_no_confirmada":
        storage.upload(clave_a, a.model_copy(update={"admision_confirmada": False}).model_dump_json())
    cambiado = []
    if caso == "etag_cambiado":
        leer = storage.get

        def leer_paquete_y_cambiar_manifest(clave, **kwargs):
            contenido = leer(clave, **kwargs)
            if clave == clave_ab and not cambiado:
                cambiado.append(True)
                storage.upload(clave_a, a.model_copy(update={"titulo": "Cambio concurrente de A"}).model_dump_json())
            return contenido

        monkeypatch.setattr(storage, "get", leer_paquete_y_cambiar_manifest)
    nuevo = RegistroOperativo(tmp_path / "nuevo_residual.sqlite3")
    try:
        with pytest.raises(error):
            restaurar_registro(nuevo, storage)
        assert nuevo.workspace_para_manifest(ws) is None
        assert nuevo.documentos_generacion(output.generation_id) == []
        for tabla in ("documents", "generations", "retired_generation_controls"):
            assert nuevo._conn.execute(f"SELECT 1 FROM {tabla} WHERE workspace_id=?", (ws,)).fetchone() is None
        assert nuevo.obtener_documento(a.document_id) is None
        assert nuevo.recuperacion_pendiente()
        assert storage.get(clave_ab)
        if caso == "etag_cambiado":
            assert cambiado == [True]
    finally:
        nuevo.cerrar()


def test_borrado_durable_no_se_restaura_en_volumen_nuevo(pila, tmp_path):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    fuente(storage, creado.workspace_id)
    manager.borrar_workspace(creado.workspace_id)
    manifest = json.loads(storage.get(clave_workspace(creado.workspace_id)))
    assert manifest["borrado"] and manifest["borrado_en"]
    assert db.listar_manifiestos_pendientes() == []
    ctx = SimpleNamespace(workspace_id=creado.workspace_id, chequear=lambda: None)
    with pytest.raises(TrabajoCanceladoError):
        _workspace_vigente(storage, ctx)
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        assert restaurar_registro(nuevo, storage) == 1
        assert nuevo.resolver_workspace(creado.recovery_code) is None
        assert nuevo.obtener_sesion(creado.token) is None
        assert nuevo.obtener_documento("doc_01HX") is None
        assert nuevo.esta_borrado("workspace", creado.workspace_id)
    finally:
        nuevo.cerrar()


def test_fallo_oci_bloquea_y_reintento_persiste_la_misma_lapida(pila, monkeypatch):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    upload = storage.upload

    def fallo(*args, **kwargs):
        raise StorageUnavailable("detalle-privado-del-proveedor")

    monkeypatch.setattr(storage, "upload", fallo)
    manager.borrar_workspace(creado.workspace_id)
    assert db.obtener_sesion(creado.token) is None
    assert db.listar_manifiestos_pendientes() == [creado.workspace_id]
    version = db.workspace_para_manifest(creado.workspace_id)["version_manifiesto"]
    monkeypatch.setattr(storage, "upload", upload)
    assert sincronizar_pendientes(db, storage) == 1
    manifest = json.loads(storage.get(clave_workspace(creado.workspace_id)))
    assert manifest["borrado"] and manifest["version"] == version
    assert db.listar_manifiestos_pendientes() == []


def test_confirmacion_ambigua_se_reconcilia_sin_otra_escritura(pila, monkeypatch):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    upload = storage.upload
    llamadas = []

    def ambiguo(*args, **kwargs):
        llamadas.append(1)
        upload(*args, **kwargs)
        raise StorageUnavailable("respuesta perdida después de escribir")

    monkeypatch.setattr(storage, "upload", ambiguo)
    manager.borrar_workspace(creado.workspace_id)
    assert db.listar_manifiestos_pendientes() == [creado.workspace_id]
    assert sincronizar_pendientes(db, storage) == 1
    assert len(llamadas) == 1


def test_actividad_renueva_espacio_agrupada_sin_prolongar_token(pila, monkeypatch):
    db, storage, manager = pila
    reloj = [datetime(2026, 10, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(store, "_ahora", lambda: reloj[0])
    creado = manager.crear_espacio()
    token_expira = db.obtener_sesion(creado.token)["expira_en"]
    reloj[0] += timedelta(hours=12)
    assert manager.obtener_sesion(creado.token)
    actualizado = db.obtener_workspace(creado.workspace_id)
    assert datetime.fromisoformat(actualizado["expira_en"]) == reloj[0] + timedelta(days=30)
    version = actualizado["version_manifiesto"]
    for _ in range(10):
        assert manager.obtener_sesion(creado.token)
    assert db.obtener_workspace(creado.workspace_id)["version_manifiesto"] == version
    assert db.obtener_sesion(creado.token)["expira_en"] == token_expira
    assert sincronizar_pendientes(db, storage) == 1
    assert json.loads(storage.get(clave_workspace(creado.workspace_id)))["version"] == version
    reloj[0] += timedelta(hours=13)
    assert manager.obtener_sesion(creado.token) is None


def test_rotacion_no_tiene_exito_si_borraron_tras_la_lectura(pila, monkeypatch):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    obtener = db.obtener_workspace

    def leer_y_borrar(workspace_id):
        vigente = obtener(workspace_id)
        db.borrar_workspace(workspace_id)
        return vigente

    monkeypatch.setattr(db, "obtener_workspace", leer_y_borrar)
    with pytest.raises(ErrorAplicacion) as exc:
        manager.rotar_credenciales(creado.workspace_id)
    assert exc.value.error.code == ErrorCode.INVALID_STATE
    assert db.obtener_sesion(creado.token) is None
    assert sincronizar_pendientes(db, storage) == 1
    assert json.loads(storage.get(clave_workspace(creado.workspace_id)))["borrado"]


def test_perdida_volumen_recupera_recursos_y_codigo_sin_sesiones(pila, tmp_path):
    _, storage, manager = pila
    creado = manager.crear_espacio()
    doc = fuente(storage, creado.workspace_id)
    output = paquete(storage, creado.workspace_id, doc)
    state = {"version": 2, "eventos": [{"generation_id": output.generation_id, "event_id": "evt-1"}]}
    storage.upload(clave_progress(creado.workspace_id), json.dumps(state))
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        assert restaurar_registro(nuevo, storage) == 1
        assert nuevo.resolver_workspace(creado.recovery_code)["workspace_id"] == creado.workspace_id
        assert nuevo.obtener_sesion(creado.token) is None
        assert nuevo.obtener_documento(doc.document_id)["estado"] == "processing"
        assert nuevo.obtener_generacion(output.generation_id)["status"] == "completed"
        assert nuevo.obtener_progreso_restaurado(creado.workspace_id) == state
        sesion = SessionManager(nuevo, storage, 30, 24).recuperar_sesion(creado.recovery_code)
        assert nuevo.obtener_sesion(sesion.token)
        assert restaurar_registro(nuevo, storage) == 0
    finally:
        nuevo.cerrar()


def test_relacion_de_purga_exige_lapida_y_aislamiento(pila):
    db, _, manager = pila
    creado = manager.crear_espacio()
    otro = manager.crear_espacio()
    db.registrar_documento("doc_retirado", creado.workspace_id, "Redes", DocumentStatus.READY)
    with pytest.raises(ValueError):
        db.registrar_generacion_retirada("gen_activa", creado.workspace_id, "doc_retirado")
    db.agendar_borrado("document", "doc_retirado", creado.workspace_id)
    with pytest.raises(ValueError):
        db.registrar_generacion_retirada("gen_ajena", otro.workspace_id, "doc_retirado")
    with pytest.raises(ValueError):
        db.registrar_generacion("gen_publica", creado.workspace_id, "doc_retirado")
    db.registrar_generacion_retirada("gen_purga", creado.workspace_id, "doc_retirado")
    assert db.recursos_documentales(creado.workspace_id, "doc_retirado")[0] == ["gen_purga"]
    assert db.obtener_generacion("gen_purga") is None
    assert db.listar_generaciones(creado.workspace_id) == []


@pytest.mark.e2e
def test_lapidas_de_documento_generacion_y_progreso_no_reaparecen(pila, tmp_path):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    retirado = fuente(storage, creado.workspace_id)
    vigente = fuente(storage, creado.workspace_id, "doc-vigente")
    paquete(storage, creado.workspace_id, retirado)
    paquete(storage, creado.workspace_id, vigente, "gen-vigente")
    db.agendar_borrado("document", retirado.document_id, creado.workspace_id)
    db.agendar_borrado("generation", "gen-vigente", creado.workspace_id)
    sincronizar_pendientes(db, storage)
    storage.upload(
        clave_progress(creado.workspace_id),
        json.dumps(
            {
                "version": 1,
                "eventos": [
                    {"generation_id": "gen_01HX", "event_id": "evt-retirado"},
                    {"generation_id": "gen-vigente", "event_id": "evt-retirado-2"},
                ],
            }
        ),
    )
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        restaurar_registro(nuevo, storage)
        assert nuevo.obtener_documento(retirado.document_id) is None
        assert nuevo.obtener_documento(vigente.document_id)
        assert nuevo.obtener_generacion("gen_01HX") is None
        assert nuevo.obtener_generacion("gen-vigente") is None
        assert nuevo.obtener_progreso_restaurado(creado.workspace_id)["eventos"] == []
        assert nuevo.esta_borrado("document", retirado.document_id)
    finally:
        nuevo.cerrar()


def test_conflicto_oci_no_sobrescribe_version_mas_nueva(pila):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    key = clave_workspace(creado.workspace_id)
    remoto = json.loads(storage.get(key))
    remoto["version"] = 100
    storage.upload(key, json.dumps(remoto))
    with pytest.raises(ErrorAplicacion) as exc:
        manager.rotar_credenciales(creado.workspace_id)
    assert exc.value.error.code == ErrorCode.INVALID_STATE
    assert db.resolver_workspace(creado.recovery_code)
    assert db.obtener_sesion(creado.token)
    assert json.loads(storage.get(key))["version"] == 100


def test_confirmacion_vieja_no_borra_actividad_nueva(pila, monkeypatch):
    db, storage, manager = pila
    reloj = [datetime(2026, 10, 8, tzinfo=timezone.utc)]
    monkeypatch.setattr(store, "_ahora", lambda: reloj[0])
    creado = manager.crear_espacio()
    reloj[0] += timedelta(hours=1)
    manager.obtener_sesion(creado.token)
    anterior = db.obtener_workspace(creado.workspace_id)["version_manifiesto"]
    reloj[0] += timedelta(hours=1)
    manager.obtener_sesion(creado.token)
    db.confirmar_manifiesto(creado.workspace_id, anterior)
    assert db.listar_manifiestos_pendientes() == [creado.workspace_id]


def test_restauracion_interrumpida_se_retoma_sin_duplicar(pila, tmp_path, monkeypatch):
    _, storage, manager = pila
    creados = [manager.crear_espacio(), manager.crear_espacio()]
    ordenados = sorted(creados, key=lambda item: item.workspace_id)
    get = storage.get
    fallo = [True]

    def interrumpido(key, **kwargs):
        if key == clave_workspace(ordenados[1].workspace_id) and fallo[0]:
            raise StorageUnavailable("interrupción simulada")
        return get(key, **kwargs)

    monkeypatch.setattr(storage, "get", interrumpido)
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        with pytest.raises(StorageUnavailable):
            restaurar_registro(nuevo, storage)
        assert nuevo.recuperacion_pendiente()
        assert nuevo.resolver_workspace(ordenados[0].recovery_code)
        fallo[0] = False
        assert restaurar_registro(nuevo, storage) == 1
        assert not nuevo.recuperacion_pendiente()
        assert all(nuevo.resolver_workspace(item.recovery_code) for item in creados)
    finally:
        nuevo.cerrar()


def test_registro_existente_y_lapidas_locales_no_se_sobrescriben(pila):
    db, storage, manager = pila
    creado = manager.crear_espacio()
    db.borrar_workspace(creado.workspace_id)
    restaurar_registro(db, storage)
    assert db.resolver_workspace(creado.recovery_code) is None
    assert db.esta_borrado("workspace", creado.workspace_id)


def test_original_inconsistente_impide_publicar_registros(pila, tmp_path):
    _, storage, manager = pila
    creado = manager.crear_espacio()
    doc = fuente(storage, creado.workspace_id)
    storage.upload(clave_original(creado.workspace_id, doc.document_id), b"otro contenido")
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        with pytest.raises(ValueError, match="Original distinto"):
            restaurar_registro(nuevo, storage)
        assert nuevo.registro_vacio()
        assert nuevo.recuperacion_pendiente()
    finally:
        nuevo.cerrar()


def test_manifesto_heredado_sin_actividad_es_recuperable(pila, tmp_path):
    _, storage, manager = pila
    creado = manager.crear_espacio()
    key = clave_workspace(creado.workspace_id)
    legacy = json.loads(storage.get(key))
    for campo in ("ultima_actividad_en", "borrado", "borrado_en", "recursos_borrados"):
        legacy.pop(campo)
    storage.upload(key, json.dumps(legacy))
    nuevo = RegistroOperativo(tmp_path / "nuevo.sqlite3")
    try:
        assert restaurar_registro(nuevo, storage) == 1
        assert nuevo.resolver_workspace(creado.recovery_code)
        assert nuevo.obtener_sesion(creado.token) is None
    finally:
        nuevo.cerrar()
