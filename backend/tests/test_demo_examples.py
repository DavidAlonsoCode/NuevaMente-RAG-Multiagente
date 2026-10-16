"""Snapshots explícitamente simulados: estos tests no acreditan Gemini ni OCI reales."""

from datetime import datetime, timezone

import pytest
from app.demo.catalog import CLAVE_CATALOGO, guardar_blob
from app.demo.models import CatalogoEjemplos, EjemploDemo, FuenteDemoGuardada
from app.demo.scenarios import ESCENARIOS
from app.schemas.documents_http import FuenteDocumentoResponse
from app.schemas.requests import GenerateRequest
from test_api_generate import entorno, esperar, generar  # noqa: F401

pytestmark = pytest.mark.integration_mock


@pytest.fixture
def ejemplo(entorno):  # noqa: F811
    e = entorno
    recibo = generar(e, "demo-fixture", formato_salida="quiz").json()
    esperar(e, recibo, timeout=30)
    gid = recibo["generation_id"]
    paquete, datos = e.app.state.generations.catalogo.cargar(e.ws["token"], gid)
    snapshot = FuenteDemoGuardada(
        document_id=e.doc,
        document_hash=paquete.documento_fuente.hash,
        original_sha256="a" * 64,
        chunks=[
            FuenteDocumentoResponse(
                document_id=e.doc,
                chunk_id=c.chunk_id,
                texto=c.texto,
                pagina=c.pagina,
                seccion=c.seccion,
                seccion_id=c.seccion_id,
            )
            for c in e.app.state.documents_query.chunks(e.ws["token"], e.doc)
        ],
    )
    item = EjemploDemo(
        escenario="vcn-quiz",
        generation_id=gid,
        fuente="fixture.txt",
        document_hash=paquete.documento_fuente.hash,
        parametros=GenerateRequest.model_validate({**e.solicitud, "formato_salida": "quiz"}),
        created_at=paquete.created_at,
        segundos=2.5,
        status="completed",
        paquete_sha256=guardar_blob(e.storage, datos),
        fuente_sha256=guardar_blob(e.storage, snapshot.model_dump_json().encode()),
    )
    catalogo = CatalogoEjemplos(provider="mock", publicado_en=datetime.now(timezone.utc), completo=False, items=[item])
    e.storage.upload(CLAVE_CATALOGO, catalogo.model_dump_json(), content_type="application/json", crear_solo=True)
    e.gid, e.paquete, e.item = gid, paquete, item
    e.otro = e.cliente.post("/api/workspaces").json()
    e.public_headers = {"Authorization": "Bearer " + e.otro["token"]}
    return e


def test_catalogo_vacio_y_matriz_oficial(entorno):  # noqa: F811
    e = entorno
    r = e.cliente.get("/api/demo/generations", headers=e.headers)
    assert r.status_code == 200 and r.json() == {"items": []}
    assert len(ESCENARIOS) == 8
    a, b, c = [GenerateRequest.model_validate(s.solicitud("mismo-documento")) for s in ESCENARIOS[:3]]
    assert {x.document_id for x in (a, b, c)} == {"mismo-documento"}
    assert [x.idioma_salida.value for x in (a, b, c)] == ["es", "en", "pt"]


def test_lectura_desde_otro_espacio_y_quiz_sin_filtrar_clave(ejemplo):
    e = ejemplo
    before = e.doble.llamadas_generacion
    ruta = "/api/demo/generations/" + e.gid
    catalogo = e.cliente.get("/api/demo/generations", headers=e.public_headers).json()
    assert catalogo["provider"] == "mock" and not catalogo["completo"]
    assert catalogo["etiqueta"] == "Ejemplo generado previamente"
    lectura = e.cliente.get(ruta, headers=e.public_headers)
    assert lectura.status_code == 200
    pregunta = lectura.json()["contenido_adaptado"]["preguntas"][0]
    assert "correct_option_id" not in pregunta and "justificacion" not in pregunta
    original = e.paquete.contenido_adaptado.preguntas[0]
    incorrecta = next(o.option_id for o in original.opciones if o.option_id != original.correct_option_id)
    for opcion, correcta in ((incorrecta, False), (original.correct_option_id, True)):
        respuesta = e.cliente.post(
            ruta + "/answers",
            headers=e.public_headers,
            json={"question_id": pregunta["id"], "option_id": opcion, "event_id": "demo-" + opcion},
        )
        assert respuesta.status_code == 200 and respuesta.json()["correcta"] is correcta
        assert respuesta.json()["justificacion"] and respuesta.json()["referencias"]
    assert e.doble.llamadas_generacion == before
    assert e.cliente.get("/api/generations/" + e.gid, headers=e.public_headers).status_code == 404
    fuente = e.paquete.referencias[0]
    assert e.cliente.get(ruta + "/sources/" + fuente.chunk_id, headers=e.public_headers).status_code == 200
    assert e.cliente.get(ruta + "/sources/inexistente", headers=e.public_headers).status_code == 404
    assert e.cliente.get(ruta + "/sources/" + fuente.chunk_id + "/image", headers=e.public_headers).status_code == 404


def test_integridad_sin_fallback_privado(ejemplo):
    from app.demo.catalog import clave_blob

    e = ejemplo
    e.storage.upload(clave_blob(e.item.paquete_sha256), b"{}", content_type="application/json")
    r = e.cliente.get("/api/demo/generations/" + e.gid, headers=e.public_headers)
    assert r.status_code == 503 and "contenido_adaptado" not in r.text


def test_no_acepta_workspace_y_sesion_revocada(ejemplo):
    e = ejemplo
    assert e.cliente.get("/api/demo/generations?workspace_id=ajeno", headers=e.public_headers).status_code == 422
    assert e.cliente.get("/api/demo/generations/gen-ajeno", headers=e.public_headers).status_code == 404
    e.cliente.delete("/api/sessions/current", headers=e.public_headers)
    assert e.cliente.get("/api/demo/generations", headers=e.public_headers).status_code == 401


def test_rechazo_no_tiene_paquete_y_publicacion_rechaza_mocks(entorno):  # noqa: F811
    from app.demo.publish import publicar

    with pytest.raises(ValueError):
        EjemploDemo(
            escenario="rechazo",
            generation_id="gen-r",
            fuente="fixture.txt",
            document_hash="a" * 64,
            parametros=GenerateRequest.model_validate(ESCENARIOS[0].solicitud("doc-r")),
            created_at=datetime.now(timezone.utc),
            segundos=1,
            status="rejected_quality",
            paquete_sha256="b" * 64,
            diagnostico="Sin cobertura",
        )
    with pytest.raises(ValueError, match="reales"):
        publicar(entorno.app, entorno.app.state.config, [], entorno.ws["token"], {})
