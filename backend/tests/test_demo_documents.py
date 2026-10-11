"""H03: OCI mock autoritativo, Chroma persistente temporal y HTTP real."""

import hashlib
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from app.api.security import _peticiones_globales, _peticiones_ip
from app.config import Configuracion
from app.core.rag.chunker import trocear
from app.core.rag.parser import parsear_archivo
from app.core.rag.rebuild import ManifiestoDocumento
from app.core.rag.vectorstore import DEMO_WORKSPACE_ID, _huella
from app.jobs.manager import ContextoEjecucion, CuotasModelo, CuotasProveedor
from app.main import crear_app
from app.schemas.documents import (
    CoberturaDocumento,
    MetadatosDocumento,
    PublicacionDemo,
    SeccionDocumental,
    huella_documental_demo,
)
from app.schemas.enums import DocumentStatus
from app.storage.object_keys import clave_demo
from app.storage.provider import StorageUnavailable
from doubles.gemini import DobleGemini
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration_mock
BASE = "/api/demo/documents"
TEXTO = "# Configuración\n" + "Una VCN aísla rutas, subredes y recursos autorizados.\n" * 18
TEXTO += "# Configuración\n" + "El gateway mantiene el tráfico y las reglas de seguridad.\n" * 18


@pytest.fixture
def entorno(tmp_path, monkeypatch):
    _peticiones_globales.clear()
    _peticiones_ip.clear()
    # Aislar las escrituras del sincronizador de la medición de GET. No altera
    # la recuperación inicial, el gestor, Chroma ni las compensaciones DELETE.
    monkeypatch.setattr("app.main.SincronizadorManifiestos._ejecutar", lambda self: self._stop.wait())
    config = Configuracion(
        app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path), embedding_dimensions=16
    )
    doble = DobleGemini(16)
    cuotas = CuotasProveedor({config.gemini_embedding_model: CuotasModelo(1000, 1000000, 1000)})
    app = crear_app(config, proveedor_embeddings=doble, cuotas_proveedor=cuotas)
    with TestClient(app) as cliente:
        ws = cliente.post("/api/workspaces").json()
        yield SimpleNamespace(
            app=app,
            cliente=cliente,
            ws=ws,
            db=app.state.db,
            storage=app.state.storage_provider,
            indice=app.state.vectorstore,
            catalogo=app.state.demo_documents_query,
            doble=doble,
            cuotas=cuotas,
            headers={"Authorization": f"Bearer {ws['token']}"},
        )
    assert app.state.demo_documents_query is None


def preparar(
    e, document_id="demo-a", *, contenido=None, nombre="fuente.md", indexar=True, evidencia=True, metadata=True
):
    contenido = TEXTO.encode() if contenido is None else contenido
    parseo = parsear_archivo(contenido, nombre)
    chunks = [c.como_chunk_interno() for c in trocear(parseo, DEMO_WORKSPACE_ID, document_id, source_name=nombre)]
    cobertura = CoberturaDocumento(
        paginas_procesadas=[p.numero for p in parseo.paginas],
        paginas_visuales_pendientes=parseo.paginas_visuales,
        secciones=[
            SeccionDocumental(
                seccion_id=f"seccion:{s.linea_inicio}",
                titulo=s.titulo,
                linea_inicio=s.linea_inicio,
                linea_fin=s.linea_fin,
            )
            for s in parseo.secciones
        ],
        detalle=parseo.cobertura,
    )
    meta = MetadatosDocumento(
        extension=parseo.extension,
        tamano_bytes=len(contenido),
        creado_en=datetime(2026, 10, 10, tzinfo=timezone.utc),
        cantidad_paginas=len({p.numero for p in parseo.paginas} | set(parseo.paginas_visuales)),
        cantidad_tokens=parseo.cantidad_tokens,
        huella_config_parser=parseo.huella_config_parser,
    )
    publicacion = None
    mf = ManifiestoDocumento(
        workspace_id=DEMO_WORKSPACE_ID,
        document_id=document_id,
        source_name=nombre,
        titulo="Fuente demo",
        hash_sha256=hashlib.sha256(contenido).hexdigest(),
        estado=parseo.estado_documento,
        metadatos=meta if metadata else None,
        cobertura=cobertura if metadata else None,
    )
    if indexar and chunks:
        ctx = ContextoEjecucion(
            "fixture-mantenimiento", DEMO_WORKSPACE_ID, "fixture", time.monotonic() + 30, cuotas=e.cuotas
        )
        e.indice._indexar(e.indice._demo, DEMO_WORKSPACE_ID, document_id, chunks, ctx)
        activo = e.indice._activo(e.indice._demo, DEMO_WORKSPACE_ID, document_id)
        publicacion = PublicacionDemo(
            hash_sha256=mf.hash_sha256,
            version_indice=activo["version"],
            cantidad_chunks=len(chunks),
            huella_chunks=_huella(chunks),
            huella_config_chunker=mf.chunker.huella(mf.parser),
            huella_documental=huella_documental_demo(meta, cobertura),
            modelo_embeddings=e.indice.embeddings.modelo,
            dimensiones_embeddings=e.indice.embeddings.dimensiones,
            preparacion_embeddings=e.indice.embeddings.preparation_version,
        )
    mf = mf.model_copy(update={"publicacion_demo": publicacion if evidencia else None})
    e.storage.upload(clave_demo(f"{document_id}/original"), contenido)
    e.storage.upload(clave_demo(f"{document_id}/manifest.json"), mf.model_dump_json())
    return SimpleNamespace(manifest=mf, chunks=chunks, original=contenido)


