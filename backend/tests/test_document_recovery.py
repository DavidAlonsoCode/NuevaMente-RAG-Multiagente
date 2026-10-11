"""H02: SQLite nueva, OCI simulado, worker común y Chroma físico temporal."""

import hashlib
import io
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from app.api import security
from app.config import Configuracion
from app.core.rag.chunker import trocear
from app.core.rag.embeddings import GeminiEmbeddings
from app.core.rag.parser import parsear_archivo
from app.core.rag.rebuild import ManifiestoDocumento
from app.core.rag.vectorstore import IndiceInconsistenteError, VectorStoreChroma, _filtro
from app.documents.ingestion import ProcesadorIngestionDocumental
from app.jobs.manager import ContextoEjecucion, CuotasModelo, CuotasProveedor, GestorTrabajos
from app.jobs.store import RegistroOperativo
from app.main import crear_app
from app.schemas.documents import MetadatosDocumento
from app.schemas.enums import DocumentStatus
from app.session.manager import SessionManager
from app.session.manifests import sincronizar_pendientes
from app.session.recovery import restaurar_registro
from app.storage.object_keys import clave_documento, clave_export, clave_original, clave_output, clave_progress
from app.storage.provider import StorageConflict, StorageNotFound, StorageUnavailable
from doubles.gemini import DobleGemini
from fastapi.testclient import TestClient
from reportlab.pdfgen import canvas
from test_workspace_recovery import paquete

pytestmark = pytest.mark.integration_mock
MODELO = "gemini-embedding-2"
TEXTO = ("# Redes\n\nLas redes VCN conectan subredes privadas y conservan aislamiento y trazabilidad.\n" * 15).encode()


def hasta(condicion):
    limite = time.monotonic() + 10
    while time.monotonic() < limite:
        if valor := condicion():
            return valor
        time.sleep(0.005)
    pytest.fail("La recuperación no alcanzó el estado esperado")


@pytest.fixture
def pila(tmp_path, mock_storage, aislar_estado_seguridad):
    db = RegistroOperativo(tmp_path / "perdida" / "operativo.db")
    manager = SessionManager(db, mock_storage, 30, 24)
    ws = manager.crear_espacio()
    otro = manager.crear_espacio()
    yield SimpleNamespace(ruta=tmp_path / "nuevo", storage=mock_storage, origen=db, ws=ws, otro=otro)
    db.cerrar()


def fuente(p, doc="doc_recuperado", *, estado=DocumentStatus.READY, contenido=TEXTO, nombre="redes.md", ws=None):
    workspace = (ws or p.ws).workspace_id
    mf = ManifiestoDocumento(
        workspace_id=workspace,
        document_id=doc,
        source_name=nombre,
        titulo="Redes recuperadas",
        hash_sha256=hashlib.sha256(contenido).hexdigest(),
        estado=estado,
        admision_confirmada=True,
        admision_sqlite_confirmada=True,
        metadatos=MetadatosDocumento(
            extension=nombre.rsplit(".", 1)[-1],
            tamano_bytes=len(contenido),
            creado_en=datetime(2026, 10, 1, tzinfo=timezone.utc),
        ),
        detalle_error="El procesamiento original falló." if estado == DocumentStatus.FAILED else None,
    )
    p.storage.upload(clave_original(workspace, doc), contenido)
    p.storage.upload(clave_documento(workspace, doc), mf.model_dump_json())
    return mf


@contextmanager
def abrir(p, *, cuotas=True, doble=None, limite_diario=1000):
    ajustes = Configuracion(
        app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(p.ruta), embedding_dimensions=16
    )
    doble = doble or DobleGemini(16)
    proveedor = CuotasProveedor({MODELO: CuotasModelo(1000, 1000000, limite_diario)}) if cuotas else None
    app = crear_app(ajustes, proveedor_embeddings=doble if cuotas else None, cuotas_proveedor=proveedor)
    app.state.storage_provider = p.storage
    with TestClient(app) as cliente:
        respuesta = cliente.post("/api/sessions/recover", json={"recovery_code": p.ws.recovery_code})
        assert respuesta.status_code == 201, respuesta.text
        token = respuesta.json()["token"]
        yield SimpleNamespace(
            app=app,
            db=app.state.db,
            cliente=cliente,
            headers={"Authorization": f"Bearer {token}"},
            token=token,
            doble=doble,
            efectos=app.state.documents_effects,
            gestor=app.state.jobs_manager,
            indice=app.state.vectorstore,
        )


def terminal(e, doc="doc_recuperado"):
    return hasta(
        lambda: (
            (d := e.db.obtener_documento(doc))
            and d["recuperacion_estado"] in ("confirmada", "fallida")
            and e.gestor._observadores_en_vuelo == 0
            and d["recuperacion_job_id"] not in e.gestor._ejecutables
            and d
        )
    )


def assert_fuente(e, mf, status=200):
    if status == 200:
        chunks = e.app.state.documents_query.chunks(e.token, mf.document_id)
        chunk_id = chunks[0].chunk_id
    else:
        chunk_id = "incompleto"
    respuesta = e.cliente.get(f"/api/documents/{mf.document_id}/sources/{chunk_id}", headers=e.headers)
    assert respuesta.status_code == status, respuesta.text
    if status == 200:
        assert respuesta.json()["document_id"] == mf.document_id
        assert "VCN" in respuesta.json()["texto"]


