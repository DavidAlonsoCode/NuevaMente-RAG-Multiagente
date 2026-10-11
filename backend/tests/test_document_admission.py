"""Admisión #19 con almacenamiento local, SQLite temporal y procesadores explícitos."""

import hashlib
import io
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from app.core.rag.parser import LimitesIngesta
from app.core.rag.rebuild import ManifiestoDocumento
from app.documents.admission import AdmisionNoConfirmadaError, ServicioAdmisionDocumental
from app.jobs.manager import ColaLlenaError, GestorTrabajos
from app.jobs.store import MIGRACIONES, RegistroOperativo
from app.schemas.documents import AdmisionDocumento, CoberturaDocumento, EntradaArchivoDocumento, MetadatosDocumento
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.requests import UploadRequest
from app.session.manifests import sincronizar_manifiesto
from app.session.recovery import restaurar_registro
from app.storage.object_keys import clave_documento, clave_original
from app.storage.provider import StorageConflict, StorageUnavailable
from pydantic import ValidationError

pytestmark = pytest.mark.integration_mock


@pytest.fixture
def entorno(tmp_path, mock_storage):
    db = RegistroOperativo(tmp_path / "operativo.db")
    for ws, token in (("ws_a", "token_a"), ("ws_b", "token_b")):
        db.crear_workspace(ws, f"codigo_{ws}", 30)
        db.crear_sesion(token, ws, 24)
        sincronizar_manifiesto(db, mock_storage, ws)
    gestor = GestorTrabajos(db)
    yield db, mock_storage, gestor
    gestor.detener()
    db.cerrar()


def servicio(entorno, procesar=None, **kwargs):
    db, storage, gestor = entorno
    # Procesador sintético explícito: no anuncia ready ni calcula embeddings.
    return ServicioAdmisionDocumental(
        db, storage, gestor, procesar or (lambda ctx, manifest: {"document_id": manifest.document_id}), **kwargs
    )


def admitir(svc, clave="intento", token="token_a", texto=b"contenido de ejemplo", titulo=None):
    return svc.admitir_archivo(
        token, texto, EntradaArchivoDocumento(source_name="fuente.txt", titulo=titulo), clave=clave
    )


def esperar(db, job_id, status):
    limite = time.monotonic() + 3
    while time.monotonic() < limite:
        fila = db.obtener_job(job_id)
        if fila and fila["status"] == status:
            return fila
        time.sleep(0.005)
    pytest.fail(f"No alcanzó {status}: {db.obtener_job(job_id)}")


def test_original_metadata_manifiesto_y_trabajo_confirmados(entorno):
    db, storage, _ = entorno
    invocado = threading.Event()

    def procesar(ctx, manifest):
        invocado.set()
        assert manifest.admision_confirmada is True
        assert db.obtener_admision_documental("ws_a", manifest.document_id)["estado"] == "encolada"
        return {"document_id": manifest.document_id}

    recibo = admitir(servicio(entorno, procesar), titulo="Título distinto del nombre")
    fila = db.obtener_documento(recibo.document_id)
    assert fila["estado"] == "processing"  # La admisión jamás declara ready.
    assert fila["titulo"] == "Título distinto del nombre"
    assert fila["source_name"] == "fuente.txt"
    metadata = json.loads(fila["metadatos_json"])
    assert metadata["tamano_bytes"] == len(b"contenido de ejemplo")
    assert metadata["cantidad_tokens"] is None
    assert fila["cobertura_json"] is None
    assert fila["hash"] == hashlib.sha256(b"contenido de ejemplo").hexdigest()
    manifest = ManifiestoDocumento.model_validate_json(storage.get(clave_documento("ws_a", recibo.document_id)))
    assert manifest.titulo == fila["titulo"] and manifest.metadatos.extension == "txt"
    assert manifest.admision_confirmada is True
    assert storage.get(clave_original("ws_a", recibo.document_id)) == b"contenido de ejemplo"
    assert esperar(db, recibo.job_id, "completed")["document_id"] == recibo.document_id
    assert invocado.is_set()
    assert db.listar_limpiezas_documentales() == []
    assert [e["status"] for e in db.eventos_desde_job(recibo.job_id)] == ["queued", "running", "completed"]


def test_texto_pegado_preserva_original_txt(entorno):
    texto = "Contenido en español.\nCon otra línea.  "
    svc = servicio(entorno)
    entrada = UploadRequest(documento_titulo="Manual", documento_contenido=texto)
    recibo = svc.admitir_texto("token_a", entrada, clave="texto")
    assert entorno[1].get(clave_original("ws_a", recibo.document_id)) == texto.encode("utf-8")
    assert entorno[0].obtener_documento(recibo.document_id)["titulo"] == "Manual"


