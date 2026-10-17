"""Stage 2: lifecycle behavior with explicit system doubles, plus native models.

These tests do not establish systemd/ARM64 installation, POSIX permissions,
Docker TCP NAT, provider connectivity, ACME issuance or public acceptance.
"""

import contextlib
import contextvars
import copy
import hashlib
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
import test_caddy_config as native

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("caddy_lifecycle", ROOT / "infra/caddy/verify_deployment.py")
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)
SECRET = native.SECRET
_publication_events = contextvars.ContextVar("fixture_publication_events", default=None)


@pytest.fixture(autouse=True)
def publication_capture(request):
    events = []
    token = _publication_events.set(events)
    try:
        yield events
    finally:
        _publication_events.reset(token)
        destination = os.environ.get("NM_CADDY_PUBLICATION_EVIDENCE_DIR")
        if destination and events:
            # Export after the test, even when recovery suppressed the exception.
            directory = Path(destination)
            directory.mkdir(parents=True, exist_ok=True)
            phase = getattr(request.node, "callspec", SimpleNamespace(params={})).params.get("phase")
            if phase not in {
                "backend-start",
                "peer-verification",
                "frontend-start",
                "reload",
                "candidate-health",
                "candidate-state",
            }:
                phase = None
            report = {
                "test": request.node.originalname,
                "phase": phase,
                "events": events,
                "result": "publication-error-observed"
                if any(e["outcome"] == "error" for e in events)
                else "no-publication-errors-observed",
            }
            (directory / (uuid.uuid4().hex + ".json")).write_text(json.dumps(report), encoding="utf-8")


def portable_atomic(path, data, **kwargs):
    """Filesystem publication double only; never evidence of POSIX permissions."""
    path = Path(path)
    temporary = path.with_suffix(".test-pending")
    events = _publication_events.get()
    destination = {"Caddyfile": "proxy-config", "current.json": "current-state", "pending.json": "pending-state"}.get(
        path.name, "other"
    )
    for stage, operation in (
        ("staging-write", lambda: temporary.write_bytes(data if isinstance(data, bytes) else data.encode())),
        ("atomic-replace", lambda: os.replace(temporary, path)),
    ):
        try:
            operation()
        except Exception as error:
            if events is not None:
                events.append(
                    {
                        "stage": stage,
                        "destination": destination,
                        "outcome": "error",
                        "type": type(error).__name__,
                        "errno": getattr(error, "errno", None),
                        "winerror": getattr(error, "winerror", None),
                    }
                )
            raise
        else:
            if events is not None:
                events.append({"stage": stage, "destination": destination, "outcome": "ok"})


def test_publication_observer_preserves_native_exception_without_retry(tmp_path, monkeypatch, publication_capture):
    target = tmp_path / "synthetic-sensitive-path"
    expected = PermissionError(13, "synthetic-sensitive-content")
    expected.winerror = 5
    calls = []

    def reject(source, destination):
        calls.append(1)
        raise expected

    monkeypatch.setattr(os, "replace", reject)
    with pytest.raises(PermissionError) as caught:
        portable_atomic(target, "synthetic-sensitive-content")
    assert caught.value is expected and calls == [1]
    assert publication_capture[-1] == {
        "stage": "atomic-replace",
        "destination": "other",
        "outcome": "error",
        "type": "PermissionError",
        "errno": 13,
        "winerror": 5,
    }
    assert "synthetic-sensitive" not in json.dumps(publication_capture)


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, "atomic_write", portable_atomic)
    return tmp_path


def environments():
    caddy = {
        "NUEVAMENTE_DOMAIN": native.DOMAIN,
        "CADDY_STORAGE_DIR": "/var/lib/caddy",
        "CADDY_LOG_DIR": "/var/log/caddy",
    }
    deployment = {
        "NUEVAMENTE_DOMAIN": native.DOMAIN,
        "NUEVAMENTE_BACKEND_ENV_FILE": str(lifecycle.BACKEND_ENV),
        "NUEVAMENTE_ORIGIN_ENV_FILE": str(lifecycle.ORIGIN_ENV),
        "OCI_CREDENTIALS_DIR": str(lifecycle.OCI_DIR),
        "FORWARDED_ALLOW_IPS": "192.0.2.10",
    }
    return caddy, deployment, {"TRUSTED_ORIGIN_SECRET": SECRET}


@pytest.mark.parametrize(
    "text",
    [
        "export A=value",
        "A=value\nA=other",
        "B=value",
        "A='quoted'",
        'A="quoted"',
        "A=$HOME",
        "A=space ",
        " A=value",
        "A=a#b",
        "A=a\\b",
        "A=`command`",
        "A=\x00",
    ],
)
def test_literal_env_rejects_ambiguous_or_unknown_values(text):
    with pytest.raises(lifecycle.Failure):
        lifecycle.parse_env(text, {"A"})


def test_literal_env_does_not_evaluate_values():
    assert lifecycle.parse_env("# comment\nA=literal-comma,colon:path\n\n", {"A"}) == {"A": "literal-comma,colon:path"}


@pytest.mark.parametrize(
    "section,key,value",
    [
        (0, "NUEVAMENTE_DOMAIN", "localhost"),
        (0, "NUEVAMENTE_DOMAIN", "https://example.com"),
        (0, "CADDY_STORAGE_DIR", "/tmp"),
        (1, "NUEVAMENTE_DOMAIN", "other.example"),
        (1, "FORWARDED_ALLOW_IPS", "*"),
        (1, "FORWARDED_ALLOW_IPS", "192.0.2.0/24"),
        (1, "FORWARDED_ALLOW_IPS", ""),
        (1, "FORWARDED_ALLOW_IPS", "0.0.0.0"),
        (1, "NUEVAMENTE_ORIGIN_ENV_FILE", "/tmp/other.env"),
        (2, "TRUSTED_ORIGIN_SECRET", "placeholder"),
    ],
)
def test_public_environment_rejects_invalid_contract(section, key, value):
    values = environments()
    values[section][key] = value
    with pytest.raises(lifecycle.Failure):
        lifecycle.public_environment(*values)


def test_initial_observation_does_not_assume_any_ip():
    caddy, deployment, origin = environments()
    deployment["FORWARDED_ALLOW_IPS"] = ""
    assert lifecycle.public_environment(caddy, deployment, origin, observation=True) == caddy | origin
    with pytest.raises(lifecycle.Failure, match="invalid-proxy-peers"):
        lifecycle.public_environment(caddy, deployment, origin)
    deployment["FORWARDED_ALLOW_IPS"] = "192.0.2.10"
    with pytest.raises(lifecycle.Failure, match="requires-empty-peers"):
        lifecycle.public_environment(caddy, deployment, origin, observation=True)


def test_secret_comparison_is_private(capsys):
    lifecycle.same_secret(SECRET, SECRET)
    with pytest.raises(lifecycle.Failure, match="origin-secret-inconsistent"):
        lifecycle.same_secret(SECRET, "f" * 64)
    assert capsys.readouterr() == ("", "")


def evidence():
    return {
        "schema": 1,
        "all_attached_reviewed": True,
        "reviewed_at": 1000,
        "instance_id": "ocid1.instance.synthetic",
        "admin_cidr": "8.8.8.8/32",
        "public_ipv4": "1.1.1.1",
        "security_lists": [
            {
                "ingress-security-rules": [
                    {
                        "is-stateless": False,
                        "protocol": "6",
                        "source-type": "CIDR_BLOCK",
                        "source": "8.8.8.8/32" if port == 22 else "0.0.0.0/0",
                        "tcp-options": {"destination-port-range": {"min": port, "max": port}},
                    }
                    for port in (22, 80, 443)
                ]
                + [
                    {
                        "is-stateless": False,
                        "protocol": "1",
                        "source-type": "CIDR_BLOCK",
                        "source": "0.0.0.0/0",
                        "icmp-options": {"type": 3, "code": 4},
                    }
                ]
            }
        ],
        "nsg_rules": [],
    }


def test_oci_offline_review_is_recent_and_explicit():
    document = evidence()
    assert lifecycle.network_evidence(document, "8.8.8.8/32", 1001) is document
    with pytest.raises(lifecycle.Failure, match="expired"):
        lifecycle.network_evidence(document, "8.8.8.8/32", 4601)


@pytest.mark.parametrize(
    "key,value",
    [
        ("protocol", "all"),
        ("protocol", "17"),
        ("source", "::/0"),
        ("source", "0.0.0.0/0"),
        ("is-stateless", True),
        ("tcp-options", None),
        ("tcp-options", {"destination-port-range": {"min": 80, "max": 443}}),
    ],
)
def test_oci_review_rejects_weakened_policy(key, value):
    document = evidence()
    document["security_lists"][0]["ingress-security-rules"][0][key] = value
    with pytest.raises(lifecycle.Failure):
        lifecycle.network_evidence(document, "8.8.8.8/32", 1001)


def test_default_security_list_and_nsg_rules_are_not_ignored():
    document = evidence()
    document["security_lists"].append({"ingress-security-rules": [{"protocol": "all"}]})
    with pytest.raises(lifecycle.Failure):
        lifecycle.network_evidence(document, "8.8.8.8/32", 1001)
    document = evidence()
    document["nsg_rules"] = [{"direction": "INGRESS", "protocol": "all"}]
    with pytest.raises(lifecycle.Failure):
        lifecycle.network_evidence(document, "8.8.8.8/32", 1001)


FIREWALL = """-A INPUT -j NUEVAMENTE-IN
-A DOCKER-USER -j NUEVAMENTE-FWD
-A NUEVAMENTE-IN -i lo -j ACCEPT
-A NUEVAMENTE-IN -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
-A NUEVAMENTE-IN -p udp -m udp --sport 67 --dport 68 -j ACCEPT
-A NUEVAMENTE-IN -s 8.8.8.8/32 -p tcp -m tcp --dport 22 -j ACCEPT
-A NUEVAMENTE-IN -p tcp -m multiport --dports 80,443 -j ACCEPT
-A NUEVAMENTE-IN -p icmp -m icmp --icmp-type 3/4 -j ACCEPT
-A NUEVAMENTE-IN -j DROP
-A NUEVAMENTE-FWD -m conntrack --ctstate RELATED,ESTABLISHED -j RETURN
-A NUEVAMENTE-FWD -i enp0s6 -j DROP
-A NUEVAMENTE-FWD -j RETURN
"""


