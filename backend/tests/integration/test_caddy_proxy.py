"""Issue 48: real Caddy/TCP/application/SQLite/React, isolated provider doubles.

Selected explicitly with -m caddy_proxy. Missing tools FAIL, never skip.
The ordinary mock suite retains its existing selection and deadline.
"""

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from caddy_browser_diagnostics import failure, read_diagnostic
from caddy_fixture import CANARIES, ROOT, ProxyHarness, clean_environment, cursor, private_write, sse

pytestmark = pytest.mark.caddy_proxy


@pytest.fixture(scope="module")
def proxy():
    (ROOT / ".data").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="caddy-proxy-", dir=ROOT / ".data") as temporary:
        harness = None
        try:
            harness = ProxyHarness(Path(temporary))
            harness.launch()
            yield harness
        finally:
            if harness:
                harness.close()
                # Include shutdown output as well as validation/start/reload;
                # raw channels remain private and are never uploaded.
                try:
                    harness.evidence["shutdown_log_audit"] = harness.audit_logs()
                except Exception as error:
                    harness.evidence["shutdown_log_audit"] = {"failed": True, "failure_type": type(error).__name__}
                    raise
                finally:
                    evidence = os.environ.get("NM_CADDY_EVIDENCE_DIR")
                    if evidence:
                        output = Path(evidence)
                        output.mkdir(parents=True, exist_ok=True)
                        private_write(output / "observations.json", json.dumps(harness.evidence, indent=2))


def progress(block):
    return next((json.loads(line[6:]) for line in block if line.startswith("data: ")), {})


def streaming_headers(response, h2):
    assert response.status_code == 200
    assert response.http_version == ("HTTP/2" if h2 else "HTTP/1.1")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    assert response.headers["x-request-id"]
    assert "content-encoding" not in response.headers
    assert "x-nuevamente-origin-key" not in response.headers
    assert "x-nuevamente-client-ip" not in response.headers


@pytest.mark.parametrize("h2", [False, True], ids=["http1", "http2"])
def test_redirects_headers_and_prefix(proxy, h2):
    with proxy.client(h2) as client:
        redirect = client.get("http://localhost:" + str(proxy.ports["http_port"]) + "/api/health?check=1")
        assert redirect.status_code == 308
        assert redirect.headers["location"] == proxy.url + "/api/health?check=1"
        spoof = {
            "Forwarded": "for=192.0.2.7;proto=http;host=evil.invalid",
            "X-Real-IP": "192.0.2.7",
            "X-Forwarded-For": "192.0.2.7",
            "X-Forwarded-Proto": "http",
            "X-Forwarded-Host": "evil.invalid",
            "X-NuevaMente-Origin-Key": CANARIES["HEADER"],
            "X-NuevaMente-Client-IP": "192.0.2.7",
            "Idempotency-Key": "synthetic-preserved",
            "Last-Event-ID": "7",
        }
        response = client.get("/api/__proxy_fixture/headers", headers=proxy.headers() | spoof)
        assert response.status_code == 200
        assert response.http_version == ("HTTP/2" if h2 else "HTTP/1.1")
        assert response.json() == {
            "origin_authenticated": True,
            "private_client_ip": "127.0.0.1",
            "host": proxy.authority,
            "scheme": "https",
            "client": "127.0.0.1",
            "forwarded": None,
            "real_ip": None,
            "idempotency": "synthetic-preserved",
            "cursor": "7",
            "owner_authenticated": True,
        }
        for name, expected in {
            "cache-control": "no-cache",
            "x-accel-buffering": "no",
            "retry-after": "2",
            "content-disposition": 'attachment; filename="fixture.txt"',
        }.items():
            assert response.headers[name] == expected
        assert "x-nuevamente-origin-key" not in response.headers
        assert "x-nuevamente-client-ip" not in response.headers
        normalized = client.get("/api/health/", headers=spoof)
        assert normalized.status_code == 307
        assert normalized.headers["location"] == proxy.url + "/api/health"
        # Exact /api belongs to backend as well as /api/*; the UI would return HTML.
        exact = client.get("/api")
        assert exact.status_code == 404 and exact.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("h2", [False, True], ids=["http1", "http2"])
