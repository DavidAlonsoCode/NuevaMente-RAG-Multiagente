"""Entrega con rutas, SQLite, sesiones y gestor reales; autorización documental doble."""

import hashlib
import json
from types import SimpleNamespace

import pytest
import test_multidocument_generation as multidocument_cases
from app.api.routes.jobs import router
from app.chat.service import ServicioChat
from app.config import Configuracion
from app.core.agents.graph_state import DocumentoGeneracion
from app.core.rag.rebuild import ManifiestoDocumento
from app.jobs.manager import GestorTrabajos
from app.jobs.store import RegistroOperativo
from app.schemas.enums import DocumentStatus, JobStatus
from app.schemas.errors import ErrorAplicacion, ErrorCode
from app.schemas.responses import DocumentoFuente
from app.storage.object_keys import clave_documento
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration_mock
entorno_documental = multidocument_cases.entorno_multi


class ConsultaFuentes:
    indice = None

    def __init__(self, db):
        self.db = db
        self.cambios = {}
        self.consultadas = []

    def _sesion(self, token):
        sesion = self.db.obtener_sesion(token)
        if sesion is None:
            raise ErrorAplicacion(ErrorCode.SESSION_INVALID, "Sesión inválida.")
        return sesion

    def actual(self, workspace_id, document_id):
        self.consultadas.append((workspace_id, document_id))
        doc = self.db.obtener_documento(document_id)
        if doc is None or doc["workspace_id"] != workspace_id or doc["estado"] != "ready":
            return None
        return DocumentoFuente(
            document_id=document_id, titulo=doc["titulo"], hash=doc["hash"], version="v1"
        ).model_copy(update=self.cambios.get(document_id, {}))

    def fuente_aprobada(self, token, document_id):
        fuente = self.actual(self._sesion(token)["workspace_id"], document_id)
        if fuente is None:
            raise ErrorAplicacion(ErrorCode.NOT_FOUND, "Documento no disponible.")
        return fuente

    def documento_grafo(self, contexto, document_id):
        fuente = self.actual(contexto.workspace_id, document_id)
        return (
            DocumentoGeneracion(workspace_id=contexto.workspace_id, fuente=fuente, estado="ready", idioma_origen="es")
            if fuente
            else None
        )


def respuesta_chat(fuentes, *, legado=False, pregunta="¿Qué indica la fuente?"):
    """Resultado sembrado; no se presenta como evidencia de generación del tutor."""
    salida = {
        "document_id": fuentes[0].document_id,
        "document_hash": fuentes[0].hash,
        "pregunta": pregunta,
        "perfil_destinatario": "principiante",
        "idioma_salida": "es",
        "estado": "fuera_de_alcance",
        "respuesta": "La fuente no cubre esta pregunta. Precisá la consulta.",
        "referencias": [],
        "evaluacion_calidad": None,
    }
    if not legado:
        salida.update(
            document_ids=[f.document_id for f in fuentes],
            documentos_fuente=[f.model_dump(mode="json") for f in fuentes],
        )
    return salida


@pytest.fixture
def chat_env(tmp_path):
    e = SimpleNamespace(db=RegistroOperativo(tmp_path / "chat.sqlite3"))
    e.db.crear_workspace("ws_a", "codigo_a", 30)
    e.db.crear_workspace("ws_b", "codigo_b", 30)
    e.db.crear_sesion("token-a", "ws_a", 24)
    e.db.crear_sesion("token-b", "ws_b", 24)
    for doc, ws in [("doc_a", "ws_a"), ("doc_b", "ws_a"), ("doc_c", "ws_a"), ("doc_x", "ws_b")]:
        e.db.registrar_documento(doc, ws, doc, DocumentStatus.READY, hashlib.sha256(doc.encode()).hexdigest())
    e.consulta = ConsultaFuentes(e.db)
    e.gestor = GestorTrabajos(e.db)
    cfg = Configuracion(app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path))
    e.servicio = ServicioChat(e.db, e.consulta, e.gestor, cfg)
    e.fuentes = [e.consulta.actual("ws_a", d) for d in ("doc_a", "doc_b")]
    e.resultado = respuesta_chat(e.fuentes)
    # El manager ya está iniciado; se siembra un trabajo terminado sin otro worker.
    e.db.encolar_job("job_chat", "ws_a", "chat", document_id="doc_a", document_ids=["doc_a", "doc_b"])
    e.db.iniciar_job("job_chat")
    e.db.finalizar_job("job_chat", JobStatus.COMPLETED, resultado=json.dumps(e.resultado))
    app = FastAPI()
    app.include_router(router)
    app.state.jobs_manager, app.state.chat = e.gestor, e.servicio
    app.state.config, app.state.db, app.state.storage_provider = cfg, e.db, None

    @app.exception_handler(ErrorAplicacion)
    async def error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.error.model_dump(mode="json")})

    with TestClient(app) as cliente:
        e.cliente = cliente
        yield e
    e.gestor.detener()
    e.db.cerrar()