def test_issue47_firewall_contract_accepts_canonical_rules():
    lifecycle.firewall_rules(FIREWALL, "8.8.8.8/32", "enp0s6")


@pytest.mark.parametrize(
    "output",
    [
        FIREWALL.replace("80,443", "80,443,8000"),
        FIREWALL.replace("8.8.8.8/32", "0.0.0.0/0"),
        "-A INPUT -j ACCEPT\n" + FIREWALL,
        FIREWALL.replace("-A NUEVAMENTE-IN -j DROP\n", ""),
        FIREWALL.replace("enp0s6", "other-interface"),
    ],
)
def test_issue47_firewall_rejects_bypass_or_changes(output):
    with pytest.raises(lifecycle.Failure):
        lifecycle.firewall_rules(output, "8.8.8.8/32", "enp0s6")


@pytest.mark.parametrize("binding", ["0.0.0.0:8000", "[::]:8501", "0.0.0.0:2019"])
def test_public_app_and_admin_listeners_are_rejected(binding):
    with pytest.raises(lifecycle.Failure):
        lifecycle.listeners(f"tcp LISTEN 0 128 {binding} 0.0.0.0:*")


def test_loopback_bindings_and_public_tls_only():
    lifecycle.listeners(
        "tcp LISTEN 0 128 127.0.0.1:8000 0.0.0.0:*\ntcp LISTEN 0 128 [::1]:8501 [::]:*\ntcp LISTEN 0 128 *:443 *:*"
    )
    with pytest.raises(lifecycle.Failure):
        lifecycle.listeners("udp UNCONN 0 0 *:443 *:*")


def test_cosign_pinned_before_execution_and_transparency_required(tmp_path, monkeypatch):
    verifier = tmp_path / "cosign"
    verifier.write_bytes(b"synthetic-official-verifier")
    monkeypatch.setattr(lifecycle, "COSIGN_SHA256", lifecycle.digest(verifier.read_bytes()))
    monkeypatch.setattr(lifecycle, "secure_path", lambda p, **kw: Path(p))
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=json.dumps({"gitVersion": "v2.6.5"}), returncode=0)

    monkeypatch.setattr(lifecycle, "run", fake_run)
    lifecycle.signature_check(tmp_path / "archive", verifier)
    args, kwargs = calls[-1]
    assert args[1] == "verify-blob"
    assert args[args.index("--certificate-identity") + 1] == lifecycle.IDENTITY
    assert args[args.index("--certificate-oidc-issuer") + 1] == lifecycle.ISSUER
    assert "--insecure-ignore-tlog=false" in args and "--insecure-ignore-sct=false" in args
    assert "--offline" not in args and "--private-infrastructure" not in args
    assert kwargs["env"]["COSIGN_EXPERIMENTAL"] == "1"
    assert kwargs["env"]["TUF_ROOT"] == kwargs["env"]["HOME"]
    verifier.write_bytes(b"tampered")
    calls.clear()
    with pytest.raises(lifecycle.Failure, match="provenance"):
        lifecycle.signature_check(tmp_path / "archive", verifier)
    assert calls == []


def test_certificate_names_match_official_release_layout():
    assert lifecycle.certificate_path(Path(lifecycle.ARCHIVE)).name == "caddy_2.11.7_linux_arm64.pem"
    assert lifecycle.certificate_path(Path("caddy_2.11.7_checksums.txt")).name == "caddy_2.11.7_checksums.txt.pem"


@pytest.fixture
def synthetic_artifacts(tmp_path, monkeypatch):
    archive = tmp_path / lifecycle.ARCHIVE
    with tarfile.open(archive, "w:gz") as tar:
        item = tarfile.TarInfo("caddy")
        item.size = 6
        tar.addfile(item, io.BytesIO(b"binary"))
    data = archive.read_bytes()
    checksums = tmp_path / "caddy_2.11.7_checksums.txt"
    sha512 = hashlib.sha512(data).hexdigest()
    checksums.write_text(f"{sha512}  {lifecycle.ARCHIVE}\n")
    for path in (archive, checksums):
        lifecycle.certificate_path(path).write_text("synthetic-certificate")
        Path(str(path) + ".sig").write_text("synthetic-signature")
    monkeypatch.setattr(lifecycle, "secure_path", lambda p, **kw: Path(p))
    monkeypatch.setattr(lifecycle, "ARCHIVE_SHA256", lifecycle.digest(data))
    monkeypatch.setattr(lifecycle, "ARCHIVE_SHA512", sha512)
    monkeypatch.setattr(lifecycle, "CHECKSUM_SHA256", lifecycle.digest(checksums.read_bytes()))
    return tmp_path, archive


def test_verified_archive_checks_both_signatures_before_extracting(synthetic_artifacts, monkeypatch):
    directory, archive = synthetic_artifacts
    calls = []
    monkeypatch.setattr(lifecycle, "signature_check", lambda p, c: calls.append(p))
    assert lifecycle.verified_binary(directory, directory / "cosign") == b"binary"
    assert calls == [archive, directory / "caddy_2.11.7_checksums.txt"]


def test_bad_hash_never_executes_verifier(synthetic_artifacts, monkeypatch):
    directory, archive = synthetic_artifacts
    archive.write_bytes(b"tampered")
    monkeypatch.setattr(lifecycle, "signature_check", lambda *a: pytest.fail("verifier must not run"))
    with pytest.raises(lifecycle.Failure, match="hash-mismatch"):
        lifecycle.verified_binary(directory, directory / "cosign")


def test_bad_signature_never_returns_binary(synthetic_artifacts, monkeypatch):
    directory, _ = synthetic_artifacts

    def bad_signature(*args):
        raise lifecycle.Failure("signature-invalid")

    monkeypatch.setattr(lifecycle, "signature_check", bad_signature)
    with pytest.raises(lifecycle.Failure, match="signature-invalid"):
        lifecycle.verified_binary(directory, directory / "cosign")


def test_repeated_install_preserves_files_and_does_not_activate(sandbox, monkeypatch, capsys):
    manifest = sandbox / "installation.json"
    manifest.write_text("managed")
    secret, certificates, config = [sandbox / name for name in ("origin.env", "acme.json", "Caddyfile")]
    for path in (secret, certificates, config):
        path.write_text("keep-existing-state")
    monkeypatch.setattr(lifecycle, "MANIFEST", manifest)
    monkeypatch.setattr(lifecycle, "platform_check", lambda: None)
    monkeypatch.setattr(lifecycle, "lock", lambda p: contextlib.nullcontext())
    monkeypatch.setattr(lifecycle, "verified_binary", lambda *a: b"binary")
    source = ROOT / "infra/caddy"
    checksums = {
        str(Path("/usr/bin/caddy")): lifecycle.digest(b"binary"),
        str(lifecycle.BASE_UNIT): lifecycle.digest(lifecycle.UNIT.encode()),
        str(lifecycle.DROPIN): lifecycle.digest((source / "caddy.service.d/nuevamente.conf").read_bytes()),
        str(lifecycle.HELPER): lifecycle.digest((source / "verify_deployment.py").read_bytes()),
    }
    monkeypatch.setattr(lifecycle, "managed_installation", lambda: {"files": checksums})
    monkeypatch.setattr(
        lifecycle, "run", lambda *a, **kw: pytest.fail("repeat must not start/enable/reload any service")
    )
    for _ in range(2):
        lifecycle.install(sandbox, sandbox / "cosign", source)
    assert all(p.read_text() == "keep-existing-state" for p in (secret, certificates, config))
    assert "state-preserved" in capsys.readouterr().out
    checksums[str(Path("/usr/bin/caddy"))] = "foreign"
    with pytest.raises(lifecycle.Failure, match="requires-review"):
        lifecycle.install(sandbox, sandbox / "cosign", source)


def test_foreign_installation_is_not_overwritten(sandbox, monkeypatch):
    monkeypatch.setattr(lifecycle, "MANIFEST", sandbox / "absent.json")
    monkeypatch.setattr(lifecycle, "platform_check", lambda: None)
    monkeypatch.setattr(lifecycle, "lock", lambda p: contextlib.nullcontext())
    monkeypatch.setattr(lifecycle, "verified_binary", lambda *a: b"binary")
    monkeypatch.setattr(lifecycle, "run", lambda *a, **kw: SimpleNamespace(stdout="install ok installed"))
    with pytest.raises(lifecycle.Failure, match="foreign-package"):
        lifecycle.install(sandbox, sandbox / "cosign", ROOT / "infra/caddy")


def test_foreign_manual_binary_is_not_overwritten(sandbox, monkeypatch):
    monkeypatch.setattr(lifecycle, "MANIFEST", sandbox / "absent.json")
    monkeypatch.setattr(lifecycle, "platform_check", lambda: None)
    monkeypatch.setattr(lifecycle, "lock", lambda p: contextlib.nullcontext())
    monkeypatch.setattr(lifecycle, "verified_binary", lambda *a: b"binary")
    calls = []

    def inspect_only(args, **kwargs):
        calls.append(args[0])
        assert args[0] in {"dpkg-query", "getent"}
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(lifecycle, "run", inspect_only)
    monkeypatch.setattr(lifecycle.os.path, "lexists", lambda p: p == Path("/usr/local/bin/caddy"))
    with pytest.raises(lifecycle.Failure, match="foreign-or-partial-installation"):
        lifecycle.install(sandbox, sandbox / "cosign", ROOT / "infra/caddy")
    assert calls == ["dpkg-query", "getent", "getent"]


def test_commands_and_main_never_echo_private_errors(monkeypatch, capsys):
    monkeypatch.setattr(lifecycle.os, "geteuid", lambda: 10001, raising=False)

    def failure(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout=SECRET, stderr=f"token={SECRET}; private body")

    monkeypatch.setattr(lifecycle.subprocess, "run", failure)
    assert lifecycle.main(["caddy-validate"]) == 1
    assert capsys.readouterr().err.strip() == "command-failed:caddy:validate:exit-1"
    assert lifecycle.main(["unknown", SECRET]) == 1
    assert SECRET not in str(capsys.readouterr())


