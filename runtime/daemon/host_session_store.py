"""Bounded in-memory host-session receipt + health observability store (THR-207).

The daemon-wide :class:`~runtime.orchestrator.host_supervisor.HostSessionSupervisor`
publishes one bounded :class:`~runtime.platform.session_backend.Receipt` per
terminal attempt through its ``publisher`` seam. The daemon wiring
(``runtime/daemon/state.py``) binds that seam to a single process-wide
:class:`HostSessionStore` so the EXISTING bounded operator surfaces — the
bearer-authed ``GET /api/v1/metrics`` snapshot (via
``metrics_store.compose_metrics_snapshot``) and the unauthenticated
``GET /api/v1/health`` liveness probe — can expose the receipt stream and the
live supervisor state (admission/backpressure, residue, capability probe)
without any schema migration or durable-store change.

Boundedness is by construction:

* at most ``_MAX_RECEIPTS`` receipts are retained (oldest dropped), so the
  serialized recent window is cardinality-bounded;
* aggregate maps are keyed by the fixed terminal-reason / cleanup-status
  vocabularies (bounded by the enums), never by session/org/task identity;
* peak aggregates are grouped **per provenance** (``kernel`` / ``sampled``)
  so an authoritative counter is never blended with a sampled estimate, and
  ``unavailable`` values are counted, never rendered as fabricated zeros;
* the census survivor list exposed on the authed surface is truncated to
  ``_MAX_SURVIVORS_EXPOSED`` (exact count preserved); the public
  (unauthenticated) surface drops per-receipt detail, survivor identities,
  and backend probe evidence entirely.

Failure containment: the store itself never raises for the publish path, and
the supervisor additionally contains a raising publisher at the
``finalize_once`` seam (logged, never replaces the terminal cause, never
disrupts cleanup ordering, never leaks the admission lease). The read
composer degrades a broken supervisor/store read to a bounded unavailable
shape rather than crashing the route or the periodic snapshot writer.
"""

from __future__ import annotations

import logging
import threading
from collections import Counter, deque
from typing import TYPE_CHECKING, Any

from runtime.platform.enforcement_policy import (
    bounded_executor_profile,
    bounded_invocation_kind,
)
from runtime.platform.session_backend import (
    MeasurementProvenance,
    Receipt,
)

if TYPE_CHECKING:
    from runtime.daemon.state import DaemonState

logger = logging.getLogger(__name__)

# Recent-window bound: at most this many receipts are retained (dropping the
# oldest) so the /metrics and /health payloads stay cardinality-bounded.
_MAX_RECEIPTS = 64
# Bounded exposure of the censused survivor identities on the authed surface.
_MAX_SURVIVORS_EXPOSED = 16
# Bounded exposure of per-receipt enforcement events (the receipt's own event
# tuple is already bounded; this is a second, tighter presentation bound).
_MAX_ENFORCEMENT_EVENTS = 3
# Bounded exposure of the capability-report evidence/reason strings.
_MAX_EVIDENCE_CHARS = 200

# Provenance keys for the per-provenance peak aggregates. Values carrying any
# other provenance (or None) are counted as ``unavailable`` — never blended
# into a measured bucket and never rendered as a fabricated zero.
_PROVENANCE_BUCKETS = (
    MeasurementProvenance.KERNEL,
    MeasurementProvenance.SAMPLED,
)


