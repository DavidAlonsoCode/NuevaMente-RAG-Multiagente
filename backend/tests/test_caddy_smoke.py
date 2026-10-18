"""Artifact sanitization preserves failures/errors/skips, never raw credentials."""

import importlib
import importlib.util
import json
import subprocess
import sys
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
# Exact output of Playwright 1.63.0 wrapInASCIIBox(kNoXServerRunningError, 1),
# captured from its bundled formatter; no formatter reimplementation in tests.
_PINNED_XSERVER_BOX = (
    "╔════════════════════════════════════════════════════════════════════════════════════════════════╗\n"
    "║ Looks like you launched a headed browser without having a XServer running.                     ║\n"
    "║ Set either 'headless: true' or use 'xvfb-run <your-playwright-app>' before running Playwright. ║\n"
    "║                                                                                                ║\n"
    "║ <3 Playwright Team                                                                             ║\n"
    "╚════════════════════════════════════════════════════════════════════════════════════════════════╝"
)
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


@pytest.mark.parametrize("outcome", ["failure", "error", "skipped", "exit"])
def test_smoke_exports_verified_coverage_before_rejecting_red(tmp_path, monkeypatch, smoke_report_double, outcome):
    state = smoke_report_double
    secret = "synthetic-coverage-private-canary"
    first = state.cases[0]
    first.set("name", first.get("name") + "[" + secret + "]")
    family = first.get("classname")
    names = [c.get("name") for c in state.cases if c.get("classname") == family]
    monkeypatch.setitem(smoke.REQUIRED_FAMILIES, family, (len(names), smoke.identity_digest(names)))
    if outcome == "exit":
        state.returncode = 1
    else:
        ET.SubElement(first, outcome, message=secret).text = secret
    checked = []
    required = smoke.require_coverage

    def verify(cases):
        required(cases)
        checked.append(True)

    monkeypatch.setattr(smoke, "require_coverage", verify)
    with pytest.raises(AssertionError) as caught:
        smoke.run(tmp_path)
    assert str(caught.value).splitlines()[0] == (
        "mandatory-tests-skipped" if outcome == "skipped" else "required-caddy-tests-failed"
    )
    assert checked == [True]
    public = tmp_path / "required-tests.xml"
    tree = ET.parse(public)
    summary = json.loads(tree.find('.//property[@name="caddy.coverage"]').get("value"))
    assert summary["complete"] is True and summary["total"] == 270
    assert summary["unexpected_family_cases"] == 0
    for key, row in summary["families"].items():
        assert row["observed"] == row["expected"] == row["unique"] == smoke.REQUIRED_FAMILIES[key][0]
        assert row["duplicates"] == 0
        assert row["observed_sha256"] == row["expected_sha256"] == smoke.REQUIRED_FAMILIES[key][1]
    assert secret not in public.read_text()
    if outcome != "exit":
        assert tree.find(".//testcase/" + outcome) is not None


@pytest.mark.parametrize("defect", ["missing-family", "substitution", "duplicate", "unknown-family", "invalid-name"])
def test_smoke_exports_coverage_defects_even_when_tests_fail(tmp_path, smoke_report_double, defect):
    state = smoke_report_double
    secret = "synthetic-coverage-private-canary"
    if defect == "missing-family":
        state.cases = [c for c in state.cases if not c.get("classname").endswith("test_caddy_smoke")]
    elif defect == "substitution":
        state.cases[0].set("name", "test_required_0[" + secret + "]")
    elif defect == "duplicate":
        state.cases[1].set("name", state.cases[0].get("name"))
    elif defect == "unknown-family":
        state.cases.append(ET.Element("testcase", classname=secret, name="test_unexpected[" + secret + "]"))
    else:
        state.cases[0].set("name", secret + "/invalid")
    ET.SubElement(state.cases[0], "failure", message=secret).text = secret
    with pytest.raises(AssertionError) as caught:
        smoke.run(tmp_path)
    assert (
        str(caught.value)
        .splitlines()[0]
        .startswith("unexpected-test-identity" if defect == "invalid-name" else "mandatory-tests-")
    )
    public = tmp_path / "required-tests.xml"
    tree = ET.parse(public)
    summary = json.loads(tree.find('.//property[@name="caddy.coverage"]').get("value"))
    assert summary["complete"] is False
    assert tree.find(".//testcase/failure") is not None
    assert secret not in public.read_text()
    rows = summary["families"]
    if defect == "duplicate":
        assert rows["backend.tests.test_caddy_config"]["duplicates"] == 1
    elif defect == "unknown-family":
        assert summary["unexpected_family_cases"] == 1
    elif defect == "missing-family":
        assert rows["backend.tests.test_caddy_smoke"]["observed"] == 0
    else:
        assert (
            rows["backend.tests.test_caddy_config"]["observed_sha256"]
            != rows["backend.tests.test_caddy_config"]["expected_sha256"]
        )


