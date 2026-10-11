"""#30: worker real, Gemini doble, Chroma físico y páginas PDF originales."""

import hashlib
import io
import json
import time
from types import SimpleNamespace

import pytest
from app.config import Configuracion
from app.core.rag.rebuild import ManifiestoDocumento, reconstruir_workspace
from app.jobs.manager import ContextoEjecucion, CuotasModelo, CuotasProveedor
from app.main import crear_app
from app.storage.object_keys import clave_documento, clave_original
from doubles.gemini import DobleGemini
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

pytestmark = pytest.mark.integration_mock


def respuesta_visual(**cambios):
    return json.dumps(
        {
            "estado": "interpretada",
            "cobertura_completa": True,
            "limitaciones": [],
            "descripcion": "La subred privada 10.0.2.0/24 carece de IP pública. El diagrama muestra salida hacia NAT Gateway.",
            **cambios,
        }
    )


@pytest.fixture
def entorno(tmp_path, mock_storage, aislar_estado_seguridad):
    cfg = Configuracion(
        app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path / "base"), embedding_dimensions=16
    )
    embeddings, vision = DobleGemini(16), DobleGemini(16)
    cuotas = CuotasProveedor(
        {
            modelo: CuotasModelo(1000, 1_000_000, 1000)
            for modelo in (cfg.gemini_embedding_model, cfg.gemini_generation_model)
        }
    )
    app = crear_app(cfg, proveedor_embeddings=embeddings, proveedor_vision=vision, cuotas_proveedor=cuotas)
    app.state.storage_provider = mock_storage
    with TestClient(app) as cliente:
        ws = cliente.post("/api/workspaces").json()
        yield SimpleNamespace(
            app=app,
            cliente=cliente,
            ws=ws,
            cfg=cfg,
            storage=mock_storage,
            vision=vision,
            embeddings=embeddings,
            cuotas=cuotas,
            headers={"Authorization": f"Bearer {ws['token']}"},
        )


def hasta(condicion):
    limite = time.monotonic() + 20
    while time.monotonic() < limite:
        if resultado := condicion():
            return resultado
        time.sleep(0.005)
    pytest.fail("El trabajo no alcanzó un estado terminal")


def subir(e, original, *, respuesta=None, terminal="completed"):
    e.vision.programar_revision(respuesta or respuesta_visual())
    upload = e.cliente.post("/api/documents/upload", files={"file": ("redes.pdf", original)}, headers=e.headers)
    assert upload.status_code == 202, upload.text
    datos = upload.json()

    def final():
        job = e.app.state.db.obtener_job(datos["job_id"])
        if job["status"] not in ("running", "queued"):
            assert job["status"] == terminal, job
            return job

    job = hasta(final)
    return datos, job


def manifest(e, document_id):
    return ManifiestoDocumento.model_validate_json(e.storage.get(clave_documento(e.ws["workspace_id"], document_id)))


def lectura(e):
    return ContextoEjecucion("lectura", e.ws["workspace_id"], "consulta", time.monotonic() + 30, cuotas=e.cuotas)


