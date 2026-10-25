"""La última autorización conserva los mapas documentales revisados por Critic."""

from dataclasses import replace
from unittest.mock import Mock

import pytest
import test_critic as critic_cases
import test_writer as writer_cases
from app.core.agents.critic import critic
from app.core.agents.graph import DependenciasOrquestacion, _finalizar
from app.schemas.responses import PersistenciaInfo
from test_critic_multidocument import preparar, programar_juez

pytestmark = pytest.mark.unit
entorno_writer = writer_cases.entorno
entorno_critic = critic_cases.entorno


@pytest.fixture
def revision_multidocumental(entorno_critic):
    caso = preparar(entorno_critic)
    programar_juez(caso)
    aprobado = writer_cases.aplicar(caso.estado, critic(caso.estado, caso.deps))
    assert aprobado.destino_revision == "finalizer", aprobado.error
    grafo = caso.deps.grafo
    persistir = Mock(return_value=PersistenciaInfo(status_upload="completado", provider="mock"))
    dependencias = DependenciasOrquestacion(
        grafo, lambda s: {}, replace(entorno_critic[0], grafo=grafo), caso.deps, persistir
    )
    return aprobado, dependencias, caso.docs["doc_b"]


def test_finaliza_si_los_mapas_revisados_se_conservan(revision_multidocumental):
    estado, dependencias, _ = revision_multidocumental
    update = _finalizar(estado, dependencias)
    assert update["status"] == "completed"
    assert {fuente.document_id for fuente in update["paquete_aprobado"].documentos_fuente} == set(estado.document_ids)
    dependencias.persistir.assert_called_once()


@pytest.mark.parametrize("cambio", ["secciones", "idioma"])
def test_no_publica_si_cambia_el_mapa_secundario_antes_de_finalizar(revision_multidocumental, cambio):
    estado, dependencias, secundaria = revision_multidocumental
    if cambio == "secciones":
        secundaria.secciones = ("sec_alterada",)
    else:
        secundaria.idioma_origen = "pt"
    update = _finalizar(estado, dependencias)
    assert update["status"] == "failed"
    assert update["error"].code == "INVALID_STATE"
    assert update["borrador"] is None and update["paquete_aprobado"] is None
    assert update["referencias"] == []
    dependencias.persistir.assert_not_called()
