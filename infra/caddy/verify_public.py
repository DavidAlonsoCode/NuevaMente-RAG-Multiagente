"""Issue 48 public evidence collector, NOT a deployment or acceptance tool.

Default: validate and print a plan, without DNS or network IO. Live probes require
both --execute and --operator-authorized. Bearer is read only by an interactive,
non-echoing prompt. No provider POSTs, redirects, response bodies or raw errors
are emitted. Human and host evidence must be reviewed separately (see runbook).
"""

import argparse
import concurrent.futures
import contextlib
import datetime
import errno
import getpass
import http.client
import io
import ipaddress
import json
import os
import platform
import re
import socket
import ssl
import sys
import time

CHECKS = {
    "https_ui": {
        "strict_tls_http": "automatic",
        "http_redirect": "automatic",
        "public_chain_dns": "human_host",
        "external_ui_interaction_ws_reconnect": "human",
        "private_origin_not_exposed": "human_host",
    },
    "real_generation_sse": {
        "sse_observation_h1": "automatic",
        "last_event_id_reconnect": "automatic",
        "live_before_completion_h1_h2": "human",
        "real_gemini_oci_provenance": "host",
        "ownership_idempotency_no_duplicate": "human_host",
    },
    "public_tcp": {
        "full_tcp_range": "automatic",
        "nonadmin_ssh_unreachable": "automatic",
        "external_nonadmin_origin_all_addresses": "human",
        "host_firewall_loopback_nsg": "host",
        "admin_ssh_key_only": "human_host",
    },
}
RESULTS = {"not_executed", "passed", "failed", "inconclusive"}
PRIVATE_HEADERS = {"x-nuevamente-origin-key", "x-nuevamente-client-ip"}
OPEN_PENDING = [
    "stage3_generation_timeouts_open",
    "stage3_frontend_start_failure_open",
    "ordinary_suite_not_approved",
    "native_linux_arm64_systemd_docker_nat_pending",
]


class InvalidInput(ValueError):
    def __init__(self):
        super().__init__("invalid_input")


class ProbeFailure(Exception):
    def __init__(self, code):
        # Codes are constants supplied internally; never include exception text.
        super().__init__(code)
        self.code = code


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        # argparse's usual message can echo an accidentally supplied credential.
        raise InvalidInput()


def inputs(argv):
    parser = SafeParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--domain", required=True, help="Explicit ASCII public hostname; no URL")
    parser.add_argument("--target-ip", action="append", required=True, help="Authorized public A/AAAA; repeat")
    parser.add_argument("--source-ip", required=True, help="Declared external public egress IP")
    parser.add_argument("--admin-cidr", action="append", required=True, help="All administrative CIDRs; repeat")
    parser.add_argument("--execute", choices=("web", "sse", "tcp"))
    parser.add_argument("--operator-authorized", action="store_true")
    parser.add_argument("--generation-id", help="Own gen_ ID; needed only for SSE")
    parser.add_argument("--observe-seconds", type=int, default=90)
    args = parser.parse_args(argv)
    domain = args.domain.lower()
    labels = domain.split(".")
    if (
        len(domain) > 253
        or len(labels) < 2
        or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
        or not re.fullmatch(r"[a-z]{2,63}|xn--[a-z0-9-]+", labels[-1])
        or labels[-1] in {"local", "localhost", "internal", "test", "invalid", "example"}
    ):
        raise InvalidInput()
    try:
        targets = list(dict.fromkeys(ipaddress.ip_address(value) for value in args.target_ip))
        source = ipaddress.ip_address(args.source_ip)
        admins = [ipaddress.ip_network(value, strict=True) for value in args.admin_cidr]
    except ValueError:
        raise InvalidInput() from None
    if (
        not all(value.is_global and not value.is_multicast for value in [*targets, source])
        or any(source in network for network in admins)
        or not 20 <= args.observe_seconds <= 300
        or bool(args.execute) != args.operator_authorized
        or (args.execute == "sse" and not args.generation_id)
        or (args.generation_id and not re.fullmatch(r"gen_[a-f0-9]{32}", args.generation_id))
    ):
        raise InvalidInput()
    args.domain = domain
    args.target_ip = [str(value) for value in targets]
    return args


