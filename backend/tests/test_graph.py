"""StateGraph real y nodos Writer/Critic: terminales, límites y finalización."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
import test_critic as critic_cases
import test_writer as writer_cases
from app.core.agents.graph import DependenciasOrquestacion, ejecutar_en_worker, ejecutar_grafo
from app.core.agents.graph_state import EstadoGrafo
from app.jobs.manager import FalloTrabajoError, RechazoCalidadError, ReintentableError
from app.schemas.enums import PedagogicalFormat
from app.schemas.responses import PedagogicalOutput, PersistenciaInfo
from app.storage.provider import StorageUnavailable

pytestmark = pytest.mark.unit
entorno_writer = writer_cases.entorno
entorno_critic = critic_cases.entorno


@pytest.fixture
def pila(entorno_critic, mock_storage):
    escritor, critico, documento = entorno_critic
    eventos, investigaciones = [], []
    escritor.grafo.ejecucion.registrar_paso = lambda paso, intento: eventos.append((paso, intento))
    base = writer_cases.estado_inicial((escritor, documento))

    def investigar(estado):
        investigaciones.append(estado.intento)
        return {
            "evidencia": base.evidencia,
            "secciones_cubiertas": base.secciones_cubiertas,
            "trazabilidad": base.trazabilidad,
        }

    def persistir(paquete, contexto):
        contexto.chequear()
        texto = paquete.model_dump_json()
        mock_storage.upload(paquete.almacenamiento_oci.objeto_id, texto)
        assert mock_storage.get_as_text(paquete.almacenamiento_oci.objeto_id) == texto
        return PersistenciaInfo(status_upload="completado", provider="mock")

    deps = DependenciasOrquestacion(escritor.grafo, investigar, escritor, critico, persistir)
    return SimpleNamespace(
        deps=deps,
        documento=documento,
        storage=mock_storage,
        doble=escritor.proveedor,
        eventos=eventos,
        investigaciones=investigaciones,
        base=base,
    )


def preparar(pila, *, formato="flashcards", redacciones=1, pedagogia=None, modificar=None, total=4, respaldadas=4):
    base = writer_cases.estado_inicial((pila.deps.escritor, pila.documento), formato)
    pila.base = base
    payload = writer_cases.respuesta(base)
    # Todas las citas del fixture proceden de la página 1 de ch_1.
    payload = json.loads(json.dumps(payload).replace('"pagina": null', '"pagina": 1'))
    pila.doble.programar_generacion(*[json.dumps(payload) for _ in range(redacciones)])
    critic_cases.juez(pila.doble, pedagogia=pedagogia, modificar=modificar, total=total, respaldadas=respaldadas)
    return EstadoGrafo(
        workspace_id=base.workspace_id,
        generation_id=base.generation_id,
        document_id=base.document_id,
        parametros=base.parametros,
        deadline=base.deadline,
    ), payload


@pytest.mark.parametrize("formato", PedagogicalFormat)
def test_feliz_paquete_snapshot_sin_contenido_nuevo(pila, formato):
    estado, revisado = preparar(pila, formato=formato)
    resultado = ejecutar_grafo(estado, pila.deps)
    assert resultado.status == "completed", resultado.error
    paquete = resultado.paquete_aprobado
    assert isinstance(paquete, PedagogicalOutput)
    assert paquete.contenido_adaptado.model_dump(mode="json") == revisado["contenido_adaptado"]
    assert paquete.metadatos.model_dump(mode="json") == revisado["metadatos"]
    assert paquete.generation_id == estado.generation_id and paquete.created_at == pila.deps.created_at
    assert paquete.almacenamiento_oci.objeto_id == "outputs/ws_test/gen_test/content.json"
    assert (
        PedagogicalOutput.model_validate_json(pila.storage.get_as_text(paquete.almacenamiento_oci.objeto_id)) == paquete
    )
    assert resultado.intento == 1
    assert resultado.presupuesto.usadas == pila.doble.llamadas_generacion + pila.doble.llamadas_verificacion
    assert pila.eventos == [("supervisor", 1), ("researcher", 1), ("writer", 1), ("critic", 1), ("finalizer", 1)]


def test_tres_revisiones_fallidas_no_publican_borrador_ni_exports(pila):
    estado, _ = preparar(pila, redacciones=3, pedagogia={**critic_cases.rubrica(), "claridad_pedagogica": "baja"})
    resultado = ejecutar_grafo(estado, pila.deps)
    assert resultado.status == "rejected_quality" and resultado.error.code == "QUALITY_REJECTED"
    assert resultado.intento == pila.doble.llamadas_generacion == 3
    assert resultado.borrador is None and resultado.paquete_aprobado is None
    assert not pila.storage.list("outputs/").items and not pila.storage.list("exports/").items
    assert [i for paso, i in pila.eventos if paso == "writer"] == [1, 2, 3]
    assert "finalizer" not in [paso for paso, _ in pila.eventos]


def test_tres_json_invalidos_son_rechazo_de_calidad(pila):
    estado, _ = preparar(pila)
    pila.doble._generaciones.clear()
    pila.doble.programar_generacion("no es JSON", "tampoco", "todavía no")
    resultado = ejecutar_grafo(estado, pila.deps)
    assert resultado.status == "rejected_quality" and resultado.intento == 3
    assert resultado.borrador is None and pila.doble.llamadas_verificacion == 0


def test_promedio_alto_con_afirmacion_falsa_no_aprueba(pila):
    estado, _ = preparar(pila, redacciones=3, total=10, respaldadas=9)
    resultado = ejecutar_grafo(estado, pila.deps)
    assert resultado.status == "rejected_quality" and resultado.intento == 3
    assert resultado.paquete_aprobado is None and not pila.storage.list("outputs/").items


def test_feedback_evidencia_regresa_a_researcher_sin_reiniciar_contadores(pila):
    revisiones = 0

    def ampliar(fase, salida, datos, entrada):
        nonlocal revisiones
        if fase == "pedagogia":
            revisiones += 1
            if revisiones == 1:
                salida["solicitudes_evidencia"] = ["Precisar la definición de subred"]
        return salida

    estado, _ = preparar(pila, redacciones=2, modificar=ampliar)
    resultado = ejecutar_grafo(estado, pila.deps)
    assert resultado.status == "completed", resultado.error
    assert pila.investigaciones == [0, 1] and resultado.intento == 2
    assert resultado.presupuesto.usadas == 8


def test_presupuesto_agotado_por_retry_es_fallo_tecnico(pila):
    estado, _ = preparar(pila)
    estado.presupuesto.usadas = 19
    pila.doble._generaciones.clear()
    pila.doble.programar_generacion(ReintentableError("fallo privado"), "no debe ejecutarse")
    resultado = ejecutar_grafo(estado, pila.deps)
    assert resultado.status == "failed" and resultado.error.code == "GENERATION_BUDGET"
    assert resultado.presupuesto.usadas == 20 and pila.doble.llamadas_generacion == 1
    assert "fallo privado" not in resultado.model_dump_json()


@pytest.mark.parametrize("modo", ["fallo", "sin_confirmar"])
def test_persistencia_no_confirmada_nunca_completed(pila, modo):
    estado, _ = preparar(pila)

    def persistir(paquete, contexto):
        if modo == "fallo":
            raise StorageUnavailable("diagnóstico privado")
        return PersistenciaInfo(status_upload="pendiente", provider="mock")

    resultado = ejecutar_grafo(estado, replace(pila.deps, persistir=persistir))
    assert resultado.status == "failed" and resultado.error.code == "STORAGE_UNAVAILABLE"
    assert resultado.paquete_aprobado is None and resultado.borrador is None
    assert "diagnóstico privado" not in resultado.model_dump_json()


def test_cancelacion_entre_nodos_no_publica(pila):
    estado, _ = preparar(pila)
    ctx = pila.deps.grafo.ejecucion
    registrar = ctx.registrar_paso

    def cancelar(paso, intento):
        registrar(paso, intento)
        if paso == "critic":
            ctx._evento_cancelacion.set()

    ctx.registrar_paso = cancelar
    resultado = ejecutar_grafo(estado, pila.deps)
    assert resultado.status == "cancelled" and pila.doble.llamadas_verificacion == 0
    assert not pila.storage.list("outputs/").items


def test_deadline_vencido_termina_sin_llamadas(pila):
    estado, _ = preparar(pila)
    pila.deps.grafo.ejecucion.deadline = 1
    estado.deadline = 1
    resultado = ejecutar_grafo(estado, pila.deps)
    assert resultado.status == "failed" and resultado.error.code == "DEADLINE"
    assert pila.doble.llamadas_generacion == pila.doble.llamadas_verificacion == 0


def test_fuente_cambiada_despues_de_critic_no_se_persiste(pila):
    estado, _ = preparar(pila)
    registrar = pila.deps.grafo.ejecucion.registrar_paso

    def cambiar(paso, intento):
        registrar(paso, intento)
        if paso == "finalizer":
            pila.documento.fuente.hash = "sha256:otra"

    pila.deps.grafo.ejecucion.registrar_paso = cambiar
    resultado = ejecutar_grafo(estado, pila.deps)
    assert resultado.status == "failed" and resultado.error.code == "INVALID_STATE"
    assert not pila.storage.list("outputs/").items


def test_worker_devuelve_solo_paquete_completado(pila):
    estado, _ = preparar(pila)
    assert ejecutar_en_worker(estado, pila.deps).status == "aprobado"


@pytest.mark.parametrize("calidad", [True, False])
def test_worker_traduce_fallo_y_rechazo_al_gestor(pila, calidad):
    estado, _ = preparar(pila, redacciones=3, pedagogia={**critic_cases.rubrica(), "claridad_pedagogica": "baja"})
    if not calidad:
        estado.presupuesto.usadas = 20
    with pytest.raises(RechazoCalidadError if calidad else FalloTrabajoError):
        ejecutar_en_worker(estado, pila.deps)