def cambiar(e, mf, **cambios):
    nuevo = mf.model_copy(update=cambios)
    e.storage.upload(clave_demo(f"{mf.document_id}/manifest.json"), nuevo.model_dump_json())
    return nuevo


def get(e, sufijo="", **params):
    return e.cliente.get(BASE + sufijo, headers=e.headers, params=params)


def error(r, status, code):
    assert r.status_code == status, r.text
    datos = r.json()
    assert set(datos) == {"error", "request_id"}
    assert datos["error"]["code"] == code
    assert datos["request_id"] == r.headers["X-Request-ID"]
    return datos


@pytest.mark.parametrize("ruta", [BASE, BASE + "/demo-a", BASE + "/demo-a/sources/chunk"])
@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer invalido"}, {"Authorization": "Basic abc"}])
def test_bearer_obligatorio(entorno, ruta, headers):
    error(entorno.cliente.get(ruta, headers=headers), 401, "SESSION_INVALID")


def test_catalogo_vacio_y_original_sin_manifiesto_no_inventa_fuentes(entorno):
    e = entorno
    e.storage.upload("demo/huerfano/original", TEXTO)
    assert get(e).json() == {"items": [], "next_cursor": None}
    error(get(e, "/huerfano"), 404, "NOT_FOUND")


def test_documento_textual_integro_listado_detalle_secciones_y_fuente(entorno):
    e = entorno
    preparado = preparar(e)
    lista = get(e)
    assert lista.status_code == 200
    detalle = get(e, "/demo-a").json()
    assert lista.json() == {"items": [detalle], "next_cursor": None}
    assert detalle["status"] == "ready" and detalle["error"] is None
    assert detalle["metadata"]["cantidad_tokens"] == preparado.manifest.metadatos.cantidad_tokens
    secciones = detalle["cobertura"]["secciones_procesadas"]
    assert [s["titulo"] for s in secciones] == ["Configuración", "Configuración"]
    assert [s["seccion_id"] for s in secciones] == ["seccion:1", "seccion:20"]
    assert secciones[0]["linea_fin"] < secciones[1]["linea_inicio"]
    for chunk in preparado.chunks:
        r = get(e, f"/demo-a/sources/{chunk.chunk_id}")
        assert r.status_code == 200, r.text
        assert r.json() == {
            "document_id": "demo-a",
            "chunk_id": chunk.chunk_id,
            "texto": chunk.texto,
            "pagina": None,
            "seccion": chunk.seccion,
            "seccion_id": chunk.seccion_id,
        }
    error(get(e, "/demo-a/sources/inexistente"), 404, "NOT_FOUND")


def test_pdf_textual_paginas_reales_sin_secciones_fabricadas(entorno, crear_pdf):
    e = entorno
    preparado = preparar(e, contenido=crear_pdf(paginas=3).read_bytes(), nombre="fuente.pdf")
    doc = get(e, "/demo-a").json()
    assert doc["status"] == "ready" and doc["metadata"]["cantidad_paginas"] == 3
    assert doc["cobertura"]["paginas_procesadas"] == [1, 2, 3]
    assert doc["cobertura"]["secciones_procesadas"] == []
    r = get(e, f"/demo-a/sources/{preparado.chunks[0].chunk_id}")
    assert r.status_code == 200 and r.json()["seccion_id"] == "pagina:1"