def test_smoke_coverage_hashes_ignore_order_without_ignoring_parameters(smoke_report_double):
    cases = smoke_report_double.cases
    original = smoke.coverage_summary(cases)
    assert original["complete"] is True
    assert smoke.coverage_summary(list(reversed(cases))) == original
    cases[0].set("name", cases[0].get("name") + "[synthetic-private-parameter]")
    changed = smoke.coverage_summary(cases)
    assert changed["complete"] is False and changed["total"] == original["total"]
    assert "synthetic-private-parameter" not in json.dumps(changed)


@pytest.fixture
def browser_diagnostic_modules(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "backend/tests/integration"))
    return SimpleNamespace(
        child=importlib.import_module("caddy_browser"),
        parent=importlib.import_module("test_caddy_proxy"),
        protocol=importlib.import_module("caddy_browser_diagnostics"),
    )


@pytest.mark.parametrize(
    "message, expected",
    [
        pytest.param(
            "[pid=17][err] [17:23:ERROR] No usable sandbox! <private>",
            "CHROMIUM_SANDBOX_STARTUP_FAILED",
            id="native-sandbox",
        ),
        pytest.param(
            "BrowserType.launch: Chromium sandboxing failed!", "CHROMIUM_SANDBOX_STARTUP_FAILED", id="rewritten-sandbox"
        ),
        pytest.param(
            "[pid=17][err] No usable sandbox! AppArmor may be relevant; <private>",
            "CHROMIUM_SANDBOX_STARTUP_FAILED",
            id="sandbox-does-not-prove-apparmor",
        ),
        pytest.param(
            "[pid=17][err] [17:23:ERROR] Missing X server or $DISPLAY <private>",
            "CHROMIUM_X_SERVER_MISSING",
            id="native-x-server",
        ),
        pytest.param(
            "Looks like you launched a headed browser without having a XServer running.",
            "CHROMIUM_X_SERVER_MISSING",
            id="rewritten-x-server",
        ),
        pytest.param(
            "- [pid=17][err] /<private>/chrome: error while loading shared libraries: lib<private>.so: cannot open shared object file: No such file or directory",
            "CHROMIUM_SHARED_LIBRARY_LOAD_FAILED",
            id="native-shared-library",
        ),
        pytest.param(
            "[pid=17][err] error while loading shared libraries: <private>", "unknown", id="incomplete-loader-message"
        ),
        pytest.param("Operation not permitted: <private>", "unknown", id="generic-permission-error"),
        pytest.param("AppArmor DENIED <private>", "unknown", id="unclassified-apparmor-text"),
        pytest.param("https://<private>/No usable sandbox!", "unknown", id="signature-in-url"),
        pytest.param(
            "Chromium sandboxing failed!\n[pid=17][err] Missing X server or $DISPLAY",
            "unknown",
            id="conflicting-sandbox-and-display",
        ),
        pytest.param(
            "Chromium sandboxing failed!\n[pid=17][err] error while loading shared libraries: lib.so: cannot open shared object file: No such file or directory",
            "unknown",
            id="conflicting-sandbox-and-library",
        ),
        pytest.param("Chromium sandboxing failed!\n" + "x" * 65536, "unknown", id="oversized-error"),
        pytest.param(
            "Chromium sandboxing failed!\n[pid=17][err] No usable sandbox!",
            "CHROMIUM_SANDBOX_STARTUP_FAILED",
            id="same-category-twice",
        ),
        pytest.param("", "unknown", id="empty-error"),
        pytest.param("[pid=17][out] No usable sandbox! <private>", "unknown", id="not-native-stderr"),
    ],
)
def test_browser_launch_signatures_are_closed_and_sanitized(
    tmp_path, browser_diagnostic_modules, message, expected, capsys
):
    protocol = browser_diagnostic_modules.protocol
    secret = "synthetic-launch-private-canary"
    report = protocol.failure("browser_launch", RuntimeError(message.replace("<private>", secret)))
    assert report["failure_code"] == "BROWSER_LAUNCH_FAILED"
    assert report["launch_signature"] == expected
    path = tmp_path / "diagnostic.json"
    path.write_text(json.dumps(report))
    assert protocol.read_diagnostic(path) == report
    assert secret not in json.dumps(report) and "https://" not in json.dumps(report)
    assert "APPARMOR" not in report["launch_signature"]
    assert capsys.readouterr() == ("", "")


