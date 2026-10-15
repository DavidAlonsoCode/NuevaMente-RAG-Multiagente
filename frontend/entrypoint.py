"""Espera a la API (/api/health), sirve el build de React+Vite en :8501 y hace proxy de /api/*."""

import http.client
import json
import math
import mimetypes
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener

DIST_DIR = Path(__file__).resolve().parent / "dist"
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def esperar_api(api_url: str, *, timeout: float = 60.0) -> None:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("API_HEALTH_WAIT_SECONDS debe ser positivo y finito")
    limite = time.monotonic() + timeout
    cliente = build_opener(ProxyHandler({}))
    while (restante := limite - time.monotonic()) > 0:
        try:
            with cliente.open(api_url.rstrip("/") + "/api/health", timeout=min(3.0, restante)) as resp:
                datos = json.loads(resp.read(4096))
                if resp.status == 200 and isinstance(datos, dict) and datos.get("status") == "ok":
                    return
        except (URLError, OSError, ValueError):
            pass
        time.sleep(min(1.0, max(0.0, limite - time.monotonic())))
    raise TimeoutError("La API no estuvo disponible dentro del plazo de arranque")


class FrontendHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    api_url = os.environ.get("API_URL", "http://backend:8000").rstrip("/")

    def log_message(self, format: str, *args) -> None:
        return

    def _proxy_api(self) -> None:
        parsed = urlsplit(self.api_url)
        conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(length) if length > 0 else None

        out_headers = {}
        for k, v in self.headers.items():
            if k.lower() not in HOP_BY_HOP and k.lower() != "host":
                out_headers[k] = v

        conn = conn_cls(parsed.hostname, port, timeout=65)
        try:
            conn.request(self.command, self.path, body=body, headers=out_headers)
            resp = conn.getresponse()
            self.send_response(resp.status)
            is_sse = resp.getheader("Content-Type", "").startswith("text/event-stream")
            for k, v in resp.getheaders():
                if k.lower() not in HOP_BY_HOP and k.lower() != "content-length":
                    self.send_header(k, v)
            if is_sse:
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                while chunk := resp.read(128):
                    self.wfile.write(chunk)
                    self.wfile.flush()
            else:
                data = resp.read()
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Connection", "close")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(data)
        except Exception:
            payload = json.dumps(
                {
                    "error": {"code": "SERVICE_UNAVAILABLE", "message": "Backend no disponible.", "details": {}},
                    "request_id": "req_proxy_err",
                }
            ).encode("utf-8")
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(payload)
        finally:
            conn.close()

    def _serve_static(self) -> None:
        if self.path in ("/_stcore/health", "/healthz"):
            body = b"ok"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
            return

        if self.path.startswith("/api/") or self.path == "/api":
            self._proxy_api()
            return

        clean_path = urlsplit(self.path).path.lstrip("/")
        candidate = (DIST_DIR / clean_path).resolve()
        if not clean_path or not str(candidate).startswith(str(DIST_DIR.resolve())) or not candidate.is_file():
            candidate = DIST_DIR / "index.html"

        data = candidate.read_bytes()
        mime, _ = mimetypes.guess_type(str(candidate))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def do_GET(self):
        self._serve_static()

    def do_HEAD(self):
        self._serve_static()

    def do_POST(self):
        self._proxy_api()

    def do_DELETE(self):
        self._proxy_api()


def main() -> None:
    api_url = os.environ.get("API_URL", "http://backend:8000")
    wait_seconds = float(os.environ.get("API_HEALTH_WAIT_SECONDS", "60"))
    try:
        print("Esperando disponibilidad de la API...", flush=True)
        esperar_api(api_url, timeout=wait_seconds)
    except (TimeoutError, ValueError) as error:
        print(str(error), file=sys.stderr, flush=True)
        raise SystemExit(1) from error

    FrontendHandler.api_url = api_url.rstrip("/")
    port = int(os.environ.get("PORT", "8501"))
    print(f"API disponible; iniciando servidor React NuevaMente en 0.0.0.0:{port}.", flush=True)
    server = ThreadingHTTPServer(("0.0.0.0", port), FrontendHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
