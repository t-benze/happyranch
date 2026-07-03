"""In-memory metrics registry for daemon runtime observability.

PR-1 (THR-066): registry singleton + uptime anchor + pull-gauge scaffolds.
PR-2 fills ``loops`` (per-loop tick store) and ``http`` (per-route histogram)
via loop-tick hooks and HTTP middleware.
"""
from __future__ import annotations

import math
import time as _time
from datetime import datetime, timezone
from typing import Any

# Maximum number of latency samples retained per route.
_RING_SIZE = 1024


class _RouteHistogram:
    """Bounded ring buffer of raw latencies for a single route.

    Stores up to ``_RING_SIZE`` samples.  Quantiles (p50, p95, max) are
    computed on read (snapshot), never stored cumulatively.
    """

    __slots__ = ("_buf", "_head", "_count")

    def __init__(self) -> None:
        self._buf: list[float] = [0.0] * _RING_SIZE
        self._head: int = 0
        self._count: int = 0

    def record(self, latency_s: float) -> None:
        """Record a single latency sample in seconds."""
        self._buf[self._head] = latency_s
        self._head = (self._head + 1) % _RING_SIZE
        if self._count < _RING_SIZE:
            self._count += 1

    def snapshot(self) -> dict[str, Any]:
        """Return {count, p50, p95, max} computed from current samples."""
        if self._count == 0:
            return {"count": 0, "p50": None, "p95": None, "max": None}
        # Collect valid samples from the ring buffer.
        if self._count < _RING_SIZE:
            # Buffer is still filling; only first _count entries are valid.
            samples = sorted(self._buf[: self._count])
        else:
            samples = sorted(self._buf)
        n = len(samples)
        return {
            "count": n,
            "p50": _quantile(samples, 0.50),
            "p95": _quantile(samples, 0.95),
            "max": samples[-1],
        }


def _quantile(sorted_vals: list[float], q: float) -> float:
    """Compute the q-th quantile of sorted values (linear interpolation).

    ``q`` in [0, 1].  Returns float seconds.
    """
    if not sorted_vals:
        return 0.0
    n = len(sorted_vals)
    idx = q * (n - 1)
    lo = int(math.floor(idx))
    hi = int(math.ceil(idx))
    if lo == hi:
        return sorted_vals[lo]
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


class MetricsRegistry:
    """Singleton in-memory metrics store held on the daemon (app.state).

    Construction captures the process start timestamp (monotonic + wall
    clock) as the uptime anchor.
    """

    def __init__(self) -> None:
        self._start_monotonic: float = _time.monotonic()
        self._wall_clock_start: float = _time.time()
        # Per-loop tick store: {loop_name: dict}
        self._loops: dict[str, dict] = {}
        # Per-route histograms: {route_key: _RouteHistogram}
        self._http: dict[str, _RouteHistogram] = {}

    # ------------------------------------------------------------------
    # Uptime
    # ------------------------------------------------------------------

    def uptime_seconds(self) -> float:
        """Return seconds since the registry (and thus the process) started."""
        return _time.monotonic() - self._start_monotonic

    # ------------------------------------------------------------------
    # Loop tick recording (PR-2)
    # ------------------------------------------------------------------

    def record_loop_tick(
        self,
        loop_name: str,
        interval_seconds: int,
        last_duration_s: float,
    ) -> None:
        """Record a single iteration's completion for a resident scheduler loop.

        ``interval_seconds`` is the configured sleep between iterations (0
        for non-sleeping loops like the run_step worker).
        """
        self._loops[loop_name] = {
            "last_tick_iso": datetime.now(timezone.utc).isoformat(),
            "interval_seconds": interval_seconds,
            "last_duration_seconds": round(last_duration_s, 6),
        }

    # ------------------------------------------------------------------
    # HTTP latency recording (PR-2)
    # ------------------------------------------------------------------

    def record_http_latency(self, route: str, latency_s: float) -> None:
        """Record a single request latency for *route*."""
        hist = self._http.get(route)
        if hist is None:
            hist = _RouteHistogram()
            self._http[route] = hist
        hist.record(latency_s)

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a JSON-safe snapshot of the metrics registry.

        Callers that need live pull-gauges (tasks, jobs, sessions, queue
        depth) compose them alongside this snapshot in the route handler —
        they are NOT stored in the registry.
        """
        http_snap: dict[str, dict] = {}
        for route, hist in self._http.items():
            http_snap[route] = hist.snapshot()
        return {
            "uptime_seconds": self.uptime_seconds(),
            "loops": dict(self._loops),
            "http": http_snap,
        }
