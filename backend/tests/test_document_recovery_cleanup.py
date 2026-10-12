"""Compensación persistente H02: conservar A sin abandonar versiones físicas B."""

import hashlib
import time
from types import SimpleNamespace

import pytest
from app.core.rag.chunker import trocear
from app.core.rag.embeddings import GeminiEmbeddings
from app.core.rag.parser import parsear_archivo
from app.core.rag.vectorstore import DEMO_WORKSPACE_ID, VectorStoreChroma, _filtro
from app.documents.lifecycle import EfectosDocumentales
from app.jobs.manager import ContextoEjecucion, CuotasModelo, CuotasProveedor
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus, JobStatus
from app.session.manager import SessionManager
from app.storage.object_keys import clave_documento, clave_original
from doubles.gemini import DobleGemini
from test_document_recovery import MODELO, TEXTO, fuente

pytestmark = pytest.mark.integration_mock


@pytest.fixture
def sistema(tmp_path, mock_storage):
    db = RegistroOperativo(tmp_path / "operativo.db")
    ws = SessionManager(db, mock_storage, 30, 24).crear_espacio()
    mf = fuente(SimpleNamespace(storage=mock_storage, ws=ws), estado=DocumentStatus.PROCESSING)
    db.registrar_documento(
        mf.document_id,
        ws.workspace_id,
        mf.titulo,
        DocumentStatus.PROCESSING,
        mf.hash_sha256,
        source_name=mf.source_name,
        metadatos=mf.metadatos,
    )
    objeto = mock_storage.list(clave_documento(ws.workspace_id, mf.document_id)).items[0]
    db.acreditar_documento_restaurado(mf.document_id, {"etag": objeto.etag, "manifest": mf.model_dump(mode="json")})
    job = "job_recovery_compensacion"
    db.reservar_reconstruccion_documental(mf.document_id, job)
    db.encolar_job(job, ws.workspace_id, "ingestion", document_id=mf.document_id)
    db.iniciar_job(job)
    doble = DobleGemini(16)
    indice = VectorStoreChroma(tmp_path / "chroma", GeminiEmbeddings(doble, modelo=MODELO, dimensiones=16))
    ctx = ContextoEjecucion(
        job,
        ws.workspace_id,
        "ingestion",
        time.monotonic() + 60,
        cuotas=CuotasProveedor({MODELO: CuotasModelo(1000, 1000000, 1000)}),
    )
    chunks = [
        c.como_chunk_interno()
        for c in trocear(
            parsear_archivo(TEXTO, mf.source_name),
            workspace_id=ws.workspace_id,
            document_id=mf.document_id,
            source_name=mf.source_name,
            config=mf.chunker,
        )
    ]
    indice.indexar(ws.workspace_id, mf.document_id, chunks, contexto=ctx)
    previo = dict(indice._activo(indice._privada, ws.workspace_id, mf.document_id))
    db.registrar_indice_reconstruccion(mf.document_id, job, previo)
    db.finalizar_job(job, JobStatus.FAILED, error_code="INGESTION_PERSISTENCIA", error_message="Fallo final simulado")
    s = SimpleNamespace(
        db=db,
        indice=indice,
        doble=doble,
        ctx=ctx,
        previo=previo,
        chunks=chunks,
        ws=ws.workspace_id,
        doc=mf.document_id,
        mf=mf,
        storage=mock_storage,
        ruta=tmp_path,
    )
    s.efectos = EfectosDocumentales(db, mock_storage, indice)
    s.datos_a = _snapshot(indice._privada, _filtro(s.ws, s.doc, previo["version"]))
    doble.reset()
    try:
        yield s
    finally:
        s.indice.cerrar()
        s.db.cerrar()


def _snapshot(coleccion, filtro=None):
    datos = coleccion.get(where=filtro, include=["metadatas", "documents", "embeddings"])
    return {
        identidad: (meta, texto, tuple(map(float, vector)))
        for identidad, meta, texto, vector in zip(
            datos["ids"], datos["metadatas"], datos["documents"], datos["embeddings"], strict=True
        )
    }


def _parcial(s, *, coleccion=None, ws=None, doc=None, version="B"):
    coleccion = coleccion if coleccion is not None else s.indice._privada
    ws, doc = ws or s.ws, doc or s.doc
    chunk = s.chunks[0].model_copy(update={"workspace_id": ws, "document_id": doc})
    vector = next(iter(s.datos_a.values()))[2]
    coleccion.upsert(
        ids=[hashlib.sha256(f"{ws}:{doc}:{version}".encode()).hexdigest()],
        embeddings=[list(vector)],
        documents=[chunk.texto],
        metadatas=[
            {
                "workspace_id": ws,
                "document_id": doc,
                "version_indice": version,
                "orden": 0,
                "chunk": chunk.model_dump_json(),
            }
        ],
    )