def test_pdf_visual_processing_con_cobertura_y_sin_fuentes(entorno, documento_demo_vcn):
    e = entorno
    preparado = preparar(e, contenido=documento_demo_vcn.read_bytes(), nombre=documento_demo_vcn.name)
    doc = get(e, "/demo-a").json()
    assert doc["status"] == "processing"
    assert 3 in doc["cobertura"]["paginas_visuales_pendientes"]
    assert doc["cobertura"]["secciones_visuales_pendientes"] is None
    assert doc["metadata"]["cantidad_paginas"] == preparado.manifest.metadatos.cantidad_paginas
    error(get(e, f"/demo-a/sources/{preparado.chunks[0].chunk_id}"), 409, "INVALID_STATE")


@pytest.mark.parametrize("estado", ["processing", "failed"])
def test_estado_no_listo_aunque_haya_indice_completo(entorno, estado):
    e = entorno
    p = preparar(e)
    cambiar(e, p.manifest, estado=DocumentStatus(estado), detalle_error="secreto privado del proveedor")
    doc = get(e, "/demo-a").json()
    assert doc["status"] == estado
    assert "secreto" not in json.dumps(doc)
    assert (doc["error"] is not None) == (estado == "failed")
    error(get(e, f"/demo-a/sources/{p.chunks[0].chunk_id}"), 409, "INVALID_STATE")


@pytest.mark.parametrize("faltante", ["publicacion", "metadata", "cobertura", "tokens", "huella_parser"])
def test_marcador_ready_solo_no_autoriza(entorno, faltante):
    e = entorno
    p = preparar(e)
    if faltante == "publicacion":
        cambiar(e, p.manifest, publicacion_demo=None)
    elif faltante in ("metadata", "cobertura"):
        cambiar(e, p.manifest, **{"metadatos" if faltante == "metadata" else "cobertura": None})
    else:
        campo = "cantidad_tokens" if faltante == "tokens" else "huella_config_parser"
        cambiar(e, p.manifest, metadatos=p.manifest.metadatos.model_copy(update={campo: None}))
    doc = get(e, "/demo-a").json()
    assert doc["status"] == "processing"
    if faltante == "metadata":
        assert doc["metadata"] is None
    if faltante == "cobertura":
        assert doc["cobertura"] is None
    error(get(e, f"/demo-a/sources/{p.chunks[0].chunk_id}"), 409, "INVALID_STATE")


@pytest.mark.parametrize(
    "defecto",
    [
        "ausente",
        "coleccion_ausente",
        "incompleto",
        "modelo",
        "version",
        "firma",
        "config",
        "hash",
        "orden",
        "contenido",
        "chunk_id",
        "coleccion_incompatible",
    ],
)
def test_indice_demo_no_confirmado_no_publica_ready(entorno, defecto):
    e = entorno
    p = preparar(e)
    activo = e.indice._activo(e.indice._demo, DEMO_WORKSPACE_ID, "demo-a")
    if defecto == "ausente":
        with e.indice._db() as db:
            db.execute("DELETE FROM documentos WHERE workspace=?", (DEMO_WORKSPACE_ID,))
    elif defecto == "coleccion_ausente":
        e.indice._cliente.delete_collection(e.indice._demo.name)
    elif defecto == "incompleto":
        ident = e.indice._datos(e.indice._demo, activo)["ids"][0]
        e.indice._demo.delete(ids=[ident])
    elif defecto == "coleccion_incompatible":
        e.indice._demo.modify(metadata={"modelo": "otro"})
    elif defecto in ("orden", "contenido", "chunk_id"):
        datos = e.indice._datos(e.indice._demo, activo)
        meta = dict(datos["metadatas"][0])
        if defecto == "orden":
            meta["orden"] = 100
        else:
            chunk = json.loads(meta["chunk"])
            chunk["texto" if defecto == "contenido" else "chunk_id"] = "alterado"
            meta["chunk"] = json.dumps(chunk)
        e.indice._demo.update(ids=[datos["ids"][0]], metadatas=[meta])
    else:
        campo, valor = {
            "modelo": ("modelo_embeddings", "incompatible"),
            "version": ("version_indice", "b" * 32),
            "firma": ("huella_chunks", "b" * 64),
            "config": ("huella_config_chunker", "b" * 64),
            "hash": ("hash_sha256", "b" * 64),
        }[defecto]
        cambiar(e, p.manifest, publicacion_demo=p.manifest.publicacion_demo.model_copy(update={campo: valor}))
    r = get(e, "/demo-a")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "processing"
    error(get(e, f"/demo-a/sources/{p.chunks[0].chunk_id}"), 409, "INVALID_STATE")


