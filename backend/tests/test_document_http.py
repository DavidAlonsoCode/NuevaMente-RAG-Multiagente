"""HTTP real #19 sobre storage mock, DobleGemini y Chroma temporal."""

import io
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest
from app.api.routes.documents import upload
from app.api.security import _peticiones_globales, _peticiones_ip
from app.config import Configuracion
from app.core.rag.rebuild import ManifiestoDocumento
from app.jobs.manager import CuotasModelo, CuotasProveedor
from app.jobs.store import RegistroOperativo
from app.main import crear_app
from app.schemas.enums import DocumentStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.session.recovery import restaurar_registro
from app.storage.object_keys import clave_documento
from app.storage.provider import StorageUnavailable
from doubles.gemini import DobleGemini
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from pydantic import ValidationError
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from starlette import formparsers
from starlette.requests import Request

pytestmark = pytest.mark.integration_mock
TEXTO = "# Redes\n\n" + "Una VCN conecta subredes privadas y conserva aislamiento y trazabilidad.\n" * 20
UPLOAD = "/api/documents/upload"


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
            db=app.state.db,
            storage=app.state.storage_provider,
            gestor=app.state.jobs_manager,
            admision=app.state.documents_admission,
            consulta=app.state.documents_query,
            doble=doble,
            headers={"Authorization": f"Bearer {ws['token']}"},
        )


def subir(e, *, clave=None, titulo="Redes", contenido=TEXTO):
    return e.cliente.post(
        UPLOAD,
        json={"documento_titulo": titulo, "documento_contenido": contenido},
        headers={**e.headers, **({"Idempotency-Key": clave} if clave is not None else {})},
    )


def terminado(e, datos, estado="completed"):
    limite = time.monotonic() + 10
    while time.monotonic() < limite:
        respuesta = e.cliente.get(datos["status_url"], headers=e.headers)
        assert respuesta.status_code == 200
        if respuesta.json()["status"] == estado:
            return respuesta.json()
        time.sleep(0.005)
    pytest.fail("Trabajo sin terminal esperado")


def listo(e):
    respuesta = subir(e)
    assert respuesta.status_code == 202
    datos = respuesta.json()
    terminado(e, datos)
    return datos


def manifest(e, doc, **cambios):
    clave = clave_documento(e.ws["workspace_id"], doc)
    mf = ManifiestoDocumento.model_validate_json(e.storage.get(clave))
    e.storage.upload(clave, mf.model_copy(update=cambios).model_dump_json().encode(), content_type="application/json")


def heredado(e, doc, *, fecha="2026-10-01T00:00:00+00:00", **cambios):
    e.db.registrar_documento(doc, e.ws["workspace_id"], doc, DocumentStatus.PROCESSING, "a" * 64)
    with e.db.transaccion():
        e.db._conn.execute("UPDATE documents SET creado_en=? WHERE document_id=?", (fecha, doc))
    mf = ManifiestoDocumento(
        workspace_id=e.ws["workspace_id"], document_id=doc, source_name="a.txt", hash_sha256="a" * 64
    )
    e.storage.upload(clave_documento(e.ws["workspace_id"], doc), mf.model_copy(update=cambios).model_dump_json())


def error(respuesta, status, code):
    assert respuesta.status_code == status, respuesta.text
    datos = respuesta.json()
    assert set(datos) == {"error", "request_id"}
    assert datos["error"]["code"] == code
    assert datos["request_id"] == respuesta.headers["X-Request-ID"]
    return datos["error"]


def _archivos_multipart(monkeypatch):
    archivos = []
    crear = formparsers.SpooledTemporaryFile

    def registrar(*args, **kwargs):
        archivo = crear(*args, **kwargs)
        archivos.append(archivo)
        return archivo

    monkeypatch.setattr(formparsers, "SpooledTemporaryFile", registrar)
    return archivos


@pytest.mark.parametrize("terminacion", ["cabecera_invalida", "sin_cierre", "parte_completa_sin_cierre"])
def test_multipart_malformado_rechazado_y_temporales_cerrados(entorno, monkeypatch, terminacion):
    archivos = _archivos_multipart(monkeypatch)
    cuerpo = b'--revision\r\nContent-Disposition: form-data; name="file"; filename="redes.txt"\r\n\r\ncontenido'
    if terminacion == "cabecera_invalida":
        cuerpo += b"\r\n--revision\r\nCabecera invalida\r\n\r\nx\r\n--revision--\r\n"
    elif terminacion == "parte_completa_sin_cierre":
        cuerpo += b'\r\n--revision\r\nContent-Disposition: form-data; name="titulo"\r\n\r\ntitulo sin cierre'
    respuesta = entorno.cliente.post(
        UPLOAD,
        headers={**entorno.headers, "Content-Type": "multipart/form-data; boundary=revision"},
        content=cuerpo,
    )
    error(respuesta, 422, "VALIDATION_ERROR")
    assert archivos and all(archivo.closed for archivo in archivos)
    assert entorno.db._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_upload_stream_oversize_cierra_temporal_abierto(entorno, monkeypatch):
    archivos = _archivos_multipart(monkeypatch)
    entorno.admision.limites = replace(entorno.admision.limites, max_bytes_pdf=100)
    partes = iter(
        [
            b'--revision\r\nContent-Disposition: form-data; name="file"; filename="redes.txt"\r\n\r\nx',
            b"x" * (100 + 64 * 1024 + 1),
        ]
    )

    async def recibir():
        return {"type": "http.request", "body": next(partes), "more_body": True}

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": UPLOAD,
            "headers": [(b"content-type", b"multipart/form-data; boundary=revision")],
            "app": entorno.app,
        },
        recibir,
    )
    with pytest.raises(ErrorAplicacion) as capturado:
        await upload(request, sesion=entorno.db.obtener_sesion(entorno.ws["token"]), clave=None)
    assert capturado.value.error.code == ErrorCode.DOCUMENT_TOO_LARGE
    assert archivos and all(archivo.closed for archivo in archivos)
    assert entorno.db._conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