def test_native_compose_model_passes_lifecycle_validation(tmp_path, monkeypatch):
    composition = native.composition.__wrapped__(tmp_path)
    model = native.compose_json(composition)
    _, _, values, _ = composition
    monkeypatch.setattr(lifecycle, "OCI_DIR", Path(values["OCI_CREDENTIALS_DIR"]))
    backend = {"GOOGLE_API_KEY": "synthetic-gemini", "MOCK_OCI": "0", "MOCK_GEMINI": "0"}
    env = {"TRUSTED_ORIGIN_SECRET": SECRET, "NUEVAMENTE_DOMAIN": native.DOMAIN}
    lifecycle.validate_model(model, values, backend, env)
    for change in (
        lambda m: m["services"]["backend"]["ports"][0].update(host_ip="0.0.0.0"),
        lambda m: m["services"]["frontend"]["environment"].update(TRUSTED_ORIGIN_SECRET=SECRET),
        lambda m: m["services"]["backend"].update(read_only=False),
        lambda m: m["networks"]["aplicacion"].update(internal=True),
        lambda m: m["services"]["backend"]["environment"].update(TRUSTED_ORIGIN_SECRET="f" * 64),
    ):
        changed = copy.deepcopy(model)
        change(changed)
        with pytest.raises(lifecycle.Failure):
            lifecycle.validate_model(changed, values, backend, env)
    monkeypatch.setattr(
        lifecycle,
        "json_output",
        lambda *a, **kw: [
            {"Architecture": "arm64", "Os": "linux", "Config": {"User": "10001"}, "Id": "sha256:" + "a" * 64}
        ],
    )
    frozen = lifecycle.freeze_model(model)
    assert frozen["volumes"]["backend_data"] == {"name": lifecycle.VOLUME, "external": True}
    assert frozen["services"]["backend"]["command"][2].startswith(lifecycle.PEER_GATE)
    path = tmp_path / "frozen.json"
    path.write_text(json.dumps(frozen), encoding="utf-8")
    exe = native.binary("COMPOSE_BIN", "docker-compose")
    result = native.run(
        [exe, "--env-file", str(tmp_path / "deployment.env"), "-f", str(path), "config", "--format", "json"],
        native.isolated_env(),
    )
    assert result.returncode == 0, result.stderr
    normalized = json.loads(result.stdout)
    assert normalized["services"]["backend"]["environment"]["TRUSTED_ORIGIN_SECRET"] == SECRET
    assert normalized["services"]["backend"]["command"] == frozen["services"]["backend"]["command"]
    assert not normalized["services"]["frontend"].get("volumes")


def topology():
    container = {
        "Id": "container-1",
        "State": {"StartedAt": "start-1"},
        "NetworkSettings": {
            "SandboxID": "sandbox-1",
            "Networks": {
                "nuevamente_aplicacion": {"NetworkID": "network-1", "IPAddress": "192.0.2.2", "Gateway": "192.0.2.1"}
            },
        },
    }
    networks = [
        {
            "Id": "network-1",
            "Driver": "bridge",
            "Internal": False,
            "EnableIPv6": False,
            "IPAM": {"Config": [{"Subnet": "192.0.2.0/24"}]},
            "Options": {},
        }
    ]
    return container, networks


@pytest.mark.parametrize(
    "change",
    [
        lambda c, n: c.update(Id="container-2"),
        lambda c, n: c["State"].update(StartedAt="start-2"),
        lambda c, n: c["NetworkSettings"].update(SandboxID="sandbox-2"),
        lambda c, n: c["NetworkSettings"]["Networks"]["nuevamente_aplicacion"].update(IPAddress="192.0.2.3"),
        lambda c, n: n[0]["IPAM"].update(Config=[{"Subnet": "198.51.100.0/24"}]),
    ],
)
def test_network_change_invalidates_observation(change):
    container, networks = topology()
    initial = lifecycle.network_fingerprint(container, networks)
    change(container, networks)
    assert lifecycle.network_fingerprint(container, networks) != initial


class DoubleOperations:
    """Deterministic system boundary, no real Docker/systemd/NAT proof."""

    def __init__(self, *, fail=None, always=False):
        self.fail, self.always = fail, always
        self.active = True
        self.calls = []
        self.backend_model = "old"

    def event(self, phase):
        self.calls.append(phase)
        if self.fail == phase:
            if not self.always:
                self.fail = None
            raise lifecycle.Failure("synthetic-system-failure")

    def start_backend(self, model):
        self.event("backend-start")
        self.backend_model = model["version"]
        self.event("peer-verification")
        return "observed-" + self.backend_model

    def start_frontend(self, model):
        self.event("frontend-start")

    def health(self, model, fingerprint):
        assert fingerprint == "observed-" + model["version"]
        self.event("health")
        if model["version"] == "new":
            self.event("candidate-health")

    def activate_caddy(self, was_active):
        self.event("reload" if was_active else "start")
        self.active = True

    def verify_caddy(self, adapted):
        self.event("effective-state")
        if adapted["version"] == "new":
            self.event("candidate-state")

    def caddy_active(self):
        return self.active

    def stop_new(self, model):
        self.calls.append("stop-new")
        self.active = False

    def emergency_stop(self):
        self.calls.append("emergency-stop")
        self.active = False

    def state_report(self):
        print(json.dumps({"caddy_active": self.active, "project_running_containers": 0}))


def candidate(version):
    return {
        "model": {"version": version},
        "caddyfile": "config-" + version,
        "adapted": {"version": version},
        "volume": {"Name": lifecycle.VOLUME, "CreatedAt": "stable"},
        "fingerprint": "observed-" + version,
    }


@pytest.fixture
def existing(sandbox):
    config = sandbox / "Caddyfile"
    config.write_text("config-old")
    (sandbox / "current.json").write_text(json.dumps(candidate("old")))
    data = sandbox / "data.sqlite"
    data.write_bytes(b"persisted-user-data")
    certificates = sandbox / "acme-state"
    certificates.write_bytes(b"persistent-account-and-certificates")
    return sandbox, config, data, certificates


def execute_transaction(existing, operations, **kwargs):
    directory, config, _, _ = existing
    lifecycle.transaction(
        candidate("new"),
        operations,
        directory,
        publish=lambda b: portable_atomic(config, b),
        delete=lambda: config.unlink(missing_ok=True),
        **kwargs,
    )


def test_activation_and_repetition_keep_volume_data_and_acme(existing):
    operations = DoubleOperations()
    execute_transaction(existing, operations)
    execute_transaction(existing, operations)
    directory, config, data, certificates = existing
    assert config.read_text() == "config-new"
    assert json.loads((directory / "current.json").read_text())["model"]["version"] == "new"
    assert not (directory / "pending.json").exists()
    assert (
        data.read_bytes() == b"persisted-user-data"
        and certificates.read_bytes() == b"persistent-account-and-certificates"
    )
    assert operations.calls.index("peer-verification") < operations.calls.index("reload")


@pytest.mark.parametrize(
    "phase", ["backend-start", "peer-verification", "frontend-start", "reload", "candidate-health", "candidate-state"]
)
def test_activation_failure_restores_previous_configuration(existing, phase):
    operations = DoubleOperations(fail=phase)
    with pytest.raises(lifecycle.Failure, match="prior-service-state-restored"):
        execute_transaction(existing, operations)
    directory, config, data, certificates = existing
    assert config.read_text() == "config-old"
    assert json.loads((directory / "current.json").read_text())["model"]["version"] == "old"
    assert not (directory / "pending.json").exists()
    assert operations.active and operations.backend_model == "old"
    assert (
        data.read_bytes() == b"persisted-user-data"
        and certificates.read_bytes() == b"persistent-account-and-certificates"
    )


def test_first_start_failure_removes_new_public_config_and_keeps_data(sandbox):
    config = sandbox / "Caddyfile"
    data = sandbox / "data.sqlite"
    data.write_bytes(b"existing-data")
    operations = DoubleOperations(fail="start")
    operations.active = False
    with pytest.raises(lifecycle.Failure, match="prior-service-state-restored"):
        lifecycle.transaction(
            candidate("new"),
            operations,
            sandbox,
            publish=lambda b: portable_atomic(config, b),
            delete=lambda: config.unlink(missing_ok=True),
        )
    assert not config.exists() and not (sandbox / "current.json").exists()
    assert data.read_bytes() == b"existing-data" and not operations.active


def test_failed_recovery_reports_actual_state_and_retains_journal(existing, capsys):
    operations = DoubleOperations(fail="backend-start", always=True)
    with pytest.raises(lifecycle.Failure, match="deployment-and-recovery-failed"):
        execute_transaction(existing, operations)
    directory, config, data, certificates = existing
    assert (directory / "pending.json").exists() and config.read_text() == "config-old"
    assert not operations.active
    assert json.loads(capsys.readouterr().out)["caddy_active"] is False
    assert (
        data.read_bytes() == b"persisted-user-data"
        and certificates.read_bytes() == b"persistent-account-and-certificates"
    )
    with pytest.raises(lifecycle.Failure, match="requires-recovery"):
        execute_transaction(existing, operations)


def test_volume_replacement_is_rejected_before_any_activation(existing):
    directory, config, _, _ = existing
    changed = candidate("new")
    changed["volume"]["CreatedAt"] = "empty-replacement"
    operations = DoubleOperations()
    with pytest.raises(lifecycle.Failure, match="volume-identity-changed"):
        lifecycle.transaction(
            changed, operations, directory, publish=lambda b: portable_atomic(config, b), delete=lambda: None
        )
    assert operations.calls == [] and config.read_text() == "config-old"


def test_reprobe_suspends_proxy_before_starting_untrusted_backend(existing):
    operations = DoubleOperations()
    execute_transaction(existing, operations, reprobe=True)
    assert operations.calls[0] == "emergency-stop"
    assert operations.calls.index("peer-verification") < operations.calls.index("start")


