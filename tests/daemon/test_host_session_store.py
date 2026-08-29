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
from runtime.daemon.app import create_app
from runtime.daemon.host_session_store import (
    HostSessionStore,
    compose_host_sessions_block,
)
from runtime.daemon.metrics_store import (
    _SNAPSHOT_FORMAT_FIELD,
    MetricsStore,
    compose_metrics_snapshot,
    maybe_persist_metrics_snapshot,
)
from runtime.daemon.state import DaemonState
from runtime.orchestrator.host_supervisor import _MAX_HEALTH_EVIDENCE_CHARS
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
    invocation_kind: str = "",
    executor_profile: str = "",
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
        invocation_kind=invocation_kind,
        executor_profile=executor_profile,
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


def test_process_peak_zero_with_sampled_provenance_never_lands_in_kernel_bucket() -> None:
    """Adversarial shipping-seam regression (TASK-5910): the defective Linux
    finish could emit process_peak == 0 with KERNEL provenance from an
    empty-tree teardown pids.current. The aggregation must keep any
    best-effort 0 in the sampled bucket — the kernel bucket stays empty — so
    fabricated authoritative zeros cannot recur in /metrics or /health."""
    store = HostSessionStore()
    store.publish(
        make_receipt(
            process_peak_provenance=MeasurementProvenance.SAMPLED,
            process_peak=0,
        )
    )
    snap = store.snapshot()
    peaks = snap["peaks"]["process_peak"]
    assert peaks["kernel"] == {"max": None, "count": 0}
    assert peaks["sampled"] == {"max": 0, "count": 1}
    assert peaks["unavailable_count"] == 0


def test_process_peak_kernel_bucket_reflects_only_kernel_peak_receipts() -> None:
    """A receipt whose process peak carries KERNEL provenance (pids.peak)
    aggregates into the kernel bucket independently of any sampled values;
    the corrected Linux provenance flows through unchanged."""
    store = HostSessionStore()
    store.publish(
        make_receipt(
            process_peak_provenance=MeasurementProvenance.KERNEL,
            process_peak=7,
        )
    )
    store.publish(
        make_receipt(
            process_peak_provenance=MeasurementProvenance.SAMPLED,
            process_peak=50,
        )
    )
    store.publish(
        make_receipt(
            process_peak_provenance=MeasurementProvenance.UNAVAILABLE,
            process_peak=None,
        )
    )
    snap = store.snapshot()
    peaks = snap["peaks"]["process_peak"]
    assert peaks["kernel"] == {"max": 7, "count": 1}
    assert peaks["sampled"] == {"max": 50, "count": 1}
    assert peaks["unavailable_count"] == 1


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


# ---------------------------------------------------------------------------
# Slice C: bounded receipt attribution (invocation_kind + executor_profile)
# ---------------------------------------------------------------------------


def test_by_invocation_kind_aggregate_is_bounded_and_fixed_vocabulary() -> None:
    """Attribution aggregation uses the FIXED canonical kind vocabulary:
    unknown/empty kinds fold into a single ``other`` bucket, so the
    aggregate map cardinality can never grow with input (no dynamic
    attribution keys)."""
    store = HostSessionStore()
    store.publish(make_receipt(invocation_kind="task"))
    store.publish(make_receipt(invocation_kind="thread"))
    store.publish(make_receipt(invocation_kind="dream"))
    store.publish(make_receipt(invocation_kind="wake"))
    store.publish(make_receipt(invocation_kind="schedule"))
    for hostile_kind in ("", "mystery", "TASK", "custom-kind-123", "executor:attack"):
        store.publish(make_receipt(invocation_kind=hostile_kind))
    snap = store.snapshot()
    assert snap["by_invocation_kind"] == {
        "task": 1,
        "thread": 1,
        "dream": 1,
        "wake": 1,
        "schedule": 1,
        "other": 5,
    }
    # Cardinality never exceeds the canonical vocabulary + 1 ``other`` bucket.
    assert set(snap["by_invocation_kind"].keys()) <= {
        "task", "thread", "dream", "wake", "schedule", "other",
    }


