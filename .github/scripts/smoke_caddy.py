"""Required Linux Caddy smoke; no daemons, providers, skip or version fallback.

Tool digests are copied from official release manifests (Caddy's signed
2.11.7 SHA512 manifest, Compose 2.40.3 SHA256 manifest). Extraction accepts
only the single expected executable, never arbitrary archive paths.
"""

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CADDY_HASHES = {
    "amd64": "a7a433a1b133efc3c8d10eb0b99d52a24b5ef5c322dc77f5282182b1c0402139ab83f3a99f0c52409df77d20123fb0b523edad8a66d8f5e49136197bf61ef0e7",
    "arm64": "3db36ba90c7a6e8dda40ee3dd71fa08844c76b5fb08f61b31e5e78d2ed38e71c51dc7baed875e50d1ca1279196e84302967237386ae87c91ae9f2aaceada682e",
}
COMPOSE_HASHES = {
    "x86_64": "dba9d98e1ba5bfe11d88c99b9bd32fc4a0624a30fafe68eea34d61a3e42fd372",
    "aarch64": "d26373b19e89160546d15407516cc59f453030d9bc5b43ba7faf16f7b4980137",
}

# Reviewed pytest collection, including parameter identities, per module. Hashes
# contain no parameters or private captures. Refresh deliberately after reviewing
# a changed collection, never from the execution report being checked. Exact
# coverage also rejects duplicates and additions that replace a required case.
REQUIRED_FAMILIES = {
    "backend.tests.test_caddy_config": (
        89,
        "b03ba96180cdd1fbfbd7933fb059d6bec72ea9cfb4eebec178fef87f37702408",
    ),
    "backend.tests.test_caddy_deploy": (
        142,
        "a0d05acde761c16a23daca2e1e48d78bd33acaf86cea5a13998ff74780659225",
    ),
    "backend.tests.test_caddy_smoke": (
        43,
        "591943b8a7c4c9c39991c9efa3c68eb2b5d5a917113c0b465a1d117efe0273b7",
    ),
    "backend.tests.integration.test_caddy_proxy": (
        10,
        "6dce368434b44737ae8069b22c9fb77b55fd0ec1fbe0b9a27775be6b9438b9af",
    ),
}


