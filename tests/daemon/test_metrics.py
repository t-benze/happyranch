"""Tests for MetricsRegistry."""
from __future__ import annotations

import time

import pytest

from runtime.daemon.metrics import MetricsRegistry, _RouteHistogram, _quantile


# ---------------------------------------------------------------------------
# Uptime (PR-1 — preserved)
# ---------------------------------------------------------------------------

def test_uptime_anchor_captured_at_construction() -> None:
    t0 = time.monotonic()
    registry = MetricsRegistry()
    assert 0 <= registry.uptime_seconds() < 0.5
    assert registry._wall_clock_start > 0


def test_uptime_seconds_increases_over_time() -> None:
    registry = MetricsRegistry()
    u1 = registry.uptime_seconds()
    time.sleep(0.1)
    u2 = registry.uptime_seconds()
    assert u2 > u1
    assert u2 - u1 >= 0.09


# ---------------------------------------------------------------------------
# Loop tick recording (PR-2)
# ---------------------------------------------------------------------------

def test_loops_initially_empty() -> None:
    registry = MetricsRegistry()
    assert registry._loops == {}
    assert registry.snapshot()["loops"] == {}


def test_record_loop_tick_populates_fields() -> None:
    registry = MetricsRegistry()
    registry.record_loop_tick("work_hours_scheduler", 60, 1.234)
    loops = registry.snapshot()["loops"]
    assert "work_hours_scheduler" in loops
    tick = loops["work_hours_scheduler"]
    assert tick["interval_seconds"] == 60
    assert tick["last_duration_seconds"] == 1.234
    assert tick["last_tick_iso"].endswith("+00:00") or "Z" in tick["last_tick_iso"] or "+00:00" in tick["last_tick_iso"] or tick["last_tick_iso"].count(":") >= 2
    # last_tick_iso should be an ISO 8601 timestamp string
    assert "T" in tick["last_tick_iso"]


def test_record_loop_tick_overwrites_previous_entry() -> None:
    registry = MetricsRegistry()
    registry.record_loop_tick("dream_scheduler", 60, 0.5)
    registry.record_loop_tick("dream_scheduler", 60, 0.8)
    loops = registry.snapshot()["loops"]
    assert len(loops) == 1  # still one key
    assert loops["dream_scheduler"]["last_duration_seconds"] == 0.8


def test_record_loop_tick_multiple_loops_independent() -> None:
    registry = MetricsRegistry()
    registry.record_loop_tick("work_hours_scheduler", 60, 1.0)
    registry.record_loop_tick("run_step_worker", 0, 2.5)
    loops = registry.snapshot()["loops"]
    assert set(loops.keys()) == {"work_hours_scheduler", "run_step_worker"}
    assert loops["run_step_worker"]["interval_seconds"] == 0
    assert loops["run_step_worker"]["last_duration_seconds"] == 2.5


# ---------------------------------------------------------------------------
# Quantile computation
# ---------------------------------------------------------------------------

def test_quantile_empty_returns_zero() -> None:
    assert _quantile([], 0.5) == 0.0


def test_quantile_single_value() -> None:
    assert _quantile([1.0], 0.0) == 1.0
    assert _quantile([1.0], 0.5) == 1.0
    assert _quantile([1.0], 1.0) == 1.0


def test_quantile_p50_median_even() -> None:
    # sorted: [1, 2, 3, 4] -> median at index (4-1)*0.5 = 1.5 -> interpolate
    # lo=1 (2), hi=2 (3), frac=0.5 -> 2*0.5 + 3*0.5 = 2.5
    result = _quantile([1.0, 2.0, 3.0, 4.0], 0.50)
    assert result == 2.5


def test_quantile_p50_median_odd() -> None:
    # sorted: [1, 2, 3, 4, 5] -> median at index (5-1)*0.5 = 2 -> 3
    result = _quantile([1.0, 2.0, 3.0, 4.0, 5.0], 0.50)
    assert result == 3.0


def test_quantile_p95() -> None:
    # 20 values: [1,2,...,20], index (20-1)*0.95 = 18.05
    # lo=18 (19), hi=19 (20), frac=0.05 -> 19*0.95 + 20*0.05 = 18.05 + 1.0 = 19.05
    vals = [float(i) for i in range(1, 21)]
    result = _quantile(vals, 0.95)
    assert result == 19.05


