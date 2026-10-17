"""Host-only lifecycle for #48. Standard library; never emits command output.

No provider calls, migrations, DNS/firewall mutations, secret generation or volume
creation. Linux entry points require root/Ubuntu 24.04/ARM64. Tests inject system
boundaries, not an alternative interpretation of Caddyfile or Compose YAML.
"""

import argparse
import configparser
import contextlib
import copy
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
import platform
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

CADDY_VERSION = "v2.11.7"
COSIGN_VERSION = "v2.6.5"
COSIGN_SHA256 = "426193b4c5da4d4d643e822f48fe0cc8a476ca1782a272704831f5a0cef716d7"
ARCHIVE = "caddy_2.11.7_linux_arm64.tar.gz"
ARCHIVE_SHA256 = "d8fc6d179a5d283028a472a5618564f6ad8a86fed513e64f032b3b0b7cc45e42"
ARCHIVE_SHA512 = (
    "3db36ba90c7a6e8dda40ee3dd71fa08844c76b5fb08f61b31e5e78d2ed38e71c51dc"
    "7baed875e50d1ca1279196e84302967237386ae87c91ae9f2aaceada682e"
)
CHECKSUM_SHA256 = "5d27b76b95f496638d79208c55250317b8755fd9e90005f7466aa32956944a90"
IDENTITY = "https://github.com/caddyserver/caddy/.github/workflows/release.yml@refs/tags/v2.11.7"
ISSUER = "https://token.actions.githubusercontent.com"
PROJECT = "nuevamente"
VOLUME = "nuevamente_backend_data"
STATE = Path("/var/lib/nuevamente-deploy")
HELPER = Path("/usr/local/lib/nuevamente/verify_deployment.py")
MANIFEST = STATE / "installation.json"
CADDYFILE = Path("/etc/caddy/Caddyfile")
CADDY_ENV = Path("/etc/caddy/caddy.env")
ORIGIN_ENV = Path("/etc/nuevamente/secrets/origin.env")
BACKEND_ENV = Path("/etc/nuevamente/backend.env")
DEPLOY_ENV = Path("/etc/nuevamente/deployment.env")
OCI_DIR = Path("/etc/nuevamente/oci")
BASE_UNIT = Path("/etc/systemd/system/caddy.service")
DROPIN = Path("/etc/systemd/system/caddy.service.d/nuevamente.conf")
ROOT = Path(__file__).resolve().parents[2]

UNIT = """[Unit]
Description=NuevaMente Caddy 2.11.7 (managed by Issue 48)
After=network-online.target docker.service nuevamente-firewall.service
Wants=network-online.target
Requires=docker.service nuevamente-firewall.service
ConditionPathExists=/etc/caddy/Caddyfile
[Service]
Type=notify
NotifyAccess=all
TimeoutStartSec=90
TimeoutStopSec=30
KillMode=mixed
Restart=on-failure
RestartSec=5
LimitNOFILE=1048576
[Install]
WantedBy=multi-user.target
"""

# Every production container starts without interpreting forwarded headers. Only
# the root deployer can publish the release marker via docker exec. Restarting a
# container clears tmpfs and closes the gate again, including daemon/host reboot.
PEER_GATE = """import json, os, socket, stat, time
marker='/tmp/nuevamente-proxy-ready'
try:
    os.unlink(marker)
except FileNotFoundError:
    pass
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
s.bind(('0.0.0.0',8000)); s.listen(8); s.settimeout(120)
deadline=time.monotonic()+120
while time.monotonic()<deadline:
    c,addr=s.accept(); c.settimeout(2)
    try:
        nonce=c.recv(256).decode('ascii').strip()
        if len(nonce)!=64 or any(x not in '0123456789abcdef' for x in nonce):
            continue
        c.sendall((json.dumps({'nonce':nonce,'peer':addr[0]})+'\\n').encode())
        while time.monotonic()<deadline:
            if os.path.lexists(marker):
                st=os.lstat(marker)
                if stat.S_ISREG(st.st_mode) and st.st_uid==0 and stat.S_IMODE(st.st_mode)==0o644:
                    with open(marker) as f:
                        approved=json.load(f)
                    if approved=={'nonce':nonce,'peer':addr[0]} and addr[0] in os.environ['FORWARDED_ALLOW_IPS'].split(','):
                        s.close(); c.close()
                        break
                raise SystemExit('Proxy peer gate rejected release marker')
            time.sleep(.05)
        else:
            continue
        break
    finally:
        c.close()
else:
    raise SystemExit('Proxy peer gate expired; run reviewed deployment')
s.close()
"""


class Failure(RuntimeError):
    """Contains only a fixed diagnostic code, never system output/input values."""


def require(condition, code):
    if not condition:
        raise Failure(code)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def same_secret(first, second):
    require(isinstance(first, str) and isinstance(second, str), "origin-secret-missing")
    require(hmac.compare_digest(first.encode(), second.encode()), "origin-secret-inconsistent")


def run(args, *, env=None, input=None, timeout=60, ok=(0,), deadline=None):
    """Capture pipes in memory. No raw stderr, repr(exception), arguments or env."""
    clean = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "HOME": "/root"}
    executable = Path(str(args[0])).name
    label = (
        executable
        if executable
        in {
            "docker",
            "systemctl",
            "iptables",
            "ip",
            "ss",
            "sshd",
            "cloud-init",
            "apt-config",
            "systemd-analyze",
            "dpkg-query",
            "getent",
            "groupadd",
            "useradd",
            "runuser",
            "caddy",
            "cosign",
        }
        else "tool"
    )
    operation = str(args[1]) if len(args) > 1 else ""
    if operation in {
        "config",
        "compose",
        "run",
        "version",
        "adapt",
        "validate",
        "reload",
        "verify-blob",
        "start",
        "stop",
        "is-active",
        "is-enabled",
        "daemon-reload",
        "show",
        "exec",
        "inspect",
        "network",
        "image",
        "volume",
        "ps",
        "info",
    }:
        label += ":" + operation
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        require(timeout > 0, "upstream-health-timeout")
    try:
        result = subprocess.run(
            [str(a) for a in args],
            input=input,
            env=clean | (env or {}),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise Failure("command-unavailable-or-timeout:" + label) from None
    require(result.returncode in ok, f"command-failed:{label}:exit-{result.returncode}")
    return result


def json_output(args, **kwargs):
    try:
        return json.loads(run(args, **kwargs).stdout)
    except (ValueError, TypeError):
        raise Failure("invalid-tool-json") from None


def secure_path(path, *, uid=0, gid=0, mode=0o600, directory=False):
    """Reject symlinks in every component, ACLs, insecure parents and ownership."""
    path = Path(path)
    require(path.is_absolute(), "path-not-absolute")
    for parent in reversed((path, *path.parents)):
        require(not parent.is_symlink(), "symlink-path-rejected")
        info = parent.stat()
        if parent != path:
            trusted_oci_parent = (
                parent == OCI_DIR and uid == 10001 and info.st_uid == 10001 and stat.S_IMODE(info.st_mode) == 0o700
            )
            require((info.st_uid == 0 or trusted_oci_parent) and not info.st_mode & 0o022, "insecure-parent")
    info = path.stat()
    require(info.st_uid == uid and info.st_gid == gid, "invalid-owner")
    require(stat.S_IMODE(info.st_mode) == mode, "invalid-permissions")
    require(stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode), "invalid-file-type")
    # Ubuntu ext4 ACLs can grant access beyond the mode bits. Fail closed.
    if hasattr(os, "listxattr"):
        require(not any("posix_acl" in x for x in os.listxattr(path)), "extended-acl-rejected")
    return path


def read_env(path, allowed):
    secure_path(path)
    return parse_env(Path(path).read_text(), allowed)


def parse_env(text, allowed):
    """Restricted literal assignments shared by dotenv, Docker and systemd.

    Intentionally reject quoting/expansion/escapes instead of implementing three
    parsers with subtly different secret semantics. Values are never evaluated.
    """
    result = {}
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=([^\r\n]*)", line)
        require(match is not None, "invalid-env-assignment")
        key, value = match.groups()
        require(key in allowed and key not in result, "unknown-or-duplicate-env-key")
        require(value == value.strip() and not any(c in value for c in "'$`\\\"#\x00"), "nonliteral-env-value")
        result[key] = value
    return result


