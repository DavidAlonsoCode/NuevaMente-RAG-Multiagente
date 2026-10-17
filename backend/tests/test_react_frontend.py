"""Servidor estático/proxy de React con upstream local sintético, sin proveedores."""

import contextlib
import http.client
import importlib.util
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration_mock


@contextlib.contextmanager
def server(handler):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_address[1]
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(3)


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "frontend/entrypoint.py"
    spec = importlib.util.spec_from_file_location("react_gateway_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html>synthetic React build</html>")
    (dist / "app.js").write_text("console.log('synthetic')")
    sibling = tmp_path / "dist-private"
    sibling.mkdir()
    (sibling / "private.txt").write_text("must-not-be-served")
    monkeypatch.setattr(module, "DIST_DIR", dist)
    return module


def test_static_spa_and_sibling_traversal(gateway):
    with server(gateway.FrontendHandler) as port:
        for path, status in [
            ("/", 200),
            ("/history", 200),
            ("/healthz", 200),
            ("/%2e%2e/dist-private/private.txt", 404),
        ]:
            with contextlib.closing(http.client.HTTPConnection("127.0.0.1", port, timeout=3)) as conn:
                conn.request("GET", path)
                response = conn.getresponse()
                assert response.status == status
                assert b"must-not-be-served" not in response.read()


@pytest.mark.parametrize("length,status", [("-1", 400), ("invalid", 400), (str(33 * 1024 * 1024), 413)])
def test_proxy_rejects_invalid_body_before_reading_or_connecting(gateway, length, status):
    with server(gateway.FrontendHandler) as port:
        with contextlib.closing(http.client.HTTPConnection("127.0.0.1", port, timeout=3)) as conn:
            conn.putrequest("POST", "/api/documents/upload")
            conn.putheader("Content-Length", length)
            conn.endheaders()
            assert conn.getresponse().status == status


def test_sse_first_event_is_forwarded_without_waiting_for_128_bytes(gateway, monkeypatch):
    release = threading.Event()

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("X-NuevaMente-Origin-Key", "synthetic-server-secret")
            self.end_headers()
            self.wfile.write(b"id: 1\nevent: progress\ndata: {}\n\n")
            self.wfile.flush()
            release.wait(3)

    try:
        with server(Upstream) as upstream:
            monkeypatch.setattr(gateway.FrontendHandler, "api_url", f"http://127.0.0.1:{upstream}")
            with server(gateway.FrontendHandler) as port:
                with contextlib.closing(http.client.HTTPConnection("127.0.0.1", port, timeout=1)) as conn:
                    conn.request("GET", "/api/jobs/synthetic/events")
                    response = conn.getresponse()
                    assert response.getheader("X-NuevaMente-Origin-Key") is None
                    assert response.readline() == b"id: 1\n"
                    release.set()
    finally:
        release.set()