def test_pila_aisla_fallos_de_recuperacion_previos_y_limpia_al_salir(request):
    registros = (
        security._peticiones_globales,
        security._peticiones_ip,
        security._intentos_fallidos_ip,
        security._intentos_en_curso,
    )

    def verificar_limpieza():
        assert all(not registro for registro in registros)

    # Se ejecutará después del teardown de pila, solicitado más abajo.
    request.addfinalizer(verificar_limpieza)
    for _ in range(5):
        security.registrar_intento_fallido("testclient")
    assert len(security._intentos_fallidos_ip["testclient"]) == 5
    assert not security._intentos_en_curso

    p = request.getfixturevalue("pila")
    with abrir(p, cuotas=False) as e:
        assert e.db.obtener_workspace(p.ws.workspace_id)
        for _ in range(5):
            respuesta = e.cliente.post("/api/sessions/recover", json={"recovery_code": "incorrecto"})
            assert respuesta.status_code == 401
        respuesta = e.cliente.post("/api/sessions/recover", json={"recovery_code": "incorrecto"})
        assert respuesta.status_code == 429
        assert respuesta.json()["error"]["code"] == "RECOVERY_LOCKED"


@pytest.mark.parametrize("estado", [DocumentStatus.READY, DocumentStatus.PROCESSING])
def test_restaura_identidad_y_http_sin_fabricar_historia(pila, estado):
    mf = fuente(pila, estado=estado)
    with abrir(pila) as e:
        doc = terminal(e)
        assert doc["estado"] == "ready" and doc["hash"] == mf.hash_sha256
        assert doc["creado_en"] == mf.metadatos.creado_en.isoformat()
        assert doc["recuperacion_intentos"] == 1
        assert doc["recuperacion_job_id"].startswith("job_recovery_")
        assert e.db._conn.execute("SELECT COUNT(*) FROM document_admissions").fetchone()[0] == 0
        assert e.db._conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 0
        jobs = e.db._conn.execute("SELECT * FROM jobs").fetchall()
        assert len(jobs) == 1 and jobs[0]["document_id"] == mf.document_id
        assert [v["status"] for v in e.db.eventos_desde_job(jobs[0]["job_id"])] == ["queued", "running", "completed"]
        assert e.cliente.get("/api/documents", headers=e.headers).json()["items"][0]["document_id"] == mf.document_id
        assert e.cliente.get(f"/api/documents/{mf.document_id}", headers=e.headers).json()["status"] == "ready"
        assert e.cliente.get("/api/documents", headers={"Authorization": f"Bearer {pila.ws.token}"}).status_code == 401
        assert_fuente(e, mf)
        remoto = ManifiestoDocumento.model_validate_json(
            pila.storage.get(clave_documento(mf.workspace_id, mf.document_id))
        )
        assert json.loads(doc["metadatos_json"]) == remoto.metadatos.model_dump(mode="json")
        assert json.loads(doc["cobertura_json"]) == remoto.cobertura.model_dump(mode="json")
        e.efectos.reconciliar()
        assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_upload_confirmado_se_recupera_desde_oci_en_otra_base(pila):
    inicial = Configuracion(
        app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(pila.ruta / "inicial"), embedding_dimensions=16
    )
    app = crear_app(
        inicial,
        proveedor_embeddings=DobleGemini(16),
        cuotas_proveedor=CuotasProveedor({MODELO: CuotasModelo(1000, 1000000, 1000)}),
    )
    app.state.storage_provider = pila.storage
    with TestClient(app) as cliente:
        ws = cliente.post("/api/workspaces").json()
        pila.ws = SimpleNamespace(**ws)
        headers = {"Authorization": f"Bearer {ws['token']}", "Idempotency-Key": "upload-original"}
        recibo = cliente.post(
            "/api/documents/upload",
            json={"documento_titulo": "Redes", "documento_contenido": TEXTO.decode()},
            headers=headers,
        ).json()
        hasta(lambda: app.state.db.obtener_job(recibo["job_id"])["status"] == "completed")
    with abrir(pila) as e:
        doc = terminal(e, recibo["document_id"])
        assert doc["recuperacion_job_id"] != recibo["job_id"]
        assert e.db.obtener_job(recibo["job_id"]) is None
        assert e.db._conn.execute("SELECT COUNT(*) FROM document_admissions").fetchone()[0] == 0
        assert e.db._conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 0
        assert_fuente(
            e,
            ManifiestoDocumento.model_validate_json(
                pila.storage.get(clave_documento(ws["workspace_id"], doc["document_id"]))
            ),
        )


def test_procedencia_sola_autoriza_detalle_pero_no_fuentes(pila):
    mf = fuente(pila)
    with abrir(pila, cuotas=False) as e:
        doc = e.db.obtener_documento(mf.document_id)
        assert doc["recuperacion_estado"] == "pendiente" and doc["estado"] == "processing"
        assert doc["recuperacion_job_id"] is None
        assert e.cliente.get(f"/api/documents/{mf.document_id}", headers=e.headers).status_code == 200
        assert_fuente(e, mf, 409)
        e.db.actualizar_documento(mf.document_id, DocumentStatus.READY)
        assert_fuente(e, mf, 409)