def identity_digest(names):
    """Canonical multiset of complete pytest names, not a total-case heuristic."""
    encoded = json.dumps(sorted(names), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def require_coverage(cases):
    families = {family: [] for family in REQUIRED_FAMILIES}
    for case in cases:
        family = case.get("classname", "")
        assert family in families, "mandatory-tests-unexpected-family"
        families[family].append(case.get("name", ""))
    for family, (count, digest) in REQUIRED_FAMILIES.items():
        names = families[family]
        assert len(names) == count, "mandatory-tests-family-count-mismatch:" + family
        assert len(set(names)) == count, "mandatory-tests-duplicate-identity:" + family
        assert identity_digest(names) == digest, "mandatory-tests-identities-mismatch:" + family


def fetch(url, destination, algorithm, expected):
    request = urllib.request.Request(url, headers={"User-Agent": "NuevaMente-Issue48-CI"})
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("xb") as output:
        digest, size = hashlib.new(algorithm), 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            assert size <= 150 * 1024 * 1024, "tool-download-size-exceeded"
            digest.update(chunk)
            output.write(chunk)
    assert digest.hexdigest() == expected, "official-tool-digest-mismatch"


def prepare(directory):
    assert sys.platform == "linux", "CI-native-tools-require-Linux"
    machine = platform.machine()
    assert machine in COMPOSE_HASHES, "unsupported-native-architecture"
    arch = "amd64" if machine == "x86_64" else "arm64"
    directory.mkdir(parents=True, exist_ok=False)
    directory.chmod(0o700)
    archive = directory / "caddy.tar.gz"
    fetch(
        f"https://github.com/caddyserver/caddy/releases/download/v2.11.7/caddy_2.11.7_linux_{arch}.tar.gz",
        archive,
        "sha512",
        CADDY_HASHES[arch],
    )
    with tarfile.open(archive) as tar:
        member = tar.getmember("caddy")
        assert member.isfile() and member.size <= 150 * 1024 * 1024, "invalid-caddy-member"
        with tar.extractfile(member) as source, (directory / "caddy").open("xb") as output:
            output.write(source.read())
    fetch(
        f"https://github.com/docker/compose/releases/download/v2.40.3/docker-compose-linux-{machine}",
        directory / "docker-compose",
        "sha256",
        COMPOSE_HASHES[machine],
    )
    for name in ("caddy", "docker-compose"):
        (directory / name).chmod(0o700)
    assert subprocess.check_output([directory / "caddy", "version"], text=True).split()[0] == "v2.11.7"
    assert subprocess.check_output([directory / "docker-compose", "version", "--short"], text=True).strip() == "2.40.3"
    print(json.dumps({"tools": ["Caddy 2.11.7", "Compose 2.40.3"], "platform": machine, "digests_verified": True}))


def dependencies():
    pins = {}
    for line in (ROOT / ".github/requirements-caddy.txt").read_text().splitlines():
        if line and not line.startswith("#"):
            name, version = line.split("==")
            pins[name] = version
            assert importlib.metadata.version(name) == version, "required-proxy-dependency-version-mismatch:" + name
    lock = (ROOT / ".github/requirements-caddy.lock").read_text()
    locked = {
        line.split("==")[0]: line.split("==")[1].split()[0]
        for line in lock.splitlines()
        if "==" in line and not line.startswith("#")
    }
    assert pins == locked, "hashed-lock-and-pins-disagree"


def publish_report(raw, destination):
    """Preserve all outcomes; keep raw failure locals/capture out of artifacts."""
    cases = ET.parse(raw).findall(".//testcase")
    root = ET.Element("testsuites")
    suite = ET.SubElement(root, "testsuite", name="Caddy required", tests=str(len(cases)))
    totals = {kind: 0 for kind in ("failure", "error", "skipped")}
    for index, case in enumerate(cases):
        name = case.get("name", "").split("[", 1)[0]
        assert re.fullmatch(r"test_[A-Za-z0-9_]+", name), "unexpected-test-identity"
        protocol = next(
            (label for label in ("http1", "http2") if case.get("name", "").endswith("[" + label + "]")), None
        )
        label = protocol or "case-" + str(index)
        clean = ET.SubElement(
            suite,
            "testcase",
            name=name + "[" + label + "]",
            classname=case.get("classname", "").split(".")[-1],
            time=case.get("time", "0"),
        )
        for kind in totals:
            if case.find(kind) is not None:
                totals[kind] += 1
                ET.SubElement(clean, kind, message="Required check " + kind + "; inspect private local report")
    suite.set("failures", str(totals["failure"]))
    suite.set("errors", str(totals["error"]))
    suite.set("skipped", str(totals["skipped"]))
    ET.ElementTree(root).write(destination, encoding="utf-8", xml_declaration=True)
    return cases


def run(evidence):
    assert os.environ.get("CADDY_BIN") and os.environ.get("COMPOSE_BIN"), "native-tools-required"
    dependencies()
    evidence.mkdir(parents=True, exist_ok=True)
    report = evidence / "required-tests.xml"
    raw = evidence / "required-tests.private.xml"
    env = dict(os.environ, NM_CADDY_EVIDENCE_DIR=str(evidence.resolve()), MOCK_GEMINI="1", MOCK_OCI="1", APP_ENV="test")
    # pytest retains failure exit status. XML also prevents a green result from
    # empty selection or skipped mandatory tool/configuration/browser tests.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-o",
            "addopts=",
            "-q",
            "--tb=short",
            "backend/tests/test_caddy_config.py",
            "backend/tests/test_caddy_deploy.py",
            "backend/tests/test_caddy_smoke.py",
            "backend/tests/integration/test_caddy_proxy.py",
            "--junitxml=" + str(raw),
        ],
        cwd=ROOT,
        env=env,
    )
    assert raw.is_file(), "required-test-report-absent"
    cases = publish_report(raw, report)
    assert not any(case.find("skipped") is not None for case in cases), "mandatory-tests-skipped"
    assert not any(case.find("failure") is not None or case.find("error") is not None for case in cases), (
        "required-caddy-tests-failed"
    )
    require_coverage(cases)
    assert result.returncode == 0, "required-caddy-tests-failed"
    print(
        json.dumps(
            {
                "platform": platform.platform(),
                "architecture": platform.machine(),
                "required_cases": len(cases),
                "skips": 0,
                "public_acceptance": False,
            }
        )
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--prepare-tools", type=Path)
    parser.add_argument("--evidence", type=Path, default=ROOT / ".data/caddy-evidence")
    args = parser.parse_args()
    if args.prepare_tools:
        prepare(args.prepare_tools)
    else:
        run(args.evidence)


if __name__ == "__main__":
    main()
