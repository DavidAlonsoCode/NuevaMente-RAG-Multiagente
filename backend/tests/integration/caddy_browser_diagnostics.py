"""Closed, bounded browser diagnostic protocol; never serialize an exception."""

import json

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
    return {
        "schema": 1,
        "status": "failed",
        "stage": stage,
        "exception_type": name if name in EXCEPTION_TYPES else "OtherError",
        "failure_code": STAGE_FAILURES[stage],
    }


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
        assert isinstance(value, dict) and set(value) == set(success())
        assert type(value["schema"]) is int and value["schema"] == 1
        if value == success():
            return success()
        assert value["status"] == "failed"
        assert value["stage"] in STAGE_FAILURES
        assert value["exception_type"] in EXCEPTION_TYPES
        assert value["failure_code"] == STAGE_FAILURES[value["stage"]]
        return {key: value[key] for key in success()}
    except (AssertionError, ValueError, TypeError, RecursionError):
        return unavailable("BROWSER_DIAGNOSTIC_INVALID")
