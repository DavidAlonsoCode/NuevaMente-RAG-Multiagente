"""Opt-in fixture wrappers. Native calls/arguments/results/exceptions stay real."""

import contextlib
import contextvars
import functools
import sqlite3
import threading
import time

LABELS = {
    "generation.validity",
    "generation.persist",
    "generation.status",
    "budget.connect",
    "budget.pragma",
    "budget.begin",
    "budget.execute",
    "budget.commit",
    "budget.close",
}


class Profile:
    def __init__(self):
        self.lock = threading.Lock()
        self.scope = contextvars.ContextVar("fixture_profile_scope", default="other")
        self.active, self.completed = {}, []
        self.sequence = 0
        self.restorations = []

    @contextlib.contextmanager
    def span(self, label):
        assert label in LABELS
        with self.lock:
            assert len(self.completed) + len(self.active) < 50000, "fixture-profile-capacity-exceeded"
            self.sequence += 1
            index = self.sequence
            self.active[index] = {"label": label, "scope": self.scope.get(), "start": time.monotonic()}
        outcome = "error"
        try:
            yield
            outcome = "ok"
        finally:
            ended = time.monotonic()
            with self.lock:
                self.completed.append(self.active.pop(index) | {"end": ended, "outcome": outcome})

    def snapshot(self):
        with self.lock:
            at = time.monotonic()
            return {
                "at": at,
                "timing": "inclusive-wall-clock",
                "spans": list(self.completed)
                + [
                    entry | {"end": None, "observed_until": at, "outcome": "in-progress"}
                    for entry in self.active.values()
                ],
            }

    def wrap(self, target, name, label, scope):
        original = getattr(target, name)

        @functools.wraps(original)
        def measured(*args, **kwargs):
            token = self.scope.set(scope)
            try:
                with self.span(label):
                    return original(*args, **kwargs)
            finally:
                self.scope.reset(token)

        self.restorations.append((target, name, original))
        setattr(target, name, measured)

    def install(self):
        from app.generations.service import ServicioGeneraciones
        from app.storage import oci_budget

        for method, label, scope in (
            ("_vigente", "generation.validity", "generation"),
            ("_persistir", "generation.persist", "generation"),
            ("estado", "generation.status", "status"),
        ):
            self.wrap(ServicioGeneraciones, method, label, scope)
        original = oci_budget.sqlite3
        self.restorations.append((oci_budget, "sqlite3", original))
        oci_budget.sqlite3 = self.sqlite_facade(original)

    def sqlite_facade(self, native):
        profile = self

        class MeasuredConnection(sqlite3.Connection):
            def execute(self, statement, *args, **kwargs):
                # Inspect only these fixed control statements; never retain SQL.
                label = {
                    "BEGIN IMMEDIATE": "budget.begin",
                    "PRAGMA synchronous=FULL": "budget.pragma",
                }.get(statement, "budget.execute")
                with profile.span(label):
                    return super().execute(statement, *args, **kwargs)

            def commit(self):
                with profile.span("budget.commit"):
                    return super().commit()

            def close(self):
                with profile.span("budget.close"):
                    return super().close()

        class NativeFacade:
            def __getattr__(self, name):
                return getattr(native, name)

            def connect(self, *args, **kwargs):
                assert "factory" not in kwargs, "fixture-profile-unexpected-connection-factory"
                with profile.span("budget.connect"):
                    return native.connect(*args, **kwargs, factory=MeasuredConnection)

        return NativeFacade()

    def restore(self):
        for target, name, original in reversed(self.restorations):
            setattr(target, name, original)
        self.restorations.clear()


def interval_union_seconds(spans, start, end):
    """Clip/merge overlaps, including active spans; never sum nested intervals."""
    intervals = sorted(
        (max(start, span["start"]), min(end, span["end"] if span["end"] is not None else span["observed_until"]))
        for span in spans
    )
    total, right = 0.0, start
    for left, stop in intervals:
        if stop > max(left, right):
            total += stop - max(left, right)
            right = stop
    return total