def test_quantile_p0_and_p100() -> None:
    vals = [5.0, 10.0, 15.0]
    assert _quantile(vals, 0.0) == 5.0
    assert _quantile(vals, 1.0) == 15.0


# ---------------------------------------------------------------------------
# Route histogram
# ---------------------------------------------------------------------------

def test_histogram_initially_empty() -> None:
    h = _RouteHistogram()
    snap = h.snapshot()
    assert snap["count"] == 0
    assert snap["p50"] is None
    assert snap["p95"] is None
    assert snap["max"] is None


def test_histogram_single_record() -> None:
    h = _RouteHistogram()
    h.record(0.123)
    snap = h.snapshot()
    assert snap["count"] == 1
    assert snap["p50"] == 0.123
    assert snap["p95"] == 0.123
    assert snap["max"] == 0.123


def test_histogram_computes_quantiles() -> None:
    h = _RouteHistogram()
    for v in [0.001, 0.002, 0.003, 0.004, 0.005]:
        h.record(v)
    snap = h.snapshot()
    assert snap["count"] == 5
    # p50 of [1,2,3,4,5] ms = 3
    assert snap["p50"] == 0.003
    # p95 of 5 values: index (5-1)*0.95 = 3.8 -> lo=3 (0.004), hi=4 (0.005)
    # frac=0.8 -> 0.004*0.2 + 0.005*0.8 = 0.0008 + 0.004 = 0.0048
    assert snap["p95"] == 0.0048
    assert snap["max"] == 0.005


def test_histogram_ring_buffer_eviction() -> None:
    h = _RouteHistogram()
    # Fill with 1024 values (exactly the ring size).
    for i in range(1024):
        h.record(float(i))
    snap = h.snapshot()
    assert snap["count"] == 1024
    assert snap["max"] == 1023.0
    # Add one more — oldest (0.0) should be evicted, newest (1024.0) added.
    h.record(1024.0)
    snap2 = h.snapshot()
    assert snap2["count"] == 1024
    assert snap2["max"] == 1024.0
    # p50 should have shifted — old p50 was 511.5; new p50 = 512.5
    # Values now: [1.0, 2.0, ..., 1024.0] → index (1023)*0.5 = 511.5 → lo=511 (512), hi=512 (513)
    assert snap2["p50"] == 512.5


def test_histogram_ring_buffer_partial_fill() -> None:
    h = _RouteHistogram()
    for i in range(100):
        h.record(float(i))
    snap = h.snapshot()
    assert snap["count"] == 100
    assert snap["max"] == 99.0
    assert snap["p50"] == 49.5  # median of 0..99


# ---------------------------------------------------------------------------
# HTTP latency recording via MetricsRegistry
# ---------------------------------------------------------------------------

def test_record_http_latency_creates_histogram() -> None:
    registry = MetricsRegistry()
    registry.record_http_latency("GET /api/v1/metrics", 0.015)
    snap = registry.snapshot()
    assert "GET /api/v1/metrics" in snap["http"]
    assert snap["http"]["GET /api/v1/metrics"]["count"] == 1


def test_record_http_latency_aggregates() -> None:
    registry = MetricsRegistry()
    for _ in range(10):
        registry.record_http_latency("GET /api/v1/tasks", 0.005)
    snap = registry.snapshot()
    assert snap["http"]["GET /api/v1/tasks"]["count"] == 10


def test_record_http_latency_multiple_routes() -> None:
    registry = MetricsRegistry()
    registry.record_http_latency("GET /a", 0.001)
    registry.record_http_latency("POST /b", 0.002)
    registry.record_http_latency("GET /a", 0.003)
    snap = registry.snapshot()
    assert {"GET /a", "POST /b", "__all__"} <= set(snap["http"].keys())
    assert snap["http"]["GET /a"]["count"] == 2
    assert snap["http"]["POST /b"]["count"] == 1