@pytest.mark.parametrize("tipo", ["compatible", "incompleto", "otra_preparacion"])
def test_indice_conservado_se_verifica_reutiliza_o_repara(pila, tipo):
    mf = fuente(pila)
    doble = DobleGemini(16)
    embeddings = GeminiEmbeddings(doble, modelo=MODELO, dimensiones=16)
    if tipo == "otra_preparacion":

        class EmbeddingsAnteriores(GeminiEmbeddings):
            @property
            def preparation_version(self):
                return "anterior"

        embeddings = EmbeddingsAnteriores(doble, modelo=MODELO, dimensiones=16)
    indice = VectorStoreChroma(pila.ruta / "chroma", embeddings)
    ctx = ContextoEjecucion(
        "preparacion",
        mf.workspace_id,
        "ingestion",
        time.monotonic() + 30,
        cuotas=CuotasProveedor({MODELO: CuotasModelo(1000, 1000000, 1000)}),
    )
    chunks = [
        c.como_chunk_interno()
        for c in trocear(
            parsear_archivo(TEXTO, mf.source_name, mf.parser),
            workspace_id=mf.workspace_id,
            document_id=mf.document_id,
            source_name=mf.source_name,
            config=mf.chunker,
        )
    ]
    indice.indexar(mf.workspace_id, mf.document_id, chunks, contexto=ctx)
    if tipo == "incompleto":
        datos = indice._privada.get(where=_filtro(mf.workspace_id, mf.document_id))
        indice._privada.delete(ids=datos["ids"][:1])
    indice.cerrar()
    doble.reset()
    if tipo == "otra_preparacion":
        with pytest.raises(IndiceInconsistenteError):
            with abrir(pila, doble=doble):
                pytest.fail("Una colección incompatible no puede confirmar recuperación")
        db = RegistroOperativo(pila.ruta / "operativo.db")
        try:
            assert db.obtener_documento(mf.document_id)["recuperacion_estado"] == "pendiente"
            assert db.obtener_documento(mf.document_id)["estado"] == "processing"
        finally:
            db.cerrar()
        assert doble.llamadas_embeddings == 0
        return
    with abrir(pila, doble=doble) as e:
        doc = terminal(e)
        assert doc["estado"] == "ready"
        assert (doble.llamadas_embeddings == 0) == (tipo == "compatible")
        assert e.app.state.documents_query.chunks(e.token, mf.document_id) == chunks
        assert_fuente(e, mf)


@pytest.mark.parametrize("mixto", [True, False])
def test_recuperacion_pdf_conserva_vision_pendiente(pila, mixto):
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    if mixto:
        for i in range(12):
            pdf.drawString(50, 760 - i * 18, "Las redes VCN conectan subredes privadas y conservan trazabilidad.")
    pdf.line(50, 100, 210, 220)
    pdf.showPage()
    pdf.save()
    mf = fuente(pila, contenido=buffer.getvalue(), nombre="redes.pdf", estado=DocumentStatus.PROCESSING)
    with abrir(pila) as e:
        doc = terminal(e)
        assert doc["recuperacion_estado"] == "confirmada" and doc["estado"] == "processing"
        cobertura = json.loads(doc["cobertura_json"])
        assert cobertura["paginas_visuales_pendientes"] == [1]
        assert cobertura["paginas_procesadas"] == ([1] if mixto else [])
        assert json.loads(doc["metadatos_json"])["cantidad_paginas"] == 1
        assert e.db.obtener_job(doc["recuperacion_job_id"])["status"] == "completed"
        assert_fuente(e, mf, 409)
        e.efectos.reconciliar()
        assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_failed_historico_restaura_diagnostico_sin_trabajo(pila):
    mf = fuente(pila, estado=DocumentStatus.FAILED)
    with abrir(pila) as e:
        doc = e.db.obtener_documento(mf.document_id)
        assert doc["estado"] == "failed" and doc["detalle_error"] == mf.detalle_error
        assert doc["recuperacion_intentos"] == 0 and doc["recuperacion_job_id"] is None
        assert e.doble.llamadas_embeddings == 0
        assert e.cliente.get(f"/api/documents/{mf.document_id}", headers=e.headers).status_code == 200
        assert_fuente(e, mf, 409)


@pytest.mark.parametrize("caso", ["true_false", "true_null", "null_true", "ausente", "hash", "metadata"])
def test_evidencia_incompleta_no_autoriza(pila, caso):
    mf = fuente(pila)
    if caso in ("true_false", "true_null", "null_true"):
        cambios = (
            {"admision_sqlite_confirmada": False if caso == "true_false" else None}
            if caso != "null_true"
            else {"admision_confirmada": None}
        )
        pila.storage.upload(
            clave_documento(mf.workspace_id, mf.document_id), mf.model_copy(update=cambios).model_dump_json()
        )
    elif caso == "ausente":
        pila.storage.delete(clave_original(mf.workspace_id, mf.document_id))
    elif caso == "hash":
        pila.storage.upload(clave_original(mf.workspace_id, mf.document_id), b"contenido diferente")
    else:
        pila.storage.upload(
            clave_documento(mf.workspace_id, mf.document_id),
            mf.model_copy(update={"metadatos": None}).model_dump_json(),
        )
    with abrir(pila) as e:
        assert e.cliente.get(f"/api/documents/{mf.document_id}", headers=e.headers).status_code == 404
        assert e.cliente.get("/api/documents", headers=e.headers).json()["items"] == []
        assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert e.doble.llamadas_embeddings == 0