@pytest.mark.parametrize("formato", ["json", "md", "txt", "pdf"])
def test_upload_real_formatos_y_jobs_comunes(entorno, crear_pdf, formato):
    e = entorno
    if formato == "json":
        respuesta = subir(e, clave="uno")
    else:
        contenido = crear_pdf(paginas=4).read_bytes() if formato == "pdf" else TEXTO.encode()
        respuesta = e.cliente.post(
            UPLOAD,
            headers={**e.headers, "Idempotency-Key": "uno"},
            files={"file": (f"redes.{formato}", contenido, "application/octet-stream")},
            data={"titulo": "Redes"},
        )
    assert respuesta.status_code == 202, respuesta.text
    datos = respuesta.json()
    assert datos["status"] == "processing"
    assert set(datos) == {"document_id", "status", "job_id", "status_url", "events_url", "cancel_url"}
    terminal = terminado(e, datos)
    assert terminal["result"]["document_id"] == datos["document_id"]
    detalle = e.cliente.get(f"/api/documents/{datos['document_id']}", headers=e.headers)
    assert detalle.status_code == 200 and detalle.json()["status"] == "ready"
    assert detalle.json()["metadata"]["cantidad_tokens"] > 0
    assert detalle.json()["cobertura"] is not None
    assert e.cliente.get(datos["events_url"], headers=e.headers).status_code == 200


@pytest.mark.parametrize(
    "extension,paginas,procesadas,visuales",
    [
        ("pdf", ("mixta",), [1], [1]),
        ("pdf", ("texto", "mixta", "escaneada", "texto"), [1, 2, 4], [2, 3]),
        ("pdf", ("texto", "texto"), [1, 2], []),
        ("pdf", ("escaneada", "escaneada"), [], [1, 2]),
        ("md", (), [], []),
        ("txt", (), [], []),
    ],
    ids=["mixta_una_pagina", "mixta_varias_paginas", "textual", "escaneado", "markdown", "texto"],
)
def test_total_paginas_fisicas_y_cobertura_persistidas_h01(
    entorno, monkeypatch, extension, paginas, procesadas, visuales
):
    e = entorno
    if extension == "pdf":
        buffer = io.BytesIO()
        pdf = canvas.Canvas(buffer)
        for numero, tipo in enumerate(paginas, start=1):
            if tipo in ("texto", "mixta"):
                for linea in range(8):
                    pdf.drawString(
                        30, 700 - linea * 16, f"Pagina {numero}: una VCN conserva rutas privadas y trazabilidad."
                    )
            if tipo == "mixta":
                pdf.line(30, 300, 150, 400)
            elif tipo == "escaneada":
                imagen = Image.new("RGB", (300, 100), "white")
                ImageDraw.Draw(imagen).text((10, 30), f"Pagina escaneada {numero}: redes VCN", fill="black")
                pdf.drawImage(ImageReader(imagen), 30, 300, width=300, height=100)
            pdf.showPage()
        pdf.save()
        contenido = buffer.getvalue()
    else:
        contenido = TEXTO.encode()

    estados = []
    actualizar = e.db.actualizar_documento

    def observar_estado(document_id, estado, **kwargs):
        estados.append(estado)
        return actualizar(document_id, estado, **kwargs)

    monkeypatch.setattr(e.db, "actualizar_documento", observar_estado)
    respuesta = e.cliente.post(UPLOAD, files={"file": (f"cobertura.{extension}", contenido)}, headers=e.headers)
    assert respuesta.status_code == 202, respuesta.text
    datos = respuesta.json()
    assert datos["status"] == "processing"
    trabajo = terminado(e, datos)
    doc = datos["document_id"]
    fila = e.db.obtener_documento(doc)
    mf = ManifiestoDocumento.model_validate_json(e.storage.get(clave_documento(e.ws["workspace_id"], doc)))
    detalle = e.cliente.get(f"/api/documents/{doc}", headers=e.headers)
    assert detalle.status_code == 200
    detalle = detalle.json()
    estado = "processing" if visuales else "ready"
    assert fila["estado"] == mf.estado.value == detalle["status"] == trabajo["result"]["estado"] == estado
    assert mf.metadatos.cantidad_paginas == detalle["metadata"]["cantidad_paginas"] == len(paginas)
    assert mf.cobertura.paginas_procesadas == detalle["cobertura"]["paginas_procesadas"] == procesadas
    assert mf.cobertura.paginas_visuales_pendientes == detalle["cobertura"]["paginas_visuales_pendientes"] == visuales
    assert json.loads(fila["metadatos_json"]) == mf.metadatos.model_dump(mode="json")
    assert json.loads(fila["cobertura_json"]) == mf.cobertura.model_dump(mode="json")
    assert mf.admision_confirmada and mf.admision_sqlite_confirmada
    chunks = e.app.state.vectorstore.listar_chunks(
        e.ws["workspace_id"],
        doc,
        contexto=e.consulta._contexto(e.ws["token"], e.ws["workspace_id"]),
        source_hash=mf.hash_sha256,
    )
    assert bool(chunks) == bool(procesadas or extension != "pdf")
    if extension == "pdf":
        assert {chunk.pagina for chunk in chunks} == set(procesadas)
        assert all("VCN" in chunk.texto for chunk in chunks)
    else:
        assert mf.cobertura.secciones and all(chunk.pagina is None for chunk in chunks)
    fuente = e.cliente.get(
        f"/api/documents/{doc}/sources/{chunks[0].chunk_id if chunks else 'sin-texto'}", headers=e.headers
    )
    if visuales:
        assert DocumentStatus.READY not in estados
        error(fuente, 409, "INVALID_STATE")
    else:
        assert DocumentStatus.READY in estados
        assert fuente.status_code == 200 and "VCN" in fuente.json()["texto"]