def test_chroma_temporalmente_inaccesible_no_simula_processing(entorno, monkeypatch):
    e = entorno
    p = preparar(e)
    original = e.indice.listar_chunks_demo

    def fallar(*args, **kwargs):
        raise RuntimeError("ruta privada del proveedor")

    monkeypatch.setattr(e.indice, "listar_chunks_demo", fallar)
    for ruta in ("", "/demo-a", f"/demo-a/sources/{p.chunks[0].chunk_id}"):
        datos = error(get(e, ruta), 503, "PROVIDER_UNAVAILABLE")
        assert "ruta privada" not in json.dumps(datos)
    monkeypatch.setattr(e.indice, "listar_chunks_demo", original)
    assert get(e, "/demo-a").json()["status"] == "ready"


@pytest.mark.parametrize("defecto", ["hash", "original_ausente", "metadata", "identidad", "malformado"])
def test_evidencia_oci_invalida_no_autoriza(entorno, defecto):
    e = entorno
    p = preparar(e)
    if defecto == "hash":
        e.storage.upload("demo/demo-a/original", TEXTO + "cambiado")
    elif defecto == "original_ausente":
        e.storage.delete("demo/demo-a/original")
    elif defecto == "metadata":
        cambiar(e, p.manifest, metadatos=p.manifest.metadatos.model_copy(update={"tamano_bytes": 1}))
    elif defecto == "identidad":
        cambiar(e, p.manifest, document_id="otro")
    else:
        e.storage.upload("demo/demo-a/manifest.json", "no-json")
    error(get(e, "/demo-a"), 503, "STORAGE_UNAVAILABLE")


@pytest.mark.parametrize("ambito", ["remoto", "local"])
def test_tombstone_demo_bloquea_incluso_con_indice(entorno, ambito):
    e = entorno
    p = preparar(e)
    if ambito == "remoto":
        cambiar(e, p.manifest, borrado=True)
    else:
        with e.indice._db() as db:
            db.execute("INSERT INTO retirados VALUES (?,?)", (DEMO_WORKSPACE_ID, "demo-a"))
    assert get(e).json() == {"items": [], "next_cursor": None}
    error(get(e, "/demo-a"), 404, "NOT_FOUND")
    error(get(e, f"/demo-a/sources/{p.chunks[0].chunk_id}"), 404, "NOT_FOUND")


def test_paginacion_orden_id_firma_separacion_y_ancla_retirada(entorno):
    e = entorno
    fuentes = {i: preparar(e, i, indexar=False, metadata=False) for i in ("c", "b", "a")}
    primera = get(e, limit=1).json()
    assert [i["document_id"] for i in primera["items"]] == ["a"]
    cursor = primera["next_cursor"]
    error(get(e, limit=1, cursor=cursor[:-1] + "!"), 400, "INVALID_REQUEST")
    privado = e.app.state.documents_query.cursor.codificar(e.ws["workspace_id"], "2026-10-10T00:00:00Z", "a")
    error(get(e, cursor=privado), 400, "INVALID_REQUEST")
    error(e.cliente.get("/api/documents", headers=e.headers, params={"cursor": cursor}), 400, "INVALID_REQUEST")
    cambiar(e, fuentes["a"].manifest, borrado=True)
    preparar(e, "d", indexar=False)
    segunda = get(e, limit=1, cursor=cursor).json()
    tercera = get(e, limit=100, cursor=segunda["next_cursor"]).json()
    assert [i["document_id"] for i in segunda["items"] + tercera["items"]] == ["b", "c", "d"]
    assert tercera["next_cursor"] is None
    assert all(i["metadata"] is None for i in primera["items"] + segunda["items"])


@pytest.mark.parametrize("limit", [0, 101, -1, "abc"])
def test_limite_invalido(entorno, limit):
    error(get(entorno, limit=limit), 422, "VALIDATION_ERROR")


