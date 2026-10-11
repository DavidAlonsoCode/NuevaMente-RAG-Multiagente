"""Pipeline real #19: worker común, parser/chunker, Gemini doble y Chroma temporal."""

import hashlib
import io
import json
import sqlite3
import threading
import time
from dataclasses import replace

import pytest
from app.core.rag.chunker import trocear
from app.core.rag.embeddings import GeminiEmbeddings
from app.core.rag.parser import LimitesIngesta, parsear_archivo
from app.core.rag.rebuild import ManifiestoDocumento
from app.core.rag.vectorstore import IndiceInconsistenteError, VectorStoreChroma
from app.documents.ingestion import crear_servicio_ingestion
from app.jobs.manager import (
    ContextoEjecucion,
    CuotaAgotadaError,
    CuotasModelo,
    CuotasProveedor,
    GestorTrabajos,
    ReintentableError,
)
from app.jobs.store import RegistroOperativo
from app.schemas.documents import EntradaArchivoDocumento
from app.schemas.enums import DocumentStatus, JobStatus
from app.session.manifests import sincronizar_manifiesto
from app.session.recovery import restaurar_registro
from app.storage.object_keys import clave_documento, clave_original
from app.storage.provider import StorageUnavailable
from doubles.gemini import DobleGemini

pytestmark = pytest.mark.integration_mock
MODELO = "gemini-embedding-2"
TEXTO = (
    "# Redes\n\nUna VCN conecta subredes privadas. Las rutas conservan el aislamiento del espacio.\n"
    "## Gateways\n\nLos gateways permiten conexiones autorizadas y trazables.\n"
).encode()


@pytest.fixture
def pila(tmp_path, mock_storage):
    db = RegistroOperativo(tmp_path / "operativo.sqlite")
    for ws in ("ws_a", "ws_b"):
        db.crear_workspace(ws, f"codigo_{ws}", 30)
        db.crear_sesion(f"token_{ws}", ws, 24)
        sincronizar_manifiesto(db, mock_storage, ws)
    cuotas = CuotasProveedor({nombre: CuotasModelo(1000, 1000000, 1000) for nombre in (MODELO, "modelo-otro")})
    gestor = GestorTrabajos(db, cuotas=cuotas)
    doble = DobleGemini(16)
    indice = VectorStoreChroma(
        tmp_path / "chroma", GeminiEmbeddings(doble, modelo=MODELO, dimensiones=16), tamano_lote=1
    )
    servicio = crear_servicio_ingestion(
        db, mock_storage, gestor, indice, limites=replace(LimitesIngesta(), minimo_tokens_documento=3)
    )
    yield db, mock_storage, gestor, doble, indice, servicio
    gestor.detener()
    db.cerrar()


def admitir(pila, contenido=TEXTO, nombre="redes.md", ws="ws_a", clave=None):
    return pila[5].admitir_archivo(f"token_{ws}", contenido, EntradaArchivoDocumento(source_name=nombre), clave=clave)


def hasta(condicion):
    limite = time.monotonic() + 10
    while time.monotonic() < limite:
        resultado = condicion()
        if resultado:
            return resultado
        time.sleep(0.005)
    pytest.fail("No se alcanzó la condición dentro de 10 segundos")


def terminal(pila, recibo, esperado="completed"):
    def leer():
        fila = pila[0].obtener_job(recibo.job_id)
        if fila and fila["status"] not in ("queued", "running"):
            assert fila["status"] == esperado, fila
        return fila if fila and fila["status"] == esperado else None

    return hasta(leer)


def manifest(pila, doc, ws="ws_a"):
    return ManifiestoDocumento.model_validate_json(pila[1].get(clave_documento(ws, doc)))


def ctx(pila, ws="ws_a"):
    return ContextoEjecucion("lectura", ws, "ingestion", time.monotonic() + 30, cuotas=pila[2].cuotas)


def fallo_confirmado(pila, recibo, estado="failed"):
    fila = terminal(pila, recibo, estado)
    hasta(lambda: manifest(pila, recibo.document_id).estado == DocumentStatus.FAILED)
    hasta(lambda: pila[4].contar("ws_a", recibo.document_id, contexto=ctx(pila)) == 0)
    assert pila[0].obtener_documento(recibo.document_id)["estado"] == "failed"
    assert fila["resultado"] is None
    return fila