def test_reintento_en_curso_reserva_mismos_ids_sin_io_duplicado(entorno, monkeypatch):
    db, storage, _ = entorno
    iniciado, liberar = threading.Event(), threading.Event()
    original = storage.upload
    escrituras = []

    def upload(*args, **kwargs):
        escrituras.append(args[0])
        if args[0].endswith("/original"):
            iniciado.set()
            assert liberar.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(storage, "upload", upload)
    svc1, svc2 = servicio(entorno), servicio(entorno)
    with ThreadPoolExecutor(max_workers=2) as pool:
        primera = pool.submit(admitir, svc1)
        try:
            assert iniciado.wait(2)
            observando = threading.Event()
            obtener = db.obtener_admision_documental

            def observar(*args):
                fila = obtener(*args)
                if threading.current_thread() is not threading.main_thread():
                    observando.set()
                return fila

            monkeypatch.setattr(db, "obtener_admision_documental", observar)
            repetida = pool.submit(admitir, svc2)
            assert observando.wait(1)
            assert not repetida.done()  # No presenta una reserva como aceptación.
            assert len(escrituras) == 1
            reservada = db._conn.execute("SELECT * FROM document_admissions").fetchone()
            assert db.obtener_documento(reservada["document_id"]) is not None
        finally:
            liberar.set()
        aceptada = primera.result(timeout=3)
        duplicada = repetida.result(timeout=3)
    assert duplicada.admision == "encolada" and duplicada.reutilizada
    assert (duplicada.document_id, duplicada.job_id) == (aceptada.document_id, aceptada.job_id)
    assert duplicada.estado == aceptada.estado == DocumentStatus.PROCESSING
    assert len(escrituras) == 4  # Original, manifiesto pendiente, flag OCI y evidencia del commit SQLite.
    assert db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_misma_clave_conflicto_con_texto_titulo_o_nombre_distinto(entorno):
    svc = servicio(entorno)
    primero = admitir(svc)
    for contenido, nombre, titulo in (
        (b"otro", "fuente.txt", None),
        (b"contenido de ejemplo", "otra.txt", None),
        (b"contenido de ejemplo", "fuente.txt", "otro"),
    ):
        with pytest.raises(ErrorAplicacion) as exc:
            svc.admitir_archivo(
                "token_a", contenido, EntradaArchivoDocumento(source_name=nombre, titulo=titulo), clave="intento"
            )
        assert exc.value.error.code == ErrorCode.IDEMPOTENCY_CONFLICT
    duplicada = admitir(svc)
    assert duplicada.document_id == primero.document_id and duplicada.job_id == primero.job_id


@pytest.mark.parametrize("clave", ["otra", None])
def test_mismo_hash_con_otra_intencion_crea_documento_nuevo(entorno, clave):
    svc = servicio(entorno)
    primero = admitir(svc)
    esperar(entorno[0], primero.job_id, "completed")
    segundo = admitir(svc, clave=clave)
    assert segundo.document_id != primero.document_id and segundo.job_id != primero.job_id
    assert (
        entorno[0].obtener_documento(primero.document_id)["hash"]
        == entorno[0].obtener_documento(segundo.document_id)["hash"]
    )


def test_ownership_idempotencia_no_cruza_workspaces(entorno):
    svc = servicio(entorno)
    a, b = admitir(svc), admitir(svc, token="token_b")
    assert a.document_id != b.document_id
    assert entorno[0].obtener_admision_documental("ws_b", a.document_id) is None
    esperar(entorno[0], b.job_id, "completed")
    with pytest.raises(ValueError):
        entorno[2].enqueue("ws_b", "ingestion", lambda ctx: None, document_id=a.document_id)


@pytest.mark.parametrize("estado", ["invalida", "revocada", "borrada", "vencida"])
def test_sesion_no_vigente_no_escribe_ni_reserva(entorno, estado):
    db, _, _ = entorno
    token = "invalido" if estado == "invalida" else "token_a"
    if estado == "revocada":
        db.revocar_sesion(token)
    if estado == "borrada":
        db.borrar_workspace("ws_a")
    if estado == "vencida":
        db._conn.execute(
            "UPDATE sessions SET expira_en=? WHERE workspace_id='ws_a'",
            ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),),
        )
    with pytest.raises(ErrorAplicacion) as exc:
        admitir(servicio(entorno), token=token)
    assert exc.value.error.code == ErrorCode.SESSION_INVALID
    assert db._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


@pytest.mark.parametrize(
    "nombre,contenido,codigo",
    [
        ("a.exe", b"texto", ErrorCode.VALIDATION_ERROR),
        ("a.pdf", b"falso", ErrorCode.VALIDATION_ERROR),
        ("a.txt", b"\x00binario", ErrorCode.VALIDATION_ERROR),
        ("a.md", b"\xff", ErrorCode.VALIDATION_ERROR),
        ("a.txt", b"   ", ErrorCode.VALIDATION_ERROR),
        ("a.txt", b"", ErrorCode.VALIDATION_ERROR),
        ("a.txt", b"123456789", ErrorCode.DOCUMENT_TOO_LARGE),
        ("a.pdf", b"%PDF-123456789", ErrorCode.DOCUMENT_TOO_LARGE),
    ],
)
def test_validacion_barata_antes_de_reservar(entorno, nombre, contenido, codigo):
    svc = servicio(entorno, limites=replace(LimitesIngesta(), max_bytes_pdf=8, max_bytes_texto=8))
    with pytest.raises(ErrorAplicacion) as exc:
        svc.admitir_archivo("token_a", contenido, EntradaArchivoDocumento(source_name=nombre), clave="k")
    assert exc.value.error.code == codigo
    assert entorno[0]._conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 0
    assert entorno[0]._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_stream_acotado_y_nombre_no_es_ruta(entorno):
    svc = servicio(entorno, limites=replace(LimitesIngesta(), max_bytes_texto=8))
    archivo = io.BytesIO(b"a" * 100)
    with pytest.raises(ErrorAplicacion) as exc:
        svc.admitir_archivo("token_a", archivo, EntradaArchivoDocumento(source_name="a.txt"))
    assert exc.value.error.code == ErrorCode.DOCUMENT_TOO_LARGE and archivo.tell() == 9
    recibo = svc.admitir_archivo("token_a", io.BytesIO(b"texto"), EntradaArchivoDocumento(source_name="../../a.txt"))
    assert entorno[1].get(clave_original("ws_a", recibo.document_id)) == b"texto"