@pytest.mark.parametrize("lapida", ["workspace_manifest", "document_manifest"])
def test_lapida_remota_impide_reconstruccion(pila, lapida):
    mf = fuente(pila)
    if lapida == "workspace_manifest":
        pila.origen.agendar_borrado("document", mf.document_id, mf.workspace_id)
        sincronizar_pendientes(pila.origen, pila.storage)
        pila.storage.delete(clave_documento(mf.workspace_id, mf.document_id))
    else:
        pila.storage.upload(
            clave_documento(mf.workspace_id, mf.document_id), mf.model_copy(update={"borrado": True}).model_dump_json()
        )
    with abrir(pila) as e:
        assert e.db.esta_borrado("document", mf.document_id)
        assert e.cliente.get(f"/api/documents/{mf.document_id}", headers=e.headers).status_code == 404
        assert e.doble.llamadas_embeddings == 0
        # Perder SQLite no debe abandonar una purga ya acreditada en OCI.
        with pytest.raises(StorageNotFound):
            pila.storage.get(clave_original(mf.workspace_id, mf.document_id))
        with pytest.raises(StorageNotFound):
            pila.storage.get(clave_documento(mf.workspace_id, mf.document_id))


@pytest.mark.parametrize("manifiesto_presente", [True, False])
def test_recuperacion_reanuda_purga_de_derivados_y_conserva_otros_espacios(pila, manifiesto_presente):
    retirado = fuente(pila)
    vigente = fuente(pila, doc="doc_otro", ws=pila.otro)
    salida_retirada = paquete(pila.storage, retirado.workspace_id, retirado, "gen_retirada")
    salida_vigente = paquete(pila.storage, vigente.workspace_id, vigente, "gen_vigente")
    export_retirado = clave_export(retirado.workspace_id, salida_retirada.generation_id, "pdf")
    export_vigente = clave_export(vigente.workspace_id, salida_vigente.generation_id, "pdf")
    pila.storage.upload(export_retirado, b"pdf retirado")
    pila.storage.upload(export_vigente, b"pdf vigente")
    pila.storage.upload(
        clave_progress(retirado.workspace_id),
        json.dumps(
            {
                "version": 1,
                "eventos": [{"document_id": retirado.document_id, "generation_id": salida_retirada.generation_id}],
                "sin_referencia": {"valor": 3},
            }
        ),
    )
    pila.origen.agendar_borrado("document", retirado.document_id, retirado.workspace_id)
    sincronizar_pendientes(pila.origen, pila.storage)
    if not manifiesto_presente:
        pila.storage.delete(clave_documento(retirado.workspace_id, retirado.document_id))

    with abrir(pila) as e:
        assert e.cliente.get(f"/api/documents/{retirado.document_id}", headers=e.headers).status_code == 404
        assert e.db.obtener_generacion(salida_retirada.generation_id) is None
        assert e.db.limpieza_documental_completada(retirado.workspace_id, retirado.document_id)
        for clave in (
            clave_original(retirado.workspace_id, retirado.document_id),
            clave_documento(retirado.workspace_id, retirado.document_id),
            clave_output(retirado.workspace_id, salida_retirada.generation_id),
            export_retirado,
        ):
            with pytest.raises(StorageNotFound):
                pila.storage.get(clave)
        progreso = json.loads(pila.storage.get(clave_progress(retirado.workspace_id)))
        assert progreso["eventos"] == [] and progreso["sin_referencia"] == {"valor": 3}
        assert pila.storage.get(export_vigente) == b"pdf vigente"
        assert pila.storage.get(clave_original(vigente.workspace_id, vigente.document_id)) == TEXTO
        assert pila.storage.get(clave_output(vigente.workspace_id, salida_vigente.generation_id))


def test_purga_recuperada_sin_cuotas_reintenta_al_configurar_indice(pila):
    mf = fuente(pila)
    pila.origen.agendar_borrado("document", mf.document_id, mf.workspace_id)
    sincronizar_pendientes(pila.origen, pila.storage)
    with abrir(pila, cuotas=False) as e:
        assert e.db.esta_borrado("document", mf.document_id)
        assert not e.db.limpieza_documental_completada(mf.workspace_id, mf.document_id)
        assert e.db._conn.execute("SELECT COUNT(*) FROM document_cleanup").fetchone()[0] == 1
        assert pila.storage.get(clave_original(mf.workspace_id, mf.document_id)) == TEXTO
        assert e.doble.llamadas_embeddings == 0
        e.db._conn.execute("UPDATE document_cleanup SET proximo_intento_en=NULL")
    with abrir(pila) as e:
        assert e.db.limpieza_documental_completada(mf.workspace_id, mf.document_id)
        assert e.doble.llamadas_embeddings == 0
        with pytest.raises(StorageNotFound):
            pila.storage.get(clave_original(mf.workspace_id, mf.document_id))


def test_despacho_concurrente_y_workspace_ajeno(pila, monkeypatch):
    mf = fuente(pila)
    fuente(pila, doc="doc_otro", ws=pila.otro)
    iniciada, liberar = threading.Event(), threading.Event()
    recuperar = ProcesadorIngestionDocumental.recuperar

    def bloqueada(self, ctx, doc):
        iniciada.set()
        assert liberar.wait(10)
        return recuperar(self, ctx, doc)

    monkeypatch.setattr(ProcesadorIngestionDocumental, "recuperar", bloqueada)
    with abrir(pila) as e:
        try:
            assert iniciada.wait(5)
            assert_fuente(e, mf, 409)
            with ThreadPoolExecutor(max_workers=2) as pool:
                list(pool.map(lambda _: e.efectos.reconciliar(), range(4)))
            assert (
                e.db._conn.execute("SELECT COUNT(*) FROM jobs WHERE document_id=?", (mf.document_id,)).fetchone()[0]
                == 1
            )
            assert e.cliente.get("/api/documents/doc_otro", headers=e.headers).status_code == 404
            assert e.cliente.get("/api/documents/doc_otro/sources/c", headers=e.headers).status_code == 404
        finally:
            liberar.set()
        terminal(e)
        terminal(e, "doc_otro")


