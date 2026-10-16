"""Metadatos #59 contrastados con transporte HTTP, servicios y fixtures existentes."""

import json
from pathlib import Path

import pytest
import test_api_generate as generaciones
import test_glossary as glosarios
import test_multimodal as visuales
from jsonschema import Draft202012Validator
from test_workspaces import cliente as cliente

pytestmark = pytest.mark.integration_mock
entorno = generaciones.entorno
entorno_generacion = generaciones.entorno
entorno_glosario = glosarios.entorno
entorno_visual = visuales.entorno
SNAPSHOT = Path(__file__).resolve().parents[2] / "docs/openapi.json"
OPERACIONES_BASE = [
    "DELETE /api/documents/{document_id}",
    "DELETE /api/sessions/current",
    "DELETE /api/workspaces/current",
    "GET /api/demo/documents",
    "GET /api/demo/documents/{document_id}",
    "GET /api/demo/documents/{document_id}/sources/{chunk_id}",
    "GET /api/documents",
    "GET /api/documents/{document_id}",
    "GET /api/documents/{document_id}/sources/{chunk_id}",
    "GET /api/documents/{document_id}/sources/{chunk_id}/image",
    "GET /api/exports/{generation_id}",
    "GET /api/generations",
    "GET /api/generations/{generation_id}",
    "GET /api/generations/{generation_id}/events",
    "GET /api/glossaries/{glossary_id}",
    "GET /api/health",
    "GET /api/jobs/{job_id}",
    "GET /api/jobs/{job_id}/events",
    "GET /api/progress",
    "GET /api/workspaces/current",
    "POST /api/chat",
    "POST /api/documents/upload",
    "POST /api/generate",
    "POST /api/generations/{generation_id}/cancel",
    "POST /api/generations/{generation_id}/persist",
    "POST /api/glossaries",
    "POST /api/jobs/{job_id}/cancel",
    "POST /api/progress/events",
    "POST /api/quizzes/{generation_id}/answers",
    "POST /api/sessions/recover",
    "POST /api/workspaces",
    "POST /api/workspaces/current/recovery-code",
]


@pytest.fixture(scope="module")
def spec():
    return json.loads(SNAPSHOT.read_bytes())


def operacion(spec, ruta, metodo="get"):
    return spec["paths"][ruta][metodo]


def comprobar_respuesta(spec, ruta, metodo, respuesta):
    declarada = operacion(spec, ruta, metodo)["responses"][str(respuesta.status_code)]
    tipo = respuesta.headers.get("content-type", "").split(";")[0]
    if tipo:
        esquema = declarada["content"][tipo]["schema"]
        if tipo == "application/json":
            Draft202012Validator(spec).evolve(schema=esquema).validate(respuesta.json())
    assert respuesta.headers["x-request-id"]
    assert "X-Request-ID" in declarada["headers"]


def test_superficie_errores_y_auth_conservados(spec):
    assert sorted(m.upper() + " " + p for p, ms in spec["paths"].items() for m in ms) == OPERACIONES_BASE
    assert "HTTPValidationError" not in spec["components"]["schemas"]
    publicas = {"/api/health", "/api/workspaces", "/api/sessions/recover"}
    for ruta, metodos in spec["paths"].items():
        for op in metodos.values():
            params = {(p["in"], p["name"]) for p in op.get("parameters", [])}
            assert {("header", "Accept-Language"), ("header", "X-Request-ID")} <= params
            if ruta not in publicas:
                assert op["security"] == [{"HTTPBearer": []}]
                assert "401" in op["responses"]
            if "422" in op["responses"]:
                assert op["responses"]["422"]["content"]["application/json"]["schema"]["$ref"].endswith(
                    "/ErrorResponse"
                )
    # Casos de control: no asignar upload/cola/errores de body a lecturas triviales.
    assert set(operacion(spec, "/api/health")["responses"]) == {"200", "500"}
    assert set(operacion(spec, "/api/sessions/current", "delete")["responses"]) == {"204", "401", "500"}


def test_recuperacion_y_cabeceras_coinciden_con_handler(spec, cliente):
    creado = cliente.post("/api/workspaces")
    comprobar_respuesta(spec, "/api/workspaces", "post", creado)
    recuperado = cliente.post("/api/sessions/recover", json={"recovery_code": creado.json()["recovery_code"]})
    comprobar_respuesta(spec, "/api/sessions/recover", "post", recuperado)
    assert set(recuperado.json()) == {"workspace_id", "token"}
    assert "No devuelve el código" in operacion(spec, "/api/sessions/recover", "post")["description"]
    error = cliente.post(
        "/api/sessions/recover", json={}, headers={"Accept-Language": "en", "X-Request-ID": "prueba-59"}
    )
    assert error.status_code == 422 and error.json()["error"]["code"] == "VALIDATION_ERROR"
    assert error.json()["request_id"] == error.headers["x-request-id"] == "prueba-59"
    assert error.json()["error"]["message"] == "Some form field has an invalid value."
    comprobar_respuesta(spec, "/api/sessions/recover", "post", error)
    assert cliente.get("/api/health", headers={"X-Request-ID": "no valido"}).headers["x-request-id"] != "no valido"


