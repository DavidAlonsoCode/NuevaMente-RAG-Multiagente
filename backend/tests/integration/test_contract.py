"""HTTP + SQLite + Chroma + worker + grafo reales; proveedores sin red (#52)."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest
from app.api.routes import jobs
from app.jobs.manager import CuotasModelo, CuotasProveedor, ReintentableError
from app.session.manager import SessionManager
from app.storage.object_keys import clave_output
from app.storage.provider import StorageUnavailable
from fastapi.testclient import TestClient
from test_api_generate import esperar, generar
from test_chat import chat
from test_glossary import glosario

pytestmark = pytest.mark.integration_mock


def envoltorio(respuesta, status, codigo):
    assert respuesta.status_code == status, respuesta.text
    cuerpo = respuesta.json()
    assert set(cuerpo) == {"error", "request_id"}
    assert set(cuerpo["error"]) == {"code", "message", "details"}
    assert cuerpo["error"]["code"] == codigo
    assert cuerpo["request_id"] == respuesta.headers.get("x-request-id")
    assert cuerpo["request_id"] and cuerpo["error"]["message"]
    return cuerpo


def frames(respuesta):
    assert respuesta.status_code == 200
    assert respuesta.headers["content-type"].startswith("text/event-stream")
    assert respuesta.headers["x-accel-buffering"] == "no"
    assert respuesta.text.endswith("\n\n")
    resultado = []
    for frame in respuesta.text.removesuffix("\n\n").split("\n\n"):
        campos = dict(linea.split(": ", 1) for linea in frame.splitlines())
        assert set(campos) == {"id", "event", "data"} and campos["event"] == "progress"
        resultado.append((int(campos["id"]), json.loads(campos["data"])))
    assert [f[0] for f in resultado] == sorted({f[0] for f in resultado})
    return resultado


@pytest.mark.parametrize("formato", ["flashcards", "quiz", "tutorial", "resumen_ejecutivo", "guion_clase"])
@pytest.mark.e2e
def test_upload_ready_generacion_exportacion_y_sse(entorno, formato):
    e = entorno
    assert e.cliente.get("/api/documents/" + e.doc, headers=e.headers).json()["status"] == "ready"
    recibo = generar(e, formato_salida=formato).json()
    resultado = esperar(e, recibo)
    assert resultado["contenido"] and resultado["persistencia"]["status_upload"] == "completado"
    formatos = ("json", "md", "pdf", "csv", "tsv", "apkg") if formato == "flashcards" else ("json", "md", "pdf")
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    for extension in formatos:
        res = e.cliente.get("/api/exports/" + recibo["generation_id"], params={"format": extension}, headers=e.headers)
        assert res.status_code == 200 and res.content
        assert res.headers["content-disposition"].endswith(extension + '"')
    eventos = frames(e.cliente.get(recibo["events_url"], headers=e.headers))
    assert {"writer", "critic", "finalizer"} <= {f[1]["step"] for f in eventos}
    ultimo = eventos[-2][0]
    nuevos = frames(e.cliente.get(recibo["events_url"], headers={**e.headers, "Last-Event-ID": str(ultimo)}))
    assert all(f[0] > ultimo for f in nuevos)
    assert e.cliente.get(recibo["events_url"], headers={**e.headers, "Last-Event-ID": str(nuevos[-1][0])}).text == ""
    assert e.cliente.get(recibo["status_url"], headers=e.headers).json()["status"] == "completed"
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)


@pytest.mark.parametrize("terminal", ["rejected_quality", "failed"])
@pytest.mark.e2e
def test_terminales_no_aprobados_no_publican(entorno, monkeypatch, terminal):
    e = entorno
    if terminal == "rejected_quality":
        e.doble.rechazar = True
    else:

        def fallar(*a, **kw):
            raise ValueError("secreto del proveedor")

        monkeypatch.setattr(e.doble, "generar_sync", fallar)
    recibo = generar(e).json()
    resultado = esperar(e, recibo, terminal)
    assert resultado["contenido"] is None and resultado["error"]
    envoltorio(e.cliente.get("/api/exports/" + recibo["generation_id"], headers=e.headers), 409, "INVALID_STATE")
    assert not e.storage.list("outputs/" + e.ws["workspace_id"] + "/").items
    assert "secreto del proveedor" not in json.dumps(resultado)
    if terminal == "rejected_quality":
        assert e.doble.llamadas_generacion == 3


@pytest.mark.parametrize("en_cola", [False, True])
@pytest.mark.e2e
def test_cancelacion_no_publica_ni_repite_proveedor(entorno, en_cola):
    e = entorno
    liberar = threading.Event()
    try:
        if en_cola:
            otro = e.cliente.post("/api/workspaces").json()
            e.gestor.enqueue(otro["workspace_id"], "chat", lambda ctx: liberar.wait(10))
        else:
            e.doble.bloquear = liberar
        recibo = generar(e, "cancelar-intencion").json()
        estado = e.cliente.get(recibo["status_url"], headers=e.headers).json()
        assert estado["status"] in ({"queued"} if en_cola else {"queued", "running"})
        assert e.cliente.post(recibo["cancel_url"], headers=e.headers).status_code == 200
        liberar.set()
        final = esperar(e, recibo, "cancelled")
        assert final["contenido"] is None
        assert not e.storage.list("exports/" + e.ws["workspace_id"] + "/").items
        assert not e.storage.list("outputs/" + e.ws["workspace_id"] + "/").items
        if en_cola:
            assert e.doble.llamadas_generacion == 0
    finally:
        liberar.set()


@pytest.mark.e2e
def test_idempotencia_concurrente_en_curso_y_conflicto(entorno):
    e = entorno
    e.doble.bloquear = threading.Event()
    try:
        with ThreadPoolExecutor(6) as pool:
            respuestas = list(pool.map(lambda _: generar(e, "misma-intencion"), range(6)))
        assert all(r.status_code == 202 for r in respuestas)
        assert len({r.json()["generation_id"] for r in respuestas}) == 1
        envoltorio(generar(e, "misma-intencion", idioma_salida="en"), 409, "IDEMPOTENCY_CONFLICT")
        e.doble.bloquear.set()
        esperar(e, respuestas[0].json())
        assert e.doble.llamadas_generacion == 1
    finally:
        e.doble.bloquear.set()


@pytest.mark.e2e
def test_quiz_y_progreso_repetidos_sin_cuota_ni_duplicados(entorno):
    e = entorno
    recibo = generar(e, formato_salida="quiz").json()
    estudiante = esperar(e, recibo)["contenido"]
    assert "correct_option_id" not in json.dumps(estudiante)
    p = e.app.state.generations.canonico(e.ws["token"], recibo["generation_id"]).contenido_adaptado.preguntas[0]
    ruta = "/api/quizzes/" + recibo["generation_id"] + "/answers"
    body = {"event_id": "un-intento", "question_id": p.id, "option_id": p.correct_option_id}
    llamadas = e.doble.llamadas_generacion + e.doble.llamadas_verificacion
    with ThreadPoolExecutor(4) as pool:
        respuestas = list(pool.map(lambda _: e.cliente.post(ruta, json=body, headers=e.headers), range(4)))
    assert all(r.status_code == 200 and r.json()["correcta"] for r in respuestas)
    progreso = e.cliente.get("/api/progress", headers=e.headers).json()
    assert progreso["quiz_intentos"] == progreso["quiz_total_primer_intento"] == 1
    opcion = next(o.option_id for o in p.opciones if o.option_id != p.correct_option_id)
    envoltorio(e.cliente.post(ruta, json={**body, "option_id": opcion}, headers=e.headers), 409, "IDEMPOTENCY_CONFLICT")
    assert e.doble.llamadas_generacion + e.doble.llamadas_verificacion == llamadas


@pytest.mark.e2e
def test_persist_503_concurrente_recupera_aprobado_sin_llm(entorno, monkeypatch):
    e = entorno
    upload = e.storage.upload

    def fallar(clave, *a, **kw):
        if clave.startswith("outputs/"):
            raise StorageUnavailable("secreto privado")
        return upload(clave, *a, **kw)

    monkeypatch.setattr(e.storage, "upload", fallar)
    recibo = generar(e).json()
    final = esperar(e, recibo, "failed")
    assert final["error"]["code"] == "STORAGE_UNAVAILABLE" and final["contenido"] is None
    aprobado = e.db.aprobado_retenido(recibo["generation_id"])
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    monkeypatch.setattr(e.storage, "upload", upload)
    with ThreadPoolExecutor(4) as pool:
        respuestas = list(
            pool.map(lambda _: e.cliente.post(recibo["status_url"] + "/persist", headers=e.headers), range(4))
        )
    assert all(r.status_code == 202 for r in respuestas)
    esperar(e, recibo)
    assert e.storage.get(clave_output(e.ws["workspace_id"], recibo["generation_id"])) == aprobado.encode()
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)


@pytest.mark.parametrize(
    "http,codigo",
    [
        (400, "INVALID_REQUEST"),
        (401, "SESSION_INVALID"),
        (404, "NOT_FOUND"),
        (409, "INVALID_STATE"),
        (413, "DOCUMENT_TOO_LARGE"),
        (422, "VALIDATION_ERROR"),
        (429, "RATE_LIMITED"),
        (500, "INTERNAL"),
        (503, "STORAGE_UNAVAILABLE"),
    ],
)
def test_envoltorios_http_con_request_id(entorno, monkeypatch, http, codigo):
    e = entorno
    if http == 400:
        res = e.cliente.get("/api/generations?cursor=no-es-cursor", headers=e.headers)
    elif http == 401:
        res = e.cliente.get("/api/generations")
    elif http == 404:
        res = e.cliente.get("/api/documents/inexistente", headers=e.headers)
    elif http == 409:
        recibo = generar(e).json()
        esperar(e, recibo)
        res = e.cliente.post(recibo["status_url"] + "/cancel", headers=e.headers)
    elif http == 413:
        res = e.cliente.post(
            "/api/documents/upload",
            headers=e.headers,
            files={"file": ("grande.txt", b"x" * (5 * 1024 * 1024 + 1), "text/plain")},
        )
    elif http == 422:
        res = e.cliente.post("/api/generate", json={}, headers=e.headers)
    elif http == 429:
        e.doble.bloquear = threading.Event()
        try:
            generar(e)
            res = generar(e)
        finally:
            e.doble.bloquear.set()
    else:

        def fallo(*a, **k):
            raise StorageUnavailable("privado") if http == 503 else RuntimeError("privado")

        monkeypatch.setattr(e.app.state.generations, "pagina", fallo)
        cliente_error = TestClient(e.app, raise_server_exceptions=False)
        try:
            res = cliente_error.get("/api/generations", headers=e.headers)
        finally:
            cliente_error.close()
    datos = envoltorio(res, http, codigo)
    assert "privado" not in json.dumps(datos)


@pytest.mark.parametrize("modo", ["multiagente", "basico"])
@pytest.mark.parametrize("ruta", ["estado", "sse", "export", "quiz", "documento", "progreso"])
@pytest.mark.e2e
def test_dos_espacios_recursos_ajenos_404(entorno, ruta, modo):
    e = entorno
    recibo = generar(e, formato_salida="quiz", modo_generacion=modo).json()
    esperar(e, recibo)
    otro = e.cliente.post("/api/workspaces").json()
    headers = {"Authorization": "Bearer " + otro["token"]}
    urls = {
        "estado": recibo["status_url"],
        "sse": recibo["events_url"],
        "export": "/api/exports/" + recibo["generation_id"],
        "documento": "/api/documents/" + e.doc,
    }
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    if ruta == "progreso":
        res = e.cliente.post(
            "/api/progress/events",
            headers=headers,
            json={"event_id": "ajeno", "tipo": "concepto_revisado", "document_id": e.doc, "concepto": "VCN"},
        )
        assert e.cliente.get("/api/progress", headers=headers).json()["conceptos_revisados"] == 0
    elif ruta == "quiz":
        res = e.cliente.post(
            "/api/quizzes/" + recibo["generation_id"] + "/answers",
            headers=headers,
            json={"event_id": "ajeno", "question_id": "q_1", "option_id": "A"},
        )
    else:
        res = e.cliente.get(urls[ruta], headers=headers)
    envoltorio(res, 404, "NOT_FOUND")
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)


@pytest.mark.parametrize("fallos,terminal", [(2, "completed"), (99, "failed")])
def test_proveedor_sync_timeout_cuota_por_intento_y_retries_limitados(entorno, monkeypatch, fallos, terminal):
    e = entorno
    e.gestor.controles = replace(e.gestor.controles, timeout_por_llamada_segundos=0.5, base_backoff_segundos=0.001)
    original = e.doble.generar_sync
    entradas = []
    modelo = e.app.state.config.gemini_generation_model
    antes = e.gestor.cuotas.disponibles_hoy(modelo)

    def proveedor(prompt, **kw):
        entradas.append((kw["timeout"], threading.current_thread().name))
        if len(entradas) <= fallos:
            raise ReintentableError("privado")
        return original(prompt, **kw)

    monkeypatch.setattr(e.doble, "generar_sync", proveedor)
    recibo = generar(e).json()
    resultado = esperar(e, recibo, terminal)
    assert len(entradas) == 3
    assert all(0 < timeout <= 0.5 and nombre != threading.current_thread().name for timeout, nombre in entradas)
    esperadas = 3 + (e.doble.llamadas_verificacion if modelo == e.app.state.config.gemini_verification_model else 0)
    assert antes - e.gestor.cuotas.disponibles_hoy(modelo) == esperadas
    if terminal == "failed":
        assert resultado["error"]["code"] == "PROVIDER_UNAVAILABLE"
        assert resultado["error"]["details"]["causa"] == "PROVIDER_UNAVAILABLE"
        assert resultado["contenido"] is None
    llamadas = len(entradas)
    frames(e.cliente.get(recibo["events_url"], headers=e.headers))
    assert len(entradas) == llamadas


def test_cuota_agotada_impide_otro_intento_del_proveedor(entorno, monkeypatch):
    e = entorno
    cfg = e.app.state.config
    modelo = cfg.gemini_generation_model
    e.gestor.cuotas = CuotasProveedor(
        {
            m: CuotasModelo(1000, 10000000, 1 if m == modelo else 1000)
            for m in {modelo, cfg.gemini_embedding_model, cfg.gemini_verification_model}
        }
    )
    entradas = []

    def proveedor(*a, **kw):
        entradas.append(kw["timeout"])
        raise ReintentableError("privado")

    monkeypatch.setattr(e.doble, "generar_sync", proveedor)
    recibo = generar(e).json()
    resultado = esperar(e, recibo, "failed")
    assert len(entradas) == 1 and resultado["error"]["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_heartbeat_desconexion_consulta_estado_y_no_nueva_ejecucion(entorno, monkeypatch):
    e = entorno
    liberar = threading.Event()
    ejecuciones = []

    def ejecutar(ctx):
        ejecuciones.append(1)
        liberar.wait(10)
        return {"resultado": "simulado"}

    job = e.gestor.enqueue(e.ws["workspace_id"], "ingestion", ejecutar)
    limite = time.monotonic() + 3
    while e.gestor.estado(job)["status"] != "running" and time.monotonic() < limite:
        time.sleep(0.01)
    ultimo = e.db.eventos_desde_job(job)[-1]["id"]
    monkeypatch.setattr(jobs, "INTERVALO_HEARTBEAT", 0)
    monkeypatch.setattr(jobs, "INTERVALO_SONDEO", 0)
    manager = SessionManager(e.db, e.storage, 30, 24)

    class Peticion:
        desconectada = False

        async def is_disconnected(self):
            return self.desconectada

    request = Peticion()
    sesion = {"token": e.ws["token"], "workspace_id": e.ws["workspace_id"]}
    stream = jobs._stream(request, e.gestor, manager, sesion, job, ultimo)
    try:
        assert await anext(stream) == ": heartbeat\n\n"
        request.desconectada = True
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
        assert e.cliente.get("/api/jobs/" + job, headers=e.headers).json()["status"] == "running"
        liberar.set()
        final = esperar(e, {"status_url": "/api/jobs/" + job})
        assert final["result"] == {"resultado": "simulado"}
        frames(e.cliente.get("/api/jobs/" + job + "/events", headers=e.headers))
        assert ejecuciones == [1]
    finally:
        liberar.set()
        await stream.aclose()


@pytest.mark.parametrize("tipo", ["chat", "glosario"])
@pytest.mark.e2e
def test_endpoints_comunes_consultan_sse_citas_y_aislamiento(request, tipo):
    e = request.getfixturevalue("entorno_chat" if tipo == "chat" else "entorno_glosario")
    res = chat(e) if tipo == "chat" else glosario(e)
    assert res.status_code == 202
    recibo = res.json()
    assert esperar(e, recibo)["result"]
    eventos = frames(e.cliente.get(recibo["events_url"], headers=e.headers))
    assert all(f[1]["job_id"] == recibo["job_id"] for f in eventos)
    llamadas = e.doble.llamadas_generacion
    assert e.cliente.get(recibo["events_url"], headers={**e.headers, "Last-Event-ID": str(eventos[-1][0])}).text == ""
    otro = e.cliente.post("/api/workspaces").json()
    for sufijo, metodo in (("", "get"), ("/events", "get"), ("/cancel", "post")):
        envoltorio(
            getattr(e.cliente, metodo)(
                recibo["status_url"] + sufijo, headers={"Authorization": "Bearer " + otro["token"]}
            ),
            404,
            "NOT_FOUND",
        )
    assert e.doble.llamadas_generacion == llamadas


def test_proveedor_sync_en_worker_comun_cuota_timeout_y_api(entorno):
    e = entorno
    e.gestor.cuotas = CuotasProveedor({"modelo_prueba": CuotasModelo(100, 10000, 5)})
    entradas = []

    def proveedor(*, timeout):
        entradas.append(timeout)
        if len(entradas) < 3:
            raise ReintentableError("privado")
        return {"prueba": "proveedor simulado"}

    job = e.gestor.enqueue(e.ws["workspace_id"], "ingestion", lambda ctx: ctx.llamar(proveedor, modelo="modelo_prueba"))
    final = esperar(e, {"status_url": "/api/jobs/" + job})
    assert final["result"] == {"prueba": "proveedor simulado"}
    assert len(entradas) == 3 and all(0 < t <= 60 for t in entradas)
    assert e.gestor.cuotas.disponibles_hoy("modelo_prueba") == 2
    frames(e.cliente.get("/api/jobs/" + job + "/events", headers=e.headers))
    assert len(entradas) == 3