def test_paginacion_retira_entre_detalle_y_entrega_sin_saltar_lookahead(entorno, monkeypatch):
    e = entorno
    fuentes = {i: preparar(e, i, indexar=False) for i in ("a", "b", "c", "d")}
    original = e.catalogo._detalle
    llamadas = 0

    def retirar(ctx, doc):
        nonlocal llamadas
        resultado = original(ctx, doc)
        llamadas += 1
        if llamadas == 3:
            cambiar(e, fuentes["a"].manifest, borrado=True)
        return resultado

    monkeypatch.setattr(e.catalogo, "_detalle", retirar)
    uno = get(e, limit=2).json()
    assert [i["document_id"] for i in uno["items"]] == ["b", "c"]
    dos = get(e, limit=2, cursor=uno["next_cursor"]).json()
    assert [i["document_id"] for i in dos["items"]] == ["d"]
    assert dos["next_cursor"] is None


@pytest.mark.parametrize(
    ("ids", "retirados", "esperados"),
    [
        pytest.param(("a", "b", "c", "d"), ("c",), [["a", "b"], ["d"]], id="lookahead_con_posteriores"),
        pytest.param(("a", "b", "c"), ("c",), [["a", "b"]], id="lookahead_sin_posteriores"),
        pytest.param(("a", "b", "c", "d"), ("a", "b", "c"), [["d"]], id="todos_seleccionados_retirados"),
    ],
)
def test_paginacion_revalida_y_completa_con_ids_posteriores(entorno, monkeypatch, ids, retirados, esperados):
    e = entorno
    fuentes = {i: preparar(e, i, indexar=False) for i in ids}
    original = e.catalogo._detalle
    llamadas = 0
    cambios, embeddings = e.db._conn.total_changes, e.doble.llamadas_embeddings

    def retirar(ctx, document_id):
        nonlocal llamadas
        llamadas += 1
        # a,b,c son la selección inicial; el cuarto llamado inicia su
        # revalidación. Las lápidas se persisten antes de volver a autorizar.
        if llamadas == 4:
            for retirado in retirados:
                cambiar(e, fuentes[retirado].manifest, borrado=True)
        return original(ctx, document_id)

    def prohibido(*args, **kwargs):
        pytest.fail("La paginación intentó producir un efecto")

    monkeypatch.setattr(e.catalogo, "_detalle", retirar)
    monkeypatch.setattr(e.app.state.jobs_manager, "enqueue", prohibido)
    monkeypatch.setattr(e.indice.embeddings, "embed_documents", prohibido)
    monkeypatch.setattr(e.indice.embeddings, "embed_query", prohibido)
    monkeypatch.setattr(e.indice._demo, "upsert", prohibido)
    cursor, entregados = None, []
    for numero, ids_esperados in enumerate(esperados):
        params = {"limit": 2}
        if cursor is not None:
            params["cursor"] = cursor
        respuesta = get(e, **params)
        assert respuesta.status_code == 200, respuesta.text
        pagina = respuesta.json()
        actuales = [item["document_id"] for item in pagina["items"]]
        assert actuales == ids_esperados
        entregados.extend(actuales)
        cursor = pagina["next_cursor"]
        if numero < len(esperados) - 1:
            assert cursor is not None
            assert e.catalogo.cursor.decodificar(cursor) == actuales[-1]
        else:
            assert cursor is None
    assert llamadas >= 4  # La retirada ocurrió durante la petición real.
    assert entregados == [i for i in ids if i not in retirados]
    assert len(entregados) == len(set(entregados))
    assert e.db._conn.total_changes == cambios
    assert e.doble.llamadas_embeddings == embeddings
    assert e.db._conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


