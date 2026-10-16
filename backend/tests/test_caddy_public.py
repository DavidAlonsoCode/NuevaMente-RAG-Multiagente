"""Local verifier validation with explicit transport/clock doubles, never public acceptance."""

import contextlib
import datetime
import errno
import importlib.util
import io
import json
import socket
import ssl
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("caddy_public", ROOT / "infra/caddy/verify_public.py")
public = importlib.util.module_from_spec(spec)
spec.loader.exec_module(public)
CANARY = "synthetic-Bearer-secret-123456789"
ARGV = [
    "--domain",
    "public.example.org",
    "--target-ip",
    "8.8.8.8",
    "--source-ip",
    "1.1.1.1",
    "--admin-cidr",
    "9.9.9.9/32",
]
GEN = "gen_" + "a" * 32
NATIVE_CONNECT = socket.socket.connect


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    monkeypatch.delenv("SSLKEYLOGFILE", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("native network prohibited in verifier unit tests")

    monkeypatch.setattr(public.socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(public.socket, "create_connection", forbidden)

    def loopback_only(sock, address):
        if address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("only literal loopback is authorized in native tests")
        return NATIVE_CONNECT(sock, address)

    monkeypatch.setattr(socket.socket, "connect", loopback_only)


def args(mode=None):
    return public.inputs(ARGV + (["--execute", mode, "--operator-authorized", "--generation-id", GEN] if mode else []))


def check(report, criterion, name):
    return report["criteria"][criterion]["checks"][name]


def test_default_plan_no_dns_network_or_credential_prompt(monkeypatch, capsys):
    monkeypatch.setattr(public, "read_bearer", lambda: pytest.fail("credential requested offline"))
    assert public.main(ARGV) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["mode"] == "plan" and report["acceptance"] == "not_approved"
    assert report["origin"]["external_nonadmin"] == "operator_declared_not_verified"
    assert "historical_pending" not in report
    assert "ordinary_suite_not_approved" not in json.dumps(report)
    assert "stage3_generation_timeouts_open" not in json.dumps(report)
    assert "stage3_frontend_start_failure_open" not in json.dumps(report)
    assert "ci_status" not in report
    for criterion in report["criteria"].values():
        assert criterion["result"] == "not_executed"
        assert all(not c["executed"] for c in criterion["checks"].values())
        assert criterion["pending"]


def test_unexecuted_optional_scan_is_not_missing_required_evidence():
    report = public.plan(args())
    exposure = report["criteria"]["public_tcp"]
    scan = check(report, "public_tcp", "full_tcp_range")
    assert scan["required"] is False and scan["executed"] is False
    assert "full_tcp_range" not in exposure["pending"]
    ssh = check(report, "public_tcp", "nonadmin_ssh_unreachable")
    assert ssh["required"] is True and ssh["verification"] == "human_host"
    # Report-contract double: these attestations are not real host/public proof.
    for name, item in exposure["checks"].items():
        if item["required"]:
            public.observed(report, "public_tcp", name, "passed")
    public.finish(report)
    assert exposure["pending"] == []
    assert scan["result"] == "not_executed" and scan["observations"] == []
    assert exposure["result"] == "inconclusive"
    assert report["acceptance"] == "not_approved"


@pytest.mark.parametrize("result", ["passed", "failed", "inconclusive"])
def test_optional_scan_retains_observed_result_without_becoming_required(result):
    report = public.plan(args("tcp"))
    exposure = report["criteria"]["public_tcp"]
    for name, item in exposure["checks"].items():
        if item["required"]:
            public.observed(report, "public_tcp", name, "passed")
    public.observed(report, "public_tcp", "full_tcp_range", result, checked=65535)
    public.finish(report)
    scan = check(report, "public_tcp", "full_tcp_range")
    assert scan["required"] is False and scan["executed"] is True
    assert scan["result"] == result and scan["observations"] == [{"result": result, "checked": 65535}]
    assert exposure["pending"] == []
    expected = "failed" if result == "failed" else "inconclusive"
    assert exposure["result"] == report["execution_result"] == expected
    assert report["acceptance"] == "not_approved"


def test_optional_scan_contradiction_survives_later_success():
    report = public.plan(args("tcp"))
    public.observed(report, "public_tcp", "full_tcp_range", "failed", open_ports=[80, 443, 8000], target_index=0)
    public.observed(report, "public_tcp", "full_tcp_range", "passed", open_ports=[80, 443], target_index=1)
    public.finish(report)
    scan = check(report, "public_tcp", "full_tcp_range")
    assert [row["result"] for row in scan["observations"]] == ["failed", "passed"]
    assert scan["result"] == report["criteria"]["public_tcp"]["result"] == report["execution_result"] == "failed"
    assert "full_tcp_range" not in report["criteria"]["public_tcp"]["pending"]
    assert "nonadmin_ssh_unreachable" in report["criteria"]["public_tcp"]["pending"]
    assert report["acceptance"] == "not_approved"


@pytest.mark.parametrize(
    "extra",
    [
        ["--execute", "web"],
        ["--operator-authorized"],
        ["--execute", "sse", "--operator-authorized"],
        ["--bearer", CANARY],
        ["--domain", f"https://{CANARY}@public.example.org/"],
        ["--domain", "localhost"],
        ["--domain", "public.test"],
        ["--domain", "127.0.0.1"],
        ["--domain", "public.example.org?token=" + CANARY],
        ["--source-ip", "9.9.9.9"],
        ["--source-ip", "127.0.0.1"],
        ["--admin-cidr", "9.9.9.10/24"],
        ["--observe-seconds", "0"],
        ["--generation-id", GEN + "?token=" + CANARY],
        ["--unexpected", CANARY],
        ["--target-ip", "127.0.0.1"],
        ["--target-ip", "169.254.169.254"],
        ["--target-ip", "::1"],
        ["--target-ip", "ff02::1"],
    ],
)
def test_invalid_inputs_fail_closed_without_echo(extra, capsys):
    assert public.main(ARGV + extra) == 1
    capture = capsys.readouterr()
    assert CANARY not in capture.out + capture.err
    assert json.loads(capture.out) == {"result": "failed", "reason": "invalid_input", "acceptance": "not_approved"}


def test_ipv6_and_multiple_authorized_addresses():
    parsed = public.inputs(ARGV + ["--target-ip", "2606:4700:4700::1111", "--admin-cidr", "2001:4860::/32"])
    assert len(parsed.target_ip) == 2


def test_dns_mismatch_and_private_rebinding_never_connect(monkeypatch):
    monkeypatch.setattr(public.socket, "getaddrinfo", lambda *a, **k: [(0, 0, 0, "", ("127.0.0.1", 443))])
    with pytest.raises(public.ProbeFailure, match="dns_address_set_mismatch"):
        public.pinned_addresses(args("web"))


def test_strict_system_tls_pinned_connection_and_sni(monkeypatch):
    connection = public.PinnedHTTPS("public.example.org", "8.8.8.8")
    assert connection._context.verify_mode == ssl.CERT_REQUIRED
    assert connection._context.check_hostname
    raw = SimpleNamespace(close=lambda: None)
    calls = []
    monkeypatch.setattr(public.socket, "create_connection", lambda target, timeout: calls.append(target) or raw)
    connection._context = SimpleNamespace(wrap_socket=lambda sock, **kw: calls.append(kw) or sock)
    connection.connect()
    assert calls == [("8.8.8.8", 443), {"server_hostname": "public.example.org"}]


def test_tls_failure_closes_raw_socket(monkeypatch):
    closed = []
    connection = public.PinnedHTTPS("public.example.org", "8.8.8.8")
    monkeypatch.setattr(
        public.socket, "create_connection", lambda *a: SimpleNamespace(close=lambda: closed.append(True))
    )

    def invalid(*a, **k):
        raise ssl.SSLCertVerificationError(CANARY)

    connection._context = SimpleNamespace(wrap_socket=invalid)
    with pytest.raises(ssl.SSLCertVerificationError):
        connection.connect()
    assert closed == [True]


def test_tls_keylogging_rejected_before_context_or_file_creation(monkeypatch, tmp_path):
    target = tmp_path / CANARY
    monkeypatch.setenv("SSLKEYLOGFILE", str(target))
    with pytest.raises(public.ProbeFailure, match="tls_keylogging_environment_rejected"):
        public.PinnedHTTPS("public.example.org", "8.8.8.8")
    assert not target.exists()


class Response(io.BytesIO):
    """HTTP body/header double, not a server or a public TLS session."""

    def __init__(self, body=b"", status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def getheaders(self):
        return list(self.headers.items())

    @contextlib.contextmanager
    def observation_window(self, deadline):
        # Explicit in-memory double: post-read filtering only; native raw IO
        # enforcement is exercised separately by the loopback transport tests.
        try:
            yield
        finally:
            self.close()


def transport(monkeypatch, replies):
    replies = iter(replies)
    calls = []

    @contextlib.contextmanager
    def request(parsed, address, path, **kwargs):
        calls.append((path, kwargs))
        response = next(replies)
        if isinstance(response, Exception):
            raise response
        yield response

    monkeypatch.setattr(public, "request", request)
    return calls


@pytest.mark.parametrize(
    "response,reason",
    [
        (ssl.SSLCertVerificationError(CANARY), "tls_verification_failed"),
        (ssl.SSLError(CANARY), "tls_failed"),
        (TimeoutError(CANARY), "probe_timeout"),
        (OSError(CANARY), "transport_or_protocol_failed"),
        (Response(status=503), None),
        (Response(headers={"X-NuevaMente-Origin-Key": CANARY}), "private_header_exposed"),
    ],
)
def test_web_failures_sanitized_not_approved(monkeypatch, response, reason):
    parsed = args("web")
    report = public.plan(parsed)
    transport(monkeypatch, [response, Response(status=308, headers={"Location": f"https://{parsed.domain}/"})])
    public.web(parsed, report, parsed.target_ip)
    result = public.finish(report)
    observation = check(result, "https_ui", "strict_tls_http")["observations"][0]
    assert observation["result"] == "failed"
    if reason:
        assert observation["reason"] == reason
    assert CANARY not in json.dumps(result)
    assert result["criteria"]["https_ui"]["result"] == "failed"
    assert result["acceptance"] == "not_approved"


@pytest.mark.parametrize(
    "location,status",
    [
        ("https://public.example.org/", 308),
        ("http://public.example.org/", 308),
        ("https://attacker.example.org/", 308),
        ("https://public.example.org/?token=" + CANARY, 308),
        ("https://public.example.org/", 200),
        ("https://public.example.org/", 101),
    ],
)
def test_redirect_no_follow_and_http_success_insufficient(monkeypatch, location, status):
    parsed = args("web")
    report = public.plan(parsed)
    calls = transport(monkeypatch, [Response(), Response(status=status, headers={"Location": location})])
    public.web(parsed, report, parsed.target_ip)
    result = public.finish(report)
    expected = "passed" if status == 308 and location == f"https://{parsed.domain}/" else "failed"
    assert check(result, "https_ui", "http_redirect")["result"] == expected
    assert len(calls) == 2  # Redirect not followed, no body read/rendering claims.
    assert check(result, "https_ui", "external_ui_interaction_ws_reconnect")["result"] == "not_executed"
    assert result["acceptance"] == "not_approved" and CANARY not in json.dumps(result)


def test_bearer_prompt_only_tty_and_no_echo_fallback(monkeypatch):
    monkeypatch.setattr(public.sys.stdin, "isatty", lambda: False)
    with pytest.raises(public.ProbeFailure, match="interactive_credential_required"):
        public.read_bearer()
    monkeypatch.setattr(public.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(public.getpass, "getpass", lambda prompt: CANARY)
    assert public.read_bearer() == CANARY
    monkeypatch.setattr(public.getpass, "getpass", lambda prompt: "bad\n" + CANARY)
    with pytest.raises(public.ProbeFailure, match="invalid_credential"):
        public.read_bearer()
    import warnings

    def fallback(prompt):
        warnings.warn(CANARY, public.getpass.GetPassWarning)

    monkeypatch.setattr(public.getpass, "getpass", fallback)
    with pytest.raises(public.getpass.GetPassWarning):
        public.read_bearer()


def sse_response(ids, *, heartbeat=True):
    body = b"".join(f"id: {i}\nevent: progress\ndata: {CANARY}\n\n".encode() for i in ids)
    return Response(body + (b": heartbeat\n\n" if heartbeat else b""), headers={"Content-Type": "text/event-stream"})


@pytest.fixture
def clock(monkeypatch):
    # Deterministic per-read time double; proves verifier logic only.
    counter = iter(i * 0.5 for i in range(100000))
    monkeypatch.setattr(public.time, "monotonic", lambda: next(counter))


def test_real_sse_logic_redacts_data_preserves_cursor_and_partial_evidence(monkeypatch, clock):
    parsed = args("sse")
    report = public.plan(parsed)
    calls = transport(
        monkeypatch,
        [
            Response(b'{"status":"running"}'),
            sse_response([1, 2]),
            Response(b'{"status":"running"}'),
            sse_response([3, 4]),
        ],
    )
    public.sse(parsed, report, parsed.target_ip, CANARY)
    result = public.finish(report)
    assert check(result, "real_generation_sse", "sse_observation_h1")["result"] == "passed"
    assert check(result, "real_generation_sse", "last_event_id_reconnect")["result"] == "passed"
    assert calls[-1][1]["headers"]["Last-Event-ID"] == "2"
    assert all(c[1]["headers"]["Authorization"] == "Bearer " + CANARY for c in calls)
    assert all(c[0].startswith("/api/generations/gen_") and "?" not in c[0] for c in calls)
    assert CANARY not in json.dumps(result) and GEN not in json.dumps(result)
    assert result["criteria"]["real_generation_sse"]["result"] == "inconclusive"
    assert check(result, "real_generation_sse", "real_gemini_oci_provenance")["result"] == "not_executed"


@pytest.mark.parametrize("state,ids,heartbeat", [("completed", [1, 2], True), ("running", [1], False)])
def test_completed_or_one_event_does_not_prove_real_live_generation(monkeypatch, clock, state, ids, heartbeat):
    parsed = args("sse")
    report = public.plan(parsed)
    transport(
        monkeypatch,
        [
            Response(json.dumps({"status": state}).encode()),
            sse_response(ids, heartbeat=heartbeat),
            Response(json.dumps({"status": state}).encode()),
            sse_response([3]),
        ],
    )
    public.sse(parsed, report, parsed.target_ip, CANARY)
    assert check(report, "real_generation_sse", "sse_observation_h1")["result"] == "inconclusive"
    assert public.finish(report)["acceptance"] == "not_approved"


@pytest.mark.parametrize(
    "response",
    [
        Response(status=401),
        Response(status=302, headers={"Location": "https://evil.example.org/"}),
        Response(headers={"Content-Type": "text/html"}),
        Response(headers={"Content-Type": "text/event-stream", "Content-Encoding": "gzip"}),
        Response(headers={"Content-Type": "text/event-stream", "X-Reflected": CANARY}),
        sse_response([1, 1]),
        sse_response([0]),
        Response(b"event: error\ndata: private\n\n", headers={"Content-Type": "text/event-stream"}),
    ],
)
def test_sse_protocol_http_credential_failures_not_success(monkeypatch, clock, response):
    parsed = args("sse")
    report = public.plan(parsed)
    transport(monkeypatch, [Response(b'{"status":"running"}'), response])
    public.sse(parsed, report, parsed.target_ip, CANARY)
    assert check(report, "real_generation_sse", "sse_observation_h1")["result"] == "failed"
    assert check(report, "real_generation_sse", "last_event_id_reconnect")["result"] == "not_executed"
    assert CANARY not in json.dumps(public.finish(report))


def test_buffered_receipt_and_empty_reconnect_are_inconclusive(monkeypatch):
    monkeypatch.setattr(public.time, "monotonic", lambda: 1.0)
    parsed = args("sse")
    report = public.plan(parsed)
    transport(
        monkeypatch,
        [Response(b'{"status":"running"}'), sse_response([1, 2]), Response(b'{"status":"running"}'), sse_response([])],
    )
    public.sse(parsed, report, parsed.target_ip, CANARY)
    assert check(report, "real_generation_sse", "sse_observation_h1")["result"] == "inconclusive"
    assert check(report, "real_generation_sse", "last_event_id_reconnect")["result"] == "inconclusive"


@pytest.mark.parametrize(
    "unexpected,silent,expected", [(False, False, "passed"), (True, False, "failed"), (False, True, "inconclusive")]
)
def test_full_range_tcp_double_not_public_acceptance(monkeypatch, unexpected, silent, expected):
    parsed = args("tcp")
    report = public.plan(parsed)
    ports = []

    def port(address, number):
        ports.append(number)
        return (
            "open"
            if number in ({80, 443, 8501} if unexpected else {80, 443})
            else "no_response"
            if silent
            else "refused"
        )

    monkeypatch.setattr(public, "tcp_port", port)
    public.tcp(parsed, report, parsed.target_ip)
    result = public.finish(report)
    assert sorted(ports) == list(range(1, 65536))
    scan = check(result, "public_tcp", "full_tcp_range")
    assert scan["result"] == expected and scan["observations"][0]["checked"] == 65535
    assert result["acceptance"] == "not_approved"
    assert check(result, "public_tcp", "admin_ssh_key_only")["result"] == "not_executed"


def test_interrupted_scan_preserves_partial_count_and_no_success(monkeypatch):
    parsed = args("tcp")
    report = public.plan(parsed)

    def port(address, number):
        if number == 300:
            raise OSError(CANARY)
        return "open" if number in {80, 443} else "refused"

    monkeypatch.setattr(public, "tcp_port", port)
    public.tcp(parsed, report, parsed.target_ip)
    scan = check(public.finish(report), "public_tcp", "full_tcp_range")
    assert scan["result"] == "inconclusive" and scan["observations"][0]["checked"] == 299
    assert CANARY not in json.dumps(report)


@pytest.mark.parametrize(
    "error,state",
    [
        (TimeoutError(CANARY), "no_response"),
        (ConnectionRefusedError(errno.ECONNREFUSED, CANARY), "refused"),
        (OSError(CANARY), "error"),
    ],
)
def test_tcp_error_classification_does_not_call_timeout_closed(monkeypatch, error, state):
    def connect(*a, **k):
        raise error

    monkeypatch.setattr(public.socket, "create_connection", connect)
    assert public.tcp_port("8.8.8.8", 22) == state


def test_live_main_double_never_outputs_raw_credential_error(monkeypatch, capsys):
    monkeypatch.setattr(public, "read_bearer", lambda: CANARY)
    monkeypatch.setattr(public, "pinned_addresses", lambda parsed: parsed.target_ip)
    transport(monkeypatch, [OSError(CANARY)])
    assert public.main(ARGV + ["--execute", "sse", "--operator-authorized", "--generation-id", GEN]) == 1
    capture = capsys.readouterr()
    assert CANARY not in capture.out + capture.err and GEN not in capture.out
    assert json.loads(capture.out)["criteria"]["real_generation_sse"]["result"] == "failed"


def test_mixed_address_results_preserve_failure_and_fake_approval_rejected():
    report = public.plan(args())
    public.observed(report, "https_ui", "strict_tls_http", "failed", reason="tls_verification_failed", target_index=0)
    public.observed(report, "https_ui", "strict_tls_http", "passed", http_status=200, target_index=1)
    report["acceptance"] = "approved"  # A caller cannot turn partial checks into approval.
    assert public.finish(report)["acceptance"] == "not_approved"
    assert report["criteria"]["https_ui"]["result"] == "failed"


def test_unknown_result_cannot_be_accepted():
    report = public.plan(args())
    check(report, "https_ui", "strict_tls_http")["result"] = "approved"
    with pytest.raises(public.InvalidInput):
        public.finish(report)


def test_reconnect_repeated_cursor_preserves_failure(monkeypatch, clock):
    parsed = args("sse")
    report = public.plan(parsed)
    transport(
        monkeypatch,
        [
            Response(b'{"status":"running"}'),
            sse_response([1, 2]),
            Response(b'{"status":"running"}'),
            sse_response([2, 3]),
        ],
    )
    public.sse(parsed, report, parsed.target_ip, CANARY)
    result = check(public.finish(report), "real_generation_sse", "last_event_id_reconnect")
    assert result["result"] == "failed"
    assert result["observations"][0]["reason"] == "sse_cursor_not_increasing"


def test_credential_failure_and_dns_error_export_plan_without_secret(monkeypatch, capsys):
    def invalid():
        raise OSError(CANARY)

    monkeypatch.setattr(public, "read_bearer", invalid)
    assert public.main(ARGV + ["--execute", "sse", "--operator-authorized", "--generation-id", GEN]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["execution_error"] == "transport_or_protocol_failed"
    assert report["execution_result"] == "failed"
    assert all(c["result"] == "not_executed" for c in report["criteria"].values())
    assert CANARY not in json.dumps(report)


def test_sse_bound_rejects_oversized_lines(monkeypatch, clock):
    transport(monkeypatch, [Response(b"data: " + b"x" * 9000, headers={"Content-Type": "text/event-stream"})])
    with pytest.raises(public.ProbeFailure, match="sse_size_limit"):
        public.stream(args("sse"), "8.8.8.8", "/api/generations/" + GEN + "/events", CANARY)


def test_main_web_double_exit_is_partial_not_acceptance(monkeypatch, capsys):
    monkeypatch.setattr(public, "pinned_addresses", lambda parsed: parsed.target_ip)
    transport(monkeypatch, [Response(), Response(status=308, headers={"Location": "https://public.example.org/"})])
    assert public.main(ARGV + ["--execute", "web", "--operator-authorized"]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["execution_result"] == "inconclusive" and report["acceptance"] == "not_approved"
    assert report["criteria"]["https_ui"]["pending"]


@pytest.mark.parametrize("state", ["rejected_quality", "failed", "cancelled"])
def test_generation_terminal_failure_not_misclassified_as_invalid_http(monkeypatch, clock, state):
    parsed = args("sse")
    report = public.plan(parsed)
    transport(
        monkeypatch,
        [
            Response(json.dumps({"status": state}).encode()),
            sse_response([1, 2]),
            Response(json.dumps({"status": state}).encode()),
            sse_response([3]),
        ],
    )
    public.sse(parsed, report, parsed.target_ip, CANARY)
    result = check(public.finish(report), "real_generation_sse", "sse_observation_h1")
    assert result["result"] == "failed"
    assert result["observations"][0]["state_before"] == state
    assert "reason" not in result["observations"][0]  # Valid HTTP contract, unsuccessful job.


def test_late_delimiter_does_not_count_heartbeat(monkeypatch):
    # Supervisor reproduction: fake blocking read advances the clock beyond the
    # window. This isolates completion filtering, NOT the native deadline guard.
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(public.time, "monotonic", lambda: clock.now)

    class DelayedResponse(Response):
        def __init__(self):
            super().__init__()
            self.lines = iter(
                [
                    (1, b"id: 1\n"),
                    (2, b"\n"),
                    (3, b"id: 2\n"),
                    (4, b"\n"),
                    (5, b": heartbeat\n"),
                    (121, b"\n"),
                ]
            )

        def readline(self, limit):
            clock.now, line = next(self.lines)
            return line

    response = DelayedResponse()
    result = public.events(response, 20)
    assert result[0] == [{"id": 1, "received_seconds": 2}, {"id": 2, "received_seconds": 4}]
    assert result[1] == 0
    assert result[2]["end"] == "deadline" and result[2]["limit_seconds"] == 20
    assert response.closed


@pytest.mark.parametrize("delimiter_time", [20, 21, 121])
def test_event_completed_at_or_after_deadline_is_excluded(monkeypatch, delimiter_time):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(public.time, "monotonic", lambda: clock.now)

    class DelayedEvent(Response):
        def __init__(self):
            super().__init__()
            self.lines = iter([(2, b"id: 1\n"), (3, b"\n"), (4, b"id: 2\n"), (delimiter_time, b"\n")])

        def readline(self, limit):
            clock.now, line = next(self.lines)
            return line

    response = DelayedEvent()
    samples, heartbeats, window = public.events(response, 20)
    assert samples == [{"id": 1, "received_seconds": 3}]
    assert heartbeats == 0 and window["end"] == "deadline"
    assert response.closed


def test_completion_before_deadline_is_not_rounded_outside_window(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(public.time, "monotonic", lambda: clock.now)

    class NearBoundary(Response):
        def __init__(self):
            super().__init__()
            self.lines = iter([(1, b"id: 1\n"), (19.9999, b"\n"), (20, b"")])

        def readline(self, limit):
            clock.now, line = next(self.lines)
            return line

    samples, _, window = public.events(NearBoundary(), 20)
    assert samples == [{"id": 1, "received_seconds": 19.9999}]
    assert samples[0]["received_seconds"] < window["limit_seconds"]


def test_unterminated_line_at_eof_is_not_a_completed_block(monkeypatch):
    monkeypatch.setattr(public.time, "monotonic", lambda: 1.0)
    response = Response(b"id: 1\n\ndata: private\n: heartbeat\n")
    samples, heartbeats, window = public.events(response, 20)
    assert samples == [{"id": 1, "received_seconds": 0.0}]
    assert heartbeats == 0 and window["end"] == "eof"
    assert response.closed


def test_raw_fragment_budget_decreases_not_inactivity_reset(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(public.time, "monotonic", lambda: clock.now)
    budgets, reads, closed = [], [], []

    def readinto(buffer):
        reads.append(True)
        buffer[0] = 120
        clock.now += 1
        return 1

    raw = SimpleNamespace(readinto=readinto, close=lambda: closed.append(True))
    reader = public.DeadlineSocketReader(raw, SimpleNamespace(settimeout=budgets.append))
    reader.deadline = 20
    try:
        for moment in [0, 5]:
            clock.now = moment
            assert reader.readinto(bytearray(1)) == 1
        clock.now = 19
        with pytest.raises(public.WindowEnded):
            reader.readinto(bytearray(1))  # Byte returned at 20 is excluded.
        with pytest.raises(public.WindowEnded):
            reader.readinto(bytearray(1))  # No further recv once expired.
        assert budgets == [20, 15, 1] and len(reads) == 3
    finally:
        reader.close()
    assert closed == [True]


def test_deadline_partial_evidence_remains_inconclusive(monkeypatch):
    clock = SimpleNamespace(now=0.0)
    monkeypatch.setattr(public.time, "monotonic", lambda: clock.now)

    class SilentResponse(Response):
        def readline(self, limit):
            clock.now += 90
            raise public.WindowEnded()

    parsed = args("sse")
    report = public.plan(parsed)
    response = SilentResponse(headers={"Content-Type": "text/event-stream"})
    transport(monkeypatch, [Response(b'{"status":"running"}'), response, Response(b'{"status":"running"}')])
    public.sse(parsed, report, parsed.target_ip, CANARY)
    result = public.finish(report)
    observation = check(result, "real_generation_sse", "sse_observation_h1")
    assert observation["result"] == "inconclusive"
    assert observation["observations"][0]["window"]["end"] == "deadline"
    assert observation["observations"][0]["receipt"] == []
    assert check(result, "real_generation_sse", "last_event_id_reconnect")["result"] == "inconclusive"
    assert result["acceptance"] == "not_approved" and CANARY not in json.dumps(result)
    assert response.closed


@pytest.mark.parametrize("elapsed,expired", [(0.6, True), (0.2, False)])
def test_native_timeout_uses_elapsed_budget_even_with_coarse_monotonic(monkeypatch, elapsed, expired):
    ticks = iter([100.0, 100.0 + elapsed])
    monkeypatch.setattr(public, "time", SimpleNamespace(monotonic=lambda: 10.0, perf_counter=lambda: next(ticks)))

    def timeout(_buffer):
        raise TimeoutError("synthetic-native-timeout")

    raw = SimpleNamespace(readinto=timeout, close=lambda: None)
    reader = public.DeadlineSocketReader(raw, SimpleNamespace(settimeout=lambda _value: None))
    reader.deadline = 10.5
    expected = public.WindowEnded if expired else TimeoutError
    with pytest.raises(expected) as captured:
        reader.readinto(bytearray(1))
    assert isinstance(captured.value, public.WindowEnded) is expired
    reader.close()


def test_request_uses_deadline_response_and_closes_resources(monkeypatch):
    response = Response()
    closed = []

    class Connection:
        def request(self, *a, **kw):
            pass

        def getresponse(self):
            assert self.response_class is public.DeadlineHTTPResponse
            return response

        def close(self):
            closed.append(True)

    monkeypatch.setattr(public, "PinnedHTTPS", lambda *a: Connection())
    with public.request(args("sse"), "8.8.8.8", "/") as received:
        assert received is response
    assert response.closed and closed == [True]


@pytest.fixture
def local_tls(tmp_path):
    # Isolated self-signed test trust, never used by PinnedHTTPS or public probes.
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "local-cert.pem", tmp_path / "local-key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.load_cert_chain(cert_path, key_path)
    client = ssl.create_default_context(cafile=str(cert_path))
    assert client.check_hostname and client.verify_mode == ssl.CERT_REQUIRED
    return server, client


@pytest.mark.parametrize("tls", [False, True], ids=["tcp-loopback", "tls-local-isolated"])
@pytest.mark.parametrize("mode", ["late_delimiter", "silence", "line_drip", "chunk_header_drip"])
def test_native_loopback_absolute_deadline_closes_reader(local_tls, tls, mode, record_property):
    # Real HTTPResponse/socket/SSL reads, no public address, DNS, providers or
    # production CA. The short window is fixture-only; CLI remains 20–300 s.
    stop, done = threading.Event(), threading.Event()
    failures, fragments = [], []
    window_seconds, peer_hold_seconds = 0.6, 3.0
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(2)
    port = listener.getsockname()[1]
    server_context, client_context = local_tls

    def peer():
        try:
            with listener.accept()[0] as accepted:
                accepted.settimeout(2)
                sock = server_context.wrap_socket(accepted, server_side=True) if tls else accepted
                with sock:
                    chunked = mode == "chunk_header_drip"
                    sock.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                        + (b"Transfer-Encoding: chunked\r\n" if chunked else b"")
                        + b"Connection: close\r\n\r\n"
                    )
                    if mode == "late_delimiter":
                        for delay, block in [(0.04, b"id: 1\n\n"), (0.04, b"id: 2\n\n"), (0.02, b": heartbeat\n")]:
                            if stop.wait(delay):
                                return
                            sock.sendall(block)
                        if not stop.wait(peer_hold_seconds):
                            sock.sendall(b"\n")
                    elif mode == "silence":
                        stop.wait(peer_hold_seconds)
                    else:
                        sock.sendall(b"0" if chunked else b"data: ")
                        until = time.monotonic() + peer_hold_seconds
                        while time.monotonic() < until and not stop.wait(0.05):
                            sock.sendall(b"0" if chunked else b"x")
                            fragments.append(True)  # Count only, no payload retained.
        except (BrokenPipeError, ConnectionResetError):
            pass  # Expected after the client closes at the observation deadline.
        except Exception as error:
            failures.append(type(error).__name__)  # Never exception content/paths.
        finally:
            done.set()

    producer = threading.Thread(target=peer, name="caddy-local-deadline-producer")
    producer.start()
    raw, client, response = None, None, None
    try:
        raw = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        raw.settimeout(2)
        raw.connect(("127.0.0.1", port))
        client = client_context.wrap_socket(raw, server_hostname="localhost") if tls else raw
        response = public.DeadlineHTTPResponse(client)
        response.begin()
        started = time.monotonic()
        samples, heartbeats, window = public.events(response, window_seconds)
        elapsed = time.monotonic() - started
        assert 0.5 <= elapsed < 1.5  # OS scheduling margin, below peer's 3 s hold.
        assert window["end"] == "deadline" and heartbeats == 0
        assert [item["id"] for item in samples] == ([1, 2] if mode == "late_delimiter" else [])
        assert all(item["received_seconds"] < window_seconds for item in samples)
        assert response.closed and response.fp is None and response.deadline_reader.closed
        if mode in {"line_drip", "chunk_header_drip"}:
            assert len(fragments) >= 4  # Active fragments cannot restart the window.
        record_property("scope", "local_loopback_not_public_acceptance")
        record_property("elapsed_seconds", round(elapsed, 3))
        record_property("window_end", window["end"])
        record_property("valid_events", len(samples))
        record_property("valid_heartbeats", heartbeats)
    finally:
        stop.set()
        if response is not None:
            response.close()
        if client is not None:
            client.close()
        if raw is not None:
            raw.close()
        listener.close()
        producer.join(timeout=3)
        assert done.is_set() and not producer.is_alive()
        assert client is None or client.fileno() == -1
        assert not failures
