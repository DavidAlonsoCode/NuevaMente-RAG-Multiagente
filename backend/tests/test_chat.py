"""Tutor #37: HTTP, Chroma, StateGraph, política completa y contaminación del historial."""

import json
import threading
import time
from dataclasses import replace

import pytest
import test_api_generate as generation_cases
from app.chat.service import acotar_historial
from app.jobs.manager import CuotasModelo, CuotasProveedor
from app.schemas.chat import BorradorChat, ChatRequest
from doubles.gemini import DobleGemini
from pydantic import ValidationError
from test_api_generate import esperar

pytestmark = pytest.mark.integration_mock
entorno_generacion = generation_cases.entorno


@pytest.fixture
def entorno(entorno_generacion, monkeypatch):
    e = entorno_generacion
    e.entradas_chat, e.revisiones_chat = [], []
    e.fuera, e.cita_invalida, e.contradiccion, e.json_invalido = False, False, False, 0
    juez_original = e.doble._revisar

    def generar(prompt, **kwargs):
        if e.doble.bloquear is not None:
            e.doble.bloquear.wait(10)
        datos = json.loads(prompt)
        e.entradas_chat.append((datos, kwargs))
        chunk = datos["evidencia"][0]["chunk"]
        respuesta = {
            "es": "Una VCN conecta subredes privadas y conserva aislamiento.",
            "en": "A VCN connects private subnets and preserves isolation.",
            "pt": "Uma VCN conecta sub-redes privadas e mantém o isolamento.",
        }[datos["parametros"]["idioma_salida"]]
        salida = {
            "fuera_de_alcance": e.fuera,
            "respuesta": "" if e.fuera else respuesta,
            "referencias": []
            if e.fuera
            else [
                {
                    "chunk_id": "ajeno" if e.cita_invalida else chunk["chunk_id"],
                    "pagina": chunk["pagina"],
                    "seccion": chunk["seccion"],
                }
            ],
        }
        if e.json_invalido:
            e.json_invalido -= 1
            e.doble.programar_generacion("no es json")
        else:
            e.doble.programar_generacion(json.dumps(salida))
        return DobleGemini.generar_sync(e.doble, prompt, **kwargs)

    def juzgar(**entrada):
        datos = json.loads(entrada["prompt"])
        e.revisiones_chat.append(datos)
        if e.contradiccion and datos["tarea"] == "descomponer":
            salida = {
                "afirmaciones": [
                    {"texto": f"Afirmación {n}", "ubicacion": "/contenido_adaptado/items/0/dorso"} for n in range(10)
                ]
            }
        elif e.contradiccion and datos["tarea"] == "juzgar":
            salida = {
                "juicios": [
                    {
                        "id_afirmacion": a["id"],
                        "estado": "contradicha" if n == 9 else "respaldada",
                        "motivo": "Juicio sintético",
                        "referencias": a["citas_permitidas"],
                    }
                    for n, a in enumerate(datos["afirmaciones"])
                ]
            }
        else:
            return juez_original(**entrada)
        e.doble.programar_revision(json.dumps(salida))
        return DobleGemini._revisar(e.doble, **entrada)

    monkeypatch.setattr(e.doble, "generar_sync", generar)
    monkeypatch.setattr(e.doble, "_revisar", juzgar)
    e.solicitud_chat = dict(
        document_id=e.doc, pregunta="¿Qué conecta una VCN?", perfil_destinatario="principiante", idioma_salida="es"
    )
    return e


def chat(e, **datos):
    return e.cliente.post("/api/chat", json={**e.solicitud_chat, **datos}, headers=e.headers)


@pytest.mark.parametrize("idioma", ["es", "en", "pt"])
def test_respuesta_citada_y_consultable(entorno, idioma):
    e = entorno
    res = chat(e, idioma_salida=idioma)
    assert res.status_code == 202, res.text
    recibo = res.json()
    assert recibo["job_id"] and recibo["cancel_url"]
    resultado = esperar(e, recibo)["result"]
    assert resultado["estado"] == "respondida" and resultado["idioma_salida"] == idioma
    assert resultado["evaluacion_calidad"]["anclaje_fuente_score"] == 1
    ref = resultado["referencias"][0]
    fuente = e.cliente.get(f"/api/documents/{e.doc}/sources/{ref['chunk_id']}", headers=e.headers)
    assert fuente.status_code == 200 and "VCN" in fuente.text
    assert resultado["respuesta"] in json.dumps(e.revisiones_chat, ensure_ascii=False)
    sse = e.cliente.get(recibo["events_url"], headers=e.headers)
    assert "writer" in sse.text and "critic" in sse.text
    assert not e.storage.list("outputs/").items


def test_fuera_de_alcance_abstencion_sin_afirmaciones(entorno):
    e = entorno
    e.fuera = True
    recibo = chat(e, pregunta="¿Cuál es la temperatura de Marte?").json()
    resultado = esperar(e, recibo)["result"]
    assert resultado["estado"] == "fuera_de_alcance"
    assert "La fuente no cubre" in resultado["respuesta"] and "¿" in resultado["respuesta"]
    assert resultado["referencias"] == [] and resultado["evaluacion_calidad"] is None
    assert e.doble.llamadas_generacion == 1 and not e.revisiones_chat