def plan(args):
    return {
        "schema": 1,
        "recorded_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "mode": args.execute or "plan",
        "execution_result": "not_executed",
        "acceptance": "not_approved",
        "platform": {"os": platform.system(), "arch": platform.machine(), "python": platform.python_version()},
        "origin": {"external_nonadmin": "operator_declared_not_verified"},
        "target": {"domain": args.domain, "addresses": args.target_ip},
        "criteria": {
            criterion: {
                "result": "not_executed",
                "checks": {
                    name: {
                        "implementation": "collector" if kind == "automatic" else "runbook",
                        "verification": kind,
                        "result": "not_executed",
                        "executed": False,
                        "observations": [],
                    }
                    for name, kind in checks.items()
                },
                "pending": list(checks),
            }
            for criterion, checks in CHECKS.items()
        },
        "historical_pending": OPEN_PENDING.copy(),
    }


def finish(report):
    """Partial successes never substitute for missing human/host evidence."""
    for criterion in report["criteria"].values():
        checks = criterion["checks"]
        states = [check["result"] for check in checks.values()]
        if any(state not in RESULTS for state in states):
            raise InvalidInput()
        criterion["pending"] = [name for name, check in checks.items() if check["result"] != "passed"]
        criterion["result"] = (
            "failed"
            if "failed" in states
            else "not_executed"
            if all(state == "not_executed" for state in states)
            else "inconclusive"
        )
    # This collector has no interface for accepting unverified attestations.
    report["acceptance"] = "not_approved"
    if report["mode"] != "plan":
        report["execution_result"] = (
            "failed"
            if report.get("execution_error") or any(c["result"] == "failed" for c in report["criteria"].values())
            else "inconclusive"
        )
    return report


def observed(report, criterion, name, result, **metrics):
    check = report["criteria"][criterion]["checks"][name]
    check["executed"] = True
    check["observations"].append({"result": result, **metrics})
    states = [entry["result"] for entry in check["observations"]]
    check["result"] = "failed" if "failed" in states else "inconclusive" if "inconclusive" in states else "passed"