def test_pipeline_textual_conserva_chunks_fuente_metadata_y_cobertura(pila):
    recibo = admitir(pila)
    fila = terminal(pila, recibo)
    doc = pila[0].obtener_documento(recibo.document_id)
    mf = manifest(pila, recibo.document_id)
    assert doc["estado"] == mf.estado.value == "ready"
    assert mf.admision_confirmada and mf.admision_sqlite_confirmada
    assert mf.hash_sha256 == doc["hash"] == hashlib.sha256(TEXTO).hexdigest()
    assert pila[1].get(clave_original("ws_a", recibo.document_id)) == TEXTO
    assert json.loads(doc["metadatos_json"]) == mf.metadatos.model_dump(mode="json")
    assert json.loads(doc["cobertura_json"]) == mf.cobertura.model_dump(mode="json")
    assert mf.metadatos.cantidad_tokens > 0 and mf.metadatos.huella_config_parser
    assert [s.seccion_id for s in mf.cobertura.secciones] == ["seccion:1", "seccion:4"]
    guardados = pila[4].listar_chunks("ws_a", recibo.document_id, contexto=ctx(pila), source_hash=mf.hash_sha256)
    esperados = [
        c.como_chunk_interno()
        for c in trocear(
            parsear_archivo(TEXTO, mf.source_name, mf.parser),
            workspace_id="ws_a",
            document_id=recibo.document_id,
            source_name=mf.source_name,
            language=mf.language,
            config=mf.chunker,
        )
    ]
    assert guardados == esperados
    assert all(c.linea_inicio and c.linea_fin and c.seccion_id and c.tokenizer for c in guardados)
    assert json.loads(fila["resultado"])["cantidad_chunks"] == len(guardados)
    assert pila[3].llamadas_embeddings == len(guardados)
    assert [e["status"] for e in pila[0].eventos_desde_job(recibo.job_id)] == ["queued", "running", "completed"]


def test_reutiliza_embeddings_compatibles_entre_ids_distintos_sin_cruzar_workspace(pila):
    primero = admitir(pila, clave="uno")
    terminal(pila, primero)
    llamadas = pila[3].llamadas_embeddings
    cuota = pila[2].cuotas.disponibles_hoy(MODELO)
    segundo = admitir(pila, nombre="otro.md", clave="dos")
    fila = terminal(pila, segundo)
    assert primero.document_id != segundo.document_id and primero.job_id != segundo.job_id
    assert pila[3].llamadas_embeddings == llamadas
    assert pila[2].cuotas.disponibles_hoy(MODELO) == cuota
    assert json.loads(fila["resultado"])["reutilizado"] is True
    for recibo in (primero, segundo):
        mf = manifest(pila, recibo.document_id)
        chunks = pila[4].listar_chunks("ws_a", recibo.document_id, contexto=ctx(pila), source_hash=mf.hash_sha256)
        assert {c.document_id for c in chunks} == {recibo.document_id}
        assert {c.source_name for c in chunks} == {mf.source_name}
    tercero = admitir(pila, ws="ws_b")
    terminal(pila, tercero)
    assert pila[3].llamadas_embeddings == llamadas * 2
    replay = admitir(pila, clave="uno")
    assert replay.document_id == primero.document_id and replay.job_id == primero.job_id
    assert pila[3].llamadas_embeddings == llamadas * 2


