"""Recorridos faltantes de §12.2; HTTP/worker/SQLite/Chroma reales, proveedores dobles."""

import hashlib
import json

import pytest
from app.jobs.manager import ReintentableError
from app.schemas.responses import GenerationJobResponse
from test_api_generate import esperar, generar

pytestmark = [pytest.mark.e2e, pytest.mark.integration_mock]


def referencias(valor):
    if isinstance(valor, dict):
        for clave, item in valor.items():
            if clave == "referencias":
                yield from item
            else:
                yield from referencias(item)
    elif isinstance(valor, list):
        for item in valor:
            yield from referencias(item)


def test_tres_adaptaciones_multilingues_recuperan_historial_citas_y_progreso(entorno):
    e = entorno
    titulo = "Capacitación: conexión, inglês y português"
    texto = "# Conexión y configuração\n\n" + "Una VCN conecta subredes privadas y conserva aislamiento. " * 20
    subida = e.cliente.post(
        "/api/documents/upload",
        headers={**e.headers, "Idempotency-Key": "fuente-multilingue"},
        files={"file": ("conexión.md", texto.encode("utf-8"), "text/markdown")},
        data={"titulo": titulo},
    )
    assert subida.status_code == 202, subida.text
    esperar(e, subida.json())
    doc = subida.json()["document_id"]
    detalle = e.cliente.get("/api/documents/" + doc, headers=e.headers).json()
    assert detalle["status"] == "ready" and detalle["titulo"] == titulo
    resultados = {}
    for idioma, perfil, formato in (
        ("es", "principiante", "tutorial"),
        ("en", "junior_ssr", "quiz"),
        ("pt", "lider_tecnico", "flashcards"),
    ):
        parametros = dict(document_id=doc, idioma_salida=idioma, perfil_destinatario=perfil, formato_salida=formato)
        recibo = generar(e, "adaptacion-" + idioma, **parametros)
        assert recibo.status_code == 202, recibo.text
        recibo = recibo.json()
        resultado = esperar(e, recibo)
        GenerationJobResponse.model_validate(resultado)
        contenido = resultado["contenido"]
        assert resultado["persistencia"] == {"status_upload": "completado", "provider": "mock"}
        assert contenido["documento_fuente"]["document_id"] == doc
        assert contenido["documento_fuente"]["hash"] == hashlib.sha256(texto.encode("utf-8")).hexdigest()
        assert contenido["metadatos"]["idioma_salida"] == idioma
        assert contenido["metadatos"]["perfil_aplicado"] == perfil
        assert contenido["contenido_adaptado"]["tipo"] == formato
        exportacion = e.cliente.get("/api/exports/" + recibo["generation_id"], headers=e.headers)
        assert exportacion.status_code == 200
        canonico = exportacion.json()
        refs = list(referencias(canonico["contenido_adaptado"]))
        assert refs
        for ref in refs:
            fuente = e.cliente.get(f"/api/documents/{doc}/sources/{ref['chunk_id']}", headers=e.headers)
            assert fuente.status_code == 200
            fuente = fuente.json()
            assert fuente["document_id"] == doc
            assert " ".join(fuente["texto"].split()) in " ".join(texto.split())
            assert fuente["seccion_id"].startswith("seccion:")
            assert fuente["pagina"] == ref["pagina"] and fuente["seccion"] == ref["seccion"]
            assert "Conexión" in fuente["seccion"] and "configuração" in fuente["seccion"]
        resultados[recibo["generation_id"]] = resultado
        if formato == "quiz":
            assert "correct_option_id" not in json.dumps(contenido)
            assert "justificacion" not in json.dumps(contenido)
            pregunta = canonico["contenido_adaptado"]["preguntas"][0]
            respuesta = e.cliente.post(
                "/api/quizzes/" + recibo["generation_id"] + "/answers",
                headers=e.headers,
                json={
                    "event_id": "respuesta-a-recuperar",
                    "question_id": pregunta["id"],
                    "option_id": pregunta["correct_option_id"],
                },
            )
            assert respuesta.status_code == 200 and respuesta.json()["correcta"]
            assert respuesta.json()["justificacion"] and respuesta.json()["referencias"]
    assert len(resultados) == 3
    progreso = e.cliente.get("/api/progress", headers=e.headers).json()
    assert progreso["quiz_intentos"] == progreso["quiz_aciertos_primer_intento"] == 1
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    anteriores = e.headers.copy()
    assert e.cliente.delete("/api/sessions/current", headers=anteriores).status_code == 204
    assert e.cliente.get("/api/generations", headers=anteriores).status_code == 401
    sesion = e.cliente.post("/api/sessions/recover", json={"recovery_code": e.ws["recovery_code"]})
    assert sesion.status_code == 201, sesion.text
    assert sesion.json()["workspace_id"] == e.ws["workspace_id"]
    assert sesion.json()["token"] != e.ws["token"]
    e.headers = {"Authorization": "Bearer " + sesion.json()["token"]}
    historial = e.cliente.get("/api/generations", params={"document_id": doc}, headers=e.headers)
    assert historial.status_code == 200
    assert {item["generation_id"] for item in historial.json()["items"]} == set(resultados)
    assert e.cliente.get("/api/progress", headers=e.headers).json() == progreso
    for generation_id, previo in resultados.items():
        recuperado = e.cliente.get("/api/generations/" + generation_id, headers=e.headers)
        assert recuperado.status_code == 200 and recuperado.json()["contenido"] == previo["contenido"]
        exportacion = e.cliente.get("/api/exports/" + generation_id, headers=e.headers)
        assert exportacion.status_code == 200
        assert exportacion.json()["documento_fuente"]["titulo"] == titulo
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion, e.doble.llamadas_embeddings)
    assert e.cliente.get("/api/generations", headers=anteriores).status_code == 401


