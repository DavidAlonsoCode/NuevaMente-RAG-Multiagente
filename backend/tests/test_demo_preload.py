"""#49: precarga real de archivos, OCI/Gemini explícitamente dobles."""

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.api.security import _peticiones_globales, _peticiones_ip
from app.config import Configuracion
from app.core.rag.multimodal import InterpretadorVisual
from app.core.rag.rebuild import ManifiestoDocumento
from app.core.rag.vectorstore import DEMO_WORKSPACE_ID
from app.documents.preload import FUENTES_DEMO, PrecargadorDemo, leer_fuentes
from app.documents.preload_cli import ejecutar, main
from app.jobs.manager import ContextoEjecucion, CuotaAgotadaError, CuotasModelo, CuotasProveedor, TrabajoCanceladoError
from app.jobs.quotas import cuotas_configuradas
from app.jobs.runtime_lock import exclusividad_runtime
from app.main import crear_app
from app.schemas.enums import DocumentStatus
from app.storage.object_keys import clave_demo
from app.storage.provider import StorageConflict, StorageUnavailable
from doubles.gemini import DobleGemini
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration_mock
DOCUMENTS = Path(__file__).resolve().parents[2] / "documents"
DESCRIPCION = json.dumps(
    {
        "estado": "interpretada",
        "descripcion": "Una VCN contiene subredes y gateways.",
        "cobertura_completa": True,
        "limitaciones": [],
    }
)


def config(tmp_path):
    return Configuracion(
        _env_file=None,
        app_env="test",
        mock_oci=True,
        mock_gemini=True,
        data_dir=str(tmp_path),
        embedding_dimensions=16,
        embedding_quota_rpm=1000,
        embedding_quota_tpm=1000000,
        embedding_quota_rpd=1000,
        vision_quota_rpm=1000,
        vision_quota_tpm=1000000,
        vision_quota_rpd=1000,
    )


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    _peticiones_globales.clear()
    _peticiones_ip.clear()
    monkeypatch.setattr("app.main.SincronizadorManifiestos._ejecutar", lambda self: self._stop.wait())
    cfg = config(tmp_path)
    doble = DobleGemini(16)
    cuotas = CuotasProveedor(cuotas_configuradas(cfg))
    app = crear_app(cfg, proveedor_embeddings=doble, proveedor_vision=doble, cuotas_proveedor=cuotas)
    with TestClient(app) as cliente:
        admision = cliente.post("/api/workspaces")
        assert admision.status_code == 201, admision.text
        ws = admision.json()
        yield SimpleNamespace(
            app=app,
            cliente=cliente,
            doble=doble,
            cuotas=cuotas,
            storage=app.state.storage_provider,
            indice=app.state.vectorstore,
            headers={"Authorization": f"Bearer {ws['token']}"},
            preparador=PrecargadorDemo(
                app.state.storage_provider,
                app.state.vectorstore,
                vision=InterpretadorVisual(doble, modelo=cfg.gemini_generation_model, mock=True),
            ),
        )


def ctx(e, **kwargs):
    return ContextoEjecucion(
        "mantenimiento-test", DEMO_WORKSPACE_ID, "precarga_demo", time.monotonic() + 60, cuotas=e.cuotas, **kwargs
    )


def preparar(e, numero=1):
    fuente, contenido = leer_fuentes(DOCUMENTS)[numero]
    return e.preparador.preparar(ctx(e), fuente, contenido)


def manifest(e, numero=1):
    return ManifiestoDocumento.model_validate_json(
        e.storage.get(clave_demo(f"{FUENTES_DEMO[numero].document_id}/manifest.json"))
    )


def detalle(e, numero=1):
    return e.cliente.get("/api/demo/documents/" + FUENTES_DEMO[numero].document_id, headers=e.headers)