def cambiar_secundaria(e, cambio):
    if cambio == "retirada":
        assert e.db.retirar_documento("token-a", "doc_b")
    elif cambio == "hash":
        e.db.actualizar_documento("doc_b", DocumentStatus.READY, hash_documento="f" * 64)
    else:
        e.consulta.cambios["doc_b"] = {cambio: "modificada"}


@pytest.mark.parametrize("token", ["token-a", None])
@pytest.mark.parametrize("cambio", ["hash", "retirada", "version", "titulo", "procedencia"])
def test_todas_las_versiones_se_validan_con_y_sin_token(chat_env, token, cambio):
    e = chat_env
    assert e.servicio.autorizar_resultado("ws_a", e.resultado, token).document_ids == ["doc_a", "doc_b"]
    cambiar_secundaria(e, cambio)
    with pytest.raises(ErrorAplicacion) as error:
        e.servicio.autorizar_resultado("ws_a", e.resultado, token)
    assert error.value.status_code == 404


@pytest.mark.parametrize("ruta", ["/api/jobs/job_chat", "/api/jobs/job_chat/events"])
@pytest.mark.parametrize("cambio", ["hash", "retirada"])
def test_consulta_y_sse_no_entregan_resultado_con_secundaria_invalida(chat_env, ruta, cambio):
    e = chat_env
    cambiar_secundaria(e, cambio)
    res = e.cliente.get(ruta, headers={"Authorization": "Bearer token-a"})
    assert res.status_code == 404, res.text
    assert e.resultado["respuesta"] not in res.text


@pytest.mark.parametrize("cambio", ["hash", "retirada"])
def test_sse_reautoriza_despues_de_headers(chat_env, monkeypatch, cambio):
    e = chat_env
    autorizar, llamadas = e.servicio.autorizar_resultado, []

    def entre_lecturas(*args, **kwargs):
        llamadas.append(1)
        if len(llamadas) == 2:
            cambiar_secundaria(e, cambio)
        return autorizar(*args, **kwargs)

    monkeypatch.setattr(e.servicio, "autorizar_resultado", entre_lecturas)
    res = e.cliente.get("/api/jobs/job_chat/events", headers={"Authorization": "Bearer token-a"})
    assert res.status_code == 200
    assert len(llamadas) == 2
    assert "event: error" in res.text and "NOT_FOUND" in res.text
    assert "event: progress" not in res.text
    assert e.resultado["respuesta"] not in res.text


def test_entrega_positiva_y_aislamiento(chat_env):
    e = chat_env
    res = e.cliente.get("/api/jobs/job_chat", headers={"Authorization": "Bearer token-a"})
    assert res.status_code == 200 and res.json()["result"] == e.resultado
    stream = e.cliente.get("/api/jobs/job_chat/events", headers={"Authorization": "Bearer token-a"})
    assert stream.status_code == 200 and "event: progress" in stream.text
    assert "event: error" not in stream.text
    for ruta in ("/api/jobs/job_chat", "/api/jobs/job_chat/events"):
        assert e.cliente.get(ruta, headers={"Authorization": "Bearer token-b"}).status_code == 404
    with pytest.raises(ErrorAplicacion):
        e.servicio.autorizar_resultado("ws_a", e.resultado, "token-b")
    with pytest.raises(ErrorAplicacion):
        e.servicio.autorizar_resultado("ws_b", e.resultado)