@pytest.mark.parametrize(
    "entrada",
    [
        {},
        {"documento_titulo": "A"},
        {"documento_titulo": "", "documento_contenido": TEXTO},
        {"documento_titulo": "A", "documento_contenido": ""},
        {"documento_titulo": "A", "documento_contenido": "   "},
        {"documento_titulo": "A", "documento_contenido": TEXTO, "workspace_id": "ajeno"},
        {"documento_titulo": "A", "documento_contenido": 123},
        {"documento_titulo": " " * 3, "documento_contenido": TEXTO},
    ],
    ids=["vacio", "sin_contenido", "sin_titulo", "texto_vacio", "espacios", "extra", "tipo", "titulo_espacios"],
)
def test_json_invalido_no_crea_recursos(entorno, entrada):
    e = entorno
    error(e.cliente.post(UPLOAD, json=entrada, headers=e.headers), 422, "VALIDATION_ERROR")
    assert not e.db.listar_documentos(e.ws["workspace_id"])
    assert e.doble.llamadas_embeddings == 0


def test_json_roto_y_tipo_no_soportado(entorno):
    e = entorno
    error(
        e.cliente.post(UPLOAD, content=b"{", headers={**e.headers, "Content-Type": "application/json"}),
        422,
        "VALIDATION_ERROR",
    )
    error(
        e.cliente.post(UPLOAD, content=TEXTO, headers={**e.headers, "Content-Type": "text/plain"}),
        400,
        "INVALID_REQUEST",
    )


@pytest.mark.parametrize(
    "nombre, contenido",
    [
        ("x.exe", b"hola"),
        ("x.pdf", b"no PDF"),
        ("x.txt", b"\x00\xff"),
        ("x.md", b""),
    ],
    ids=["extension", "firma", "binario", "vacio"],
)
@pytest.mark.e2e
def test_archivo_invalido_no_admitido(entorno, nombre, contenido):
    e = entorno
    error(e.cliente.post(UPLOAD, files={"file": (nombre, contenido)}, headers=e.headers), 422, "VALIDATION_ERROR")
    assert not e.db.listar_documentos(e.ws["workspace_id"])


@pytest.mark.parametrize("caso", ["sin_file", "extra", "dos_files", "titulo_repetido", "file_texto"])
def test_multipart_estricto(entorno, caso):
    e = entorno
    partes = [("file", ("a.txt", TEXTO.encode()))]
    if caso == "sin_file":
        partes = [("titulo", (None, "Redes"))]
    elif caso == "extra":
        partes.append(("workspace_id", (None, "ajeno")))
    elif caso == "dos_files":
        partes.append(("file", ("b.txt", TEXTO.encode())))
    elif caso == "titulo_repetido":
        partes.extend([("titulo", (None, "A")), ("titulo", (None, "B"))])
    else:
        partes = [("file", (None, TEXTO))]
    error(e.cliente.post(UPLOAD, files=partes, headers=e.headers), 422, "VALIDATION_ERROR")


def test_nombre_archivo_es_etiqueta_y_no_ruta_de_storage(entorno):
    e = entorno
    respuesta = e.cliente.post(UPLOAD, files={"file": ("../fuera.txt", TEXTO.encode())}, headers=e.headers)
    assert respuesta.status_code == 202
    datos = respuesta.json()
    terminado(e, datos)
    mf = ManifiestoDocumento.model_validate_json(
        e.storage.get(clave_documento(e.ws["workspace_id"], datos["document_id"]))
    )
    assert mf.source_name == "../fuera.txt"
    assert e.storage.get(f"source_documents/{e.ws['workspace_id']}/{datos['document_id']}/original") == TEXTO.encode()


@pytest.mark.parametrize("formato", ["json", "txt", "pdf", "stream"])
@pytest.mark.e2e
def test_limites_antes_de_storage_y_trabajo(entorno, monkeypatch, formato):
    e = entorno
    e.admision.limites = replace(e.admision.limites, max_bytes_texto=100, max_bytes_pdf=200)
    monkeypatch.setattr(e.storage, "upload", lambda *a, **kw: pytest.fail("No persistir oversize"))
    if formato == "json":
        respuesta = subir(e, contenido="a" * 101)
    elif formato in ("txt", "pdf"):
        respuesta = e.cliente.post(UPLOAD, files={"file": (f"a.{formato}", b"a" * 201)}, headers=e.headers)
    else:
        respuesta = e.cliente.post(
            UPLOAD,
            content=iter([b"a" * 40000, b"a" * 40000]),
            headers={**e.headers, "Content-Type": "multipart/form-data; boundary=abc"},
        )
    error(respuesta, 413, "DOCUMENT_TOO_LARGE")
    assert not e.db.listar_documentos(e.ws["workspace_id"])


@pytest.mark.parametrize("ruta", [UPLOAD, "/api/documents", "/api/documents/noexiste", "/api/documents/d/sources/c"])
def test_rutas_exigen_sesion(entorno, ruta):
    e = entorno
    metodo = e.cliente.post if ruta == UPLOAD else e.cliente.get
    error(metodo(ruta, headers={"Authorization": "Bearer invalido"}), 401, "SESSION_INVALID")
    assert metodo(ruta).status_code == 401


def test_ownership_y_fuente_ajena_sin_io(entorno, monkeypatch):
    e = entorno
    datos = listo(e)
    otro = e.cliente.post("/api/workspaces").json()
    headers = {"Authorization": f"Bearer {otro['token']}"}
    monkeypatch.setattr(e.storage, "get", lambda *a, **kw: pytest.fail("Ownership antes de I/O"))
    error(e.cliente.get(f"/api/documents/{datos['document_id']}", headers=headers), 404, "NOT_FOUND")
    error(e.cliente.get(f"/api/documents/{datos['document_id']}/sources/c", headers=headers), 404, "NOT_FOUND")
    assert e.cliente.get("/api/documents", headers=headers).json() == {"items": [], "next_cursor": None}