@pytest.mark.parametrize("cambio", ["dimensiones", "modelo", "preparacion", "chunker"])
def test_no_reutiliza_embeddings_incompatibles(pila, cambio):
    primero = admitir(pila)
    terminal(pila, primero)
    llamadas = pila[3].llamadas_embeddings
    if cambio == "chunker":
        # Configuración durable distinta produce cortes distintos, sin tocar el algoritmo.
        original = pila[5].procesar

        def procesar(ctx_worker, mf):
            distinto = mf.model_copy(update={"chunker": replace(mf.chunker, tamano_objetivo=20, solapamiento=2)})
            obj = pila[1].list(clave_documento("ws_a", mf.document_id), limit=1).items[0]
            original._persistir(distinto, obj.etag)
            return original(ctx_worker, distinto)

        pila[5].procesar = procesar
        usado = pila[3]
    else:

        class OtraPreparacion(GeminiEmbeddings):
            @property
            def preparation_version(self):
                return "compatible-test-v2"

        nuevo = DobleGemini(8 if cambio == "dimensiones" else 16)
        clase = OtraPreparacion if cambio == "preparacion" else GeminiEmbeddings
        emb = clase(
            nuevo, modelo="modelo-otro" if cambio == "modelo" else MODELO, dimensiones=nuevo.dimension_embeddings
        )
        if cambio == "preparacion":
            with pytest.raises(IndiceInconsistenteError):
                VectorStoreChroma(pila[4].ruta, emb)
        # Preparación incompatible exige reconstrucción; no mezcla la colección vigente.
        ruta = pila[4].ruta / "otra" if cambio == "preparacion" else pila[4].ruta
        indice = VectorStoreChroma(ruta, emb)
        pila[5].procesar.indice = indice
        usado = nuevo
    segundo = admitir(pila)
    fila = terminal(pila, segundo)
    assert json.loads(fila["resultado"])["reutilizado"] is False
    assert usado.llamadas_embeddings > (llamadas if usado is pila[3] else 0)


@pytest.mark.parametrize("mixto", [False, True])
def test_pdf_visual_completed_documento_processing_y_metadata_durable(pila, mixto):
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    if mixto:
        pdf.drawString(30, 700, "Redes VCN privadas con rutas autorizadas. " * 4)
        pdf.showPage()
    pdf.showPage()
    pdf.save()
    recibo = admitir(pila, buffer.getvalue(), "visual.pdf")
    fila = terminal(pila, recibo)
    mf = manifest(pila, recibo.document_id)
    assert mf.estado == DocumentStatus.PROCESSING
    assert pila[0].obtener_documento(recibo.document_id)["estado"] == "processing"
    assert mf.cobertura.paginas_visuales_pendientes == [2 if mixto else 1]
    assert mf.cobertura.paginas_procesadas == ([1] if mixto else [])
    assert mf.metadatos.cantidad_paginas == (2 if mixto else 1)
    assert mf.metadatos.huella_config_parser and mf.parser and mf.chunker
    assert pila[3].llamadas_embeddings == (1 if mixto else 0)
    assert json.loads(fila["resultado"])["estado"] == "processing"
    restaurado = RegistroOperativo(pila[4].ruta.parent / "restaurado.sqlite")
    try:
        restaurar_registro(restaurado, pila[1])
        doc = restaurado.obtener_documento(recibo.document_id)
        assert doc["estado"] == "processing"
        assert (
            json.loads(doc["cobertura_json"])["paginas_visuales_pendientes"] == mf.cobertura.paginas_visuales_pendientes
        )
    finally:
        restaurado.cerrar()


def test_ready_y_completed_se_confirman_juntos_sin_ready_prematuro(pila, monkeypatch):
    original = pila[0].finalizar_job
    observado = []

    def finalizar(job_id, estado, **datos):
        doc = pila[0].obtener_documento(pila[0].obtener_job(job_id)["document_id"])
        if estado == JobStatus.COMPLETED:
            observado.append((doc["estado"], manifest(pila, doc["document_id"]).estado))
            assert datos["documento_confirmado"].cantidad_chunks > 0
        return original(job_id, estado, **datos)

    monkeypatch.setattr(pila[0], "finalizar_job", finalizar)
    recibo = admitir(pila)
    terminal(pila, recibo)
    assert observado == [("processing", DocumentStatus.READY)]
    assert pila[0].obtener_documento(recibo.document_id)["estado"] == "ready"


@pytest.mark.parametrize(
    "contenido,nombre,codigo",
    [
        (b"\xef\xbb\xbf ", "vacio.txt", "PARSER_EXTRACCION_INSUFICIENTE"),
        (b"%PDF-1.7\narchivo roto", "roto.pdf", "PARSER_CORRUPTO"),
    ],
)
def test_parser_fallido_deja_diagnostico_y_nunca_ready(pila, contenido, nombre, codigo):
    recibo = admitir(pila, contenido, nombre)
    assert fallo_confirmado(pila, recibo)["error_code"] == codigo
    assert pila[3].llamadas_embeddings == 0


