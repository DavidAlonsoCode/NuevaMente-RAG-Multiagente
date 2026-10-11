"""StateGraph definitivo: un contexto, tres redacciones y terminales explícitos."""

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.core.agents.critic import DependenciasCritic, critic
from app.core.agents.finalizer import finalizer
from app.core.agents.graph_state import DependenciasGrafo, DiagnosticoGrafo, EstadoGrafo
from app.core.agents.supervisor import supervisor
from app.core.agents.writer import DependenciasWriter, writer
from app.jobs.manager import (
    ContextoEjecucion,
    DeadlineExcedidoError,
    FalloTrabajoError,
    RechazoCalidadError,
    TrabajoCanceladoError,
)
from app.schemas.enums import JobStatus
from app.schemas.responses import PedagogicalOutput, PersistenciaInfo
from app.storage.object_keys import BUCKET_PRODUCCION
from app.storage.provider import StorageUnavailable


@dataclass(frozen=True)
class DependenciasOrquestacion:
    grafo: DependenciasGrafo = field(repr=False)
    investigar: Callable[[EstadoGrafo], dict] = field(repr=False)
    escritor: DependenciasWriter = field(repr=False)
    critico: DependenciasCritic = field(repr=False)
    persistir: Callable[[PedagogicalOutput, ContextoEjecucion], PersistenciaInfo] = field(repr=False)
    bucket: str = BUCKET_PRODUCCION
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        if self.escritor.grafo is not self.grafo or self.critico.grafo is not self.grafo:
            raise ValueError("Todos los nodos deben compartir las mismas dependencias y contexto")
        if any(not callable(f) or inspect.iscoroutinefunction(f) for f in (self.investigar, self.persistir)):
            raise TypeError("La investigación y persistencia requieren adaptadores síncronos")


def _terminal(code, message, status=JobStatus.FAILED):
    return {
        "status": status,
        "error": DiagnosticoGrafo(code=code, message=message),
        "borrador": None,
        "paquete_aprobado": None,
        "referencias": [],
        "destino_revision": None,
    }


def _envolver(nombre, funcion):
    def nodo(estado: EstadoGrafo, runtime: Runtime[DependenciasOrquestacion]):
        deps, ctx = runtime.context, runtime.context.grafo.ejecucion
        try:
            ctx.chequear()
            if estado.status != JobStatus.RUNNING:
                return {}
            if nombre != "writer":
                intento = estado.intento + 1 if nombre in ("supervisor", "researcher") else estado.intento
                ctx.registrar_paso(nombre, max(1, min(3, intento)))
            update = funcion(estado, deps)
            if inspect.isawaitable(update):
                if inspect.iscoroutine(update):
                    update.close()
                raise TypeError("El nodo debe devolver un update resuelto")
            nuevo = EstadoGrafo.model_validate({**estado.model_dump(), **update})
            if nuevo.intento < estado.intento or nuevo.presupuesto.usadas < estado.presupuesto.usadas:
                raise ValueError("Los contadores no pueden retroceder entre nodos")
            if nuevo.deadline != ctx.deadline or nuevo.workspace_id != ctx.workspace_id:
                raise ValueError("El nodo cambió el contexto autorizado")
            ctx.chequear()
            return update
        except TrabajoCanceladoError:
            return _terminal("CANCELLED", "El trabajo fue cancelado.", JobStatus.CANCELLED)
        except DeadlineExcedidoError:
            return _terminal("DEADLINE", "El trabajo excedió su deadline de ejecución.")
        except StorageUnavailable:
            return _terminal("STORAGE_UNAVAILABLE", "No se pudo confirmar la persistencia del aprobado.")
        except Exception:
            return _terminal("INVALID_STATE", "Un nodo no pudo completar un estado válido del grafo.")

    return nodo