def test_jobs_early_delivery_heartbeat_reconnect_and_owner(proxy, h2):
    with proxy.client(h2, timeout=20) as client:
        receipt = proxy.job(client)
        start = time.monotonic()
        observations, last_id = [], None
        try:
            with client.stream("GET", receipt["events_url"], headers=proxy.headers()) as response:
                streaming_headers(response, h2)
                for block, received in sse(response):
                    if cursor(block) is not None:
                        last_id = cursor(block)
                    if progress(block).get("step", "").startswith("proxy_probe_"):
                        observations.append(received - start)
                        assert received - start < 3, "event-buffered"
                    if block == [": heartbeat"]:
                        assert len(observations) == 2
                        assert observations[1] - observations[0] >= 0.25, "separate-events-arrived-together"
                        assert client.get(receipt["status_url"], headers=proxy.headers()).json()["status"] == "running"
                        diagnosis = client.get("/api/__proxy_fixture/diagnostics", headers=proxy.headers())
                        assert diagnosis.status_code == 200
                        stack = diagnosis.json()["worker_stack"]
                        assert stack and all(set(entry) == {"function", "file", "line"} for entry in stack)
                        assert any(entry["function"] == "work" for entry in stack)
                        break
            # Disconnect did not cancel the real worker; replay is strictly after cursor.
            assert last_id is not None
            assert client.get(receipt["status_url"], headers=proxy.headers()).json()["status"] == "running"
            assert client.get(receipt["events_url"], headers=proxy.headers(proxy.other)).status_code == 404
            proxy.release(receipt)
            proxy.wait_terminal(client, receipt)
            replay = client.get(receipt["events_url"], headers=proxy.headers() | {"Last-Event-ID": str(last_id)})
            ids = [int(line[4:]) for line in replay.text.splitlines() if line.startswith("id: ")]
            assert ids and ids == sorted(set(ids)) and all(item > last_id for item in ids)
            assert '"status": "completed"' in replay.text
            assert proxy.counters()["jobs"][receipt["job_id"]] == 1
            proxy.evidence["checks"].append(
                {
                    "probe": "job-sse",
                    "protocol": response.http_version,
                    "small_event_seconds": [round(value, 3) for value in observations],
                    "heartbeat_seconds": round(received - start, 3),
                    "executions": 1,
                }
            )
        finally:
            proxy.release(receipt)


def assert_first_event_early(response, start):
    block, received = next(sse(response))
    assert cursor(block) is not None
    assert received - start < 2, "event-buffered"


def test_deliberate_buffering_is_detected(proxy):
    with proxy.client(timeout=8) as client:
        receipt = proxy.job(client)
        release = threading.Timer(3.5, proxy.release, args=(receipt,))
        release.start()
        try:
            start = time.monotonic()
            with client.stream(
                "GET", receipt["events_url"], headers=proxy.headers() | {"X-Fixture-Buffer": "1"}
            ) as response:
                with pytest.raises(AssertionError, match="event-buffered"):
                    assert_first_event_early(response, start)
            proxy.wait_terminal(client, receipt)
            proxy.evidence["checks"].append({"probe": "buffering-fault", "detected": True})
        finally:
            proxy.release(receipt)
            release.cancel()
            release.join()


def upload(client, proxy, content=None):
    response = client.post(
        "/api/documents/upload",
        headers=proxy.headers(),
        json={
            "documento_titulo": "Redes VCN",
            "documento_contenido": content or "Una VCN conecta subredes privadas y conserva aislamiento. " * 20,
        },
    )
    assert response.status_code == 202, "real-upload-contract-failed"
    proxy.wait_terminal(client, response.json())
    return response.json()["document_id"]