def public_environment(caddy, deployment, origin, *, observation=False):
    domain = caddy.get("NUEVAMENTE_DOMAIN", "")
    require(
        len(domain) <= 253
        and not domain.endswith((".localhost", ".local", ".internal", ".home.arpa"))
        and re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?", domain),
        "invalid-public-hostname",
    )
    require(deployment.get("NUEVAMENTE_DOMAIN") == domain, "hostname-inconsistent")
    require(caddy.get("CADDY_STORAGE_DIR") == "/var/lib/caddy", "invalid-caddy-storage-path")
    require(caddy.get("CADDY_LOG_DIR") == "/var/log/caddy", "invalid-caddy-log-path")
    require(re.fullmatch("[0-9a-f]{64}", origin.get("TRUSTED_ORIGIN_SECRET", "")), "invalid-origin-secret")
    for key, value in {
        "NUEVAMENTE_BACKEND_ENV_FILE": str(BACKEND_ENV),
        "NUEVAMENTE_ORIGIN_ENV_FILE": str(ORIGIN_ENV),
        "OCI_CREDENTIALS_DIR": str(OCI_DIR),
    }.items():
        require(deployment.get(key) == value, "noncanonical-env-path")
    if observation:
        require(not deployment.get("FORWARDED_ALLOW_IPS"), "initial-observation-requires-empty-peers")
        return caddy | origin
    try:
        peers = deployment["FORWARDED_ALLOW_IPS"].split(",")
        require(bool(peers) and len(peers) == len(set(peers)), "invalid-proxy-peers")
        require(
            all(not ipaddress.ip_address(p).is_unspecified and not ipaddress.ip_address(p).is_multicast for p in peers),
            "invalid-proxy-peers",
        )
    except (ValueError, KeyError):
        raise Failure("invalid-proxy-peers") from None
    return caddy | origin


