"""Artifact sanitization preserves failures/errors/skips, never raw credentials."""

import importlib
import importlib.util
import json
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("caddy_smoke", ROOT / ".github/scripts/smoke_caddy.py")
smoke = importlib.util.module_from_spec(spec)
spec.loader.exec_module(smoke)


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped"])
def test_public_report_preserves_outcome_without_private_capture(tmp_path, outcome):
    secret = "synthetic-private-report-canary"
    raw, public = tmp_path / "private.xml", tmp_path / "public.xml"
    raw.write_text(
        f'<testsuites><testsuite><testcase classname="test_caddy_proxy" name="test_probe[{secret}]" time="1.2">'
        f'<{outcome} message="{secret}">{secret}</{outcome}><system-out>{secret}</system-out>'
        "</testcase></testsuite></testsuites>"
    )
    original = smoke.publish_report(raw, public)
    assert len(original) == 1 and original[0].find(outcome) is not None
    clean = public.read_text()
    assert secret not in clean
    tree = ET.parse(public)
    assert tree.find(".//testcase").find(outcome) is not None
    assert tree.find(".//testcase").get("time") == "1.2"


def test_public_report_preserves_protocol_identity_and_success(tmp_path):
    raw, public = tmp_path / "private.xml", tmp_path / "public.xml"
    raw.write_text(
        '<testsuites><testsuite><testcase classname="test_caddy_proxy" '
        'name="test_probe[http2]" time="0.2"/></testsuite></testsuites>'
    )
    smoke.publish_report(raw, public)
    case = ET.parse(public).find(".//testcase")
    assert case.get("name") == "test_probe[http2]"
    assert not list(case)


@pytest.fixture
def smoke_report_double(monkeypatch):
    """Synthetic JUnit/process/coverage doubles; never a real proxy smoke."""
    families = {
        "backend.tests.test_caddy_config": 89,
        "backend.tests.test_caddy_deploy": 142,
        "backend.tests.test_caddy_smoke": 29,
        "backend.tests.integration.test_caddy_proxy": 10,
    }
    cases = []
    for family, count in families.items():
        cases.extend(ET.Element("testcase", classname=family, name=f"test_required_{i}") for i in range(count))
    # Small, explicit identities stand in for the reviewed collection in these
    # algorithm tests. Production's manifest is never derived from its report.
    monkeypatch.setattr(
        smoke,
        "REQUIRED_FAMILIES",
        {
            family: (count, smoke.identity_digest([c.get("name") for c in cases if c.get("classname") == family]))
            for family, count in families.items()
        },
    )
    monkeypatch.setenv("CADDY_BIN", "synthetic-tool")
    monkeypatch.setenv("COMPOSE_BIN", "synthetic-tool")
    monkeypatch.setattr(smoke, "dependencies", lambda: None)
    state = SimpleNamespace(cases=cases, returncode=0, calls=0)

    def process_double(arguments, **kwargs):
        state.calls += 1
        destination = next(arg.split("=", 1)[1] for arg in arguments if arg.startswith("--junitxml="))
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite")
        suite.extend(state.cases)
        ET.ElementTree(root).write(destination, encoding="utf-8")
        return SimpleNamespace(returncode=state.returncode)

    monkeypatch.setattr(smoke.subprocess, "run", process_double)
    return state


@pytest.mark.parametrize("family", ["config", "deploy", "smoke", "proxy"])
def test_smoke_rejects_missing_family_despite_sufficient_total(tmp_path, smoke_report_double, family):
    state = smoke_report_double
    absent = next(c.get("classname") for c in state.cases if c.get("classname").endswith("test_caddy_" + family))
    state.cases = [c for c in state.cases if c.get("classname") != absent]
    # Missing report/diagnostics leaves exactly 241 cases and ten proxy cases:
    # the supervisor's reproduction. Other families get compensating extras.
    target = next(c.get("classname") for c in state.cases if "proxy" not in c.get("classname"))
    for i in range(max(0, 241 - len(state.cases))):
        state.cases.append(ET.Element("testcase", classname=target, name=f"test_extra_{i}"))
    assert len(state.cases) >= 241
    with pytest.raises(AssertionError, match="mandatory-tests"):
        smoke.run(tmp_path)
    assert len(ET.parse(tmp_path / "required-tests.xml").findall(".//testcase")) == len(state.cases)
    assert state.calls == 1