@pytest.mark.parametrize(
    "error,codigo",
    [
        (ValueError("secreto del proveedor"), "INGESTION_INDICE"),
        (CuotaAgotadaError("Cuota diaria agotada"), "CUOTA_AGOTADA"),
    ],
)
def test_error_embeddings_no_publica_indice_ni_filtra_excepcion(pila, error, codigo):
    pila[3].programar_error_embeddings(error)
    recibo = admitir(pila)
    fila = fallo_confirmado(pila, recibo)
    assert fila["error_code"] == codigo
    assert "secreto" not in (fila["error_message"] or "")
    assert "secreto" not in (manifest(pila, recibo.document_id).detalle_error or "")


def test_fallo_lote_chroma_no_expone_indice_parcial(pila, monkeypatch):
    upsert = pila[4]._privada.upsert
    lotes = []

    def guardar(**kwargs):
        lotes.append(kwargs)
        if len(lotes) == 2:
            raise RuntimeError("fallo nativo")
        upsert(**kwargs)
        assert pila[4].contar("ws_a", kwargs["metadatas"][0]["document_id"], contexto=ctx(pila)) == 0

    monkeypatch.setattr(pila[4]._privada, "upsert", guardar)
    recibo = admitir(pila)
    assert fallo_confirmado(pila, recibo)["error_code"] == "INGESTION_INDICE"
    assert len(lotes) == 2


@pytest.mark.parametrize("aplicada", [False, True])
def test_fallo_ultima_escritura_storage_no_publica_ready(pila, monkeypatch, aplicada):
    upload = pila[1].upload
    fallo = []

    def escribir(clave, contenido, **kwargs):
        if clave.endswith("/manifest.json") and clave.startswith("source_documents/"):
            payload = json.loads(contenido)
            if payload["estado"] == "ready" and not fallo:
                fallo.append(True)
                if aplicada:
                    upload(clave, contenido, **kwargs)
                raise StorageUnavailable("respuesta perdida")
        return upload(clave, contenido, **kwargs)

    monkeypatch.setattr(pila[1], "upload", escribir)
    recibo = admitir(pila)
    assert fallo_confirmado(pila, recibo)["error_code"] == "STORAGE_UNAVAILABLE"
    assert fallo


def test_cancelacion_despues_de_return_no_conserva_ready(pila):
    procesar = pila[5].procesar

    def cancelar(ctx_worker, mf):
        resultado = procesar(ctx_worker, mf)
        assert pila[2].cancelar(ctx_worker.job_id)
        return resultado

    pila[5].procesar = cancelar
    recibo = admitir(pila)
    fallo_confirmado(pila, recibo, "cancelled")


@pytest.mark.parametrize("accion", ["cancelar", "deadline", "borrar_local", "borrar_storage"])
def test_interrupcion_en_embeddings_impide_publicacion(pila, monkeypatch, accion):
    original = pila[3].embed_sync
    contexto_actual = []

    def embed(textos, *, timeout):
        fila = pila[0].primer_job_encolado()  # El actual ya es running; no existe otra cola.
        assert fila is None
        with pila[0]._lock:
            actual = dict(pila[0]._conn.execute("SELECT * FROM jobs WHERE status='running'").fetchone())
        doc = actual["document_id"]
        if accion == "cancelar":
            pila[2].cancelar(actual["job_id"])
        elif accion == "deadline":
            contexto_actual[0].deadline = time.monotonic() + 0.05
            time.sleep(0.08)
        elif accion == "borrar_local":
            pila[0].agendar_borrado("document", doc, "ws_a")
        else:
            clave = clave_documento("ws_a", doc)
            obj = pila[1].list(clave, limit=1).items[0]
            mf = manifest(pila, doc).model_copy(update={"borrado": True})
            pila[1].upload(clave, mf.model_dump_json().encode(), if_match=obj.etag)
        return original(textos, timeout=timeout)

    monkeypatch.setattr(pila[3], "embed_sync", embed)
    if accion == "deadline":
        procesar = pila[5].procesar

        def plazo(ctx_worker, mf):
            # Evita depender del tiempo de inicialización del tokenizador.
            contexto_actual.append(ctx_worker)
            return procesar(ctx_worker, mf)

        pila[5].procesar = plazo
    recibo = admitir(pila)
    esperado = "failed" if accion in ("deadline", "borrar_storage") else "cancelled"
    fila = terminal(pila, recibo, esperado)
    assert fila["resultado"] is None
    assert manifest(pila, recibo.document_id).estado != DocumentStatus.READY
    hasta(lambda: pila[4].contar("ws_a", recibo.document_id, contexto=ctx(pila)) == 0)
    if accion == "borrar_local":
        assert pila[0].obtener_documento(recibo.document_id) is None
    else:
        assert pila[0].obtener_documento(recibo.document_id)["estado"] == "failed"