def test_browser_launch_recognizes_pinned_xserver_box(tmp_path, browser_diagnostic_modules, capsys):
    protocol = browser_diagnostic_modules.protocol
    error = RuntimeError("BrowserType.launch: \n" + _PINNED_XSERVER_BOX + "\nCall log:\nsynthetic-box-private-canary")
    report = protocol.failure("browser_launch", error)
    assert report["failure_code"] == "BROWSER_LAUNCH_FAILED"
    assert report["launch_signature"] == "CHROMIUM_X_SERVER_MISSING"
    path = tmp_path / "diagnostic.json"
    path.write_text(json.dumps(report))
    assert protocol.read_diagnostic(path) == report
    assert "synthetic-box-private-canary" not in json.dumps(report) and "║" not in json.dumps(report)
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("variant", ["paragraph", "framed-fragment", "html-content", "modified-box"])
def test_browser_launch_does_not_extract_xserver_phrase_from_arbitrary_content(browser_diagnostic_modules, variant):
    phrase = "Looks like you launched a headed browser without having a XServer running."
    message = {
        "paragraph": "Arbitrary page content: " + phrase + " synthetic-box-private-canary",
        "framed-fragment": "║ " + phrase + " ║",
        "html-content": "<html><pre>" + _PINNED_XSERVER_BOX + "</pre></html>",
        "modified-box": _PINNED_XSERVER_BOX.replace("Playwright Team", "arbitrary page"),
    }[variant]
    report = browser_diagnostic_modules.protocol.failure("browser_launch", RuntimeError(message))
    assert report["launch_signature"] == "unknown"
    assert "synthetic-box-private-canary" not in json.dumps(report)


def test_browser_launch_box_preserves_unknown_for_conflicting_signatures(browser_diagnostic_modules):
    error = RuntimeError(_PINNED_XSERVER_BOX + "\nChromium sandboxing failed!")
    report = browser_diagnostic_modules.protocol.failure("browser_launch", error)
    assert report["launch_signature"] == "unknown"


def test_browser_launch_classifier_handles_unprintable_error(browser_diagnostic_modules, capsys):
    class UnprintableError(Exception):
        def __str__(self):
            raise RuntimeError("synthetic-launch-private-canary")

    report = browser_diagnostic_modules.protocol.failure("browser_launch", UnprintableError())
    assert report["launch_signature"] == "unknown" and report["exception_type"] == "OtherError"
    assert capsys.readouterr() == ("", "")


def test_browser_launch_classifier_does_not_inspect_other_stages(browser_diagnostic_modules):
    class UnprintableError(Exception):
        def __str__(self):
            pytest.fail("non-launch-error-was-inspected")

    report = browser_diagnostic_modules.protocol.failure("privacy_audit", UnprintableError())
    assert report["stage"] == "privacy_audit" and "launch_signature" not in report


@pytest.mark.parametrize("value", ["synthetic-launch-private-canary", None, [], {}, "absent"])
def test_browser_launch_report_rejects_untrusted_signature(tmp_path, browser_diagnostic_modules, value):
    protocol = browser_diagnostic_modules.protocol
    report = protocol.failure("browser_launch", RuntimeError())
    if value == "absent":
        report.pop("launch_signature")
    else:
        report["launch_signature"] = value
    path = tmp_path / "diagnostic.json"
    path.write_text(json.dumps(report))
    sanitized = protocol.read_diagnostic(path)
    assert sanitized["failure_code"] == "BROWSER_DIAGNOSTIC_INVALID"
    assert "synthetic-launch-private-canary" not in json.dumps(sanitized)


