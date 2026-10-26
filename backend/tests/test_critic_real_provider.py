"""Caminos del Critic con proveedor real (es_mock=False).

Los tests con dobles fijan el contrato estricto de EvaluadorInvalidoError, pero no
pueden ejercitar la tolerancia del proveedor real. Estos casos cubren los fixes:
- A.1: citas cruzadas del juez (chunk válido de otra tarjeta) no tumban la ejecución.
- A.2: chat con síntesis no contradicha y generación real >= umbral sin exigir 100%.
Cada caso conserva su contraparte con mock para demostrar que el contrato de tests
no cambió.
"""

import pytest
import test_critic as critic_cases
import test_writer as writer_cases
from app.core.agents.critic import DependenciasCritic, critic
from test_critic import borrador, juez, rubrica

# Mismo mecanismo que test_critic: exponer las fixtures importadas en el
# namespace del módulo para que pytest resuelva la cadena de dependencias.
entorno_writer = writer_cases.entorno
entorno_base = critic_cases.entorno
pytestmark = pytest.mark.unit


@pytest.fixture
def entorno_real(entorno_base):
    deps_writer, deps_critic, documento = entorno_base
    doble = deps_writer.proveedor
    assert doble.es_mock
    doble.es_mock = False  # el proveedor se comporta como Gemini real ante los filtros
    cfg = deps_critic.configuracion.model_copy(update={"mock_gemini": False})
    return deps_writer, DependenciasCritic(deps_critic.grafo, doble, cfg), documento


def con_segunda_evidencia(estado):
    """Agrega un chunk real a la evidencia para simular una cita cruzada válida."""
    duplicado = estado.evidencia[0].model_copy(deep=True)
    duplicado = duplicado.model_copy(update={"chunk": duplicado.chunk.model_copy(update={"chunk_id": "ch_2"})})
    estado.evidencia.append(duplicado)
    return estado


def cita_cruzada(doble):
    """El juez cita ch_2 (evidencia real, fuera de las citas de la tarjeta)."""

    def modificar(fase, salida, datos, entrada):
        if fase == "juzgar":
            salida["juicios"] = [
                {
                    "id_afirmacion": a["id"],
                    "estado": "respaldada",
                    "motivo": "Comprobación con un chunk válido de otra sección",
                    "referencias": ["ch_2"],
                }
                for a in datos["afirmaciones"]
            ]
        return salida

    juez(doble, total=1, respaldadas=1, modificar=modificar)


def test_citas_cruzadas_no_tumban_generacion_con_proveedor_real(entorno_real):
    deps_writer, deps, _ = entorno_real
    estado = con_segunda_evidencia(borrador(entorno_real))
    cita_cruzada(deps_writer.proveedor)
    resultado = critic(estado, deps)
    assert resultado["error"] is None
    assert resultado["destino_revision"] == "finalizer"
    assert resultado["afirmaciones_fallidas"] == []


def test_citas_cruzadas_siguen_siendo_evaluador_invalido_con_mock(entorno_base):
    deps_writer, deps, _ = entorno_base
    estado = con_segunda_evidencia(borrador(entorno_base))
    cita_cruzada(deps_writer.proveedor)
    resultado = critic(estado, deps)
    assert resultado["error"] is not None
    assert resultado["error"].code == "INVALID_EVALUATION"


def sintesis_no_contradicha(doble):
    """Una afirmación respaldada y una síntesis general sin_evidencia no contradicha."""

    def modificar(fase, salida, datos, entrada):
        if fase == "juzgar":
            afirmaciones = datos["afirmaciones"]
            salida["juicios"] = [
                {
                    "id_afirmacion": afirmaciones[0]["id"],
                    "estado": "respaldada",
                    "motivo": "Respaldada con la cita de la tarjeta",
                    "referencias": ["ch_1"],
                },
                {
                    "id_afirmacion": afirmaciones[1]["id"],
                    "estado": "sin_evidencia",
                    "motivo": "Síntesis conversacional general",
                    "referencias": [],
                },
            ]
        if fase == "pedagogia":
            return rubrica()
        return salida

    juez(doble, total=2, respaldadas=2, modificar=modificar)


def test_chat_real_acepta_sintesis_no_contradicha(entorno_real):
    deps_writer, deps, _ = entorno_real
    deps.grafo.ejecucion.tipo = "chat"
    estado = borrador(entorno_real)
    sintesis_no_contradicha(deps_writer.proveedor)
    resultado = critic(estado, deps)
    assert resultado["error"] is None
    assert resultado["destino_revision"] == "finalizer"


def test_chat_mock_conserva_el_contrato_estricto(entorno_base):
    deps_writer, deps, _ = entorno_base
    deps.grafo.ejecucion.tipo = "chat"
    estado = borrador(entorno_base)
    sintesis_no_contradicha(deps_writer.proveedor)
    resultado = critic(estado, deps)
    assert resultado["destino_revision"] != "finalizer"
    assert resultado["evaluacion_pedagogica"].razones_bloqueo


def con_una_sin_evidencia(doble):
    """Nueve afirmaciones respaldadas y una menor sin_evidencia (sin contradicciones)."""

    def modificar(fase, salida, datos, entrada):
        if fase == "juzgar":
            salida["juicios"] = [
                {
                    "id_afirmacion": a["id"],
                    "estado": "respaldada" if i < 9 else "sin_evidencia",
                    "motivo": "Afirmación menor sin respaldo directo",
                    "referencias": ["ch_1"] if i < 9 else [],
                }
                for i, a in enumerate(datos["afirmaciones"])
            ]
        return salida

    juez(doble, total=10, respaldadas=10, modificar=modificar)


def test_generacion_real_aprueba_al_canzar_umbral_sin_exigir_cien_por_ciento(entorno_real):
    deps_writer, deps, _ = entorno_real
    estado = borrador(entorno_real)
    con_una_sin_evidencia(deps_writer.proveedor)
    resultado = critic(estado, deps)
    assert resultado["error"] is None
    assert resultado["destino_revision"] == "finalizer"
    assert resultado["afirmaciones_fallidas"] == []


def test_generacion_mock_conserva_exigencia_del_contrato(entorno_base):
    deps_writer, deps, _ = entorno_base
    estado = borrador(entorno_base)
    con_una_sin_evidencia(deps_writer.proveedor)
    resultado = critic(estado, deps)
    assert resultado["destino_revision"] != "finalizer"
    assert resultado["afirmaciones_fallidas"]