@pytest.mark.parametrize("family", ["config", "deploy", "smoke", "proxy"])
def test_smoke_rejects_substituted_identity_with_unchanged_counts(tmp_path, smoke_report_double, family):
    case = next(c for c in smoke_report_double.cases if c.get("classname").endswith("test_caddy_" + family))
    case.set("name", "test_unrelated_replacement[extra]")
    with pytest.raises(AssertionError, match="mandatory-tests"):
        smoke.run(tmp_path)
    assert (tmp_path / "required-tests.xml").is_file()


def test_smoke_rejects_duplicate_identity(tmp_path, smoke_report_double):
    cases = smoke_report_double.cases
    cases[1].set("name", cases[0].get("name"))
    with pytest.raises(AssertionError, match="mandatory-tests"):
        smoke.run(tmp_path)


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped"])
def test_smoke_rejects_and_preserves_red_report_even_with_zero_exit(tmp_path, smoke_report_double, outcome):
    secret = "synthetic-private-report-canary"
    ET.SubElement(smoke_report_double.cases[0], outcome, message=secret).text = secret
    with pytest.raises(AssertionError):
        smoke.run(tmp_path)
    public = tmp_path / "required-tests.xml"
    assert ET.parse(public).find(".//" + outcome) is not None
    assert secret not in public.read_text()
    assert secret in (tmp_path / "required-tests.private.xml").read_text()


def test_smoke_rejects_nonzero_process_exit_with_complete_report(tmp_path, smoke_report_double):
    smoke_report_double.returncode = 1
    with pytest.raises(AssertionError, match="required-caddy-tests-failed"):
        smoke.run(tmp_path)
    assert (tmp_path / "required-tests.xml").is_file()


def test_smoke_accepts_complete_successful_identity_contract(tmp_path, smoke_report_double, capsys):
    smoke.run(tmp_path)
    result = json.loads(capsys.readouterr().out)
    assert result["required_cases"] == 270
    assert result["skips"] == 0 and result["public_acceptance"] is False


@pytest.fixture
def diagnostic_harness(tmp_path, monkeypatch):
    # Explicit clock/HTTP doubles execute the real wait_terminal(), not Caddy.
    monkeypatch.syspath_prepend(str(ROOT / "backend/tests/integration"))
    module = importlib.import_module("caddy_fixture")
    harness = object.__new__(module.ProxyHarness)
    harness.directory = tmp_path
    harness.evidence = {"checks": []}
    harness.owner = {"token": "synthetic-private-report-canary"}
    harness.ports = {"backend_port": 12345}
    harness.query_sequence = 0
    harness.provider_releases = []
    harness.diagnostic_mode = "profile"
    monkeypatch.setattr(harness, "export_profile", lambda: {"transport_double": True})
    clock = SimpleNamespace(value=100.0)

    def sleep(seconds):
        clock.value += seconds

    monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=lambda: clock.value, sleep=sleep))
    return module, harness, clock


def test_terminal_diagnostics_record_queries_and_provider_release(diagnostic_harness):
    module, harness, clock = diagnostic_harness
    states = iter(["running", "completed"])

    def query(url, headers):
        assert "X-Fixture-Query" in headers
        clock.value += 0.25
        return module.httpx.Response(200, json={"status": next(states), "payload": harness.owner["token"]})

    harness.release_generation()
    result = harness.wait_terminal(SimpleNamespace(get=query), {"status_url": "/api/jobs/synthetic"})
    assert result["status"] == "completed"
    observed = harness.evidence["checks"][0]
    assert observed["deadline"] - observed["start"] == 15
    assert observed["outcome"] == "completed"
    assert [query["status"] for query in observed["queries"]] == ["running", "completed"]
    assert all(query["end"] - query["start"] == 0.25 for query in observed["queries"])
    assert observed["provider_releases"][0]["end"] <= observed["start"]
    assert harness.owner["token"] not in str(observed)


