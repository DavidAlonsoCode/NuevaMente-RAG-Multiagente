"""Temporary real application/worker/SQLite; only external providers are doubles.

Not deployed. Test controls are authenticated and never part of the production
router. A socket audit hook rejects external connections, including telemetry.
"""

import argparse
import ipaddress
import json
import os
import sys
import threading
import time
from pathlib import Path

import uvicorn
from caddy_diagnostics import Timeline, status_probe, worker_stack
from caddy_profile import Profile
from fastapi import Depends, Request
from fastapi.responses import JSONResponse


def loopback_only(event, args):
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple):
            if not ipaddress.ip_address(address[0]).is_loopback:
                raise PermissionError("test-external-egress-rejected")
    if event == "socket.getaddrinfo" and args[0] not in {"localhost", "127.0.0.1", "::1", None}:
        raise PermissionError("test-external-dns-rejected")


def main(path):
    sys.addaudithook(loopback_only)
    from app.api.deps import get_sesion_actual
    from app.api.routes import jobs
    from app.config import Configuracion
    from app.jobs.manager import CuotasModelo, CuotasProveedor
    from app.main import crear_app
    from test_api_generate import DobleEducativo

    settings = json.loads(Path(path).read_text())
    directory = Path(settings["directory"])
    mode = settings.get("diagnostic_mode", "off")
    assert mode in {"off", "profile"}, "fixture-diagnostic-mode-invalid"
    timeline = Timeline(enabled=mode == "profile")
    profile = Profile() if mode == "profile" else None
    if profile:
        profile.install()
    counters = {"jobs": {}, "generation_calls": 0, "verification_calls": 0, "ui_origin_authenticated": 0}
    lock = threading.Lock()

    def record(kind, job_id=None):
        with lock:
            if job_id:
                counters[kind][job_id] = counters[kind].get(job_id, 0) + 1
            else:
                counters[kind] += 1
            temporary = directory / "counters.pending"
            temporary.write_text(json.dumps(counters))
            os.replace(temporary, directory / "counters.json")

    class DelayedProvider(DobleEducativo):
        def embed_sync(self, textos, **kwargs):
            time.sleep(0.6)
            return super().embed_sync(textos, **kwargs)

        def generar_sync(self, prompt, **kwargs):
            record("generation_calls")
            timeline.record("provider-start")
            deadline = time.monotonic() + 50
            while not (directory / "release-generation").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("synthetic-provider-gate-expired")
                time.sleep(0.05)
            timeline.record("provider-release-observed")
            time.sleep(0.4)
            try:
                return super().generar_sync(prompt, **kwargs)
            finally:
                timeline.record("provider-end")

        def _revisar(self, **kwargs):
            record("verification_calls")
            timeline.record("verification-start")
            time.sleep(0.4)
            try:
                return super()._revisar(**kwargs)
            finally:
                timeline.record("verification-end")

    cfg = Configuracion(
        _env_file=None,
        app_env="test",
        mock_oci=True,
        mock_gemini=True,
        data_dir=str(directory / "application-data"),
        embedding_dimensions=16,
        trusted_origin_secret=settings["secret"],
    )
    provider = DelayedProvider(16)
    quotas = CuotasProveedor(
        {
            model: CuotasModelo(1000, 10_000_000, 1000)
            for model in (cfg.gemini_embedding_model, cfg.gemini_generation_model, cfg.gemini_verification_model)
        }
    )
    app = crear_app(
        cfg,
        proveedor_embeddings=provider,
        proveedor_generacion=provider,
        proveedor_verificacion=provider,
        cuotas_proveedor=quotas,
    )

    @app.middleware("http")
    async def inspect_server_origin(request, call_next):
        if request.headers.get("user-agent", "").startswith("python-requests"):
            if (
                request.headers.get("x-nuevamente-origin-key") == settings["secret"]
                and request.headers.get("x-nuevamente-client-ip") == "127.0.0.1"
            ):
                record("ui_origin_authenticated")
        probe = request.headers.get("x-fixture-query", "")
        observed = timeline.enabled and status_probe(
            request.url.path,
            probe,
            authenticated=request.headers.get("x-nuevamente-origin-key") == settings["secret"],
            method=request.method,
        )
        if observed:
            timeline.record("query-start", query=int(probe), worker=app.state.jobs_manager._hilo)
        response = None
        try:
            response = await call_next(request)
            return response
        finally:
            if observed:
                # Header response milestone only; never consume/buffer the body.
                timeline.record(
                    "query-end",
                    query=int(probe),
                    response_code=response.status_code if response else None,
                    worker=app.state.jobs_manager._hilo,
                )

    @app.get("/api/__proxy_fixture/headers")
    def headers(request: Request, session=Depends(get_sesion_actual)):
        return JSONResponse(
            {
                "origin_authenticated": request.headers.get("x-nuevamente-origin-key") == settings["secret"],
                "private_client_ip": request.headers.get("x-nuevamente-client-ip"),
                "host": request.headers.get("host"),
                "scheme": request.url.scheme,
                "client": request.client.host,
                "forwarded": request.headers.get("forwarded"),
                "real_ip": request.headers.get("x-real-ip"),
                "idempotency": request.headers.get("idempotency-key"),
                "cursor": request.headers.get("last-event-id"),
                "owner_authenticated": bool(session),
            },
            headers={
                "X-NuevaMente-Origin-Key": settings["secret"],
                "X-NuevaMente-Client-IP": "private",
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Retry-After": "2",
                "Content-Disposition": 'attachment; filename="fixture.txt"',
            },
        )

    @app.post("/api/__proxy_fixture/jobs")
    def controlled_job(session=Depends(get_sesion_actual)):
        manager = app.state.jobs_manager
        started = threading.Event()
        job_id = None

        def work(ctx):
            assert started.wait(2), "job-registration-timeout"
            record("jobs", job_id)
            for number in (1, 2):
                ctx.chequear()
                ctx.registrar_paso("proxy_probe_" + str(number), number)
                time.sleep(0.6)
            while not (directory / ("release-" + job_id)).exists():
                ctx.chequear()
                time.sleep(0.05)
            return {"synthetic": True}

        job_id = manager.enqueue(session["workspace_id"], "ingestion", work)
        started.set()
        return {"job_id": job_id, "status_url": "/api/jobs/" + job_id, "events_url": "/api/jobs/" + job_id + "/events"}

    @app.post("/api/__proxy_fixture/heartbeat/{seconds}")
    def heartbeat(seconds: int, session=Depends(get_sesion_actual)):
        assert seconds in (15, 135), "fixture-heartbeat-out-of-range"
        jobs.INTERVALO_HEARTBEAT = seconds
        return {"heartbeat_seconds": seconds}

    @app.get("/api/__proxy_fixture/diagnostics")
    def diagnostics(session=Depends(get_sesion_actual)):
        # Function/file/line only. No source lines, locals, payloads or tokens.
        return {"worker_stack": worker_stack(app.state.jobs_manager._hilo)}

    @app.post("/api/__proxy_fixture/profile")
    def export_profile(session=Depends(get_sesion_actual)):
        if profile is None:
            return JSONResponse({"profile": "disabled"}, status_code=404)
        # The client calls this only AFTER its measured wait, including failures.
        return {"timeline": timeline.snapshot(), "profile": profile.snapshot()}

    # A deliberate buffering fault wraps the REAL application SSE body. It does
    # not implement a second SSE endpoint or replace the worker/SQLite readers.
    class SeedBuffering:
        def __init__(self, wrapped):
            self.wrapped = wrapped

        async def __call__(self, scope, receive, send):
            enabled = any(k == b"x-fixture-buffer" and v == b"1" for k, v in scope.get("headers", []))
            collected = []

            async def buffered(message):
                if enabled and scope.get("path", "").endswith("/events") and message["type"] == "http.response.body":
                    collected.append(message.get("body", b""))
                    if not message.get("more_body", False):
                        await send({"type": "http.response.body", "body": b"".join(collected)})
                else:
                    await send(message)

            await self.wrapped(scope, receive, buffered)

    try:
        uvicorn.run(
            SeedBuffering(app),
            host="127.0.0.1",
            port=settings["backend_port"],
            access_log=False,
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
            log_level="info",
            lifespan="on",
        )
    finally:
        if profile:
            profile.restore()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    main(parser.parse_args().config)