def test_vcn_ready_solo_con_vision_fuente_original_filtros_y_aislamiento(entorno, documento_demo_vcn):
    e = entorno
    original = documento_demo_vcn.read_bytes()
    datos, job = subir(e, original)
    assert job["resultado"] and e.vision.llamadas_verificacion == 1
    mf = manifest(e, datos["document_id"])
    assert mf.estado == "ready" and not mf.cobertura.paginas_visuales_pendientes
    assert mf.cobertura.paginas_procesadas == list(range(1, mf.metadatos.cantidad_paginas + 1))
    assert len(mf.interpretaciones_visuales) == 1 and mf.interpretaciones_visuales[0].pagina == 3
    assert mf.interpretaciones_visuales[0].hash_sha256 == hashlib.sha256(original).hexdigest()
    assert e.storage.get(clave_original(e.ws["workspace_id"], mf.document_id)) == original
    chunks = e.app.state.documents_query.chunks(e.ws["token"], mf.document_id)
    visual = next(c for c in chunks if c.source_type == "image_description")
    assert visual.es_diagrama and visual.pagina == 3
    assert any(c.source_type == "pdf" and not c.es_diagrama for c in chunks)
    fuente = e.cliente.get(f"/api/documents/{mf.document_id}/sources/{visual.chunk_id}", headers=e.headers).json()
    assert fuente["interpretacion_modelo"] and fuente["source_type"] == "image_description"
    imagen = e.cliente.get(fuente["imagen_url"], headers=e.headers)
    assert imagen.status_code == 200 and imagen.headers["cache-control"] == "no-store"
    assert hashlib.sha256(imagen.content).hexdigest() == mf.interpretaciones_visuales[0].imagen_sha256
    assert imagen.content == e.vision.entradas_verificacion[0]["imagen_original"]
    assert e.cliente.get(fuente["imagen_url"]).status_code == 401
    otro = e.cliente.post("/api/workspaces").json()
    assert e.cliente.get(fuente["imagen_url"], headers={"Authorization": f"Bearer {otro['token']}"}).status_code == 404
    for tipo in ("pdf", "image_description"):
        hits = e.app.state.vectorstore.buscar(
            e.ws["workspace_id"], mf.document_id, "NAT", contexto=lectura(e), tipo_fuente=tipo
        )
        assert hits and all(h.chunk.source_type == tipo for h in hits)
    loader = e.app.state.documents_query.loader_critic(lectura(e))
    assert loader("otro", mf.document_id, mf.hash_sha256, visual.chunk_id) is None
    prueba = loader(e.ws["workspace_id"], mf.document_id, "sha256:" + mf.hash_sha256, visual.chunk_id)
    assert prueba.contenido == imagen.content and not prueba.interpretacion_ambigua


def escaneo():
    imagen = Image.new("RGB", (700, 500), "white")
    ImageDraw.Draw(imagen).text((50, 100), "Subred privada sin IP publica. Salida via NAT.", fill="black", font_size=24)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer)
    pdf.drawImage(ImageReader(imagen), 40, 200, width=500, height=400)
    pdf.save()
    imagen.close()
    return buffer.getvalue()


def test_escaneo_legible_completa_cobertura_sin_fabricar_texto_original(entorno):
    e = entorno
    datos, _ = subir(e, escaneo())
    mf = manifest(e, datos["document_id"])
    assert mf.estado == "ready" and mf.cobertura.paginas_procesadas == [1]
    chunks = e.app.state.documents_query.chunks(e.ws["token"], mf.document_id)
    assert chunks and all(c.source_type == "image_description" and c.es_diagrama for c in chunks)


@pytest.mark.parametrize(
    "respuesta",
    [
        respuesta_visual(
            estado="ilegible", descripcion="", cobertura_completa=False, limitaciones=["Leyendas ilegibles"]
        ),
        respuesta_visual(cobertura_completa=False, limitaciones=["Contenido parcialmente omitido"]),
    ],
)
def test_omision_visual_se_informa_y_bloquea_ready(entorno, respuesta):
    e = entorno
    datos, job = subir(e, escaneo(), respuesta=respuesta, terminal="failed")
    assert job["error_code"] == "INGESTION_VISION_OMITIDA"
    hasta(lambda: e.app.state.db.obtener_documento(datos["document_id"])["estado"] == "failed")
    mf = manifest(e, datos["document_id"])
    assert mf.cobertura.paginas_omitidas == [1] and mf.interpretaciones_visuales[0].limitaciones
    assert e.app.state.vectorstore.contar(e.ws["workspace_id"], mf.document_id, contexto=lectura(e)) == 0


def test_respuesta_invalida_no_publica_ni_filtra_payload(entorno):
    datos, job = subir(entorno, escaneo(), respuesta='{"clave_privada":"no-publicar"}', terminal="failed")
    assert job["error_code"] == "INGESTION_VISION" and "no-publicar" not in job["error_message"]
    assert entorno.embeddings.llamadas_embeddings == 0