def test_tres_fuentes_publicadas_vcn_visual_y_sin_resultados_inventados(entorno):
    e = entorno
    e.doble.programar_revision(DESCRIPCION)
    for f, contenido in leer_fuentes(DOCUMENTS):
        assert e.preparador.preparar(ctx(e), f, contenido)["estado"] == "ready"
    r = e.cliente.get("/api/demo/documents", headers=e.headers)
    assert r.status_code == 200, r.text
    assert len(r.json()["items"]) == 3
    assert all(d["status"] == "ready" for d in r.json()["items"])
    mf = manifest(e, 0)
    chunks = e.indice.listar_chunks_demo(
        mf.document_id, contexto=ctx(e), source_hash=mf.hash_sha256, publicacion=mf.publicacion_demo
    )
    visual = next(ch for ch in chunks if ch.source_type == "image_description")
    r = e.cliente.get(f"/api/demo/documents/{mf.document_id}/sources/{visual.chunk_id}", headers=e.headers)
    assert r.status_code == 200, r.text
    assert r.json()["interpretacion_modelo"] is True and r.json()["pagina"] == 3
    assert r.json()["texto"].startswith("Interpretación del modelo")
    with e.app.state.db._lock:
        assert e.app.state.db._conn.execute("SELECT count(*) FROM generations").fetchone()[0] == 0
        assert e.app.state.db._conn.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    assert not e.storage.list("generated_content/").items


def test_recarga_reutiliza_indice_y_descripcion_sin_llm(entorno):
    e = entorno
    e.doble.programar_revision(DESCRIPCION)
    for f, contenido in leer_fuentes(DOCUMENTS):
        e.preparador.preparar(ctx(e), f, contenido)
    llamadas = e.doble.llamadas_embeddings, e.doble.llamadas_verificacion
    for f, contenido in leer_fuentes(DOCUMENTS):
        assert e.preparador.preparar(ctx(e), f, contenido)["reutilizado"]
    assert (e.doble.llamadas_embeddings, e.doble.llamadas_verificacion) == llamadas
    assert detalle(e, 0).json()["status"] == "ready"


def test_vision_ausente_conserva_processing_sin_publicacion(entorno):
    e = entorno
    e.preparador.vision = None
    assert preparar(e, 0)["vision_pendiente"]
    mf = manifest(e, 0)
    assert mf.estado == DocumentStatus.PROCESSING and mf.publicacion_demo is None
    assert mf.cobertura.paginas_visuales_pendientes == [3]
    assert detalle(e, 0).json()["status"] == "processing"
    assert e.doble.llamadas_embeddings == e.doble.llamadas_verificacion == 0


def test_vision_incompleta_no_publica_ready(entorno):
    e = entorno
    e.doble.programar_revision(
        json.dumps(
            {"estado": "ilegible", "descripcion": "", "cobertura_completa": False, "limitaciones": ["Texto ilegible."]}
        )
    )
    with pytest.raises(ValueError, match="cobertura"):
        preparar(e, 0)
    assert manifest(e, 0).publicacion_demo is None
    assert detalle(e, 0).json()["status"] == "processing"
    assert e.doble.llamadas_embeddings == 0


@pytest.mark.parametrize("retirada", ["manifest", "indice"])
def test_lapidas_no_reaparecen(entorno, retirada):
    e = entorno
    preparar(e)
    mf = manifest(e)
    clave = clave_demo(f"{mf.document_id}/manifest.json")
    if retirada == "manifest":
        e.storage.upload(clave, mf.model_copy(update={"borrado": True}).model_dump_json())
    else:
        e.indice._borrar(e.indice._demo, DEMO_WORKSPACE_ID, mf.document_id)
    llamadas = e.doble.llamadas_embeddings
    with pytest.raises((ValueError, TrabajoCanceladoError)):
        preparar(e)
    assert detalle(e).status_code == 404
    assert e.doble.llamadas_embeddings == llamadas


def test_original_alterado_no_se_sobrescribe(entorno):
    e = entorno
    preparar(e)
    clave = clave_demo(f"{FUENTES_DEMO[1].document_id}/original")
    e.storage.upload(clave, b"contenido ajeno")
    with pytest.raises(ValueError, match="original"):
        preparar(e)
    assert e.storage.get(clave) == b"contenido ajeno"
    assert detalle(e).status_code == 503


