"""Private fixture timeline; never deployed, no request contents or frame locals."""

import sys
import threading
import time
from pathlib import Path


def status_probe(path, probe, *, authenticated, method):
    if not authenticated or method != "GET":
        return False
    if not (probe.isascii() and probe.isdecimal() and len(probe) <= 5 and 0 < int(probe) <= 10000):
        return False
    for prefix in ("/api/jobs/", "/api/generations/"):
        if path.startswith(prefix):
            remainder = path[len(prefix) :]
            return bool(remainder) and "/" not in remainder
    return False


def worker_stack(worker):
    frame = sys._current_frames().get(worker.ident) if worker else None
    entries = []
    while frame is not None and len(entries) < 16:
        entries.append(
            {"function": frame.f_code.co_name, "file": Path(frame.f_code.co_filename).name, "line": frame.f_lineno}
        )
        frame = frame.f_back
    return list(reversed(entries))


class Timeline:
    """Optional in-memory observations; export only outside the measured window."""

    def __init__(self, *, enabled=False):
        self.enabled = enabled
        self.lock = threading.Lock()
        self.events = []

    def record(self, event, *, query=None, response_code=None, worker=None):
        if not self.enabled:
            return
        assert event in {
            "provider-start",
            "provider-release-observed",
            "provider-end",
            "verification-start",
            "verification-end",
            "query-start",
            "query-end",
        }
        entry = {"event": event, "at": round(time.monotonic(), 6)}
        if query is not None:
            assert type(query) is int and 0 < query <= 10000
            entry["query"] = query
        if response_code is not None:
            assert type(response_code) is int and 100 <= response_code <= 599
            entry["response_code"] = response_code
        if worker is not None:
            entry["worker_stack"] = worker_stack(worker)
        with self.lock:
            assert len(self.events) < 4096, "fixture-timeline-capacity-exceeded"
            self.events.append(entry)

    def snapshot(self):
        with self.lock:
            return list(self.events)
