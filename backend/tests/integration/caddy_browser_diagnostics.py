"""Closed, bounded browser diagnostic protocol; never serialize an exception."""

import json
import re

LAUNCH_SIGNATURES = frozenset(
    {"unknown", "CHROMIUM_SANDBOX_STARTUP_FAILED", "CHROMIUM_X_SERVER_MISSING", "CHROMIUM_SHARED_LIBRARY_LOAD_FAILED"}
)

# Exact Playwright 1.63.0 wrapInASCIIBox(kNoXServerRunningError, 1).
# Match the complete block on line boundaries, not a quoted/framed phrase.
_BOXED_XSERVER = (
    "╔════════════════════════════════════════════════════════════════════════════════════════════════╗\n"
    "║ Looks like you launched a headed browser without having a XServer running.                     ║\n"
    "║ Set either 'headless: true' or use 'xvfb-run <your-playwright-app>' before running Playwright. ║\n"
    "║                                                                                                ║\n"
    "║ <3 Playwright Team                                                                             ║\n"
    "╚════════════════════════════════════════════════════════════════════════════════════════════════╝"
)


def classify_launch(error):
    """Recognize only native stderr records or exact Playwright startup rewrites.

    Playwright 1.63.0 Chromium.doRewriteStartupLog recognizes the native sandbox
    and X-server signatures and rewrites them. Its browser launcher also checks
    the ELF loader's missing-shared-object signature. See the runbook for scope.
    A sandbox signature says nothing specific about AppArmor.
    """
    try:
        text = str(error)
    except Exception:
        return "unknown"
    if len(text) > 65536:
        return "unknown"
    matches = set()
    if "\n" + _BOXED_XSERVER + "\n" in "\n" + "\n".join(text.splitlines()) + "\n":
        matches.add("CHROMIUM_X_SERVER_MISSING")
    for line in text.splitlines():
        line = line.strip().removeprefix("BrowserType.launch: ")
        if line == "Chromium sandboxing failed!":
            matches.add("CHROMIUM_SANDBOX_STARTUP_FAILED")
        if line == "Looks like you launched a headed browser without having a XServer running.":
            matches.add("CHROMIUM_X_SERVER_MISSING")
        native = re.match(r"^(?:-\s*)?\[pid=\d+\]\[err\]\s*(.*)$", line)
        if native:
            message = native[1]
            if "No usable sandbox!" in message:
                matches.add("CHROMIUM_SANDBOX_STARTUP_FAILED")
            if "Missing X server or $DISPLAY" in message:
                matches.add("CHROMIUM_X_SERVER_MISSING")
            if (
                "error while loading shared libraries:" in message
                and "cannot open shared object file: No such file or directory" in message
            ):
                matches.add("CHROMIUM_SHARED_LIBRARY_LOAD_FAILED")
    return next(iter(matches)) if len(matches) == 1 else "unknown"


STAGE_FAILURES = {
    "process_start": "BROWSER_PROCESS_START_FAILED",
    "process_wait": "BROWSER_PROCESS_WAIT_FAILED",
    "playwright_start": "PLAYWRIGHT_START_FAILED",
    "browser_launch": "BROWSER_LAUNCH_FAILED",
    "browser_version": "BROWSER_VERSION_CHECK_FAILED",
    "context_setup": "BROWSER_CONTEXT_SETUP_FAILED",
    "page_setup": "BROWSER_PAGE_SETUP_FAILED",
    "navigation": "BROWSER_NAVIGATION_FAILED",
    "first_interaction": "BROWSER_FIRST_INTERACTION_FAILED",
    "reconnection": "BROWSER_RECONNECTION_FAILED",
    "hold": "BROWSER_HOLD_FAILED",
    "second_interaction": "BROWSER_SECOND_INTERACTION_FAILED",
    "privacy_audit": "BROWSER_PRIVACY_AUDIT_FAILED",
    "browser_close": "BROWSER_CLOSE_FAILED",
    "result_write": "BROWSER_RESULT_WRITE_FAILED",
}
EXCEPTION_TYPES = frozenset(
    {
        "AssertionError",
        "Error",
        "TimeoutError",
        "TargetClosedError",
        "OSError",
        "PermissionError",
        "FileNotFoundError",
        "ModuleNotFoundError",
        "RuntimeError",
        "ValueError",
        "TypeError",
        "TimeoutExpired",
        "OtherError",
    }
)


def failure(stage, error):
    name = type(error).__name__
    report = {
        "schema": 1,
        "status": "failed",
        "stage": stage,
        "exception_type": name if name in EXCEPTION_TYPES else "OtherError",
        "failure_code": STAGE_FAILURES[stage],
    }
    if stage == "browser_launch":
        report["launch_signature"] = classify_launch(error)
    return report


def success():
    return {"schema": 1, "status": "passed", "stage": "complete", "exception_type": None, "failure_code": None}


class BrowserProgress:
    def __init__(self):
        self.stage = "playwright_start"

    def reached(self, stage):
        assert stage in STAGE_FAILURES, "unknown-browser-diagnostic-stage"
        self.stage = stage

    def failed(self, error):
        return failure(self.stage, error)


def unavailable(code):
    assert code in {"BROWSER_DIAGNOSTIC_MISSING", "BROWSER_DIAGNOSTIC_INVALID"}
    return {"schema": 1, "status": "unavailable", "stage": "unknown", "exception_type": None, "failure_code": code}


def read_diagnostic(path):
    """A malformed child report stays inconclusive; never forward its fields."""
    try:
        if path.is_symlink():
            return unavailable("BROWSER_DIAGNOSTIC_INVALID")
        with path.open("rb") as file:
            data = file.read(4097)
    except FileNotFoundError:
        return unavailable("BROWSER_DIAGNOSTIC_MISSING")
    except OSError:
        return unavailable("BROWSER_DIAGNOSTIC_INVALID")
    try:
        assert len(data) <= 4096
        value = json.loads(data)
        assert isinstance(value, dict)
        fields = set(success())
        if value.get("stage") == "browser_launch":
            fields.add("launch_signature")
            assert value.get("launch_signature") in LAUNCH_SIGNATURES
        assert set(value) == fields
        assert type(value["schema"]) is int and value["schema"] == 1
        if value == success():
            return success()
        assert value["status"] == "failed"
        assert value["stage"] in STAGE_FAILURES
        assert value["exception_type"] in EXCEPTION_TYPES
        assert value["failure_code"] == STAGE_FAILURES[value["stage"]]
        return {key: value[key] for key in fields}
    except (AssertionError, ValueError, TypeError, RecursionError):
        return unavailable("BROWSER_DIAGNOSTIC_INVALID")