def test_browser_launch_signature_survives_child_and_parent_failure(
    tmp_path, monkeypatch, browser_diagnostic_modules, browser_parent_double
):
    modules = browser_diagnostic_modules

    def launch_failure(url, directory, hold, progress):
        progress.reached("browser_launch")
        raise RuntimeError("[pid=17][err] No usable sandbox! synthetic-launch-private-canary")

    monkeypatch.setattr(modules.child, "probe", launch_failure)
    assert modules.child.run_probe("https://synthetic.invalid", tmp_path, 0) == 1
    with pytest.raises(AssertionError) as caught:
        modules.parent.browser_probe(browser_parent_double.proxy)
    assert str(caught.value).splitlines()[0] == "real-browser-probe-failed"
    observed = browser_parent_double.proxy.evidence
    assert observed["checks"][0]["diagnostic"]["launch_signature"] == "CHROMIUM_SANDBOX_STARTUP_FAILED"
    assert "synthetic-launch-private-canary" not in json.dumps(observed)


@pytest.mark.parametrize("stage", ["playwright_start", "browser_launch", "context_setup", "page_setup", "navigation"])
def test_browser_child_diagnostic_tracks_real_probe_stage(
    tmp_path, monkeypatch, browser_diagnostic_modules, stage, capsys
):
    """Execute the real probe with an explicit Playwright double, never Chromium."""
    module = browser_diagnostic_modules.child
    secret = "synthetic-browser-private-canary"
    error = type("TargetClosedError", (RuntimeError,), {})(secret)
    options = []

    def fail(at, result=None):
        if at == stage:
            raise error
        return result

    page = SimpleNamespace(
        on=lambda *args: None,
        add_init_script=lambda *args: None,
        goto=lambda *args, **kwargs: fail("navigation"),
    )
    context = SimpleNamespace(
        route=lambda *args: None,
        add_cookies=lambda *args: None,
        set_extra_http_headers=lambda *args: None,
        new_page=lambda: fail("page_setup", page),
        new_cdp_session=lambda *args: SimpleNamespace(send=lambda *args: None, on=lambda *args: None),
    )
    browser = SimpleNamespace(new_context=lambda **kwargs: fail("context_setup", context))

    def launch(**kwargs):
        options.append(kwargs)
        return fail("browser_launch", browser)

    class Manager:
        def __enter__(self):
            return fail("playwright_start", SimpleNamespace(chromium=SimpleNamespace(launch=launch)))

        def __exit__(self, *args):
            return False

    package, api = ModuleType("playwright"), ModuleType("playwright.sync_api")
    package.__path__ = []
    api.sync_playwright = Manager
    api.expect = lambda *_: pytest.fail("No debe alcanzar aserciones de interacción")
    monkeypatch.setitem(sys.modules, "playwright", package)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", api)
    # POSIX options are simulated here, not evidence of native Ubuntu execution.
    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix", environ={"NM_BROWSER_EXECUTABLE": secret}))
    assert module.run_probe("https://" + secret, tmp_path, 0) == 1
    report = browser_diagnostic_modules.protocol.read_diagnostic(tmp_path / "browser-0.diagnostic.json")
    assert report == browser_diagnostic_modules.protocol.failure(stage, error)
    assert report["exception_type"] == "TargetClosedError"
    assert secret not in json.dumps(report)
    assert capsys.readouterr() == ("", "")
    if options:
        assert options[0]["chromium_sandbox"] is True
        assert "--no-sandbox" not in options[0]["args"]


