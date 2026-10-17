"""Modos de generación sobre API/worker/RAG reales, proveedores dobles explícitos."""

import copy
import io
import json
import threading

import pytest
import test_api_generate as api_cases
from app.schemas.enums import PedagogicalFormat
from app.schemas.responses import cargar_paquete_json
from app.storage.provider import StorageUnavailable
from pydantic import ValidationError
from pypdf import PdfReader

pytestmark = pytest.mark.integration_mock
entorno = api_cases.entorno


@pytest.mark.parametrize("formato", PedagogicalFormat)
def test_basico_cinco_formatos_sin_juez_ni_score(entorno, formato):
    e = entorno
    r = api_cases.generar(e, modo_generacion="basico", formato_salida=formato.value)
    assert r.status_code == 202, r.text
    job = api_cases.esperar(e, r.json())
    p = job["contenido"]
    assert (p["schema_version"], p["modo_generacion"], p["status"]) == ("1.1", "basico", "no_evaluado")
    assert p["evaluacion_calidad"]["estado_evaluacion"] == "no_realizada"
    assert all(v is None for k, v in p["evaluacion_calidad"].items() if k != "estado_evaluacion")
    assert p["trazabilidad"]["modelo_verificacion"] is None
    assert e.doble.llamadas_generacion == 1 and e.doble.llamadas_verificacion == 0
    if formato == PedagogicalFormat.QUIZ:
        assert "correct_option_id" not in json.dumps(p) and "justificacion" not in json.dumps(p)
        pregunta = e.app.state.generations.canonico(e.ws["token"], job["generation_id"]).contenido_adaptado.preguntas[0]
        body = dict(event_id="quiz-basico", question_id=pregunta.id, option_id=pregunta.correct_option_id)
        ruta = f"/api/quizzes/{job['generation_id']}/answers"
        respuesta = e.cliente.post(ruta, json=body, headers=e.headers)
        assert respuesta.status_code == 200 and respuesta.json()["correcta"] is True
        assert respuesta.json()["justificacion"] == pregunta.justificacion
        assert e.cliente.post(ruta, json=body, headers=e.headers).json() == respuesta.json()
        assert e.cliente.get("/api/progress", headers=e.headers).json()["quiz_intentos"] == 1
        assert e.doble.llamadas_generacion == 1 and e.doble.llamadas_verificacion == 0
    else:
        assert cargar_paquete_json(json.dumps(p)).status == "no_evaluado"
    assert job["persistencia"] == {"provider": "mock", "status_upload": "completado"}


def test_cambio_modo_conserva_documento_resultados_y_legacy(entorno):
    e = entorno
    source, ids = None, []
    for mode in ("basico", "multiagente", "basico", None):
        r = api_cases.generar(e, **({"modo_generacion": mode} if mode else {}))
        job = api_cases.esperar(e, r.json())
        p = job["contenido"]
        assert p["documento_fuente"] == (source or p["documento_fuente"])
        source = p["documento_fuente"]
        ids.append(job["generation_id"])
        if mode == "multiagente":
            assert p["schema_version"] == "1.1" and p["status"] == "aprobado"
        elif mode is None:
            assert p["schema_version"] == "1.0" and "modo_generacion" not in p
    assert len(set(ids)) == 4
    docs = e.cliente.get("/api/documents", headers=e.headers).json()["items"]
    assert [d["document_id"] for d in docs] == [e.doc]
    for generation_id in ids:
        assert e.cliente.get(f"/api/generations/{generation_id}", headers=e.headers).json()["status"] == "completed"
    history = e.cliente.get("/api/generations", headers=e.headers).json()["items"]
    assert len(history) == 4
    modes = {item["generation_id"]: item["parametros"].get("modo_generacion", "multiagente") for item in history}
    assert [modes[generation_id] for generation_id in ids] == ["basico", "multiagente", "basico", "multiagente"]


def test_idempotencia_incluye_modo_y_conserva_claves_legacy(entorno):
    e = entorno
    first = api_cases.generar(e, "basic-key", modo_generacion="basico")
    api_cases.esperar(e, first.json())
    again = api_cases.generar(e, "basic-key", modo_generacion="basico")
    assert again.json()["generation_id"] == first.json()["generation_id"]
    assert e.doble.llamadas_generacion == 1
    assert api_cases.generar(e, "basic-key", modo_generacion="multiagente").status_code == 409
    legacy = api_cases.generar(e, "legacy-key")
    api_cases.esperar(e, legacy.json())
    modern = api_cases.generar(e, "legacy-key", modo_generacion="multiagente")
    assert modern.json()["generation_id"] == legacy.json()["generation_id"]