def test_historial_nunca_es_evidencia_y_cada_pregunta_recupera(entorno):
    e = entorno
    primero = chat(e).json()
    previo = esperar(e, primero)["result"]
    previo["respuesta"] = "HISTORIA_FALSA: una VCN garantiza ingresos de un millón."
    with e.db.transaccion():
        e.db._conn.execute("UPDATE jobs SET resultado=? WHERE job_id=?", (json.dumps(previo), primero["job_id"]))
    consultas_antes = len(e.doble.entradas_embeddings)
    segundo = chat(e, pregunta="¿Se conserva aislamiento?").json()
    resultado = esperar(e, segundo)["result"]
    datos, kwargs = e.entradas_chat[-1]
    assert "HISTORIA_FALSA" in json.dumps(datos["historial_no_es_fuente"])
    assert "HISTORIA_FALSA" not in json.dumps(datos["evidencia"])
    assert len(e.doble.entradas_embeddings) > consultas_antes
    nuevas = e.doble.entradas_embeddings[consultas_antes:]
    assert "aislamiento" in json.dumps(nuevas) and "HISTORIA_FALSA" not in json.dumps(nuevas)
    assert "HISTORIA_FALSA" not in resultado["respuesta"]
    assert "historial_no_es_fuente" not in json.dumps(e.revisiones_chat)
    assert "NO" in kwargs["system_instruction"]


@pytest.mark.parametrize("defecto", ["rubrica", "cita", "contradiccion"])
def test_politica_completa_rechaza_tras_tres_redacciones(entorno, defecto):
    e = entorno
    e.doble.rechazar = defecto == "rubrica"
    e.cita_invalida = defecto == "cita"
    e.contradiccion = defecto == "contradiccion"
    recibo = chat(e).json()
    resultado = esperar(e, recibo, "rejected_quality")
    assert resultado["result"] is None and resultado["error"]
    assert e.doble.llamadas_generacion == 3
    assert e.db.obtener_documento(e.doc)["estado"] == "ready"


def test_json_invalido_consumo_de_redaccion_y_correccion(entorno):
    e = entorno
    e.json_invalido = 1
    esperar(e, chat(e).json())
    assert e.doble.llamadas_generacion == 2


def test_cancelacion_en_espera_y_ajeno(entorno):
    e = entorno
    inicio, liberar = threading.Event(), threading.Event()

    def ocupar(ctx):
        inicio.set()
        liberar.wait(10)

    otro = e.cliente.post("/api/workspaces").json()
    bloqueador = e.gestor.enqueue(otro["workspace_id"], "glosario", ocupar)
    assert inicio.wait(5)
    try:
        recibo = chat(e).json()
        assert recibo["status"] == "queued"
        ajeno = {"Authorization": "Bearer " + otro["token"]}
        assert e.cliente.get(recibo["status_url"], headers=ajeno).status_code == 404
        assert e.cliente.get(recibo["events_url"], headers=ajeno).status_code == 404
        assert e.cliente.post(recibo["cancel_url"], headers=ajeno).status_code == 404
        cancel = e.cliente.post(recibo["cancel_url"], headers=e.headers)
        assert cancel.status_code == 200 and cancel.json()["status"] == "cancelled"
        assert e.doble.llamadas_generacion == 0
    finally:
        liberar.set()
    while e.gestor.estado(bloqueador)["status"] == "running":
        time.sleep(0.01)


def test_historial_acotado_y_validaciones():
    turnos = [{"pregunta": "pregunta", "respuesta": "respuesta " * n} for n in (1, 2, 3)]
    assert acotar_historial(turnos, 0) == []
    assert len(acotar_historial(turnos, 1000)) == 3
    limitado = acotar_historial(turnos, 30)
    assert len(limitado) < 3
    from app.core.rag.tokenizer import TokenizadorBPE

    assert TokenizadorBPE().contar(json.dumps(limitado, ensure_ascii=False)) <= 30
    with pytest.raises(ValidationError):
        ChatRequest(document_id="doc", pregunta="  ", perfil_destinatario="principiante", idioma_salida="es")
    with pytest.raises(ValidationError):
        BorradorChat(fuera_de_alcance=True, respuesta="Hecho externo", referencias=[])


def test_borrado_invalida_respuesta_e_historial(entorno):
    e = entorno
    recibo = chat(e).json()
    esperar(e, recibo)
    res = e.cliente.delete("/api/documents/" + e.doc, headers=e.headers)
    assert res.status_code in {202, 204}
    assert e.cliente.get(recibo["status_url"], headers=e.headers).status_code == 404
    assert chat(e).status_code == 404


def test_cuota_compartida_generacion_y_juez_no_abre_otro_presupuesto(entorno):
    e = entorno
    cfg = e.app.state.config
    e.gestor.cuotas = CuotasProveedor(
        {
            cfg.gemini_embedding_model: CuotasModelo(1000, 10_000_000, 1000),
            cfg.gemini_generation_model: CuotasModelo(1000, 10_000_000, 1),
        }
    )
    assert cfg.gemini_generation_model == cfg.gemini_verification_model
    recibo = chat(e).json()
    fallo = esperar(e, recibo, "failed")
    assert fallo["result"] is None and fallo["error"]["code"] == "RATE_LIMITED"
    assert e.doble.llamadas_generacion + e.doble.llamadas_verificacion == 1


def test_deadline_del_worker_y_fuente_conservada(entorno):
    e = entorno
    e.gestor.controles = replace(e.gestor.controles, deadline_ejecucion_segundos=0.001)
    fallo = esperar(e, chat(e).json(), "failed")
    assert fallo["error"]["code"] == "DEADLINE" and fallo["result"] is None
    assert e.db.obtener_documento(e.doc)["estado"] == "ready"
