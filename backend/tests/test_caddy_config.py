"""Stage 1: inspect output from real Caddy/Compose; no server or provider calls.

CADDY_BIN and COMPOSE_BIN select verified temporary binaries if absent from PATH.
Missing tools are explicit skips, never evidence of a successful verification.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
CADDYFILE = ROOT / "infra/caddy/Caddyfile"
UNIT = ROOT / "infra/caddy/caddy.service.d/nuevamente.conf"
SECRET = "0123456789abcdef" * 4  # Synthetic, never a deployment credential.
DOMAIN = "nuevamente.example"


def run(args, env, *, cwd=ROOT):
    return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)


def isolated_env():
    # Do not inherit application credentials or Compose interpolation from a workstation.
    return {k: v for k, v in os.environ.items() if k.upper() in {"PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME"}}


def binary(variable, name):
    result = os.environ.get(variable) or shutil.which(name)
    if not result:
        pytest.skip(f"Not executed: {name} unavailable; set {variable} to a verified official binary")
    return result


@pytest.fixture(scope="module")
def caddy():
    exe = binary("CADDY_BIN", "caddy")
    version = run([exe, "version"], isolated_env())
    assert version.returncode == 0, version.stderr
    assert version.stdout.split()[0] == "v2.11.7", "This contract requires Caddy 2.11.7"
    return exe


@pytest.fixture
def caddy_env():
    # Windows sandbox TEMP may include {GUID}, interpreted as a Caddy placeholder.
    scratch = ROOT / ".data"
    scratch.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="caddy-config-test-", dir=scratch) as directory:
        path = Path(directory)
        yield isolated_env() | {
            "NUEVAMENTE_DOMAIN": DOMAIN,
            "TRUSTED_ORIGIN_SECRET": SECRET,
            "CADDY_LOG_DIR": (path / "logs").as_posix(),
            "CADDY_STORAGE_DIR": (path / "storage").as_posix(),
            "APPDATA": (path / "appdata").as_posix(),
            "XDG_CONFIG_HOME": (path / "config").as_posix(),
            "XDG_DATA_HOME": (path / "data").as_posix(),
        }


@pytest.fixture
def adapted(caddy, caddy_env):
    result = run([caddy, "adapt", "--config", str(CADDYFILE), "--adapter", "caddyfile"], caddy_env)
    assert result.returncode == 0, result.stderr
    assert SECRET not in result.stdout + result.stderr
    return json.loads(result.stdout)


def objects(value):
    """Traverse tool-generated JSON, without interpreting Caddyfile/YAML semantics."""
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)


def server(config, port):
    return next(s for s in config["apps"]["http"]["servers"].values() if s["listen"] == [f":{port}"])


def test_caddy_format_adapt_and_validate(caddy, caddy_env):
    fmt = run([caddy, "fmt", str(CADDYFILE)], caddy_env)
    assert fmt.returncode == 0, fmt.stderr
    assert fmt.stdout == CADDYFILE.read_text(encoding="utf-8")
    diff = run([caddy, "fmt", "--diff", str(CADDYFILE)], caddy_env)
    assert diff.returncode == 0, diff.stderr
    validation = run([caddy, "validate", "--config", str(CADDYFILE), "--adapter", "caddyfile"], caddy_env)
    assert validation.returncode == 0, validation.stderr
    assert "Valid configuration" in validation.stdout + validation.stderr
    assert SECRET not in validation.stdout + validation.stderr


def test_caddy_routes_prefix_redirect_tls(adapted):
    https = server(adapted, 443)
    assert https["routes"][0]["match"] == [{"host": [DOMAIN]}]
    api = next(o for o in objects(https) if o.get("match") == [{"path": ["/api", "/api/*"]}])
    fallback = next(o for o in objects(https) if o.get("group") == api["group"] and "match" not in o)
    assert [o["upstreams"] for o in objects(api) if o.get("handler") == "reverse_proxy"] == [
        [{"dial": "127.0.0.1:8000"}]
    ]
    assert [o["upstreams"] for o in objects(fallback) if o.get("handler") == "reverse_proxy"] == [
        [{"dial": "127.0.0.1:8501"}]
    ]
    assert not any(o.get("handler") in {"rewrite", "encode"} for o in objects(adapted))
    http = server(adapted, 80)
    assert http["routes"][0]["match"] == [{"host": [DOMAIN]}]
    redirects = [o for o in objects(http) if o.get("handler") == "static_response"]
    assert redirects == [
        {
            "handler": "static_response",
            "headers": {"Location": [f"https://{DOMAIN}{{http.request.uri}}"]},
            "status_code": 308,
        }
    ]
    assert not any(o.get("handler") == "reverse_proxy" for o in objects(http))
    assert not https.get("automatic_https", {}).get("disable")
    assert not adapted.get("apps", {}).get("pki")  # No internal/local certificate fallback.
    assert adapted["apps"]["tls"]["automation"]["policies"] == [
        {"subjects": [DOMAIN], "issuers": [{"module": "acme", "ca": "https://acme-v02.api.letsencrypt.org/directory"}]}
    ]


def test_caddy_headers_preserve_functional_contract_and_replace_origin(adapted):
    proxies = [o for o in objects(adapted) if o.get("handler") == "reverse_proxy"]
    assert len(proxies) == 2
    for proxy in proxies:
        request = proxy["headers"]["request"]
        sets = {k.lower(): v for k, v in request["set"].items()}
        assert sets == {
            "host": [DOMAIN],
            "x-forwarded-for": ["{http.request.remote.host}"],
            "x-forwarded-proto": ["https"],
            "x-forwarded-host": [DOMAIN],
            "x-nuevamente-client-ip": ["{http.request.remote.host}"],
            "x-nuevamente-origin-key": ["{env.TRUSTED_ORIGIN_SECRET}"],
        }
        assert {h.lower() for h in request["delete"]} == {"forwarded", "x-real-ip"}
        assert {h.lower() for h in proxy["headers"]["response"]["delete"]} == {
            "x-nuevamente-client-ip",
            "x-nuevamente-origin-key",
        }
        # No changes to Origin/Upgrade/Connection or the API's functional headers.
        assert not set(sets) & {
            "origin",
            "upgrade",
            "authorization",
            "idempotency-key",
            "last-event-id",
            "x-request-id",
        }
        assert not proxy["headers"]["response"].get("set")
    global_headers = next(o for o in objects(server(adapted, 443)) if o.get("handler") == "headers")["response"]
    assert global_headers["deferred"] is True
    assert {h.lower() for h in global_headers["delete"]} == {"x-nuevamente-client-ip", "x-nuevamente-origin-key"}
    assert "Content-Security-Policy" not in global_headers["set"]


def test_caddy_timeouts_protocols_no_buffering_or_compression(adapted):
    for s in adapted["apps"]["http"]["servers"].values():
        assert s["protocols"] == ["h1", "h2"]
        assert s["strict_sni_host"] is True
        assert s["read_header_timeout"] == 15_000_000_000
        assert s["read_idle_timeout"] == s["write_idle_timeout"] == 60_000_000_000
        assert s["idle_timeout"] == 120_000_000_000
        assert not s.get("read_timeout") and not s.get("write_timeout")
        assert "trusted_proxies" not in s and not s.get("logs", {}).get("should_log_credentials")
    for p in (o for o in objects(adapted) if o.get("handler") == "reverse_proxy"):
        assert not set(p) & {"flush_interval", "request_buffers", "response_buffers", "stream_timeout"}
        transport = p["transport"]
        assert transport["compression"] is False
        assert transport["dial_timeout"] == 3_000_000_000
        assert transport["versions"] == ["1.1"]
        timeout = 60_000_000_000 if p["upstreams"][0]["dial"].endswith("8501") else 0
        assert transport.get("response_header_timeout", 0) == timeout
        assert not transport.get("read_timeout") and not transport.get("write_timeout")


def test_caddy_admin_storage_and_sanitized_log_configuration(adapted, caddy_env):
    assert adapted["admin"] == {"listen": "unix//run/caddy/admin.sock|0200", "config": {"persist": False}}
    assert adapted["storage"] == {"module": "file_system", "root": caddy_env["CADDY_STORAGE_DIR"]}
    logs = adapted["logging"]["logs"]
    for name, log in logs.items():
        assert log["writer"]["output"] == "file"
        assert log["writer"]["mode"] == "0600"
        assert log["writer"]["roll_size_mb"] == 10
        assert log["writer"]["roll_keep"] == 5
        assert log["writer"]["roll_keep_days"] == 7
        assert log["encoder"]["format"] == "filter"
        for field in ("request", "resp_headers"):
            assert log["encoder"]["fields"][field] == {"filter": "delete"}
        assert name in {"default", "access"}
    operational = logs["default"]
    assert operational["level"] == "INFO"
    assert operational["encoder"]["wrap"]["message_key"] == ""
    assert operational["encoder"]["wrap"]["stacktrace_key"] == ""
    for field in ("error", "err", "uri", "url", "headers", "body", "config", "stacktrace"):
        assert operational["encoder"]["fields"][field] == {"filter": "delete"}
    append = [o for o in objects(adapted) if o.get("handler") == "log_append"]
    assert len(append) == 4 and all(o.get("early") is True for o in append)
    assert {o["value"] for o in append if o["key"] == "component"} == {"api", "ui"}
    assert any(o["value"] == "{http.request.uuid}" for o in append)


@pytest.mark.parametrize(
    "directive", ["encode imaginary", "reverse_proxy 127.0.0.1:8000 {\n transport http {\n dial_timeout invalid\n }\n}"]
)
def test_native_caddy_rejects_invalid_configuration(caddy, caddy_env, tmp_path, directive):
    invalid = tmp_path / "invalid.Caddyfile"
    invalid.write_text(f"https://{DOMAIN} {{\n{directive}\n}}\n", encoding="utf-8")
    result = run([caddy, "validate", "--config", str(invalid), "--adapter", "caddyfile"], caddy_env)
    assert result.returncode != 0


@pytest.fixture
def composition(tmp_path):
    exe = binary("COMPOSE_BIN", "docker-compose")
    backend = tmp_path / "backend.env"
    origin = tmp_path / "origin.env"
    backend.write_text(
        "GOOGLE_API_KEY=synthetic-gemini\nAPP_ENV=development\nMOCK_OCI=1\nMOCK_GEMINI=1\n", encoding="utf-8"
    )
    origin.write_text(f"TRUSTED_ORIGIN_SECRET={SECRET}\n", encoding="utf-8")
    oci = tmp_path / "oci"
    oci.mkdir()
    deployment = tmp_path / "deployment.env"
    values = {
        "NUEVAMENTE_DOMAIN": DOMAIN,
        "NUEVAMENTE_BACKEND_ENV_FILE": backend.as_posix(),
        "NUEVAMENTE_ORIGIN_ENV_FILE": origin.as_posix(),
        "OCI_CREDENTIALS_DIR": oci.as_posix(),
        "FORWARDED_ALLOW_IPS": "198.51.100.10,2001:db8::10",  # Test-only, NOT an assumed deployment IP.
        "NUEVAMENTE_ENV_FILE": (tmp_path / "development-env-MUST-NOT-BE-READ").as_posix(),
    }
    deployment.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n", encoding="utf-8")
    args = [exe, "--env-file", str(deployment)]
    for file in ("docker-compose.yml", "docker-compose.oci.yml", "docker-compose.production.yml"):
        args += ["-f", str(ROOT / file)]
    return args, isolated_env(), values, deployment


def compose_json(composition, *, resolve=True):
    args, env, _, _ = composition
    # Compose 2.40.3's interpolated model resolves env_file even with --no-env-resolution.
    # Inspect the native merged model before interpolation, then resolve it separately.
    flags = [] if resolve else ["--no-env-resolution", "--no-interpolate", "--no-path-resolution"]
    result = run(args + ["config", "--format", "json"] + flags, env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_effective_compose_env_files_are_replaced_not_merged(composition):
    config = compose_json(composition, resolve=False)
    assert config["services"]["backend"]["env_file"] == [
        {"path": "${NUEVAMENTE_BACKEND_ENV_FILE:?Required private backend.env path}", "required": True},
        {"path": "${NUEVAMENTE_ORIGIN_ENV_FILE:?Required canonical origin.env path}", "required": True},
    ]
    assert not config["services"]["frontend"].get("env_file")
    resolved = compose_json(composition)
    _, _, values, _ = composition
    backend = resolved["services"]["backend"]["environment"]
    frontend = resolved["services"]["frontend"]["environment"]
    assert backend["TRUSTED_ORIGIN_SECRET"] == SECRET
    assert backend["GOOGLE_API_KEY"] == "synthetic-gemini"
    assert backend["APP_ENV"] == "production" and backend["MOCK_OCI"] == backend["MOCK_GEMINI"] == "0"
    assert backend["FORWARDED_ALLOW_IPS"] == values["FORWARDED_ALLOW_IPS"]
    assert not set(frontend) & {"TRUSTED_ORIGIN_SECRET", "GOOGLE_API_KEY", "OCI_CONFIG_FILE", "FORWARDED_ALLOW_IPS"}
    assert frontend["API_URL"] == "http://backend:8000"
    assert frontend["STREAMLIT_BROWSER_SERVER_ADDRESS"] == DOMAIN
    assert frontend["STREAMLIT_BROWSER_SERVER_PORT"] == "443"
    assert frontend["STREAMLIT_SERVER_ENABLE_CORS"] == frontend["STREAMLIT_SERVER_ENABLE_XSRF_PROTECTION"] == "true"


def test_effective_compose_restrictions_network_volumes_and_launch(composition):
    config = compose_json(composition)
    assert config["name"] == "nuevamente"
    for name, port in (("backend", 8000), ("frontend", 8501)):
        service = config["services"][name]
        assert service["ports"] == [
            {"mode": "ingress", "host_ip": "127.0.0.1", "target": port, "published": str(port), "protocol": "tcp"}
        ]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["tmpfs"] == ["/tmp:rw,noexec,nosuid,size=128m,mode=1777"]
        assert service["restart"] == "unless-stopped"
        assert service["healthcheck"]["test"][0] == "CMD"
        assert set(service["networks"]) == {"aplicacion"}
        assert not service.get("privileged") and not service.get("network_mode")
    backend = config["services"]["backend"]
    assert backend["stop_grace_period"] == "1m10s"
    assert {v["target"] for v in backend["volumes"]} == {"/app/.data", "/app/documents", "/run/oci"}
    oci = next(v for v in backend["volumes"] if v["target"] == "/run/oci")
    # compose-go uses omitempty for a false boolean in normalized JSON.
    assert oci["read_only"] and oci["bind"].get("create_host_path", False) is False
    merged = compose_json(composition, resolve=False)
    native_oci = next(v for v in merged["services"]["backend"]["volumes"] if v["target"] == "/run/oci")
    assert native_oci["bind"]["create_host_path"] is False
    assert config["volumes"]["backend_data"]["name"] == "nuevamente_backend_data"
    assert not config["services"]["frontend"].get("volumes")
    assert config["networks"]["aplicacion"]["driver"] == "bridge"
    assert not config["networks"]["aplicacion"].get("internal")
    assert config["networks"]["aplicacion"]["driver_opts"]["com.docker.network.bridge.host_binding_ipv4"] == "127.0.0.1"
    command = backend["command"]
    # Execute the actual guard with only execvp substituted, without importing Uvicorn/providers.
    capture = "import os,json\nos.execvp=lambda executable,args: print(json.dumps(args))\n"
    result = run([sys.executable, "-c", capture + command[2]], isolated_env() | backend["environment"])
    assert result.returncode == 0, result.stderr
    launched = json.loads(result.stdout)
    assert launched[1:] == [
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
        "--workers",
        "1",
        "--proxy-headers",
        "--no-access-log",
    ]


@pytest.mark.parametrize(
    "missing",
    [
        "NUEVAMENTE_DOMAIN",
        "FORWARDED_ALLOW_IPS",
        "NUEVAMENTE_BACKEND_ENV_FILE",
        "NUEVAMENTE_ORIGIN_ENV_FILE",
        "OCI_CREDENTIALS_DIR",
    ],
)
def test_compose_rejects_missing_interpolation(composition, missing):
    args, env, values, deployment = composition
    deployment.write_text("\n".join(f"{k}={v}" for k, v in values.items() if k != missing), encoding="utf-8")
    result = run(args + ["config", "--format", "json"], env)
    assert result.returncode != 0
    assert missing in result.stderr


@pytest.mark.parametrize("file_key", ["NUEVAMENTE_BACKEND_ENV_FILE", "NUEVAMENTE_ORIGIN_ENV_FILE"])
def test_compose_rejects_absent_private_file(composition, file_key):
    args, env, values, _ = composition
    Path(values[file_key]).unlink()
    assert run(args + ["config", "--format", "json"], env).returncode != 0


def test_unmodified_production_template_cannot_be_used(composition):
    args, env, _, _ = composition
    args = args.copy()
    args[2] = str(ROOT / ".env.production.example")
    result = run(args + ["config", "--format", "json"], env)
    assert result.returncode != 0
    assert "NUEVAMENTE_DOMAIN" in result.stderr or "FORWARDED_ALLOW_IPS" in result.stderr


INVALID_DOMAINS = [
    "",
    "https://example.com",
    "example.com/path",
    "example.com:443",
    "*.example.com",
    "127.0.0.1",
    "localhost",
    "app.localhost",
    "app.local",
    "app.internal",
    "app.home.arpa",
    "-bad.example",
    "UPPER.example",
    "bad_.example",
    "example.com.",
    "a" * 64 + ".example",
]


@pytest.mark.parametrize("domain", INVALID_DOMAINS)
def test_backend_startup_rejects_invalid_hostname(composition, domain):
    config = compose_json(composition)["services"]["backend"]
    env = isolated_env() | config["environment"] | {"NUEVAMENTE_DOMAIN": domain}
    result = run([sys.executable, "-c", config["command"][2]], env)
    assert result.returncode != 0
    assert result.stderr.strip() == "Invalid production hostname, origin secret or verified proxy peers"
    assert SECRET not in result.stderr


@pytest.mark.parametrize(
    "key,value",
    [
        ("FORWARDED_ALLOW_IPS", v)
        for v in ("", "*", "0.0.0.0", "::", "224.0.0.1", "172.17.0.0/16", "proxy", "127.0.0.1,", " 127.0.0.1")
    ]
    + [("TRUSTED_ORIGIN_SECRET", v) for v in ("", "placeholder", "a" * 32, "g" * 64, "A" * 64)],
)
def test_backend_startup_rejects_invalid_trust_without_exposing_values(composition, key, value):
    config = compose_json(composition)["services"]["backend"]
    result = run([sys.executable, "-c", config["command"][2]], isolated_env() | config["environment"] | {key: value})
    assert result.returncode != 0
    assert result.stderr.strip() == "Invalid production hostname, origin secret or verified proxy peers"


def unit_guard(kind):
    # Extract the Python argument ONLY; not a substitute for systemd-analyze verify.
    prefix = f'{kind}=/usr/bin/python3 -c "import os,re,sys;'
    line = next(line for line in UNIT.read_text(encoding="utf-8").splitlines() if line.startswith(prefix))
    return line.split(' -c "', 1)[1][:-1]


@pytest.mark.parametrize("kind", ["ExecStartPre", "ExecReload"])
@pytest.mark.parametrize("domain", [DOMAIN, *INVALID_DOMAINS])
def test_systemd_environment_guards_execute(kind, domain):
    env = isolated_env() | {
        "NUEVAMENTE_DOMAIN": domain,
        "TRUSTED_ORIGIN_SECRET": SECRET,
        "CADDY_STORAGE_DIR": "/var/lib/caddy",
        "CADDY_LOG_DIR": "/var/log/caddy",
    }
    result = run([sys.executable, "-c", unit_guard(kind)], env)
    assert (result.returncode == 0) == (domain == DOMAIN)
    assert SECRET not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "key,value",
    [
        ("TRUSTED_ORIGIN_SECRET", "placeholder"),
        ("TRUSTED_ORIGIN_SECRET", ""),
        ("CADDY_STORAGE_DIR", "/tmp"),
        ("CADDY_LOG_DIR", "/tmp"),
    ],
)
@pytest.mark.parametrize("kind", ["ExecStartPre", "ExecReload"])
def test_systemd_environment_guards_reject_bad_secret_or_paths(key, value, kind):
    env = isolated_env() | {
        "NUEVAMENTE_DOMAIN": DOMAIN,
        "TRUSTED_ORIGIN_SECRET": SECRET,
        "CADDY_STORAGE_DIR": "/var/lib/caddy",
        "CADDY_LOG_DIR": "/var/log/caddy",
        key: value,
    }
    result = run([sys.executable, "-c", unit_guard(kind)], env)
    assert result.returncode != 0
    assert result.stderr.strip() == "Invalid Caddy production environment; check hostname, origin secret and paths"