@pytest.mark.parametrize(
    "stage",
    [
        "first_interaction",
        "reconnection",
        "hold",
        "second_interaction",
        "privacy_audit",
        "browser_close",
        "result_write",
    ],
)
def test_browser_child_failure_protocol_never_serializes_exception(
    tmp_path, monkeypatch, browser_diagnostic_modules, stage, capsys
):
    module = browser_diagnostic_modules.child
    secret = "synthetic-browser-private-canary"

    def failing_probe(url, directory, hold, progress):
        # Explicit probe double exercises the real reporting/exit boundary.
        progress.reached(stage)
        raise type(secret, (RuntimeError,), {})(url, directory, {"page": secret, "profile": secret})

    monkeypatch.setattr(module, "probe", failing_probe)
    assert module.run_probe("https://" + secret, tmp_path, 130) == 1
    data = (tmp_path / "browser-130.diagnostic.json").read_text()
    assert secret not in data and str(tmp_path) not in data and "https://" not in data
    report = json.loads(data)
    assert report["stage"] == stage and report["exception_type"] == "OtherError"
    assert report["failure_code"] == browser_diagnostic_modules.protocol.STAGE_FAILURES[stage]
    assert capsys.readouterr() == ("", "")


def test_browser_child_success_reports_complete(tmp_path, monkeypatch, browser_diagnostic_modules):
    module = browser_diagnostic_modules.child
    monkeypatch.setattr(module, "probe", lambda *args: None)
    assert module.run_probe("https://synthetic.invalid", tmp_path, 0) == 0
    assert browser_diagnostic_modules.protocol.read_diagnostic(tmp_path / "browser-0.diagnostic.json") == (
        browser_diagnostic_modules.protocol.success()
    )


@pytest.fixture
def browser_parent_double(tmp_path, browser_diagnostic_modules):
    """Real parent wait/report logic; process and infrastructure are doubles."""
    state = SimpleNamespace(exit_code=1, start_error=None, wait_error=None, wait_timeouts=[])

    def wait(timeout):
        state.wait_timeouts.append(timeout)
        if state.wait_error:
            raise state.wait_error
        return state.exit_code

    state.proxy = SimpleNamespace(
        directory=tmp_path,
        url="https://synthetic-browser-private-canary.invalid",
        evidence={"checks": []},
        processes={},
        counters=lambda: {"ui_origin_authenticated": 1},
    )

    def start(name, args, env):
        if state.start_error:
            raise state.start_error
        state.proxy.processes[name] = SimpleNamespace(wait=wait)

    state.proxy.start = start
    return state


@pytest.mark.parametrize("hold", [0, 130])
def test_browser_parent_exports_failed_child_diagnostic_to_observations(
    tmp_path, monkeypatch, browser_diagnostic_modules, browser_parent_double, hold, capsys
):
    module, protocol = browser_diagnostic_modules.parent, browser_diagnostic_modules.protocol
    state = browser_parent_double
    secret = "synthetic-browser-private-canary"
    report = protocol.failure("browser_launch", RuntimeError(secret))
    output = tmp_path / "evidence"
    monkeypatch.setenv("NM_CADDY_EVIDENCE_DIR", str(output))

    def harness(directory):
        state.proxy.directory = directory
        (directory / f"browser-{hold}.diagnostic.json").write_text(json.dumps(report))
        state.proxy.launch = lambda: None
        state.proxy.close = lambda: None
        state.proxy.audit_logs = lambda: {"channels_audited": 0, "transport_double": True}
        return state.proxy

    monkeypatch.setattr(module, "ProxyHarness", harness)
    fixture = module.proxy.__wrapped__()
    proxy = next(fixture)
    try:
        with pytest.raises(AssertionError) as caught:
            module.browser_probe(proxy, hold)
        assert str(caught.value).splitlines()[0] == "real-browser-probe-failed"
    finally:
        fixture.close()  # Execute the real fixture's failure-time artifact export.
    observed = json.loads((output / "observations.json").read_text())
    assert observed["checks"] == [{"probe": "real-browser", "process": "exited_nonzero", "diagnostic": report}]
    assert secret not in json.dumps(observed) and str(tmp_path) not in json.dumps(observed)
    assert state.wait_timeouts == [hold + 90]
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize(
    "variant",
    [
        "missing",
        "malformed",
        "oversized",
        "raw_keys",
        "raw_stage",
        "raw_type",
        "raw_code",
        "bad_schema",
        "failed_with_zero_exit",
        "passed_with_nonzero_exit",
        "wrong_hold",
    ],
)
def test_browser_parent_rejects_incomplete_or_untrusted_diagnostic(
    tmp_path, browser_diagnostic_modules, browser_parent_double, variant
):
    module, protocol = browser_diagnostic_modules.parent, browser_diagnostic_modules.protocol
    state = browser_parent_double
    state.exit_code = 0
    secret = "synthetic-browser-private-canary"
    value = protocol.failure("navigation", RuntimeError(secret))
    if variant == "raw_keys":
        value["profile"] = secret
    elif variant in {"raw_stage", "raw_type", "raw_code"}:
        value[{"raw_stage": "stage", "raw_type": "exception_type", "raw_code": "failure_code"}[variant]] = secret
    elif variant == "bad_schema":
        value["schema"] = True
    elif variant == "passed_with_nonzero_exit":
        value, state.exit_code = protocol.success(), 1
    if variant != "missing":
        path = tmp_path / ("browser-130.diagnostic.json" if variant == "wrong_hold" else "browser-0.diagnostic.json")
        path.write_text("{" if variant == "malformed" else "x" * 4097 if variant == "oversized" else json.dumps(value))
    with pytest.raises(AssertionError) as caught:
        module.browser_probe(state.proxy)
    assert str(caught.value).splitlines()[0] == (
        "real-browser-probe-failed" if state.exit_code else "real-browser-diagnostic-not-passed"
    )
    diagnostic = state.proxy.evidence["checks"][0]["diagnostic"]
    assert secret not in json.dumps(state.proxy.evidence)
    if variant in {"missing", "wrong_hold"}:
        assert diagnostic["failure_code"] == "BROWSER_DIAGNOSTIC_MISSING"
    elif variant not in {"failed_with_zero_exit", "passed_with_nonzero_exit"}:
        assert diagnostic["failure_code"] == "BROWSER_DIAGNOSTIC_INVALID"