def pinned_addresses(args):
    # Resolve only in explicit live mode; pin each connect to the checked IP.
    resolved = {entry[4][0] for entry in socket.getaddrinfo(args.domain, 443, type=socket.SOCK_STREAM)}
    if resolved != set(args.target_ip):
        raise ProbeFailure("dns_address_set_mismatch")
    return args.target_ip


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, domain, address):
        # create_default_context honors SSLKEYLOGFILE: reject before it can open
        # a key log, rather than disabling it after secret-bearing TLS starts.
        if os.environ.get("SSLKEYLOGFILE"):
            raise ProbeFailure("tls_keylogging_environment_rejected")
        super().__init__(domain, timeout=20, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        raw = socket.create_connection((self.address, 443), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except BaseException:
            raw.close()
            raise


class WindowEnded(TimeoutError):
    def __init__(self):
        super().__init__("sse_window_ended")


class DeadlineSocketReader(io.RawIOBase):
    """Apply remaining absolute time on EVERY raw recv, including HTTP framing.

    A BufferedReader.readline() may perform many raw reads. A fixed socket
    inactivity timeout would restart for every fragment and is insufficient.
    The native socket/SSL recv timeout bounds each raw read; recomputing the
    remaining budget bounds their sum. No reader/timer thread is spawned.
    """

    def __init__(self, raw, sock):
        super().__init__()
        self.raw, self.sock, self.deadline = raw, sock, None

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.deadline is not None:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise WindowEnded()
            self.sock.settimeout(remaining)
        try:
            count = self.raw.readinto(buffer)
        except TimeoutError:
            if self.deadline is not None and time.monotonic() >= self.deadline:
                raise WindowEnded() from None
            raise
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise WindowEnded()
        return count

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class DeadlineHTTPResponse(http.client.HTTPResponse):
    def __init__(self, sock, *args, **kwargs):
        super().__init__(sock, *args, **kwargs)
        # HTTPResponse has not read headers yet: no buffered bytes are detached.
        # Keep the standard HTTP parser, including chunk sizes/trailers/CRLF.
        self.deadline_reader = DeadlineSocketReader(self.fp.detach(), sock)
        self.fp = io.BufferedReader(self.deadline_reader)

    @contextlib.contextmanager
    def observation_window(self, deadline):
        reader = self.deadline_reader
        previous_timeout = reader.sock.gettimeout()
        reader.deadline = deadline
        try:
            yield
        finally:
            reader.deadline = None
            # The peer or HTTP parser may have closed the socket on EOF.
            with contextlib.suppress(OSError):
                reader.sock.settimeout(previous_timeout)
            self.close()


@contextlib.contextmanager
def request(args, address, path, *, secure=True, headers=None):
    connection = PinnedHTTPS(args.domain, address) if secure else http.client.HTTPConnection(address, 80, timeout=20)
    connection.response_class = DeadlineHTTPResponse
    try:
        connection.request("GET", path, headers={"Host": args.domain, **(headers or {})})
        with connection.getresponse() as response:
            yield response
    finally:
        connection.close()


def safe_headers(response, bearer=None):
    headers = response.getheaders()
    if any(name.lower() in PRIVATE_HEADERS or (bearer and bearer in value) for name, value in headers):
        raise ProbeFailure("private_header_exposed")


def failure_code(error):
    if isinstance(error, ProbeFailure):
        return error.code
    if isinstance(error, ssl.SSLCertVerificationError):
        return "tls_verification_failed"
    if isinstance(error, ssl.SSLError):
        return "tls_failed"
    if isinstance(error, TimeoutError):
        return "probe_timeout"
    return "transport_or_protocol_failed"


def web(args, report, addresses):
    for target_index, address in enumerate(addresses):
        for name, secure in (("strict_tls_http", True), ("http_redirect", False)):
            try:
                with request(args, address, "/", secure=secure) as response:
                    safe_headers(response)
                    ok = (
                        response.status == 200
                        if secure
                        else response.status == 308 and response.getheader("Location") == f"https://{args.domain}/"
                    )
                    observed(
                        report,
                        "https_ui",
                        name,
                        "passed" if ok else "failed",
                        http_status=response.status,
                        target_index=target_index,
                    )
            except Exception as error:
                observed(report, "https_ui", name, "failed", reason=failure_code(error), target_index=target_index)


def read_bearer():
    if not sys.stdin.isatty():
        raise ProbeFailure("interactive_credential_required")
    # getpass must fail closed rather than falling back to an echoing prompt.
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        value = getpass.getpass("Bearer propio (oculto; no se guarda): ")
    if not re.fullmatch(r"[A-Za-z0-9._~-]{16,4096}", value):
        raise ProbeFailure("invalid_credential")
    return value


def status(args, address, path, bearer):
    with request(args, address, path, headers={"Authorization": f"Bearer {bearer}"}) as response:
        safe_headers(response, bearer)
        if response.status != 200:
            raise ProbeFailure("status_http_failed")
        raw = response.read(65537)
        if len(raw) > 65536:
            raise ProbeFailure("status_size_limit")
        state = json.loads(raw).get("status")
        if state not in {"queued", "running", "completed", "rejected_quality", "failed", "cancelled"}:
            raise ProbeFailure("invalid_status")
        return state


def events(response, seconds, cursor=0):
    """Only IDs, elapsed times and heartbeat counts survive; discard data lines.

    Bounded line/stream sizes prevent untrusted upstream memory exhaustion. The
    clock measures client receipt, not provider execution or server emission.
    Only newline-delimited blocks completed strictly BEFORE start + seconds
    count. Raw reads share that absolute deadline, including incomplete lines
    and HTTP chunk framing. Expiry returns partial evidence and closes response.
    """
    start = time.monotonic()
    deadline = start + seconds
    samples, heartbeats, size = [], 0, 0
    event_id, heartbeat = None, False
    end = "deadline"
    with response.observation_window(deadline):
        while time.monotonic() < deadline:
            try:
                line = response.readline(8193)
            except WindowEnded:
                break
            completed = time.monotonic()
            if completed >= deadline:
                break  # Also reject late data already in the HTTP buffer.
            size += len(line)
            if len(line) > 8192 or size > 4 * 1024 * 1024:
                raise ProbeFailure("sse_size_limit")
            if not line or not line.endswith(b"\n"):
                end = "eof"
                break  # Unterminated lines/blocks are not completed evidence.
            stripped = line.rstrip(b"\r\n")
            if not stripped:
                if heartbeat:
                    heartbeats += 1
                if event_id is not None:
                    if event_id <= cursor:
                        raise ProbeFailure("sse_cursor_not_increasing")
                    cursor = event_id
                    samples.append({"id": cursor, "received_seconds": completed - start})
                event_id, heartbeat = None, False
                if len(samples) >= 2 and heartbeats:
                    end = "sufficient"
                    break
            elif stripped.startswith(b"id:"):
                value = stripped[3:].strip()
                if not re.fullmatch(rb"[0-9]{1,19}", value) or not 0 < int(value) < 2**63:
                    raise ProbeFailure("invalid_sse_id")
                event_id = int(value)
            elif stripped == b": heartbeat":
                heartbeat = True
            elif stripped == b"event: error":
                raise ProbeFailure("sse_error_event")
            if len(samples) >= 4096:
                raise ProbeFailure("sse_event_limit")
    return (
        samples,
        heartbeats,
        {
            "end": end,
            "limit_seconds": seconds,
            "elapsed_seconds": round(time.monotonic() - start, 3),
        },
    )


def stream(args, address, path, bearer, cursor=0):
    headers = {"Authorization": f"Bearer {bearer}", "Accept": "text/event-stream", "Accept-Encoding": "identity"}
    if cursor:
        headers["Last-Event-ID"] = str(cursor)
    with request(args, address, path, headers=headers) as response:
        safe_headers(response, bearer)
        if response.status != 200:
            raise ProbeFailure("sse_http_failed")
        if response.getheader("Content-Type", "").split(";", 1)[0].strip() != "text/event-stream":
            raise ProbeFailure("sse_content_type_failed")
        if response.getheader("Content-Encoding", "identity") != "identity":
            raise ProbeFailure("sse_compressed")
        return events(response, args.observe_seconds, cursor)


def sse(args, report, addresses, bearer):
    # No generation/upload/persist POST. Operator starts a real generation under
    # the separate runbook authorization and supplies ONLY its own ID.
    base = f"/api/generations/{args.generation_id}"
    for target_index, address in enumerate(addresses):
        try:
            before = status(args, address, base, bearer)
            samples, heartbeats, window = stream(args, address, base + "/events", bearer)
            after = status(args, address, base, bearer)
            separated = len(samples) >= 2 and samples[-1]["received_seconds"] - samples[0]["received_seconds"] >= 0.25
            failed_job = before in {"failed", "rejected_quality", "cancelled"} or after in {
                "failed",
                "rejected_quality",
                "cancelled",
            }
            observed(
                report,
                "real_generation_sse",
                "sse_observation_h1",
                "failed"
                if failed_job
                else "passed"
                if separated and heartbeats and before == after == "running"
                else "inconclusive",
                receipt=samples,
                heartbeats=heartbeats,
                window=window,
                running_before=before == "running",
                running_after_disconnect=after == "running",
                state_before=before,
                state_after_disconnect=after,
                protocol="http1.1",
                target_index=target_index,
            )
        except Exception as error:
            observed(
                report,
                "real_generation_sse",
                "sse_observation_h1",
                "failed",
                reason=failure_code(error),
                target_index=target_index,
            )
            continue
        if not samples:
            observed(
                report,
                "real_generation_sse",
                "last_event_id_reconnect",
                "inconclusive",
                reason="no_cursor",
                target_index=target_index,
            )
            continue
        try:
            replay, _, window = stream(args, address, base + "/events", bearer, samples[-1]["id"])
            observed(
                report,
                "real_generation_sse",
                "last_event_id_reconnect",
                "passed" if replay and window["end"] != "deadline" else "inconclusive",
                receipt=replay,
                window=window,
                sent_cursor=samples[-1]["id"],
                protocol="http1.1",
                target_index=target_index,
            )
        except Exception as error:
            observed(
                report,
                "real_generation_sse",
                "last_event_id_reconnect",
                "failed",
                reason=failure_code(error),
                target_index=target_index,
            )


def tcp_port(address, port):
    try:
        with socket.create_connection((address, port), timeout=1):
            return "open"
    except TimeoutError:
        return "no_response"
    except OSError as error:
        return "refused" if error.errno == errno.ECONNREFUSED else "error"


def tcp(args, report, addresses):
    # All ports, every authorized A/AAAA, bounded concurrency, no port subset.
    # A timeout is NOT relabelled as closed or as proof of firewall policy.
    for target_index, address in enumerate(addresses):
        counts = dict.fromkeys(("open", "refused", "no_response", "error"), 0)
        opened, checked, ssh = [], 0, None
        interrupted = False
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=16) as pool:
                for start in range(1, 65536, 256):
                    ports = range(start, min(start + 256, 65536))
                    for port, state in zip(ports, pool.map(lambda port: tcp_port(address, port), ports), strict=True):
                        counts[state] += 1
                        checked += 1
                        if state == "open":
                            opened.append(port)
                        if port == 22:
                            ssh = state
        except (Exception, KeyboardInterrupt):
            interrupted = True
        unexpected = any(port not in {80, 443} for port in opened)
        result = (
            "failed"
            if unexpected or (checked == 65535 and not {80, 443}.issubset(opened))
            else "inconclusive"
            if interrupted or checked != 65535 or counts["error"] or counts["no_response"]
            else "passed"
        )
        observed(
            report,
            "public_tcp",
            "full_tcp_range",
            result,
            checked=checked,
            open_ports=opened,
            counts=counts,
            target_index=target_index,
        )
        observed(
            report,
            "public_tcp",
            "nonadmin_ssh_unreachable",
            "failed" if ssh == "open" else "passed" if ssh == "refused" else "inconclusive",
            target_index=target_index,
        )
        if interrupted:
            break


def main(argv=None):
    report = None
    try:
        args = inputs(argv)
        report = plan(args)
        if args.execute:
            bearer = read_bearer() if args.execute == "sse" else None
            addresses = pinned_addresses(args)
            if args.execute == "web":
                web(args, report, addresses)
            elif args.execute == "sse":
                sse(args, report, addresses, bearer)
            else:
                tcp(args, report, addresses)
        finish(report)
        print(json.dumps(report, ensure_ascii=True, indent=2))
        # 0 means a valid offline plan; 2 partial/inconclusive live evidence; 1 failure.
        if not args.execute:
            return 0
        return 1 if any(c["result"] == "failed" for c in report["criteria"].values()) else 2
    except (Exception, KeyboardInterrupt) as error:
        if report is None:
            print(json.dumps({"result": "failed", "reason": "invalid_input", "acceptance": "not_approved"}))
        else:
            report["execution_error"] = failure_code(error)
            print(json.dumps(finish(report), ensure_ascii=True, indent=2))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