@pytest.mark.parametrize(
    "fase",
    ["original_upload", "original_get", "manifest_upload", "manifest_get", "confirmacion_upload", "confirmacion_get"],
)
def test_fallo_parcial_no_ejecuta_pipeline_y_agenda_limpieza(entorno, monkeypatch, fase):
    db, storage, _ = entorno
    original_upload, original_get = storage.upload, storage.get
    ejecuciones, escrituras = [], []

    def upload(key, *args, **kwargs):
        escrituras.append(key)
        etiqueta = "original" if key.endswith("/original") else "confirmacion" if kwargs.get("if_match") else "manifest"
        if fase == etiqueta + "_upload":
            # Simular respuesta perdida: el efecto sí ocurrió antes del error.
            original_upload(key, *args, **kwargs)
            raise StorageUnavailable("secreto-del-proveedor")
        return original_upload(key, *args, **kwargs)

    def get(key, **kwargs):
        etiqueta = "original" if key.endswith("/original") else "confirmacion" if len(escrituras) == 3 else "manifest"
        if fase == etiqueta + "_get":
            raise StorageUnavailable("secreto-del-proveedor")
        return original_get(key, **kwargs)

    monkeypatch.setattr(storage, "upload", upload)
    monkeypatch.setattr(storage, "get", get)
    svc = servicio(entorno, lambda ctx, manifest: ejecuciones.append(manifest.document_id))
    with pytest.raises(ErrorAplicacion) as exc:
        admitir(svc)
    assert exc.value.error.code == ErrorCode.STORAGE_UNAVAILABLE
    assert "secreto" not in exc.value.error.model_dump_json()
    pendientes = db.listar_limpiezas_documentales()
    assert len(pendientes) == 1
    doc_id = pendientes[0]["document_id"]
    assert db.obtener_documento(doc_id)["estado"] == "failed"
    previo = len(escrituras)
    with pytest.raises(ErrorAplicacion):
        admitir(svc)
    assert len(escrituras) == previo and ejecuciones == []


def test_confirmacion_get_diferente_rechaza_contenido(entorno, monkeypatch):
    storage = entorno[1]
    monkeypatch.setattr(storage, "get", lambda key, **kwargs: b"contenido distinto")
    with pytest.raises(ErrorAplicacion) as exc:
        admitir(servicio(entorno))
    assert exc.value.error.code == ErrorCode.STORAGE_UNAVAILABLE
    assert len(entorno[0].listar_limpiezas_documentales()) == 1


def test_conflicto_de_storage_no_sobrescribe(entorno, monkeypatch):
    def upload(*args, **kwargs):
        assert kwargs["crear_solo"] is True
        raise StorageConflict("ya existe")

    monkeypatch.setattr(entorno[1], "upload", upload)
    with pytest.raises(ErrorAplicacion) as exc:
        admitir(servicio(entorno))
    assert exc.value.error.code == ErrorCode.INVALID_STATE
    assert len(entorno[0].listar_limpiezas_documentales()) == 1


def test_revocacion_durante_storage_impide_admision(entorno, monkeypatch):
    db, storage, _ = entorno
    original = storage.upload

    def upload(*args, **kwargs):
        objeto = original(*args, **kwargs)
        db.revocar_sesion("token_a")
        return objeto

    monkeypatch.setattr(storage, "upload", upload)
    with pytest.raises(ErrorAplicacion) as exc:
        admitir(servicio(entorno))
    assert exc.value.error.code == ErrorCode.SESSION_INVALID
    assert db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert len(db.listar_limpiezas_documentales()) == 1


def test_retiro_concurrente_no_publica_resultado(entorno):
    db, _, gestor = entorno
    iniciado, liberar = threading.Event(), threading.Event()

    def procesar(ctx, manifest):
        iniciado.set()
        assert liberar.wait(2)
        return {"resultado": "no publicable"}

    recibo = admitir(servicio(entorno, procesar))
    try:
        assert iniciado.wait(1)
        db.agendar_borrado("document", recibo.document_id, "ws_a")
    finally:
        liberar.set()
    assert esperar(db, recibo.job_id, "cancelled")["resultado"] is None
    assert db.obtener_documento(recibo.document_id) is None


def test_cancelacion_y_fallo_vinculan_estado_documental(entorno):
    db, _, gestor = entorno

    def fallar(ctx, manifest):
        raise RuntimeError("error interno")

    recibo = admitir(servicio(entorno, fallar))
    esperar(db, recibo.job_id, "failed")
    assert db.obtener_documento(recibo.document_id)["estado"] == "failed"
    liberar = threading.Event()
    recibo2 = admitir(servicio(entorno, lambda ctx, manifest: liberar.wait(2)), clave="otra")
    esperar(db, recibo2.job_id, "running")
    gestor.cancelar(recibo2.job_id)
    liberar.set()
    esperar(db, recibo2.job_id, "cancelled")
    assert db.obtener_documento(recibo2.document_id)["estado"] == "failed"


def test_migracion_desde_v5_preserva_registros(tmp_path):
    ruta = tmp_path / "v5.db"
    con = sqlite3.connect(ruta)
    con.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, aplicada_en TEXT NOT NULL)")
    for version, sql in MIGRACIONES:
        if version <= 5:
            con.executescript(sql)
            con.execute(
                "INSERT INTO schema_migrations VALUES (?, ?)", (version, datetime.now(timezone.utc).isoformat())
            )
            con.commit()
    futuro = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    con.execute(
        "INSERT INTO workspaces(workspace_id,codigo_hash,codigo_creado_en,expira_en) VALUES('viejo','hash','fecha',?)",
        (futuro,),
    )
    con.execute(
        "INSERT INTO documents(document_id,workspace_id,titulo,estado,creado_en) VALUES('doc_viejo','viejo','Título','ready','fecha')"
    )
    con.commit()
    con.close()
    db = RegistroOperativo(ruta)
    try:
        from app.jobs.store import VERSION_ESQUEMA

        assert db.version_esquema() == VERSION_ESQUEMA
        fila = db.obtener_documento("doc_viejo")
        assert fila["estado"] == "ready" and fila["titulo"] == "Título"
        assert fila["metadatos_json"] is None and fila["cobertura_json"] is None
    finally:
        db.cerrar()