@pytest.mark.parametrize("h2", [False, True], ids=["http1", "http2"])
def test_real_generations_sse_idempotency_and_disconnect(proxy, h2):
    gate = proxy.directory / "release-generation"
    gate.unlink(missing_ok=True)
    before = proxy.counters()["generation_calls"]
    with proxy.client(h2, timeout=20) as client:
        document = upload(client, proxy)
        payload = {
            "document_id": document,
            "perfil_destinatario": "principiante",
            "formato_salida": "flashcards",
            "nicho_sector": "general",
            "nivel_detalle": "didactico",
            "idioma_salida": "es",
        }
        key = "generation-" + str(h2)
        response = client.post("/api/generate", headers=proxy.headers() | {"Idempotency-Key": key}, json=payload)
        assert response.status_code == 202
        receipt = response.json()
        start = time.monotonic()
        events, last_id = [], None
        try:
            with client.stream("GET", receipt["events_url"], headers=proxy.headers()) as stream:
                streaming_headers(stream, h2)
                for block, received in sse(stream):
                    if cursor(block) is not None:
                        last_id = cursor(block)
                        events.append((progress(block), received - start))
                        assert received - start < 5, "generation-event-buffered"
                    if block == [": heartbeat"]:
                        assert last_id and len(events) >= 2
                        assert events[-1][1] - events[0][1] >= 0.25, "generation-events-arrived-together"
                        assert proxy.counters()["generation_calls"] == before + 1, (
                            "real-pipeline-did-not-reach-provider"
                        )
                        break
            proxy.evidence["checks"].append(
                {
                    "probe": "generation-live-before-completion",
                    "protocol": stream.http_version,
                    "early_event_seconds": [round(value, 3) for _, value in events],
                    "heartbeat_seconds": round(received - start, 3),
                    "provider_executions": 1,
                }
            )
            assert client.get(receipt["status_url"], headers=proxy.headers()).json()["status"] == "running"
            assert client.get(receipt["events_url"], headers=proxy.headers(proxy.other)).status_code == 404
            same = client.post("/api/generate", headers=proxy.headers() | {"Idempotency-Key": key}, json=payload)
            assert same.status_code == 202 and same.json()["generation_id"] == receipt["generation_id"]
            proxy.release_generation()
            proxy.wait_terminal(client, receipt)
            replay = client.get(receipt["events_url"], headers=proxy.headers() | {"Last-Event-ID": str(last_id)})
            ids = [int(line[4:]) for line in replay.text.splitlines() if line.startswith("id: ")]
            assert ids and ids == sorted(set(ids)) and all(item > last_id for item in ids)
            assert '"status": "completed"' in replay.text
            assert proxy.counters()["generation_calls"] == before + 1
            proxy.evidence["checks"].append(
                {
                    "probe": "generation-sse",
                    "protocol": stream.http_version,
                    "event_seconds": [round(value, 3) for _, value in events],
                    "provider_executions": 1,
                }
            )
        finally:
            private_write(gate, "release")


def browser_probe(proxy, hold=0):
    # Playwright supplies a fresh temporary --user-data-dir. Chrome on Windows
    # cannot launch with an invented OS USERPROFILE; do not impersonate one.
    # Forward native Chromium stdout/stderr through Playwright's browser
    # diagnostic channel so they are audited, rather than discarded internally.
    env = clean_environment() | {"DEBUG": "pw:browser"}
    name = "browser-" + str(hold)
    try:
        proxy.start(
            name,
            [
                sys.executable,
                str(ROOT / "backend/tests/integration/caddy_browser.py"),
                proxy.url,
                str(proxy.directory),
                "--hold",
                str(hold),
            ],
            env,
        )
    except Exception as error:
        proxy.evidence["checks"].append(
            {"probe": "real-browser", "process": "start_failed", "diagnostic": failure("process_start", error)}
        )
        raise AssertionError("real-browser-probe-start-failed") from None
    process = proxy.processes[name]
    try:
        exit_code = process.wait(timeout=hold + 90)
    except subprocess.TimeoutExpired as error:
        proxy.evidence["checks"].append(
            {"probe": "real-browser", "process": "timed_out", "diagnostic": failure("process_wait", error)}
        )
        raise AssertionError("real-browser-probe-timeout") from None
    observation = {
        "probe": "real-browser",
        "process": "exited_zero" if exit_code == 0 else "exited_nonzero",
        "diagnostic": read_diagnostic(proxy.directory / f"browser-{hold}.diagnostic.json"),
    }
    proxy.evidence["checks"].append(observation)
    assert exit_code == 0, "real-browser-probe-failed"
    assert observation["diagnostic"]["status"] == "passed", "real-browser-diagnostic-not-passed"
    assert proxy.counters()["ui_origin_authenticated"] > 0, "browser-origin-not-authenticated-by-backend"
    result = json.loads((proxy.directory / "browser-result.json").read_text())
    observation.update(result)


def test_react_real_browser_interaction_and_reload(proxy):
    browser_probe(proxy)