@pytest.mark.parametrize("carrera", ["tombstone", "estado", "sesion", "version", "original"])
def test_revalidacion_despues_de_io_impide_entrega(entorno, monkeypatch, carrera):
    e = entorno
    p = preparar(e)
    original = e.indice.listar_chunks_demo
    aplicado = False

    def modificar(*args, **kwargs):
        nonlocal aplicado
        chunks = original(*args, **kwargs)
        if not aplicado:
            aplicado = True
            if carrera == "tombstone":
                cambiar(e, p.manifest, borrado=True)
            elif carrera == "estado":
                cambiar(e, p.manifest, estado=DocumentStatus.PROCESSING)
            elif carrera == "sesion":
                e.db.revocar_sesion(e.ws["token"])
            elif carrera == "original":
                e.storage.upload("demo/demo-a/original", TEXTO + "cambió")
            else:
                with e.indice._db() as db:
                    db.execute("UPDATE documentos SET version=? WHERE workspace=?", ("b" * 32, DEMO_WORKSPACE_ID))
        return chunks

    monkeypatch.setattr(e.indice, "listar_chunks_demo", modificar)
    r = get(e, f"/demo-a/sources/{p.chunks[0].chunk_id}")
    assert r.status_code in (401, 404, 409, 503), r.text
    assert "texto" not in r.json()


def test_sesiones_distintas_comparten_demo_pero_no_documentos_privados(entorno):
    e = entorno
    p = preparar(e)
    otro = e.cliente.post("/api/workspaces").json()
    headers = {"Authorization": f"Bearer {otro['token']}"}
    assert e.cliente.get(BASE + "/demo-a", headers=headers).json() == get(e, "/demo-a").json()
    assert e.cliente.get(BASE + f"/demo-a/sources/{p.chunks[0].chunk_id}", headers=headers).status_code == 200
    error(e.cliente.get("/api/documents/demo-a", headers=headers), 404, "NOT_FOUND")
    subir = e.cliente.post(
        "/api/documents/upload", headers=e.headers, json={"documento_titulo": "privado", "documento_contenido": TEXTO}
    )
    assert subir.status_code == 202
    document_id = subir.json()["document_id"]
    error(get(e, "/" + document_id), 404, "NOT_FOUND")
    error(e.cliente.get("/api/documents/" + document_id, headers=headers), 404, "NOT_FOUND")
    error(get(e, "/demo-a/sources/chunk-privado"), 404, "NOT_FOUND")


def test_get_no_crea_embeddings_jobs_ni_escrituras(entorno, monkeypatch):
    e = entorno
    p = preparar(e)
    cambios, llamadas = e.db._conn.total_changes, e.doble.llamadas_embeddings

    def prohibido(*args, **kwargs):
        pytest.fail("GET intentó producir un efecto")

    for metodo in ("upload", "delete"):
        monkeypatch.setattr(e.storage, metodo, prohibido)
    monkeypatch.setattr(e.db, "registrar_actividad", prohibido)
    monkeypatch.setattr(e.app.state.jobs_manager, "enqueue", prohibido)
    monkeypatch.setattr(e.indice.embeddings, "embed_documents", prohibido)
    monkeypatch.setattr(e.indice.embeddings, "embed_query", prohibido)
    monkeypatch.setattr(e.indice._demo, "upsert", prohibido)
    for ruta in ("", "/demo-a", f"/demo-a/sources/{p.chunks[0].chunk_id}"):
        assert get(e, ruta).status_code == 200
    assert e.db._conn.total_changes == cambios
    assert e.doble.llamadas_embeddings == llamadas
    assert e.db._conn.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_delete_privado_deja_demo_fisicamente_intacta(entorno):
    e = entorno
    p = preparar(e)
    demo_antes = e.indice._demo.get(include=["documents", "metadatas"])
    subir = e.cliente.post(
        "/api/documents/upload", headers=e.headers, json={"documento_titulo": "privado", "documento_contenido": TEXTO}
    )
    assert subir.status_code == 202
    recibo = subir.json()
    fin = time.monotonic() + 10
    while e.cliente.get(recibo["status_url"], headers=e.headers).json()["status"] != "completed":
        assert time.monotonic() < fin
        time.sleep(0.005)
    borrar = e.cliente.delete("/api/documents/" + recibo["document_id"], headers=e.headers)
    assert borrar.status_code == 204, borrar.text
    assert e.indice._demo.get(include=["documents", "metadatas"]) == demo_antes
    assert e.storage.get("demo/demo-a/original") == p.original
    assert ManifiestoDocumento.model_validate_json(e.storage.get("demo/demo-a/manifest.json")) == p.manifest
    assert get(e, f"/demo-a/sources/{p.chunks[0].chunk_id}").status_code == 200


def test_storage_no_disponible_no_simula_catalogo_vacio(entorno, monkeypatch):
    def fallar(*args, **kwargs):
        raise StorageUnavailable("credenciales privadas")

    monkeypatch.setattr(entorno.storage, "list", fallar)
    error(get(entorno), 503, "STORAGE_UNAVAILABLE")