@pytest.mark.parametrize("fallo", ["oci", "chroma", "embeddings"])
def test_fallo_reconstruccion_conserva_fuente_y_manifest(pila, monkeypatch, fallo):
    mf = fuente(pila)
    original = pila.storage.get(clave_original(mf.workspace_id, mf.document_id))
    mf_antes = pila.storage.get(clave_documento(mf.workspace_id, mf.document_id))
    if fallo == "oci":
        subir = pila.storage.upload

        def fallar(key, *args, **kwargs):
            if key == clave_documento(mf.workspace_id, mf.document_id):
                raise StorageUnavailable("fallo simulado")
            return subir(key, *args, **kwargs)

        monkeypatch.setattr(pila.storage, "upload", fallar)
    if fallo == "chroma":
        indexar = VectorStoreChroma.indexar

        def fallar_indice(self, *args, **kwargs):
            indexar(self, *args, **kwargs)
            raise RuntimeError("respuesta de Chroma perdida")

        monkeypatch.setattr(VectorStoreChroma, "indexar", fallar_indice)
    doble = DobleGemini(16)
    if fallo == "embeddings":
        doble.programar_error_embeddings(ValueError("fallo simulado"))
    with abrir(pila, doble=doble) as e:
        doc = terminal(e)
        assert doc["estado"] == "failed" and doc["recuperacion_intentos"] == 1
        assert pila.storage.get(clave_original(mf.workspace_id, mf.document_id)) == original
        remoto = ManifiestoDocumento.model_validate_json(
            pila.storage.get(clave_documento(mf.workspace_id, mf.document_id))
        )
        assert remoto.estado != DocumentStatus.FAILED and remoto.admision_sqlite_confirmada is True
        if fallo == "oci":
            assert pila.storage.get(clave_documento(mf.workspace_id, mf.document_id)) == mf_antes
        assert e.indice._privada.get(where=_filtro(mf.workspace_id, mf.document_id))["ids"] == []
        assert e.db.limpieza_documental_completada(mf.workspace_id, mf.document_id)
        assert_fuente(e, mf, 409)
        e.efectos.reconciliar()
        assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


def test_cuotas_compartidas_no_crean_bucle_de_reconstruccion(pila):
    texto = b"Las redes VCN conectan subredes privadas y conservan trazabilidad. " * 12
    a = fuente(pila, contenido=texto)
    b = fuente(pila, doc="doc_cuota", ws=pila.otro, contenido=texto)
    with abrir(pila, limite_diario=1) as e:
        docs = [terminal(e, a.document_id), terminal(e, b.document_id)]
        assert sorted(d["estado"] for d in docs) == ["failed", "ready"]
        fallido = next(d for d in docs if d["estado"] == "failed")
        assert e.db.obtener_job(fallido["recuperacion_job_id"])["error_code"] == "CUOTA_AGOTADA"
        assert e.doble.llamadas_embeddings == 1
        for _ in range(3):
            e.efectos.reconciliar()
        assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2
        assert e.doble.llamadas_embeddings == 1


def test_delete_concurrente_gana_y_limpia_fuente(pila, monkeypatch):
    mf = fuente(pila)
    iniciada, liberar = threading.Event(), threading.Event()
    recuperar = ProcesadorIngestionDocumental.recuperar

    def bloqueada(self, ctx, doc):
        iniciada.set()
        assert liberar.wait(10)
        return recuperar(self, ctx, doc)

    monkeypatch.setattr(ProcesadorIngestionDocumental, "recuperar", bloqueada)
    with abrir(pila) as e:
        try:
            assert iniciada.wait(5)
            respuesta = e.cliente.delete(f"/api/documents/{mf.document_id}", headers=e.headers)
            assert respuesta.status_code == 202 and respuesta.json()["cleanup_pending"] is True
            assert e.db.esta_borrado("document", mf.document_id)
            assert e.cliente.get(f"/api/documents/{mf.document_id}", headers=e.headers).status_code == 404
        finally:
            liberar.set()
        hasta(lambda: e.gestor._observadores_en_vuelo == 0 and not e.gestor._ejecutables)
        e.db._conn.execute("UPDATE document_cleanup SET proximo_intento_en=NULL")
        e.efectos.reconciliar()
        assert e.db.limpieza_documental_completada(mf.workspace_id, mf.document_id)
        with pytest.raises(StorageNotFound):
            pila.storage.get(clave_original(mf.workspace_id, mf.document_id))
        assert e.indice._privada.get(where=_filtro(mf.workspace_id, mf.document_id))["ids"] == []


def test_reserva_previa_al_despacho_sobrevive_reinicio(pila):
    mf = fuente(pila)
    with abrir(pila, cuotas=False) as e:
        reserva = e.db.reservar_reconstruccion_documental(mf.document_id, "job_reservado_real")
        assert reserva["recuperacion_estado"] == "reservada"
        assert reserva["recuperacion_intentos"] == 0
        assert e.db.obtener_job("job_reservado_real") is None
        otra = e.db.reservar_reconstruccion_documental(mf.document_id, "otra_reserva")
        assert otra["recuperacion_job_id"] == "job_reservado_real"
    with abrir(pila) as e:
        doc = terminal(e)
        assert doc["recuperacion_job_id"] == "job_reservado_real"
        assert doc["recuperacion_intentos"] == 1
        assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert_fuente(e, mf)