def test_idempotencia_reproduce_respuesta_sin_recalcular(entorno):
    e = entorno
    uno = subir(e, clave="estable")
    terminado(e, uno.json())
    llamadas = e.doble.llamadas_embeddings
    dos = subir(e, clave="estable")
    assert uno.status_code == dos.status_code == 202 and uno.json() == dos.json()
    assert e.doble.llamadas_embeddings == llamadas
    error(subir(e, clave="estable", titulo="Distinto"), 409, "IDEMPOTENCY_CONFLICT")
    error(subir(e, clave=" "), 400, "INVALID_REQUEST")
    assert len(e.db.listar_documentos(e.ws["workspace_id"])) == 1


def test_hash_compatible_otro_id_reutiliza_embeddings(entorno):
    e = entorno
    uno = listo(e)
    llamadas = e.doble.llamadas_embeddings
    dos = subir(e, clave="otra")
    terminado(e, dos.json())
    assert dos.json()["document_id"] != uno["document_id"]
    assert e.doble.llamadas_embeddings == llamadas > 0


def test_processing_desconocido_busy429_y_reintento_antes_del_limite(entorno):
    e = entorno
    otro = e.cliente.post("/api/workspaces").json()
    iniciada, liberar = threading.Event(), threading.Event()

    def bloquear(ctx):
        iniciada.set()
        assert liberar.wait(10)

    e.gestor.enqueue(otro["workspace_id"], "chat", bloquear)
    assert iniciada.wait(3)
    try:
        uno = subir(e, clave="uno")
        assert uno.status_code == 202
        datos = uno.json()
        assert e.cliente.get(datos["status_url"], headers=e.headers).json()["status"] == "queued"
        detalle = e.cliente.get(f"/api/documents/{datos['document_id']}", headers=e.headers).json()
        assert detalle["status"] == "processing" and detalle["cobertura"] is None
        assert detalle["metadata"]["cantidad_tokens"] is None
        assert detalle["metadata"]["cantidad_paginas"] is None
        assert subir(e, clave="uno").json() == datos
        error(subir(e, clave="otra"), 429, "RATE_LIMITED")
        error(
            e.cliente.get(f"/api/documents/{datos['document_id']}/sources/c", headers=e.headers), 409, "INVALID_STATE"
        )
        assert e.cliente.post(datos["cancel_url"], headers=e.headers).json()["status"] == "cancelled"
        detalle = e.cliente.get(f"/api/documents/{datos['document_id']}", headers=e.headers).json()
        assert detalle["status"] == "failed" and detalle["error"] is not None
    finally:
        liberar.set()


def test_duplicado_en_curso_503_sin_ids_y_posterior_misma_respuesta(entorno, monkeypatch):
    e = entorno
    iniciada, liberar = threading.Event(), threading.Event()
    original = e.storage.upload
    llamadas = []

    def upload(*args, **kw):
        llamadas.append(args[0])
        if args[0].endswith("/original"):
            iniciada.set()
            assert liberar.wait(10)
        return original(*args, **kw)

    monkeypatch.setattr(e.storage, "upload", upload)
    e.admision.espera_admision_segundos = 0.03
    with ThreadPoolExecutor(max_workers=1) as pool:
        primera = pool.submit(subir, e, clave="estable")
        try:
            assert iniciada.wait(3)
            respuesta = subir(e, clave="estable")
            datos = error(respuesta, 503, "STORAGE_UNAVAILABLE")
            assert datos["details"] == {"confirmacion": "indeterminada", "idempotency_key_presente": True}
            assert "document_id" not in respuesta.text and "job_id" not in respuesta.text
            assert e.cliente.get("/api/documents", headers=e.headers).json()["items"] == []
            error(subir(e, clave="otra"), 429, "RATE_LIMITED")
            assert len(llamadas) == 1
        finally:
            liberar.set()
        recibo = primera.result(10)
    assert recibo.status_code == 202
    assert subir(e, clave="estable").json() == recibo.json()


@pytest.mark.parametrize("clave", [None, "estable"])
def test_fallo_storage_no_promete_exito_ni_reintento_sin_clave(entorno, monkeypatch, clave):
    e = entorno

    def fallar(*a, **kw):
        raise StorageUnavailable("credencial-privada")

    monkeypatch.setattr(e.storage, "upload", fallar)
    respuesta = subir(e, clave=clave)
    datos = error(respuesta, 503, "STORAGE_UNAVAILABLE")
    assert datos["details"]["idempotency_key_presente"] is (clave is not None)
    assert "credencial-privada" not in respuesta.text
    assert e.doble.llamadas_embeddings == 0
    assert len(e.db.listar_limpiezas_documentales()) == 1


def test_ultima_escritura_oci_indeterminada_conserva_intencion_y_bloquea_lectura(entorno, monkeypatch, tmp_path):
    e = entorno
    original = e.storage.upload

    def fallar(clave, contenido, **kw):
        if clave.endswith("/manifest.json") and json.loads(contenido).get("admision_sqlite_confirmada") is True:
            raise StorageUnavailable("respuesta-perdida")
        return original(clave, contenido, **kw)

    monkeypatch.setattr(e.storage, "upload", fallar)
    error(subir(e, clave="estable"), 503, "STORAGE_UNAVAILABLE")
    fila = dict(e.db._conn.execute("SELECT * FROM document_admissions").fetchone())
    assert fila["estado"] == "encolada" and fila["clave_idempotencia"] == "estable"
    error(e.cliente.get(f"/api/documents/{fila['document_id']}", headers=e.headers), 404, "NOT_FOUND")
    assert e.cliente.get("/api/documents", headers=e.headers).json()["items"] == []
    e.admision.espera_admision_segundos = 0.03
    error(subir(e, clave="estable"), 503, "STORAGE_UNAVAILABLE")
    assert e.db._conn.execute("SELECT COUNT(*) FROM document_admissions").fetchone()[0] == 1
    reconstruida = RegistroOperativo(tmp_path / "reconstruida.db")
    try:
        restaurar_registro(reconstruida, e.storage)
        assert reconstruida.obtener_documento(fila["document_id"]) is None
    finally:
        reconstruida.cerrar()