def test_validation_failure_leaves_running_state_untouched(sandbox, monkeypatch):
    for function in ("platform_check", "host_preflight"):
        monkeypatch.setattr(lifecycle, function, lambda: None)
    monkeypatch.setattr(lifecycle, "lock", lambda p: contextlib.nullcontext())
    monkeypatch.setattr(lifecycle, "secure_path", lambda p, **kw: Path(p))
    monkeypatch.setattr(lifecycle, "STATE", sandbox)
    monkeypatch.setattr(lifecycle, "environment_files", lambda *a, **kw: ({}, {}, {}))
    monkeypatch.setattr(lifecycle, "HostOperations", lambda e: DoubleOperations())
    monkeypatch.setattr(lifecycle, "volume_preflight", lambda: {})
    monkeypatch.setattr(lifecycle, "compose_configuration", lambda *a: {})
    monkeypatch.setattr(lifecycle, "freeze_model", lambda m: m)
    monkeypatch.setattr(lifecycle, "provider_validation", lambda *args: None)
    monkeypatch.setattr(DoubleOperations, "caddy_environment", lambda s: None, raising=False)

    def invalid(*a):
        raise lifecycle.Failure("invalid-caddy-config")

    monkeypatch.setattr(lifecycle, "validate_caddy_candidate", invalid)
    with pytest.raises(lifecycle.Failure, match="invalid-caddy-config"):
        lifecycle.deploy(ROOT, activate=True)
    assert not (sandbox / "pending.json").exists()


def test_concurrent_deployment_is_rejected_before_body(sandbox, monkeypatch):
    # flock/ownership system double: Windows cannot establish Linux flock proof.
    held = {"fd": None}
    real_close = os.close
    real_stat = os.fstat

    def flock(fd, flags):
        if held["fd"] is not None:
            raise BlockingIOError()
        held["fd"] = fd

    def close(fd):
        if held["fd"] == fd:
            held["fd"] = None
        real_close(fd)

    def fstat(fd):
        original = real_stat(fd)
        return SimpleNamespace(st_mode=(original.st_mode & ~0o777) | 0o600, st_uid=0)

    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(flock=flock, LOCK_EX=1, LOCK_NB=2))
    monkeypatch.setattr(lifecycle.os, "O_NOFOLLOW", getattr(os, "O_NOFOLLOW", 0), raising=False)
    monkeypatch.setattr(lifecycle.os, "fstat", fstat)
    monkeypatch.setattr(lifecycle.os, "close", close)
    with lifecycle.lock(sandbox / "deployment.lock"):
        with pytest.raises(lifecycle.Failure, match="already-running"), lifecycle.lock(sandbox / "deployment.lock"):
            pytest.fail("concurrent activation body must not execute")
    with lifecycle.lock(sandbox / "deployment.lock"):
        assert held["fd"] is not None


@pytest.mark.parametrize("mode,uid,gid", [(0o644, 0, 0), (0o600, 10001, 0), (0o600, 0, 10001)])
def test_private_file_bad_permissions_or_owner_rejected(sandbox, monkeypatch, mode, uid, gid):
    # POSIX metadata double; real ownership/ACL verification requires Ubuntu.
    target = sandbox / "secret.env"
    target.write_text("A=synthetic")
    original = Path.stat

    def metadata(path, **kwargs):
        info = original(path, **kwargs)
        return SimpleNamespace(
            st_mode=(info.st_mode & ~0o777) | (mode if path == target else 0o755),
            st_uid=uid if path == target else 0,
            st_gid=gid if path == target else 0,
        )

    monkeypatch.setattr(Path, "stat", metadata)
    monkeypatch.setattr(Path, "is_symlink", lambda p: False)
    with pytest.raises(lifecycle.Failure, match="invalid-(permissions|owner)"):
        lifecycle.secure_path(target)


def test_caddy_uid_probe_restores_privileges_even_on_failure(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda p: SimpleNamespace(pw_uid=123, pw_gid=456)))
    for name, value in (("geteuid", 0), ("getegid", 0), ("getgroups", [0])):
        monkeypatch.setattr(lifecycle.os, name, lambda v=value: v, raising=False)
    for name in ("setgroups", "setegid", "seteuid"):
        monkeypatch.setattr(lifecycle.os, name, lambda value, n=name: calls.append((n, value)), raising=False)

    def failure(peers):
        assert calls[-1] == ("seteuid", 123)
        raise lifecycle.Failure("unverified-peer")

    monkeypatch.setattr(lifecycle, "controlled_peer", failure)
    with pytest.raises(lifecycle.Failure, match="unverified-peer"):
        lifecycle.caddy_peer(["192.0.2.10"])
    assert calls == [
        ("setgroups", []),
        ("setegid", 456),
        ("seteuid", 123),
        ("seteuid", 0),
        ("setegid", 0),
        ("setgroups", [0]),
    ]


@pytest.mark.parametrize(
    "peer,nonce,allowed",
    [
        ("192.0.2.10", "wrong-nonce", ["192.0.2.10"]),
        ("198.51.100.1", "a" * 64, ["192.0.2.10"]),
        ("192.0.2.10", "a" * 64, ["192.0.2.10", "192.0.2.11"]),
        ("0.0.0.0", "a" * 64, None),
    ],
)
def test_controlled_probe_rejects_unverified_peer(monkeypatch, peer, nonce, allowed):
    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def sendall(self, data):
            assert data == ("a" * 64 + "\n").encode()

        def recv(self, size):
            return (json.dumps({"nonce": nonce, "peer": peer}) + "\n").encode()

    monkeypatch.setattr(lifecycle.os, "urandom", lambda n: bytes.fromhex("a" * 64))
    monkeypatch.setattr(lifecycle.socket, "create_connection", lambda *a, **kw: Connection())
    with pytest.raises(lifecycle.Failure, match="peer|unobserved"):
        lifecycle.controlled_peer(allowed)


def test_multiple_projects_cannot_share_data_volume(sandbox, monkeypatch):
    actual = {
        "Name": lifecycle.VOLUME,
        "Driver": "local",
        "Mountpoint": "/var/lib/docker/volumes/data",
        "CreatedAt": "stable",
        "Scope": "local",
    }
    approved = sandbox / "volume.json"
    approved.write_text(json.dumps(actual))
    monkeypatch.setattr(lifecycle, "secure_path", lambda p, **kw: approved)
    container = {
        "Id": "writer",
        "Config": {"Labels": {"com.docker.compose.project": "other-project", "com.docker.compose.service": "backend"}},
        "Mounts": [{"Name": lifecycle.VOLUME, "Destination": "/app/.data"}],
    }
    monkeypatch.setattr(lifecycle, "json_output", lambda args, **kw: [actual] if args[1] == "volume" else [container])
    monkeypatch.setattr(
        lifecycle,
        "run",
        lambda args, **kw: SimpleNamespace(stdout=lifecycle.VOLUME + "\n" if args[1] == "volume" else "writer\n"),
    )
    with pytest.raises(lifecycle.Failure, match="foreign-data-writer"):
        lifecycle.volume_preflight()
    container["Config"]["Labels"]["com.docker.compose.project"] = lifecycle.PROJECT
    assert lifecycle.volume_preflight() == actual


def test_missing_pmtu_rule_or_broad_icmp_is_rejected():
    document = evidence()
    document["security_lists"][0]["ingress-security-rules"][-1]["icmp-options"] = {"type": 3}
    with pytest.raises(lifecycle.Failure, match="icmp-policy"):
        lifecycle.network_evidence(document, "8.8.8.8/32", 1001)
    document["security_lists"][0]["ingress-security-rules"].pop()
    with pytest.raises(lifecycle.Failure, match="pmtu-rule-missing"):
        lifecycle.network_evidence(document, "8.8.8.8/32", 1001)


@pytest.mark.parametrize(
    "owner,mode,approved_peer,success",
    [
        (0, 0o644, "192.0.2.10", True),
        (10001, 0o644, "192.0.2.10", False),
        (0, 0o666, "192.0.2.10", False),
        (0, 0o644, "198.51.100.1", False),
    ],
)
def test_actual_gate_code_requires_root_marker_nonce_and_peer(monkeypatch, owner, mode, approved_peer, success):
    nonce = "a" * 64
    sent = []

    class Connection:
        def settimeout(self, value):
            pass

        def recv(self, size):
            return nonce.encode()

        def sendall(self, data):
            sent.append(json.loads(data))

        def close(self):
            pass

    class Listener:
        def setsockopt(self, *args):
            pass

        def bind(self, address):
            assert address == ("0.0.0.0", 8000)

        def listen(self, count):
            pass

        def settimeout(self, value):
            pass

        def accept(self):
            return Connection(), ("192.0.2.10", 1234)

        def close(self):
            pass

    def absent(path):
        raise FileNotFoundError()

    fake_os = SimpleNamespace(
        unlink=absent,
        path=SimpleNamespace(lexists=lambda p: True),
        lstat=lambda p: SimpleNamespace(st_uid=owner, st_mode=stat.S_IFREG | mode),
        environ={"FORWARDED_ALLOW_IPS": "192.0.2.10", "TRUSTED_ORIGIN_SECRET": SECRET},
    )
    fake_socket = SimpleNamespace(socket=Listener, SOL_SOCKET=1, SO_REUSEADDR=1)
    marker = {"nonce": nonce, "peer": approved_peer}
    namespace = {"open": lambda p: io.StringIO(json.dumps(marker))}
    with monkeypatch.context() as isolated:
        isolated.setitem(sys.modules, "os", fake_os)
        isolated.setitem(sys.modules, "socket", fake_socket)
        if success:
            exec(lifecycle.PEER_GATE + "\nlaunched=True", namespace)
        else:
            with pytest.raises(SystemExit, match="rejected release marker"):
                exec(lifecycle.PEER_GATE + "\nlaunched=True", namespace)
    assert namespace.get("launched", False) is success
    assert sent == [{"nonce": nonce, "peer": "192.0.2.10"}]
    assert SECRET not in json.dumps(sent)


