"""Tests for GET /api/v1/orgs/{slug}/dashboard/summary."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from runtime.infrastructure.kb_store import KBStore


def test_summary_returns_full_shape(tmp_home, app, org_state, auth_headers) -> None:
    """After projection warm, returns 200 with full shape including generated_at."""
    # Warm the projection before calling the route
    kb_store = KBStore(org_state.root / "kb")
    asyncio.run(org_state.dashboard_projection.warm(
        db=org_state.db, kb_store=kb_store, teams=org_state.teams,
    ))
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/dashboard/summary",
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert "heartbeat" in body
    assert len(body["heartbeat"]) == 24
    assert "narrative_counts" in body
    assert set(body["narrative_counts"].keys()) == {
        "completed_today", "failed_today", "escalated_open",
        "kb_added_today", "agents_active_now", "spend_today_usd",
    }
    assert "escalations" in body
    assert "active_by_team" in body
    assert "recent_activity" in body
    assert "updates_this_week" in body
    assert "org_pulse" in body
    assert "org_age_days" in body
    assert "server_now" in body
    assert "generated_at" in body
    # generated_at must be non-null after warm
    assert body["generated_at"] is not None
    # generated_at must differ from server_now (response-time vs projection-time)
    assert body["generated_at"] != body["server_now"]


def test_summary_no_projection_returns_503(tmp_home, app, org_state, auth_headers) -> None:
    """Cold-start / no projection yet → 503 (cache-only, no synchronous compose)."""
    # Ensure no projection is in memory
    assert org_state.dashboard_projection.get_projection() is None
    client = TestClient(app)
    r = client.get(
        f"/api/v1/orgs/{org_state.slug}/dashboard/summary",
        headers=auth_headers,
    )
    assert r.status_code == 503
    body = r.json()
    assert "detail" in body
    assert "not yet available" in body["detail"]


def test_summary_unknown_slug_returns_404(tmp_home, app, auth_headers) -> None:
    client = TestClient(app)
    r = client.get(
        "/api/v1/orgs/nope/dashboard/summary",
        headers=auth_headers,
    )
    assert r.status_code == 404


def test_summary_requires_auth(tmp_home, app, org_state) -> None:
    client = TestClient(app)
    r = client.get(f"/api/v1/orgs/{org_state.slug}/dashboard/summary")
    assert r.status_code == 401


# ── THR-129 fix-forward: projection validation tests ────────────────────

import json
from pathlib import Path

from runtime.orchestrator.dashboard_projection import (
    DashboardProjectionManager,
    DashboardProjection,
    _SUPPORTED_VERSION,
)


def test_load_from_disk_rejects_foreign_org_slug(tmp_home, org_state) -> None:
    """A sidecar with a different org_slug must be rejected (cross-org safety)."""
    mgr = org_state.dashboard_projection
    # Write a valid projection with a foreign slug
    foreign = DashboardProjection(
        version=_SUPPORTED_VERSION,
        org_slug="other-org",
        generated_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        payload={"heartbeat": [], "narrative_counts": {"completed_today": 0},
                  "escalations": [], "stale_escalations": [],
                  "active_by_team": [], "recent_activity": [],
                  "updates_this_week": [], "org_pulse": [],
                  "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                  "generated_at": "2026-06-01T12:00:00"},
    )
    mgr.persist(foreign)
    result = mgr.load_from_disk()
    assert result is None, f"Foreign org_slug should be rejected, got {result}"


def test_load_from_disk_rejects_unsupported_version(tmp_home, org_state) -> None:
    """A sidecar with an unsupported version must be rejected."""
    mgr = org_state.dashboard_projection
    raw = {
        "version": 99,
        "org_slug": org_state.slug,
        "generated_at": "2026-06-01T12:00:00",
        "payload": {"heartbeat": [], "narrative_counts": {}, "escalations": [],
                     "stale_escalations": [], "active_by_team": [],
                     "recent_activity": [], "updates_this_week": [],
                     "org_pulse": [], "org_age_days": 0, "server_now": "...",
                     "generated_at": None},
    }
    path = mgr.projection_path
    path.write_text(json.dumps(raw), encoding="utf-8")
    result = mgr.load_from_disk()
    assert result is None, f"Unsupported version should be rejected, got {result}"


def test_load_from_disk_rejects_bad_json(tmp_home, org_state) -> None:
    """A non-JSON file must be rejected as cache unavailable."""
    mgr = org_state.dashboard_projection
    mgr.projection_path.write_text("not json at all", encoding="utf-8")
    result = mgr.load_from_disk()
    assert result is None


def test_load_from_disk_rejects_malformed_payload(tmp_home, org_state) -> None:
    """Valid JSON envelope but payload fails DashboardSummaryResponse validation."""
    mgr = org_state.dashboard_projection
    raw = {
        "version": _SUPPORTED_VERSION,
        "org_slug": org_state.slug,
        "generated_at": "2026-06-01T12:00:00",
        "payload": {"heartbeat": "not_a_list",  # should be list
                     "narrative_counts": "not_an_object",
                     "escalations": [], "stale_escalations": [],
                     "active_by_team": [], "recent_activity": [],
                     "updates_this_week": [], "org_pulse": [],
                     "org_age_days": 0, "server_now": "...",
                     "generated_at": None},
    }
    mgr.projection_path.write_text(json.dumps(raw), encoding="utf-8")
    result = mgr.load_from_disk()
    assert result is None, f"Malformed payload should be rejected, got {result}"


def test_load_from_disk_rejects_empty_file(tmp_home, org_state) -> None:
    """An empty file is not valid JSON → rejected."""
    mgr = org_state.dashboard_projection
    mgr.projection_path.write_text("", encoding="utf-8")
    result = mgr.load_from_disk()
    assert result is None


def test_warm_preserves_prior_projection_on_failure(tmp_home, org_state) -> None:
    """When warm() fails, the prior in-memory projection AND sidecar are preserved."""
    import asyncio
    kb_store = KBStore(org_state.root / "kb")
    mgr = org_state.dashboard_projection
    # Seed a prior good projection
    prior_payload = {"heartbeat": [{"hour": 0, "steps": 42, "failed": 0, "tier": "ok"}],
                      "narrative_counts": {"completed_today": 1, "failed_today": 0,
                                          "escalated_open": 0, "kb_added_today": 0,
                                          "agents_active_now": 0, "spend_today_usd": 0.0},
                      "escalations": [], "stale_escalations": [],
                      "active_by_team": [], "recent_activity": [],
                      "updates_this_week": [], "org_pulse": [],
                      "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                      "generated_at": "2026-06-01T12:00:00"}
    prior = DashboardProjection(
        version=_SUPPORTED_VERSION,
        org_slug=org_state.slug,
        generated_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        payload=prior_payload,
    )
    mgr._projection = prior
    mgr.persist(prior)

    # Now make warm fail by passing a broken db that raises
    class BrokenDB:
        pass
    ok = asyncio.run(mgr.warm(db=BrokenDB(), kb_store=kb_store, teams=org_state.teams))
    assert ok is False, "warm should return False on failure"
    # The prior projection must be intact in memory
    current = mgr.get_projection()
    assert current is not None, "prior projection must be preserved in memory"
    assert current.payload["heartbeat"][0]["steps"] == 42, "prior payload preserved"
    # The sidecar must also be intact
    from_disk = mgr.load_from_disk()
    assert from_disk is not None
    assert from_disk.payload["heartbeat"][0]["steps"] == 42, "prior sidecar preserved"


def test_warm_preserves_prior_on_persist_failure(tmp_home, org_state, monkeypatch) -> None:
    """When compose succeeds but disk persist fails, the prior projection
    remains in memory AND the sidecar is preserved (write-then-rename atomic)."""
    import asyncio
    kb_store = KBStore(org_state.root / "kb")
    mgr = org_state.dashboard_projection
    # Seed a prior good projection
    prior_payload = {"heartbeat": [{"hour": 0, "steps": 7, "failed": 0, "tier": "ok"}],
                      "narrative_counts": {"completed_today": 0, "failed_today": 0,
                                          "escalated_open": 0, "kb_added_today": 0,
                                          "agents_active_now": 0, "spend_today_usd": 0.0},
                      "escalations": [], "stale_escalations": [],
                      "active_by_team": [], "recent_activity": [],
                      "updates_this_week": [], "org_pulse": [],
                      "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                      "generated_at": "2026-06-01T12:00:00"}
    prior = DashboardProjection(
        version=_SUPPORTED_VERSION,
        org_slug=org_state.slug,
        generated_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        payload=prior_payload,
    )
    mgr._projection = prior
    mgr.persist(prior)

    # Monkeypatch _atomic_persist to raise AFTER the compose succeeds
    original_persist = mgr._atomic_persist
    def failing_persist(projection):
        raise OSError("simulated disk write failure")
    mgr._atomic_persist = failing_persist

    try:
        ok = asyncio.run(mgr.warm(db=org_state.db, kb_store=kb_store, teams=org_state.teams))
        assert ok is False, "warm should return False on persist failure"
        # The prior projection must still be in memory
        current = mgr.get_projection()
        assert current is not None, "prior projection must be preserved in memory"
        assert current.payload["heartbeat"][0]["steps"] == 7, "prior payload preserved"
        # The prior sidecar must still be on disk
        from_disk = mgr.load_from_disk()
        assert from_disk is not None
        assert from_disk.payload["heartbeat"][0]["steps"] == 7, "prior sidecar preserved"
    finally:
        mgr._atomic_persist = original_persist


def test_warm_atomic_publish_never_updates_in_memory_before_disk(
    tmp_home, org_state, monkeypatch,
) -> None:
    """The in-memory projection must never be set before disk persist succeeds.
    If serialization itself fails, the projection field stays unchanged."""
    import asyncio
    kb_store = KBStore(org_state.root / "kb")
    mgr = org_state.dashboard_projection
    # Ensure no prior projection
    assert mgr.get_projection() is None
    # Monkeypatch _atomic_persist to raise — the in-memory _projection must stay None
    original_persist = mgr._atomic_persist
    def failing_persist(projection):
        raise OSError("disk failure")
    mgr._atomic_persist = failing_persist
    try:
        ok = asyncio.run(mgr.warm(db=org_state.db, kb_store=kb_store, teams=org_state.teams))
        assert ok is False
        assert mgr.get_projection() is None, (
            "in-memory projection must not be updated on persist failure"
        )
    finally:
        mgr._atomic_persist = original_persist


def test_scheduler_cancel_reap_no_hang(tmp_home, org_state) -> None:
    """cancel_scheduler + reap_scheduler must complete without hanging,
    even when the scheduler loop is running. This proves the two-step
    cancel-then-await pattern doesn't deadlock on a stuck warm path."""
    import asyncio
    from runtime.infrastructure.kb_store import KBStore

    mgr = org_state.dashboard_projection
    kb_store = KBStore(org_state.root / "kb")
    # Start the scheduler
    loop = asyncio.new_event_loop()
    try:
        mgr.start_scheduler(db=org_state.db, kb_store=kb_store, teams=org_state.teams, loop=loop)
        # Let one tick fire (if possible) then cancel
        async def _run_then_cancel():
            await asyncio.sleep(0.05)  # short wait
            mgr.cancel_scheduler()
            await mgr.reap_scheduler()
        loop.run_until_complete(_run_then_cancel())
        assert mgr._refresh_task is None
    finally:
        loop.close()