def test_fuente_nueva_requiere_id_nuevo(entorno):
    e = entorno
    preparar(e)
    fuente, contenido = leer_fuentes(DOCUMENTS)[1]
    mf = manifest(e)
    with pytest.raises(ValueError, match="fuente distinta"):
        e.preparador.preparar(ctx(e), fuente, contenido + b"\nCambio.")
    assert manifest(e) == mf


def test_contexto_privado_rechazado_antes_de_escribir(entorno):
    e = entorno
    contexto = ctx(e)
    contexto.workspace_id = "privado"
    f, b = leer_fuentes(DOCUMENTS)[1]
    with pytest.raises(PermissionError):
        e.preparador.preparar(contexto, f, b)
    assert not e.storage.list("demo/").items


def test_etag_cambia_durante_embeddings_no_publica(entorno, monkeypatch):
    e = entorno
    original = e.doble.embed_sync

    def cambiado(textos, *, timeout):
        mf = manifest(e)
        e.storage.upload(
            clave_demo(f"{mf.document_id}/manifest.json"), mf.model_copy(update={"borrado": True}).model_dump_json()
        )
        return original(textos, timeout=timeout)

    monkeypatch.setattr(e.doble, "embed_sync", cambiado)
    with pytest.raises(StorageConflict):
        preparar(e)
    assert manifest(e).borrado and manifest(e).publicacion_demo is None
    assert detalle(e).status_code == 404


def test_publicacion_falla_reintento_reutiliza_indice(entorno, monkeypatch):
    e = entorno
    upload = e.storage.upload

    def fallo(clave, contenido, **kwargs):
        if clave.endswith("manifest.json") and json.loads(contenido)["estado"] == "ready":
            raise StorageUnavailable("sin confirmación")
        return upload(clave, contenido, **kwargs)

    monkeypatch.setattr(e.storage, "upload", fallo)
    with pytest.raises(StorageUnavailable):
        preparar(e)
    assert detalle(e).json()["status"] == "processing"
    llamadas = e.doble.llamadas_embeddings
    monkeypatch.setattr(e.storage, "upload", upload)
    assert preparar(e)["reutilizado"]
    assert e.doble.llamadas_embeddings == llamadas
    assert detalle(e).json()["status"] == "ready"


@pytest.mark.parametrize("cambio", ["imagen", "descripcion"])
def test_interpretacion_alterada_bloquea_fuentes(entorno, cambio):
    e = entorno
    e.doble.programar_revision(DESCRIPCION)
    preparar(e, 0)
    mf = manifest(e, 0)
    datos = {"imagen_sha256": "0" * 64} if cambio == "imagen" else {"descripcion": "Otro contenido"}
    visual = mf.interpretaciones_visuales[0].model_copy(update=datos)
    e.storage.upload(
        clave_demo(f"{mf.document_id}/manifest.json"),
        mf.model_copy(update={"interpretaciones_visuales": [visual]}).model_dump_json(),
    )
    assert detalle(e, 0).json()["status"] == "processing"


def test_cuotas_embeddings_agotadas_no_publican(entorno):
    e = entorno
    e.cuotas = CuotasProveedor({e.indice.embeddings.modelo: CuotasModelo(1, 1, 1)})
    with pytest.raises(CuotaAgotadaError):
        preparar(e)
    assert manifest(e).publicacion_demo is None
    assert e.doble.llamadas_embeddings == 0


def test_cli_worker_publica_y_audita_sin_tocar_registro_privado(tmp_path, capsys):
    cfg = config(tmp_path)
    doble = DobleGemini(16)
    doble.programar_revision(DESCRIPCION)
    resultados = ejecutar(cfg, leer_fuentes(DOCUMENTS), proveedor_embeddings=doble, proveedor_vision=doble)
    assert len(resultados) == 3 and all(r["estado"] == "ready" for r in resultados)
    eventos = [json.loads(linea) for linea in capsys.readouterr().out.splitlines() if linea.startswith("{")]
    solicitudes = [e for e in eventos if e["evento"] == "solicitud_proveedor"]
    assert len(solicitudes) == doble.llamadas_embeddings + doble.llamadas_verificacion
    assert all(e["tokens_estimados"] > 0 for e in solicitudes)
    assert not (tmp_path / "operativo.db").exists()