def test_original_corrupto_falla_antes_de_gemini(pila):
    procesar = pila[5].procesar

    def corromper(ctx_worker, mf):
        pila[1].upload(clave_original("ws_a", mf.document_id), b"otra version")
        return procesar(ctx_worker, mf)

    pila[5].procesar = corromper
    recibo = admitir(pila)
    assert fallo_confirmado(pila, recibo)["error_code"] == "INGESTION_FUENTE"
    assert pila[3].llamadas_embeddings == 0


def test_fallo_secundario_storage_conserva_terminal_y_diagnostico_original(pila, monkeypatch, caplog):
    upload = pila[1].upload

    def escribir(clave, contenido, **kwargs):
        if clave.startswith("source_documents/") and clave.endswith("manifest.json"):
            if json.loads(contenido)["estado"] == "failed":
                raise StorageUnavailable("secreto compensacion")
        return upload(clave, contenido, **kwargs)

    monkeypatch.setattr(pila[1], "upload", escribir)
    pila[3].programar_error_embeddings(ValueError("secreto original"))
    recibo = admitir(pila)
    fila = terminal(pila, recibo, "failed")
    hasta(lambda: "Sincronización terminal" in caplog.text)
    assert fila["error_code"] == "INGESTION_INDICE"
    assert pila[0].obtener_documento(recibo.document_id)["estado"] == "failed"
    assert "secreto" not in caplog.text
    hasta(lambda: pila[4].contar("ws_a", recibo.document_id, contexto=ctx(pila)) == 0)


@pytest.mark.parametrize("aplicado", [False, True])
def test_fallo_commit_terminal_sqlite_preserva_publicacion_atomica(pila, monkeypatch, aplicado):
    conexion = pila[0]._conn
    provocado = threading.Event()

    class ConexionConFallo:
        def __getattr__(self, nombre):
            return getattr(conexion, nombre)

        def execute(self, sql, *args):
            if sql == "COMMIT" and not provocado.is_set():
                completado = conexion.execute("SELECT 1 FROM jobs WHERE status='completed'").fetchone()
                if completado:
                    provocado.set()
                    if aplicado:
                        conexion.execute(sql, *args)
                    raise sqlite3.OperationalError("Respuesta del commit perdida")
            return conexion.execute(sql, *args)

    monkeypatch.setattr(pila[0], "_conn", ConexionConFallo())
    recibo = admitir(pila)
    if aplicado:
        fila = terminal(pila, recibo)
        assert fila["resultado"] and pila[0].obtener_documento(recibo.document_id)["estado"] == "ready"
        assert manifest(pila, recibo.document_id).estado == DocumentStatus.READY
        assert pila[4].contar("ws_a", recibo.document_id, contexto=ctx(pila)) > 0
    else:
        assert fallo_confirmado(pila, recibo)["error_code"] == "ERROR_TRABAJO"
    assert provocado.is_set()


@pytest.mark.parametrize("momento", ["metadata", "inventario", "confirmacion_storage"])
def test_fallo_parcial_no_produce_completed_ni_ready(pila, monkeypatch, momento):
    if momento == "metadata":

        def fallar(*args, **kwargs):
            raise sqlite3.OperationalError("Fallo guardando metadata")

        monkeypatch.setattr(pila[0], "guardar_metadata_documental", fallar)
        codigo = "INGESTION_METADATA"
    elif momento == "inventario":
        monkeypatch.setattr(pila[4], "listar_chunks", lambda *args, **kwargs: [])
        codigo = "INGESTION_INDICE"
    else:
        get = pila[1].get
        una_vez = []

        def no_confirmar(clave, **kwargs):
            contenido = get(clave, **kwargs)
            if clave.startswith("source_documents/") and clave.endswith("manifest.json"):
                if json.loads(contenido)["estado"] == "ready" and not una_vez:
                    una_vez.append(True)
                    raise StorageUnavailable("No se recibió la confirmación")
            return contenido

        monkeypatch.setattr(pila[1], "get", no_confirmar)
        codigo = "STORAGE_UNAVAILABLE"
    recibo = admitir(pila)
    assert fallo_confirmado(pila, recibo)["error_code"] == codigo