@pytest.mark.parametrize("interrupciones", [1, 2])
def test_terminal_sqlite_perdido_reinicio_y_limite_durable(pila, monkeypatch, interrupciones):
    mf = fuente(pila)
    job_ids = []
    for _ in range(interrupciones):
        with monkeypatch.context() as parche:
            finalizar = RegistroOperativo.finalizar_job
            huerfanos = RegistroOperativo.marcar_huerfanos_como_interrumpidos
            averiada = [False]

            def fallar_terminal(self, job_id, status, **datos):
                if datos.get("error_code") != "INTERRUPTED":
                    averiada[0] = True
                    raise sqlite3.OperationalError("SQLite temporalmente no disponible")
                return finalizar(self, job_id, status, **datos)

            def fallar_recuperacion_local(self):
                if averiada[0]:
                    raise sqlite3.OperationalError("SQLite continúa no disponible")
                return huerfanos(self)

            parche.setattr(RegistroOperativo, "finalizar_job", fallar_terminal)
            parche.setattr(RegistroOperativo, "marcar_huerfanos_como_interrumpidos", fallar_recuperacion_local)
            with abrir(pila) as e:
                hasta(lambda: not e.gestor._hilo.is_alive())
                doc = e.db.obtener_documento(mf.document_id)
                job_ids.append(doc["recuperacion_job_id"])
                assert doc["recuperacion_estado"] == "en_curso" and doc["estado"] == "processing"
                assert e.db.obtener_job(doc["recuperacion_job_id"])["status"] == "running"
                assert e.indice._privada.get(where=_filtro(mf.workspace_id, mf.document_id))["ids"]
                assert_fuente(e, mf, 409)
    with abrir(pila) as e:
        doc = terminal(e)
        assert doc["recuperacion_intentos"] == 2
        for job_id in job_ids:
            assert e.db.obtener_job(job_id)["error_code"] == "INTERRUPTED"
            assert "completed" not in [v["status"] for v in e.db.eventos_desde_job(job_id)]
        assert len(set(job_ids)) == interrupciones
        if interrupciones == 1:
            assert doc["recuperacion_job_id"] != job_ids[0] and doc["estado"] == "ready"
            assert_fuente(e, mf)
        else:
            assert doc["estado"] == "failed" and e.doble.llamadas_embeddings == 0
            assert e.indice._privada.get(where=_filtro(mf.workspace_id, mf.document_id))["ids"] == []
            assert_fuente(e, mf, 409)
        assert pila.storage.get(clave_original(mf.workspace_id, mf.document_id)) == TEXTO
        assert (
            ManifiestoDocumento.model_validate_json(
                pila.storage.get(clave_documento(mf.workspace_id, mf.document_id))
            ).admision_sqlite_confirmada
            is True
        )
        e.efectos.reconciliar()
        assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_cancelacion_reconstruccion_conserva_oci_y_no_reintenta(pila, monkeypatch):
    mf = fuente(pila)
    iniciada, liberar = threading.Event(), threading.Event()
    recuperar = ProcesadorIngestionDocumental.recuperar

    def bloqueada(self, ctx, doc):
        iniciada.set()
        assert liberar.wait(10)
        return recuperar(self, ctx, doc)

    monkeypatch.setattr(ProcesadorIngestionDocumental, "recuperar", bloqueada)
    with abrir(pila) as e:
        try:
            assert iniciada.wait(5)
            doc = e.db.obtener_documento(mf.document_id)
            respuesta = e.cliente.post(f"/api/jobs/{doc['recuperacion_job_id']}/cancel", headers=e.headers)
            assert respuesta.status_code == 200
        finally:
            liberar.set()
        doc = terminal(e)
        assert e.db.obtener_job(doc["recuperacion_job_id"])["status"] == "cancelled"
        assert pila.storage.get(clave_original(mf.workspace_id, mf.document_id)) == TEXTO
        assert (
            ManifiestoDocumento.model_validate_json(
                pila.storage.get(clave_documento(mf.workspace_id, mf.document_id))
            ).estado
            == DocumentStatus.READY
        )
        e.efectos.reconciliar()
        assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert_fuente(e, mf, 409)


def test_respuesta_oci_perdida_no_publica_ready_ni_purga_fuente(pila, monkeypatch):
    mf = fuente(pila, estado=DocumentStatus.PROCESSING)
    subir = pila.storage.upload
    perdida = [False]

    def aplicar_sin_respuesta(key, contenido, **kwargs):
        objeto = subir(key, contenido, **kwargs)
        if key == clave_documento(mf.workspace_id, mf.document_id) and json.loads(contenido)["estado"] == "ready":
            perdida[0] = True
            raise StorageUnavailable("respuesta perdida después de aplicar")
        return objeto

    monkeypatch.setattr(pila.storage, "upload", aplicar_sin_respuesta)
    with abrir(pila) as e:
        doc = terminal(e)
        assert perdida[0] and doc["estado"] == "failed"
        assert e.indice._privada.get(where=_filtro(mf.workspace_id, mf.document_id))["ids"] == []
        assert_fuente(e, mf, 409)
        assert pila.storage.get(clave_original(mf.workspace_id, mf.document_id)) == TEXTO
        assert (
            ManifiestoDocumento.model_validate_json(
                pila.storage.get(clave_documento(mf.workspace_id, mf.document_id))
            ).estado
            == DocumentStatus.READY
        )