def test_indice_no_configurado_y_metadata_desconocida(entorno):
    e = entorno
    p = preparar(e)
    e.catalogo.indice = None
    error(get(e, "/demo-a"), 503, "PROVIDER_UNAVAILABLE")
    cambiar(e, p.manifest, estado=DocumentStatus.PROCESSING, metadatos=None, cobertura=None, publicacion_demo=None)
    doc = get(e, "/demo-a").json()
    assert doc["status"] == "processing" and doc["metadata"] is None and doc["cobertura"] is None
    error(get(e, "/demo-a/sources/x"), 409, "INVALID_STATE")


def test_openapi_y_proyeccion_excluyen_campos_internos(entorno):
    e = entorno
    preparar(e)
    paths = e.app.openapi()["paths"]
    for ruta in (BASE, BASE + "/{document_id}", BASE + "/{document_id}/sources/{chunk_id}"):
        assert set(paths[ruta]) == {"get"}
        assert paths[ruta]["get"]["security"]
    datos = get(e, "/demo-a").json()
    assert set(datos) == {"document_id", "titulo", "status", "metadata", "cobertura", "error"}
    assert not {"workspace_id", "hash_sha256", "publicacion_demo", "admision_confirmada", "parser"} & set(datos)
    assert "huella_config_parser" not in datos["metadata"]


@pytest.mark.parametrize("campo", ["cantidad_tokens", "creado_en", "huella_config_parser", "secciones"])
def test_metadata_y_cobertura_no_se_desligan_de_la_publicacion(entorno, campo):
    e = entorno
    p = preparar(e)
    if campo == "secciones":
        s = p.manifest.cobertura.secciones[0].model_copy(update={"linea_fin": 10000})
        cambiar(e, p.manifest, cobertura=p.manifest.cobertura.model_copy(update={"secciones": [s]}))
    else:
        valor = {
            "cantidad_tokens": 90000,
            "creado_en": datetime(2020, 1, 1, tzinfo=timezone.utc),
            "huella_config_parser": "a" * 64,
        }[campo]
        cambiar(e, p.manifest, metadatos=p.manifest.metadatos.model_copy(update={campo: valor}))
    assert get(e, "/demo-a").json()["status"] == "processing"
    error(get(e, f"/demo-a/sources/{p.chunks[0].chunk_id}"), 409, "INVALID_STATE")


def test_chunks_privados_reales_con_id_de_documento_coincidente_no_se_filtran(entorno):
    e = entorno
    p = preparar(e)
    texto_privado = ("Documento estrictamente privado que debe conservar su ámbito.\n" * 30).encode()
    parseo = parsear_archivo(texto_privado, "privado.txt")
    chunks = [
        c.como_chunk_interno() for c in trocear(parseo, e.ws["workspace_id"], "demo-a", source_name="privado.txt")
    ]
    ctx = ContextoEjecucion("fixture-privada", e.ws["workspace_id"], "fixture", time.monotonic() + 30, cuotas=e.cuotas)
    e.indice.indexar(e.ws["workspace_id"], "demo-a", chunks, contexto=ctx)
    error(get(e, f"/demo-a/sources/{chunks[0].chunk_id}"), 404, "NOT_FOUND")
    assert get(e, f"/demo-a/sources/{p.chunks[0].chunk_id}").json()["texto"] == p.chunks[0].texto
    assert all(
        "estrictamente privado" not in c.texto
        for c in e.catalogo._detalle(e.catalogo._contexto(e.ws["token"]), "demo-a")[1]
    )


@pytest.mark.parametrize("ruta", ["", "/demo-a", "/demo-a/sources/chunk"])
def test_no_acepta_workspace_en_query(entorno, ruta):
    error(get(entorno, ruta, workspace_id="espacio-ajeno"), 422, "VALIDATION_ERROR")


def test_cursor_demo_compartido_no_es_permiso_y_no_depende_de_fechas(entorno):
    e = entorno
    preparar(e, "Z", indexar=False, metadata=False)
    preparar(e, "a", indexar=False, metadata=False)
    primera = get(e, limit=1).json()
    assert primera["items"][0]["document_id"] == "Z"
    otro = e.cliente.post("/api/workspaces").json()
    headers = {"Authorization": f"Bearer {otro['token']}"}
    r = e.cliente.get(BASE, headers=headers, params={"cursor": primera["next_cursor"]})
    assert r.status_code == 200 and [i["document_id"] for i in r.json()["items"]] == ["a"]
    error(e.cliente.get(BASE, params={"cursor": primera["next_cursor"]}), 401, "SESSION_INVALID")