def test_recent_summary_carries_bounded_attribution() -> None:
    """The authed recent window attributes each receipt with a bounded
    invocation kind and a redacted executor profile (length/character
    conservative redaction for the externally-influenced value)."""
    store = HostSessionStore()
    store.publish(
        make_receipt(
            invocation_kind="task",
            executor_profile="claude",
        )
    )
    store.publish(
        make_receipt(
            invocation_kind="unknown-kind",
            executor_profile="evil\nprofile" + "x" * 200,
        )
    )
    snap = store.snapshot()
    task_summary = snap["recent"][-1]
    assert task_summary["invocation_kind"] == "task"
    assert task_summary["executor_profile"] == "claude"
    hostile_summary = snap["recent"][0]
    # Unknown kind folds to the fixed ``other`` bucket; the profile is
    # redacted: length-bounded, control chars scrubbed, never raw input.
    assert hostile_summary["invocation_kind"] == "other"
    assert len(hostile_summary["executor_profile"]) <= 64
    assert "\n" not in hostile_summary["executor_profile"]
    assert hostile_summary["executor_profile"].startswith("evil_profile")


def test_attribution_never_creates_dynamic_aggregate_keys() -> None:
    """Adversarial: 100 distinct hostile invocation-kind values still produce
    the SAME fixed key set — only the ``other`` bucket count grows."""
    store = HostSessionStore(max_receipts=128)
    for i in range(100):
        store.publish(make_receipt(invocation_kind=f"kind-{i}"))
    snap = store.snapshot()
    assert set(snap["by_invocation_kind"].keys()) == {"other"}
    assert snap["by_invocation_kind"]["other"] == 100


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


def test_metrics_process_peak_aggregates_never_mix_provenance(
    tmp_home, app, auth_headers
) -> None:
    """Shipping-seam aggregation: corrected Linux receipts (pids.peak -> kernel
    bucket; best-effort fallback -> sampled bucket) flow into /metrics without
    blending; a best-effort 0 never lands in the kernel bucket."""
    client = TestClient(app)
    state = client.app.state.daemon
    state.host_session_store.publish(
        make_receipt(
            terminal_reason=TerminalReason.SUCCESS.value,
            process_peak=7,
            process_peak_provenance=MeasurementProvenance.KERNEL,
        )
    )
    state.host_session_store.publish(
        make_receipt(
            terminal_reason=TerminalReason.SUCCESS.value,
            process_peak=0,
            process_peak_provenance=MeasurementProvenance.SAMPLED,
        )
    )
    r = client.get("/api/v1/metrics", headers=auth_headers)
    assert r.status_code == 200
    peaks = r.json()["host_sessions"]["receipts"]["peaks"]["process_peak"]
    assert peaks["kernel"] == {"max": 7, "count": 1}
    assert peaks["sampled"] == {"max": 0, "count": 1}
    # The recent window carries the corrected per-receipt provenance.
    recent = r.json()["host_sessions"]["receipts"]["recent"]
    assert {row["process_peak_provenance"] for row in recent} == {
        MeasurementProvenance.KERNEL.value,
        MeasurementProvenance.SAMPLED.value,
    }


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
        invocation_kind="task",
        executor_profile="claude",
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
    # Slice C: the bounded kind aggregate IS observable (fixed vocabulary,
    # non-sensitive counts) but per-receipt attribution detail is not.
    assert hs["receipts"]["by_invocation_kind"] == {"task": 1}
    blob = json.dumps(body)
    # Never leaks survivor identities (PIDs / start identities) anywhere.
    assert "4000" not in blob
    assert "start_identity" not in blob
    # Never leaks per-receipt attribution (recent window is dropped public).
    assert "executor_profile" not in blob


