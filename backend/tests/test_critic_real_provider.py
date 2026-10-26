"""Misma política de revisión con dobles y proveedor real, sin llamadas externas."""

from dataclasses import replace

import pytest
import test_critic as critic_cases
import test_writer as writer_cases
from app.core.agents.critic import critic
from test_critic import borrador, juez

entorno_writer = writer_cases.entorno
entorno_base = critic_cases.entorno
pytestmark = pytest.mark.unit


@pytest.fixture(params=[True, False], ids=["mock", "real"])
def entorno_revision(entorno_base, request):
    escritor, deps, documento = entorno_base
    escritor.proveedor.es_mock = request.param
    deps = replace(deps, configuracion=deps.configuracion.model_copy(update={"mock_gemini": request.param}))
    return escritor, deps, documento


def programar(entorno, *, total=2, modificar=None):
    def ajustar(fase, salida, datos, entrada):
        if fase == "descomponer":
            for afirmacion in salida["afirmaciones"]:
                afirmacion["ubicacion"] = "/contenido_adaptado/items/0/dorso"
        return modificar(fase, salida, datos, entrada) if modificar else salida

    juez(entorno[0].proveedor, total=total, respaldadas=total, modificar=ajustar)


@pytest.mark.parametrize("tipo", ["generation", "chat", "glosario"])
def test_no_inventa_respaldo_ni_descarta_fallidas_con_score_alto(entorno_revision, tipo):
    entorno = entorno_revision
    entorno[1].grafo.ejecucion.tipo = tipo
    estado = borrador(entorno)

    def modificar(fase, salida, *_):
        if fase == "juzgar":
            salida["juicios"][-1].update(estado="sin_evidencia", referencias=[])
        return salida

    programar(entorno, total=10, modificar=modificar)
    resultado = critic(estado, entorno[1])
    assert resultado["error"] is None
    assert resultado["evaluacion_factual"].score == 0.9
    assert len(resultado["afirmaciones_fallidas"]) == 1
    assert resultado["destino_revision"] == "researcher"


@pytest.mark.parametrize("tipo", ["chat", "glosario"])
def test_consulta_general_no_exige_todas_las_secciones(entorno_revision, tipo):
    entorno = entorno_revision
    entorno[1].grafo.ejecucion.tipo = tipo
    entorno[2].secciones = tuple(f"sec_{i}" for i in range(1, 16))
    estado = borrador(entorno)
    programar(entorno)
    resultado = critic(estado, entorno[1])
    assert resultado["error"] is None
    assert resultado["destino_revision"] == "finalizer"
    assert resultado["evaluacion_factual"].score == 1
    segmentos = entorno[0].proveedor.entradas_verificacion[0]["prompt"]
    assert "/metadatos/" not in segmentos and "/frente" not in segmentos


@pytest.mark.parametrize("tipo", ["chat", "glosario"])
@pytest.mark.parametrize(
    "campo", ["contradicciones", "afirmaciones_omitidas", "solicitudes_evidencia", "cobertura_objetivos"]
)
def test_consultas_conservan_hallazgos_sustantivos(entorno_revision, tipo, campo):
    entorno = entorno_revision
    entorno[1].grafo.ejecucion.tipo = tipo
    estado = borrador(entorno)

    def modificar(fase, salida, *_):
        if fase == "pedagogia":
            salida[campo] = "parcial" if campo == "cobertura_objetivos" else ["Falta evidencia pertinente"]
        return salida

    programar(entorno, modificar=modificar)
    resultado = critic(estado, entorno[1])
    assert resultado["error"] is None
    assert resultado["destino_revision"] != "finalizer"
    assert resultado["evaluacion_pedagogica"].razones_bloqueo


@pytest.mark.parametrize("citas", [["ch_2"], ["ch_2", "ch_1"]])
def test_cita_cruzada_filtra_sin_inventar_respaldo(entorno_revision, citas):
    entorno = entorno_revision
    estado = borrador(entorno)
    extra = estado.evidencia[0].model_copy(deep=True)
    extra.chunk = extra.chunk.model_copy(update={"chunk_id": "ch_2"})
    estado.evidencia.append(extra)

    def modificar(fase, salida, *_):
        if fase == "juzgar":
            for juicio in salida["juicios"]:
                juicio["referencias"] = citas
        return salida

    programar(entorno, modificar=modificar)
    resultado = critic(estado, entorno[1])
    assert resultado["error"] is None
    assert resultado["evaluacion_factual"].score == (1 if "ch_1" in citas else 0)
    assert resultado["destino_revision"] == ("finalizer" if "ch_1" in citas else "researcher")
    assert all(set(j.referencias) <= {"ch_1"} for j in resultado["evaluacion_factual"].juicios)


@pytest.mark.parametrize("referencias", [[], ["ajena"]])
def test_quiz_sin_citas_validas_pide_correccion(entorno_revision, referencias):
    entorno = entorno_revision
    estado = borrador(entorno, "quiz")

    def modificar(fase, salida, *_):
        if fase == "quiz":
            for opcion in salida["opciones"]:
                opcion["referencias"] = referencias
        return salida

    juez(entorno[0].proveedor, modificar=modificar)
    resultado = critic(estado, entorno[1])
    assert resultado["error"] is None
    assert "quiz_invalido" in resultado["evaluacion_pedagogica"].razones_bloqueo
    assert resultado["destino_revision"] == "writer"


@pytest.mark.parametrize("tipo", ["chat", "glosario", "generation"])
def test_ubicacion_inventada_sigue_siendo_error_tecnico(entorno_revision, tipo):
    entorno = entorno_revision
    entorno[1].grafo.ejecucion.tipo = tipo
    estado = borrador(entorno)

    def modificar(fase, salida, *_):
        if fase == "descomponer":
            salida["afirmaciones"][0]["ubicacion"] = "/inventada"
        return salida

    programar(entorno, modificar=modificar)
    resultado = critic(estado, entorno[1])
    assert resultado["error"].code == "INVALID_EVALUATION"
    assert resultado["evaluacion_factual"] is None