def test_422_reales_body_query_y_headers_y_bearer(spec, entorno):
    e = entorno
    casos = [
        ("post", "/api/generate", "/api/generate", {"json": {}}),
        ("post", "/api/chat", "/api/chat", {"json": {}}),
        ("post", "/api/glossaries", "/api/glossaries", {"json": {}}),
        ("post", "/api/progress/events", "/api/progress/events", {"json": {}}),
        ("post", "/api/quizzes/x/answers", "/api/quizzes/{generation_id}/answers", {"json": {}}),
        ("get", "/api/documents?limit=0", "/api/documents", {}),
        ("get", "/api/generations?limit=101", "/api/generations", {}),
        ("get", "/api/jobs/x/events", "/api/jobs/{job_id}/events", {"headers": {**e.headers, "Last-Event-ID": "-1"}}),
        (
            "get",
            "/api/generations/x/events",
            "/api/generations/{generation_id}/events",
            {"headers": {**e.headers, "Last-Event-ID": str(2**63)}},
        ),
    ]
    for metodo, url, ruta, opciones in casos:
        respuesta = e.cliente.request(metodo, url, **{"headers": e.headers, **opciones})
        assert respuesta.status_code == 422, respuesta.text
        assert respuesta.json()["error"]["code"] == "VALIDATION_ERROR"
        comprobar_respuesta(spec, ruta, metodo, respuesta)
    for ruta, metodos in spec["paths"].items():
        for metodo, op in metodos.items():
            if "security" in op:
                url = ruta.replace("{generation_id}", "x").replace("{job_id}", "x").replace("{glossary_id}", "x")
                url = url.replace("{document_id}", "x").replace("{chunk_id}", "x")
                respuesta = e.cliente.request(metodo, url)
                assert respuesta.status_code == 401, (url, respuesta.text)
                comprobar_respuesta(spec, ruta, metodo, respuesta)


def test_limites_idempotencia_distintos_siguen_siendo_del_servicio(spec, entorno):
    e = entorno
    for largo, esperado in [(200, 404), (201, 400)]:
        r = e.cliente.post(
            "/api/generate",
            json={**e.solicitud, "document_id": "ausente"},
            headers={**e.headers, "Idempotency-Key": "k" * largo},
        )
        assert r.status_code == esperado, r.text
        comprobar_respuesta(spec, "/api/generate", "post", r)
    for ruta, body in [
        (
            "/api/chat",
            {"document_id": e.doc, "pregunta": "VCN", "perfil_destinatario": "principiante", "idioma_salida": "es"},
        ),
        ("/api/glossaries", {"document_id": e.doc, "perfil_destinatario": "principiante", "idioma_salida": "es"}),
        (
            "/api/progress/events",
            {"event_id": "largo", "tipo": "concepto_revisado", "concepto": "VCN", "document_id": e.doc},
        ),
    ]:
        r = e.cliente.post(ruta, json=body, headers={**e.headers, "Idempotency-Key": "k" * 129})
        assert r.status_code == 400, r.text
        comprobar_respuesta(spec, ruta, "post", r)
    subida = e.cliente.post(
        "/api/documents/upload",
        headers={**e.headers, "Idempotency-Key": "k" * 300},
        json={"documento_titulo": "Texto", "documento_contenido": "Una VCN conserva subredes."},
    )
    assert subida.status_code == 202, subida.text
    comprobar_respuesta(spec, "/api/documents/upload", "post", subida)
    terminado = generaciones.esperar(e, subida.json())
    assert terminado["result"]["document_id"] == subida.json()["document_id"]
    # El job genérico conserva su resultado real, sin introducir otra proyección.
    assert {"workspace_id", "hash_sha256", "metadatos", "cobertura"} <= set(terminado["result"])
    comprobar_respuesta(
        spec, "/api/jobs/{job_id}", "get", e.cliente.get(subida.json()["status_url"], headers=e.headers)
    )


