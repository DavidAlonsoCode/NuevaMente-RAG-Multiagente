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
from urllib.parse import unquote, urlsplit
from urllib.request import ProxyHandler, build_opener

DIST_DIR = Path(__file__).resolve().parent / "dist"
mimetypes.add_type("text/javascript", ".js")
HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


# con esta funcion espero activamente que el backend este disponible respondiendo exitosamente en su ruta de diagnostico (health check)
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


# con este manejador http sirvo la aplicacion web construida en react y reenvio peticiones de api hacia fastapi como un proxy inverso
class FrontendHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    api_url = os.environ.get("API_URL", "http://backend:8000").rstrip("/")

    def log_message(self, format: str, *args) -> None:
        return

    # aqui reenvio la peticion hacia el backend filtrando cabeceras de salto (hop by hop) y soportando flujos de eventos sse
    def _proxy_api(self) -> None:
        route = urlsplit(self.path)
        if route.scheme or route.netloc or not (route.path.startswith("/api/") or route.path == "/api"):
            self.send_error(404)
            return
        parsed = urlsplit(self.api_url)
        conn_cls = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        self.connection.settimeout(65)
        if self.headers.get("Transfer-Encoding"):
            self.send_error(411)
            return
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) > 1 or (lengths and not lengths[0].isdigit()):
            self.send_error(400)
            return
        length = int(lengths[0]) if lengths else 0
        if length > 32 * 1024 * 1024:
            self.send_error(413)
            return
        try:
            body = self.rfile.read(length) if length > 0 else None
        except OSError:
            self.send_error(408)
            return
        if body is not None and len(body) != length:
            self.send_error(400)
            return

        out_headers = {}
        for k, v in self.headers.items():
            if k.lower() not in HOP_BY_HOP and k.lower() != "host":
                out_headers[k] = v

        conn = conn_cls(parsed.hostname, port, timeout=65)
        started = False
        try:
            conn.request(self.command, self.path, body=body, headers=out_headers)
            resp = conn.getresponse()
            self.send_response(resp.status)
            started = True
            is_sse = resp.getheader("Content-Type", "").startswith("text/event-stream")
            for k, v in resp.getheaders():
                if k.lower() not in HOP_BY_HOP | {
                    "content-length",
                    "x-nuevamente-origin-key",
                    "x-nuevamente-client-ip",
                }:
                    self.send_header(k, v)
            if is_sse:
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                while chunk := resp.read1(65536):
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
            if started:
                self.close_connection = True
                return
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

    # con este metodo entrego los archivos estaticos generados por vite o redirijo cualquier ruta no encontrada hacia index html para la navegacion spa
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

        clean_path = unquote(urlsplit(self.path).path).lstrip("/")
        candidate = (DIST_DIR / clean_path).resolve()
        if not candidate.is_relative_to(DIST_DIR.resolve()):
            self.send_error(404)
            return
        if not clean_path or not candidate.is_file():
            candidate = DIST_DIR / "index.html"

        data = candidate.read_bytes()
        mime, _ = mimetypes.guess_type(str(candidate))
        self.send_response(200)
        self.send_header("Content-Type", mime or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; connect-src 'self'; font-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
        )
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


# con esta funcion principal inicio el servidor web multihilo escuchando las conexiones del usuario en el puerto configurado
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
    host = os.environ.get("FRONTEND_HOST", "127.0.0.1")
    print(f"API disponible; iniciando servidor React NuevaMente en {host}:{port}.", flush=True)
    server = ThreadingHTTPServer((host, port), FrontendHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