def test_timeout_stays_failed_after_later_direct_completion(diagnostic_harness, monkeypatch):
    module, harness, clock = diagnostic_harness

    def query(url, headers):
        clock.value += 8  # Existing request timeout can outlive the loop deadline.
        return module.httpx.Response(200, json={"status": "running"})

    class Direct:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def get(self, url, headers):
            clock.value += 0.3
            return module.httpx.Response(
                200, json={"worker_stack": []} if url.endswith("diagnostics") else {"status": "completed"}
            )

    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: Direct())
    with pytest.raises(AssertionError, match="real-application-status-timeout"):
        harness.wait_terminal(SimpleNamespace(get=query), {"status_url": "/api/jobs/synthetic"})
    diagnosis, observed = harness.evidence["checks"]
    assert diagnosis["proxy_status"] == "running" and diagnosis["direct_backend_status"] == "completed"
    assert observed["outcome"] == "timeout" and observed["limit_seconds"] == 15
    assert len(observed["queries"]) == 2
    assert observed["queries"][-1]["ended_after_deadline"] is True
    assert diagnosis["direct_start"] > observed["deadline"]
    assert diagnosis["direct_end"] < diagnosis["worker_observation_start"] + 0.000001


@pytest.mark.parametrize("fault", ["http-status", "application-failed", "transport"])
def test_terminal_diagnostics_preserve_each_failure(diagnostic_harness, fault):
    module, harness, clock = diagnostic_harness

    def query(url, headers):
        clock.value += 0.2
        if fault == "transport":
            raise module.httpx.ConnectError("synthetic-private-report-canary")
        return module.httpx.Response(503 if fault == "http-status" else 200, json={"status": "failed"})

    with pytest.raises(module.httpx.ConnectError if fault == "transport" else AssertionError):
        harness.wait_terminal(SimpleNamespace(get=query), {"status_url": "/api/jobs/synthetic"})
    observed = harness.evidence["checks"][0]
    assert observed["outcome"] == "exception" and len(observed["queries"]) == 1
    assert observed["queries"][0]["end"] > observed["queries"][0]["start"]
    assert harness.owner["token"] not in str(observed)


def test_private_timeline_records_worker_without_source_locals_or_tokens(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "backend/tests/integration"))
    module = importlib.import_module("caddy_diagnostics")
    timeline = module.Timeline(enabled=True)
    ready, release = threading.Event(), threading.Event()

    def worker():
        sensitive_local = "synthetic-private-report-canary"
        ready.set()
        assert release.wait(2)
        assert sensitive_local

    thread = threading.Thread(target=worker)
    thread.start()
    try:
        assert ready.wait(1)
        timeline.record("provider-start")
        timeline.record("provider-release-observed")
        timeline.record("query-start", query=1, worker=thread)
        timeline.record("query-end", query=1, response_code=200, worker=thread)
        events = timeline.snapshot()
        assert [event["at"] for event in events] == sorted(event["at"] for event in events)
        assert any(frame["function"] == "worker" for frame in events[-1]["worker_stack"])
        assert all(set(frame) == {"function", "file", "line"} for frame in events[-1]["worker_stack"])
        assert "synthetic-private-report-canary" not in json.dumps(events)
        assert not list(tmp_path.iterdir()), "record must never write during the measured window"
        with pytest.raises(AssertionError):
            timeline.record("synthetic-private-report-canary")
        with pytest.raises(AssertionError):
            timeline.record("query-start", query="synthetic-private-report-canary")
        assert len(timeline.snapshot()) == 4
    finally:
        release.set()
        thread.join(2)
        assert not thread.is_alive()


@pytest.mark.parametrize(
    "path,probe,authenticated,method,expected",
    [
        ("/api/jobs/synthetic", "1", True, "GET", True),
        ("/api/generations/synthetic", "2", True, "GET", True),
        ("/api/generations/synthetic/events", "2", True, "GET", False),
        ("/api/jobs/synthetic/events", "1", True, "GET", False),
        ("/api/generations/synthetic/cancel", "2", True, "GET", False),
        ("/api/generations/", "2", True, "GET", False),
        ("/api/generations/synthetic", "2", False, "GET", False),
        ("/api/generations/synthetic", "2", True, "POST", False),
        ("/api/generations/synthetic", "0", True, "GET", False),
        ("/api/generations/synthetic", "10001", True, "GET", False),
        ("/api/generations/synthetic", "123456", True, "GET", False),
        ("/api/generations/synthetic", "secret-canary", True, "GET", False),
        ("/api/generations/synthetic", "١", True, "GET", False),
        ("/api/workspaces", "1", True, "GET", False),
    ],
)
def test_status_timeline_covers_actual_jobs_and_generation_routes(
    monkeypatch, path, probe, authenticated, method, expected
):
    monkeypatch.syspath_prepend(str(ROOT / "backend/tests/integration"))
    module = importlib.import_module("caddy_diagnostics")
    assert module.status_probe(path, probe, authenticated=authenticated, method=method) is expected


