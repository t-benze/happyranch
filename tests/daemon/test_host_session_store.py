"""Tests for the bounded host-session observability store + health/metrics wiring
(THR-207 observability slice).

Covers:

- ``HostSessionStore`` bounded recent-window retention and cumulative counters;
- bounded-cardinality aggregates (by terminal reason / cleanup status) and
  per-provenance peak aggregates that never mix ``kernel``/``sampled`` values;
- provenance/unavailable honesty: an unavailable peak is counted, never
  rendered as a fabricated zero;
- the shared ``compose_host_sessions_block`` shape wired into
  ``compose_metrics_snapshot`` (live /metrics + persisted rows) and the
  unauthenticated ``GET /health`` public (non-sensitive) variant;
- existing contract preservation: idle ``/health`` keeps its exact dict and
  legacy metrics rows stay readable; the snapshot format marker is unchanged.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon.host_session_store import HostSessionStore
from runtime.daemon.metrics_store import (
    _SNAPSHOT_FORMAT_FIELD,
    MetricsStore,
    compose_metrics_snapshot,
    maybe_persist_metrics_snapshot,
)
from runtime.daemon.state import DaemonState
from runtime.platform.session_backend import (
    CleanupStatus,
    MeasurementProvenance,
    Receipt,
    SurvivorRecord,
    TerminalReason,
)


# ---------------------------------------------------------------------------
# Receipt builders
# ---------------------------------------------------------------------------


def make_receipt(
    *,
    terminal_reason: str = TerminalReason.SUCCESS.value,
    cleanup_status: CleanupStatus = CleanupStatus.CLEAN,
    cleanup_duration_seconds: float = 0.5,
    quiescent: bool = True,
    memory_peak_bytes: int | None = 1234,
    memory_peak_provenance: MeasurementProvenance = MeasurementProvenance.KERNEL,
    cpu_total_seconds: float | None = 2.5,
    cpu_total_provenance: MeasurementProvenance = MeasurementProvenance.KERNEL,
    process_peak: int | None = 7,
    process_peak_provenance: MeasurementProvenance = MeasurementProvenance.KERNEL,
    sample_gaps: tuple[float, ...] = (0.9, 1.1),
    enforcement_events: tuple[str, ...] = (),
    survivors: tuple[SurvivorRecord, ...] = (),
    backend: str = "fake",
) -> Receipt:
    return Receipt(
        backend=backend,
        terminal_reason=terminal_reason,
        cleanup_status=cleanup_status,
        cleanup_duration_seconds=cleanup_duration_seconds,
        quiescent=quiescent,
        wall_time_seconds=1.0,
        memory_peak_bytes=memory_peak_bytes,
        memory_peak_provenance=memory_peak_provenance,
        cpu_total_seconds=cpu_total_seconds,
        cpu_total_provenance=cpu_total_provenance,
        process_peak=process_peak,
        process_peak_provenance=process_peak_provenance,
        sample_gaps=sample_gaps,
        enforcement_events=enforcement_events,
        survivors=survivors,
    )


def make_survivor(pid: int, start_identity: str = "boot-1") -> SurvivorRecord:
    return SurvivorRecord(
        pid=pid,
        start_identity=start_identity,
        backend="fake",
        discovered_at=1.0,
        last_seen_at=2.0,
    )


# ---------------------------------------------------------------------------
# HostSessionStore unit tests
# ---------------------------------------------------------------------------


def test_empty_store_snapshot_shape() -> None:
    store = HostSessionStore()
    snap = store.snapshot()
    assert snap["published_total"] == 0
    assert snap["window_size"] == 0
    assert snap["recent"] == []
    assert snap["by_terminal_reason"] == {}
    assert snap["by_cleanup_status"] == {}
    assert snap["quiescent_count"] == 0
    assert snap["with_residue_count"] == 0
    assert snap["cleanup_duration_seconds"] == {"max": None, "last": None}
    assert snap["peaks"]["memory_peak_bytes"] == {
        "kernel": {"max": None, "count": 0},
        "sampled": {"max": None, "count": 0},
        "unavailable_count": 0,
    }


def test_recent_window_is_bounded_and_newest_first() -> None:
    store = HostSessionStore(max_receipts=4)
    for i in range(10):
        store.publish(
            make_receipt(
                terminal_reason=(
                    TerminalReason.SUCCESS.value if i % 2 == 0
                    else TerminalReason.FAILURE.value
                ),
                memory_peak_bytes=i,
            )
        )
    snap = store.snapshot()
    # Cumulative counter never drops; the window retains only the last 4.
    assert snap["published_total"] == 10
    assert snap["window_size"] == 4
    assert len(snap["recent"]) == 4
    # Newest-first: i == 9 is the head.
    assert snap["recent"][0]["memory_peak_bytes"] == 9
    assert snap["recent"][-1]["memory_peak_bytes"] == 6
    # Aggregates computed over the retained window only (9,7 success; 8,6 failure).
    assert snap["by_terminal_reason"] == {
        TerminalReason.SUCCESS.value: 2,
        TerminalReason.FAILURE.value: 2,
    }


def test_aggregates_bounded_cardinality_and_json_safe() -> None:
    store = HostSessionStore(max_receipts=64)
    for i in range(64):
        store.publish(
            make_receipt(
                terminal_reason=TerminalReason.SUCCESS.value,
                cleanup_status=CleanupStatus.CLEAN,
                cleanup_duration_seconds=float(i),
                quiescent=(i % 5 != 0),
                survivors=(make_survivor(4000 + i),) if i % 7 == 0 else (),
            )
        )
    snap = store.snapshot()
    # Cardinality bounded by the enums, never by session count.
    assert set(snap["by_terminal_reason"].keys()) <= {t.value for t in TerminalReason}
    assert set(snap["by_cleanup_status"].keys()) <= {c.value for c in CleanupStatus}
    assert snap["by_terminal_reason"][TerminalReason.SUCCESS.value] == 64
    assert snap["by_cleanup_status"][CleanupStatus.CLEAN.value] == 64
    assert snap["quiescent_count"] == 64 - 13  # i % 5 == 0 → 13 non-quiescent
    assert snap["with_residue_count"] == 10  # i % 7 == 0 for i in 0..63 → 10
    assert snap["cleanup_duration_seconds"]["max"] == 63.0
    assert snap["cleanup_duration_seconds"]["last"] == 63.0
    # JSON-safe (no enums/dataclasses leak into the payload).
    blob = json.dumps(snap)
    assert TerminalReason.SUCCESS.value in blob


def test_peaks_are_grouped_by_provenance_and_never_mixed() -> None:
    store = HostSessionStore()
    store.publish(
        make_receipt(
            memory_peak_provenance=MeasurementProvenance.KERNEL,
            memory_peak_bytes=100,
            cpu_total_provenance=MeasurementProvenance.KERNEL,
            cpu_total_seconds=1.0,
            process_peak_provenance=MeasurementProvenance.KERNEL,
            process_peak=1,
        )
    )
    store.publish(
        make_receipt(
            memory_peak_provenance=MeasurementProvenance.SAMPLED,
            memory_peak_bytes=5000,
            cpu_total_provenance=MeasurementProvenance.SAMPLED,
            cpu_total_seconds=99.0,
            process_peak_provenance=MeasurementProvenance.SAMPLED,
            process_peak=50,
        )
    )
    store.publish(
        make_receipt(
            memory_peak_provenance=MeasurementProvenance.UNAVAILABLE,
            memory_peak_bytes=None,
            cpu_total_provenance=MeasurementProvenance.UNAVAILABLE,
            cpu_total_seconds=None,
            process_peak_provenance=MeasurementProvenance.UNAVAILABLE,
            process_peak=None,
        )
    )
    snap = store.snapshot()
    peaks = snap["peaks"]["memory_peak_bytes"]
    # The kernel max never absorbs the (larger) sampled value.
    assert peaks["kernel"] == {"max": 100, "count": 1}
    assert peaks["sampled"] == {"max": 5000, "count": 1}
    assert peaks["unavailable_count"] == 1
    # Same shape for the other two measured fields.
    assert snap["peaks"]["cpu_total_seconds"]["kernel"]["max"] == 1.0
    assert snap["peaks"]["cpu_total_seconds"]["sampled"]["max"] == 99.0
    assert snap["peaks"]["cpu_total_seconds"]["unavailable_count"] == 1
    assert snap["peaks"]["process_peak"]["kernel"]["max"] == 1
    assert snap["peaks"]["process_peak"]["sampled"]["max"] == 50
    assert snap["peaks"]["process_peak"]["unavailable_count"] == 1


def test_recent_summary_carries_provenance_and_bounded_fields() -> None:
    store = HostSessionStore(max_receipts=2)
    store.publish(
        make_receipt(
            terminal_reason=TerminalReason.CANCELLED.value,
            cleanup_status=CleanupStatus.KILL,
            cleanup_duration_seconds=2.0,
            quiescent=False,
            memory_peak_provenance=MeasurementProvenance.SAMPLED,
            memory_peak_bytes=999,
            cpu_total_provenance=MeasurementProvenance.SAMPLED,
            cpu_total_seconds=8.5,
            process_peak_provenance=MeasurementProvenance.SAMPLED,
            process_peak=4,
            sample_gaps=(0.1, 0.2, 0.3),
            enforcement_events=("oom", "throttle", "job_limit", "extra"),
            survivors=(make_survivor(4000), make_survivor(4001)),
        )
    )
    snap = store.snapshot()
    summary = snap["recent"][0]
    assert summary["terminal_reason"] == TerminalReason.CANCELLED.value
    assert summary["cleanup_status"] == CleanupStatus.KILL.value
    assert summary["cleanup_duration_seconds"] == 2.0
    assert summary["quiescent"] is False
    assert summary["memory_peak_bytes"] == 999
    assert summary["memory_peak_provenance"] == MeasurementProvenance.SAMPLED.value
    assert summary["cpu_total_seconds"] == 8.5
    assert summary["cpu_total_provenance"] == MeasurementProvenance.SAMPLED.value
    assert summary["process_peak"] == 4
    assert summary["process_peak_provenance"] == MeasurementProvenance.SAMPLED.value
    assert summary["sample_gap_span_seconds"] == 0.6
    assert summary["enforcement_events"] == ["oom", "throttle", "job_limit"]
    assert summary["survivors_count"] == 2


def test_publish_is_thread_safe() -> None:
    import threading

    store = HostSessionStore(max_receipts=8)
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(200):
                store.publish(
                    make_receipt(terminal_reason=TerminalReason.SUCCESS.value, memory_peak_bytes=i)
                )
        except BaseException as exc:  # pragma: no cover - diagnostics
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    snap = store.snapshot()
    assert snap["published_total"] == 800
    assert snap["window_size"] == 8


# ---------------------------------------------------------------------------
# compose_metrics_snapshot + /metrics wiring
# ---------------------------------------------------------------------------


def test_compose_metrics_idle_has_bounded_host_sessions_shape(daemon_state_idle) -> None:
    state: DaemonState = daemon_state_idle
    snap = compose_metrics_snapshot(state)
    hs = snap["host_sessions"]
    assert hs["wired"] is False
    assert hs["backend"]["name"] is None
    assert hs["backend"]["healthy"] is False
    assert hs["backend"]["capabilities"] == {}
    assert hs["admission"]["cap"] is None
    assert hs["admission"]["active"] == 0
    assert hs["admission"]["queue_depth"] == 0
    assert hs["residue"]["admission_blocked"] is False
    assert hs["residue"]["survivors_count"] == 0
    assert hs["receipts"]["window_size"] == 0
    assert hs["receipts"]["recent"] == []
    # format marker unchanged (route-template label semantics untouched)
    assert snap[_SNAPSHOT_FORMAT_FIELD] == 2


def test_compose_metrics_reflects_published_receipts(daemon_state) -> None:
    state: DaemonState = daemon_state
    assert state.host_supervisor is not None
    state.host_session_store.publish(
        make_receipt(
            terminal_reason=TerminalReason.SUCCESS.value,
            memory_peak_bytes=4321,
            memory_peak_provenance=MeasurementProvenance.KERNEL,
        )
    )
    snap = compose_metrics_snapshot(state)
    hs = snap["host_sessions"]
    assert hs["wired"] is True
    assert hs["receipts"]["published_total"] == 1
    assert hs["receipts"]["recent"][0]["memory_peak_bytes"] == 4321
    # Live admission/backpressure surface is present.
    assert hs["admission"]["cap"] >= 1
    assert hs["admission"]["active"] == 0
    assert hs["admission"]["admitted_total"] == 0
    # Backend capability view comes from the wired supervisor's cached probe.
    assert hs["backend"]["name"] is not None
    assert hs["backend"]["probed_at"] >= 0


def test_metrics_route_includes_host_sessions(tmp_home, app_idle, auth_headers) -> None:
    client = TestClient(app_idle)
    r = client.get("/api/v1/metrics", headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert "host_sessions" in body
    assert body["host_sessions"]["wired"] is False
    assert body["host_sessions"]["receipts"]["recent"] == []


def test_metrics_route_wired_reflects_store(tmp_home, app, auth_headers) -> None:
    client = TestClient(app)
    # Publish directly into the daemon state's store (route-level read test).
    state = client.app.state.daemon
    state.host_session_store.publish(
        make_receipt(terminal_reason=TerminalReason.TIMEOUT.value)
    )
    r = client.get("/api/v1/metrics", headers=auth_headers)
    assert r.status_code == 200
    hs = r.json()["host_sessions"]
    assert hs["wired"] is True
    assert hs["receipts"]["published_total"] == 1
    assert hs["receipts"]["recent"][0]["terminal_reason"] == TerminalReason.TIMEOUT.value


def test_persisted_snapshot_includes_host_sessions(tmp_path: Path) -> None:
    state = DaemonState.idle(Settings())
    state.metrics_store = MetricsStore(str(tmp_path / "metrics.db"))
    state._last_metrics_snapshot_at = 0.0  # defeat throttle
    state.metrics_registry.record_http_latency("GET /health", 0.001)
    now = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)
    maybe_persist_metrics_snapshot(state, now)
    rows = state.metrics_store.query()
    assert len(rows) == 1
    stored = json.loads(rows[0]["snapshot_json"])
    assert "host_sessions" in stored
    assert stored["host_sessions"]["wired"] is False
    assert stored[_SNAPSHOT_FORMAT_FIELD] == 2


# ---------------------------------------------------------------------------
# /health public (unauthenticated) surface
# ---------------------------------------------------------------------------


def test_health_idle_exact_dict_unchanged(tmp_home, app_idle) -> None:
    client = TestClient(app_idle)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    # Pre-existing contract: idle health keeps its exact two-key shape.
    assert r.json() == {"status": "ok", "active_runtime": None}


def test_health_wired_adds_bounded_non_sensitive_host_sessions(tmp_home, app) -> None:
    client = TestClient(app)
    state = client.app.state.daemon
    assert state.host_supervisor is not None
    receipt = make_receipt(
        terminal_reason=TerminalReason.FAILURE.value,
        cleanup_status=CleanupStatus.INCOMPLETE,
        survivors=(make_survivor(4000),),
        memory_peak_bytes=777,
    )
    state.host_session_store.publish(receipt)
    # Route the receipt through the residue accountant (the real publish path)
    # so the live census carries the verified survivor.
    state.host_supervisor._residue.account(receipt, ())
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    hs = body["host_sessions"]
    assert hs["wired"] is True
    assert hs["backend"]["name"] is not None
    assert hs["admission"]["cap"] >= 1
    assert hs["residue"]["admission_blocked"] is False
    # Public variant: counts yes, identities no.
    assert hs["residue"]["survivors_count"] == 1
    assert "survivors" not in hs["residue"]
    assert "recent" not in hs["receipts"]
    # Receipt aggregate is still observable on the public surface.
    assert hs["receipts"]["published_total"] == 1
    assert hs["receipts"]["by_terminal_reason"][TerminalReason.FAILURE.value] == 1
    # Never leaks survivor identities (PIDs / start identities) anywhere.
    blob = json.dumps(body)
    assert "4000" not in blob
    assert "start_identity" not in blob


def test_health_host_sessions_reflects_admission_backpressure(tmp_home, app) -> None:
    client = TestClient(app)
    state = client.app.state.daemon
    # Tighten the admission cap and deny via the residue gate to make
    # backpressure observable on the unauthenticated surface.
    controller = state.host_supervisor._admission
    residue = state.host_supervisor._residue
    residue.note_measurement_failure("measurement_unhealthy")
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    hs = r.json()["host_sessions"]
    assert hs["residue"]["admission_blocked"] is True
    assert hs["residue"]["block_reason"] == "measurement_unhealthy"