def test_commit_aplicado_con_error_no_se_compensa_como_fallido(entorno, monkeypatch):
    e = entorno
    original = e.db.confirmar_admision_documental

    def confirmar(*a, **kw):
        original(*a, **kw)
        raise sqlite3.OperationalError("confirmacion-perdida")

    monkeypatch.setattr(e.db, "confirmar_admision_documental", confirmar)
    monkeypatch.setattr(e.db, "fallar_admision_documental", lambda *a, **kw: pytest.fail("No sobrescribir commit"))
    error(subir(e, clave="estable"), 503, "STORAGE_UNAVAILABLE")
    fila = dict(e.db._conn.execute("SELECT * FROM document_admissions").fetchone())
    assert fila["estado"] == "encolada" and not e.db.listar_limpiezas_documentales()
    assert e.doble.llamadas_embeddings == 0


@pytest.mark.parametrize("flag", ["admision_confirmada", "admision_sqlite_confirmada"])
@pytest.mark.parametrize("valor", [None, False])
def test_admision_nueva_exige_ambos_marcadores_true(entorno, flag, valor):
    e = entorno
    datos = listo(e)
    manifest(e, datos["document_id"], **{flag: valor})
    error(e.cliente.get(f"/api/documents/{datos['document_id']}", headers=e.headers), 404, "NOT_FOUND")
    assert e.cliente.get("/api/documents", headers=e.headers).json()["items"] == []


def test_pagina_filtra_lapidas_y_admisiones_sin_saltos_ni_duplicados(entorno):
    e = entorno
    # Orden por fecha primero; IDs deliberadamente fuera de ese orden.
    heredado(e, "z_primero", fecha="2026-09-01T00:00:00+00:00")
    heredado(e, "a_filtrado", borrado=True)
    heredado(e, "b_filtrado", admision_confirmada=True, admision_sqlite_confirmada=False)
    for doc in ("c_segundo", "d_tercero", "e_cuarto"):
        heredado(e, doc)
    uno = e.cliente.get("/api/documents?limit=2", headers=e.headers).json()
    assert [d["document_id"] for d in uno["items"]] == ["z_primero", "c_segundo"]
    cursor = uno["next_cursor"]
    assert cursor and cursor != "c_segundo"
    # El cursor sigue funcionando cuando se retira su documento ancla.
    e.db.agendar_borrado("document", "c_segundo", e.ws["workspace_id"])
    dos = e.cliente.get("/api/documents", params={"limit": 2, "cursor": cursor}, headers=e.headers).json()
    assert [d["document_id"] for d in dos["items"]] == ["d_tercero", "e_cuarto"]
    assert dos["next_cursor"] is None
    assert uno["items"][0]["metadata"] is None and uno["items"][0]["cobertura"] is None


def test_cursor_firmado_ligado_al_workspace_y_persistente(entorno):
    from app.documents.cursor import CursorDocumental

    e = entorno
    heredado(e, "uno")
    heredado(e, "dos")
    cursor = e.cliente.get("/api/documents?limit=1", headers=e.headers).json()["next_cursor"]
    otro = e.cliente.post("/api/workspaces").json()
    for valor, headers in [
        ("uno", e.headers),
        (cursor[:-4] + "AAAA", e.headers),
        (cursor, {"Authorization": f"Bearer {otro['token']}"}),
        ("", e.headers),
    ]:
        error(e.cliente.get("/api/documents", params={"cursor": valor}, headers=headers), 400, "INVALID_REQUEST")
    nueva = CursorDocumental(e.db.ruta_db.parent / "document-cursor.key")
    assert nueva.decodificar(cursor, e.ws["workspace_id"]) == e.consulta.cursor.decodificar(
        cursor, e.ws["workspace_id"]
    )


def test_limit_default20_max100_y_filtrado_extenso(entorno):
    e = entorno
    for i in range(105):
        heredado(e, f"doc_{i:03}", borrado=i < 82)
    pagina = e.cliente.get("/api/documents", headers=e.headers).json()
    assert len(pagina["items"]) == 20 and pagina["next_cursor"]
    assert pagina["items"][0]["document_id"] == "doc_082"
    ultima = e.cliente.get("/api/documents", params={"cursor": pagina["next_cursor"]}, headers=e.headers).json()
    assert len(ultima["items"]) == 3 and ultima["next_cursor"] is None
    assert len(e.cliente.get("/api/documents?limit=100", headers=e.headers).json()["items"]) == 23
    for limit in (0, -1, 101, "no"):
        error(e.cliente.get("/api/documents", params={"limit": limit}, headers=e.headers), 422, "VALIDATION_ERROR")


@pytest.mark.parametrize("retirada", ["sql", "oci", "sesion", "estado"])
def test_fuente_revalida_despues_del_indice(entorno, monkeypatch, retirada):
    e = entorno
    datos = listo(e)
    doc = datos["document_id"]
    chunk = e.consulta.chunks(e.ws["token"], doc)[0]
    original = e.app.state.vectorstore.listar_chunks

    def interceptar(*a, **kw):
        chunks = original(*a, **kw)
        if retirada == "sql":
            e.db.agendar_borrado("document", doc, e.ws["workspace_id"])
        elif retirada == "oci":
            manifest(e, doc, borrado=True)
        elif retirada == "sesion":
            e.db.revocar_sesion(e.ws["token"])
        else:
            e.db.actualizar_documento(doc, DocumentStatus.FAILED)
        return chunks

    monkeypatch.setattr(e.app.state.vectorstore, "listar_chunks", interceptar)
    respuesta = e.cliente.get(f"/api/documents/{doc}/sources/{chunk.chunk_id}", headers=e.headers)
    status, codigo = (
        (401, "SESSION_INVALID")
        if retirada == "sesion"
        else ((409, "INVALID_STATE") if retirada == "estado" else (404, "NOT_FOUND"))
    )
    error(respuesta, status, codigo)
    assert chunk.texto not in respuesta.text