class HostSessionStore:
    """Thread-safe, bounded in-memory receipt store for one daemon process.

    Receipts are published from session threads (the supervisor's run loop /
    cancel path) and read from the event-loop route handlers and the periodic
    metrics writer, so every accessor is lock-protected and the snapshot is
    computed from an immutable copy of the retained window.
    """

    def __init__(
        self,
        max_receipts: int = _MAX_RECEIPTS,
    ) -> None:
        if max_receipts < 1:
            raise ValueError("max_receipts must be >= 1")
        self._max_receipts = max_receipts
        self._lock = threading.Lock()
        self._receipts: deque[Receipt] = deque(maxlen=max_receipts)
        self._published_total = 0

    # ── write path ────────────────────────────────────────────────

    def publish(self, receipt: Receipt) -> None:
        """Record one terminal receipt (bounded recent window).

        Appends under the lock; the deque bound drops the oldest. Never
        raises for the normal path — the supervisor additionally contains
        any publisher exception at the ``finalize_once`` seam."""
        with self._lock:
            self._receipts.append(receipt)
            self._published_total += 1

    # ── snapshot ─────────────────────────────────────────────────

    def snapshot(self) -> dict[str, Any]:
        """Return the bounded, JSON-safe receipt aggregate + recent window.

        ``recent`` is newest-first. Peak aggregates are grouped by
        provenance; ``unavailable`` values are counted, never fabricated as
        zeros. All keys are fixed-shape; cardinality is bounded by the enum
        vocabularies and the window bound."""
        with self._lock:
            receipts = list(self._receipts)
            published_total = self._published_total
        by_terminal = Counter(r.terminal_reason for r in receipts)
        by_cleanup = Counter(r.cleanup_status.value for r in receipts)
        # Attribution aggregate: keyed by the FIXED canonical invocation-kind
        # vocabulary (unknown kinds fold into a single ``other`` bucket) —
        # never by session/org/task identity and never a dynamic key per
        # distinct raw value (THR-207 Slice C: no dynamic attribution keys).
        by_invocation_kind = Counter(bounded_invocation_kind(r.invocation_kind) for r in receipts)
        durations = [r.cleanup_duration_seconds for r in receipts]
        return {
            "published_total": published_total,
            "window_size": len(receipts),
            "by_terminal_reason": dict(by_terminal),
            "by_cleanup_status": dict(by_cleanup),
            "by_invocation_kind": dict(by_invocation_kind),
            "quiescent_count": sum(1 for r in receipts if r.quiescent),
            "with_residue_count": sum(1 for r in receipts if r.survivors),
            "cleanup_duration_seconds": {
                "max": max(durations) if durations else None,
                "last": durations[-1] if durations else None,
            },
            "peaks": _peak_aggregates(receipts),
            "recent": [_summarize_receipt(r) for r in reversed(receipts)],
        }


# ── aggregation helpers ─────────────────────────────────────────────


def _peak_aggregates(receipts: list[Receipt]) -> dict[str, Any]:
    """Per-provenance peak aggregates over the retained window.

    Three measured fields (memory peak bytes, CPU total seconds, process
    peak) each get ``kernel`` and ``sampled`` buckets — a kernel counter is
    never blended with a sampled estimate — plus an ``unavailable_count``
    (values that are None or carry unavailable/unknown provenance)."""
    return {
        "memory_peak_bytes": _peak_bucket(receipts, "memory_peak_bytes", "memory_peak_provenance"),
        "cpu_total_seconds": _peak_bucket(receipts, "cpu_total_seconds", "cpu_total_provenance"),
        "process_peak": _peak_bucket(receipts, "process_peak", "process_peak_provenance"),
    }


def _peak_bucket(
    receipts: list[Receipt], value_attr: str, provenance_attr: str
) -> dict[str, Any]:
    bucket: dict[str, Any] = {
        "kernel": {"max": None, "count": 0},
        "sampled": {"max": None, "count": 0},
        "unavailable_count": 0,
    }
    for r in receipts:
        value = getattr(r, value_attr)
        provenance = getattr(r, provenance_attr)
        if value is None or provenance not in _PROVENANCE_BUCKETS:
            bucket["unavailable_count"] += 1
            continue
        key = provenance.value
        if bucket[key]["max"] is None or value > bucket[key]["max"]:
            bucket[key]["max"] = value
        bucket[key]["count"] += 1
    return bucket