def test_long_silence_sse_slow_upload_and_browser(proxy):
    """>120s silence and >60s progressing upload, unchanged production timeouts."""
    with proxy.client(timeout=10) as client:
        assert client.post("/api/__proxy_fixture/heartbeat/135", headers=proxy.headers()).status_code == 200
        receipt = proxy.job(client)

    def silent(h2):
        with proxy.client(h2, timeout=150) as client:
            with client.stream("GET", receipt["events_url"], headers=proxy.headers()) as response:
                streaming_headers(response, h2)
                events = sse(response)
                for block, _ in events:
                    if progress(block).get("step") == "proxy_probe_2":
                        break
                quiet_start = time.monotonic()
                block, received = next(events)
                assert block == [": heartbeat"], "silent-period-ended-prematurely"
                assert received - quiet_start > 120, "insufficient-silent-period"
                assert client.get(receipt["status_url"], headers=proxy.headers()).json()["status"] == "running"
                return {"protocol": response.http_version, "silence_seconds": round(received - quiet_start, 2)}

    def slow_upload():
        # Other real owner: avoid competing for the controlled owner's one active job.
        body = json.dumps({"documento_titulo": "Slow upload", "documento_contenido": "Red privada. " * 100}).encode()

        chunk_times = []

        def chunks():
            for offset in range(0, len(body), 112):
                if offset:
                    time.sleep(6)
                chunk_times.append(time.monotonic())
                yield body[offset : offset + 112]

        start = time.monotonic()
        with proxy.client(timeout=150) as client:
            response = client.post(
                "/api/documents/upload",
                headers=proxy.headers(proxy.other)
                | {"Content-Type": "application/json", "Content-Length": str(len(body))},
                content=chunks(),
            )
            assert response.status_code == 202, "slow-progress-upload-rejected"
        assert chunk_times[-1] - chunk_times[0] > 60
        assert max(second - first for first, second in zip(chunk_times, chunk_times[1:])) < 60
        return {
            "upload_progress_seconds": round(chunk_times[-1] - chunk_times[0], 2),
            "upload_total_seconds": round(time.monotonic() - start, 2),
            "chunks": len(chunk_times),
        }

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(silent, False),
                executor.submit(silent, True),
                executor.submit(slow_upload),
                executor.submit(browser_probe, proxy, 130),
            ]
            results, failures = [], []
            for label, future in zip(("http1", "http2", "upload", "browser"), futures, strict=True):
                try:
                    results.append({"probe": label, "result": future.result()})
                except Exception as error:
                    failures.append({"probe": label, "failure_type": type(error).__name__})
                    results.append({"probe": label, "failed": True})
        proxy.evidence["checks"].append({"probe": "long-lived", "observations": results[:3]})
        assert not failures, "long-lived-probes-failed:" + json.dumps(failures)
    finally:
        proxy.release(receipt)
        with proxy.client() as client:
            client.post("/api/__proxy_fixture/heartbeat/15", headers=proxy.headers())
            proxy.wait_terminal(client, receipt)


def test_all_logs_canaries_reload_and_upstream_failure(proxy):
    with proxy.client() as client:
        headers = {
            "Authorization": "Bearer " + CANARIES["AUTH"],
            "Cookie": "fixture=" + CANARIES["COOKIE"],
            "X-NuevaMente-Origin-Key": CANARIES["HEADER"],
            "X-NuevaMente-Client-IP": CANARIES["HEADER"],
        }
        response = client.post(
            "/api/" + CANARIES["URI"] + "?probe=" + CANARIES["QUERY"], headers=headers, content=CANARIES["BODY"]
        )
        assert response.status_code == 404
        client.post("/api/documents/upload?probe=" + CANARIES["QUERY"], headers=headers, content=CANARIES["BODY"])
        proxy.reload()
        backend = proxy.processes["backend"]
        backend.terminate()
        backend.wait(8)
        response = client.post(
            "/api/" + CANARIES["URI"] + "?probe=" + CANARIES["QUERY"], headers=headers, content=CANARIES["BODY"]
        )
        assert response.status_code == 502
    time.sleep(0.2)
    result = proxy.audit_logs()
    rows = [json.loads(line) for line in (proxy.directory / "logs/access.json").read_text().splitlines()]
    assert any(row.get("status") == 502 and row.get("component") == "api" for row in rows)
    operations = [json.loads(line) for line in (proxy.directory / "logs/operational.json").read_text().splitlines()]
    assert any(row.get("level") == "error" for row in operations), "upstream-error-diagnostic-missing"
    proxy.evidence["checks"].append({"probe": "privacy-all-channels", **result})