@pytest.mark.parametrize("boundary", ["start", "wait"])
def test_browser_parent_process_errors_are_sanitized(
    browser_diagnostic_modules, browser_parent_double, boundary, capsys
):
    module = browser_diagnostic_modules.parent
    state = browser_parent_double
    secret = "synthetic-browser-private-canary"
    if boundary == "start":
        state.start_error = PermissionError(5, secret, secret)
    else:
        state.wait_error = subprocess.TimeoutExpired([secret], 90, output=secret, stderr=secret)
    with pytest.raises(AssertionError, match="^real-browser-probe-(start-failed|timeout)$") as caught:
        module.browser_probe(state.proxy)
    observed = state.proxy.evidence["checks"][0]
    assert observed["diagnostic"]["stage"] == ("process_start" if boundary == "start" else "process_wait")
    assert observed["diagnostic"]["exception_type"] == ("PermissionError" if boundary == "start" else "TimeoutExpired")
    assert secret not in json.dumps(observed) and secret not in str(caught.value)
    assert capsys.readouterr() == ("", "")


def test_browser_parent_retains_success_assertions_and_diagnostic(
    tmp_path, browser_diagnostic_modules, browser_parent_double
):
    module, protocol = browser_diagnostic_modules.parent, browser_diagnostic_modules.protocol
    state = browser_parent_double
    state.exit_code = 0
    (tmp_path / "browser-0.diagnostic.json").write_text(json.dumps(protocol.success()))
    (tmp_path / "browser-result.json").write_text(json.dumps({"interactions": 2, "reload_verified": True}))
    module.browser_probe(state.proxy)
    assert state.proxy.evidence["checks"][0]["diagnostic"] == protocol.success()
    assert state.proxy.evidence["checks"][0]["interactions"] == 2
    assert state.wait_timeouts == [90]


def test_browser_child_report_write_failure_remains_red(tmp_path, monkeypatch, browser_diagnostic_modules, capsys):
    module = browser_diagnostic_modules.child
    monkeypatch.setattr(module, "probe", lambda *args: None)

    def denied(*args):
        raise PermissionError("synthetic-browser-private-canary")

    monkeypatch.setattr(module, "private_write", denied)
    assert module.run_probe("https://synthetic.invalid", tmp_path, 0) == 1
    assert capsys.readouterr() == ("", "")


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