def test_fuente_publica_ubicacion_y_aislamiento_de_chunk(entorno):
    e = entorno
    datos = listo(e)
    chunk = e.consulta.chunks(e.ws["token"], datos["document_id"])[0]
    llamadas = e.doble.llamadas_embeddings
    url = f"/api/documents/{datos['document_id']}/sources/{chunk.chunk_id}"
    respuesta = e.cliente.get(url, headers=e.headers)
    assert respuesta.status_code == 200
    assert respuesta.json() == {
        "document_id": chunk.document_id,
        "chunk_id": chunk.chunk_id,
        "texto": chunk.texto,
        "pagina": chunk.pagina,
        "seccion": chunk.seccion,
        "seccion_id": chunk.seccion_id,
        "source_type": chunk.source_type,
        "interpretacion_modelo": False,
        "imagen_url": None,
    }
    error(e.cliente.get(url.rsplit("/", 1)[0] + "/noexiste", headers=e.headers), 404, "NOT_FOUND")
    assert e.doble.llamadas_embeddings == llamadas


@pytest.mark.parametrize("caso", ["remote_processing", "cov_null", "cov_pending", "tokens_null", "cov_distinta"])
def test_ready_solo_no_habilita_fuentes_sin_cobertura_confirmada(entorno, monkeypatch, caso):
    e = entorno
    datos = listo(e)
    doc = datos["document_id"]
    mf = ManifiestoDocumento.model_validate_json(e.storage.get(clave_documento(e.ws["workspace_id"], doc)))
    if caso == "remote_processing":
        manifest(e, doc, estado=DocumentStatus.PROCESSING)
    elif caso == "cov_null":
        manifest(e, doc, cobertura=None)
    elif caso == "cov_pending":
        manifest(e, doc, cobertura=mf.cobertura.model_copy(update={"paginas_visuales_pendientes": [1]}))
    elif caso == "tokens_null":
        manifest(e, doc, metadatos=mf.metadatos.model_copy(update={"cantidad_tokens": None}))
    else:
        manifest(e, doc, cobertura=mf.cobertura.model_copy(update={"detalle": "otra version"}))
    monkeypatch.setattr(e.app.state.vectorstore, "listar_chunks", lambda *a, **kw: pytest.fail("No autorizado"))
    status, codigo = (503, "STORAGE_UNAVAILABLE") if caso == "cov_pending" else (409, "INVALID_STATE")
    error(e.cliente.get(f"/api/documents/{doc}/sources/c", headers=e.headers), status, codigo)


def test_vision_pendiente_completed_no_ready_y_metadata_conservada(entorno):
    e = entorno
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.showPage()
    pdf.save()
    respuesta = e.cliente.post(UPLOAD, files={"file": ("escaneo.pdf", buffer.getvalue())}, headers=e.headers)
    assert respuesta.status_code == 202
    datos = respuesta.json()
    terminado(e, datos)
    detalle = e.cliente.get(f"/api/documents/{datos['document_id']}", headers=e.headers).json()
    assert detalle["status"] == "processing"
    assert detalle["metadata"]["cantidad_tokens"] == 0
    assert detalle["cobertura"]["paginas_visuales_pendientes"] == [1]
    assert detalle["cobertura"]["secciones_visuales_pendientes"] is None
    assert detalle["cobertura"]["secciones_omitidas"] is None
    error(e.cliente.get(f"/api/documents/{datos['document_id']}/sources/c", headers=e.headers), 409, "INVALID_STATE")


@pytest.mark.e2e
def test_fallo_parser_trabajo_failed_y_error_seguro(entorno, pdf_corrupto):
    e = entorno
    respuesta = e.cliente.post(UPLOAD, files={"file": ("a.pdf", pdf_corrupto.read_bytes())}, headers=e.headers)
    assert respuesta.status_code == 202
    datos = respuesta.json()
    trabajo = terminado(e, datos, "failed")
    assert trabajo["error"]["code"] == "PARSER_CORRUPTO"
    detalle = e.cliente.get(f"/api/documents/{datos['document_id']}", headers=e.headers).json()
    assert detalle["status"] == "failed" and detalle["error"] is not None


@pytest.mark.parametrize("ruta", ["lista", "detalle", "fuente"])
def test_error_storage_lectura_sin_fallback(entorno, monkeypatch, ruta):
    e = entorno
    datos = listo(e)
    url = "/api/documents" if ruta == "lista" else f"/api/documents/{datos['document_id']}"
    if ruta == "fuente":
        url += "/sources/c"

    def fallar(*a, **kw):
        raise StorageUnavailable("texto-privado")

    monkeypatch.setattr(e.storage, "get", fallar)
    respuesta = e.cliente.get(url, headers=e.headers)
    error(respuesta, 503, "STORAGE_UNAVAILABLE")
    assert "texto-privado" not in respuesta.text


def test_cuotas_configuradas_activan_runtime_comun_sin_valores_implicitos(tmp_path):
    ajustes = Configuracion(
        app_env="test",
        mock_oci=True,
        mock_gemini=True,
        data_dir=str(tmp_path),
        embedding_dimensions=16,
        embedding_quota_rpm=12,
        embedding_quota_tpm=10000,
        embedding_quota_rpd=30,
    )
    app = crear_app(ajustes, proveedor_embeddings=DobleGemini(16))
    with TestClient(app):
        assert app.state.documents_admission.gestor is app.state.jobs_manager
        assert app.state.jobs_manager.cuotas.disponibles_hoy(ajustes.gemini_embedding_model) == 30
    with pytest.raises(ValidationError, match="juntas"):
        Configuracion(embedding_quota_rpm=1, _env_file=None)


