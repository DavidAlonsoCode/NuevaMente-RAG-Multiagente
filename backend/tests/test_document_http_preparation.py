"""Preparación #19: lecturas internas y jobs HTTP, sin publicar contratos pendientes."""

import json
import threading
import time
from types import SimpleNamespace

import pytest
from app.api.security import _peticiones_globales, _peticiones_ip
from app.config import Configuracion
from app.core.rag.rebuild import ManifiestoDocumento
from app.jobs.manager import CuotasModelo, CuotasProveedor
from app.main import crear_app
from app.schemas.documents import EntradaArchivoDocumento
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.requests import UploadRequest
from app.storage.object_keys import clave_documento, clave_workspace
from app.storage.provider import StorageUnavailable
from doubles.gemini import DobleGemini
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration_mock
TEXTO = "# Redes\n\n" + (
    "Una VCN conecta subredes privadas. Las rutas conservan aislamiento y trazabilidad del espacio.\n" * 12
)


@pytest.fixture
def entorno(tmp_path):
    _peticiones_globales.clear()
    _peticiones_ip.clear()
    ajustes = Configuracion(
        app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path), embedding_dimensions=16
    )
    doble = DobleGemini(16)
    cuotas = CuotasProveedor({ajustes.gemini_embedding_model: CuotasModelo(1000, 1000000, 1000)})
    app = crear_app(ajustes, proveedor_embeddings=doble, cuotas_proveedor=cuotas)
    with TestClient(app) as cliente:
        ws = cliente.post("/api/workspaces").json()
        yield SimpleNamespace(
            app=app,
            cliente=cliente,
            ws=ws,
            doble=doble,
            cuotas=cuotas,
            db=app.state.db,
            storage=app.state.storage_provider,
            gestor=app.state.jobs_manager,
            admision=app.state.documents_admission,
            consulta=app.state.documents_query,
            headers={"Authorization": f"Bearer {ws['token']}"},
        )
    assert app.state.documents_admission is None
    assert app.state.documents_query is None
    assert app.state.vectorstore is None


def hasta(condicion):
    limite = time.monotonic() + 10
    while time.monotonic() < limite:
        valor = condicion()
        if valor:
            return valor
        time.sleep(0.005)
    pytest.fail("No se confirmó la condición en 10 segundos")


def admitir(e, *, clave=None, titulo="Redes"):
    return e.admision.admitir_texto(
        e.ws["token"], UploadRequest(documento_titulo=titulo, documento_contenido=TEXTO), clave=clave
    )


def terminado(e, recibo, estado="completed"):
    return hasta(lambda: e.gestor.estado(recibo.job_id)["status"] == estado)


def listo(e):
    recibo = admitir(e)
    terminado(e, recibo)
    return recibo


def escribir_manifest(e, document_id, **cambios):
    clave = clave_documento(e.ws["workspace_id"], document_id)
    mf = ManifiestoDocumento.model_validate_json(e.storage.get(clave))
    e.storage.upload(clave, mf.model_copy(update=cambios).model_dump_json().encode(), content_type="application/json")


def test_lifespan_comparte_recursos_sin_otro_gestor(entorno):
    e = entorno
    assert e.admision.db is e.consulta.db is e.gestor.store is e.db
    assert e.admision.storage is e.consulta.storage is e.storage
    assert e.admision.gestor is e.gestor
    assert e.admision.procesar.indice is e.consulta.indice is e.app.state.vectorstore
    assert e.gestor.cuotas is e.cuotas
    assert e.app.state.vectorstore.embeddings.proveedor is e.doble
    assert e.doble.llamadas_embeddings == 0


@pytest.mark.parametrize("extension", ["md", "txt", "pdf"])
def test_formatos_admitidos_exponen_progreso_por_jobs_existentes(entorno, crear_pdf, extension):
    e = entorno
    contenido = crear_pdf("redes.pdf", paginas=4).read_bytes() if extension == "pdf" else TEXTO.encode()
    recibo = e.admision.admitir_archivo(
        e.ws["token"], contenido, EntradaArchivoDocumento(source_name=f"redes.{extension}"), clave="upload"
    )
    terminado(e, recibo)
    respuesta = e.cliente.get(f"/api/jobs/{recibo.job_id}", headers=e.headers)
    assert respuesta.status_code == 200
    datos = respuesta.json()
    assert datos["status"] == "completed" and datos["cancel_url"] is None
    assert datos["result"]["document_id"] == recibo.document_id
    assert e.consulta.detalle(e.ws["token"], recibo.document_id)["estado"] == "ready"
    assert e.consulta.fuente(
        e.ws["token"], recibo.document_id, e.consulta.chunks(e.ws["token"], recibo.document_id)[0].chunk_id
    )
    eventos = e.cliente.get(datos["events_url"], headers=e.headers)
    assert eventos.status_code == 200 and '"status": "completed"' in eventos.text