@pytest.mark.parametrize(
    "campo,valor",
    [
        ("document_id", "ajeno"),
        ("workspace_id", "ws_b"),
        ("hash_sha256", "0" * 64),
    ],
)
def test_confirmacion_ajena_no_puede_publicar_ready(pila, campo, valor):
    procesar = pila[5].procesar

    def falsificar(ctx_worker, mf):
        resultado = procesar(ctx_worker, mf)
        return resultado.model_copy(update={campo: valor})

    pila[5].procesar = falsificar
    recibo = admitir(pila)
    assert fallo_confirmado(pila, recibo)["error_code"] == "ERROR_TRABAJO"


def test_reintentos_embeddings_usando_cuotas_y_contexto_del_worker(pila):
    pila[3].programar_error_embeddings(ReintentableError("transitorio"))
    cuota = pila[2].cuotas.disponibles_hoy(MODELO)
    recibo = admitir(pila)
    fila = terminal(pila, recibo)
    cantidad = json.loads(fila["resultado"])["cantidad_chunks"]
    assert fila["intentos"] == 1
    assert pila[3].llamadas_embeddings == cantidad + 1
    assert pila[2].cuotas.disponibles_hoy(MODELO) == cuota - cantidad - 1


def test_cancelacion_en_cola_no_ejecuta_pipeline(pila):
    iniciado, liberar = threading.Event(), threading.Event()

    def otro_trabajo(ctx_worker):
        iniciado.set()
        assert liberar.wait(5)
        ctx_worker.chequear()

    pila[2].enqueue("ws_b", "chat", otro_trabajo)
    assert iniciado.wait(3)
    try:
        recibo = admitir(pila)
        assert pila[0].obtener_job(recibo.job_id)["status"] == "queued"
        assert pila[2].cancelar(recibo.job_id)
        fallo_confirmado(pila, recibo, "cancelled")
        assert pila[3].llamadas_embeddings == 0
    finally:
        liberar.set()


def test_borrado_concurrente_impide_reconstruccion_del_derivado(pila, monkeypatch):
    from app.core.rag.rebuild import reconstruir_workspace

    original = pila[3].embed_sync
    retirado = []

    def borrar(textos, *, timeout):
        with pila[0]._lock:
            doc = pila[0]._conn.execute("SELECT document_id FROM jobs WHERE status='running'").fetchone()[0]
        pila[0].agendar_borrado("document", doc, "ws_a")
        retirado.append(doc)
        return original(textos, timeout=timeout)

    monkeypatch.setattr(pila[3], "embed_sync", borrar)
    recibo = admitir(pila)
    terminal(pila, recibo, "cancelled")
    hasta(lambda: pila[4]._retirado("ws_a", recibo.document_id))
    assert reconstruir_workspace(pila[1], pila[4], contexto=ctx(pila)) == []
    assert retirado == [recibo.document_id] and pila[3].llamadas_embeddings == 1


@pytest.mark.parametrize("accion", ["corromper", "retirar"])
def test_fuente_modificada_durante_gemini_no_publica_indice(pila, monkeypatch, accion):
    original = pila[3].embed_sync
    una_vez = []

    def modificar(textos, *, timeout):
        if not una_vez:
            una_vez.append(True)
            with pila[0]._lock:
                doc = pila[0]._conn.execute("SELECT document_id FROM jobs WHERE status='running'").fetchone()[0]
            clave = clave_original("ws_a", doc)
            if accion == "retirar":
                pila[1].delete(clave)
            else:
                pila[1].upload(clave, b"una fuente diferente")
        return original(textos, timeout=timeout)

    monkeypatch.setattr(pila[3], "embed_sync", modificar)
    recibo = admitir(pila)
    codigo = "STORAGE_UNAVAILABLE" if accion == "retirar" else "INGESTION_INDICE"
    assert fallo_confirmado(pila, recibo)["error_code"] == codigo