def test_sin_cuotas_no_admite_pero_lista_vacia_disponible(tmp_path):
    _peticiones_globales.clear()
    _peticiones_ip.clear()
    ajustes = Configuracion(app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path))
    app = crear_app(ajustes)
    with TestClient(app) as cliente:
        ws = cliente.post("/api/workspaces").json()
        headers = {"Authorization": f"Bearer {ws['token']}"}
        assert cliente.get("/api/documents", headers=headers).json() == {"items": [], "next_cursor": None}
        error(
            cliente.post(UPLOAD, json={"documento_titulo": "A", "documento_contenido": TEXTO}, headers=headers),
            503,
            "PROVIDER_UNAVAILABLE",
        )
        assert not app.state.db.listar_documentos(ws["workspace_id"])


def test_fuentes_pdf_conservan_pagina_y_metadata_publica_no_interna(entorno, crear_pdf):
    e = entorno
    respuesta = e.cliente.post(
        UPLOAD, files={"file": ("redes.pdf", crear_pdf(paginas=4).read_bytes())}, headers=e.headers
    )
    assert respuesta.status_code == 202
    datos = respuesta.json()
    terminado(e, datos)
    doc = datos["document_id"]
    chunk = e.consulta.chunks(e.ws["token"], doc)[0]
    fuente = e.cliente.get(f"/api/documents/{doc}/sources/{chunk.chunk_id}", headers=e.headers).json()
    assert fuente["pagina"] == chunk.pagina and chunk.pagina >= 1
    metadata = e.cliente.get(f"/api/documents/{doc}", headers=e.headers).json()["metadata"]
    assert set(metadata) == {"extension", "tamano_bytes", "creado_en", "cantidad_paginas", "cantidad_tokens"}
    assert metadata["cantidad_paginas"] == 4


@pytest.mark.parametrize("caso", ["inconsistente", "deadline", "coverage_corrupta"])
def test_fallo_indice_o_cobertura_no_expone_fuente(entorno, monkeypatch, caso):
    from app.core.rag.vectorstore import IndiceInconsistenteError
    from app.jobs.manager import DeadlineExcedidoError

    e = entorno
    doc = listo(e)["document_id"]
    if caso == "coverage_corrupta":
        with e.db.transaccion():
            e.db._conn.execute("UPDATE documents SET cobertura_json='{} invalido' WHERE document_id=?", (doc,))
        monkeypatch.setattr(
            e.app.state.vectorstore, "listar_chunks", lambda *a, **kw: pytest.fail("No consultar índice")
        )
    else:

        def fallar(*a, **kw):
            raise IndiceInconsistenteError("privado") if caso == "inconsistente" else DeadlineExcedidoError()

        monkeypatch.setattr(e.app.state.vectorstore, "listar_chunks", fallar)
    status, codigo = (409, "INVALID_STATE") if caso == "inconsistente" else (503, "STORAGE_UNAVAILABLE")
    respuesta = e.cliente.get(f"/api/documents/{doc}/sources/c", headers=e.headers)
    error(respuesta, status, codigo)
    assert "privado" not in respuesta.text


def test_manifest_nuevo_sin_admision_no_se_trata_como_legacy(entorno):
    e = entorno
    heredado(e, "nuevo_sin_registro", admision_confirmada=True, admision_sqlite_confirmada=True)
    error(e.cliente.get("/api/documents/nuevo_sin_registro", headers=e.headers), 404, "NOT_FOUND")
    assert e.cliente.get("/api/documents", headers=e.headers).json()["items"] == []


def test_retiro_durante_revalidacion_de_pagina_no_pierde_lookahead(entorno, monkeypatch):
    e = entorno
    for doc in ("a", "b", "c", "d"):
        heredado(e, doc)
    original = e.consulta.detalle
    llamadas = 0

    def interceptar(token, doc):
        nonlocal llamadas
        llamadas += 1
        # Página de dos: después de reunir a/b/c, retirar a al revalidar.
        if llamadas == 4:
            e.db.agendar_borrado("document", "a", e.ws["workspace_id"])
        return original(token, doc)

    monkeypatch.setattr(e.consulta, "detalle", interceptar)
    uno = e.cliente.get("/api/documents?limit=2", headers=e.headers).json()
    assert [d["document_id"] for d in uno["items"]] == ["b", "c"]
    dos = e.cliente.get("/api/documents", params={"limit": 2, "cursor": uno["next_cursor"]}, headers=e.headers).json()
    assert [d["document_id"] for d in dos["items"]] == ["d"]


def test_clave_cursor_truncada_falla_sin_rotar_ni_ocultar(tmp_path):
    from app.documents.cursor import CursorDocumental

    ruta = tmp_path / "document-cursor.key"
    ruta.write_bytes(b"no valida")
    with pytest.raises(StorageUnavailable):
        CursorDocumental(ruta)
    assert ruta.read_bytes() == b"no valida"


@pytest.mark.parametrize("flag", ["admision_confirmada", "admision_sqlite_confirmada"])
@pytest.mark.parametrize("valor", ["true", 1])
def test_marcadores_no_booleanos_no_acreditan_confirmacion(entorno, flag, valor):
    e = entorno
    doc = listo(e)["document_id"]
    clave = clave_documento(e.ws["workspace_id"], doc)
    contenido = json.loads(e.storage.get(clave))
    contenido[flag] = valor
    e.storage.upload(clave, json.dumps(contenido).encode(), content_type="application/json")
    error(e.cliente.get(f"/api/documents/{doc}", headers=e.headers), 503, "STORAGE_UNAVAILABLE")


def test_fuente_pdf_exige_cobertura_de_todas_las_paginas(entorno, crear_pdf, monkeypatch):
    e = entorno
    respuesta = e.cliente.post(
        UPLOAD, files={"file": ("redes.pdf", crear_pdf(paginas=4).read_bytes())}, headers=e.headers
    )
    datos = respuesta.json()
    terminado(e, datos)
    doc = datos["document_id"]
    mf = ManifiestoDocumento.model_validate_json(e.storage.get(clave_documento(e.ws["workspace_id"], doc)))
    cobertura = mf.cobertura.model_copy(update={"paginas_procesadas": [1]})
    manifest(e, doc, cobertura=cobertura)
    e.db.guardar_metadata_documental(e.ws["workspace_id"], doc, metadatos=mf.metadatos, cobertura=cobertura)
    monkeypatch.setattr(e.app.state.vectorstore, "listar_chunks", lambda *a, **kw: pytest.fail("No autorizado"))
    error(e.cliente.get(f"/api/documents/{doc}/sources/c", headers=e.headers), 409, "INVALID_STATE")