def test_listado_paginado_y_fuentes_sin_embeddings_adicionales(entorno):
    e = entorno
    recibos = []
    for titulo in ("Primero", "Segundo", "Tercero"):
        recibo = admitir(e, titulo=titulo)
        terminado(e, recibo)
        recibos.append(recibo)
    llamadas = e.doble.llamadas_embeddings
    pagina = e.consulta.listar(e.ws["token"], limit=2)
    segunda = e.consulta.listar(e.ws["token"], limit=2, cursor=pagina.siguiente_id)
    ids = [d["document_id"] for d in pagina.documentos + segunda.documentos]
    assert ids == sorted(r.document_id for r in recibos)
    assert len(set(ids)) == 3 and segunda.siguiente_id is None
    assert len(e.consulta.secciones(e.ws["token"], ids[0])) == 1
    chunks = e.consulta.chunks(e.ws["token"], ids[0])
    assert e.consulta.fuente(e.ws["token"], ids[0], chunks[0].chunk_id) == chunks[0]
    assert e.doble.llamadas_embeddings == llamadas


def test_idempotencia_http_jobs_conserva_ids_y_no_repite_embeddings(entorno):
    e = entorno
    uno = admitir(e, clave="misma")
    terminado(e, uno)
    llamadas = e.doble.llamadas_embeddings
    dos = admitir(e, clave="misma")
    assert (dos.document_id, dos.job_id) == (uno.document_id, uno.job_id)
    assert e.doble.llamadas_embeddings == llamadas
    assert e.cliente.get(f"/api/jobs/{dos.job_id}", headers=e.headers).json()["status"] == "completed"
    with pytest.raises(ErrorAplicacion) as error:
        admitir(e, clave="misma", titulo="Otro contenido lógico")
    assert error.value.error.code == ErrorCode.IDEMPOTENCY_CONFLICT


@pytest.mark.parametrize("consulta", ["detalle", "chunks", "secciones", "fuente"])
def test_documento_ajeno_no_consulta_storage_ni_chroma(entorno, monkeypatch, consulta):
    e = entorno
    recibo = listo(e)
    otro = e.cliente.post("/api/workspaces").json()

    def prohibido(*args, **kwargs):
        pytest.fail("Se consultó storage/Chroma antes de autorizar ownership")

    monkeypatch.setattr(e.storage, "get", prohibido)
    monkeypatch.setattr(e.app.state.vectorstore, "listar_chunks", prohibido)
    args = (otro["token"], recibo.document_id) + (("cualquiera",) if consulta == "fuente" else ())
    with pytest.raises(ErrorAplicacion) as error:
        getattr(e.consulta, consulta)(*args)
    assert error.value.error.code == ErrorCode.NOT_FOUND
    respuesta = e.cliente.get(f"/api/jobs/{recibo.job_id}", headers={"Authorization": f"Bearer {otro['token']}"})
    assert respuesta.status_code == 404


def test_session_revocada_no_entrega_documento_ni_job(entorno):
    e = entorno
    recibo = listo(e)
    e.db.revocar_sesion(e.ws["token"])
    with pytest.raises(ErrorAplicacion) as error:
        e.consulta.detalle(e.ws["token"], recibo.document_id)
    assert error.value.error.code == ErrorCode.SESSION_INVALID
    assert e.cliente.get(f"/api/jobs/{recibo.job_id}", headers=e.headers).status_code == 401