def test_reserva_interrumpida_y_reintentos_limpieza_sobreviven_reinicio(tmp_path):
    ruta = tmp_path / "durable.db"
    db = RegistroOperativo(ruta)
    db.crear_workspace("ws", "codigo", 30)
    db.crear_sesion("token", "ws", 24)
    metadata = MetadatosDocumento(extension="pdf", tamano_bytes=20, creado_en=datetime.now(timezone.utc))
    db.reservar_admision_documental(
        token="token",
        document_id="doc",
        job_id="job",
        titulo="Título",
        source_name="a.pdf",
        hash_documento="a" * 64,
        metadatos=metadata,
        cuerpo="cuerpo",
        clave="clave",
    )
    db.cerrar()
    db = RegistroOperativo(ruta)
    try:
        assert db.obtener_documento("doc")["estado"] == "failed"
        registro = db.obtener_admision_documental("ws", "doc")
        assert registro["error_code"] == "INTERRUPTED" and registro["job_id"] == "job"
        lapida = db.listar_limpiezas_documentales()[0]
        db.registrar_intento_limpieza_documental("ajeno", "doc")
        assert len(db.listar_limpiezas_documentales()) == 1
        db.registrar_intento_limpieza_documental("ws", "doc", error_code="STORAGE_UNAVAILABLE")
        db.cerrar()
        db = RegistroOperativo(ruta)
        pendiente = db.listar_limpiezas_documentales()[0]
        assert pendiente["intentos"] == 1 and pendiente["solicitado_en"] == lapida["solicitado_en"]
        assert db.idempotencia_iniciar("ws", "upload", "clave", "cuerpo") == ("duplicada", "doc")
        db.registrar_intento_limpieza_documental("ws", "doc")
        assert db.listar_limpiezas_documentales() == []
    finally:
        db.cerrar()


def test_manifest_viejo_y_metadata_visual_restaurada(tmp_path, mock_storage):
    db = RegistroOperativo(tmp_path / "original.db")
    db.crear_workspace("ws", "codigo", 30)
    sincronizar_manifiesto(db, mock_storage, "ws")
    contenido = b"original pdf conservado"
    manifest = ManifiestoDocumento(
        workspace_id="ws", document_id="doc", source_name="a.pdf", hash_sha256=hashlib.sha256(contenido).hexdigest()
    )
    viejo = manifest.model_dump_json(exclude_none=True)
    assert ManifiestoDocumento.model_validate_json(viejo).admision_confirmada is None
    manifest.titulo = "Título independiente"
    manifest.metadatos = MetadatosDocumento(
        extension="pdf",
        tamano_bytes=len(contenido),
        creado_en=datetime.now(timezone.utc),
        cantidad_paginas=3,
        cantidad_tokens=60,
        huella_config_parser="huella",
    )
    manifest.cobertura = CoberturaDocumento(
        paginas_procesadas=[1], paginas_omitidas=[2, 3], paginas_visuales_pendientes=[2, 3], detalle="Visión pendiente"
    )
    manifest.admision_confirmada = True
    mock_storage.upload(clave_original("ws", "doc"), contenido)
    mock_storage.upload(clave_documento("ws", "doc"), manifest.model_dump_json())
    restaurada = RegistroOperativo(tmp_path / "restaurada.db")
    try:
        assert restaurar_registro(restaurada, mock_storage) == 1
        fila = restaurada.obtener_documento("doc")
        assert fila["titulo"] == "Título independiente" and fila["estado"] == "processing"
        assert json.loads(fila["cobertura_json"])["paginas_visuales_pendientes"] == [2, 3]
        assert json.loads(fila["metadatos_json"])["huella_config_parser"] == "huella"
    finally:
        restaurada.cerrar()
        db.cerrar()


def test_recuperacion_no_restaura_admision_no_confirmada(tmp_path, mock_storage):
    db = RegistroOperativo(tmp_path / "original.db")
    db.crear_workspace("ws", "codigo", 30)
    sincronizar_manifiesto(db, mock_storage, "ws")
    manifest = ManifiestoDocumento(
        workspace_id="ws", document_id="doc", source_name="a.txt", hash_sha256="a" * 64, admision_confirmada=False
    )
    mock_storage.upload(clave_documento("ws", "doc"), manifest.model_dump_json())
    restaurada = RegistroOperativo(tmp_path / "restaurada.db")
    try:
        restaurar_registro(restaurada, mock_storage)
        assert restaurada.obtener_documento("doc") is None
    finally:
        db.cerrar()
        restaurada.cerrar()


def test_schemas_rechazan_campos_extra_y_cobertura_imposible():
    with pytest.raises(ValidationError):
        UploadRequest(documento_titulo="Título", documento_contenido="texto", workspace_id="ajeno")
    for kwargs in (
        {"paginas_procesadas": [1, 1]},
        {"paginas_procesadas": [0]},
        {"paginas_procesadas": [1], "paginas_omitidas": [1]},
    ):
        with pytest.raises(ValidationError):
            CoberturaDocumento(**kwargs)


