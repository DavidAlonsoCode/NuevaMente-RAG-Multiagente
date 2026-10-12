"""SSE por TCP local: desconectar no cancela, heartbeat y cursor reales (#52)."""

import json
import socket
import threading
import time

import pytest
import requests
import uvicorn
from app.api.routes import jobs
from test_api_generate import esperar

pytestmark = pytest.mark.integration_mock


def test_stream_tcp_heartbeat_cierre_status_y_reconexion(entorno, monkeypatch):
    e = entorno
    liberar = threading.Event()
    ejecuciones = []

    def trabajo(ctx):
        ejecuciones.append(1)
        liberar.wait(10)
        return {"prueba": "sin llamadas externas"}

    job = e.gestor.enqueue(e.ws["workspace_id"], "ingestion", trabajo)
    monkeypatch.setattr(jobs, "INTERVALO_HEARTBEAT", 0.02)
    monkeypatch.setattr(jobs, "INTERVALO_SONDEO", 0.01)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    puerto = sock.getsockname()[1]
    # El TestClient ya abrió el lifespan real. TCP comparte esa misma app/worker.
    servidor = uvicorn.Server(
        uvicorn.Config(e.app, host="127.0.0.1", port=puerto, lifespan="off", access_log=False, log_level="error")
    )
    hilo = threading.Thread(target=servidor.run, kwargs={"sockets": [sock]}, daemon=True)
    hilo.start()
    url = f"http://127.0.0.1:{puerto}/api/jobs/{job}"
    try:
        limite = time.monotonic() + 5
        while not servidor.started and time.monotonic() < limite:
            time.sleep(0.01)
        assert servidor.started
        cursor = None
        with requests.get(url + "/events", headers=e.headers, stream=True, timeout=(2, 3)) as respuesta:
            assert respuesta.status_code == 200 and respuesta.headers["content-type"].startswith("text/event-stream")
            assert respuesta.headers["x-request-id"]
            for linea in respuesta.iter_lines(chunk_size=1, decode_unicode=True):
                if linea.startswith("id: "):
                    cursor = int(linea[4:])
                if linea == ": heartbeat":
                    break
        # Cerrar el socket del stream no cancela ni inicia otra ejecución.
        assert cursor is not None
        assert requests.get(url, headers=e.headers, timeout=3).json()["status"] == "running"
        liberar.set()
        esperar(e, {"status_url": "/api/jobs/" + job})
        reconexion = requests.get(url + "/events", headers={**e.headers, "Last-Event-ID": str(cursor)}, timeout=3)
        assert reconexion.status_code == 200 and reconexion.text.endswith("\n\n")
        ids = [int(linea[4:]) for linea in reconexion.text.splitlines() if linea.startswith("id: ")]
        eventos = [json.loads(linea[6:]) for linea in reconexion.text.splitlines() if linea.startswith("data: ")]
        assert ids == sorted(set(ids)) and all(i > cursor for i in ids)
        assert eventos[-1]["status"] == "completed" and ejecuciones == [1]
    finally:
        liberar.set()
        servidor.should_exit = True
        hilo.join(5)
        sock.close()
        assert not hilo.is_alive()