@pytest.mark.parametrize("fault", [None, "workers", "missing-launch", "quotas"])
def test_actual_provider_preflight_code_uses_native_settings_and_crypto(sandbox, monkeypatch, fault):
    # Map only the read-only OCI mount to a synthetic local directory; execute the
    # real image validation payload with this workspace's pinned Python packages.
    # Docker isolation/volume permissions remain explicitly unproved here.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    composition = native.composition.__wrapped__(sandbox)
    model = native.compose_json(composition)
    secret_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_path = sandbox / "synthetic.pem"
    key_path.write_bytes(
        secret_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    public = secret_key.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    md5 = hashlib.md5(public, usedforsecurity=False).hexdigest()
    fingerprint = ":".join(md5[i : i + 2] for i in range(0, 32, 2))
    config_path = sandbox / "config"
    config_path.write_text(
        f"[DOCKER]\nuser=ocid1.user.oc1..synthetic\ntenancy=ocid1.tenancy.oc1..synthetic\nfingerprint={fingerprint}\nkey_file={key_path.as_posix()}\nregion=sa-vinhedo-1\n"
    )
    service = model["services"]["backend"]
    service["image"] = "synthetic-image"
    service["command"][2] = lifecycle.PEER_GATE + service["command"][2]
    values = service["environment"]
    values.update(
        OCI_ALWAYS_FREE_CONFIRMED="1",
        OCI_COMPARTMENT_ID="ocid1.compartment.oc1..synthetic",
        OCI_REGION="sa-vinhedo-1",
        OCI_BUCKET_NAME="synthetic-bucket",
        OCI_CONFIG_FILE=config_path.as_posix(),
    )
    for prefix in ("EMBEDDING", "VISION", "GENERATION", "VERIFICATION"):
        for suffix in ("RPM", "TPM", "RPD"):
            values[prefix + "_QUOTA_" + suffix] = "10"
    if fault == "workers":
        service["command"][2] = service["command"][2].replace("'--workers', '1'", "'--workers', '2'")
    elif fault == "missing-launch":
        service["command"][2] = lifecycle.PEER_GATE + "pass\n"
    elif fault == "quotas":
        values["VISION_QUOTA_RPM"] = "11"
    monkeypatch.setattr(lifecycle, "STATE", sandbox)
    calls = []

    def native_payload(args, **kwargs):
        calls.append(args)
        assert args[args.index("--network") + 1] == "none"
        assert "--read-only" in args and args[args.index("--log-driver") + 1] == "none"
        if "--env-file" in args:
            payload = args[-1].replace("'/run/oci/config'", repr(config_path.as_posix()))
            env = (
                native.isolated_env()
                | values
                | {"PYTHONPATH": str(ROOT / "backend"), "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}
            )
            result = subprocess.run(
                [sys.executable, "-c", payload], cwd=ROOT, env=env, capture_output=True, text=True, timeout=30
            )
            if fault is not None:
                assert result.returncode != 0
                raise lifecycle.Failure("provider-preflight-rejected")
            assert result.returncode == 0, result.stderr
            assert result.stdout == "" and SECRET not in result.stderr
        else:
            assert args[args.index("--mount") + 1] == f"type=bind,src={sandbox.as_posix()},dst=/app/.data,readonly"

    monkeypatch.setattr(lifecycle, "run", native_payload)
    if fault is None:
        lifecycle.provider_validation(model, {"Mountpoint": sandbox.as_posix()})
        assert len(calls) == 2
    else:
        with pytest.raises(lifecycle.Failure, match="preflight-rejected"):
            lifecycle.provider_validation(model, {"Mountpoint": sandbox.as_posix()})
        assert len(calls) == 1


def test_host_route_change_invalidates_fingerprint(monkeypatch):
    container, networks = topology()
    routes = [{"dst": "default", "gateway": "192.0.2.1", "dev": "bridge"}]
    monkeypatch.setattr(lifecycle.HostOperations, "backend", lambda self, **kw: container)
    monkeypatch.setattr(
        lifecycle,
        "json_output",
        lambda args, **kw: networks if args[0] == "docker" else routes if "route" in args else [{"table": "main"}],
    )
    operations = lifecycle.HostOperations({})
    before = operations.fingerprint()
    routes[0]["gateway"] = "192.0.2.254"
    assert operations.fingerprint() != before


def test_caddy_supervisor_discards_stdio_and_forwards_termination(monkeypatch, capsys):
    calls, handlers = [], {}

    class Child:
        def send_signal(self, sig):
            calls.append(sig)

        def wait(self):
            handlers[lifecycle.signal.SIGTERM](lifecycle.signal.SIGTERM, None)
            return 17

    def start(args, **kwargs):
        assert args == ["/usr/bin/caddy", "run", "--config", str(lifecycle.CADDYFILE), "--adapter", "caddyfile"]
        assert kwargs["stdout"] == subprocess.DEVNULL and kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["env"]["TRUSTED_ORIGIN_SECRET"] == SECRET
        assert kwargs["env"]["NOTIFY_SOCKET"] == "synthetic-notify-socket"
        return Child()

    monkeypatch.setenv("TRUSTED_ORIGIN_SECRET", SECRET)
    monkeypatch.setenv("NOTIFY_SOCKET", "synthetic-notify-socket")
    monkeypatch.setattr(lifecycle.subprocess, "Popen", start)
    monkeypatch.setattr(lifecycle.signal, "signal", lambda number, callback: handlers.update({number: callback}))
    assert lifecycle.caddy_run() == 17
    assert calls == [lifecycle.signal.SIGTERM]
    assert capsys.readouterr().out.strip() == "caddy-process-exited; consult-filtered-operational-log"


@pytest.mark.parametrize("phase", ["before-start", "before-release"])
def test_volume_replacement_never_releases_quarantined_writer(sandbox, monkeypatch, phase):
    approved, replacement = {"CreatedAt": "approved"}, {"CreatedAt": "replacement"}
    observations = iter([replacement] if phase == "before-start" else [approved, replacement])
    operations = lifecycle.HostOperations({})
    operations.expected_volume = approved
    calls = []
    monkeypatch.setattr(lifecycle, "volume_preflight", lambda: next(observations))
    monkeypatch.setattr(operations, "compose", lambda *args: calls.append("quarantine"))
    monkeypatch.setattr(operations, "fingerprint", lambda: "stable-network")
    monkeypatch.setattr(lifecycle, "caddy_peer", lambda peers: {"peer": "192.0.2.10", "nonce": "a" * 64})
    monkeypatch.setattr(lifecycle, "run", lambda *args, **kwargs: pytest.fail("must not publish release marker"))
    data = sandbox / "data"
    data.write_bytes(b"existing-state")
    with pytest.raises(lifecycle.Failure, match="data-volume-identity-changed"):
        operations.start_backend({"services": {"backend": {"environment": {"FORWARDED_ALLOW_IPS": "192.0.2.10"}}}})
    assert calls == ([] if phase == "before-start" else ["quarantine"])
    assert data.read_bytes() == b"existing-state"


def install_fixture_path(value, sandbox, path_type=Path):
    path = path_type(value)
    # Check path components, not a textual prefix (install vs install-other).
    # Linux temporary paths are absolute too: mapping must be idempotent.
    if path.is_relative_to(sandbox):
        return path
    return sandbox / "host" / str(value).lstrip("/") if str(value).startswith("/") else path


def test_initial_install_mapping_is_idempotent_with_posix_semantics():
    # Lexical regression only: this is not a native Linux filesystem test.
    sandbox = PurePosixPath("/tmp/pytest-install")

    def mapped(value):
        return install_fixture_path(value, sandbox, PurePosixPath)

    directory = mapped("/root") / "issue48-install-staging"
    assert mapped(directory) == directory
    assert mapped(str(directory)) == directory
    assert mapped(sandbox / "temporary") == sandbox / "temporary"
    assert mapped("/etc/caddy") == sandbox / "host/etc/caddy"
    assert mapped("/tmp/pytest-install-other/file") == sandbox / "host/tmp/pytest-install-other/file"


@pytest.fixture
def initial_install(sandbox, monkeypatch):
    """Run install(), with explicit POSIX umask/ownership and system doubles.

    Files live in a temporary native directory. Their simulated POSIX metadata
    is NOT evidence of native chmod, chown or Caddy access on Ubuntu.
    """
    host = sandbox / "host"
    for directory in ("usr/bin", "usr/local/lib", "etc/systemd/system", "etc", "var/lib", "var/log", "root"):
        (host / directory).mkdir(parents=True, exist_ok=True)

    def mapped(value):
        return install_fixture_path(value, sandbox)

    metadata, created, corrected, calls = {}, {}, [], []
    original_stat, original_mkdir = Path.stat, Path.mkdir

    def simulated_stat(path, **kwargs):
        info = original_stat(path, **kwargs)
        mode, uid, gid = metadata.get(path, (0o755, 0, 0))
        return SimpleNamespace(st_mode=stat.S_IFMT(info.st_mode) | mode, st_uid=uid, st_gid=gid)

    def masked_mkdir(path, mode=0o777, **kwargs):
        original_mkdir(path, mode=mode, **kwargs)
        metadata[path] = (mode & ~0o077, 0, 0)
        created[path] = metadata[path]

    def chmod(path, mode):
        _, uid, gid = metadata.get(path, (0o600, 0, 0))
        metadata[path] = (mode, uid, gid)
        corrected.append(path)

    def chown(path, uid, gid):
        mode, _, _ = metadata[path]
        metadata[path] = (mode, uid, gid)

    def command(args, **kwargs):
        calls.append([str(a) for a in args])
        return SimpleNamespace(stdout="v2.11.7" if args[1] == "version" else "", returncode=0)

    monkeypatch.setattr(lifecycle, "Path", mapped)
    for name in ("STATE", "MANIFEST", "HELPER", "BASE_UNIT", "DROPIN", "CADDYFILE"):
        monkeypatch.setattr(lifecycle, name, mapped(getattr(lifecycle, name).as_posix()))
    monkeypatch.setattr(Path, "stat", simulated_stat)
    monkeypatch.setattr(Path, "mkdir", masked_mkdir)
    monkeypatch.setattr(Path, "chmod", chmod)
    monkeypatch.setattr(lifecycle.os, "chown", chown, raising=False)
    monkeypatch.setattr(lifecycle.os, "listxattr", lambda p: [], raising=False)
    monkeypatch.setattr(lifecycle, "platform_check", lambda: None)
    monkeypatch.setattr(lifecycle, "lock", lambda p: contextlib.nullcontext())
    monkeypatch.setattr(lifecycle, "verified_binary", lambda *a: b"synthetic-binary")
    monkeypatch.setattr(lifecycle, "run", command)
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda p: SimpleNamespace(pw_uid=123, pw_gid=456)))
    temporary = lifecycle.tempfile.TemporaryDirectory

    def temporary_directory(suffix=None, prefix=None, dir=None, **kwargs):
        return temporary(suffix=suffix, prefix=prefix, dir=mapped(dir) if dir is not None else sandbox, **kwargs)

    monkeypatch.setattr(lifecycle.tempfile, "TemporaryDirectory", temporary_directory)
    return SimpleNamespace(
        host=host, mapped=mapped, metadata=metadata, created=created, corrected=corrected, calls=calls
    )