def test_dos_intenciones_concurrentes_reservan_un_solo_lugar(entorno, monkeypatch):
    db, storage, _ = entorno
    iniciado, liberar = threading.Event(), threading.Event()
    original = storage.upload

    def upload(*args, **kwargs):
        iniciado.set()
        assert liberar.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(storage, "upload", upload)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pendiente = pool.submit(admitir, servicio(entorno))
        try:
            assert iniciado.wait(1)
            with pytest.raises(ErrorAplicacion) as exc:
                admitir(servicio(entorno), clave="otra")
            assert exc.value.error.code == ErrorCode.INVALID_STATE
            assert db._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 1
            assert db._conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 1
        finally:
            liberar.set()
        pendiente.result(timeout=3)


def test_cola_llena_despues_de_persistir_conserva_limpieza_durable(entorno, monkeypatch):
    db, storage, gestor = entorno

    def rechazar(*args, **kwargs):
        raise ColaLlenaError(5, 5)

    monkeypatch.setattr(gestor, "enqueue", rechazar)
    with pytest.raises(ErrorAplicacion) as exc:
        admitir(servicio(entorno))
    assert exc.value.error.code == ErrorCode.QUEUE_FULL
    pendiente = db.listar_limpiezas_documentales()[0]
    manifest = ManifiestoDocumento.model_validate_json(storage.get(clave_documento("ws_a", pendiente["document_id"])))
    assert manifest.admision_confirmada is False
    assert db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_fallo_sqlite_despues_de_escrituras_confirmadas_conserva_intencion(entorno, monkeypatch):
    db, _, _ = entorno
    ejecuciones = []

    def fallar(*args, **kwargs):
        raise sqlite3.OperationalError("fallo de disco")

    monkeypatch.setattr(db, "confirmar_admision_documental", fallar)
    with pytest.raises(sqlite3.OperationalError):
        admitir(servicio(entorno, lambda ctx, manifest: ejecuciones.append(manifest.document_id)))
    assert len(db.listar_limpiezas_documentales()) == 1 and ejecuciones == []
    assert db._conn.execute("SELECT estado FROM idempotency_keys").fetchone()[0] == "en_curso"


def test_recibo_idempotente_y_metadata_sobreviven_reinicio(tmp_path, mock_storage):
    ruta = tmp_path / "persistente.db"
    db = RegistroOperativo(ruta)
    db.crear_workspace("ws", "codigo", 30)
    db.crear_sesion("token", "ws", 24)
    gestor = GestorTrabajos(db)
    svc = ServicioAdmisionDocumental(
        db, mock_storage, gestor, lambda ctx, manifest: {"document_id": manifest.document_id}
    )
    recibo = admitir(svc, token="token")
    esperar(db, recibo.job_id, "completed")
    gestor.detener()
    db.cerrar()
    db = RegistroOperativo(ruta)
    gestor = GestorTrabajos(db)
    try:
        svc = ServicioAdmisionDocumental(
            db, mock_storage, gestor, lambda ctx, manifest: pytest.fail("No debe reingestar")
        )
        repetida = admitir(svc, token="token")
        assert repetida.reutilizada and repetida.document_id == recibo.document_id and repetida.job_id == recibo.job_id
        assert db.obtener_job(recibo.job_id)["status"] == "completed"
        assert json.loads(db.obtener_documento(recibo.document_id)["metadatos_json"])["extension"] == "txt"
    finally:
        gestor.detener()
        db.cerrar()


def test_metadata_visual_persistida_y_completed_processing(entorno, crear_pdf):
    db, _, _ = entorno
    metadata_guardada = []

    def procesar(ctx, manifest):
        metadata = manifest.metadatos.model_copy(update={"cantidad_paginas": 2, "cantidad_tokens": 60})
        cobertura = CoberturaDocumento(paginas_procesadas=[1], paginas_omitidas=[2], paginas_visuales_pendientes=[2])
        db.guardar_metadata_documental("ws_a", manifest.document_id, metadatos=metadata, cobertura=cobertura)
        metadata_guardada.append(metadata)
        return {"document_id": manifest.document_id, "requiere_vision": True}

    recibo = servicio(entorno, procesar).admitir_archivo(
        "token_a", crear_pdf("visual.pdf", paginas=2).read_bytes(), EntradaArchivoDocumento(source_name="visual.pdf")
    )
    esperar(db, recibo.job_id, "completed")
    documento = db.obtener_documento(recibo.document_id)
    assert documento["estado"] == "processing"
    cobertura = CoberturaDocumento.model_validate_json(documento["cobertura_json"])
    assert cobertura.paginas_visuales_pendientes == [2]
    with pytest.raises(ValueError):
        db.guardar_metadata_documental("ws_b", recibo.document_id, metadatos=metadata_guardada[0], cobertura=cobertura)
    db.agendar_borrado("document", recibo.document_id, "ws_a")
    with pytest.raises(ValueError):
        db.guardar_metadata_documental("ws_a", recibo.document_id, metadatos=metadata_guardada[0], cobertura=cobertura)


def test_manifest_no_admite_ready_con_vision_pendiente():
    with pytest.raises(ValidationError, match="visión pendiente"):
        ManifiestoDocumento(
            workspace_id="ws",
            document_id="doc",
            source_name="a.pdf",
            hash_sha256="a" * 64,
            estado=DocumentStatus.READY,
            cobertura=CoberturaDocumento(paginas_visuales_pendientes=[1]),
        )


def test_documento_fallido_no_publica_completed(entorno):
    db, _, _ = entorno

    def procesar(ctx, manifest):
        db.actualizar_documento(manifest.document_id, DocumentStatus.FAILED, "falló extracción")
        return {"document_id": manifest.document_id}

    recibo = admitir(servicio(entorno, procesar))
    fila = esperar(db, recibo.job_id, "failed")
    assert fila["resultado"] is None and fila["error_code"] == "INVALID_STATE"