@pytest.mark.parametrize("caso", ["sin_snapshot", "hash_primario", "id_ajeno", "duplicado", "snapshot_incompleto"])
def test_resultado_incoherente_no_reconstruye_fuentes(chat_env, caso):
    e = chat_env
    resultado = respuesta_chat(e.fuentes)
    if caso == "sin_snapshot":
        resultado["documentos_fuente"] = []
    elif caso == "hash_primario":
        resultado["document_hash"] = "otro"
    elif caso == "id_ajeno":
        resultado["document_ids"][1] = "doc_x"
        resultado["documentos_fuente"][1]["document_id"] = "doc_x"
    elif caso == "duplicado":
        resultado["documentos_fuente"].append(resultado["documentos_fuente"][0])
    else:
        resultado["documentos_fuente"].pop()
    for token in ("token-a", None):
        with pytest.raises(ErrorAplicacion) as error:
            e.servicio.autorizar_resultado("ws_a", resultado, token)
        assert error.value.status_code == 404


@pytest.mark.parametrize("token", ["token-a", None])
def test_legado_unidocumental_conserva_id_y_hash(chat_env, token):
    e = chat_env
    legado = respuesta_chat(e.fuentes[:1], legado=True)
    assert e.servicio.autorizar_resultado("ws_a", legado, token).document_id == "doc_a"
    e.db.actualizar_documento("doc_a", DocumentStatus.READY, hash_documento="otra_version")
    with pytest.raises(ErrorAplicacion):
        e.servicio.autorizar_resultado("ws_a", legado, token)


@pytest.mark.parametrize("cambio", ["hash", "retirada", "titulo"])
def test_entrega_con_autorizacion_documental_real(entorno_documental, cambio):
    """Ingesta/consulta/SQLite/HTTP reales; OCI y embeddings dobles, resultado sembrado."""
    e = entorno_documental
    e.subir("Redes", "Las redes conectan servidores y subredes. ")
    e.subir("Almacenamiento", "El almacenamiento conserva las bases de datos. ")
    consulta = e.app.state.documents_query
    token, ws = e.ws["token"], e.ws["workspace_id"]
    fuentes = [consulta.fuente_aprobada(token, d_id) for d_id in sorted(e.docs)]
    salida = respuesta_chat(fuentes)
    e.db.encolar_job("chat_sembrado", ws, "chat", document_id=fuentes[0].document_id, document_ids=sorted(e.docs))
    e.db.iniciar_job("chat_sembrado")
    e.db.finalizar_job("chat_sembrado", JobStatus.COMPLETED, resultado=json.dumps(salida))
    ruta = "/api/jobs/chat_sembrado"
    assert e.cliente.get(ruta, headers=e.headers).json()["result"] == salida
    secundaria = fuentes[1].document_id
    if cambio == "retirada":
        assert e.db.retirar_documento(token, secundaria)
    elif cambio == "titulo":
        with e.db.transaccion():
            e.db._conn.execute("UPDATE documents SET titulo='Otro título' WHERE document_id=?", (secundaria,))
        assert consulta.fuente_aprobada(token, secundaria).titulo == "Otro título"
    else:
        nuevo_hash = hashlib.sha256(b"otra version documental").hexdigest()
        clave = clave_documento(ws, secundaria)
        manifest = ManifiestoDocumento.model_validate_json(e.storage.get(clave))
        e.storage.upload(clave, manifest.model_copy(update={"hash_sha256": nuevo_hash}).model_dump_json().encode())
        e.db.actualizar_documento(secundaria, DocumentStatus.READY, hash_documento=nuevo_hash)
        # La autorización actual admite esta versión; el snapshot anterior debe rechazarla.
        assert consulta.fuente_aprobada(token, secundaria).hash == nuevo_hash
    llamadas = (e.doble.llamadas_embeddings, e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    for url in (ruta, ruta + "/events"):
        res = e.cliente.get(url, headers=e.headers)
        assert res.status_code == 404, res.text
        assert salida["respuesta"] not in res.text
    assert (e.doble.llamadas_embeddings, e.doble.llamadas_generacion, e.doble.llamadas_verificacion) == llamadas