@pytest.mark.parametrize("mapped_parent", [False, True])
def test_initial_install_staging_paths_reach_real_secure_path(initial_install, mapped_parent):
    system = initial_install
    parent = system.host / "root" if mapped_parent else "/root"
    with lifecycle.tempfile.TemporaryDirectory(prefix="staging-", dir=parent) as directory:
        native_path = Path(directory)
        assert native_path.parent == system.host / "root"
        assert lifecycle.Path(directory) == native_path
        assert lifecycle.Path(lifecycle.Path(directory)) == native_path
        system.metadata[native_path] = (0o700, 0, 0)
        assert lifecycle.secure_path(directory, directory=True, mode=0o700) == native_path
        staged = lifecycle.Path(directory) / "caddy"
        staged.write_bytes(b"synthetic-binary")
        system.metadata[staged] = (0o755, 0, 0)
        assert lifecycle.secure_path(str(staged), mode=0o755) == staged


def test_initial_install_sets_and_verifies_final_directory_modes_under_umask077(initial_install):
    system = initial_install
    lifecycle.install(system.host, system.host / "cosign", ROOT / "infra/caddy")
    expected = {
        "var/lib/nuevamente-deploy": (0o700, 0, 0),
        "usr/local/lib/nuevamente": (0o755, 0, 0),
        "etc/systemd/system/caddy.service.d": (0o755, 0, 0),
        "etc/caddy": (0o750, 0, 456),
        "var/lib/caddy": (0o700, 123, 456),
        "var/log/caddy": (0o750, 123, 456),
    }
    for relative, final in expected.items():
        path = system.host / relative
        assert system.created[path][0] == 0o700
        assert system.metadata[path] == final
        assert path in system.corrected
    assert (system.host / "usr/local/lib/nuevamente/verify_deployment.py").is_file()
    assert not any("start" in args or "enable" in args for args in system.calls)


def test_initial_install_does_not_normalize_existing_insecure_directory(initial_install):
    system = initial_install
    directory = system.host / "etc/caddy"
    directory.mkdir()
    system.metadata[directory] = (0o777, 1000, 1000)
    with pytest.raises(lifecycle.Failure, match="invalid-(owner|permissions)"):
        lifecycle.install(system.host, system.host / "cosign", ROOT / "infra/caddy")
    assert system.metadata[directory] == (0o777, 1000, 1000)
    assert directory not in system.corrected


def test_initial_install_verifies_chmod_result(initial_install, monkeypatch):
    system = initial_install
    monkeypatch.setattr(Path, "chmod", lambda p, mode: None)
    with pytest.raises(lifecycle.Failure, match="invalid-permissions"):
        lifecycle.install(system.host, system.host / "cosign", ROOT / "infra/caddy")
    assert not lifecycle.MANIFEST.exists()


def test_initial_install_verifies_chown_result(initial_install, monkeypatch):
    system = initial_install
    monkeypatch.setattr(lifecycle.os, "chown", lambda p, uid, gid: None)
    with pytest.raises(lifecycle.Failure, match="invalid-owner"):
        lifecycle.install(system.host, system.host / "cosign", ROOT / "infra/caddy")
    assert not lifecycle.MANIFEST.exists()


@pytest.fixture
def issue47_marker_metadata(sandbox, monkeypatch):
    """Real bootstrap contract, simulated POSIX metadata; no host modifications."""
    directory = sandbox / "opt/nuevamente"
    directory.mkdir(parents=True)
    marker = directory / "issue47-ready"
    marker.write_text("bootstrap-completed\n")
    metadata = {directory: (stat.S_IFDIR | 0o750, 1000, 1000), marker: (stat.S_IFREG | 0o644, 0, 0)}
    symlinks, acls = set(), {}
    original = Path.stat

    def mapped(value):
        path = Path(value)
        if path.is_relative_to(sandbox):
            return path
        if str(value) == "/opt" or str(value).startswith("/opt/"):
            return sandbox / str(value).lstrip("/")
        return path

    def simulated_stat(path, **kwargs):
        info = original(path, **kwargs)
        mode, uid, gid = metadata.get(path, (stat.S_IFMT(info.st_mode) | 0o755, 0, 0))
        return SimpleNamespace(st_mode=mode, st_uid=uid, st_gid=gid)

    monkeypatch.setattr(lifecycle, "Path", mapped)
    monkeypatch.setattr(Path, "stat", simulated_stat)
    monkeypatch.setattr(Path, "is_symlink", lambda p: p in symlinks)
    monkeypatch.setattr(lifecycle.os, "listxattr", lambda p: acls.get(p, []), raising=False)
    monkeypatch.setitem(
        sys.modules, "pwd", SimpleNamespace(getpwnam=lambda p: SimpleNamespace(pw_uid=1000, pw_gid=1000))
    )
    return SimpleNamespace(directory=directory, marker=marker, metadata=metadata, symlinks=symlinks, acls=acls)


def test_host_preflight_accepts_actual_issue47_marker_contract(issue47_marker_metadata, monkeypatch):
    monkeypatch.setattr(lifecycle, "platform_check", lambda: None)
    monkeypatch.setattr(lifecycle, "managed_installation", lambda: None)
    secure = lifecycle.secure_path
    monkeypatch.setattr(
        lifecycle, "secure_path", lambda p, **kw: secure(p, **kw) if "issue47-ready" in str(p) else None
    )

    class ReachedCloudInit(Exception):
        pass

    def stop_after_marker(args):
        assert args == ["cloud-init", "status", "--format", "json"]
        raise ReachedCloudInit

    monkeypatch.setattr(lifecycle, "json_output", stop_after_marker)
    with pytest.raises(ReachedCloudInit):
        lifecycle.host_preflight()


@pytest.mark.parametrize(
    "target,change",
    [
        ("directory", "owner"),
        ("directory", "group"),
        ("directory", "permissions"),
        ("directory", "type"),
        ("directory", "symlink"),
        ("directory", "acl"),
        ("marker", "owner"),
        ("marker", "group"),
        ("marker", "permissions"),
        ("marker", "type"),
        ("marker", "symlink"),
        ("marker", "acl"),
        ("ancestor", "owner"),
        ("ancestor", "permissions"),
        ("ancestor", "symlink"),
        ("ancestor", "acl"),
    ],
)
def test_issue47_marker_rejects_unsafe_variants(issue47_marker_metadata, target, change):
    system = issue47_marker_metadata
    path = system.directory.parent if target == "ancestor" else getattr(system, target)
    mode, uid, gid = system.metadata.get(path, (stat.S_IFDIR | 0o755, 0, 0))
    if change == "symlink":
        system.symlinks.add(path)
    elif change == "acl":
        system.acls[path] = ["system.posix_acl_access"]
    else:
        system.metadata[path] = {
            "owner": (mode, 999, gid),
            "group": (mode, uid, 999),
            "permissions": ((mode & ~0o777) | 0o777, uid, gid),
            "type": (stat.S_IFLNK | stat.S_IMODE(mode), uid, gid),
        }[change]
    with pytest.raises(lifecycle.Failure):
        lifecycle.issue47_marker()


def test_issue47_marker_exception_does_not_weaken_secure_path(issue47_marker_metadata):
    with pytest.raises(lifecycle.Failure, match="insecure-parent"):
        lifecycle.secure_path(issue47_marker_metadata.marker, mode=0o644)