def test_cli_con_backend_activo_rechaza_antes_de_storage(entorno, tmp_path):
    e = entorno
    cfg = config(e.app.state.db.ruta_db.parent)
    with pytest.raises(RuntimeError, match="volumen"):
        ejecutar(cfg, leer_fuentes(DOCUMENTS), proveedor_embeddings=e.doble, proveedor_vision=e.doble)
    assert not (Path(cfg.data_dir) / "demo-mantenimiento.db").exists()


def test_bloqueo_entre_procesos_y_liberacion(tmp_path):
    codigo = (
        "import sys; from pathlib import Path; from app.jobs.runtime_lock import exclusividad_runtime; "
        "lock=exclusividad_runtime(Path(sys.argv[1])); lock.__enter__(); lock.__exit__(None,None,None)"
    )
    with exclusividad_runtime(tmp_path):
        r = subprocess.run(
            [sys.executable, "-c", codigo, str(tmp_path)],
            cwd=DOCUMENTS.parent / "backend",
            capture_output=True,
            text=True,
        )
        assert r.returncode != 0 and "volumen" in r.stderr
    with exclusividad_runtime(tmp_path):
        pass


def test_check_no_lee_configuracion_ni_proveedores(monkeypatch, capsys):
    monkeypatch.setattr("app.documents.preload_cli.Configuracion", lambda: pytest.fail("Sin configuración"))
    assert main(["--check", "--documents-dir", str(DOCUMENTS)]) == 0
    filas = [json.loads(linea) for linea in capsys.readouterr().out.splitlines()]
    assert len(filas) == 3 and all(f["publicado"] is False for f in filas)


def test_cli_mock_sin_doble_rechazado(tmp_path):
    with pytest.raises(ValueError, match="Gemini real"):
        ejecutar(config(tmp_path), [])
    assert not (tmp_path / "runtime.lock").exists()


def test_indice_perdido_se_reconstruye_desde_mismo_original(entorno):
    e = entorno
    preparar(e)
    antes = manifest(e)
    e.indice._borrar(e.indice._demo, DEMO_WORKSPACE_ID, antes.document_id, definitivo=False)
    assert detalle(e).json()["status"] == "processing"
    assert not preparar(e)["reutilizado"]
    despues = manifest(e)
    assert despues.hash_sha256 == antes.hash_sha256
    assert despues.publicacion_demo.version_indice != antes.publicacion_demo.version_indice
    assert detalle(e).json()["status"] == "ready"


def test_reintentos_visuales_solo_tres_y_auditados(tmp_path, capsys):
    from app.jobs.manager import ReintentableError

    cfg = config(tmp_path)
    doble = DobleGemini(16)
    doble.programar_revision(ReintentableError("temporal"), ReintentableError("temporal"), DESCRIPCION)
    ejecutar(cfg, leer_fuentes(DOCUMENTS), proveedor_embeddings=doble, proveedor_vision=doble)
    eventos = [json.loads(linea) for linea in capsys.readouterr().out.splitlines() if linea.startswith("{")]
    visuales = [e for e in eventos if e.get("modelo") == cfg.gemini_generation_model]
    assert len(visuales) == doble.llamadas_verificacion == 3
    assert all(e["gemini_mock"] is True for e in visuales)


def test_fallo_lifespan_libera_exclusividad(tmp_path, monkeypatch):
    monkeypatch.setattr("app.main.restaurar_registro", lambda *args: (_ for _ in ()).throw(ValueError("fallo")))
    app = crear_app(config(tmp_path), proveedor_embeddings=DobleGemini(16))
    with pytest.raises(ValueError, match="fallo"):
        with TestClient(app):
            pass
    with exclusividad_runtime(tmp_path):
        pass
