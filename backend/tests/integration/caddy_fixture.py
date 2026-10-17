"""Ephemeral TCP/TLS harness. Nothing is installed or published on the host."""

import contextlib
import gzip
import json
import os
import socket
import ssl
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[3]
SECRET = "8123456789abcdef" * 4
CANARIES = {name: "NM48_PRIVATE_" + name + "_a791e" for name in ("AUTH", "COOKIE", "URI", "QUERY", "BODY", "HEADER")}


def private_write(path, content):
    data = content.encode() if isinstance(content, str) else content
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as file:
        file.write(data)


def clean_environment():
    allowed = {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "PLAYWRIGHT_BROWSERS_PATH", "NM_BROWSER_EXECUTABLE"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    paths = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
    tools = os.environ.get("NM_TEST_TOOLS_PYTHONPATH")
    if tools:
        paths.insert(0, tools)
    env.update(
        PYTHONPATH=os.pathsep.join(paths),
        APP_ENV="test",
        MOCK_OCI="1",
        MOCK_GEMINI="1",
        LANGSMITH_TRACING="false",
        LANGCHAIN_TRACING_V2="false",
        PYTHONUNBUFFERED="1",
    )
    return env


def unused_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def certificate(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    private_write(directory / "tls.pem", cert.public_bytes(serialization.Encoding.PEM))
    private_write(
        directory / "tls.key",
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()),
    )


def native_policy(base, local, ports):
    """Compare native adapted routes and all relevant server policy, not YAML/text."""

    def server(config, port):
        return next(
            s
            for s in config["apps"]["http"]["servers"].values()
            if any(address.endswith(":" + str(port)) for address in s["listen"])
        )

    def normalized(value):
        text = json.dumps(value, sort_keys=True)
        for first, second in (
            ("localhost:" + str(ports["https_port"]), "localhost"),
            ("127.0.0.1:" + str(ports["backend_port"]), "127.0.0.1:8000"),
            ("127.0.0.1:" + str(ports["ui_port"]), "127.0.0.1:8501"),
        ):
            text = text.replace(first, second)
        groups = {}

        def canonical(node):
            if isinstance(node, dict):
                return {
                    key: groups.setdefault(item, "group" + str(len(groups))) if key == "group" else canonical(item)
                    for key, item in node.items()
                }
            if isinstance(node, list):
                return [canonical(item) for item in node]
            return node

        return canonical(json.loads(text))

    for original, adapted in ((80, ports["http_port"]), (443, ports["https_port"])):
        before, after = server(base, original), server(local, adapted)
        for key in (
            "routes",
            "logs",
            "read_header_timeout",
            "read_idle_timeout",
            "write_idle_timeout",
            "idle_timeout",
            "protocols",
            "strict_sni_host",
        ):
            assert normalized(after.get(key)) == normalized(before.get(key)), "fixture-altered-production-policy:" + key
    assert local["logging"] == base["logging"], "fixture-altered-log-policy"


class ProxyHarness:
    def __init__(self, directory, *, frontend="react", reject_quality=False):
        assert frontend == "react"
        self.frontend = frontend
        self.reject_quality = reject_quality
        self.directory = Path(directory)
        self.processes, self.handles = {}, []
        self.evidence = {"platform": sys.platform, "caddy": "2.11.7", "checks": []}
        self.diagnostic_mode = os.environ.get("NM_CADDY_DIAGNOSTIC", "off")
        assert self.diagnostic_mode in {"off", "profile"}, "fixture-diagnostic-mode-invalid"
        self.evidence["diagnostic_mode"] = self.diagnostic_mode
        self.query_sequence = 0
        self.provider_releases = []
        self.ports = {
            key: unused_port() for key in ("http_port", "https_port", "backend_port", "ui_port", "admin_port")
        }
        assert len(set(self.ports.values())) == len(self.ports), "fixture-port-collision"
        self.authority = "localhost:" + str(self.ports["https_port"])
        self.url = "https://" + self.authority
        self.env = clean_environment() | {
            "HOME": str(self.directory),
            "USERPROFILE": str(self.directory),
            "NUEVAMENTE_DOMAIN": self.authority,
            "TRUSTED_ORIGIN_SECRET": SECRET,
            "CADDY_LOG_DIR": (self.directory / "logs").as_posix(),
            "CADDY_STORAGE_DIR": (self.directory / "storage").as_posix(),
            "XDG_DATA_HOME": (self.directory / "xdg-data").as_posix(),
            "XDG_CONFIG_HOME": (self.directory / "xdg-config").as_posix(),
            "APPDATA": (self.directory / "appdata").as_posix(),
        }
        self.binary = os.environ.get("CADDY_BIN")
        assert self.binary and Path(self.binary).is_file(), "required-caddy-binary-unavailable"
        version = self.command("version", [self.binary, "version"])
        assert version.stdout.split()[0] == b"v2.11.7", "required-caddy-version-mismatch"
        (self.directory / "logs").mkdir()
        certificate(self.directory)
        self.context = ssl.create_default_context(cafile=str(self.directory / "tls.pem"))
        self.config = self.directory / "Caddyfile"
        self.admin = "127.0.0.1:" + str(self.ports["admin_port"])
        self.make_configuration()

    def command(self, label, argv, *, env=None, success=True):
        result = subprocess.run(argv, env=env or self.env, cwd=ROOT, capture_output=True, timeout=35)
        private_write(self.directory / (label + ".stdout"), result.stdout)
        private_write(self.directory / (label + ".stderr"), result.stderr)
        if success:
            assert result.returncode == 0, "native-command-failed:" + label
        return result

    def make_configuration(self):
        original = (ROOT / "infra/caddy/Caddyfile").read_text()
        tls = "\ttls {\n\t\tissuer acme {\n\t\t\tdir https://acme-v02.api.letsencrypt.org/directory\n\t\t}\n\t}"
        assert original.count(tls) == 1, "production-tls-template-changed"
        local = original.replace(
            "{\n",
            "{\n\tdefault_bind 127.0.0.1\n\tauto_https disable_certs\n"
            + "\thttp_port "
            + str(self.ports["http_port"])
            + "\n"
            + "\thttps_port "
            + str(self.ports["https_port"])
            + "\n",
            1,
        )
        local = local.replace("admin unix//run/caddy/admin.sock|0200", "admin " + self.admin)
        local = local.replace("http://{$NUEVAMENTE_DOMAIN}", "http://localhost:" + str(self.ports["http_port"]))
        local = local.replace(
            tls, f'\ttls "{(self.directory / "tls.pem").as_posix()}" "{(self.directory / "tls.key").as_posix()}"'
        )
        local = local.replace("127.0.0.1:8000", "127.0.0.1:" + str(self.ports["backend_port"]))
        local = local.replace("127.0.0.1:8501", "127.0.0.1:" + str(self.ports["ui_port"]))
        private_write(self.config, local)
        formatted = self.command("format", [self.binary, "fmt", str(self.config)])
        private_write(self.config, formatted.stdout)
        self.command("validate", [self.binary, "validate", "--config", str(self.config)])
        adapted = json.loads(self.command("adapt", [self.binary, "adapt", "--config", str(self.config)]).stdout)
        production = json.loads(
            self.command(
                "production-adapt",
                [self.binary, "adapt", "--config", str(ROOT / "infra/caddy/Caddyfile")],
                env=self.env | {"NUEVAMENTE_DOMAIN": "localhost"},
            ).stdout
        )
        native_policy(production, adapted, self.ports)

    def start(self, name, argv, env):
        output = []
        for channel in ("stdout", "stderr"):
            file = os.fdopen(os.open(self.directory / (name + "." + channel), os.O_WRONLY | os.O_CREAT, 0o600), "wb")
            output.append(file)
            self.handles.append(file)
        # Imported app.main creates its ordinary ASGI app as well. A temporary
        # cwd isolates that initialization and prevents reading the repo .env.
        self.processes[name] = subprocess.Popen(argv, cwd=self.directory, env=env, stdout=output[0], stderr=output[1])

    def launch(self):
        assert not (self.directory / "application-data").exists(), "fixture-data-not-fresh"
        private_write(
            self.directory / "backend-config.json",
            json.dumps(
                self.ports
                | {
                    "directory": str(self.directory),
                    "secret": SECRET,
                    "diagnostic_mode": self.diagnostic_mode,
                    "reject_quality": self.reject_quality,
                }
            ),
        )
        self.start(
            "backend",
            [
                sys.executable,
                str(ROOT / "backend/tests/integration/caddy_backend.py"),
                str(self.directory / "backend-config.json"),
            ],
            clean_environment() | {"HOME": str(self.directory), "USERPROFILE": str(self.directory)},
        )
        self.wait_http("http://127.0.0.1:" + str(self.ports["backend_port"]) + "/api/health", "backend")
        self.start("caddy", [self.binary, "run", "--config", str(self.config)], self.env)
        self.wait_http(self.url + "/api/health", "caddy")
        self.launch_ui()
        with self.client() as client:
            self.owner = self.workspace(client)
            self.other = self.workspace(client)
        self.evidence["initial_state"] = {
            "data": "fresh",
            "workspaces": 2,
            "generation_calls": self.counters()["generation_calls"],
            "providers": "explicit-doubles",
        }

    def launch_ui(self):
        assert (ROOT / "frontend/dist/index.html").is_file(), "react-build-required"
        self.start(
            "react",
            [sys.executable, str(ROOT / "frontend/entrypoint.py")],
            clean_environment()
            | {
                "API_URL": "http://127.0.0.1:" + str(self.ports["backend_port"]),
                "PORT": str(self.ports["ui_port"]),
                "FRONTEND_HOST": "127.0.0.1",
            },
        )
        self.wait_http(self.url + "/healthz", "react")

    def wait_http(self, url, process):
        deadline = time.monotonic() + 35
        with httpx.Client(verify=self.context, trust_env=False, timeout=2) as client:
            while time.monotonic() < deadline:
                assert self.processes[process].poll() is None, "temporary-process-exited:" + process
                try:
                    if client.get(url).status_code == 200:
                        return
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
        raise AssertionError("temporary-process-health-timeout:" + process)

    @contextlib.contextmanager
    def client(self, h2=False, timeout=8):
        # httpx sets ALPN on its SSLContext. Sharing one across simultaneous
        # HTTP/1 and HTTP/2 clients races that negotiation in the test itself.
        context = ssl.create_default_context(cafile=str(self.directory / "tls.pem"))
        with httpx.Client(base_url=self.url, verify=context, trust_env=False, http2=h2, timeout=timeout) as client:
            yield client

    @staticmethod
    def workspace(client):
        response = client.post("/api/workspaces")
        assert response.status_code == 201, "workspace-creation-failed"
        return response.json()

    def headers(self, owner=None):
        return {"Authorization": "Bearer " + (owner or self.owner)["token"]}

    def job(self, client):
        response = client.post("/api/__proxy_fixture/jobs", headers=self.headers())
        assert response.status_code == 200, "controlled-real-worker-enqueue-failed"
        return response.json()

    def release(self, receipt):
        private_write(self.directory / ("release-" + receipt["job_id"]), "release")

    def release_generation(self):
        if self.diagnostic_mode == "off":
            private_write(self.directory / "release-generation", "release")
            return
        observation = {"event": "provider-gate-published", "start": round(time.monotonic(), 6)}
        private_write(self.directory / "release-generation", "release")
        observation["end"] = round(time.monotonic(), 6)
        self.provider_releases.append(observation)

    def wait_terminal(self, client, receipt):
        if self.diagnostic_mode == "off":
            return self.wait_terminal_unprofiled(client, receipt)
        started = time.monotonic()
        deadline = started + 15
        observation = {
            "probe": "application-terminal-wait",
            "clock": "monotonic-shared-host",
            "start": round(started, 6),
            "deadline": round(deadline, 6),
            "limit_seconds": 15,
            "queries": [],
            "outcome": "exception",
        }
        try:
            status = "not-observed"
            while time.monotonic() < deadline:
                self.query_sequence += 1
                query = {"query": self.query_sequence, "start": round(time.monotonic(), 6)}
                observation["queries"].append(query)
                try:
                    response = client.get(
                        receipt["status_url"], headers=self.headers() | {"X-Fixture-Query": str(query["query"])}
                    )
                finally:
                    query["end"] = round(time.monotonic(), 6)
                query["http_status"] = response.status_code
                query["protocol"] = response.http_version
                assert response.status_code == 200, "status-contract-failed"
                status = response.json()["status"]
                assert status in {"queued", "running", "completed", "rejected_quality", "failed", "cancelled"}, (
                    "unexpected-job-status"
                )
                query["status"] = status
                query["ended_after_deadline"] = query["end"] > deadline
                if status not in {"queued", "running"}:
                    assert status == "completed", "real-application-work-failed"
                    observation["outcome"] = "completed"
                    return response.json()
                time.sleep(0.1)
            observation["outcome"] = "timeout"
            diagnosis = {
                "probe": "application-terminal-timeout",
                "proxy_status": status,
                "at": round(time.monotonic(), 6),
            }
            try:
                # Sequential AFTER-deadline diagnosis, never a successful fallback.
                with httpx.Client(trust_env=False, timeout=2) as direct:
                    base = "http://127.0.0.1:" + str(self.ports["backend_port"])
                    headers = self.headers() | {
                        "X-NuevaMente-Origin-Key": SECRET,
                        "X-NuevaMente-Client-IP": "127.0.0.1",
                    }
                    diagnosis["direct_start"] = round(time.monotonic(), 6)
                    state = direct.get(base + receipt["status_url"], headers=headers)
                    diagnosis["direct_end"] = round(time.monotonic(), 6)
                    direct_status = state.json().get("status")
                    assert direct_status in {
                        "queued",
                        "running",
                        "completed",
                        "rejected_quality",
                        "failed",
                        "cancelled",
                    }
                    diagnosis["direct_backend_status"] = direct_status
                    diagnosis["worker_observation_start"] = round(time.monotonic(), 6)
                    stack = direct.get(base + "/api/__proxy_fixture/diagnostics", headers=headers)
                    diagnosis["worker_observation_end"] = round(time.monotonic(), 6)
                    diagnosis["worker_stack"] = stack.json()["worker_stack"]
            except (httpx.HTTPError, ValueError, KeyError) as error:
                diagnosis["diagnostic_failure_type"] = type(error).__name__
            self.evidence["checks"].append(diagnosis)
            raise AssertionError("real-application-status-timeout")
        finally:
            observation["end"] = round(time.monotonic(), 6)
            observation["provider_releases"] = list(self.provider_releases)
            self.evidence["checks"].append(observation)
            observation["backend_export"] = self.export_profile()

    def wait_terminal_unprofiled(self, client, receipt):
        # Same requests/assertions/deadline, without query or worker instrumentation.
        started = time.monotonic()
        deadline = started + 15
        outcome = "exception"
        try:
            while time.monotonic() < deadline:
                response = client.get(receipt["status_url"], headers=self.headers())
                assert response.status_code == 200, "status-contract-failed"
                status = response.json()["status"]
                assert status in {"queued", "running", "completed", "rejected_quality", "failed", "cancelled"}, (
                    "unexpected-job-status"
                )
                if status not in {"queued", "running"}:
                    assert status == "completed", "real-application-work-failed"
                    outcome = "completed"
                    return response.json()
                time.sleep(0.1)
            outcome = "timeout"
            raise AssertionError("real-application-status-timeout")
        finally:
            self.evidence["checks"].append(
                {
                    "probe": "application-terminal-wait-unprofiled",
                    "limit_seconds": 15,
                    "start": started,
                    "end": time.monotonic(),
                    "outcome": outcome,
                }
            )

    def export_profile(self):
        # No successful fallback. Export errors are visible without masking the
        # original pipeline failure; artifact consumers must reject failed exports.
        started = time.monotonic()
        try:
            with self.client(timeout=3) as client:
                result = client.post("/api/__proxy_fixture/profile", headers=self.headers())
                assert result.status_code == 200, "fixture-profile-export-failed"
                return result.json() | {"export_start": started, "export_end": time.monotonic()}
        except (httpx.HTTPError, AssertionError, ValueError) as error:
            return {
                "export_failed": True,
                "failure_type": type(error).__name__,
                "export_start": started,
                "export_end": time.monotonic(),
            }

    def counters(self):
        path = self.directory / "counters.json"
        return json.loads(path.read_text()) if path.exists() else {"generation_calls": 0, "jobs": {}}

    def reload(self):
        self.command(
            "reload", [self.binary, "reload", "--config", str(self.config), "--address", self.admin, "--force"]
        )

    def audit_logs(self):
        # Inspect every actual stdio channel and every rotated Caddy log. Never
        # put config, responses, cookies, packets or the browser profile in CI artifacts.
        paths = [
            *self.directory.glob("*.stdout"),
            *self.directory.glob("*.stderr"),
            *(self.directory / "logs").glob("*"),
            *self.directory.glob("timeline.json"),
            *self.directory.glob("timeline.pending"),
        ]
        forbidden = [SECRET.encode(), *(value.encode() for value in CANARIES.values())]
        for path in paths:
            if path.is_file():
                content = path.read_bytes()
                if path.suffix == ".gz":
                    content = gzip.decompress(content)
                assert not any(value in content for value in forbidden), "private-canary-in-channel:" + path.name
        rows = [json.loads(line) for line in (self.directory / "logs/access.json").read_text().splitlines()]
        assert any(row.get("component") == "api" and row.get("status") == 200 for row in rows), (
            "access-diagnostic-missing"
        )
        incomplete = [
            {"status": row.get("status"), "fields": sorted(row)}
            for row in rows
            if not ("duration" in row and "size" in row and row.get("proxy_request_id"))
        ]
        assert not incomplete, "access-diagnostic-incomplete:" + json.dumps(incomplete)
        operation = [json.loads(line) for line in (self.directory / "logs/operational.json").read_text().splitlines()]
        assert operation and all("level" in row and "ts" in row for row in operation), "operational-diagnostic-missing"
        return {"channels_audited": len(paths), "access_rows": len(rows), "operational_rows": len(operation)}

    def close(self):
        if self.diagnostic_mode == "profile" and "owner" in self.__dict__:
            self.evidence["profile_final"] = self.export_profile()
        # Release only synthetic jobs before shutting down. Never touch app data
        # on the host, a daemon, DNS, public certificates or production services.
        for job_id in self.counters().get("jobs", {}):
            private_write(self.directory / ("release-" + job_id), "release")
        private_write(self.directory / "release-generation", "release")
        for process in reversed(list(self.processes.values())):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(5)
        for handle in self.handles:
            handle.close()


def sse(response):
    """Incremental reader: each yielded block has a local reception timestamp."""
    lines = []
    for line in response.iter_lines():
        if line:
            lines.append(line)
        elif lines:
            yield lines, time.monotonic()
            lines = []


def cursor(block):
    return next((int(line[4:]) for line in block if line.startswith("id: ")), None)