@pytest.mark.parametrize("estado", [DocumentStatus.PROCESSING, DocumentStatus.FAILED])
def test_sqlite_no_ready_bloquea_indice_existente(entorno, monkeypatch, estado):
    e = entorno
    recibo = listo(e)
    e.db.actualizar_documento(recibo.document_id, estado)
    assert e.consulta.detalle(e.ws["token"], recibo.document_id)["estado"] == estado.value
    monkeypatch.setattr(e.app.state.vectorstore, "listar_chunks", lambda *a, **kw: pytest.fail("No autorizado"))
    with pytest.raises(ErrorAplicacion) as error:
        e.consulta.chunks(e.ws["token"], recibo.document_id)
    assert error.value.error.code == ErrorCode.INVALID_STATE


@pytest.mark.parametrize("tipo", ["sql_document", "sql_workspace", "oci_document", "oci_workspace", "oci_recurso"])
def test_lapidas_bloquean_lectura_aunque_indice_exista(entorno, tipo):
    e = entorno
    recibo = listo(e)
    if tipo.startswith("sql_"):
        recurso = "document" if tipo == "sql_document" else "workspace"
        e.db.agendar_borrado(
            recurso, recibo.document_id if recurso == "document" else e.ws["workspace_id"], e.ws["workspace_id"]
        )
    elif tipo == "oci_document":
        escribir_manifest(e, recibo.document_id, borrado=True)
    else:
        clave = clave_workspace(e.ws["workspace_id"])
        mf = json.loads(e.storage.get(clave))
        if tipo == "oci_workspace":
            mf["borrado"] = True
        else:
            mf["recursos_borrados"] = [{"recurso_tipo": "document", "recurso_id": recibo.document_id}]
        e.storage.upload(clave, json.dumps(mf).encode(), content_type="application/json")
    with pytest.raises(ErrorAplicacion) as error:
        e.consulta.chunks(e.ws["token"], recibo.document_id)
    assert error.value.error.code in (ErrorCode.NOT_FOUND, ErrorCode.SESSION_INVALID)
    assert e.consulta.listar(e.ws["token"]).documentos == []


@pytest.mark.parametrize("interrupcion", ["lapida", "revocacion", "failed"])
def test_revalida_despues_de_chroma_antes_de_entregar(entorno, monkeypatch, interrupcion):
    e = entorno
    recibo = listo(e)
    original = e.app.state.vectorstore.listar_chunks

    def interceptar(*args, **kwargs):
        chunks = original(*args, **kwargs)
        if interrupcion == "lapida":
            e.db.agendar_borrado("document", recibo.document_id, e.ws["workspace_id"])
        elif interrupcion == "revocacion":
            e.db.revocar_sesion(e.ws["token"])
        else:
            e.db.actualizar_documento(recibo.document_id, DocumentStatus.FAILED)
        return chunks

    monkeypatch.setattr(e.app.state.vectorstore, "listar_chunks", interceptar)
    with pytest.raises(ErrorAplicacion):
        e.consulta.chunks(e.ws["token"], recibo.document_id)


def test_error_storage_no_fallback_y_sin_publicar_ready(entorno, monkeypatch):
    e = entorno
    recibo = listo(e)

    def fallar(*args, **kwargs):
        raise StorageUnavailable("Fallo simulado")

    monkeypatch.setattr(e.storage, "get", fallar)
    with pytest.raises(StorageUnavailable):
        e.consulta.detalle(e.ws["token"], recibo.document_id)
    assert e.db.obtener_documento(recibo.document_id)["estado"] == "ready"


@pytest.mark.parametrize("flag", ["admision_confirmada", "admision_sqlite_confirmada"])
def test_admision_indeterminada_no_se_presenta_en_lecturas(entorno, flag):
    e = entorno
    recibo = listo(e)
    escribir_manifest(e, recibo.document_id, **{flag: False})
    with pytest.raises(ErrorAplicacion) as error:
        e.consulta.detalle(e.ws["token"], recibo.document_id)
    assert error.value.error.code == ErrorCode.NOT_FOUND
    assert e.consulta.listar(e.ws["token"]).documentos == []