@pytest.fixture
def health_system(monkeypatch):
    """Docker inspect, HTTP and time doubles; run health/effective_containers logic."""
    model, actual = {"services": {}}, {}
    for service, port in (("backend", "8000"), ("frontend", "8501")):
        env = {"TRUSTED_ORIGIN_SECRET": SECRET} if service == "backend" else {"PUBLIC": "value"}
        expected = {
            "image": "sha256:" + service,
            "command": ["python", "app"],
            "environment": env,
            "healthcheck": {"test": ["CMD", "python", "health"]},
        }
        model["services"][service] = expected
        container, _ = topology()
        container.update(Id=service + "-1", Image=expected["image"])
        container["State"].update(Running=True, Health={"Status": "starting"})
        container["Config"] = {
            "Labels": {"com.docker.compose.project": lifecycle.PROJECT, "com.docker.compose.service": service},
            "Cmd": expected["command"],
            "User": "10001",
            "Env": [f"{k}={v}" for k, v in env.items()],
            "Healthcheck": {"Test": expected["healthcheck"]["test"]},
        }
        container["HostConfig"] = {
            "ReadonlyRootfs": True,
            "CapDrop": ["ALL"],
            "CapAdd": [],
            "Privileged": False,
            "SecurityOpt": ["no-new-privileges:true"],
            "LogConfig": {"Type": "none"},
            "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=128m,mode=1777"},
            "RestartPolicy": {"Name": "unless-stopped"},
            "PortBindings": {port + "/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]},
        }
        container["Mounts"] = (
            []
            if service == "frontend"
            else [
                {"Destination": "/app/.data", "Type": "volume", "Name": lifecycle.VOLUME, "RW": True},
                {"Destination": "/app/documents", "Type": "bind", "RW": False},
                {"Destination": "/run/oci", "Type": "bind", "Source": str(lifecycle.OCI_DIR), "RW": False},
            ]
        )
        actual[service] = container
    system = SimpleNamespace(
        model=model,
        actual=actual,
        now=0,
        sleeps=0,
        transition=None,
        after_http=None,
        http_calls=0,
        inspections=0,
        http_unavailable=False,
        body=b'{"status":"ok"}',
    )
    _, networks = topology()

    def command(args, **kwargs):
        assert args[:3] == ["docker", "ps", "-aq"]
        service = args[-1].split("=")[-1]
        return SimpleNamespace(stdout=actual[service]["Id"] + "\n")

    def inspect(args, **kwargs):
        if args[:2] == ["docker", "inspect"]:
            system.inspections += 1
            return [copy.deepcopy(next(c for c in actual.values() if c["Id"] == args[2]))]
        if args[:3] == ["docker", "network", "inspect"]:
            return copy.deepcopy(networks)
        if args[:2] == ["docker", "exec"]:
            return dict(model["services"]["backend"]["environment"])
        assert args[0] == "ip"
        return [{"route": "stable"}]

    def sleep(seconds):
        system.now += seconds
        system.sleeps += 1
        if system.transition:
            system.transition(system)

    @contextlib.contextmanager
    def http(*args, **kwargs):
        system.http_calls += 1
        if system.http_unavailable:
            raise OSError("synthetic unavailable upstream")
        yield SimpleNamespace(status=200, read=lambda n: system.body)
        if system.after_http:
            system.after_http(system)

    monkeypatch.setattr(lifecycle, "run", command)
    monkeypatch.setattr(lifecycle, "json_output", inspect)
    monkeypatch.setattr(lifecycle, "volume_preflight", lambda **kw: {"Name": lifecycle.VOLUME})
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: system.now)
    monkeypatch.setattr(lifecycle.time, "sleep", sleep)
    monkeypatch.setattr(lifecycle.urllib.request, "urlopen", http)
    system.operations = lifecycle.HostOperations({"TRUSTED_ORIGIN_SECRET": SECRET})
    system.fingerprint = system.operations.fingerprint()
    return system


def test_http_ready_waits_for_docker_starting_to_healthy(health_system):
    system = health_system

    def transition(s):
        for container in s.actual.values():
            container["State"]["Health"]["Status"] = "healthy"

    system.transition = transition
    system.operations.health(system.model, system.fingerprint)
    assert system.sleeps == 1
    assert system.inspections >= 6


def test_http_ready_docker_starting_times_out_within_existing_deadline(health_system):
    system = health_system
    with pytest.raises(lifecycle.Failure, match="health-timeout"):
        system.operations.health(system.model, system.fingerprint)
    assert system.now == 90


@pytest.mark.parametrize(
    "fault,code",
    [
        ("unhealthy", "container-not-healthy"),
        ("stopped", "container-not-healthy"),
        ("binding", "runtime-public-binding-rejected"),
        ("secret", "runtime-container-environment-inconsistent"),
    ],
)
@pytest.mark.parametrize("during_wait", [False, True])
def test_health_does_not_retry_security_or_terminal_failures(health_system, fault, code, during_wait):
    system = health_system

    def invalidate(s):
        container = s.actual["backend"]
        if fault == "unhealthy":
            container["State"]["Health"]["Status"] = "unhealthy"
        elif fault == "stopped":
            container["State"]["Running"] = False
        elif fault == "binding":
            container["HostConfig"]["PortBindings"]["8000/tcp"][0]["HostIp"] = "0.0.0.0"
        else:
            container["Config"]["Env"] = ["TRUSTED_ORIGIN_SECRET=" + "f" * 64]

    if during_wait:
        system.transition = invalidate
    else:
        invalidate(system)
    with pytest.raises(lifecycle.Failure, match=code):
        system.operations.health(system.model, system.fingerprint)
    assert system.sleeps == int(during_wait)


@pytest.mark.parametrize("service", ["backend", "frontend"])
@pytest.mark.parametrize("change", ["identity", "restart", "network"])
def test_health_rejects_container_and_network_changes_while_waiting(health_system, service, change):
    system = health_system

    def transition(s):
        container = s.actual[service]
        if change == "identity":
            container["Id"] += "-replacement"
        elif change == "restart":
            container["State"]["StartedAt"] = "restarted"
        else:
            container["NetworkSettings"]["SandboxID"] = "changed-network"
        for c in s.actual.values():
            c["State"]["Health"]["Status"] = "healthy"

    system.transition = transition
    with pytest.raises(lifecycle.Failure, match="(network-observation|container-observation)-invalidated"):
        system.operations.health(system.model, system.fingerprint)
    assert system.sleeps == 1


def test_health_command_budget_cannot_outlive_deadline(monkeypatch):
    calls = []
    monkeypatch.setattr(lifecycle.time, "monotonic", lambda: 88)

    def subprocess_double(args, **kw):
        calls.append(kw["timeout"])
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(lifecycle.subprocess, "run", subprocess_double)
    lifecycle.run(["docker", "inspect", "synthetic"], deadline=90)
    assert calls == [2]
    with pytest.raises(lifecycle.Failure, match="health-timeout"):
        lifecycle.run(["docker", "inspect", "synthetic"], deadline=88)
    assert calls == [2]


def test_docker_healthy_cannot_pass_without_http_health(health_system):
    system = health_system
    for container in system.actual.values():
        container["State"]["Health"]["Status"] = "healthy"
    system.http_unavailable = True
    with pytest.raises(lifecycle.Failure, match="health-timeout"):
        system.operations.health(system.model, system.fingerprint)
    assert system.now == 90


@pytest.mark.parametrize("body", [b"invalid JSON", b"[]", b'{"status":"invalid"}'])
def test_health_rejects_invalid_http_payload_without_retry(health_system, body):
    system = health_system
    system.body = body
    with pytest.raises(lifecycle.Failure, match="backend-health-invalid"):
        system.operations.health(system.model, system.fingerprint)
    assert system.sleeps == 0


def test_health_preserves_strict_effective_verification_after_http_ready(health_system):
    system = health_system
    for container in system.actual.values():
        container["State"]["Health"]["Status"] = "healthy"

    def invalidate(s):
        s.actual["frontend"]["HostConfig"]["PortBindings"]["8501/tcp"][0]["HostIp"] = "0.0.0.0"

    system.after_http = invalidate
    with pytest.raises(lifecycle.Failure, match="runtime-public-binding-rejected"):
        system.operations.health(system.model, system.fingerprint)
    assert system.sleeps == 0


@pytest.fixture
def backend_recreation(existing, monkeypatch):
    """Real deploy/transaction/gate flow; explicit Docker/systemd/health doubles."""
    directory, config, data, certificates = existing
    prior = candidate("old")
    prior["model"]["services"] = {
        "backend": {"environment": {"TRUSTED_ORIGIN_SECRET": SECRET, "FORWARDED_ALLOW_IPS": "192.0.2.10"}}
    }
    (directory / "current.json").write_text(json.dumps(prior))
    checkout = directory / "checkout"
    (checkout / "infra/caddy").mkdir(parents=True)
    (checkout / "infra/caddy/Caddyfile").write_text(prior["caddyfile"])
    preserved = [data, certificates, directory / "origin.env", directory / "ledger", directory / "obligations"]
    for path in preserved[2:]:
        path.write_bytes(b"synthetic-durable-state")
    system = SimpleNamespace(
        directory=directory,
        checkout=checkout,
        config=config,
        prior=prior,
        model=copy.deepcopy(prior["model"]),
        volume=copy.deepcopy(prior["volume"]),
        containers={"backend": [], "frontend": [{"Id": "frontend-1"}]},
        fail=None,
        always=False,
        events=[],
        preserved={p: p.read_bytes() for p in preserved},
        installed=True,
    )
    _, networks = topology()

    def container(service):
        value, _ = topology()
        value["Id"] = service + "-1"
        value["Config"] = {
            "Labels": {"com.docker.compose.project": lifecycle.PROJECT, "com.docker.compose.service": service}
        }
        return value

    system.container = container
    system.containers["frontend"] = [container("frontend")]

    def event(phase):
        system.events.append(phase)
        if system.fail == phase:
            if not system.always:
                system.fail = None
            raise lifecycle.Failure("synthetic-recreation-failure")

    class Operations(DoubleOperations):
        backend = lifecycle.HostOperations.backend
        service_container = lifecycle.HostOperations.service_container
        fingerprint = lifecycle.HostOperations.fingerprint
        start_backend = lifecycle.HostOperations.start_backend

        def __init__(self):
            super().__init__()
            self.env = {"TRUSTED_ORIGIN_SECRET": SECRET}

        def caddy_environment(self):
            pass

        def compose(self, model, *args):
            assert not self.active and (directory / "pending.json").exists()
            event("quarantine")
            system.containers["backend"] = [container("backend")]

        def start_frontend(self, model):
            assert not self.active
            event("frontend-start")
            system.containers["frontend"] = [container("frontend")]

        def health(self, model, fingerprint):
            assert self.fingerprint() == fingerprint
            assert system.containers["backend"] and system.containers["frontend"]
            event("health")

        def emergency_stop(self):
            assert (directory / "pending.json").exists()
            event("close")
            self.active = False

        def activate_caddy(self, was_active):
            assert not self.active and "health" in system.events
            event("activate")
            self.active = True

        def verify_caddy(self, adapted):
            assert self.active and adapted == prior["adapted"]
            event("effective-state")

    operations = Operations()
    system.operations = operations

    def command(args, **kwargs):
        if args[:3] == ["docker", "ps", "-aq"]:
            event("listing")
            service = args[-1].rsplit("=", 1)[-1]
            return SimpleNamespace(stdout="\n".join(c["Id"] for c in system.containers[service]))
        assert args[:4] == ["docker", "exec", "--user", "0"]
        assert not operations.active and (directory / "pending.json").exists()
        assert json.loads(kwargs["input"]) == {"peer": "192.0.2.10", "nonce": "a" * 64}
        event("release")

    def inspect(args, **kwargs):
        if args[:2] == ["docker", "inspect"]:
            event("inspect")
            return [copy.deepcopy(c) for values in system.containers.values() for c in values if c["Id"] == args[2]]
        if args[:3] == ["docker", "network", "inspect"]:
            return copy.deepcopy(networks)
        assert args[0] == "ip"
        return [{"route": "synthetic-stable"}]

    def peer(allowed):
        assert allowed == ["192.0.2.10"] and not operations.active
        event("peer")
        return {"peer": allowed[0], "nonce": "a" * 64}

    def publish(path, content, **kwargs):
        if path.name == "pending.json":
            event("journal")
        elif path == config:
            assert not operations.active
            event("publish")
        elif path.name == "current.json":
            assert operations.active and system.events[-1] == "effective-state"
            event("current")
        portable_atomic(path, content)

    monkeypatch.setattr(lifecycle, "platform_check", lambda: None)
    monkeypatch.setattr(lifecycle, "host_preflight", lambda: event("host-preflight"))
    monkeypatch.setattr(lifecycle, "lock", lambda p: contextlib.nullcontext())
    monkeypatch.setattr(lifecycle, "secure_path", lambda p, **kw: Path(p))
    monkeypatch.setattr(lifecycle, "STATE", directory)
    monkeypatch.setattr(lifecycle, "CADDYFILE", config)
    monkeypatch.setattr(lifecycle, "environment_files", lambda *a, **kw: (operations.env, {}, {}))
    monkeypatch.setattr(lifecycle, "HostOperations", lambda env: operations)
    monkeypatch.setattr(lifecycle, "volume_preflight", lambda **kw: copy.deepcopy(system.volume))
    monkeypatch.setattr(lifecycle, "compose_configuration", lambda *a: copy.deepcopy(system.model))
    monkeypatch.setattr(lifecycle, "freeze_model", lambda model: (event("images"), model)[1])
    monkeypatch.setattr(lifecycle, "provider_validation", lambda *a: event("provider-config"))
    monkeypatch.setattr(lifecycle, "validate_caddy_candidate", lambda *a: prior["adapted"])
    monkeypatch.setattr(lifecycle, "run", command)
    monkeypatch.setattr(lifecycle, "json_output", inspect)
    monkeypatch.setattr(lifecycle, "caddy_peer", peer)
    monkeypatch.setattr(lifecycle, "atomic_write", publish)
    monkeypatch.setattr(
        lifecycle,
        "managed_installation",
        lambda: {
            "files": {
                str(lifecycle.HELPER): lifecycle.digest(Path(lifecycle.__file__).read_bytes())
                if system.installed
                else "old-helper"
            }
        },
    )
    monkeypatch.setitem(sys.modules, "pwd", SimpleNamespace(getpwnam=lambda p: SimpleNamespace(pw_gid=123)))
    transaction = lifecycle.transaction
    monkeypatch.setattr(lifecycle, "transaction", lambda *a, **kw: transaction(*a, state_dir=directory, **kw))
    return system