def test_quiz_estudiante_export_canonico_y_sse_de_generacion(spec, entorno):
    e = entorno
    recibo = generaciones.generar(e, formato_salida="quiz").json()
    resultado = generaciones.esperar(e, recibo)
    estudiante = resultado["contenido"]["contenido_adaptado"]["preguntas"][0]
    assert "correct_option_id" not in estudiante and "justificacion" not in estudiante
    comprobar_respuesta(
        spec, "/api/generations/{generation_id}", "get", e.cliente.get(recibo["status_url"], headers=e.headers)
    )
    ruta = "/api/exports/{generation_id}"
    export = e.cliente.get("/api/exports/" + recibo["generation_id"], headers=e.headers)
    comprobar_respuesta(spec, ruta, "get", export)
    pregunta = export.json()["contenido_adaptado"]["preguntas"][0]
    assert "correct_option_id" in pregunta and "justificacion" in pregunta
    assert export.content == e.storage.get(
        "outputs/" + e.ws["workspace_id"] + "/" + recibo["generation_id"] + "/content.json"
    )
    for nombre in ("Content-Disposition", "Cache-Control", "X-Content-Type-Options"):
        assert nombre in operacion(spec, ruta)["responses"]["200"]["headers"]
        assert export.headers[nombre]
    for formato in ("csv", "desconocido"):
        r = e.cliente.get("/api/exports/" + recibo["generation_id"], params={"format": formato}, headers=e.headers)
        assert r.status_code == 422 and r.json()["error"]["code"] == "EXPORT_INCOMPATIBLE"
        comprobar_respuesta(spec, ruta, "get", r)
    param = next(p for p in operacion(spec, ruta)["parameters"] if p["name"] == "format")
    assert param["schema"]["type"] == "string" and "enum" not in param["schema"]
    verificar_stream(spec, "/api/generations/{generation_id}/events", e, recibo, "generation_id")


def verificar_stream(spec, ruta, e, recibo, id_nombre):
    r = e.cliente.get(recibo["events_url"], headers=e.headers)
    assert r.status_code == 200
    comprobar_respuesta(spec, ruta, "get", r)
    op = operacion(spec, ruta)
    assert set(op["responses"]["200"]["content"]) == {"text/event-stream"}
    assert id_nombre in op["description"] and "event: error" in op["description"] and "sin id" in op["description"]
    cursor = next(p for p in op["parameters"] if p["name"] == "Last-Event-ID")["schema"]
    assert cursor["minimum"] == 0 and cursor["maximum"] == 2**63 - 1 and cursor["default"] == 0
    ultimo = None
    for frame in r.text.rstrip().split("\n\n"):
        lineas = frame.splitlines()
        assert lineas[0].startswith("id: ") and lineas[1] == "event: progress"
        ultimo = int(lineas[0][4:])
        data = json.loads(lineas[2][6:])
        assert set(data) == {id_nombre, "step", "status", "iteration"}
        assert data[id_nombre] == recibo.get(id_nombre, recibo.get("job_id"))
    assert ultimo is not None
    assert e.cliente.get(recibo["events_url"], headers={**e.headers, "Last-Event-ID": str(ultimo)}).text == ""


def test_glosario_200_202_y_sse_comun(spec, entorno_glosario):
    e = entorno_glosario
    primero = glosarios.glosario(e)
    assert primero.status_code == 202
    comprobar_respuesta(spec, "/api/glossaries", "post", primero)
    generaciones.esperar(e, primero.json())
    cache = glosarios.glosario(e)
    assert cache.status_code == 200
    comprobar_respuesta(spec, "/api/glossaries", "post", cache)
    assert cache.json()["glossary_id"] == cache.json()["job_id"] and cache.json()["result"]["terminos"]
    verificar_stream(spec, "/api/jobs/{job_id}/events", e, primero.json(), "job_id")


def test_png_real_y_multipart_documentados(spec, entorno_visual):
    e = entorno_visual
    datos, _ = visuales.subir(e, visuales.escaneo())
    chunks = e.app.state.documents_query.chunks(e.ws["token"], datos["document_id"])
    chunk = next(c for c in chunks if c.source_type == "image_description")
    ruta = "/api/documents/{document_id}/sources/{chunk_id}/image"
    r = e.cliente.get(f"/api/documents/{datos['document_id']}/sources/{chunk.chunk_id}/image", headers=e.headers)
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n") and r.headers["content-type"] == "image/png"
    comprobar_respuesta(spec, ruta, "get", r)
    assert set(operacion(spec, ruta)["responses"]["200"]["content"]) == {"image/png"}
    subida = operacion(spec, "/api/documents/upload", "post")["requestBody"]["content"]
    assert set(subida) == {"application/json", "multipart/form-data"}
    assert subida["multipart/form-data"]["schema"]["required"] == ["file"]
    # El parser real rechaza titulo vacío, como ahora indica el metadato.
    invalido = e.cliente.post(
        "/api/documents/upload",
        headers=e.headers,
        files={"file": ("texto.txt", b"Texto", "text/plain")},
        data={"titulo": ""},
    )
    assert invalido.status_code == 422, invalido.text
    comprobar_respuesta(spec, "/api/documents/upload", "post", invalido)