# ── THR-129 fix-forward round 2: strict validation regression tests ────────

def test_load_from_disk_rejects_boolean_version(tmp_home, org_state) -> None:
    """Boolean true for version must be rejected (Pydantic int Field(strict=True))."""
    mgr = org_state.dashboard_projection
    raw = {
        "version": True,
        "org_slug": org_state.slug,
        "generated_at": "2026-06-01T12:00:00",
        "payload": {"heartbeat": [], "narrative_counts": {},
                     "escalations": [], "stale_escalations": [],
                     "active_by_team": [], "recent_activity": [],
                     "updates_this_week": [], "org_pulse": [],
                     "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                     "generated_at": None},
    }
    mgr.projection_path.write_text(json.dumps(raw), encoding="utf-8")
    result = mgr.load_from_disk()
    assert result is None, f"Boolean version should be rejected, got {result}"


def test_load_from_disk_rejects_string_numeric_version(tmp_home, org_state) -> None:
    """String '1' for version must be rejected (not coerced to int 1)."""
    mgr = org_state.dashboard_projection
    raw = {
        "version": "1",
        "org_slug": org_state.slug,
        "generated_at": "2026-06-01T12:00:00",
        "payload": {"heartbeat": [], "narrative_counts": {},
                     "escalations": [], "stale_escalations": [],
                     "active_by_team": [], "recent_activity": [],
                     "updates_this_week": [], "org_pulse": [],
                     "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                     "generated_at": None},
    }
    mgr.projection_path.write_text(json.dumps(raw), encoding="utf-8")
    result = mgr.load_from_disk()
    assert result is None, f"String version should be rejected, got {result}"


def test_load_from_disk_rejects_unknown_envelope_fields(tmp_home, org_state) -> None:
    """Unknown fields in the envelope must be rejected (extra='forbid')."""
    mgr = org_state.dashboard_projection
    raw = {
        "version": 1,
        "org_slug": org_state.slug,
        "generated_at": "2026-06-01T12:00:00",
        "payload": {"heartbeat": [], "narrative_counts": {},
                     "escalations": [], "stale_escalations": [],
                     "active_by_team": [], "recent_activity": [],
                     "updates_this_week": [], "org_pulse": [],
                     "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                     "generated_at": None},
        "extra_field": "should_be_rejected",
    }
    mgr.projection_path.write_text(json.dumps(raw), encoding="utf-8")
    result = mgr.load_from_disk()
    assert result is None, f"Unknown envelope fields should be rejected, got {result}"


def test_load_from_disk_rejects_payload_string_numeric_coercion(
    tmp_home, org_state,
) -> None:
    """String numeric fields in payload (e.g. org_age_days='42') must be
    rejected via strict validation — never normalized by the route."""
    mgr = org_state.dashboard_projection
    raw = {
        "version": 1,
        "org_slug": org_state.slug,
        "generated_at": "2026-06-01T12:00:00",
        "payload": {"heartbeat": [],
                     "narrative_counts": {"completed_today": "0", "failed_today": 0,
                                         "escalated_open": 0, "kb_added_today": 0,
                                         "agents_active_now": 0, "spend_today_usd": 0},
                     "escalations": [], "stale_escalations": [],
                     "active_by_team": [], "recent_activity": [],
                     "updates_this_week": [], "org_pulse": [],
                     "org_age_days": "not_a_number",
                     "server_now": "2026-06-01T12:00:00",
                     "generated_at": None},
    }
    mgr.projection_path.write_text(json.dumps(raw), encoding="utf-8")
    result = mgr.load_from_disk()
    assert result is None, f"String numeric payload coercion should be rejected, got {result}"


def test_load_from_disk_rejects_payload_boolean_list_coercion(
    tmp_home, org_state,
) -> None:
    """Boolean value for list field (e.g. heartbeat=True) must be rejected."""
    mgr = org_state.dashboard_projection
    raw = {
        "version": 1,
        "org_slug": org_state.slug,
        "generated_at": "2026-06-01T12:00:00",
        "payload": {"heartbeat": True,  # should be a list
                     "narrative_counts": {"completed_today": 0, "failed_today": 0,
                                         "escalated_open": 0, "kb_added_today": 0,
                                         "agents_active_now": 0, "spend_today_usd": 0},
                     "escalations": [], "stale_escalations": [],
                     "active_by_team": [], "recent_activity": [],
                     "updates_this_week": [], "org_pulse": [],
                     "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                     "generated_at": None},
    }
    mgr.projection_path.write_text(json.dumps(raw), encoding="utf-8")
    result = mgr.load_from_disk()
    assert result is None, f"Boolean list payload coercion should be rejected, got {result}"


# ── THR-129 fix-forward round 2: comprehensive seam fault-injection tests ──

def test_warm_preserves_prior_on_envelope_validation_failure(
    tmp_home, org_state, monkeypatch,
) -> None:
    """When compose succeeds but envelope (DashboardProjection) validation
    fails, the prior in-memory projection AND sidecar are preserved."""
    import asyncio
    kb_store = KBStore(org_state.root / "kb")
    mgr = org_state.dashboard_projection
    # Seed a prior good projection with a distinctive marker
    prior_payload = {"heartbeat": [{"hour": 0, "steps": 99, "failed": 0, "tier": "ok"}],
                      "narrative_counts": {"completed_today": 1, "failed_today": 0,
                                          "escalated_open": 0, "kb_added_today": 0,
                                          "agents_active_now": 0, "spend_today_usd": 0.0},
                      "escalations": [], "stale_escalations": [],
                      "active_by_team": [], "recent_activity": [],
                      "updates_this_week": [], "org_pulse": [],
                      "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                      "generated_at": "2026-06-01T12:00:00"}
    prior = DashboardProjection(
        version=_SUPPORTED_VERSION,
        org_slug=org_state.slug,
        generated_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        payload=prior_payload,
    )
    mgr._projection = prior
    mgr.persist(prior)
    # Capture the exact old sidecar bytes
    old_bytes = mgr.projection_path.read_bytes()

    # Monkeypatch DashboardProjection constructor to raise AFTER compose
    # succeeds but before the envelope can be accepted
    from runtime.orchestrator import dashboard_projection as dp_mod
    original_init = dp_mod.DashboardProjection.__init__
    def failing_init(self, **kwargs):
        raise ValueError("simulated envelope validation failure")
    monkeypatch.setattr(dp_mod.DashboardProjection, "__init__", failing_init)

    ok = asyncio.run(mgr.warm(db=org_state.db, kb_store=kb_store, teams=org_state.teams))
    assert ok is False, "warm should return False on envelope validation failure"
    # Prior projection must be preserved in memory
    current = mgr.get_projection()
    assert current is not None, "prior projection must be preserved in memory"
    assert current.payload["heartbeat"][0]["steps"] == 99, "prior payload preserved"
    # Prior sidecar must be intact byte-for-byte
    current_bytes = mgr.projection_path.read_bytes()
    assert current_bytes == old_bytes, (
        f"old sidecar bytes must be preserved byte-for-byte; "
        f"old={len(old_bytes)}B, current={len(current_bytes)}B"
    )


def test_warm_preserves_prior_on_serialization_failure(
    tmp_home, org_state, monkeypatch,
) -> None:
    """When compose + envelope succeed but serialization (model_dump_json)
    fails, the prior in-memory projection AND sidecar are preserved."""
    import asyncio
    kb_store = KBStore(org_state.root / "kb")
    mgr = org_state.dashboard_projection
    prior_payload = {"heartbeat": [{"hour": 0, "steps": 77, "failed": 0, "tier": "ok"}],
                      "narrative_counts": {"completed_today": 1, "failed_today": 0,
                                          "escalated_open": 0, "kb_added_today": 0,
                                          "agents_active_now": 0, "spend_today_usd": 0.0},
                      "escalations": [], "stale_escalations": [],
                      "active_by_team": [], "recent_activity": [],
                      "updates_this_week": [], "org_pulse": [],
                      "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                      "generated_at": "2026-06-01T12:00:00"}
    prior = DashboardProjection(
        version=_SUPPORTED_VERSION,
        org_slug=org_state.slug,
        generated_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        payload=prior_payload,
    )
    mgr._projection = prior
    mgr.persist(prior)
    old_bytes = mgr.projection_path.read_bytes()

    # Monkeypatch model_dump_json to raise AFTER envelope validation succeeds
    from runtime.orchestrator import dashboard_projection as dp_mod
    original_dump = dp_mod.DashboardProjection.model_dump_json
    def failing_dump(self, **kwargs):
        raise RuntimeError("simulated serialization failure")
    monkeypatch.setattr(dp_mod.DashboardProjection, "model_dump_json", failing_dump)

    ok = asyncio.run(mgr.warm(db=org_state.db, kb_store=kb_store, teams=org_state.teams))
    assert ok is False, "warm should return False on serialization failure"
    current = mgr.get_projection()
    assert current is not None, "prior projection must be preserved in memory"
    assert current.payload["heartbeat"][0]["steps"] == 77, "prior payload preserved"
    current_bytes = mgr.projection_path.read_bytes()
    assert current_bytes == old_bytes, "old sidecar bytes must be preserved byte-for-byte"


def test_warm_preserves_prior_on_os_replace_failure(
    tmp_home, org_state, monkeypatch,
) -> None:
    """When compose + serialize succeed but os.replace fails, the prior
    in-memory projection AND the exact old sidecar bytes are preserved.
    os.replace never unlinks the canonical file first — the old sidecar
    is intact byte-for-byte without any non-atomic recovery rewrite."""
    import asyncio
    kb_store = KBStore(org_state.root / "kb")
    mgr = org_state.dashboard_projection
    prior_payload = {"heartbeat": [{"hour": 0, "steps": 55, "failed": 0, "tier": "ok"}],
                      "narrative_counts": {"completed_today": 1, "failed_today": 0,
                                          "escalated_open": 0, "kb_added_today": 0,
                                          "agents_active_now": 0, "spend_today_usd": 0.0},
                      "escalations": [], "stale_escalations": [],
                      "active_by_team": [], "recent_activity": [],
                      "updates_this_week": [], "org_pulse": [],
                      "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                      "generated_at": "2026-06-01T12:00:00"}
    prior = DashboardProjection(
        version=_SUPPORTED_VERSION,
        org_slug=org_state.slug,
        generated_at=datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
        payload=prior_payload,
    )
    mgr._projection = prior
    mgr.persist(prior)
    old_bytes = mgr.projection_path.read_bytes()

    # Monkeypatch os.replace to raise — simulates replace failure after tmp
    # write succeeds. The canonical file must be untouched because os.replace
    # never unlinks the target first.
    original_replace = os.replace
    def failing_replace(src, dst):
        raise OSError("simulated os.replace failure")
    monkeypatch.setattr(os, "replace", failing_replace)

    ok = asyncio.run(mgr.warm(db=org_state.db, kb_store=kb_store, teams=org_state.teams))
    assert ok is False, "warm should return False on replace failure"
    # Prior projection in memory
    current = mgr.get_projection()
    assert current is not None, "prior projection must be preserved in memory"
    assert current.payload["heartbeat"][0]["steps"] == 55, "prior payload preserved"
    # Sidecar bytes: os.replace never unlinks the target before replacement,
    # so the canonical file is automatically intact on failure — no
    # non-atomic recovery rewrite needed.
    current_bytes = mgr.projection_path.read_bytes()
    assert current_bytes == old_bytes, (
        f"old sidecar bytes must be preserved byte-for-byte; "
        f"os.replace guarantees this (no unlink-then-rename window). "
        f"old={len(old_bytes)}B, current={len(current_bytes)}B"
    )
    # Tmp file debris is tolerated — it cannot affect the canonical cache.
    tmp_path = mgr.projection_path.with_suffix(
        mgr.projection_path.suffix + ".tmp"
    )


# ── THR-129 fix-forward round 2: real scheduler shutdown test ───────────────

def test_scheduler_cancel_during_blocking_warm_no_hang(tmp_home, org_state, monkeypatch) -> None:
    """When the scheduler is cancelled during a blocking warm (stuck in
    asyncio.to_thread), cancel_scheduler + reap_scheduler must complete
    under a short deadline. Must not wait for cooperative completion of
    the stuck warm, and must not leak/unown task exceptions."""
    import asyncio
    from runtime.infrastructure.kb_store import KBStore
    from runtime.orchestrator import dashboard_projection as dp_mod

    mgr = org_state.dashboard_projection
    kb_store = KBStore(org_state.root / "kb")

    # Shorten the interval so the scheduler tick fires quickly
    monkeypatch.setattr(dp_mod, "_REFRESH_INTERVAL_SECONDS", 0.1)

    # Event that the blocking warm will wait on (forever until set)
    warm_blocked = asyncio.Event()
    warm_entered = asyncio.Event()

    original_warm = mgr.warm
    async def blocking_warm(db, kb_store, teams):
        warm_entered.set()
        # Block forever (or until cancelled) — simulates a warm stuck in
        # asyncio.to_thread or a long-running compose
        await warm_blocked.wait()
        return True
    mgr.warm = blocking_warm

    try:
        loop = asyncio.new_event_loop()
        try:
            mgr.start_scheduler(
                db=org_state.db, kb_store=kb_store,
                teams=org_state.teams, loop=loop,
            )

            async def _cancel_during_warm():
                # Wait for warm to enter its blocking path
                await asyncio.wait_for(warm_entered.wait(), timeout=5.0)
                # Cancel while warm is blocking — must not hang
                mgr.cancel_scheduler()
                # Reap under a short deadline
                await asyncio.wait_for(mgr.reap_scheduler(), timeout=2.0)
                # Release the blocked warm so it can finish cleanly
                warm_blocked.set()
                # Verify task is cleaned up
                assert mgr._refresh_task is None, (
                    "_refresh_task should be None after reap; "
                    "shutdown must not wait for cooperative warm completion"
                )

            loop.run_until_complete(_cancel_during_warm())
        finally:
            # Clean up any remaining tasks
            pending = asyncio.all_tasks(loop)
            for t in pending:
                t.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()
    finally:
        mgr.warm = original_warm


# ── THR-129 fix-forward round 3: strict validation + async startup ──────

def test_load_from_disk_rejects_numeric_generated_at(tmp_home, org_state) -> None:
    """Numeric generated_at (e.g. 12345) must be rejected by strict
    envelope validation — never coerced to a datetime."""
    mgr = org_state.dashboard_projection
    raw = {
        "version": 1,
        "org_slug": org_state.slug,
        "generated_at": 12345,  # numeric, not ISO string
        "payload": {"heartbeat": [], "narrative_counts": {},
                     "escalations": [], "stale_escalations": [],
                     "active_by_team": [], "recent_activity": [],
                     "updates_this_week": [], "org_pulse": [],
                     "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                     "generated_at": None},
    }
    mgr.projection_path.write_text(json.dumps(raw), encoding="utf-8")
    result = mgr.load_from_disk()
    assert result is None, f"Numeric generated_at should be rejected, got {result}"


def test_load_from_disk_rejects_boolean_generated_at(tmp_home, org_state) -> None:
    """Boolean generated_at (e.g. true) must be rejected by strict
    envelope validation."""
    mgr = org_state.dashboard_projection
    raw = {
        "version": 1,
        "org_slug": org_state.slug,
        "generated_at": True,  # boolean, not ISO string
        "payload": {"heartbeat": [], "narrative_counts": {},
                     "escalations": [], "stale_escalations": [],
                     "active_by_team": [], "recent_activity": [],
                     "updates_this_week": [], "org_pulse": [],
                     "org_age_days": 0, "server_now": "2026-06-01T12:00:00",
                     "generated_at": None},
    }
    mgr.projection_path.write_text(json.dumps(raw), encoding="utf-8")
    result = mgr.load_from_disk()
    assert result is None, f"Boolean generated_at should be rejected, got {result}"


def test_load_from_disk_retains_validated_payload(tmp_home, org_state) -> None:
    """A valid sidecar passes strict validation AND the returned projection
    carries the validated (re-serialized) payload from DashboardSummaryResponse,
    not the raw on-disk dict. This proves validation output is not discarded."""
    import asyncio
    kb_store = KBStore(org_state.root / "kb")
    mgr = org_state.dashboard_projection
    # Write a real valid projection via warm (which composes + persists)
    ok = asyncio.run(mgr.warm(db=org_state.db, kb_store=kb_store, teams=org_state.teams))
    assert ok is True, "warm must succeed to seed a valid projection"
    # Now load it — the result must be a valid DashboardProjection
    result = mgr.load_from_disk()
    assert result is not None, "valid sidecar must load successfully"
    assert isinstance(result, DashboardProjection)
    # The payload must be the validated/re-serialized form (with all expected keys)
    assert isinstance(result.payload, dict)
    assert "heartbeat" in result.payload
    assert "narrative_counts" in result.payload
    assert "server_now" in result.payload
    # server_now in the persisted payload matches the generated_at time
    assert result.payload["generated_at"] is None  # null per DashboardSummaryResponse default
    # generated_at is the projection timestamp
    assert result.generated_at is not None


def test_load_from_disk_rejects_payload_with_extra_fields(tmp_home, org_state) -> None:
    """Payload with unknown/extra fields (not in DashboardSummaryResponse)
    must be rejected by strict validation.

    This regression seeds a FULLY VALID canonical payload (every required
    field present), adds exactly one unknown key, and proves load_from_disk()
    returns None (cache-unavailable) without mutating the old projection or
    altering the canonical sidecar bytes."""
    import asyncio
    mgr = org_state.dashboard_projection
    kb_store = KBStore(org_state.root / "kb")

    # Seed a valid canonical projection via warm (compose + persist),
    # ensuring the sidecar holds a complete DashboardSummaryResponse.
    ok = asyncio.run(mgr.warm(db=org_state.db, kb_store=kb_store, teams=org_state.teams))
    assert ok is True, "warm must succeed to seed a valid projection"
    # Verify it loads cleanly before tampering
    before = mgr.load_from_disk()
    assert before is not None, "canonical sidecar must load cleanly"
    old_bytes = mgr.projection_path.read_bytes()

    # Now add exactly one unknown key to the payload, keep everything else
    # identical (the payload is a full DashboardSummaryResponse).
    tampered = json.loads(old_bytes)
    tampered["payload"]["extra_payload_field"] = "should_be_rejected"
    mgr.projection_path.write_text(json.dumps(tampered), encoding="utf-8")

    result = mgr.load_from_disk()
    assert result is None, (
        f"Payload with extra fields should be rejected, got {result}"
    )
    # Prove the sidecar was not mutated by the load attempt
    current_bytes = mgr.projection_path.read_bytes()
    assert current_bytes == json.dumps(tampered).encode("utf-8"), (
        "canonical sidecar bytes must be unchanged after rejection"
    )
    # Prove the in-memory projection is not affected (old projection
    # was None before warm — warm set it, but load_from_disk failure
    # should not alter the in-memory ref)
    assert mgr.get_projection() is not None, (
        "in-memory projection must not be altered by load-from-disk rejection"
    )


def test_lifespan_async_warm_serves_503_and_clean_shutdown(
    tmp_home, daemon_state, auth_headers, monkeypatch,
) -> None:
    """Daemon-lifespan regression: enter the actual FastAPI lifespan via
    TestClient(app) as a context manager, patch the imported production
    compose_dashboard_summary binding so the first compose runs and blocks in
    a real worker thread, demonstrate cache-only 503 while that cold initial
    compose is blocked, keep it blocked while exiting TestClient so lifespan
    shutdown cancels and reaps the scheduler refresh task without waiting for
    cooperative thread completion, prove shutdown completes under a bounded
    deadline with no unowned task exception, then release and account for the
    worker and assert clean eventual completion.

    This test exercises app.py's shipping lifespan ownership + cleanup path
    (cancel_scheduler → reap_scheduler). It does NOT call
    manager.start_scheduler manually.

    The TestClient lifespan context is run in a daemon thread with a bounded
    join-timeout watchdog. If the lifespan shutdown hangs — e.g. a regression
    where cancel_scheduler does not properly cancel the warm task and
    asyncio.gather blocks — the join times out and the test fails promptly
    with a clear diagnostic, instead of hanging until the outer pytest/CI
    timeout."""
    import threading

    from fastapi.testclient import TestClient
    from runtime.daemon.app import create_app
    from runtime.orchestrator import dashboard_projection as dp_mod

    # Ensure no prior projection for the alpha org.
    org_state = daemon_state.orgs["alpha"]
    assert org_state.dashboard_projection.get_projection() is None

    # Patch compose_dashboard_summary to block in a real worker thread.
    compose_entered = threading.Event()
    compose_unblock = threading.Event()
    compose_done = threading.Event()
    # Track whether cancel+reap have completed (i.e., the lifespan has
    # exercised the cancel-before-await path while the warm was blocked).
    cancel_reap_done = threading.Event()

    original_compose = dp_mod.compose_dashboard_summary
    def blocking_compose(*, db, kb_store, teams, now):
        compose_entered.set()
        # Block until released — lifespan shutdown must complete its
        # cancel+reap path before we release this thread.
        compose_unblock.wait()
        compose_done.set()
        # Return a minimal valid dict for clean thread completion.
        # The cancelled asyncio task does not consume the return value.
        return {
            "heartbeat": [],
            "narrative_counts": {
                "completed_today": 0, "failed_today": 0,
                "escalated_open": 0, "kb_added_today": 0,
                "agents_active_now": 0, "spend_today_usd": 0.0,
            },
            "escalations": [], "stale_escalations": [],
            "active_by_team": [], "recent_activity": [],
            "updates_this_week": [], "org_pulse": [],
            "org_age_days": 0, "server_now": "2026-01-01T00:00:00",
            "generated_at": None,
        }
    monkeypatch.setattr(dp_mod, "compose_dashboard_summary", blocking_compose)

    # Shorten the refresh interval.
    monkeypatch.setattr(dp_mod, "_REFRESH_INTERVAL_SECONDS", 0.1)

    # Monkeypatch reap_scheduler to release the blocked worker AFTER
    # cancel+reap complete. The lifespan calls cancel_scheduler, then
    # asyncio.gather (returns immediately with CancelledError), then
    # reap_scheduler. We release the worker inside reap_scheduler so:
    #  (a) cancel+reap complete without waiting for the thread, and
    #  (b) the thread finishes before loop.shutdown_default_executor()
    #      (called by anyio/asyncio.run during TestClient portal stop).
    mgr = org_state.dashboard_projection
    original_reap = mgr.reap_scheduler
    async def _reap_and_release():
        await original_reap()
        # Cancel+reap are done — release the worker thread now.
        cancel_reap_done.set()
        compose_unblock.set()
    monkeypatch.setattr(mgr, "reap_scheduler", _reap_and_release)

    try:
        app = create_app(daemon_state)

        # ── Bounded-deadline lifespan watchdog ──────────────────────
        # Run the TestClient lifespan context in a daemon thread with a
        # bounded join timeout. If the lifespan shutdown hangs — e.g. a
        # regression where cancel_scheduler does not properly cancel the
        # warm task and asyncio.gather blocks — the join times out and the
        # test fails promptly with a clear diagnostic, instead of hanging
        # until the outer pytest/CI timeout.
        _DEADLINE_SECONDS = 5.0
        _context_completed = threading.Event()
        _context_error = [None]

        def _run_context():
            try:
                with TestClient(app) as client:
                    # Warm must enter the blocked compose — proves the
                    # lifespan yielded before warm completed (no sync await).
                    assert compose_entered.wait(timeout=10), (
                        "compose_dashboard_summary must be entered by "
                        "lifespan's initial warm task"
                    )
                    # Cache-only dashboard route must return 503 — the warm
                    # is still blocked, no projection in memory.
                    r = client.get(
                        f"/api/v1/orgs/{org_state.slug}/dashboard/summary",
                        headers=auth_headers,
                    )
                    assert r.status_code == 503, (
                        f"Expected 503 while warm is blocked, got {r.status_code}"
                    )
                    assert "not yet available" in r.json()["detail"]
                    # DO NOT release compose — exit while warm is in-flight
                    # so lifespan shutdown exercises cancel-before-await/reap.
                # Lifespan shutdown completed in __exit__.
            except Exception as exc:
                _context_error[0] = exc
            finally:
                _context_completed.set()

        _context_thread = threading.Thread(target=_run_context, daemon=True)
        _context_thread.start()
        _context_thread.join(timeout=_DEADLINE_SECONDS)

        assert _context_completed.is_set(), (
            f"TestClient lifespan shutdown exceeded bounded deadline "
            f"({_DEADLINE_SECONDS}s) — cancel_scheduler/reap regression: "
            f"the lifespan shutdown did not complete within the bounded "
            f"watchdog; cancel_scheduler may not have properly cancelled "
            f"the warm task, or asyncio.gather may be blocked"
        )
        if _context_error[0] is not None:
            raise _context_error[0]

        # ── Post-shutdown assertions ────────────────────────────────
        # The lifespan shutdown has completed: cancel_scheduler,
        # asyncio.gather (return_exceptions=True), and reap_scheduler
        # all finished. Inside the monkeypatched reap_scheduler, we
        # released the worker thread and set cancel_reap_done.

        # Verify that cancel+reap completed (and released the worker).
        assert cancel_reap_done.is_set(), (
            "cancel+reap must complete during lifespan shutdown"
        )

        # Verify scheduler cleanup: _refresh_task must be None after reap.
        assert mgr._refresh_task is None, (
            "_refresh_task must be None after lifespan shutdown (reap completed)"
        )

        # Verify the worker thread completed cleanly.
        assert compose_done.wait(timeout=5), (
            "blocked compose must complete cleanly after release"
        )
    finally:
        dp_mod.compose_dashboard_summary = original_compose
        mgr.reap_scheduler = original_reap