def test_revalida_manifiesto_despues_del_ultimo_io_chroma(entorno, monkeypatch):
    e = entorno
    p = preparar(e)
    original = e.indice.listar_chunks_demo
    llamadas = 0

    def retirar(*args, **kwargs):
        nonlocal llamadas
        chunks = original(*args, **kwargs)
        llamadas += 1
        if llamadas == 2:
            cambiar(e, p.manifest, borrado=True)
        return chunks

    monkeypatch.setattr(e.indice, "listar_chunks_demo", retirar)
    error(get(e, f"/demo-a/sources/{p.chunks[0].chunk_id}"), 409, "INVALID_STATE")
    error(get(e, "/demo-a"), 404, "NOT_FOUND")


def test_lectura_con_nuevo_registro_e_indice_persistente_sin_reindexar(entorno, monkeypatch):
    from app.documents.demo import ServicioCatalogoDemo
    from app.jobs.store import RegistroOperativo

    e = entorno
    p = preparar(e)
    db_nueva = RegistroOperativo(e.db.ruta_db.parent / "restaurado.db")
    # Solo reconstruir la sesión/workspace para esta lectura; no inventar
    # documentos, jobs ni admisiones privadas para la biblioteca compartida.
    from app.session.manager import SessionManager
    from app.session.recovery import restaurar_registro

    restaurar_registro(db_nueva, e.storage)
    manager = SessionManager(db_nueva, e.storage, workspace_retention_days=30, session_max_hours=24)
    sesion = manager.recuperar_sesion(e.ws["recovery_code"])
    catalogo = ServicioCatalogoDemo(db_nueva, e.storage, e.indice, plazo=30)
    cambios, llamadas = db_nueva._conn.total_changes, e.doble.llamadas_embeddings
    try:
        assert catalogo.detalle(sesion.token, "demo-a").status == DocumentStatus.READY
        assert catalogo.fuente(sesion.token, "demo-a", p.chunks[0].chunk_id).texto == p.chunks[0].texto
        assert db_nueva._conn.total_changes == cambios and e.doble.llamadas_embeddings == llamadas
        assert db_nueva._conn.execute("SELECT count(*) FROM document_admissions").fetchone()[0] == 0
    finally:
        db_nueva.cerrar()


def test_lapida_local_sin_chroma_configurado_tampoco_resucita_demo(entorno):
    e = entorno
    preparar(e, indexar=False)
    with e.indice._db() as db:
        db.execute("INSERT INTO retirados VALUES (?,?)", (DEMO_WORKSPACE_ID, "demo-a"))
    e.catalogo.indice = None
    assert get(e).json() == {"items": [], "next_cursor": None}
    error(get(e, "/demo-a"), 404, "NOT_FOUND")


def test_paginacion_omite_retirados_antes_de_contar_y_recorre_oci(entorno, monkeypatch):
    from dataclasses import replace

    e = entorno
    fuentes = {i: preparar(e, i, indexar=False) for i in ("a", "b", "c", "d")}
    cambiar(e, fuentes["b"].manifest, borrado=True)
    original = e.storage.list
    paginas = []

    def acotar(prefix="", *, limit=100, cursor=None):
        pagina = original(prefix, limit=1, cursor=cursor)
        if prefix == "demo/":
            paginas.append(cursor)
            # Objetos privados fuera del prefijo no constituyen tarjetas demo.
            ajeno = (
                replace(pagina.items[0], object_name="source_documents/ajeno/privado/manifest.json")
                if pagina.items
                else None
            )
            if ajeno:
                pagina = replace(pagina, items=[*pagina.items, ajeno])
        return pagina

    monkeypatch.setattr(e.storage, "list", acotar)
    uno = get(e, limit=2).json()
    assert [i["document_id"] for i in uno["items"]] == ["a", "c"]
    dos = get(e, limit=2, cursor=uno["next_cursor"]).json()
    assert [i["document_id"] for i in dos["items"]] == ["d"]
    assert dos["next_cursor"] is None and len(paginas) > 2
