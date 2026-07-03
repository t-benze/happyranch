"""Tests for MetricsRegistry."""
from __future__ import annotations

import time

from runtime.daemon.metrics import MetricsRegistry


def test_uptime_anchor_captured_at_construction() -> None:
    t0 = time.monotonic()
    registry = MetricsRegistry()
    # At construction, uptime should be ~0
    assert 0 <= registry.uptime_seconds() < 0.5
    # Verify the wall-clock stamp is also captured
    assert registry._wall_clock_start > 0


def test_uptime_seconds_increases_over_time() -> None:
    registry = MetricsRegistry()
    u1 = registry.uptime_seconds()
    time.sleep(0.1)
    u2 = registry.uptime_seconds()
    assert u2 > u1
    assert u2 - u1 >= 0.09  # allow small timing variance


def test_loops_scaffold_is_empty() -> None:
    registry = MetricsRegistry()
    assert registry._loops == {}
    assert registry.snapshot()["loops"] == {}


def test_http_scaffold_is_empty() -> None:
    registry = MetricsRegistry()
    assert registry._http == {}
    assert registry.snapshot()["http"] == {}


def test_snapshot_shape() -> None:
    registry = MetricsRegistry()
    snap = registry.snapshot()
    assert set(snap.keys()) == {"uptime_seconds", "loops", "http"}
    assert isinstance(snap["uptime_seconds"], float)
    assert isinstance(snap["loops"], dict)
    assert isinstance(snap["http"], dict)