@contextlib.contextmanager
def lock(path):
    import fcntl  # POSIX-only entry points; tests can substitute this boundary.

    path = Path(path)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        require(
            stat.S_ISREG(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o600, "insecure-lock"
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Failure("deployment-already-running") from None
        yield
    finally:
        os.close(fd)


def atomic_write(path, data, *, mode=0o600, uid=0, gid=0):
    path = Path(path)
    require(not path.is_symlink(), "symlink-target-rejected")
    fd, temporary = tempfile.mkstemp(prefix=".issue48-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        os.fchown(fd, uid, gid)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data if isinstance(data, bytes) else data.encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def platform_check():
    require(os.name == "posix" and os.geteuid() == 0, "requires-linux-root")
    os_release = platform.freedesktop_os_release()
    require(os_release.get("ID") == "ubuntu" and os_release.get("VERSION_ID") == "24.04", "requires-ubuntu-24.04")
    require(platform.machine() == "aarch64", "requires-arm64")


def signature_check(artifact, cosign):
    # Check bytes before executing the verifier; the operator supplies it from
    # the pinned official Sigstore release, never from PATH or a curl | shell.
    secure_path(cosign, mode=0o755)
    require(digest(Path(cosign).read_bytes()) == COSIGN_SHA256, "cosign-provenance-mismatch")
    version = json_output([cosign, "version", "--json"])
    require(version.get("gitVersion") == COSIGN_VERSION, "cosign-version-mismatch")
    with tempfile.TemporaryDirectory(prefix=".cosign-cache-", dir=Path(artifact).parent) as directory:
        run(
            [
                cosign,
                "verify-blob",
                "--certificate",
                str(certificate_path(artifact)),
                "--signature",
                str(artifact) + ".sig",
                "--certificate-identity",
                IDENTITY,
                "--certificate-oidc-issuer",
                ISSUER,
                "--rekor-url",
                "https://rekor.sigstore.dev",
                "--insecure-ignore-tlog=false",
                "--insecure-ignore-sct=false",
                artifact,
            ],
            env={"COSIGN_EXPERIMENTAL": "1", "HOME": directory, "TUF_ROOT": directory},
            timeout=120,
        )


def certificate_path(artifact):
    # Official tar certificates omit .tar.gz; checksum certificates retain .txt.
    path = Path(artifact)
    return path.with_name(path.name.removesuffix(".tar.gz") + ".pem")


def verified_binary(artifacts, cosign):
    artifacts = secure_path(artifacts, mode=0o700, directory=True)
    archive = secure_path(artifacts / ARCHIVE)
    checksums = secure_path(artifacts / "caddy_2.11.7_checksums.txt")
    for item in (archive, checksums):
        secure_path(certificate_path(item))
        secure_path(Path(str(item) + ".sig"))
    data = archive.read_bytes()
    require(digest(data) == ARCHIVE_SHA256, "caddy-hash-mismatch")
    require(hashlib.sha512(data).hexdigest() == ARCHIVE_SHA512, "caddy-sha512-mismatch")
    require(digest(checksums.read_bytes()) == CHECKSUM_SHA256, "checksum-manifest-mismatch")
    rows = [row.split() for row in checksums.read_text().splitlines()]
    require([row for row in rows if row[-1:] == [ARCHIVE]] == [[ARCHIVE_SHA512, ARCHIVE]], "checksum-entry-mismatch")
    signature_check(archive, cosign)
    signature_check(checksums, cosign)
    # Never extract paths/links from an untrusted archive into the host.
    with tarfile.open(archive, "r:gz") as tar:
        candidates = [item for item in tar.getmembers() if item.name == "caddy"]
        require(
            len(candidates) == 1 and candidates[0].isfile() and candidates[0].size < 150_000_000,
            "invalid-caddy-archive",
        )
        return tar.extractfile(candidates[0]).read()


def managed_installation():
    secure_path(STATE, mode=0o700, directory=True)
    secure_path(MANIFEST)
    manifest = json.loads(MANIFEST.read_text())
    require(manifest.get("version") == CADDY_VERSION, "foreign-installation")
    expected = {"/usr/bin/caddy", str(BASE_UNIT), str(DROPIN), str(HELPER)}
    require(set(manifest.get("files", {})) == expected, "invalid-installation-manifest")
    for name, checksum in manifest["files"].items():
        path = secure_path(name, mode=0o755 if name == "/usr/bin/caddy" else 0o644)
        require(digest(path.read_bytes()) == checksum, "managed-installation-changed")
    require(run(["/usr/bin/caddy", "version"]).stdout.split()[0] == CADDY_VERSION, "caddy-version-mismatch")
    return manifest


def install(artifacts, cosign, source):
    platform_check()
    with lock("/run/lock/nuevamente-caddy.lock"):
        binary = verified_binary(artifacts, cosign)
        files = {
            Path("/usr/bin/caddy"): binary,
            BASE_UNIT: UNIT.encode(),
            DROPIN: (source / "caddy.service.d/nuevamente.conf").read_bytes(),
            HELPER: (source / "verify_deployment.py").read_bytes(),
        }
        if MANIFEST.exists():
            manifest = managed_installation()
            require(
                manifest["files"] == {str(p): digest(b) for p, b in files.items()},
                "installation-update-requires-review",
            )
            print("installation-already-prepared; state-preserved")
            return
        require(
            not run(["dpkg-query", "-W", "-f=${Status}", "caddy"], ok=(0, 1)).stdout.strip(),
            "foreign-package-installation",
        )
        require(not run(["getent", "passwd", "caddy"], ok=(0, 2)).stdout, "foreign-caddy-user")
        require(not run(["getent", "group", "caddy"], ok=(0, 2)).stdout, "foreign-caddy-group")
        for path in (
            *files,
            CADDYFILE,
            Path("/lib/systemd/system/caddy.service"),
            Path("/usr/lib/systemd/system/caddy.service"),
            Path("/usr/local/bin/caddy"),
            Path("/etc/systemd/system/caddy-api.service"),
            Path("/lib/systemd/system/caddy-api.service"),
            Path("/usr/lib/systemd/system/caddy-api.service"),
            STATE,
            Path("/var/lib/caddy"),
            Path("/var/log/caddy"),
            BASE_UNIT.parent / "caddy.service.d",
        ):
            require(not os.path.lexists(path), "foreign-or-partial-installation")
        # Validate the signed binary version in a private staging directory first.
        with tempfile.TemporaryDirectory(prefix="issue48-install-", dir="/root") as directory:
            staged = Path(directory) / "caddy"
            staged.write_bytes(binary)
            staged.chmod(0o755)
            require(run([staged, "version"]).stdout.split()[0] == CADDY_VERSION, "caddy-version-mismatch")
        run(["groupadd", "--system", "caddy"])
        run(
            [
                "useradd",
                "--system",
                "--gid",
                "caddy",
                "--home-dir",
                "/var/lib/caddy",
                "--no-create-home",
                "--shell",
                "/usr/sbin/nologin",
                "caddy",
            ]
        )
        import pwd

        account = pwd.getpwnam("caddy")
        for path, mode, uid, gid in (
            (STATE, 0o700, 0, 0),
            (HELPER.parent, 0o755, 0, 0),
            (DROPIN.parent, 0o755, 0, 0),
            (CADDYFILE.parent, 0o750, 0, account.pw_gid),
            (Path("/var/lib/caddy"), 0o700, account.pw_uid, account.pw_gid),
            (Path("/var/log/caddy"), 0o750, account.pw_uid, account.pw_gid),
        ):
            if path.exists():
                secure_path(path, uid=uid, gid=gid, mode=mode, directory=True)
            else:
                path.mkdir(mode=mode, parents=False)
                os.chown(path, uid, gid)
                # mkdir applies the restrictive wrapper umask. Only a directory
                # created here may be finalized; existing paths are never fixed.
                path.chmod(mode)
                secure_path(path, uid=uid, gid=gid, mode=mode, directory=True)
        for path, data in files.items():
            atomic_write(path, data, mode=0o755 if path == Path("/usr/bin/caddy") else 0o644)
        run(["systemd-analyze", "verify", BASE_UNIT])
        run(["systemctl", "daemon-reload"])
        atomic_write(
            MANIFEST, json.dumps({"version": CADDY_VERSION, "files": {str(p): digest(b) for p, b in files.items()}})
        )
        print("installation-prepared; service-not-activated")


def caddy_cli(action, config=CADDYFILE, *, env=None):
    require(action in ("adapt", "validate", "reload"), "invalid-caddy-action")
    args = ["/usr/bin/caddy", action, "--config", config, "--adapter", "caddyfile"]
    if action == "reload":
        args += ["--address", "unix//run/caddy/admin.sock"]
    if env is None:
        env = {
            key: os.environ[key]
            for key in ("NUEVAMENTE_DOMAIN", "TRUSTED_ORIGIN_SECRET", "CADDY_STORAGE_DIR", "CADDY_LOG_DIR")
            if key in os.environ
        }
    env = env | {"HOME": "/var/lib/caddy", "XDG_DATA_HOME": "/var/lib/caddy", "XDG_CONFIG_HOME": "/var/lib/caddy"}
    if os.geteuid() == 0:
        args = ["runuser", "-u", "caddy", "--", *args]
    return run(args, env=env)


def caddy_run():
    """Keep Caddy CLI startup/shutdown diagnostics out of the journal.

    Runtime diagnostics use the filtered operational file. Before it is configured
    stderr is unstructured/untrusted and cannot safely be partially redacted.
    systemd notify and termination reach the child; no shell/debug/environment log.
    """
    child = subprocess.Popen(
        ["/usr/bin/caddy", "run", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
        env=os.environ.copy(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda number, frame: child.send_signal(number))
    code = child.wait()
    print("caddy-process-exited; consult-filtered-operational-log", flush=True)
    return code if code >= 0 else 128 - code


def network_evidence(document, admin_cidr, now):
    """Check a recently reviewed export, not claim a live OCI query happened.

    security_lists/nsg_rules contain the native OCI CLI JSON data for ALL attached
    lists/groups, including the default list. The operator attests completeness.
    Public verification remains independent; this file is not production evidence.
    """
    require(
        document.get("schema") == 1 and document.get("all_attached_reviewed") is True, "oci-network-review-required"
    )
    require(
        isinstance(document.get("reviewed_at"), (int, float)) and 0 <= now - document["reviewed_at"] <= 3600,
        "oci-network-review-expired",
    )
    require(str(document.get("instance_id", "")).startswith("ocid1.instance."), "oci-instance-evidence-missing")
    require(document.get("admin_cidr") == admin_cidr, "oci-admin-cidr-inconsistent")
    address = ipaddress.ip_address(document.get("public_ipv4", ""))
    require(address.version == 4 and address.is_global, "oci-public-address-invalid")
    require(bool(document.get("security_lists")), "oci-security-list-evidence-missing")
    rules = [rule for item in document["security_lists"] for rule in item["ingress-security-rules"]]
    rules += [r for r in document.get("nsg_rules", []) if r.get("direction") == "INGRESS"]
    seen = set()
    pmtu = False
    for rule in rules:
        require(
            rule.get("is-stateless") is False and rule.get("source-type") == "CIDR_BLOCK", "oci-ingress-policy-invalid"
        )
        if rule.get("protocol") == "1":
            require(
                rule.get("source") == "0.0.0.0/0" and rule.get("icmp-options") == {"type": 3, "code": 4},
                "oci-icmp-policy-changed",
            )
            pmtu = True
            continue
        require(rule.get("protocol") == "6", "oci-nontcp-ingress-rejected")
        options = rule.get("tcp-options") or {}
        ports = options.get("destination-port-range") or {}
        require(ports.get("min") == ports.get("max") and ports.get("min") in (22, 80, 443), "oci-ingress-port-rejected")
        port = ports["min"]
        require(rule.get("source") == (admin_cidr if port == 22 else "0.0.0.0/0"), "oci-ingress-source-rejected")
        require(options.get("source-port-range") is None, "oci-ingress-source-port-rejected")
        seen.add(port)
    require(seen == {22, 80, 443}, "oci-ingress-incomplete")
    require(pmtu, "issue47-pmtu-rule-missing")
    return document


def firewall_rules(output, admin_cidr, interface):
    rules = [shlex.split(line) for line in output.splitlines()]
    # iptables canonicalizes conntrack state order. All other rules/order must
    # match #47, including first jump (an earlier ACCEPT would bypass the chain).
    rules = [["ESTABLISHED,RELATED" if x == "RELATED,ESTABLISHED" else x for x in row] for row in rules]
    expected_in = [
        ["-A", "NUEVAMENTE-IN", "-i", "lo", "-j", "ACCEPT"],
        ["-A", "NUEVAMENTE-IN", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT"],
        ["-A", "NUEVAMENTE-IN", "-p", "udp", "-m", "udp", "--sport", "67", "--dport", "68", "-j", "ACCEPT"],
        ["-A", "NUEVAMENTE-IN", "-s", admin_cidr, "-p", "tcp", "-m", "tcp", "--dport", "22", "-j", "ACCEPT"],
        ["-A", "NUEVAMENTE-IN", "-p", "tcp", "-m", "multiport", "--dports", "80,443", "-j", "ACCEPT"],
        ["-A", "NUEVAMENTE-IN", "-p", "icmp", "-m", "icmp", "--icmp-type", "3/4", "-j", "ACCEPT"],
        ["-A", "NUEVAMENTE-IN", "-j", "DROP"],
    ]
    expected_fwd = [
        ["-A", "NUEVAMENTE-FWD", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN"],
        ["-A", "NUEVAMENTE-FWD", "-i", interface, "-j", "DROP"],
        ["-A", "NUEVAMENTE-FWD", "-j", "RETURN"],
    ]
    for chain, expected in (("NUEVAMENTE-IN", expected_in), ("NUEVAMENTE-FWD", expected_fwd)):
        require([r for r in rules if r[:2] == ["-A", chain]] == expected, "issue47-firewall-changed")
    for chain, target in (("INPUT", "NUEVAMENTE-IN"), ("DOCKER-USER", "NUEVAMENTE-FWD")):
        chain_rules = [r for r in rules if r[:2] == ["-A", chain]]
        require(chain_rules and chain_rules[0] == ["-A", chain, "-j", target], "issue47-firewall-bypassed")


def listeners(output):
    for line in output.splitlines():
        fields = line.split()
        require(len(fields) >= 5, "invalid-listener-output")
        protocol, local = fields[0], fields[4]
        host, port = local.rsplit(":", 1)
        host = host.strip("[]").split("%", 1)[0]
        address = None if host == "*" else ipaddress.ip_address(host)
        if address is not None and address.is_loopback:
            continue
        require(
            protocol == "tcp" and port in ("22", "80", "443") or protocol == "udp" and port == "68",
            "public-listener-rejected",
        )


def issue47_marker():
    """Read-only exception for the exact bootstrap marker, never for secrets.

    #47 deliberately gives ubuntu ownership of /opt/nuevamente (0750). The
    marker itself must remain root:root 0644, with protected root ancestors.
    """
    import pwd

    account = pwd.getpwnam("ubuntu")
    require(account.pw_uid > 0 and account.pw_gid > 0, "issue47-ubuntu-account-invalid")
    path = Path("/opt/nuevamente/issue47-ready")
    for component in reversed((path, *path.parents)):
        require(not component.is_symlink(), "issue47-marker-symlink-rejected")
        info = component.lstat()
        require(not stat.S_ISLNK(info.st_mode), "issue47-marker-symlink-rejected")
        if component == path:
            require(stat.S_ISREG(info.st_mode), "issue47-marker-type-invalid")
            require(info.st_uid == 0 and info.st_gid == 0, "issue47-marker-owner-invalid")
            require(stat.S_IMODE(info.st_mode) == 0o644, "issue47-marker-permissions-invalid")
        else:
            require(stat.S_ISDIR(info.st_mode), "issue47-marker-parent-type-invalid")
            if component == path.parent:
                require(
                    info.st_uid == account.pw_uid and info.st_gid == account.pw_gid,
                    "issue47-marker-parent-owner-invalid",
                )
                require(stat.S_IMODE(info.st_mode) == 0o750, "issue47-marker-parent-permissions-invalid")
            else:
                require(
                    info.st_uid == 0 and info.st_gid == 0 and not info.st_mode & 0o022,
                    "issue47-marker-ancestor-invalid",
                )
        if hasattr(os, "listxattr"):
            require(not any("posix_acl" in x for x in os.listxattr(component)), "issue47-marker-acl-rejected")


def host_preflight():
    import pwd

    platform_check()
    managed_installation()
    account = pwd.getpwnam("caddy")
    for path, mode in (("/var/lib/caddy", 0o700), ("/var/log/caddy", 0o750)):
        secure_path(path, uid=account.pw_uid, gid=account.pw_gid, mode=mode, directory=True)
    issue47_marker()
    require(
        json_output(["cloud-init", "status", "--format", "json"]).get("status") == "done",
        "issue47-cloud-init-not-ready",
    )
    run(["/usr/sbin/sshd", "-t"])
    ssh = run(["/usr/sbin/sshd", "-T"]).stdout.splitlines()
    require(
        all(
            x in ssh
            for x in (
                "passwordauthentication no",
                "kbdinteractiveauthentication no",
                "permitrootlogin no",
                "addressfamily inet",
            )
        ),
        "issue47-ssh-policy-changed",
    )
    for service in ("docker", "nuevamente-firewall", "unattended-upgrades"):
        run(["systemctl", "is-active", "--quiet", service])
        run(["systemctl", "is-enabled", "--quiet", service])
    require(
        'APT::Periodic::Unattended-Upgrade "1";' in run(["apt-config", "dump"]).stdout,
        "issue47-security-updates-disabled",
    )
    admin = read_env("/etc/nuevamente-firewall.conf", {"NUEVAMENTE_ADMIN_CIDR"})["NUEVAMENTE_ADMIN_CIDR"]
    net = ipaddress.ip_network(admin, strict=True)
    require(net.version == 4 and net.prefixlen == 32 and net.network_address.is_global, "issue47-admin-cidr-invalid")
    routes = json_output(["ip", "-j", "-4", "route", "show", "default"])
    require(len(routes) == 1, "ambiguous-default-route")
    firewall_rules(run(["iptables", "-w", "10", "-S"]).stdout, admin, routes[0]["dev"])
    policies = json_output(["ip", "-j", "-4", "rule", "show"])
    require(
        [(p.get("priority"), p.get("table")) for p in policies] == [(0, "local"), (32766, "main"), (32767, "default")],
        "nonstandard-routing-requires-review",
    )
    for table in ("filter", "mangle", "nat", "raw"):
        rules = run(["iptables", "-w", "10", "-t", table, "-S"]).stdout
        require(
            not any(option in shlex.split(rules) for option in ("--uid-owner", "--gid-owner", "--cgroup")),
            "process-specific-network-policy-requires-review",
        )

    def process_specific(value):
        if isinstance(value, dict):
            if value.get("meta", {}).get("key") == "cgroup" or value.get("socket", {}).get("key") == "cgroupv2":
                return True
            return any(process_specific(item) for item in value.values())
        if isinstance(value, list):
            return any(process_specific(item) for item in value)
        return False

    require(
        not process_specific(json_output(["nft", "-j", "list", "ruleset"])),
        "cgroup-specific-nft-policy-requires-review",
    )
    addresses = json_output(["ip", "-j", "-6", "address"])
    require(
        not any(a.get("scope") == "global" for link in addresses for a in link.get("addr_info", [])),
        "ipv6-requires-separate-firewall-review",
    )
    listeners(run(["ss", "-H", "-lntu"]).stdout)
    require(
        run(["docker", "info", "--format", "{{.Architecture}}"]).stdout.strip() in {"aarch64", "arm64"},
        "docker-not-arm64",
    )
    evidence = secure_path("/etc/nuevamente/issue47-network.json")
    document = network_evidence(json.loads(evidence.read_text()), admin, time.time())
    require(
        document.get("host_machine_id") == Path("/etc/machine-id").read_text().strip(),
        "oci-network-evidence-from-other-host",
    )
    unit = run(
        [
            "systemctl",
            "show",
            "caddy",
            "--property=FragmentPath,DropInPaths,User,Group,UMask,EnvironmentFiles,AmbientCapabilities,CapabilityBoundingSet,NoNewPrivileges,PrivateTmp,ProtectSystem,ProtectHome,RuntimeDirectoryMode,StateDirectoryMode,LogsDirectoryMode",
        ]
    ).stdout
    properties = dict(line.split("=", 1) for line in unit.splitlines() if "=" in line)
    expected = {
        "FragmentPath": str(BASE_UNIT),
        "DropInPaths": str(DROPIN),
        "User": "caddy",
        "Group": "caddy",
        "UMask": "0077",
        "NoNewPrivileges": "yes",
        "PrivateTmp": "yes",
        "ProtectSystem": "full",
        "ProtectHome": "yes",
        "AmbientCapabilities": "cap_net_bind_service",
        "CapabilityBoundingSet": "cap_net_bind_service",
        "RuntimeDirectoryMode": "0700",
        "StateDirectoryMode": "0700",
        "LogsDirectoryMode": "0750",
    }
    require(all(properties.get(k) == v for k, v in expected.items()), "unexpected-effective-systemd-unit")
    require(
        properties.get("EnvironmentFiles")
        == "/etc/caddy/caddy.env (ignore_errors=no) /etc/nuevamente/secrets/origin.env (ignore_errors=no)",
        "unexpected-systemd-environment-sources",
    )


def environment_files(root, *, observation=False):
    caddy = read_env(CADDY_ENV, {"NUEVAMENTE_DOMAIN", "CADDY_STORAGE_DIR", "CADDY_LOG_DIR"})
    origin = read_env(ORIGIN_ENV, {"TRUSTED_ORIGIN_SECRET"})
    secure_path(ORIGIN_ENV.parent, mode=0o700, directory=True)
    deployment = read_env(
        DEPLOY_ENV,
        {
            "NUEVAMENTE_DOMAIN",
            "NUEVAMENTE_BACKEND_ENV_FILE",
            "NUEVAMENTE_ORIGIN_ENV_FILE",
            "OCI_CREDENTIALS_DIR",
            "FORWARDED_ALLOW_IPS",
        },
    )
    # Allow documented Configuracion inputs, including commented quota examples.
    template_keys = set(re.findall(r"^\s*(?:#\s*)?([A-Z][A-Z0-9_]*)=", (root / ".env.example").read_text(), re.M))
    backend = read_env(BACKEND_ENV, (template_keys | {"MOCK_GEMINI", "CORS_ORIGINS"}) - {"TRUSTED_ORIGIN_SECRET"})
    require("TRUSTED_ORIGIN_SECRET" not in backend, "duplicate-origin-secret-source")
    for key, value in {
        "APP_ENV": "production",
        "MOCK_OCI": "0",
        "MOCK_GEMINI": "0",
        "OCI_AUTH_TYPE": "api_key",
        "OCI_CONFIG_PROFILE": "DOCKER",
        "OCI_CONFIG_FILE": "/run/oci/config",
        "OCI_ALWAYS_FREE_CONFIRMED": "1",
    }.items():
        require(backend.get(key) == value, "backend-production-settings-missing")
    require(backend.get("GOOGLE_API_KEY", "").strip() not in {"", "placeholder"}, "gemini-not-configured")
    for prefix in ("EMBEDDING", "VISION", "GENERATION", "VERIFICATION"):
        for suffix in ("RPM", "TPM", "RPD"):
            require(
                re.fullmatch(r"[1-9][0-9]*", backend.get(f"{prefix}_QUOTA_{suffix}", "")),
                "provider-quotas-not-configured",
            )
    env = public_environment(caddy, deployment, origin, observation=observation)
    # Technical OCI credentials are readable only by the container UID/GID; no
    # operator credentials or extra files are accepted in this dedicated mount.
    secure_path(OCI_DIR, uid=10001, gid=10001, mode=0o700, directory=True)
    require({p.name for p in OCI_DIR.iterdir()} == {"config", "private_key.pem"}, "unexpected-oci-credential-files")
    for name in ("config", "private_key.pem"):
        secure_path(OCI_DIR / name, uid=10001, gid=10001)
    config = configparser.ConfigParser(interpolation=None)
    config.read(OCI_DIR / "config")
    require(config.sections() == ["DOCKER"] and not config.defaults(), "unexpected-oci-profile")
    profile = dict(config["DOCKER"])
    require(set(profile) == {"user", "tenancy", "fingerprint", "key_file", "region"}, "unexpected-oci-profile-keys")
    require(
        profile["key_file"] == "/run/oci/private_key.pem" and profile["region"] == backend.get("OCI_REGION"),
        "oci-profile-inconsistent",
    )
    require(
        profile["user"].startswith("ocid1.user.") and profile["tenancy"].startswith("ocid1.tenancy."),
        "oci-profile-placeholder",
    )
    require(re.fullmatch(r"(?:[0-9a-f]{2}:){15}[0-9a-f]{2}", profile["fingerprint"]), "oci-fingerprint-invalid")
    return env, deployment, backend


def compose_configuration(root, deployment, backend, env):
    version = run(["docker", "compose", "version", "--short"]).stdout.strip().lstrip("v")
    require(tuple(map(int, version.split(".")[:3])) >= (2, 24, 4), "compose-too-old")
    args = ["docker", "compose", "--project-name", PROJECT, "--project-directory", root, "--env-file", DEPLOY_ENV]
    for name in ("docker-compose.yml", "docker-compose.oci.yml", "docker-compose.production.yml"):
        args += ["-f", root / name]
    interpolation = {"FORWARDED_ALLOW_IPS": deployment["FORWARDED_ALLOW_IPS"]}
    raw = json_output(
        [*args, "config", "--format", "json", "--no-interpolate", "--no-path-resolution", "--no-env-resolution"]
    )
    require(
        raw["services"]["backend"]["env_file"]
        == [
            {"path": "${NUEVAMENTE_BACKEND_ENV_FILE:?Required private backend.env path}", "required": True},
            {"path": "${NUEVAMENTE_ORIGIN_ENV_FILE:?Required canonical origin.env path}", "required": True},
        ],
        "compose-env-sources-changed",
    )
    require(not raw["services"]["frontend"].get("env_file"), "frontend-env-file-rejected")
    model = json_output([*args, "config", "--format", "json"], env=interpolation)
    validate_model(model, deployment, backend, env)
    return model


def validate_model(model, deployment, backend, env):
    require(
        model.get("name") == PROJECT and set(model.get("services", {})) == {"backend", "frontend"},
        "unexpected-compose-project",
    )
    for service, port in (("backend", 8000), ("frontend", 8501)):
        item = model["services"][service]
        require(
            item.get("ports")
            == [{"mode": "ingress", "host_ip": "127.0.0.1", "target": port, "published": str(port), "protocol": "tcp"}],
            "nonloopback-compose-binding",
        )
        require(
            item.get("read_only") is True
            and item.get("cap_drop") == ["ALL"]
            and item.get("security_opt") == ["no-new-privileges:true"],
            "container-hardening-changed",
        )
        require(item.get("tmpfs") == ["/tmp:rw,noexec,nosuid,size=128m,mode=1777"], "container-tmpfs-changed")
        require(item.get("restart") == "unless-stopped" and item.get("healthcheck"), "container-lifecycle-changed")
        require(
            not any(item.get(key) for key in ("privileged", "network_mode", "pid", "ipc", "devices", "cap_add")),
            "unsafe-container-mode",
        )
        require(set(item.get("networks", {})) == {"aplicacion"}, "container-network-changed")
        require(
            item.get("environment", {}).get("LANGSMITH_TRACING", "false").lower() not in {"true", "1"},
            "tracing-rejected",
        )
    network = model["networks"]["aplicacion"]
    require(
        network.get("driver") == "bridge" and not network.get("internal") and not network.get("enable_ipv6"),
        "compose-egress-network-changed",
    )
    require(
        network.get("driver_opts") == {"com.docker.network.bridge.host_binding_ipv4": "127.0.0.1"},
        "compose-network-options-changed",
    )
    b = model["services"]["backend"]
    require(b.get("stop_grace_period") == "1m10s", "backend-stop-grace-changed")
    loaded = b["environment"]
    same_secret(env["TRUSTED_ORIGIN_SECRET"], loaded.get("TRUSTED_ORIGIN_SECRET"))
    require(all(loaded.get(k) == v for k, v in backend.items()), "backend-effective-environment-inconsistent")
    for key, value in {
        "APP_ENV": "production",
        "MOCK_OCI": "0",
        "MOCK_GEMINI": "0",
        "DATA_DIR": "/app/.data",
        "FORWARDED_ALLOW_IPS": deployment["FORWARDED_ALLOW_IPS"],
        "NUEVAMENTE_DOMAIN": env["NUEVAMENTE_DOMAIN"],
        "OCI_CONFIG_FILE": "/run/oci/config",
        "OCI_CONFIG_PROFILE": "DOCKER",
        "OCI_AUTH_TYPE": "api_key",
    }.items():
        require(loaded.get(key) == value, "backend-effective-settings-changed")
    command = b.get("command", [])
    require(len(command) == 3 and command[:2] == ["python", "-c"], "backend-command-changed")
    mounts = b["volumes"]
    require(
        len(mounts) == 3
        and any(
            m["type"] == "volume" and m["source"] == "backend_data" and m["target"] == "/app/.data" for m in mounts
        ),
        "data-volume-changed",
    )
    oci = next((m for m in mounts if m["target"] == "/run/oci"), {})
    require(
        Path(oci.get("source", "")) == OCI_DIR
        and oci.get("read_only") is True
        and not oci.get("bind", {}).get("create_host_path", False),
        "oci-mount-changed",
    )
    ui = model["services"]["frontend"]
    require(not ui.get("volumes"), "frontend-mount-rejected")
    require(
        ui["environment"]
        == {
            "API_URL": "http://backend:8000",
            "API_HEALTH_WAIT_SECONDS": "60",
        },
        "frontend-effective-environment-changed",
    )
    require(model["volumes"]["backend_data"].get("name") == VOLUME, "stable-volume-name-changed")


def freeze_model(model):
    frozen = copy.deepcopy(model)
    for name, service in frozen["services"].items():
        image = json_output(["docker", "image", "inspect", f"{PROJECT}-{name}"])[0]
        require(
            image["Architecture"] == "arm64"
            and image["Os"] == "linux"
            and image["Config"]["User"] in {"10001", "10001:10001"},
            "image-platform-or-user-changed",
        )
        service["image"] = image["Id"]
        service.pop("build", None)
        service.pop("env_file", None)
        # Docker journald/console logging must not bypass filtered application
        # diagnostics during bootstrap/provider validation. Access logs are Caddy.
        service["logging"] = {"driver": "none"}
    frozen["services"]["backend"]["command"][2] = PEER_GATE + frozen["services"]["backend"]["command"][2]
    for mount in frozen["services"]["backend"]["volumes"]:
        if mount["target"] == "/app/.data":
            mount["volume"] = {"nocopy": True}
    frozen["volumes"]["backend_data"] = {"name": VOLUME, "external": True}
    return frozen


def volume_identity(volume):
    return {k: volume[k] for k in ("Name", "Driver", "Mountpoint", "CreatedAt", "Scope")}


def volume_preflight(*, deadline=None):
    actual = json_output(["docker", "volume", "inspect", VOLUME], deadline=deadline)[0]
    approved = json.loads(secure_path("/etc/nuevamente/volume.json").read_text())
    require(
        volume_identity(actual) == approved and actual["Driver"] == "local" and not actual.get("Options"),
        "unapproved-or-replaced-data-volume",
    )
    candidates = run(["docker", "volume", "ls", "--format", "{{.Name}}"], deadline=deadline).stdout.splitlines()
    require(
        not any(v.endswith("_backend_data") and v != VOLUME for v in candidates), "legacy-data-volume-requires-review"
    )
    ids = run(["docker", "ps", "-aq"], deadline=deadline).stdout.splitlines()
    containers = json_output(["docker", "inspect", *ids], deadline=deadline) if ids else []
    writers = []
    for item in containers:
        for mount in item.get("Mounts", []):
            if mount.get("Name") == VOLUME or mount.get("Destination") == "/app/.data":
                require(
                    mount.get("Name") == VOLUME
                    and item["Config"]["Labels"].get("com.docker.compose.project") == PROJECT,
                    "foreign-data-writer-rejected",
                )
                require(
                    item["Config"]["Labels"].get("com.docker.compose.service") == "backend", "unexpected-data-writer"
                )
                writers.append(item["Id"])
    require(len(set(writers)) <= 1, "multiple-data-writers-rejected")
    return volume_identity(actual)


def network_fingerprint(container, networks):
    attachments = container["NetworkSettings"]["Networks"]
    require(len(attachments) == 1, "unexpected-backend-network")
    ids = {item["NetworkID"] for item in attachments.values()}
    require(ids == {network["Id"] for network in networks}, "network-inspection-inconsistent")
    for network in networks:
        require(
            network["Driver"] == "bridge" and not network["Internal"] and not network["EnableIPv6"],
            "runtime-network-policy-changed",
        )
    return digest(
        json.dumps(
            {
                "container": container["Id"],
                "started": container["State"]["StartedAt"],
                "sandbox": container["NetworkSettings"]["SandboxID"],
                "attachments": attachments,
                "networks": [
                    {k: n[k] for k in ("Id", "Driver", "Internal", "EnableIPv6", "IPAM", "Options")} for n in networks
                ],
            },
            sort_keys=True,
        ).encode()
    )


def controlled_peer(peers=None):
    """Raw TCP challenge to the quarantined backend, before Uvicorn is imported."""
    nonce = os.urandom(32).hex()  # Probe nonce, never an origin secret/rotation.
    deadline = time.monotonic() + 60
    while True:
        try:
            connection = socket.create_connection(("127.0.0.1", 8000), timeout=2)
            break
        except OSError:
            require(time.monotonic() < deadline, "peer-probe-unavailable")
            time.sleep(0.2)
    with connection:
        connection.sendall((nonce + "\n").encode())
        data = b""
        while not data.endswith(b"\n") and len(data) <= 512:
            part = connection.recv(512)
            require(bool(part), "peer-probe-incomplete")
            data += part
        try:
            result = json.loads(data)
        except ValueError:
            raise Failure("peer-probe-invalid") from None
        require(result.get("nonce") == nonce, "peer-not-verified")
        address = ipaddress.ip_address(result.get("peer", ""))
        require(not address.is_unspecified and not address.is_multicast, "peer-not-verified")
        if peers is not None:
            require(result.get("peer") in peers, "peer-not-verified")
            require(set(peers) == {result["peer"]}, "unobserved-extra-proxy-peers")
        return result


def caddy_peer(peers=None):
    """Observe with Caddy's socket UID/GID, host namespace and loopback route."""
    import pwd

    account = pwd.getpwnam("caddy")
    uid, gid, groups = os.geteuid(), os.getegid(), os.getgroups()
    require(uid == 0, "peer-probe-requires-root-controller")
    try:
        os.setgroups([])
        os.setegid(account.pw_gid)
        os.seteuid(account.pw_uid)
        return controlled_peer(peers)
    finally:
        os.seteuid(uid)
        os.setegid(gid)
        os.setgroups(groups)


class HostOperations:
    def __init__(self, env):
        self.env = env
        self.model_path = STATE / "transaction-compose.json"
        self.expected_volume = None

    def compose(self, model, *args):
        atomic_write(self.model_path, json.dumps(model))
        return run(
            ["docker", "compose", "--project-name", PROJECT, "--env-file", "/dev/null", "-f", self.model_path, *args],
            timeout=180,
        )

    def service_container(self, service, *, deadline=None, allow_absent=False):
        """Only an empty successful listing is absence; inspection errors propagate."""
        ids = run(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                f"label=com.docker.compose.project={PROJECT}",
                "--filter",
                f"label=com.docker.compose.service={service}",
            ],
            deadline=deadline,
        ).stdout.splitlines()
        if not ids:
            require(allow_absent, service + "-container-absent")
            return None
        require(len(ids) == 1, service + "-container-not-unique")
        inspected = json_output(["docker", "inspect", ids[0]], deadline=deadline)
        require(
            isinstance(inspected, list) and len(inspected) == 1 and isinstance(inspected[0], dict),
            "container-inspection-inconsistent",
        )
        container = inspected[0]
        require(
            isinstance(container.get("Id"), str) and container["Id"].startswith(ids[0]),
            "container-inspection-identity-changed",
        )
        config = container.get("Config")
        require(isinstance(config, dict), "container-inspection-inconsistent")
        labels = config.get("Labels")
        require(isinstance(labels, dict), "container-inspection-inconsistent")
        require(
            labels.get("com.docker.compose.project") == PROJECT and labels.get("com.docker.compose.service") == service,
            "foreign-container-rejected",
        )
        return container

    def backend(self, *, deadline=None, allow_absent=False):
        return self.service_container("backend", deadline=deadline, allow_absent=allow_absent)

    def fingerprint(self, *, deadline=None):
        container = self.backend(deadline=deadline)
        networks = json_output(
            [
                "docker",
                "network",
                "inspect",
                *[n["NetworkID"] for n in container["NetworkSettings"]["Networks"].values()],
            ],
            deadline=deadline,
        )
        network = network_fingerprint(container, networks)
        routes = json_output(["ip", "-j", "-4", "route", "show", "table", "all"], deadline=deadline)
        rules = json_output(["ip", "-j", "-4", "rule", "show"], deadline=deadline)
        return digest(json.dumps({"docker": network, "routes": routes, "rules": rules}, sort_keys=True).encode())

    def start_backend(self, model):
        require(volume_preflight() == self.expected_volume, "data-volume-identity-changed-before-start")
        self.compose(model, "up", "-d", "--no-build", "--pull", "never", "--no-deps", "--force-recreate", "backend")
        before = self.fingerprint()
        approved = caddy_peer(model["services"]["backend"]["environment"]["FORWARDED_ALLOW_IPS"].split(","))
        require(self.fingerprint() == before, "network-changed-during-peer-probe")
        # Quarantine has not imported the application or written data. Detect a
        # replacement during recreation before allowing the first writer to run.
        require(volume_preflight() == self.expected_volume, "data-volume-identity-changed-before-release")
        # Root-owned marker; no environment or secret is sent over argv/stdout.
        code = (
            "import os,sys\n"
            "p='/tmp/nuevamente-proxy-ready'; t=p+'.pending'\n"
            "fd=os.open(t,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)\n"
            "with os.fdopen(fd,'wb') as f:\n"
            "    f.write(sys.stdin.buffer.read()); f.flush(); os.fsync(f.fileno()); os.fchmod(f.fileno(),0o644)\n"
            "os.link(t,p,follow_symlinks=False); os.unlink(t)\n"
        )
        run(
            ["docker", "exec", "--user", "0", "-i", self.backend()["Id"], "python", "-c", code],
            input=json.dumps(approved),
        )
        return before

    def start_frontend(self, model):
        self.compose(model, "up", "-d", "--no-build", "--pull", "never", "--no-deps", "--force-recreate", "frontend")

    def health(self, model, fingerprint):
        deadline = time.monotonic() + 90
        identities = None
        while True:
            require(time.monotonic() < deadline, "upstream-health-timeout")
            require(self.fingerprint(deadline=deadline) == fingerprint, "network-observation-invalidated")
            observations = self.effective_containers(model, allow_starting=True, deadline=deadline)
            current = {service: value["identity"] for service, value in observations.items()}
            if identities is None:
                identities = current
            require(current == identities, "container-observation-invalidated")
            http_ready = True
            for port, path in ((8000, "/api/health"), (8501, "/healthz")):
                try:
                    remaining = deadline - time.monotonic()
                    require(remaining > 0, "upstream-health-timeout")
                    with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}{path}", timeout=min(3, remaining)
                    ) as response:
                        require(response.status == 200, "upstream-health-failed")
                        if port == 8000:
                            try:
                                payload = json.loads(response.read(4096))
                            except ValueError:
                                raise Failure("backend-health-invalid") from None
                            require(
                                isinstance(payload, dict) and payload.get("status") == "ok", "backend-health-invalid"
                            )
                except OSError:
                    http_ready = False
            require(self.fingerprint(deadline=deadline) == fingerprint, "network-observation-invalidated")
            require(time.monotonic() < deadline, "upstream-health-timeout")
            if http_ready and all(value["health"] == "healthy" for value in observations.values()):
                break
            remaining = deadline - time.monotonic()
            require(remaining > 0, "upstream-health-timeout")
            time.sleep(min(0.5, remaining))
        final = self.effective_containers(model, deadline=deadline)
        require(
            {service: value["identity"] for service, value in final.items()} == identities,
            "container-observation-invalidated",
        )
        require(self.fingerprint(deadline=deadline) == fingerprint, "network-observation-invalidated")
        require(time.monotonic() < deadline, "upstream-health-timeout")

    def effective_containers(self, model, *, allow_starting=False, deadline=None):
        observations = {}
        for service, expected in model["services"].items():
            ids = run(
                [
                    "docker",
                    "ps",
                    "-aq",
                    "--filter",
                    f"label=com.docker.compose.project={PROJECT}",
                    "--filter",
                    f"label=com.docker.compose.service={service}",
                ],
                deadline=deadline,
            ).stdout.splitlines()
            require(len(ids) == 1, "runtime-container-not-unique")
            actual = json_output(["docker", "inspect", ids[0]], deadline=deadline)[0]
            config, host = actual["Config"], actual["HostConfig"]
            health = actual["State"].get("Health", {}).get("Status")
            require(
                actual["State"]["Running"] and health in (("starting", "healthy") if allow_starting else ("healthy",)),
                "container-not-healthy",
            )
            observations[service] = {
                "health": health,
                "identity": digest(
                    json.dumps(
                        {
                            "id": actual["Id"],
                            "started": actual["State"]["StartedAt"],
                            "network": actual["NetworkSettings"],
                        },
                        sort_keys=True,
                    ).encode()
                ),
            }
            require(
                actual["Image"] == expected["image"] and config["Cmd"] == expected.get("command", config["Cmd"]),
                "runtime-image-or-command-changed",
            )
            require(
                config["User"] in {"10001", "10001:10001"}
                and host["ReadonlyRootfs"]
                and host["CapDrop"] == ["ALL"]
                and not host["CapAdd"]
                and not host["Privileged"],
                "runtime-container-hardening-changed",
            )
            require(
                host["SecurityOpt"] == ["no-new-privileges:true"] and host["LogConfig"]["Type"] == "none",
                "runtime-container-security-or-logging-changed",
            )
            require(
                host["Tmpfs"] == {"/tmp": "rw,noexec,nosuid,size=128m,mode=1777"}
                and host["RestartPolicy"]["Name"] == "unless-stopped",
                "runtime-tmpfs-or-restart-changed",
            )
            require(config["Healthcheck"]["Test"] == expected["healthcheck"]["test"], "runtime-healthcheck-changed")
            port = "8000" if service == "backend" else "8501"
            require(
                host["PortBindings"] == {port + "/tcp": [{"HostIp": "127.0.0.1", "HostPort": port}]},
                "runtime-public-binding-rejected",
            )
            values = dict(value.split("=", 1) for value in config["Env"])
            require(
                all(values.get(k) == str(v) for k, v in expected["environment"].items()),
                "runtime-container-environment-inconsistent",
            )
            if service == "backend":
                same_secret(self.env["TRUSTED_ORIGIN_SECRET"], values.get("TRUSTED_ORIGIN_SECRET"))
                loaded = json_output(
                    [
                        "docker",
                        "exec",
                        "--user",
                        "0",
                        ids[0],
                        "python",
                        "-c",
                        "import json; print(json.dumps(dict(x.decode().split('=',1) for x in open('/proc/1/environ','rb').read().split(b'\\0') if x)))",
                    ],
                    deadline=deadline,
                )
                same_secret(self.env["TRUSTED_ORIGIN_SECRET"], loaded.get("TRUSTED_ORIGIN_SECRET"))
                require(
                    all(loaded.get(k) == str(v) for k, v in expected["environment"].items()),
                    "runtime-backend-process-environment-changed",
                )
                mounts = {m["Destination"]: m for m in actual["Mounts"] if m["Type"] != "tmpfs"}
                require(set(mounts) == {"/app/.data", "/app/documents", "/run/oci"}, "runtime-backend-mounts-changed")
                require(
                    mounts["/app/.data"]["Name"] == VOLUME
                    and mounts["/app/.data"]["RW"]
                    and not mounts["/run/oci"]["RW"]
                    and mounts["/run/oci"]["Source"] == str(OCI_DIR)
                    and not mounts["/app/documents"]["RW"],
                    "runtime-backend-volume-or-credentials-changed",
                )
            else:
                require(
                    not any(k.startswith(("OCI_", "GOOGLE_", "TRUSTED_ORIGIN_", "FORWARDED_")) for k in values),
                    "frontend-runtime-credential-rejected",
                )
                require(not any(m["Type"] != "tmpfs" for m in actual["Mounts"]), "frontend-runtime-mount-rejected")
        volume_preflight(deadline=deadline)
        return observations

    def caddy_active(self):
        return run(["systemctl", "is-active", "--quiet", "caddy"], ok=(0, 3)).returncode == 0

    def caddy_environment(self):
        pid = run(["systemctl", "show", "caddy", "--property=MainPID", "--value"]).stdout.strip()
        require(pid.isdecimal() and int(pid) > 1, "caddy-main-pid-invalid")
        children = (Path("/proc") / pid / "task" / pid / "children").read_text().split()
        require(len(children) == 1 and children[0].isdecimal(), "caddy-child-process-not-unique")
        child = Path("/proc") / children[0]
        require((child / "exe").resolve() == Path("/usr/bin/caddy"), "caddy-child-binary-changed")
        for process in (Path("/proc") / pid, child):
            values = dict(x.decode().split("=", 1) for x in (process / "environ").read_bytes().split(b"\0") if x)
            same_secret(self.env["TRUSTED_ORIGIN_SECRET"], values.get("TRUSTED_ORIGIN_SECRET"))
            require(
                all(values.get(k) == v for k, v in self.env.items()),
                "caddy-process-environment-changed-restart-required",
            )

    def activate_caddy(self, was_active):
        run(["systemctl", "reload" if was_active else "start", "caddy"], timeout=120)

    def verify_caddy(self, adapted):
        import pwd

        require(self.caddy_active(), "caddy-not-active")
        self.caddy_environment()
        account = pwd.getpwnam("caddy")
        secure_path("/run/caddy", uid=account.pw_uid, gid=account.pw_gid, mode=0o700, directory=True)
        info = Path("/run/caddy/admin.sock").lstat()
        require(
            stat.S_ISSOCK(info.st_mode)
            and info.st_uid == account.pw_uid
            and info.st_gid == account.pw_gid
            and stat.S_IMODE(info.st_mode) == 0o200,
            "caddy-admin-socket-not-protected",
        )
        connection = http.client.HTTPConnection("localhost", timeout=3)
        connection.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.sock.settimeout(3)
        try:
            connection.sock.connect("/run/caddy/admin.sock")
            connection.request("GET", "/config/")
            response = connection.getresponse()
            require(response.status == 200, "caddy-admin-check-failed")
            require(json.loads(response.read(2_000_000)) == adapted, "caddy-effective-configuration-changed")
        finally:
            connection.close()
        output = run(["ss", "-H", "-lntu"]).stdout
        listeners(output)
        bound = {line.split()[4].rsplit(":", 1)[-1] for line in output.splitlines() if line.split()[0] == "tcp"}
        require({"80", "443"} <= bound, "caddy-public-tcp-listeners-missing")

    def stop_new(self, model):
        run(["systemctl", "stop", "caddy"])
        self.compose(model, "stop", "frontend", "backend")

    def emergency_stop(self):
        run(["systemctl", "stop", "caddy"])

    def state_report(self):
        # Only enum states/counts, never process/container names, logs or env.
        try:
            active = self.caddy_active()
        except Failure:
            active = "unknown"
        try:
            running = len(
                run(
                    ["docker", "ps", "-q", "--filter", f"label=com.docker.compose.project={PROJECT}"]
                ).stdout.splitlines()
            )
        except Failure:
            running = "unknown"
        print(json.dumps({"caddy_active": active, "project_running_containers": running}))


def provider_validation(model, volume):
    """Use the actual Settings validators in the built image; network disabled."""
    env = model["services"]["backend"]["environment"]
    command = model["services"]["backend"]["command"][2]
    require(command.startswith(PEER_GATE), "compiled-peer-gate-missing")
    guard = command[len(PEER_GATE) :]
    # Execute the actual startup guard in the built image. Only execvp is
    # substituted, proving the launch contract without starting Uvicorn/providers.
    code = (
        "import os,sys\n"
        "launch_checked=0\n"
        "def verified_launch(executable,args):\n"
        "    global launch_checked\n"
        "    launch_checked+=1\n"
        "    assert executable==sys.executable\n"
        "    assert args==[sys.executable,'-m','uvicorn','app.main:app','--host','0.0.0.0','--port','8000','--workers','1','--proxy-headers','--no-access-log']\n"
        "os.execvp=verified_launch\n"
        "exec(" + repr(guard) + ")\n"
        "assert launch_checked==1\n"
        "from app.config import Configuracion\n"
        "c=Configuracion(_env_file=None); c.validar_critico()\n"
        "assert all(getattr(c,p+'_quota_'+s) is not None for p in ('embedding','vision','generation','verification') for s in ('rpm','tpm','rpd'))\n"
        "import oci, hashlib\n"
        "from cryptography.hazmat.primitives import serialization\n"
        "profile=oci.config.from_file('/run/oci/config','DOCKER'); oci.config.validate_config(profile)\n"
        "key=oci.signer.load_private_key_from_file(profile['key_file'],None)\n"
        "public=key.public_key().public_bytes(serialization.Encoding.DER,serialization.PublicFormat.SubjectPublicKeyInfo)\n"
        "fingerprint=':'.join(hashlib.md5(public,usedforsecurity=False).hexdigest()[i:i+2] for i in range(0,32,2))\n"
        "assert fingerprint==profile['fingerprint']\n"
        "models={}\n"
        "for name,prefix in ((c.gemini_generation_model,'generation'),(c.gemini_generation_model,'vision'),(c.gemini_verification_model,'verification'),(c.gemini_embedding_model,'embedding')):\n"
        "    assert name.strip()\n"
        "    quotas=tuple(getattr(c,prefix+'_quota_'+s) for s in ('rpm','tpm','rpd'))\n"
        "    assert name not in models or models[name]==quotas\n"
        "    models[name]=quotas\n"
    )
    with tempfile.TemporaryDirectory(prefix="provider-check-", dir=STATE) as directory:
        path = Path(directory) / "backend.env"
        atomic_write(path, "".join(f"{k}={v}\n" for k, v in env.items()))
        run(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--log-driver",
                "none",
                "--user",
                "10001:10001",
                "--env-file",
                path,
                "--mount",
                "type=bind,src=/etc/nuevamente/oci,dst=/run/oci,readonly",
                "--entrypoint",
                "python",
                model["services"]["backend"]["image"],
                "-c",
                code,
            ]
        )
    # No copy-up, provider access or writer process during the data permission check.
    run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--log-driver",
            "none",
            "--user",
            "10001:10001",
            "--mount",
            # A named mount would implicitly create an empty volume in a race.
            f"type=bind,src={volume['Mountpoint']},dst=/app/.data,readonly",
            "--entrypoint",
            "python",
            model["services"]["backend"]["image"],
            "-c",
            "import os,stat; s=os.stat('/app/.data'); assert s.st_uid==10001 and s.st_gid==10001 and stat.S_IMODE(s.st_mode)==0o700; assert os.access('/app/.data',os.R_OK|os.X_OK)",
        ]
    )


def validate_caddy_candidate(data, env):
    import pwd

    require(env["TRUSTED_ORIGIN_SECRET"].encode() not in data, "literal-origin-secret-rejected")
    account = pwd.getpwnam("caddy")
    with tempfile.TemporaryDirectory(prefix=".validate-", dir=CADDYFILE.parent) as directory:
        os.chown(directory, 0, account.pw_gid)
        os.chmod(directory, 0o750)
        path = Path(directory) / "Caddyfile"
        atomic_write(path, data, mode=0o640, gid=account.pw_gid)
        caddy_cli("validate", path, env=env)
        adapted = json.loads(caddy_cli("adapt", path, env=env).stdout)
        require(env["TRUSTED_ORIGIN_SECRET"] not in json.dumps(adapted), "adapted-secret-rejected")
        return adapted


def restore_transaction(journal, operations, publish, delete):
    """Recovery never restores/migrates data, secrets or ACME storage."""
    # A failed candidate may already have published. Never recreate its previous
    # backend while the public proxy is still active. The real journal survives.
    operations.emergency_stop()
    previous = journal["previous"]
    operations.expected_volume = (previous or journal["candidate"])["volume"]
    if previous is None:
        operations.stop_new(journal["candidate"]["model"])
        delete()
        return
    publish(previous["caddyfile"].encode())
    fingerprint = operations.start_backend(previous["model"])
    operations.start_frontend(previous["model"])
    operations.health(previous["model"], fingerprint)
    operations.activate_caddy(operations.caddy_active())
    operations.verify_caddy(previous["adapted"])
    previous["fingerprint"] = fingerprint
    return previous


def transaction(candidate, operations, state_dir=STATE, *, publish=None, delete=None, reprobe=False):
    if publish is None:
        import pwd

        account = pwd.getpwnam("caddy")
    publish = publish or (lambda data: atomic_write(CADDYFILE, data, mode=0o640, gid=account.pw_gid))
    delete = delete or (lambda: CADDYFILE.unlink(missing_ok=True))
    current_path, pending = state_dir / "current.json", state_dir / "pending.json"
    require(not pending.exists(), "interrupted-deployment-requires-recovery")
    previous = json.loads(current_path.read_text()) if current_path.exists() else None
    if previous is not None:
        require(previous["volume"] == candidate["volume"], "data-volume-identity-changed")
        if not reprobe:
            operations.health(previous["model"], previous["fingerprint"])
            operations.verify_caddy(previous["adapted"])
    journal = {"previous": previous, "candidate": candidate}
    atomic_write(pending, json.dumps(journal))
    try:
        operations.expected_volume = candidate["volume"]
        if reprobe:
            operations.emergency_stop()
        fingerprint = operations.start_backend(candidate["model"])
        operations.start_frontend(candidate["model"])
        operations.health(candidate["model"], fingerprint)
        publish(candidate["caddyfile"].encode())
        operations.activate_caddy(operations.caddy_active())
        operations.verify_caddy(candidate["adapted"])
        candidate["fingerprint"] = fingerprint
        atomic_write(current_path, json.dumps(candidate))
        pending.unlink()
    except Exception:
        # Catch system-boundary failures as well; do not print exception content.
        try:
            restored = restore_transaction(journal, operations, publish, delete)
            if restored is not None:
                atomic_write(current_path, json.dumps(restored))
            else:
                current_path.unlink(missing_ok=True)
            pending.unlink()
        except Exception:
            with contextlib.suppress(Exception):
                operations.emergency_stop()
            operations.state_report()
            raise Failure("deployment-and-recovery-failed; pending-journal-retained; data-not-rolled-back") from None
        operations.state_report()
        raise Failure("deployment-failed; prior-service-state-restored; data-not-rolled-back") from None


def deploy(root, *, activate=False, recover=False, observation=False):
    platform_check()
    with lock("/run/lock/nuevamente-caddy.lock"):
        host_preflight()
        secure_path(STATE, mode=0o700, directory=True)
        env, deployment, backend = environment_files(root, observation=observation)
        operations = HostOperations(env)
        if operations.caddy_active():
            operations.caddy_environment()  # Normal deployment cannot rotate secrets.
        if recover:
            pending = secure_path(STATE / "pending.json")
            journal = json.loads(pending.read_text())
            try:
                import pwd

                account = pwd.getpwnam("caddy")
                restored = restore_transaction(
                    journal,
                    operations,
                    lambda data: atomic_write(CADDYFILE, data, mode=0o640, gid=account.pw_gid),
                    lambda: CADDYFILE.unlink(missing_ok=True),
                )
                if restored is not None:
                    atomic_write(STATE / "current.json", json.dumps(restored))
                else:
                    (STATE / "current.json").unlink(missing_ok=True)
                pending.unlink()
            except Exception:
                with contextlib.suppress(Exception):
                    operations.emergency_stop()
                operations.state_report()
                raise Failure("recovery-failed; pending-journal-retained; data-not-rolled-back") from None
            print("prior-service-state-restored; data-not-rolled-back")
            return
        require(not (STATE / "pending.json").exists(), "interrupted-deployment-requires-recovery")
        volume = volume_preflight()
        operations.expected_volume = volume
        if observation:
            require(
                not (STATE / "current.json").exists() and not CADDYFILE.exists() and not operations.caddy_active(),
                "initial-peer-observation-only",
            )
            require(
                not run(
                    ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"]
                ).stdout.strip(),
                "initial-observation-project-not-empty",
            )
            # A non-IP sentinel satisfies native Compose interpolation only. It
            # cannot pass the Uvicorn guard or release the gate; no trust implied.
            deployment = deployment | {"FORWARDED_ALLOW_IPS": "unverified"}
            model = freeze_model(compose_configuration(root, deployment, backend, env))
            atomic_write(STATE / "peer-probe-compose.json", json.dumps(model))
            try:
                operations.compose(model, "up", "-d", "--no-build", "--pull", "never", "--no-deps", "backend")
                fingerprint = operations.fingerprint()
                peer = caddy_peer()
                require(operations.fingerprint() == fingerprint, "network-changed-during-peer-probe")
                atomic_write(
                    STATE / "observed-peer.json",
                    json.dumps({"peer": peer["peer"], "fingerprint": fingerprint, "observed_at": time.time()}),
                )
            finally:
                # No app imports, data writes, release marker or public Caddy.
                operations.compose(model, "stop", "backend")
                operations.compose(model, "rm", "-f", "backend")
            print("initial-peer-observed; read-private-observed-peer-file; configure-exact-peer-before-activation")
            return
        model = freeze_model(compose_configuration(root, deployment, backend, env))
        provider_validation(model, volume)
        data = (root / "infra/caddy/Caddyfile").read_bytes()
        adapted = validate_caddy_candidate(data, env)
        current = STATE / "current.json"
        reprobe = False
        if current.exists():
            prior = json.loads(secure_path(current).read_text())
            same_secret(
                env["TRUSTED_ORIGIN_SECRET"],
                prior["model"]["services"]["backend"]["environment"].get("TRUSTED_ORIGIN_SECRET"),
            )
            import pwd

            account = pwd.getpwnam("caddy")
            require(
                secure_path(CADDYFILE, gid=account.pw_gid, mode=0o640).read_text() == prior["caddyfile"],
                "published-caddyfile-changed",
            )
            if operations.backend(allow_absent=True) is None:
                require(activate, "backend-container-absent; explicit-activation-required")
                # No implicit installer upgrade: the reviewed executing helper
                # must be the exact one covered by the managed installation.
                manifest = managed_installation()
                require(
                    manifest["files"][str(HELPER)] == digest(Path(__file__).read_bytes()),
                    "backend-recreation-requires-installed-helper",
                )
                require(
                    prior["model"] == model and prior["caddyfile"] == data.decode() and prior["adapted"] == adapted,
                    "backend-recreation-configuration-changed",
                )
                require(prior["volume"] == volume, "data-volume-identity-changed")
                operations.service_container("frontend", allow_absent=True)
                reprobe = True
            else:
                reprobe = prior["fingerprint"] != operations.fingerprint()
            require(activate or not reprobe, "network-observation-invalidated; explicit-activation-required")
            if not reprobe:
                operations.health(prior["model"], prior["fingerprint"])
                operations.verify_caddy(prior["adapted"])
        else:
            require(not CADDYFILE.exists() and not operations.caddy_active(), "unmanaged-proxy-configuration")
            require(
                not run(
                    ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={PROJECT}"]
                ).stdout.strip(),
                "unmanaged-compose-deployment",
            )
        if not activate:
            print("configuration-validated; activation-required; peer-observation-will-precede-uvicorn")
            return
        transaction(
            {"model": model, "caddyfile": data.decode(), "adapted": adapted, "volume": volume},
            operations,
            reprobe=reprobe,
        )
        print("deployment-activated-and-locally-verified; public-acceptance-pending")


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        raise Failure("invalid-command-arguments; consult-runbook")


def main(argv=None):
    parser = SafeParser(description="Issue 48 host lifecycle; no raw diagnostics")
    commands = parser.add_subparsers(dest="action", required=True, parser_class=SafeParser)
    install_parser = commands.add_parser("install")
    install_parser.add_argument("--artifacts", type=Path, required=True)
    install_parser.add_argument("--cosign", type=Path, required=True)
    install_parser.add_argument("--source", type=Path, default=Path(__file__).parent)
    deploy_parser = commands.add_parser("deploy")
    deploy_parser.add_argument("--checkout", type=Path, required=True)
    modes = deploy_parser.add_mutually_exclusive_group()
    modes.add_argument("--validate", action="store_true")
    modes.add_argument("--activate", action="store_true")
    modes.add_argument("--recover", action="store_true")
    modes.add_argument("--observe-peer", action="store_true")
    for action in ("caddy-validate", "caddy-reload", "caddy-run"):
        commands.add_parser(action)
    try:
        args = parser.parse_args(argv)
        if args.action == "install":
            install(args.artifacts, args.cosign, args.source)
        elif args.action == "deploy":
            deploy(args.checkout.resolve(), activate=args.activate, recover=args.recover, observation=args.observe_peer)
        elif args.action == "caddy-run":
            return caddy_run()
        else:
            caddy_cli("validate")
            if args.action == "caddy-reload":
                caddy_cli("reload")
            print(
                "caddy-configuration-validated" if args.action == "caddy-validate" else "caddy-configuration-reloaded"
            )
    except Failure as failure:
        print(str(failure), file=sys.stderr)
        return 1
    except Exception:
        print("lifecycle-operation-failed; no-private-diagnostics-emitted", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