def _finalizar(estado, deps):
    # Revalidar acceso, versión y reglas inmediatamente antes de persistir.
    autorizacion = supervisor(estado, deps.grafo)
    if autorizacion.get("error") is not None:
        return _terminal(autorizacion["error"].code, autorizacion["error"].message, autorizacion["status"])
    if any(
        autorizacion.get(c) != getattr(estado, c)
        for c in (
            "source_hash",
            "documento_fuente",
            "idioma_origen",
            "secciones_disponibles",
            "restricciones",
            "rubrica",
        )
    ):
        return _terminal("INVALID_STATE", "La fuente cambió después de la revisión.")
    paquete = finalizer(estado, bucket=deps.bucket, created_at=deps.created_at)
    confirmacion = deps.persistir(paquete, deps.grafo.ejecucion)
    if not isinstance(confirmacion, PersistenciaInfo) or confirmacion.status_upload != "completado":
        raise StorageUnavailable("La escritura no fue confirmada")
    return {
        "status": JobStatus.COMPLETED,
        "borrador": None,
        "paquete_aprobado": paquete,
        "persistencia": confirmacion,
        "almacenamiento": paquete.almacenamiento_oci,
    }


def _siguiente(estado, destino):
    return destino if estado.status == JobStatus.RUNNING else END


def _revision(estado):
    if estado.status != JobStatus.RUNNING:
        return END
    if estado.destino_revision in ("writer", "researcher", "finalizer"):
        return estado.destino_revision
    return "sin_salida"


def crear_grafo():
    builder = StateGraph(EstadoGrafo, context_schema=DependenciasOrquestacion)
    builder.add_node("supervisor", _envolver("supervisor", lambda s, d: supervisor(s, d.grafo)))
    builder.add_node("researcher", _envolver("researcher", lambda s, d: d.investigar(s)))
    builder.add_node("writer", _envolver("writer", lambda s, d: writer(s, d.escritor)))
    builder.add_node("critic", _envolver("critic", lambda s, d: critic(s, d.critico)))
    builder.add_node("finalizer", _envolver("finalizer", _finalizar))
    builder.add_node("sin_salida", lambda s: _terminal("INVALID_STATE", "El grafo no tiene una salida válida."))
    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", lambda s: _siguiente(s, "researcher"))
    builder.add_conditional_edges("researcher", lambda s: _siguiente(s, "writer"))
    builder.add_conditional_edges("writer", lambda s: _siguiente(s, "critic"))
    builder.add_conditional_edges("critic", _revision)
    builder.add_edge("finalizer", END)
    builder.add_edge("sin_salida", END)
    return builder.compile()


def ejecutar_grafo(estado: EstadoGrafo, dependencias: DependenciasOrquestacion) -> EstadoGrafo:
    try:
        datos = crear_grafo().invoke(estado.model_dump(), {"recursion_limit": 20}, context=dependencias)
        final = EstadoGrafo.model_validate(datos)
        if final.status in (JobStatus.QUEUED, JobStatus.RUNNING):
            return EstadoGrafo.model_validate(
                {**final.model_dump(), **_terminal("INVALID_STATE", "Grafo sin terminal.")}
            )
        return final
    except Exception:
        return EstadoGrafo.model_validate(
            {**estado.model_dump(), **_terminal("INVALID_STATE", "La orquestación no pudo terminar correctamente.")}
        )


def ejecutar_en_worker(estado: EstadoGrafo, dependencias: DependenciasOrquestacion) -> PedagogicalOutput:
    """Invocar desde la función encolada del #20; nunca reinicia el pipeline."""
    final = ejecutar_grafo(estado, dependencias)
    if final.status == JobStatus.COMPLETED:
        return final.paquete_aprobado
    if final.status == JobStatus.CANCELLED:
        raise TrabajoCanceladoError()
    if final.status == JobStatus.REJECTED_QUALITY:
        raise RechazoCalidadError()
    raise FalloTrabajoError(final.error.code, final.error.message)