def test_jobs_queued_cancelled_y_fuentes_no_disponibles(entorno):
    e = entorno
    otro = e.cliente.post("/api/workspaces").json()
    iniciada, liberar = threading.Event(), threading.Event()

    def bloquear(ctx):
        iniciada.set()
        assert liberar.wait(10)
        return {"ok": True}

    e.gestor.enqueue(otro["workspace_id"], "chat", bloquear)
    assert iniciada.wait(5)
    try:
        recibo = admitir(e)
        assert e.consulta.detalle(e.ws["token"], recibo.document_id)["estado"] == "processing"
        with pytest.raises(ErrorAplicacion) as error:
            e.consulta.chunks(e.ws["token"], recibo.document_id)
        assert error.value.error.code == ErrorCode.INVALID_STATE
        url = f"/api/jobs/{recibo.job_id}"
        assert e.cliente.get(url, headers=e.headers).json()["status"] == "queued"
        assert e.cliente.post(url + "/cancel", headers=e.headers).json()["status"] == "cancelled"
        assert e.cliente.post(url + "/cancel", headers=e.headers).status_code == 200
        assert e.consulta.detalle(e.ws["token"], recibo.document_id)["estado"] == "failed"
    finally:
        liberar.set()


def test_jobs_failed_por_proveedor_y_diagnostico_sin_contenido(entorno):
    e = entorno
    e.doble.programar_error_embeddings(RuntimeError("Texto privado del proveedor"))
    recibo = admitir(e)
    terminado(e, recibo, "failed")
    respuesta = e.cliente.get(f"/api/jobs/{recibo.job_id}", headers=e.headers)
    assert respuesta.status_code == 200 and respuesta.json()["status"] == "failed"
    assert respuesta.json()["result"] is None
    assert "Texto privado" not in respuesta.text
    assert respuesta.json()["error"]["code"] == "INGESTION_INDICE"


def test_contratos_documentales_no_se_publican_sin_revision(entorno):
    e = entorno
    rutas = e.app.openapi()["paths"]
    assert set(p for p in rutas if p.startswith("/api/documents")) == {
        "/api/documents/upload",
        "/api/documents",
        "/api/documents/{document_id}",
        "/api/documents/{document_id}/sources/{chunk_id}",
    }
    assert "delete" in rutas["/api/documents/{document_id}"]
    assert {"202", "204", "404", "401", "503"} <= set(rutas["/api/documents/{document_id}"]["delete"]["responses"])
    respuesta = e.cliente.post(
        "/api/documents/upload", json={"documento_titulo": "A", "documento_contenido": TEXTO}, headers=e.headers
    )
    assert respuesta.status_code == 202
    assert set(respuesta.json()) == {"document_id", "status", "job_id", "status_url", "events_url", "cancel_url"}
    terminado(e, SimpleNamespace(job_id=respuesta.json()["job_id"]))
    lista = e.cliente.get("/api/documents", headers=e.headers)
    assert lista.status_code == 200 and set(lista.json()) == {"items", "next_cursor"}
    assert set(lista.json()["items"][0]) == {"document_id", "titulo", "status", "metadata", "cobertura", "error"}


def test_proveedor_sin_cuotas_falla_antes_de_abrir_registro(tmp_path):
    ajustes = Configuracion(app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path))
    with pytest.raises(ValueError, match="cuotas"):
        crear_app(ajustes, proveedor_embeddings=DobleGemini(16))
    assert not (tmp_path / "operativo.db").exists()


@pytest.mark.parametrize("limit", [0, -1, 101, True])
def test_limite_de_paginacion_interno_acotado(entorno, limit):
    with pytest.raises(ValueError):
        entorno.consulta.listar(entorno.ws["token"], limit=limit)


def test_fuente_de_otro_documento_y_fuente_inexistente_no_se_entregan(entorno):
    e = entorno
    uno = listo(e)
    dos = listo(e)
    fuente = e.consulta.chunks(e.ws["token"], uno.document_id)[0]
    for chunk_id in (fuente.chunk_id, "inexistente"):
        with pytest.raises(ErrorAplicacion) as error:
            e.consulta.fuente(e.ws["token"], dos.document_id, chunk_id)
        assert error.value.error.code == ErrorCode.NOT_FOUND


def test_vision_pendiente_informa_cobertura_sin_habilitar_fuentes(entorno):
    import io

    from reportlab.pdfgen import canvas

    e = entorno
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.showPage()
    pdf.save()
    contenido = buffer.getvalue()
    recibo = e.admision.admitir_archivo(e.ws["token"], contenido, EntradaArchivoDocumento(source_name="escaneo.pdf"))
    terminado(e, recibo)
    detalle = e.consulta.detalle(e.ws["token"], recibo.document_id)
    assert detalle["estado"] == "processing"
    assert json.loads(detalle["cobertura_json"])["paginas_visuales_pendientes"] == [1]
    assert e.cliente.get(f"/api/jobs/{recibo.job_id}", headers=e.headers).json()["status"] == "completed"
    with pytest.raises(ErrorAplicacion) as error:
        e.consulta.chunks(e.ws["token"], recibo.document_id)
    assert error.value.error.code == ErrorCode.INVALID_STATE
    assert e.doble.llamadas_embeddings == 0