def _a_intacta(s):
    assert dict(s.indice._activo(s.indice._privada, s.ws, s.doc)) == s.previo
    assert _snapshot(s.indice._privada, _filtro(s.ws, s.doc, s.previo["version"])) == s.datos_a
    assert s.indice.listar_chunks(s.ws, s.doc, contexto=s.ctx, source_hash=s.mf.hash_sha256) == s.chunks
    assert s.doble.llamadas_embeddings == 0
    assert s.storage.get(clave_original(s.ws, s.doc)) == TEXTO


def _pendiente(s):
    assert not s.db.limpieza_documental_completada(s.ws, s.doc)
    fila = s.db._conn.execute("SELECT * FROM document_cleanup WHERE document_id=?", (s.doc,)).fetchone()
    assert fila["completado_en"] is None and fila["ultimo_error"] is not None
    assert s.db.obtener_documento(s.doc)["estado"] == "failed"


def _hacer_vencer_reintento(s):
    # Adelanta solo el reloj del backoff persistido; no altera la obligación ni su ack.
    s.db._conn.execute("UPDATE document_cleanup SET proximo_intento_en=NULL WHERE document_id=?", (s.doc,))


def test_a_compatible_b_parcial_se_retira_antes_del_ack(sistema):
    s = sistema
    _parcial(s)
    assert s.indice._privada.get(where=_filtro(s.ws, s.doc, "B"))["ids"]
    assert s.efectos.reconciliar(s.doc) == 1
    _a_intacta(s)
    assert s.indice._privada.get(where=_filtro(s.ws, s.doc, "B"))["ids"] == []
    assert s.db.limpieza_documental_completada(s.ws, s.doc)
    assert s.efectos.reconciliar(s.doc) == 0


@pytest.mark.parametrize("fallo", ["delete", "respuesta_perdida", "verificacion"])
def test_fallo_de_b_no_confirma_y_reintento_completa(sistema, monkeypatch, fallo):
    s = sistema
    _parcial(s)
    clase = type(s.indice._privada)
    borrar, leer = clase.delete, clase.get
    borrado = [False]
    with monkeypatch.context() as parche:

        def delete(coleccion, **kwargs):
            if coleccion.name == s.indice._privada.name:
                if fallo == "delete":
                    raise RuntimeError("B no se eliminó")
                resultado = borrar(coleccion, **kwargs)
                borrado[0] = True
                if fallo == "respuesta_perdida":
                    raise RuntimeError("B eliminada sin respuesta")
                return resultado
            return borrar(coleccion, **kwargs)

        def get(coleccion, **kwargs):
            if fallo == "verificacion" and borrado[0] and coleccion.name == s.indice._privada.name:
                raise RuntimeError("Ausencia física no verificable")
            return leer(coleccion, **kwargs)

        parche.setattr(clase, "delete", delete)
        parche.setattr(clase, "get", get)
        assert s.efectos.reconciliar(s.doc) == 0
    _pendiente(s)
    _a_intacta(s)
    assert bool(s.indice._privada.get(where=_filtro(s.ws, s.doc, "B"))["ids"]) == (fallo == "delete")
    _hacer_vencer_reintento(s)
    assert s.efectos.reconciliar(s.doc) == 1
    _a_intacta(s)
    assert s.indice._privada.get(where=_filtro(s.ws, s.doc, "B"))["ids"] == []
    assert s.db.limpieza_documental_completada(s.ws, s.doc)


def test_b_en_otras_colecciones_se_retira_sin_tocar_otros_ambitos(sistema):
    s = sistema
    otra = s.indice._cliente.get_or_create_collection("embeddings-otro-modelo", embedding_function=None)
    for coleccion in [s.indice._privada, otra]:
        _parcial(s, coleccion=coleccion)
        _parcial(s, coleccion=coleccion, doc="doc_ajeno")
        _parcial(s, coleccion=coleccion, ws="workspace_ajeno")
    _parcial(s, coleccion=s.indice._demo, ws=DEMO_WORKSPACE_ID)
    externos = [
        (c, _filtro(ws, doc), _snapshot(c, _filtro(ws, doc)))
        for c in [s.indice._privada, otra]
        for ws, doc in [(s.ws, "doc_ajeno"), ("workspace_ajeno", s.doc)]
    ]
    demo = _snapshot(s.indice._demo)
    assert s.efectos.reconciliar(s.doc) == 1
    _a_intacta(s)
    assert otra.get(where=_filtro(s.ws, s.doc))["ids"] == []
    assert s.indice._privada.get(where=_filtro(s.ws, s.doc, "B"))["ids"] == []
    for coleccion, filtro, datos in externos:
        assert _snapshot(coleccion, filtro) == datos
    assert _snapshot(s.indice._demo) == demo