def test_optional_timeline_is_disabled_by_default(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "backend/tests/integration"))
    module = importlib.import_module("caddy_diagnostics")
    timeline = module.Timeline()
    monkeypatch.setattr(module, "worker_stack", lambda *args: pytest.fail("disabled must not inspect frames"))
    timeline.record("provider-start", worker=threading.current_thread())
    assert timeline.snapshot() == []


@pytest.mark.parametrize("completed", [False, True])
def test_normal_wait_has_no_diagnostic_queries_or_exports(diagnostic_harness, completed):
    module, harness, clock = diagnostic_harness
    harness.diagnostic_mode = "off"

    def query(url, headers):
        assert "X-Fixture-Query" not in headers
        clock.value += 8
        return module.httpx.Response(200, json={"status": "completed" if completed else "running"})

    if completed:
        assert (
            harness.wait_terminal(SimpleNamespace(get=query), {"status_url": "/api/generations/synthetic"})["status"]
            == "completed"
        )
    else:
        with pytest.raises(AssertionError, match="real-application-status-timeout"):
            harness.wait_terminal(SimpleNamespace(get=query), {"status_url": "/api/generations/synthetic"})
    observed = harness.evidence["checks"][0]
    assert observed["limit_seconds"] == 15
    assert observed["probe"] == "application-terminal-wait-unprofiled"
    assert "backend_export" not in observed and "queries" not in observed


def test_profile_delegates_native_budget_operations_and_keeps_durability(tmp_path, monkeypatch):
    import sqlite3

    from app.storage import oci_budget
    from app.storage.provider import StorageBudgetExceeded

    monkeypatch.syspath_prepend(str(ROOT / "backend/tests/integration"))
    module = importlib.import_module("caddy_profile")
    original = oci_budget.sqlite3
    profile = module.Profile()
    profile.install()
    try:
        budget = oci_budget.PresupuestoOCI(tmp_path / "synthetic-private-report-canary.sqlite", solicitudes=2)
        budget.reservar_solicitud()
        budget.reservar_solicitud()
        with pytest.raises(StorageBudgetExceeded):
            budget.reservar_solicitud()
        assert budget.resumen()["solicitudes"] == 2
        connection = oci_budget.sqlite3.connect(budget.ruta)
        cursor = connection.execute("PRAGMA synchronous=FULL")
        assert isinstance(cursor, sqlite3.Cursor)
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INVALID synthetic-private-report-canary")
        connection.close()
        snapshot = profile.snapshot()
        assert {s["label"] for s in snapshot["spans"]} >= {
            "budget.connect",
            "budget.begin",
            "budget.execute",
            "budget.commit",
            "budget.close",
        }
        assert all(s["end"] >= s["start"] for s in snapshot["spans"])
        assert any(s["outcome"] == "error" for s in snapshot["spans"])
        assert "synthetic-private-report-canary" not in json.dumps(snapshot)
        assert not (tmp_path / "timeline.json").exists()
    finally:
        profile.restore()
    assert oci_budget.sqlite3 is original


def test_profile_intervals_are_inclusive_and_union_does_not_double_count(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "backend/tests/integration"))
    module = importlib.import_module("caddy_profile")
    spans = [{"start": 1, "end": 5}, {"start": 2, "end": 3}, {"start": 4, "end": None, "observed_until": 7}]
    assert module.interval_union_seconds(spans, 0, 10) == 6
    assert module.interval_union_seconds(spans, 2, 6) == 4
    profile = module.Profile()
    with profile.span("generation.persist"):
        active = profile.snapshot()["spans"]
        assert active[0]["end"] is None and active[0]["outcome"] == "in-progress"
    assert profile.snapshot()["spans"][0]["outcome"] == "ok"