@pytest.mark.parametrize("frontend_present", [True, False])
def test_absent_backend_activation_recreates_only_after_journal_and_closure(backend_recreation, frontend_present):
    system = backend_recreation
    if not frontend_present:
        system.containers["frontend"] = []
    lifecycle.deploy(system.checkout, activate=True)
    ordered = [
        "journal",
        "close",
        "quarantine",
        "peer",
        "release",
        "frontend-start",
        "health",
        "publish",
        "activate",
        "effective-state",
        "current",
    ]
    assert [system.events.index(phase) for phase in ordered] == sorted(system.events.index(phase) for phase in ordered)
    assert all(path.read_bytes() == data for path, data in system.preserved.items())
    assert not (system.directory / "pending.json").exists()
    current = json.loads((system.directory / "current.json").read_text())
    assert current["model"] == system.prior["model"] and current["volume"] == system.prior["volume"]
    assert current["fingerprint"] == system.operations.fingerprint()


def test_absent_backend_validate_never_recreates_or_writes_journal(backend_recreation):
    system = backend_recreation
    with pytest.raises(lifecycle.Failure, match="absent; explicit-activation-required"):
        lifecycle.deploy(system.checkout)
    assert "journal" not in system.events and "close" not in system.events
    assert not system.containers["backend"] and system.operations.active


def test_unique_backend_keeps_read_only_validation(backend_recreation):
    system = backend_recreation
    system.containers["backend"] = [system.container("backend")]
    system.prior["fingerprint"] = system.operations.fingerprint()
    (system.directory / "current.json").write_text(json.dumps(system.prior))
    lifecycle.deploy(system.checkout)
    assert "health" in system.events and "effective-state" in system.events
    assert "journal" not in system.events and "quarantine" not in system.events


@pytest.mark.parametrize("service", ["backend", "frontend"])
@pytest.mark.parametrize("kind", ["multiple", "foreign", "inspection-failure"])
def test_recreation_rejects_ambiguous_foreign_and_failed_inspections(backend_recreation, monkeypatch, service, kind):
    system = backend_recreation
    system.containers[service] = [system.container(service)]
    if kind == "multiple":
        system.containers[service] *= 2
    elif kind == "foreign":
        system.containers[service][0]["Config"]["Labels"]["com.docker.compose.project"] = "foreign"
    else:
        inspect = lifecycle.json_output

        def reject(args, **kw):
            if args[:2] == ["docker", "inspect"]:
                raise lifecycle.Failure("synthetic-inspection-failed")
            return inspect(args, **kw)

        monkeypatch.setattr(lifecycle, "json_output", reject)
    with pytest.raises(lifecycle.Failure):
        lifecycle.deploy(system.checkout, activate=True)
    assert "journal" not in system.events and "quarantine" not in system.events


@pytest.mark.parametrize(
    "fault", ["host-preflight", "images", "provider-config", "listing", "helper", "model", "volume"]
)
def test_recreation_preflight_failures_do_not_mutate(backend_recreation, fault):
    system = backend_recreation
    if fault in {"host-preflight", "images", "provider-config", "listing"}:
        system.fail = fault
    elif fault == "helper":
        system.installed = False
    elif fault == "model":
        system.model["version"] = "new"
    else:
        system.volume["CreatedAt"] = "replacement"
    with pytest.raises(lifecycle.Failure):
        lifecycle.deploy(system.checkout, activate=True)
    assert "journal" not in system.events and "close" not in system.events
    assert all(path.read_bytes() == data for path, data in system.preserved.items())


@pytest.mark.parametrize(
    "phase",
    ["quarantine", "peer", "release", "frontend-start", "health", "publish", "activate", "effective-state", "current"],
)
@pytest.mark.parametrize("recovery_fails", [False, True])
def test_recreation_failure_restores_or_retains_authentic_journal(backend_recreation, phase, recovery_fails):
    system = backend_recreation
    previous = (system.directory / "current.json").read_bytes()
    system.fail, system.always = phase, recovery_fails
    match = "pending-journal-retained" if recovery_fails else "prior-service-state-restored"
    with pytest.raises(lifecycle.Failure, match=match):
        lifecycle.deploy(system.checkout, activate=True)
    assert all(path.read_bytes() == data for path, data in system.preserved.items())
    assert system.events.count("close") >= 2
    pending = system.directory / "pending.json"
    assert pending.exists() is recovery_fails
    if recovery_fails:
        assert not system.operations.active
        assert (system.directory / "current.json").read_bytes() == previous
        journal = json.loads(pending.read_text())
        assert journal["previous"] == system.prior
        # The real deploy transaction produced this journal; --recover reuses it.
        system.fail = None
        lifecycle.deploy(system.checkout, recover=True)
        assert not pending.exists() and system.operations.active
    else:
        assert system.operations.active


def test_recreation_journal_write_failure_never_closes_or_recreates(backend_recreation):
    system = backend_recreation
    system.fail = "journal"
    with pytest.raises(lifecycle.Failure, match="synthetic-recreation-failure"):
        lifecycle.deploy(system.checkout, activate=True)
    assert "close" not in system.events and "quarantine" not in system.events


def test_recover_does_not_invent_journal_for_absent_backend(backend_recreation):
    system = backend_recreation
    with pytest.raises(FileNotFoundError):
        lifecycle.deploy(system.checkout, recover=True)
    assert "journal" not in system.events and "close" not in system.events


@pytest.mark.parametrize("kind", ["empty", "multiple-inspections", "identity", "config", "labels"])
def test_backend_inspection_must_identify_one_matching_container(backend_recreation, monkeypatch, kind):
    system = backend_recreation
    system.containers["backend"] = [system.container("backend")]
    value = system.container("backend")
    if kind == "identity":
        value["Id"] = "foreign-id"
    elif kind == "config":
        value["Config"] = None
    elif kind == "labels":
        value["Config"]["Labels"] = []
    result = [] if kind == "empty" else [value, value] if kind == "multiple-inspections" else [value]
    monkeypatch.setattr(lifecycle, "json_output", lambda *a, **kw: result)
    with pytest.raises(lifecycle.Failure, match="container-inspection"):
        lifecycle.deploy(system.checkout, activate=True)
    assert "journal" not in system.events and "close" not in system.events


@pytest.mark.parametrize("phase", ["before-start", "before-release"])
def test_absent_backend_volume_replacement_never_releases_writer(backend_recreation, monkeypatch, phase):
    system = backend_recreation
    calls = 0

    def volume(**kwargs):
        nonlocal calls
        calls += 1
        value = copy.deepcopy(system.volume)
        if calls >= (2 if phase == "before-start" else 3):
            value["CreatedAt"] = "replaced-after-preflight"
        return value

    monkeypatch.setattr(lifecycle, "volume_preflight", volume)
    with pytest.raises(lifecycle.Failure, match="pending-journal-retained"):
        lifecycle.deploy(system.checkout, activate=True)
    assert "release" not in system.events and "publish" in system.events  # recovery restores config only
    assert not system.operations.active and (system.directory / "pending.json").exists()
    assert all(path.read_bytes() == data for path, data in system.preserved.items())