def test_basico_exporta_etiqueta_y_rechaza_aprobacion_falsa(entorno):
    e = entorno
    r = api_cases.generar(e, modo_generacion="basico")
    job = api_cases.esperar(e, r.json())
    generation_id = job["generation_id"]
    for formato in ("json", "md", "pdf", "csv", "tsv", "apkg"):
        export = e.cliente.get(f"/api/exports/{generation_id}?format={formato}", headers=e.headers)
        assert export.status_code == 200, export.text[:200] if formato != "pdf" else "PDF fallido"
        if formato in ("md", "csv", "tsv"):
            assert "Sin evaluación independiente" in export.text
        elif formato == "pdf":
            assert "Sin evaluación independiente" in " ".join(
                p.extract_text() for p in PdfReader(io.BytesIO(export.content)).pages
            )
    for path, value in (
        (("status",), "aprobado"),
        (("modo_generacion",), "multiagente"),
        (("evaluacion_calidad", "anclaje_fuente_score"), 1),
        (("evaluacion_calidad", "cantidad_afirmaciones"), 0),
        (("trazabilidad", "modelo_verificacion"), "juez-inventado"),
    ):
        changed = copy.deepcopy(job["contenido"])
        container = changed if len(path) == 1 else changed[path[0]]
        container[path[-1]] = value
        with pytest.raises(ValidationError):
            cargar_paquete_json(json.dumps(changed))
    assert e.doble.llamadas_verificacion == 0


def test_basico_reintenta_solo_persistencia(entorno, monkeypatch):
    e = entorno
    original = e.storage.upload

    def fallar(objeto_id, *args, **kwargs):
        if objeto_id.startswith("outputs/"):
            raise StorageUnavailable("Fallo simulado")
        return original(objeto_id, *args, **kwargs)

    monkeypatch.setattr(e.storage, "upload", fallar)
    r = api_cases.generar(e, modo_generacion="basico")
    failed = api_cases.esperar(e, r.json(), status="failed")
    assert failed["contenido"] is None
    monkeypatch.setattr(e.storage, "upload", original)
    retry = e.cliente.post(
        r.json()["status_url"] + "/persist", headers={**e.headers, "Idempotency-Key": "persist-basic"}
    )
    assert retry.status_code == 202, retry.text
    done = api_cases.esperar(e, r.json())
    assert done["contenido"]["status"] == "no_evaluado"
    assert e.doble.llamadas_generacion == 1 and e.doble.llamadas_verificacion == 0


def test_basico_no_invoca_langgraph_y_rechaza_json_invalido(entorno, monkeypatch):
    from app.core.agents import graph

    e, calls = entorno, []

    def prohibido(*args, **kwargs):
        raise AssertionError("El modo básico no ejecuta LangGraph")

    def invalido(*args, **kwargs):
        calls.append(1)
        return "{}"

    monkeypatch.setattr(graph, "crear_grafo", prohibido)
    monkeypatch.setattr(e.doble, "generar_sync", invalido)
    r = api_cases.generar(e, modo_generacion="basico")
    job = api_cases.esperar(e, r.json(), status="failed")
    assert job["contenido"] is None and calls == [1]
    assert e.doble.llamadas_verificacion == 0


def test_cancelacion_basica_conserva_documento_y_no_publica(entorno, monkeypatch):
    e = entorno
    entered, release = threading.Event(), threading.Event()
    original = e.doble.generar_sync

    def bloqueado(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(e.doble, "generar_sync", bloqueado)
    r = api_cases.generar(e, modo_generacion="basico")
    try:
        assert entered.wait(5)
        cancelled = e.cliente.post(r.json()["cancel_url"], headers=e.headers)
        assert cancelled.status_code == 202
        assert e.db.obtener_generacion(r.json()["generation_id"])["status"] == "running"
    finally:
        release.set()
    job = api_cases.esperar(e, r.json(), status="cancelled")
    assert job["contenido"] is None
    assert e.cliente.get("/api/documents/" + e.doc, headers=e.headers).json()["status"] == "ready"


def test_recuperacion_oci_simulada_conserva_modo_sin_restaurar_tokens(entorno, tmp_path):
    from app.jobs.store import RegistroOperativo
    from app.session.manifests import sincronizar_manifiesto
    from app.session.recovery import restaurar_registro

    e = entorno
    r = api_cases.generar(e, modo_generacion="basico")
    job = api_cases.esperar(e, r.json())
    sincronizar_manifiesto(e.db, e.storage, e.ws["workspace_id"])
    nuevo = RegistroOperativo(tmp_path / "restaurado.sqlite3")
    try:
        assert restaurar_registro(nuevo, e.storage) == 1
        fila = nuevo.obtener_generacion(job["generation_id"])
        assert fila["status"] == "completed"
        assert json.loads(fila["parametros_json"])["modo_generacion"] == "basico"
        assert nuevo.obtener_sesion(e.ws["token"]) is None
    finally:
        nuevo.cerrar()