def test_resultado_sqlite_no_verificable_preserva_intencion_y_diagnostico(entorno, monkeypatch, caplog):
    e = entorno
    confirmar, obtener = e.db.confirmar_admision_documental, e.db.obtener_admision_documental
    fallo = False
    original = sqlite3.OperationalError("privado-original")

    def commit(*a, **kw):
        nonlocal fallo
        confirmar(*a, **kw)
        fallo = True
        raise original

    def leer(*a, **kw):
        if fallo:
            raise sqlite3.OperationalError("privado-secundario")
        return obtener(*a, **kw)

    monkeypatch.setattr(e.db, "confirmar_admision_documental", commit)
    monkeypatch.setattr(e.db, "obtener_admision_documental", leer)
    monkeypatch.setattr(
        e.db, "fallar_admision_documental", lambda *a, **kw: pytest.fail("No compensar resultado desconocido")
    )
    respuesta = subir(e, clave="estable")
    error(respuesta, 503, "STORAGE_UNAVAILABLE")
    assert e.db._conn.execute("SELECT estado FROM document_admissions").fetchone()[0] == "encolada"
    assert "resultado_sqlite_no_verificable" in caplog.text and original.__notes__
    assert "privado-original" not in caplog.text + respuesta.text
    assert "privado-secundario" not in caplog.text + respuesta.text + " ".join(original.__notes__)


def test_legacy_sin_cobertura_se_lista_pero_no_habilita_contenido(entorno, monkeypatch):
    e = entorno
    heredado(e, "legacy", estado=DocumentStatus.READY)
    e.db.actualizar_documento("legacy", DocumentStatus.READY)
    assert e.cliente.get("/api/documents/legacy", headers=e.headers).status_code == 200
    monkeypatch.setattr(e.app.state.vectorstore, "listar_chunks", lambda *a, **kw: pytest.fail("Cobertura desconocida"))
    error(e.cliente.get("/api/documents/legacy/sources/c", headers=e.headers), 409, "INVALID_STATE")


def test_ejemplos_documentales_tipados_validan_con_schemas_publicos():
    import re
    from pathlib import Path

    from app.schemas.documents_http import DocumentoResponse, UploadDocumentoResponse

    doc = Path(__file__).resolve().parents[2] / "docs" / "contratos-api.md"
    modelos = {"DocumentoResponse": DocumentoResponse, "UploadDocumentoResponse": UploadDocumentoResponse}
    bloques = re.findall(r"```json (\w+)\n(.*?)```", doc.read_text(encoding="utf-8"), re.S)
    assert {nombre for nombre, _ in bloques} == set(modelos)
    for nombre, bloque in bloques:
        modelos[nombre].model_validate_json(bloque)


def test_lifespan_libera_chroma_y_reabre_datos_sin_recalcular(tmp_path):
    from chromadb.api.shared_system_client import SharedSystemClient

    _peticiones_globales.clear()
    _peticiones_ip.clear()
    ajustes = Configuracion(
        app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path), embedding_dimensions=16
    )
    doble = DobleGemini(16)
    cuotas = CuotasProveedor({ajustes.gemini_embedding_model: CuotasModelo(1000, 1000000, 1000)})
    primera = crear_app(ajustes, proveedor_embeddings=doble, cuotas_proveedor=cuotas)
    with TestClient(primera) as cliente:
        ws = cliente.post("/api/workspaces").json()
        headers = {"Authorization": f"Bearer {ws['token']}"}
        datos = cliente.post(
            UPLOAD, headers=headers, json={"documento_titulo": "Redes", "documento_contenido": TEXTO}
        ).json()
        e = SimpleNamespace(cliente=cliente, headers=headers)
        terminado(e, datos)
        indice = primera.state.vectorstore
        identificador = indice._cliente._identifier
        assert identificador in SharedSystemClient._identifier_to_system
        chunk = primera.state.documents_query.chunks(ws["token"], datos["document_id"])[0]
        cursor = primera.state.documents_query.cursor.codificar(
            ws["workspace_id"], "2026-10-01T00:00:00+00:00", "doc_ancla"
        )
    assert identificador not in SharedSystemClient._identifier_to_system
    indice.cerrar()  # Cierre repetido no destruye ni reabre datos.
    llamadas = doble.llamadas_embeddings
    segunda = crear_app(ajustes, proveedor_embeddings=doble, cuotas_proveedor=cuotas)
    with TestClient(segunda) as cliente:
        respuesta = cliente.get(f"/api/documents/{datos['document_id']}/sources/{chunk.chunk_id}", headers=headers)
        assert respuesta.status_code == 200 and respuesta.json()["texto"] == chunk.texto
        assert cliente.get("/api/documents", headers=headers, params={"cursor": cursor}).status_code == 200
        assert doble.llamadas_embeddings == llamadas
    assert identificador not in SharedSystemClient._identifier_to_system


@pytest.mark.parametrize("ruta", ["lista", "detalle", "fuente"])
def test_revocacion_durante_storage_devuelve401_sin_contenido(entorno, monkeypatch, ruta):
    e = entorno
    datos = listo(e)
    doc = datos["document_id"]
    original = e.storage.get
    clave = clave_documento(e.ws["workspace_id"], doc)

    def interceptar(nombre, **kw):
        contenido = original(nombre, **kw)
        if nombre == clave:
            e.db.revocar_sesion(e.ws["token"])
        return contenido

    monkeypatch.setattr(e.storage, "get", interceptar)
    url = "/api/documents" if ruta == "lista" else f"/api/documents/{doc}"
    if ruta == "fuente":
        url += "/sources/c"
    error(e.cliente.get(url, headers=e.headers), 401, "SESSION_INVALID")