@pytest.mark.parametrize("datos", [b"no json", b"[]", b'{"workspace_id": "ajeno"}'])
def test_manifest_workspace_invalido_no_habilita_indice(entorno, datos):
    e = entorno
    recibo = listo(e)
    e.storage.upload(clave_workspace(e.ws["workspace_id"]), datos, content_type="application/json")
    with pytest.raises(StorageUnavailable):
        e.consulta.chunks(e.ws["token"], recibo.document_id)


def test_cambio_de_estado_durante_oci_no_habilita_lectura(entorno, monkeypatch):
    e = entorno
    recibo = listo(e)
    original = e.storage.get

    def interceptar(nombre, **kwargs):
        contenido = original(nombre, **kwargs)
        if nombre == clave_documento(e.ws["workspace_id"], recibo.document_id):
            e.db.actualizar_documento(recibo.document_id, DocumentStatus.FAILED)
        return contenido

    monkeypatch.setattr(e.storage, "get", interceptar)
    with pytest.raises(ErrorAplicacion) as error:
        e.consulta.chunks(e.ws["token"], recibo.document_id)
    assert error.value.error.code == ErrorCode.INVALID_STATE


def test_cobertura_visual_en_sqlite_bloquea_ready_inconsistente(entorno):
    from app.schemas.documents import CoberturaDocumento, MetadatosDocumento

    e = entorno
    recibo = listo(e)
    doc = e.db.obtener_documento(recibo.document_id)
    e.db.actualizar_documento(recibo.document_id, DocumentStatus.PROCESSING)
    e.db.guardar_metadata_documental(
        e.ws["workspace_id"],
        recibo.document_id,
        metadatos=MetadatosDocumento.model_validate_json(doc["metadatos_json"]),
        cobertura=CoberturaDocumento(paginas_visuales_pendientes=[1]),
    )
    # Reproduce un registro inconsistente sin alterar la lógica de publicación.
    e.db.actualizar_documento(recibo.document_id, DocumentStatus.READY)
    with pytest.raises(ErrorAplicacion) as error:
        e.consulta.chunks(e.ws["token"], recibo.document_id)
    assert error.value.error.code == ErrorCode.INVALID_STATE


def test_factory_sin_cuota_de_modelo_cierra_registro_al_fallar_lifespan(tmp_path):
    import sqlite3

    ajustes = Configuracion(app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path))
    app = crear_app(ajustes, proveedor_embeddings=DobleGemini(16), cuotas_proveedor=CuotasProveedor())
    with pytest.raises(ValueError, match="cuotas explícitas"):
        with TestClient(app):
            pytest.fail("No debe iniciar un pipeline sin cuotas")
    assert app.state.vectorstore is None
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        app.state.db.obtener_workspace("cualquiera")


@pytest.mark.parametrize(
    "archivo,nombre,codigo",
    [
        (b"contenido", "archivo.exe", ErrorCode.VALIDATION_ERROR),
        (b"no es PDF", "archivo.pdf", ErrorCode.VALIDATION_ERROR),
        (b"\xff", "archivo.txt", ErrorCode.VALIDATION_ERROR),
        (b"a" * (5 * 1024 * 1024 + 1), "archivo.md", ErrorCode.DOCUMENT_TOO_LARGE),
    ],
    ids=["extension-invalida", "firma-pdf-invalida", "utf8-invalido", "markdown-oversize"],
)
def test_validacion_interna_previa_a_persistir_y_encolar(entorno, archivo, nombre, codigo):
    e = entorno
    with pytest.raises(ErrorAplicacion) as error:
        e.admision.admitir_archivo(e.ws["token"], archivo, EntradaArchivoDocumento(source_name=nombre))
    assert error.value.error.code == codigo
    assert e.db.listar_documentos(e.ws["workspace_id"]) == []
    assert e.storage.list(f"source_documents/{e.ws['workspace_id']}/").items == []
    assert e.doble.llamadas_embeddings == 0
