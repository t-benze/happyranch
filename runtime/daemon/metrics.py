"""In-memory metrics registry for daemon runtime observability.

PR-1 (THR-066): registry singleton + uptime anchor + pull-gauge scaffolds.
PR-2 will fill ``loops`` (per-loop tick store) and ``http`` (per-route
histogram) via loop-tick hooks and HTTP middleware without re-churning the
JSON shape.
"""
from __future__ import annotations

import time as _time


class MetricsRegistry:
    """Singleton in-memory metrics store held on the daemon (app.state).

    Construction captures the process start timestamp (monotonic + wall
    clock) as the uptime anchor.  The ``loops`` and ``http`` stores are
    empty scaffolds — PR-2 fills them; PR-1 only renders them as empty
    dicts to stabilise the JSON contract.
    """

    def __init__(self) -> None:
        self._start_monotonic: float = _time.monotonic()
        self._wall_clock_start: float = _time.time()
        # PR-2 fill: per-loop tick store {loop_name: _LoopTick}
        self._loops: dict = {}
        # PR-2 fill: per-route histograms {route: _RouteHistogram}
        self._http: dict = {}

    def uptime_seconds(self) -> float:
        """Return seconds since the registry (and thus the process) started."""
        return _time.monotonic() - self._start_monotonic

    def snapshot(self) -> dict:
        """Return a JSON-safe snapshot of the metrics registry.

        ``loops`` and ``http`` are empty in PR-1.  Callers that need live
        pull-gauges (tasks, jobs, sessions, queue depth) compose them
        alongside this snapshot in the route handler — they are NOT stored
        in the registry.
        """
        return {
            "uptime_seconds": self.uptime_seconds(),
            "loops": dict(self._loops),
            "http": dict(self._http),
        }