def test_reinicio_de_job_queued_vinculado_marca_documento_failed(tmp_path):
    ruta = tmp_path / "reinicio.db"
    db = RegistroOperativo(ruta)
    db.crear_workspace("ws", "codigo", 30)
    db.registrar_documento("doc", "ws", "Título", DocumentStatus.PROCESSING)
    db.encolar_job("job", "ws", "ingestion", document_id="doc")
    db.cerrar()
    db = RegistroOperativo(ruta)
    gestor = GestorTrabajos(db)
    try:
        assert db.obtener_job("job")["error_code"] == "INTERRUPTED"
        assert db.obtener_documento("doc")["estado"] == "failed"
    finally:
        gestor.detener()
        db.cerrar()


async def procesador_async(ctx, manifest):
    return {"document_id": manifest.document_id}


@pytest.mark.parametrize("procesar", [None, object(), procesador_async])
def test_admision_exige_procesador_sincrono_explicito(entorno, procesar):
    with pytest.raises(TypeError, match="síncrono"):
        ServicioAdmisionDocumental(*entorno, procesar)
    assert entorno[0]._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_duplicado_agota_espera_sin_aceptar_cancelar_ni_repetir_io(entorno, monkeypatch):
    db, storage, _ = entorno
    iniciado, liberar = threading.Event(), threading.Event()
    original = storage.upload
    escrituras = []

    def upload(*args, **kwargs):
        escrituras.append(args[0])
        if args[0].endswith("/original"):
            assert not db._conn.in_transaction
            iniciado.set()
            assert liberar.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(storage, "upload", upload)
    with ThreadPoolExecutor(max_workers=1) as pool:
        primera = pool.submit(admitir, servicio(entorno))
        try:
            assert iniciado.wait(1)
            principio = time.monotonic()
            with pytest.raises(AdmisionNoConfirmadaError) as exc:
                admitir(servicio(entorno, espera_admision_segundos=0.03))
            assert time.monotonic() - principio < 0.5
            reservada = db._conn.execute("SELECT * FROM document_admissions").fetchone()
            assert reservada["estado"] == "en_curso"
            assert (exc.value.document_id, exc.value.job_id) == (reservada["document_id"], reservada["job_id"])
            assert db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
            assert len(escrituras) == 1
            assert not primera.done()
        finally:
            liberar.set()
        aceptada = primera.result(timeout=3)
    assert aceptada.document_id == exc.value.document_id
    assert esperar(db, aceptada.job_id, "completed")["document_id"] == aceptada.document_id
    assert len(escrituras) == 4


def test_duplicado_observa_fallo_del_propietario_sin_aceptar(entorno, monkeypatch):
    db, storage, _ = entorno
    iniciado, liberar, observando = threading.Event(), threading.Event(), threading.Event()
    escrituras, respuestas = [], []
    obtener = db.obtener_admision_documental

    def upload(*args, **kwargs):
        escrituras.append(args[0])
        iniciado.set()
        assert liberar.wait(3)
        raise StorageUnavailable("secreto-original")

    def observar(*args):
        fila = obtener(*args)
        if iniciado.is_set():
            observando.set()
        return fila

    monkeypatch.setattr(storage, "upload", upload)
    monkeypatch.setattr(db, "obtener_admision_documental", observar)
    with ThreadPoolExecutor(max_workers=2) as pool:
        primera = pool.submit(admitir, servicio(entorno))
        try:
            assert iniciado.wait(1)
            repetida = pool.submit(admitir, servicio(entorno))
            assert observando.wait(1)
            assert not repetida.done()
        finally:
            liberar.set()
        for futura in (primera, repetida):
            with pytest.raises(ErrorAplicacion) as exc:
                futura.result(timeout=3)
            assert exc.value.error.code == ErrorCode.STORAGE_UNAVAILABLE
            assert "secreto-original" not in exc.value.error.model_dump_json()
            respuestas.append(exc.value.error.model_dump())
    assert respuestas[0] == respuestas[1]
    assert len(escrituras) == 1
    assert len(db.listar_limpiezas_documentales()) == 1
    assert db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_duplicado_no_acepta_commit_sqlite_sin_confirmacion_final_storage(entorno, monkeypatch):
    db, storage, _ = entorno
    iniciado, liberar = threading.Event(), threading.Event()
    original = storage.upload
    escrituras = []

    def upload(key, content, **kwargs):
        escrituras.append(key)
        if key.endswith("/manifest.json") and json.loads(content).get("admision_sqlite_confirmada") is True:
            iniciado.set()
            assert liberar.wait(3)
        return original(key, content, **kwargs)

    monkeypatch.setattr(storage, "upload", upload)
    with ThreadPoolExecutor(max_workers=1) as pool:
        primera = pool.submit(admitir, servicio(entorno))
        try:
            assert iniciado.wait(1)
            fila = db._conn.execute("SELECT * FROM document_admissions").fetchone()
            assert fila["estado"] == "encolada"
            manifest = ManifiestoDocumento.model_validate_json(
                storage.get(clave_documento("ws_a", fila["document_id"]))
            )
            assert manifest.admision_confirmada and manifest.admision_sqlite_confirmada is False
            with pytest.raises(AdmisionNoConfirmadaError):
                admitir(servicio(entorno, espera_admision_segundos=0.03))
            assert len(escrituras) == 4
            assert db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        finally:
            liberar.set()
        aceptada = primera.result(timeout=3)
    repetida = admitir(servicio(entorno))
    assert repetida.document_id == aceptada.document_id and repetida.admision == "encolada"