def test_metrics_authed_surface_carries_bounded_attribution(
    tmp_home, app, auth_headers
) -> None:
    """Slice C: the bearer-authed /metrics surface carries the explicitly
    bounded receipt attribution — per-receipt bounded kind + redacted
    executor profile in the recent window, and the fixed-vocabulary
    by_invocation_kind aggregate — without schema/route/config change."""
    client = TestClient(app)
    state = client.app.state.daemon
    state.host_session_store.publish(
        make_receipt(
            terminal_reason=TerminalReason.SUCCESS.value,
            invocation_kind="task",
            executor_profile="claude",
        )
    )
    state.host_session_store.publish(
        make_receipt(
            terminal_reason=TerminalReason.SUCCESS.value,
            invocation_kind="schedule",
            executor_profile="pi",
        )
    )
    r = client.get("/api/v1/metrics", headers=auth_headers)
    assert r.status_code == 200
    hs = r.json()["host_sessions"]
    assert hs["receipts"]["by_invocation_kind"] == {"task": 1, "schedule": 1}
    recent = hs["receipts"]["recent"]
    assert {row["invocation_kind"] for row in recent} == {"task", "schedule"}
    assert {row["executor_profile"] for row in recent} == {"claude", "pi"}
    # Redacted/bounded per-receipt attribution stays JSON-safe.
    blob = json.dumps(hs)
    assert "claude" in blob and "pi" in blob


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


def _hostile_probe() -> None:
    """A capability probe that fails with secret-bearing diagnostics."""
    raise RuntimeError("secret-path=/srv/private/token")


def _state_with_hostile_probe(runtime, monkeypatch) -> DaemonState:
    """Runtime-backed state whose next supervisor probe embeds the hostile
    sentinel in the cached evidence (the construction-time probe already
    cached a healthy passthrough report, so we reset the cache to force a
    re-probe through the hostile backend probe on the next read)."""
    state = DaemonState.from_runtime(runtime, Settings())
    assert state.host_supervisor is not None
    monkeypatch.setattr(state.host_supervisor._backend, "probe", _hostile_probe)
    state.host_supervisor._capability_report = None
    return state


def test_health_public_variant_never_exposes_hostile_probe_evidence(
    tmp_home, runtime, monkeypatch
) -> None:
    """Adversarial: a probe exception carrying secret-bearing text must never
    reach the unauthenticated /health payload, even though the supervisor
    embeds the raw exception text in its evidence field (the reviewer repro:
    ``probe failed: secret-path=/srv/private/token`` verbatim). The degraded
    probe stays honestly classified (healthy=False) without the raw text."""
    state = _state_with_hostile_probe(runtime, monkeypatch)
    client = TestClient(create_app(state))
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    body = r.json()
    blob = json.dumps(body)
    assert "secret-path" not in blob
    assert "/srv/private/token" not in blob
    hs = body["host_sessions"]
    assert hs["wired"] is True
    assert hs["backend"]["healthy"] is False
    assert "evidence" not in hs["backend"]


def test_health_public_backend_shape_omits_evidence_when_wired(tmp_home, app) -> None:
    client = TestClient(app)
    r = client.get("/api/v1/health")
    assert r.status_code == 200
    hs = r.json()["host_sessions"]
    assert hs["wired"] is True
    # Intended public backend shape: bounded classification + capability view,
    # never the probe evidence string.
    assert set(hs["backend"].keys()) == {
        "name", "version", "healthy", "probed_at", "capabilities",
    }


def test_health_public_backend_shape_omits_evidence_when_unwired(daemon_state_idle) -> None:
    # The unauthenticated /health route only serves the block when the
    # supervisor is wired (idle keeps the two-key contract), so the unwired
    # public projection is exercised at the shared composer boundary.
    block = compose_host_sessions_block(daemon_state_idle, public=True)
    assert block["wired"] is False
    # Uniform public backend shape even when the supervisor is not wired.
    assert set(block["backend"].keys()) == {
        "name", "version", "healthy", "probed_at", "capabilities",
    }


def test_metrics_authed_surface_retains_bounded_backend_evidence(
    tmp_home, runtime, monkeypatch, auth_headers
) -> None:
    """The authenticated operator surface retains the bounded operational
    evidence (truncated), so the scrub is scoped to the public projection
    only and does not over-redact the /metrics observability contract."""
    state = _state_with_hostile_probe(runtime, monkeypatch)
    client = TestClient(create_app(state))
    r = client.get("/api/v1/metrics", headers=auth_headers)
    assert r.status_code == 200
    hs = r.json()["host_sessions"]
    assert hs["wired"] is True
    evidence = hs["backend"]["evidence"]
    assert evidence is not None
    assert "secret-path" in evidence
    assert len(evidence) <= _MAX_HEALTH_EVIDENCE_CHARS