def test_reinicio_recupera_compensacion_con_sqlite_y_chroma_persistentes(sistema, monkeypatch):
    s = sistema
    _parcial(s)
    with monkeypatch.context() as parche:

        def fallar(*args, **kwargs):
            raise RuntimeError("Delete temporalmente indisponible")

        parche.setattr(type(s.indice._privada), "delete", fallar)
        assert s.efectos.reconciliar(s.doc) == 0
    _pendiente(s)
    s.indice.cerrar()
    s.db.cerrar()
    s.db = RegistroOperativo(s.ruta / "operativo.db")
    s.indice = VectorStoreChroma(s.ruta / "chroma", GeminiEmbeddings(s.doble, modelo=MODELO, dimensiones=16))
    s.efectos = EfectosDocumentales(s.db, s.storage, s.indice)
    _pendiente(s)
    assert s.indice._privada.get(where=_filtro(s.ws, s.doc, "B"))["ids"]
    _hacer_vencer_reintento(s)
    assert s.efectos.reconciliar(s.doc) == 1
    _a_intacta(s)
    assert s.indice._privada.get(where=_filtro(s.ws, s.doc, "B"))["ids"] == []
    assert s.db.limpieza_documental_completada(s.ws, s.doc)
    assert s.db._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1


@pytest.mark.parametrize("estado_a", ["ausente", "incompleto", "incompatible"])
def test_a_no_verificable_mantiene_compensacion_total(sistema, estado_a):
    s = sistema
    _parcial(s)
    if estado_a == "ausente":
        with s.indice._db() as db:
            db.execute("DELETE FROM documentos WHERE workspace=? AND documento=?", (s.ws, s.doc))
    elif estado_a == "incompleto":
        s.indice._privada.delete(ids=[next(iter(s.datos_a))])
    else:
        with s.indice._db() as db:
            db.execute("UPDATE documentos SET firma=? WHERE workspace=? AND documento=?", ("incompatible", s.ws, s.doc))
    assert s.efectos.reconciliar(s.doc) == 1
    assert s.indice._activo(s.indice._privada, s.ws, s.doc) is None
    assert s.indice._privada.get(where=_filtro(s.ws, s.doc))["ids"] == []
    assert s.db.limpieza_documental_completada(s.ws, s.doc)
    assert s.storage.get(clave_original(s.ws, s.doc)) == TEXTO
    assert s.doble.llamadas_embeddings == 0


def test_escritura_tardia_antes_de_verificar_no_recibe_ack(sistema, monkeypatch):
    s = sistema
    _parcial(s)
    borrar = type(s.indice._privada).delete
    with monkeypatch.context() as parche:

        def delete(coleccion, **kwargs):
            resultado = borrar(coleccion, **kwargs)
            if coleccion.name == s.indice._privada.name:
                # Simula un escritor externo al worker único después del delete.
                _parcial(s, version="B_tardia")
            return resultado

        parche.setattr(type(s.indice._privada), "delete", delete)
        assert s.efectos.reconciliar(s.doc) == 0
    _pendiente(s)
    _a_intacta(s)
    assert s.indice._privada.get(where=_filtro(s.ws, s.doc, "B_tardia"))["ids"]
    _hacer_vencer_reintento(s)
    assert s.efectos.reconciliar(s.doc) == 1
    assert s.indice._privada.get(where=_filtro(s.ws, s.doc, "B_tardia"))["ids"] == []
    _a_intacta(s)


def test_cambio_catalogo_despues_de_validar_no_recibe_ack(sistema, monkeypatch):
    s = sistema
    _parcial(s)
    conservar = s.efectos._conservar_indice_recuperable
    with monkeypatch.context() as parche:

        def cambiar(efecto):
            assert conservar(efecto)
            with s.indice._db() as db:
                db.execute("UPDATE documentos SET firma=? WHERE workspace=? AND documento=?", ("cambio", s.ws, s.doc))
            return True

        parche.setattr(s.efectos, "_conservar_indice_recuperable", cambiar)
        assert s.efectos.reconciliar(s.doc) == 0
    _pendiente(s)
    assert s.indice._privada.get(where=_filtro(s.ws, s.doc, "B"))["ids"]
    _hacer_vencer_reintento(s)
    assert s.efectos.reconciliar(s.doc) == 1
    assert s.indice._privada.get(where=_filtro(s.ws, s.doc))["ids"] == []