def test_record_http_latency_aggregate_bucket_spans_all_routes() -> None:
    """Aggregate '__all__' bucket count == sum of per-route counts and
    its p50/p95/max span samples from multiple distinct routes."""
    registry = MetricsRegistry()
    # Route A: 3 slow requests
    for _ in range(3):
        registry.record_http_latency("GET /a", 0.100)
    # Route B: 2 fast requests
    registry.record_http_latency("POST /b", 0.001)
    registry.record_http_latency("POST /b", 0.002)
    snap = registry.snapshot()
    agg = snap["http"]["__all__"]
    assert agg["count"] == 5  # 3 + 2
    # Sorted samples: [0.001, 0.002, 0.100, 0.100, 0.100]
    # median at index (4)*0.5 = 2 -> 0.100
    assert agg["p50"] == 0.100
    # p95 at index (4)*0.95 = 3.8 -> lo=3(0.100), hi=4(0.100) -> 0.100
    assert agg["p95"] == 0.100
    assert agg["max"] == 0.100


def test_record_http_latency_aggregate_single_route_only() -> None:
    """When all requests hit one route, aggregate == that route's histogram."""
    registry = MetricsRegistry()
    for _ in range(3):
        registry.record_http_latency("GET /x", 0.050)
    snap = registry.snapshot()
    assert snap["http"]["__all__"]["count"] == 3
    assert snap["http"]["__all__"] == snap["http"]["GET /x"]


# ---------------------------------------------------------------------------
# Snapshot shape (combined)
# ---------------------------------------------------------------------------

def test_snapshot_shape_pr2_populated() -> None:
    registry = MetricsRegistry()
    registry.record_loop_tick("work_hours_scheduler", 60, 0.5)
    registry.record_http_latency("GET /", 0.001)
    snap = registry.snapshot()
    assert set(snap.keys()) == {"uptime_seconds", "loops", "http"}
    assert isinstance(snap["uptime_seconds"], float)
    assert isinstance(snap["loops"], dict)
    assert isinstance(snap["http"], dict)
    assert "work_hours_scheduler" in snap["loops"]
    assert "GET /" in snap["http"]


# ---------------------------------------------------------------------------
# Queue worker tick on failure (FINDING 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_loop_records_tick_on_run_step_exception() -> None:
    """run_step_worker tick is recorded even when run_step raises.

    Finding 2: currently the tick is only recorded on success.
    This test asserts that a failing run_step still produces a tick.
    """
    import asyncio

    from runtime.daemon.queue import TaskQueue

    registry = MetricsRegistry()
    queue = TaskQueue()
    queue._metrics_registry = registry

    class FailDispatcher:
        def run_step(self, slug, task_id, metadata=None):
            raise RuntimeError("simulated worker failure")

        def heartbeat(self, slug, task_id):
            pass

    queue.enqueue("alpha", "task-fail")
    worker = asyncio.create_task(queue._worker_loop(FailDispatcher()))
    # Give the worker time to pick up and process the item (the exception
    # is caught internally so the loop continues and blocks on _queue.get).
    await asyncio.sleep(0.2)
    # Signal stop + unblock _queue.get() with a sentinel so the loop exits.
    queue._stopping = True
    queue.enqueue("alpha", "task-sentinel")
    try:
        await asyncio.wait_for(worker, timeout=3.0)
    except asyncio.TimeoutError:
        worker.cancel()
        raise

    assert "run_step_worker" in registry._loops
    tick = registry._loops["run_step_worker"]
    assert tick["interval_seconds"] == 0
    assert isinstance(tick["last_duration_seconds"], float)
    assert tick["last_duration_seconds"] >= 0


@pytest.mark.asyncio
async def test_worker_loop_records_tick_on_success() -> None:
    """run_step_worker tick is recorded when run_step succeeds (existing behavior)."""
    import asyncio

    from runtime.daemon.queue import TaskQueue

    registry = MetricsRegistry()
    queue = TaskQueue()
    queue._metrics_registry = registry

    class OkDispatcher:
        def run_step(self, slug, task_id, metadata=None):
            pass  # no-op success

        def heartbeat(self, slug, task_id):
            pass

    queue.enqueue("alpha", "task-ok")
    worker = asyncio.create_task(queue._worker_loop(OkDispatcher()))
    await asyncio.sleep(0.2)
    queue._stopping = True
    queue.enqueue("alpha", "task-sentinel")
    try:
        await asyncio.wait_for(worker, timeout=3.0)
    except asyncio.TimeoutError:
        worker.cancel()
        raise

    assert "run_step_worker" in registry._loops
    tick = registry._loops["run_step_worker"]
    assert tick["interval_seconds"] == 0
    assert isinstance(tick["last_duration_seconds"], float)
    assert tick["last_duration_seconds"] >= 0