@pytest.mark.parametrize(
    "fallo,terminal,codigo,redacciones,revisiones",
    [
        ("sin_afirmaciones", "rejected_quality", "INVALID_STATE", 3, 3),
        ("vacio", "failed", "INVALID_STATE", 1, 1),
        ("caido", "failed", "PROVIDER_UNAVAILABLE", 1, 3),
    ],
)
def test_evaluador_insuficiente_no_publica_ni_reinicia_redaccion(
    entorno, monkeypatch, fallo, terminal, codigo, redacciones, revisiones
):
    e = entorno

    def evaluar(**entrada):
        e.doble.llamadas_verificacion += 1
        if fallo == "caido":
            raise ReintentableError("Detalle privado del evaluador", retry_after=0)
        return json.dumps({"afirmaciones": []}) if fallo == "sin_afirmaciones" else ""

    monkeypatch.setattr(e.doble, "_revisar", evaluar)
    recibo = generar(e, "evaluacion-insegura")
    assert recibo.status_code == 202, recibo.text
    recibo = recibo.json()
    resultado = esperar(e, recibo, terminal)
    assert resultado["contenido"] is None and resultado["error"]["code"] == codigo
    causa = {"sin_afirmaciones": "REJECTED_QUALITY", "vacio": "INVALID_EVALUATION", "caido": "PROVIDER_UNAVAILABLE"}
    assert resultado["error"]["details"]["causa"] == causa[fallo]
    assert e.doble.llamadas_generacion == redacciones
    assert e.doble.llamadas_verificacion == revisiones
    assert "Detalle privado" not in json.dumps(resultado)
    assert e.cliente.get("/api/exports/" + recibo["generation_id"], headers=e.headers).status_code == 409
    assert e.cliente.post(recibo["status_url"] + "/persist", headers=e.headers).status_code == 409
    assert not e.storage.list("outputs/" + e.ws["workspace_id"] + "/").items
    llamadas = (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
    assert generar(e, "evaluacion-insegura").json()["generation_id"] == recibo["generation_id"]
    assert llamadas == (e.doble.llamadas_generacion, e.doble.llamadas_verificacion)
