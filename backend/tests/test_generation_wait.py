"""La espera HTTP respeta el plazo sin competir con el worker mediante sondeo intensivo."""

import json
from types import SimpleNamespace

import pytest
import test_api_generate as generaciones

pytestmark = pytest.mark.unit


class RelojVirtual:
    def __init__(self):
        self.actual = 0.0
        self.pausas = []

    def monotonic(self):
        return self.actual

    def sleep(self, segundos):
        assert segundos > 0
        self.pausas.append(segundos)
        self.actual += segundos


class ClienteHTTP:
    def __init__(self, reloj, respuestas):
        self.reloj = reloj
        self.respuestas = respuestas if callable(respuestas) else iter(respuestas)
        self.consultas = []

    def get(self, url, *, headers):
        self.consultas.append((self.reloj.actual, url, dict(headers)))
        respuesta = self.respuestas(self.reloj.actual) if callable(self.respuestas) else next(self.respuestas)
        self.reloj.actual += respuesta.get("duracion", 0)
        cuerpo = respuesta["cuerpo"]
        return SimpleNamespace(status_code=respuesta.get("codigo", 200), text=json.dumps(cuerpo), json=lambda: cuerpo)


@pytest.fixture
def reloj(monkeypatch):
    reloj = RelojVirtual()
    monkeypatch.setattr(generaciones, "time", SimpleNamespace(monotonic=reloj.monotonic, sleep=reloj.sleep))
    return reloj


def entorno_http(reloj, respuestas):
    return SimpleNamespace(
        cliente=ClienteHTTP(reloj, respuestas), headers={"Authorization": "Bearer token-del-workspace"}
    )


RECIBO = {"status_url": "/api/generations/gen_original"}


def test_consulta_inicial_inmediata_con_url_y_autorizacion(reloj):
    cuerpo = {"status": "completed", "generation_id": "gen_original", "contenido": {"aprobado": True}}
    entorno = entorno_http(reloj, [{"cuerpo": cuerpo}])

    assert generaciones.esperar(entorno, RECIBO) == cuerpo
    assert entorno.cliente.consultas == [(0, RECIBO["status_url"], entorno.headers)]
    assert reloj.pausas == []


def test_conserva_consultas_http_y_progreso_hasta_el_terminal(reloj):
    respuestas = [{"cuerpo": {"status": estado}} for estado in ("queued", "running", "completed")]
    entorno = entorno_http(reloj, respuestas)

    assert generaciones.esperar(entorno, RECIBO)["status"] == "completed"
    assert len(entorno.cliente.consultas) == 3
    assert all(
        url == RECIBO["status_url"] and headers == entorno.headers for _, url, headers in entorno.cliente.consultas
    )
    instantes = [instante for instante, _, _ in entorno.cliente.consultas]
    assert instantes[0] == 0
    assert all(posterior - anterior >= 0.25 for anterior, posterior in zip(instantes, instantes[1:]))
    assert len(reloj.pausas) == 2


@pytest.mark.parametrize("estado", ["failed", "rejected_quality", "cancelled"])
def test_terminal_solicitado_retorna_su_payload_sin_otra_espera(reloj, estado):
    cuerpo = {"status": estado, "error": {"code": "DIAGNOSTICO_TERMINAL", "message": "Conservar"}}
    entorno = entorno_http(reloj, [{"cuerpo": cuerpo}])

    assert generaciones.esperar(entorno, RECIBO, estado) == cuerpo
    assert len(entorno.cliente.consultas) == 1
    assert reloj.pausas == []


@pytest.mark.parametrize("estado", ["failed", "rejected_quality", "cancelled", "desconocido"])
def test_terminal_inesperado_falla_de_inmediato_y_conserva_error(reloj, estado):
    cuerpo = {"status": estado, "error": {"code": "FALLO_ESPECIFICO", "message": "Motivo original"}}
    entorno = entorno_http(reloj, [{"cuerpo": cuerpo}])

    with pytest.raises(pytest.fail.Exception) as error:
        generaciones.esperar(entorno, RECIBO)

    assert str(error.value) == str(cuerpo)
    assert len(entorno.cliente.consultas) == 1
    assert reloj.pausas == []


def test_error_http_falla_antes_de_interpretar_el_estado(reloj):
    cuerpo = {"status": "completed", "error": {"code": "SESSION_INVALID"}}
    entorno = entorno_http(reloj, [{"codigo": 401, "cuerpo": cuerpo}])

    with pytest.raises(AssertionError, match="SESSION_INVALID"):
        generaciones.esperar(entorno, RECIBO)

    assert len(entorno.cliente.consultas) == 1
    assert reloj.pausas == []


def test_plazo_absoluto_incluye_http_y_limita_la_ultima_pausa(reloj):
    respuestas = [
        {"duracion": 0.12, "cuerpo": {"status": "running", "step": paso}}
        for paso in ("researcher", "critic", "persistencia")
    ]
    entorno = entorno_http(reloj, respuestas)

    with pytest.raises(pytest.fail.Exception) as error:
        generaciones.esperar(entorno, RECIBO, timeout=1)

    assert reloj.actual == pytest.approx(1)
    assert reloj.pausas == pytest.approx([0.25, 0.25, 0.14])
    assert len(entorno.cliente.consultas) == 3
    assert str(error.value) == str(respuestas[-1]["cuerpo"])


@pytest.mark.parametrize("duracion", [1, 1.1])
def test_respuesta_completed_fuera_del_plazo_no_se_acepta(reloj, duracion):
    cuerpo = {"status": "completed", "generation_id": "gen_tardia"}
    entorno = entorno_http(reloj, [{"duracion": duracion, "cuerpo": cuerpo}])

    with pytest.raises(pytest.fail.Exception) as error:
        generaciones.esperar(entorno, RECIBO, timeout=1)

    assert str(error.value) == str(cuerpo)
    assert len(entorno.cliente.consultas) == 1
    assert reloj.pausas == []


def test_trabajo_de_un_segundo_no_provoca_cien_consultas(reloj):
    def respuesta(instante):
        return {"cuerpo": {"status": "completed" if instante >= 1 else "running"}}

    entorno = entorno_http(reloj, respuesta)

    assert generaciones.esperar(entorno, RECIBO, timeout=2)["status"] == "completed"
    assert reloj.actual == pytest.approx(1)
    assert len(entorno.cliente.consultas) <= 5


def test_plazo_menor_que_el_intervalo_no_agrega_consultas(reloj):
    cuerpo = {"status": "running", "step": "exportaciones", "error": None}
    entorno = entorno_http(reloj, [{"cuerpo": cuerpo}])

    with pytest.raises(pytest.fail.Exception) as error:
        generaciones.esperar(entorno, RECIBO, timeout=0.1)

    assert reloj.actual == pytest.approx(0.1)
    assert reloj.pausas == pytest.approx([0.1])
    assert len(entorno.cliente.consultas) == 1
    assert str(error.value) == str(cuerpo)