def _summarize_receipt(receipt: Receipt) -> dict[str, Any]:
    """One bounded per-receipt summary for the recent window.

    Carries the exact measured values WITH their provenance so the operator
    can distinguish authoritative counters from sampled estimates, plus the
    cleanup outcome and a bounded gap/event view. Survivor identities stay
    in the residue section of the authed surface, never per-receipt here."""
    return {
        "backend": receipt.backend,
        "terminal_reason": receipt.terminal_reason,
        "cleanup_status": receipt.cleanup_status.value,
        "cleanup_duration_seconds": receipt.cleanup_duration_seconds,
        "quiescent": receipt.quiescent,
        "wall_time_seconds": receipt.wall_time_seconds,
        "invocation_kind": bounded_invocation_kind(receipt.invocation_kind),
        "executor_profile": bounded_executor_profile(receipt.executor_profile),
        "memory_peak_bytes": receipt.memory_peak_bytes,
        "memory_peak_provenance": receipt.memory_peak_provenance.value,
        "cpu_total_seconds": receipt.cpu_total_seconds,
        "cpu_total_provenance": receipt.cpu_total_provenance.value,
        "process_peak": receipt.process_peak,
        "process_peak_provenance": receipt.process_peak_provenance.value,
        "sample_gap_span_seconds": (
            round(sum(receipt.sample_gaps), 6) if receipt.sample_gaps else 0.0
        ),
        "enforcement_events": list(receipt.enforcement_events)[:_MAX_ENFORCEMENT_EVENTS],
        "survivors_count": len(receipt.survivors),
    }


# ── shared composer (route + periodic writer + /health) ─────────────


def compose_host_sessions_block(state: "DaemonState", *, public: bool = False) -> dict[str, Any]:
    """Compose the bounded ``host_sessions`` observability block.

    Merges the store's receipt aggregates/recent window with the live
    supervisor view (cached capability probe, admission/backpressure stats,
    residue census/gate). Additive and failure-contained: a missing or
    broken supervisor/store degrades to a bounded unavailable shape and can
    never crash the calling route or the periodic snapshot writer.

    ``public=True`` drops the per-receipt recent window, the censused
    survivor identities (PIDs / start identities), and the backend probe
    evidence string (a failed probe can embed raw exception text) so the
    unauthenticated ``/health`` surface stays non-sensitive while keeping
    counts, aggregates, and the stable classification observable.
    """
    store = getattr(state, "host_session_store", None)
    if store is not None:
        try:
            receipts_block = store.snapshot()
        except Exception as exc:  # noqa: BLE001 — observation must not crash
            logger.warning("host_session_store snapshot failed: %s", exc)
            receipts_block = _empty_receipts_block()
    else:
        receipts_block = _empty_receipts_block()

    supervisor = getattr(state, "host_supervisor", None)
    if supervisor is None:
        block = {
            "wired": False,
            "backend": _empty_backend_block(),
            "admission": _empty_admission_block(),
            "residue": _empty_residue_block(),
            "receipts": receipts_block,
        }
    else:
        try:
            live = supervisor.health_snapshot()
        except Exception as exc:  # noqa: BLE001 — observation must not crash
            logger.warning("host_supervisor health snapshot failed: %s", exc)
            live = {
                "backend": _empty_backend_block(),
                "admission": _empty_admission_block(),
                "residue": _empty_residue_block(),
            }
        block = {
            "wired": True,
            "backend": live["backend"],
            "admission": live["admission"],
            "residue": live["residue"],
            "receipts": receipts_block,
        }

    if public:
        # Non-sensitive public surface: counts and aggregates yes; per-receipt
        # detail, survivor identities, and backend probe evidence (raw
        # exception text on a failed probe) no.
        block["residue"].pop("survivors", None)
        block["receipts"].pop("recent", None)
        block["backend"].pop("evidence", None)
    return block


def _empty_receipts_block() -> dict[str, Any]:
    return HostSessionStore().snapshot()


def _empty_backend_block() -> dict[str, Any]:
    return {
        "name": None,
        "version": None,
        "healthy": False,
        "probed_at": 0.0,
        "capabilities": {},
        "evidence": None,
    }


def _empty_admission_block() -> dict[str, Any]:
    return {
        "cap": None,
        "active": 0,
        "queue_depth": 0,
        "oldest_wait_seconds": 0.0,
        "head_stall_reason": None,
        "shutdown": False,
        "admitted_total": 0,
        "released_total": 0,
        "cancelled_queued_total": 0,
    }


def _empty_residue_block() -> dict[str, Any]:
    return {
        "admission_blocked": False,
        "block_reason": None,
        "survivors_count": 0,
        "survivors": [],
    }
