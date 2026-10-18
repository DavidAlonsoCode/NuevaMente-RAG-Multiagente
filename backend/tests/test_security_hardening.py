"""Regresiones de privacidad del registro HTTP (#51), sin proveedores."""

import json
import logging
import socket
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from app.config import Configuracion
from app.main import crear_app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.unit


def test_contenedor_no_activa_otro_log_de_urls_crudas():
    dockerfile = Path(__file__).resolve().parents[2] / "backend/Dockerfile"
    comando = next(
        linea.removeprefix("CMD ")
        for linea in dockerfile.read_text(encoding="utf-8").splitlines()
        if linea.startswith("CMD ")
    )
    assert "--no-access-log" in json.loads(comando)


def test_error_500_no_filtra_mensaje_privado_en_el_log_del_servidor(tmp_path, caplog):
    app = crear_app(
        Configuracion(_env_file=None, app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path))
    )
    canario = "NM51_PRIVATE_SERVER_EXCEPTION"

    @app.get("/api/security_error_fixture")
    def prueba():
        raise RuntimeError(canario)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    puerto = sock.getsockname()[1]
    servidor = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_config=None, access_log=False))
    hilo = threading.Thread(target=servidor.run, kwargs={"sockets": [sock]}, daemon=True)
    try:
        with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
            hilo.start()
            limite = time.monotonic() + 5
            while not servidor.started and hilo.is_alive() and time.monotonic() < limite:
                time.sleep(0.01)
            assert servidor.started
            with httpx.Client(trust_env=False) as cliente:
                respuesta = cliente.get(f"http://127.0.0.1:{puerto}/api/security_error_fixture", timeout=3)
            assert respuesta.status_code == 500
            assert canario not in respuesta.text
            servidor.should_exit = True
            hilo.join(timeout=5)
            assert not hilo.is_alive()
        assert "RuntimeError" in caplog.text
        assert canario not in caplog.text
    finally:
        servidor.should_exit = True
        if hilo.is_alive():
            hilo.join(timeout=5)
        sock.close()


@pytest.mark.parametrize("con_ruta,metodo", [(True, "POST"), (False, "POST"), (False, "NM51_PRIVATE_REQUEST_CANARY")])
def test_log_http_no_registra_valores_de_ruta_query_headers_ni_body(tmp_path, caplog, con_ruta, metodo):
    app = crear_app(
        Configuracion(_env_file=None, app_env="test", mock_oci=True, mock_gemini=True, data_dir=str(tmp_path))
    )

    @app.post("/api/security_fixture/{recurso}")
    def prueba(recurso: str):
        return {"ok": True}

    canario = "NM51_PRIVATE_REQUEST_CANARY"
    ruta = "/api/security_fixture/" if con_ruta else "/api/no_existe/"
    with caplog.at_level(logging.INFO, logger="nuevamente.api"):
        respuesta = TestClient(app).request(
            metodo,
            ruta + canario + "?dato=" + canario,
            headers={"Authorization": "Bearer " + canario, "X-Request-ID": "nm51-solicitud"},
            json={"documento_contenido": canario},
        )
    assert respuesta.status_code == (200 if con_ruta else 404)
    mensajes = "\n".join(r.getMessage() for r in caplog.records if r.name == "nuevamente.api")
    assert "request_id=nm51-solicitud" in mensajes
    assert canario not in mensajes
    assert ("/api/security_fixture/{recurso}" if con_ruta else "ruta_no_encontrada") in mensajes
