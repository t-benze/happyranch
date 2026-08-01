"""Tests for GET /api/v1/orgs/{slug}/dashboard/summary."""
from __future__ import annotations

import asyncio
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

    # Monkeypatch persist to raise AFTER the compose succeeds
    original_persist = mgr.persist
    def failing_persist(projection):
        raise OSError("simulated disk write failure")
    mgr.persist = failing_persist

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
        mgr.persist = original_persist


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
    # Monkeypatch persist to raise — the in-memory _projection must stay None
    original_persist = mgr.persist
    def failing_persist(projection):
        raise OSError("disk failure")
    mgr.persist = failing_persist
    try:
        ok = asyncio.run(mgr.warm(db=org_state.db, kb_store=kb_store, teams=org_state.teams))
        assert ok is False
        assert mgr.get_projection() is None, (
            "in-memory projection must not be updated on persist failure"
        )
    finally:
        mgr.persist = original_persist


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