@pytest.mark.parametrize("accion", ["cancelar", "retirar", "cuota"])
def test_vision_obedece_cuotas_cancelacion_y_tombstones(entorno, accion):
    e = entorno
    llamar = e.vision.verificar_visual_sync
    if accion == "cuota":
        e.cuotas._cuotas[e.cfg.gemini_generation_model].rpd = 0
    else:

        def interrumpir(*args, **kwargs):
            db = e.app.state.db
            job = db._conn.execute("SELECT * FROM jobs WHERE status='running'").fetchone()
            if accion == "cancelar":
                e.app.state.jobs_manager.cancelar(job["job_id"])
            else:
                db.retirar_documento(e.ws["token"], job["document_id"])
            return llamar(*args, **kwargs)

        e.vision.verificar_visual_sync = interrumpir
    datos, job = subir(e, escaneo(), terminal="failed" if accion == "cuota" else "cancelled")
    assert job["resultado"] is None and e.embeddings.llamadas_embeddings == 0
    assert e.vision.llamadas_verificacion == (0 if accion == "cuota" else 1)
    if accion == "retirar":
        assert e.app.state.db.obtener_documento(datos["document_id"]) is None


def test_ambiguedad_persistida_no_es_evidencia_visual_suficiente(entorno):
    e = entorno
    datos, _ = subir(
        e, escaneo(), respuesta=respuesta_visual(estado="ambigua", limitaciones=["Flecha sin dirección verificable"])
    )
    mf = manifest(e, datos["document_id"])
    chunk = e.app.state.documents_query.chunks(e.ws["token"], mf.document_id)[0]
    imagen = e.app.state.documents_query.loader_critic(lectura(e))(
        e.ws["workspace_id"], mf.document_id, mf.hash_sha256, chunk.chunk_id
    )
    assert imagen.interpretacion_ambigua and "Interpretación ambigua" in chunk.texto


def test_reconstruccion_conserva_interpretaciones_verificadas_sin_llamar_vision(entorno):
    e = entorno
    datos, _ = subir(e, escaneo())
    mf = manifest(e, datos["document_id"])
    antes = e.app.state.documents_query.chunks(e.ws["token"], mf.document_id)
    restaurados = reconstruir_workspace(e.storage, e.app.state.vectorstore, contexto=lectura(e))
    assert restaurados[0].estado == "ready" and not restaurados[0].requiere_vision
    assert e.app.state.documents_query.chunks(e.ws["token"], mf.document_id) == antes
    assert e.vision.llamadas_verificacion == 1


def test_original_cambiado_bloquea_imagen(entorno, monkeypatch):
    from app.documents import visual

    e = entorno
    datos, _ = subir(e, escaneo())
    chunk = e.app.state.documents_query.chunks(e.ws["token"], datos["document_id"])[0]
    render = visual.rasterizar_pagina

    def cambiar(*args, **kwargs):
        imagen = render(*args, **kwargs)
        e.storage.upload(clave_original(e.ws["workspace_id"], datos["document_id"]), b"fuente cambiada")
        return imagen

    monkeypatch.setattr(visual, "rasterizar_pagina", cambiar)
    respuesta = e.cliente.get(
        f"/api/documents/{datos['document_id']}/sources/{chunk.chunk_id}/image", headers=e.headers
    )
    assert respuesta.status_code == 503 and not respuesta.content.startswith(b"\x89PNG")


def test_sqlite_nueva_reusa_descripcion_verificada_y_conserva_citas(entorno, tmp_path):
    e = entorno
    datos, _ = subir(e, escaneo())
    antes = e.app.state.documents_query.chunks(e.ws["token"], datos["document_id"])
    cfg = e.cfg.model_copy(update={"data_dir": str(tmp_path / "restaurada")})
    vision = DobleGemini(16)
    app = crear_app(cfg, proveedor_embeddings=DobleGemini(16), proveedor_vision=vision, cuotas_proveedor=e.cuotas)
    app.state.storage_provider = e.storage
    with TestClient(app) as cliente:
        recuperada = cliente.post("/api/sessions/recover", json={"recovery_code": e.ws["recovery_code"]})
        assert recuperada.status_code == 201, recuperada.text
        token = recuperada.json()["token"]
        hasta(lambda: (d := app.state.db.obtener_documento(datos["document_id"])) and d["estado"] == "ready")
        assert app.state.documents_query.chunks(token, datos["document_id"]) == antes
        assert vision.llamadas_verificacion == 0