@pytest.mark.parametrize("falla_compensacion", [False, True])
def test_flag_oci_sin_commit_sqlite_no_reaparece_con_base_reconstruida(
    entorno, monkeypatch, tmp_path, falla_compensacion, caplog
):
    db, storage, _ = entorno
    ejecuciones = []
    fallo = sqlite3.OperationalError("diagnostico-original")

    def fallar_confirmacion(*args):
        raise fallo

    def fallar_compensacion(*args, **kwargs):
        raise sqlite3.OperationalError("secreto-compensacion")

    monkeypatch.setattr(db, "confirmar_admision_documental", fallar_confirmacion)
    if falla_compensacion:
        monkeypatch.setattr(db, "fallar_admision_documental", fallar_compensacion)
    with pytest.raises(sqlite3.OperationalError) as exc:
        admitir(servicio(entorno, lambda ctx, manifest: ejecuciones.append(manifest.document_id)))
    assert exc.value is fallo
    fila = db._conn.execute("SELECT * FROM document_admissions").fetchone()
    manifest = ManifiestoDocumento.model_validate_json(storage.get(clave_documento("ws_a", fila["document_id"])))
    assert manifest.admision_confirmada is True
    assert manifest.admision_sqlite_confirmada is False and not manifest.admision_recuperable
    assert ejecuciones == []
    if falla_compensacion:
        assert db.listar_limpiezas_documentales() == []
        assert "limpieza_durable_no_confirmada" in caplog.text
        assert "secreto-compensacion" not in caplog.text
    else:
        assert len(db.listar_limpiezas_documentales()) == 1
        assert fila["estado"] == "fallida"
    reconstruida = RegistroOperativo(tmp_path / "sin-registro-previo.db")
    try:
        assert restaurar_registro(reconstruida, storage) == 2
        assert reconstruida.obtener_documento(fila["document_id"]) is None
        assert reconstruida._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0
    finally:
        reconstruida.cerrar()


def test_admision_confirmada_se_recupera_con_base_nueva(entorno, tmp_path):
    db, storage, _ = entorno
    recibo = admitir(servicio(entorno))
    esperar(db, recibo.job_id, "completed")
    manifest = ManifiestoDocumento.model_validate_json(storage.get(clave_documento("ws_a", recibo.document_id)))
    assert manifest.admision_sqlite_confirmada is True and manifest.admision_recuperable
    reconstruida = RegistroOperativo(tmp_path / "nueva.db")
    try:
        assert restaurar_registro(reconstruida, storage) == 2
        documento = reconstruida.obtener_documento(recibo.document_id)
        assert documento["estado"] == "processing" and documento["hash"] == manifest.hash_sha256
    finally:
        reconstruida.cerrar()


@pytest.mark.parametrize("efecto_aplicado", [False, True])
def test_respuesta_perdida_del_commit_final_conserva_incertidumbre_sin_falso_fracaso(
    entorno, monkeypatch, tmp_path, efecto_aplicado, caplog
):
    db, storage, _ = entorno
    original = storage.upload
    ejecuciones = []

    def upload(key, content, **kwargs):
        if key.endswith("/manifest.json") and json.loads(content).get("admision_sqlite_confirmada") is True:
            if efecto_aplicado:
                original(key, content, **kwargs)
            raise StorageUnavailable("secreto-respuesta-perdida")
        return original(key, content, **kwargs)

    monkeypatch.setattr(storage, "upload", upload)
    with pytest.raises(ErrorAplicacion) as exc:
        admitir(servicio(entorno, lambda ctx, manifest: ejecuciones.append(manifest.document_id)))
    assert exc.value.error.code == ErrorCode.STORAGE_UNAVAILABLE
    fila = db._conn.execute("SELECT * FROM document_admissions").fetchone()
    assert fila["estado"] == "encolada" and ejecuciones == []
    assert db.listar_limpiezas_documentales() == []
    assert "indeterminada" in caplog.text and "secreto-respuesta-perdida" not in caplog.text
    svc = servicio(entorno, espera_admision_segundos=0.03)
    if efecto_aplicado:
        assert admitir(svc).document_id == fila["document_id"]
    else:
        with pytest.raises(AdmisionNoConfirmadaError):
            admitir(svc)
    reconstruida = RegistroOperativo(tmp_path / "recuperacion.db")
    try:
        assert restaurar_registro(reconstruida, storage) == 2
        assert (reconstruida.obtener_documento(fila["document_id"]) is not None) is efecto_aplicado
    finally:
        reconstruida.cerrar()


@pytest.mark.parametrize("fase_secundaria", ["fallar", "cancelar", "ambas"])
def test_compensacion_secundaria_preserva_original_y_no_filtra_secretos(entorno, monkeypatch, caplog, fase_secundaria):
    db, storage, gestor = entorno
    original = StorageUnavailable("secreto-original")
    cancelar = gestor.cancelar
    cancelaciones = []

    def upload(*args, **kwargs):
        raise original

    def fallar(*args, **kwargs):
        raise sqlite3.OperationalError("secreto-sqlite")

    def cancelar_con_fallo(job_id):
        cancelaciones.append(job_id)
        if fase_secundaria in ("cancelar", "ambas"):
            raise RuntimeError("secreto-cancelacion")
        return cancelar(job_id)

    monkeypatch.setattr(storage, "upload", upload)
    if fase_secundaria in ("fallar", "ambas"):
        monkeypatch.setattr(db, "fallar_admision_documental", fallar)
    monkeypatch.setattr(gestor, "cancelar", cancelar_con_fallo)
    with pytest.raises(ErrorAplicacion) as exc:
        admitir(servicio(entorno))
    assert exc.value.__cause__ is original and exc.value.error.code == ErrorCode.STORAGE_UNAVAILABLE
    assert cancelaciones  # Se intenta aun si falló registrar la limpieza.
    assert original.__notes__
    for texto in (caplog.text, exc.value.error.model_dump_json(), " ".join(original.__notes__)):
        assert "secreto-" not in texto
    if fase_secundaria in ("fallar", "ambas"):
        assert db.listar_limpiezas_documentales() == []
        assert "limpieza_durable_no_confirmada" in caplog.text
    else:
        assert len(db.listar_limpiezas_documentales()) == 1
    if fase_secundaria in ("cancelar", "ambas"):
        assert "cancelacion_no_confirmada" in caplog.text


