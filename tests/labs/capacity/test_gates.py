"""Unit tests for abort-gate logic (lab acceptance gates, not product SLOs)."""

from __future__ import annotations

import pytest

from gates import (
    AbortGate,
    LabLimits,
    evaluate_cell_gates,
    evaluate_enrollment_gate,
    evaluate_host_gates,
    evaluate_connected_gate,
)


def test_default_limits_bounded():
    limits = LabLimits()
    assert limits.host_cpu_max_pct <= 90
    assert limits.host_mem_max_pct <= 85
    assert limits.host_disk_max_pct <= 50
    assert limits.cell_rss_max_bytes == pytest.approx(1.5 * 1024**3)
    assert limits.enroll_fail_max == pytest.approx(0.10)
    assert limits.connected_min == pytest.approx(0.90)
    assert limits.sustained == 3


def test_host_cpu_sustained_triggers():
    limits = LabLimits(host_cpu_max_pct=90.0)
    # 2 samples under -> ok; 3rd consecutive over -> abort.
    assert evaluate_host_gates([("t0", {"cpu_pct": 80.0, "mem_pct": 30.0, "disk_pct": 20.0}),
                                ("t1", {"cpu_pct": 95.0, "mem_pct": 30.0, "disk_pct": 20.0}),
                                ("t2", {"cpu_pct": 96.0, "mem_pct": 30.0, "disk_pct": 20.0})],
                               limits) == []
    aborts = evaluate_host_gates([("t0", {"cpu_pct": 95.0, "mem_pct": 30.0, "disk_pct": 20.0}),
                                  ("t1", {"cpu_pct": 96.0, "mem_pct": 30.0, "disk_pct": 20.0}),
                                  ("t2", {"cpu_pct": 97.0, "mem_pct": 30.0, "disk_pct": 20.0})],
                                 limits)
    assert any(a.name == "host_cpu" for a in aborts)


def test_host_mem_and_disk_gates():
    limits = LabLimits(host_mem_max_pct=85.0, host_disk_max_pct=50.0)
    mem_aborts = evaluate_host_gates(
        [("t0", {"cpu_pct": 10.0, "mem_pct": 90.0, "disk_pct": 20.0})] * 3, limits
    )
    assert any(a.name == "host_mem" for a in mem_aborts)
    disk_aborts = evaluate_host_gates(
        [("t0", {"cpu_pct": 10.0, "mem_pct": 30.0, "disk_pct": 60.0})] * 3, limits
    )
    assert any(a.name == "host_disk" for a in disk_aborts)


def test_cell_rss_gate():
    limits = LabLimits(cell_rss_max_bytes=1024 * 1024)  # 1 MiB for the test
    samples = [
        {"cell": "hs-x-c1", "rss_bytes": 2 * 1024 * 1024},
        {"cell": "hs-x-c1", "rss_bytes": 3 * 1024 * 1024},
        {"cell": "hs-x-c1", "rss_bytes": 4 * 1024 * 1024},
    ]
    aborts = evaluate_cell_gates(samples, limits)
    assert any(a.name == "cell_rss" and a.cell == "hs-x-c1" for a in aborts)


def test_enrollment_and_connected_gates():
    limits = LabLimits()
    assert evaluate_enrollment_gate(0.0, limits) == []
    assert any(
        a.name == "enroll_fail" for a in evaluate_enrollment_gate(0.5, limits)
    )
    assert evaluate_connected_gate(1.0, limits) == []
    assert any(
        a.name == "connected_ratio" for a in evaluate_connected_gate(0.5, limits)
    )


def test_abort_gate_repr():
    g = AbortGate(name="host_cpu", reason="sustained over 90%")
    assert g.name == "host_cpu"
    assert "host_cpu" in str(g)
