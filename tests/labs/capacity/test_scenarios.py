"""Unit tests for scenario orchestration: the fail-closed startup-health gate.

The capacity lab must never run a measurement against a cell whose headscale
control plane failed to start (e.g. headscale v0.25.1 exits immediately on an
invalid config, such as an empty DERP map). The gate raises before any
apikey/measurement step, so the scenario's exception path captures cell logs
and tears down with a residue check instead of recording garbage samples.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scenarios import Runner


class _FakeDocker:
    """Stub docker wrapper: cell_health reports a fixed per-cell state."""

    def __init__(self, healthy: dict[int, bool]):
        self._healthy = healthy

    def cell_health(self, cell: int) -> bool:
        return self._healthy.get(cell, False)


def _runner() -> Runner:
    return Runner("cap-20260826T120000Z-ab12", Path("/tmp/cap-test-out"))


def test_wait_cells_healthy_passes_when_healthy():
    r = _runner()
    r.docker = _FakeDocker({1: True, 2: True})
    r._wait_cells_healthy([1, 2], timeout_s=1)
    assert True  # no exception


def test_wait_cells_healthy_fails_closed_on_unhealthy():
    """Red proof for the startup-failure guard: a cell that never becomes
    healthy raises (scenario aborts before any measurement step)."""
    r = _runner()
    r.docker = _FakeDocker({1: False})
    with pytest.raises(RuntimeError, match="did not become healthy"):
        r._wait_cells_healthy([1], timeout_s=1)


def test_wait_cells_healthy_fails_closed_on_partial():
    r = _runner()
    r.docker = _FakeDocker({1: True, 2: False})
    with pytest.raises(RuntimeError, match="did not become healthy"):
        r._wait_cells_healthy([1, 2], timeout_s=1)