def test_cambio_etag_durante_restauracion_impide_acreditar(pila, monkeypatch):
    mf = fuente(pila)
    get = pila.storage.get
    cambiada = [False]

    def cambiar_despues_de_fuente(key, **kwargs):
        contenido = get(key, **kwargs)
        if key == clave_original(mf.workspace_id, mf.document_id) and not cambiada[0]:
            cambiada[0] = True
            pila.storage.upload(
                clave_documento(mf.workspace_id, mf.document_id),
                mf.model_copy(update={"borrado": True}).model_dump_json(),
            )
        return contenido

    monkeypatch.setattr(pila.storage, "get", cambiar_despues_de_fuente)
    db = RegistroOperativo(pila.ruta / "operativo.db")
    try:
        with pytest.raises(StorageConflict):
            restaurar_registro(db, pila.storage)
        assert db.obtener_documento(mf.document_id) is None
        assert db.recuperacion_pendiente()
        pendientes = 2 - db._conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0]
        assert restaurar_registro(db, pila.storage) == pendientes
        assert db._conn.execute("SELECT COUNT(*) FROM workspaces").fetchone()[0] == 2
        assert db.esta_borrado("document", mf.document_id)
    finally:
        db.cerrar()


def test_despacho_comprueba_reserva_y_no_duplica_si_respuesta_sqlite_se_pierde(pila, monkeypatch):
    mf = fuente(pila)
    reservar = RegistroOperativo.reservar_reconstruccion_documental
    perdida = [False]

    def reservar_sin_respuesta(self, *args):
        resultado = reservar(self, *args)
        if not perdida[0]:
            perdida[0] = True
            raise sqlite3.OperationalError("respuesta de reserva perdida")
        return resultado

    with abrir(pila, cuotas=False) as e:
        monkeypatch.setattr(e.db, "reservar_reconstruccion_documental", reservar_sin_respuesta.__get__(e.db))
        with pytest.raises(sqlite3.OperationalError):
            e.db.reservar_reconstruccion_documental(mf.document_id, "job_reserva_confirmada")
        assert e.db.obtener_documento(mf.document_id)["recuperacion_job_id"] == "job_reserva_confirmada"
        assert e.db.obtener_job("job_reserva_confirmada") is None
    enqueue = GestorTrabajos.enqueue
    observaciones = []

    def despacho(self, ws, tipo, funcion, **kwargs):
        doc = self.store.obtener_documento(kwargs["document_id"])
        assert doc["recuperacion_estado"] == "reservada"
        assert doc["recuperacion_job_id"] == kwargs["job_id"]
        assert self.store.obtener_job(kwargs["job_id"]) is None
        observaciones.append(doc["recuperacion_job_id"])
        return enqueue(self, ws, tipo, funcion, **kwargs)

    monkeypatch.setattr(GestorTrabajos, "enqueue", despacho)
    with abrir(pila) as e:
        doc = terminal(e)
        assert observaciones == ["job_reserva_confirmada"]
        assert doc["recuperacion_intentos"] == 1
        assert_fuente(e, mf)


def test_pendientes_superan_capacidad_sin_perder_reservas(pila):
    docs = [fuente(pila, doc=f"doc_{i}") for i in range(7)]
    with abrir(pila) as e:
        reservas = {mf.document_id: e.db.obtener_documento(mf.document_id)["recuperacion_job_id"] for mf in docs}
        assert all(reservas.values())
        assert e.db.contar_activos_de_workspace(pila.ws.workspace_id) <= 1
        for mf in docs:
            e.efectos.reconciliar()
            doc = terminal(e, mf.document_id)
            assert doc["estado"] == "ready" and doc["recuperacion_job_id"] == reservas[mf.document_id]
            assert doc["recuperacion_intentos"] == 1
        assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 7
        assert len(e.cliente.get("/api/documents", headers=e.headers).json()["items"]) == 7


def test_fallo_terminal_con_indice_previo_compatible_lo_conserva(pila, monkeypatch):
    mf = fuente(pila, estado=DocumentStatus.PROCESSING)
    doble = DobleGemini(16)
    indice = VectorStoreChroma(pila.ruta / "chroma", GeminiEmbeddings(doble, modelo=MODELO, dimensiones=16))
    ctx = ContextoEjecucion(
        "preparar",
        mf.workspace_id,
        "ingestion",
        time.monotonic() + 30,
        cuotas=CuotasProveedor({MODELO: CuotasModelo(1000, 1000000, 1000)}),
    )
    chunks = [
        c.como_chunk_interno()
        for c in trocear(
            parsear_archivo(TEXTO, mf.source_name),
            workspace_id=mf.workspace_id,
            document_id=mf.document_id,
            source_name=mf.source_name,
            config=mf.chunker,
        )
    ]
    indice.indexar(mf.workspace_id, mf.document_id, chunks, contexto=ctx)
    previo = dict(indice._activo(indice._privada, mf.workspace_id, mf.document_id))
    indice.cerrar()
    doble.reset()
    subir = pila.storage.upload

    def fallar_final(key, contenido, **kwargs):
        if key == clave_documento(mf.workspace_id, mf.document_id) and json.loads(contenido)["estado"] == "ready":
            raise StorageUnavailable("No se confirmó el manifiesto final")
        return subir(key, contenido, **kwargs)

    monkeypatch.setattr(pila.storage, "upload", fallar_final)
    with abrir(pila, doble=doble) as e:
        doc = terminal(e)
        assert doc["estado"] == "failed"
        assert dict(e.indice._activo(e.indice._privada, mf.workspace_id, mf.document_id)) == previo
        assert (
            e.indice.listar_chunks(mf.workspace_id, mf.document_id, contexto=ctx, source_hash=mf.hash_sha256) == chunks
        )
        assert doble.llamadas_embeddings == 0
        assert_fuente(e, mf, 409)
        assert pila.storage.get(clave_original(mf.workspace_id, mf.document_id)) == TEXTO
        assert e.db.limpieza_documental_completada(mf.workspace_id, mf.document_id)