@pytest.mark.parametrize("estado", ["en_curso", "fallida"])
def test_recibo_no_admite_reserva_ni_fracaso(estado):
    with pytest.raises(ValidationError):
        AdmisionDocumento(document_id="doc", job_id="job", estado=DocumentStatus.PROCESSING, admision=estado)


def test_replay_de_aceptacion_no_cambia_por_estado_posterior_del_job(entorno):
    db, _, _ = entorno

    def fallar(ctx, manifest):
        raise RuntimeError("procesamiento fallido")

    aceptada = admitir(servicio(entorno, fallar))
    esperar(db, aceptada.job_id, "failed")
    repetida = admitir(servicio(entorno))
    assert repetida.model_dump(exclude={"reutilizada"}) == aceptada.model_dump(exclude={"reutilizada"})


def test_fallos_secundarios_con_job_encolado_no_abren_procesador(entorno, monkeypatch, caplog):
    db, _, gestor = entorno
    original = sqlite3.OperationalError("diagnostico-original")
    ejecuciones, cancelaciones = [], []

    def fallar_confirmacion(*args):
        raise original

    def fallar_compensacion(*args, **kwargs):
        raise sqlite3.OperationalError("secreto-limpieza")

    def fallar_cancelacion(job_id):
        cancelaciones.append(job_id)
        raise RuntimeError("secreto-cancelacion")

    monkeypatch.setattr(db, "confirmar_admision_documental", fallar_confirmacion)
    monkeypatch.setattr(db, "fallar_admision_documental", fallar_compensacion)
    monkeypatch.setattr(gestor, "cancelar", fallar_cancelacion)
    with pytest.raises(sqlite3.OperationalError) as exc:
        admitir(servicio(entorno, lambda ctx, manifest: ejecuciones.append(manifest.document_id)))
    assert exc.value is original
    fila = db._conn.execute("SELECT * FROM document_admissions").fetchone()
    assert cancelaciones == [fila["job_id"]]
    assert esperar(db, fila["job_id"], "failed")["resultado"] is None
    assert ejecuciones == [] and db.listar_limpiezas_documentales() == []
    assert "limpieza_durable_no_confirmada" in caplog.text and "cancelacion_no_confirmada" in caplog.text
    assert "secreto-" not in caplog.text


def test_duplicado_revalida_sesion_mientras_espera(entorno, monkeypatch):
    db, storage, _ = entorno
    iniciado, liberar, observando = threading.Event(), threading.Event(), threading.Event()
    original, obtener = storage.upload, db.obtener_admision_documental

    def upload(*args, **kwargs):
        if args[0].endswith("/original"):
            iniciado.set()
            assert liberar.wait(3)
        return original(*args, **kwargs)

    def observar(*args):
        fila = obtener(*args)
        if iniciado.is_set():
            observando.set()
        return fila

    monkeypatch.setattr(storage, "upload", upload)
    monkeypatch.setattr(db, "obtener_admision_documental", observar)
    with ThreadPoolExecutor(max_workers=2) as pool:
        primera = pool.submit(admitir, servicio(entorno))
        try:
            assert iniciado.wait(1)
            repetida = pool.submit(admitir, servicio(entorno))
            assert observando.wait(1)
            db.revocar_sesion("token_a")
            with pytest.raises(ErrorAplicacion) as exc:
                repetida.result(timeout=1)
            assert exc.value.error.code == ErrorCode.SESSION_INVALID
        finally:
            liberar.set()
        with pytest.raises(ErrorAplicacion) as exc:
            primera.result(timeout=3)
        assert exc.value.error.code == ErrorCode.SESSION_INVALID
    assert db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0


def test_error_idempotente_seguro_sobrevive_reinicio(tmp_path, mock_storage, monkeypatch):
    ruta = tmp_path / "fallida.db"
    db = RegistroOperativo(ruta)
    db.crear_workspace("ws_a", "codigo", 30)
    db.crear_sesion("token_a", "ws_a", 24)
    gestor = GestorTrabajos(db)
    escrituras = []

    def upload(*args, **kwargs):
        escrituras.append(args[0])
        raise StorageUnavailable("secreto-proveedor")

    monkeypatch.setattr(mock_storage, "upload", upload)
    try:
        with pytest.raises(ErrorAplicacion) as primera:
            admitir(servicio((db, mock_storage, gestor)))
        cache = db._conn.execute("SELECT estado, respuesta_json FROM idempotency_keys").fetchone()
        assert cache["estado"] == "en_curso" and "secreto-proveedor" not in cache["respuesta_json"]
    finally:
        gestor.detener()
        db.cerrar()
    db = RegistroOperativo(ruta)
    gestor = GestorTrabajos(db)
    try:
        with pytest.raises(ErrorAplicacion) as repetida:
            admitir(servicio((db, mock_storage, gestor)))
        assert repetida.value.error == primera.value.error
        assert len(escrituras) == 1
        assert len(db.listar_limpiezas_documentales()) == 1
        assert db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    finally:
        gestor.detener()
        db.cerrar()