@pytest.mark.parametrize("secundario", ["oci", "ack_sqlite"])
def test_compensacion_recuperacion_pendiente_se_confirma_tras_reinicio(pila, monkeypatch, secundario):
    mf = fuente(pila)
    with monkeypatch.context() as parche:
        indexar = VectorStoreChroma.indexar
        get = pila.storage.get
        fallo = [False]

        def indice_aplicado_sin_respuesta(self, *args, **kwargs):
            indexar(self, *args, **kwargs)
            fallo[0] = True
            raise RuntimeError("Índice aplicado sin confirmación")

        def fuente_no_verificable(key, **kwargs):
            if fallo[0] and key == clave_original(mf.workspace_id, mf.document_id):
                raise StorageUnavailable("Compensación sin respuesta OCI")
            return get(key, **kwargs)

        def ack_no_disponible(self, *args, **kwargs):
            raise sqlite3.OperationalError("No se pudo confirmar el ack")

        parche.setattr(VectorStoreChroma, "indexar", indice_aplicado_sin_respuesta)
        if secundario == "oci":
            parche.setattr(pila.storage, "get", fuente_no_verificable)
        else:
            parche.setattr(RegistroOperativo, "registrar_resultado_efecto_documental", ack_no_disponible)
        with abrir(pila) as e:
            doc = terminal(e)
            assert doc["estado"] == "failed"
            assert not e.db.limpieza_documental_completada(mf.workspace_id, mf.document_id)
            assert e.indice._privada.get(where=_filtro(mf.workspace_id, mf.document_id))["ids"] == []
            assert get(clave_original(mf.workspace_id, mf.document_id)) == TEXTO
            assert (
                ManifiestoDocumento.model_validate_json(
                    get(clave_documento(mf.workspace_id, mf.document_id))
                ).admision_sqlite_confirmada
                is True
            )
    con = sqlite3.connect(pila.ruta / "operativo.db")
    try:
        con.execute("UPDATE document_cleanup SET proximo_intento_en=NULL")
        con.commit()
    finally:
        con.close()
    with abrir(pila) as e:
        assert e.db.limpieza_documental_completada(mf.workspace_id, mf.document_id)
        assert e.db.obtener_documento(mf.document_id)["estado"] == "failed"
        assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
        assert e.doble.llamadas_embeddings == 0
        assert_fuente(e, mf, 409)


def test_lapida_remota_durante_publicacion_gana(pila, monkeypatch):
    mf = fuente(pila)
    indexar = VectorStoreChroma.indexar
    intentos = []

    def retirar_antes_de_confirmar(self, ws, doc, chunks, **kwargs):
        confirmar = kwargs["confirmar"]

        def retirada_remota():
            intentos.append(doc)
            pila.storage.upload(clave_documento(ws, doc), mf.model_copy(update={"borrado": True}).model_dump_json())
            confirmar()

        return indexar(self, ws, doc, chunks, **{**kwargs, "confirmar": retirada_remota})

    monkeypatch.setattr(VectorStoreChroma, "indexar", retirar_antes_de_confirmar)
    with abrir(pila) as e:
        hasta(lambda: intentos and not e.gestor._ejecutables and e.gestor._observadores_en_vuelo == 0)
        assert intentos == [mf.document_id]
        assert e.db.esta_borrado("document", mf.document_id)
        assert e.cliente.get(f"/api/documents/{mf.document_id}", headers=e.headers).status_code == 404
        assert e.cliente.get(f"/api/documents/{mf.document_id}/sources/c", headers=e.headers).status_code == 404
        assert e.indice._privada.get(where=_filtro(mf.workspace_id, mf.document_id))["ids"] == []
        assert (
            e.db._conn.execute("SELECT estado FROM documents WHERE document_id=?", (mf.document_id,)).fetchone()[0]
            != "ready"
        )


@pytest.mark.parametrize("cuotas", [True, False])
def test_lapida_chroma_conservada_no_resucita_al_perder_sqlite(pila, cuotas):
    mf = fuente(pila)
    indice = VectorStoreChroma(pila.ruta / "chroma", GeminiEmbeddings(DobleGemini(16), modelo=MODELO, dimensiones=16))
    indice._retirar_verificado(mf.workspace_id, mf.document_id, definitivo=True)
    indice.cerrar()
    with abrir(pila, cuotas=cuotas) as e:
        assert e.cliente.get(f"/api/documents/{mf.document_id}", headers=e.headers).status_code == 404
        assert e.cliente.get("/api/documents", headers=e.headers).json()["items"] == []
        assert e.db.esta_borrado("document", mf.document_id)
        assert e.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
        assert e.doble.llamadas_embeddings == 0
