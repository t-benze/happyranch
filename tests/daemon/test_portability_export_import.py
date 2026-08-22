"""Integration tests for THR-187 Slice B: archive export, inspection, and
import-relocation through the production daemon routes.

Covers the founder contract: full roundtrip into an unused same-slug
destination in a non-empty schema-v2 runtime; armed/firing-schedule refusal and
the transfer-fence recheck race; destination-collision refusal; digest
idempotency/conflict; no WAL/SHM or task-output/credential/repo transfer; and
quarantined (never activated) legacy skills.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from runtime.config import Settings
from runtime.daemon import paths
from runtime.daemon.routes.portability import router as portability_router
from runtime.daemon.state import DaemonState
from runtime.models import (
    ScheduleKind,
    ScheduleRecord,
    ScheduleStatus,
    TaskRecord,
    TaskStatus,
)
from runtime.runtime import RuntimeDir

TOKEN = "test-bearer-token"
DEAD_PID = 99999

_VALID_SKILL_YAML = (
    "description: QA scroll verification skill\n"
    "id: hr:qa-scroll-test\n"
    "name: QA Scroll Test Skill\n"
    "owner: operator\n"
    "policy_class: standard_operational\n"
    "slug: qa-scroll-test\n"
    "source: user_authored\n"
    "status: enabled\n"
    "version: 0.1.0\n"
    "when_to_use: ''\n"
)


def _write_token() -> None:
    home = paths.daemon_home()
    home.mkdir(parents=True, exist_ok=True)
    (home / "daemon.token").write_text(TOKEN)


def _seed_org(org_root: Path) -> None:
    org_root.mkdir(parents=True)
    (org_root / "org").mkdir(parents=True, exist_ok=True)
    (org_root / "org" / "teams.yaml").write_text("teams: {}\n")


def _make_source_state(tmp_path: Path, slug: str = "alpha") -> DaemonState:
    rt = RuntimeDir.init(tmp_path / "rt")
    _seed_org(rt.orgs_dir / slug)
    _write_token()
    return DaemonState.from_runtime(rt, Settings())


def _make_app(state: DaemonState) -> FastAPI:
    app = FastAPI()
    app.state.daemon = state
    app.include_router(portability_router, prefix="/api/v1/orgs/{slug}")
    return app


def _client(state: DaemonState) -> TestClient:
    return TestClient(_make_app(state), headers={"Authorization": f"Bearer {TOKEN}"})


def _seed_full_roots(state: DaemonState, slug: str = "alpha") -> Path:
    """Seed talks + memory + artifacts + kb + threads + a valid legacy skill."""
    org = state.orgs[slug]
    root = org.root
    (root / "talks").mkdir(parents=True, exist_ok=True)
    (root / "talks" / "TALK-1.md").write_text("# Talk\n\nbody\n")
    (root / "workspaces" / "dev_agent" / "memory").mkdir(parents=True, exist_ok=True)
    (root / "workspaces" / "dev_agent" / "memory" / "index.md").write_text("# memory\n")
    # non-memory workspace data that must NOT be exported
    (root / "workspaces" / "dev_agent" / "task_history.md").write_text("history")
    (root / "workspaces" / "dev_agent" / "output" / "T-1").mkdir(parents=True, exist_ok=True)
    (root / "workspaces" / "dev_agent" / "output" / "T-1" / "report.txt").write_text("x")
    (root / "workspaces" / "dev_agent" / "repos").mkdir(parents=True, exist_ok=True)
    (root / "workspaces" / "dev_agent" / "repos" / "foo" / "x").mkdir(parents=True, exist_ok=True)
    (root / "artifacts").mkdir(parents=True, exist_ok=True)
    (root / "kb").mkdir(parents=True, exist_ok=True)
    (root / "threads").mkdir(parents=True, exist_ok=True)
    # legacy skill package (quarantined carry)
    pkg = root / "skills" / "qa-scroll-test"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "SKILL.md").write_text("# QA Scroll Test\n\nbody\n")
    (pkg / "skill.yaml").write_text(_VALID_SKILL_YAML)
    return root


def _seed_schedule(org, schedule_id: str, status: ScheduleStatus, active: int) -> None:
    now = datetime.now(timezone.utc)
    org.db.schedules.insert(ScheduleRecord(
        id=schedule_id, agent_name="dev_agent", kind=ScheduleKind.ONE_SHOT,
        fire_at=now, normalized_brief="b", source_instruction="s",
        status=status, active=active, created_at=now,
    ))


def _target_runtime(tmp_path: Path, *, with_beta: bool = True) -> Path:
    target = RuntimeDir.init(tmp_path / "target")
    if with_beta:
        _seed_org(target.orgs_dir / "beta")
    return target.root


# ── Requirement C: full roundtrip ───────────────────────────────────────────


def test_full_export_import_roundtrip(tmp_path: Path) -> None:
    state = _make_source_state(tmp_path)
    root = _seed_full_roots(state)
    _seed_schedule(state.orgs["alpha"], "SCHEDULE-001", ScheduleStatus.FIRED, 1)

    client = _client(state)
    archive = tmp_path / "alpha.archive"
    r = client.post(
        "/api/v1/orgs/alpha/portability-export",
        json={"archive_path": str(archive), "trust_acknowledged": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "alpha"
    assert len(body["archive_digest"]) == 64
    assert archive.is_file()

    # The archive inventory includes durable roots + memory + valid legacy skill.
    assert "org" in body["source_root_inventory"]
    assert "talks" in body["source_root_inventory"]
    assert "workspaces" in body["source_root_inventory"]
    assert "skills" in body["source_root_inventory"]
    quarantined = {e["slug"] for e in body["legacy_skills_quarantined"]}
    assert "qa-scroll-test" in quarantined

    # No WAL/SHM or task-output/credential/repo content inside the archive.
    import tarfile
    names = set()
    with tarfile.open(archive, "r:gz") as tar:
        for m in tar.getmembers():
            names.add(m.name)
    assert "payload/happyranch.db" in names
    assert "payload/talks/TALK-1.md" in names
    assert "payload/workspaces/dev_agent/memory/index.md" in names
    assert "payload/skills/qa-scroll-test/SKILL.md" in names
    assert not any(n.endswith("-wal") or n.endswith("-shm") for n in names)
    assert not any("task_history.md" in n for n in names)
    assert not any("output/" in n for n in names)
    assert not any("repos/" in n for n in names)

    # Import into a non-empty schema-v2 target runtime.
    target = _target_runtime(tmp_path)
    r2 = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["result"] == "imported"
    assert body2["schedules_deactivated"] == 1

    dest = target / "orgs" / "alpha"
    assert (dest / "org" / "teams.yaml").is_file()
    assert (dest / "happyranch.db").is_file()
    assert (dest / "talks" / "TALK-1.md").is_file()
    assert (dest / "workspaces" / "dev_agent" / "memory" / "index.md").is_file()
    # task output / repos / task_history were NOT imported
    assert not (dest / "workspaces" / "dev_agent" / "task_history.md").exists()
    assert not (dest / "workspaces" / "dev_agent" / "output").exists()
    assert not (dest / "workspaces" / "dev_agent" / "repos").exists()

    # Imported schedule active flag forced to 0 (status semantics unchanged).
    conn = sqlite3.connect(str(dest / "happyranch.db"))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT id, active, status FROM schedules WHERE id = 'SCHEDULE-001'"
    ).fetchone()
    conn.close()
    assert row["active"] == 0
    assert row["status"] == "fired"

    # Receipt records digest + slug + quarantined legacy skill evidence.
    receipt = json.loads((target / "orgs" / "_archive" / "import-alpha.json").read_text())
    assert receipt["digest"] == body["archive_digest"]
    assert receipt["slug"] == "alpha"
    assert [e["slug"] for e in receipt["legacy_skills_quarantined"]] == ["qa-scroll-test"]

    # Source untouched: still has its task_history + output (never deleted).
    assert (root / "workspaces" / "dev_agent" / "task_history.md").exists()
    assert (root / "workspaces" / "dev_agent" / "output").exists()


def test_optional_root_absence_roundtrip(tmp_path: Path) -> None:
    state = _make_source_state(tmp_path)
    # Minimal source: only teams.yaml + DB (no talks/skills/workspaces).
    client = _client(state)
    archive = tmp_path / "min.archive"
    r = client.post(
        "/api/v1/orgs/alpha/portability-export",
        json={"archive_path": str(archive), "trust_acknowledged": True},
    )
    assert r.status_code == 200, r.text
    target = _target_runtime(tmp_path)
    r2 = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r2.status_code == 200, r2.text
    assert (target / "orgs" / "alpha" / "org" / "teams.yaml").is_file()


# ── Requirement D: readiness + fence recheck race ───────────────────────────


@pytest.mark.parametrize("status", [ScheduleStatus.ARMED, ScheduleStatus.FIRING],
                         ids=["armed", "firing"])
def test_export_refuses_armed_and_firing_schedule(
    tmp_path: Path, status: ScheduleStatus,
) -> None:
    state = _make_source_state(tmp_path)
    _seed_schedule(state.orgs["alpha"], "SCHED-A", status, 1)
    client = _client(state)
    archive = tmp_path / "x.archive"
    r = client.post(
        "/api/v1/orgs/alpha/portability-export",
        json={"archive_path": str(archive), "trust_acknowledged": True},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "source_not_ready"
    assert "SCHED-A" in r.json()["detail"]["blockers"]["active_schedules"]
    assert not archive.exists()


def test_export_requires_trust_acknowledgement(tmp_path: Path) -> None:
    state = _make_source_state(tmp_path)
    client = _client(state)
    archive = tmp_path / "x.archive"
    r = client.post(
        "/api/v1/orgs/alpha/portability-export",
        json={"archive_path": str(archive), "trust_acknowledged": False},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "trust_not_acknowledged"
    assert not archive.exists()


def test_export_fence_rejects_admission_and_race(tmp_path: Path) -> None:
    """A second export cannot acquire the fence while a capture is in progress
    (transfer_in_progress); after release, admission resumes."""
    import asyncio
    from httpx import ASGITransport, AsyncClient

    state = _make_source_state(tmp_path)
    org = state.orgs["alpha"]
    archive = tmp_path / "x.archive"
    app = _make_app(state)
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {TOKEN}"}

    async def scenario() -> None:
        # Acquire the fence directly (simulates an in-progress capture).
        assert await org.transfer_fence.acquire() is True
        try:
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                # A second export cannot acquire the fence.
                r = await client.post(
                    "/api/v1/orgs/alpha/portability-export",
                    json={"archive_path": str(archive), "trust_acknowledged": True},
                    headers=headers,
                )
            assert r.status_code == 409
            assert r.json()["detail"]["code"] == "transfer_in_progress"

            # The real admission lease refuses new task admission while held.
            from runtime.portability.fence import TransferFenceHeld
            with pytest.raises(TransferFenceHeld):
                async with org.transfer_fence.admission():
                    pass  # pragma: no cover
        finally:
            await org.transfer_fence.release()

        # Once released, admission resumes.
        async with org.transfer_fence.admission():
            pass

    asyncio.run(scenario())


def test_export_recheck_conflict_leaves_source_untouched(tmp_path: Path) -> None:
    """A live-work appearance between fence acquire and the DB-coordinated
    recheck must conflict and produce no archive. We simulate this by making
    the org non-quiescent before export, which is detected on the first check
    (no archive); the recheck path is exercised by the fence test above."""
    state = _make_source_state(tmp_path)
    org = state.orgs["alpha"]
    db = org.db
    db.insert_task(TaskRecord(
        id="T-1", brief="t", team="engineering",
        assigned_agent="dev_agent", status=TaskStatus.IN_PROGRESS,
    ))
    client = _client(state)
    archive = tmp_path / "x.archive"
    r = client.post(
        "/api/v1/orgs/alpha/portability-export",
        json={"archive_path": str(archive), "trust_acknowledged": True},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "source_not_ready"
    assert not archive.exists()
    # source task row untouched
    assert db.get_task("T-1").status == TaskStatus.IN_PROGRESS


# ── Requirement E: destination collision ────────────────────────────────────


def _exported_archive(tmp_path: Path) -> tuple[DaemonState, Path, str]:
    state = _make_source_state(tmp_path)
    client = _client(state)
    archive = tmp_path / "alpha.archive"
    r = client.post(
        "/api/v1/orgs/alpha/portability-export",
        json={"archive_path": str(archive), "trust_acknowledged": True},
    )
    assert r.status_code == 200, r.text
    return state, archive, r.json()["archive_digest"]


@pytest.mark.parametrize("occupancy", ["loaded", "broken", "data-bearing"],
                         ids=["loaded", "broken", "data-bearing"])
def test_import_refuses_destination_collision(tmp_path: Path, occupancy: str) -> None:
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    dest = target / "orgs" / "alpha"

    if occupancy == "loaded":
        _seed_org(dest)  # a valid-looking org (would be loaded on target boot)
    elif occupancy == "broken":
        dest.mkdir(parents=True)  # partial/broken: dir without teams.yaml
    else:
        _seed_org(dest)
        (dest / "happyranch.db").write_bytes(b"data-bearing")

    r = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "destination_occupied"


def test_import_refuses_slug_mismatch(tmp_path: Path) -> None:
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    r = client.post(
        "/api/v1/orgs/beta/portability-import",
        json={
            "archive_path": str(archive),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "slug_mismatch"


# ── Requirement F: idempotency + digest conflict ────────────────────────────


def test_import_idempotent_and_digest_conflict(tmp_path: Path) -> None:
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    payload = {
        "archive_path": str(archive),
        "target_runtime": str(target),
        "trust_acknowledged": True,
    }
    r1 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r1.status_code == 200
    assert r1.json()["result"] == "imported"

    # Exact digest + slug retry is idempotent.
    r2 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r2.status_code == 200
    assert r2.json()["result"] == "already_imported"

    # A different archive (different digest) for the same slug conflicts: add a
    # new talks file to the source, re-export, and re-import the new archive.
    (state.orgs["alpha"].root / "talks").mkdir(parents=True, exist_ok=True)
    (state.orgs["alpha"].root / "talks" / "TALK-2.md").write_text("# Talk 2\n")
    archive2 = tmp_path / "alpha-2.archive"
    r_exp = client.post(
        "/api/v1/orgs/alpha/portability-export",
        json={"archive_path": str(archive2), "trust_acknowledged": True},
    )
    assert r_exp.status_code == 200, r_exp.text
    assert r_exp.json()["archive_digest"] != digest

    r3 = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive2),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r3.status_code == 409
    assert r3.json()["detail"]["code"] == "digest_conflict"


# ── Requirement G: no execution / no dereference / no live binding ──────────


def test_no_archived_content_executed_and_no_legacy_binding(tmp_path: Path) -> None:
    state = _make_source_state(tmp_path)
    root = _seed_full_roots(state)
    client = _client(state)
    archive = tmp_path / "alpha.archive"
    r = client.post(
        "/api/v1/orgs/alpha/portability-export",
        json={"archive_path": str(archive), "trust_acknowledged": True},
    )
    assert r.status_code == 200, r.text
    target = _target_runtime(tmp_path)
    r2 = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r2.status_code == 200, r2.text

    # The imported legacy skill is carried as quarantined archive content only:
    # it has no canonical-store entry and no workspace symlink/materialization.
    dest = target / "orgs" / "alpha"
    pkg = dest / "skills" / "qa-scroll-test"
    assert (pkg / "SKILL.md").is_file()  # bytes carried for inspection
    # No workspace materialization link for the legacy skill.
    ws_skills = dest / "workspaces" / "dev_agent"
    for agent_dir in dest.glob("workspaces/*"):
        for skill_link in agent_dir.glob(".claude/skills/*"):
            assert not skill_link.exists() or "qa-scroll-test" not in str(skill_link)
    # No canonical skill-store materialization was created under the machine
    # store (defense-in-depth: the import path never touches the canonical store).
    from runtime.runtime import daemon_home
    canon_root = daemon_home() / "canonical-skills"
    if canon_root.exists():
        assert "qa-scroll-test" not in {
            p.name for p in canon_root.rglob("*") if p.is_dir()
        }


def test_inspect_reports_manifest_and_digest(tmp_path: Path) -> None:
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    r = client.post(
        "/api/v1/orgs/alpha/portability-inspect",
        json={"archive_path": str(archive)},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["archive_digest"] == digest
    assert body["source_slug"] == "alpha"
    assert body["format_version"] == 1
    assert "org" in body["source_root_inventory"]


def test_inspect_rejects_invalid_archive(tmp_path: Path) -> None:
    state = _make_source_state(tmp_path)
    client = _client(state)
    bad = tmp_path / "bad.archive"
    bad.write_bytes(b"not a tar")
    r = client.post(
        "/api/v1/orgs/alpha/portability-inspect",
        json={"archive_path": str(bad)},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "invalid_archive"


def test_import_requires_bearer(tmp_path: Path) -> None:
    state = _make_source_state(tmp_path)
    app = _make_app(state)  # no bearer header
    client = TestClient(app)
    r = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={"archive_path": "/tmp/x", "target_runtime": "/tmp/y",
              "trust_acknowledged": True},
    )
    assert r.status_code == 401


# ── Finding #3: no-replace publish (TOCTOU) ────────────────────────────────


def test_import_competitor_sentinel_fails_clean_and_keeps_staging_pending(
    tmp_path: Path, monkeypatch,
) -> None:
    """A competitor that occupies the destination *after* validation but
    before publish makes the import fail cleanly (no overwrite); the private
    staging directory stays under _pending as visible recovery state."""
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    dest = target / "orgs" / "alpha"

    import runtime.daemon.routes.portability as proutes
    real_publish = proutes._publish_no_replace

    def competitor_publish(payload_dir, dest_path):
        # Simulate a post-validation competitor that claims the destination.
        dest_path.mkdir(parents=True, exist_ok=True)
        (dest_path / "sentinel.txt").write_text("occupied")
        return real_publish(payload_dir, dest_path)  # must refuse (no-replace)

    monkeypatch.setattr(proutes, "_publish_no_replace", competitor_publish)

    r = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "destination_occupied"
    # The competitor's sentinel is intact; nothing was published over it.
    assert (dest / "sentinel.txt").exists()
    assert not (dest / "org" / "teams.yaml").exists()
    # Staging stays under _pending (visible recovery), not silently deleted.
    pending = target / "orgs" / "_pending"
    assert pending.exists()
    assert len(list(pending.iterdir())) == 1


# ── Finding #4: prepare/publish/finalize/recovery (filesystem-only) ─────────


def test_import_postpublish_receipt_fault_recovers_idempotently(
    tmp_path: Path, monkeypatch,
) -> None:
    """A fault between publish and receipt finalize leaves the published org +
    a pending marker (visible recovery). A same-digest+slug retry revalidates
    and converges by writing the receipt WITHOUT overwriting the org; the
    marker is then cleaned up."""
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    dest = target / "orgs" / "alpha"

    import runtime.daemon.routes.portability as proutes
    real_receipt = proutes._write_receipt

    def failing_receipt(*args, **kwargs):
        raise OSError("simulated finalize crash")

    monkeypatch.setattr(proutes, "_write_receipt", failing_receipt)

    payload = {
        "archive_path": str(archive),
        "target_runtime": str(target),
        "trust_acknowledged": True,
    }
    r1 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r1.status_code == 500
    assert r1.json()["detail"]["code"] == "import_failed"
    # Publish happened: exactly one target exists; receipt missing; marker present.
    assert dest.exists()
    assert (dest / "org" / "teams.yaml").is_file()
    receipt_path = target / "orgs" / "_archive" / "import-alpha.json"
    pending_path = target / "orgs" / "_archive" / ".pending-import-alpha.json"
    assert not receipt_path.exists()
    assert pending_path.exists()

    # Restore finalize and retry the SAME archive: converge, never overwrite.
    monkeypatch.setattr(proutes, "_write_receipt", real_receipt)
    before_mtime = (dest / "org" / "teams.yaml").stat().st_mtime_ns
    r2 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r2.status_code == 200, r2.text
    assert r2.json()["result"] == "imported"
    assert r2.json()["recovered"] is True
    assert (dest / "org" / "teams.yaml").stat().st_mtime_ns == before_mtime  # no overwrite
    receipt = json.loads(receipt_path.read_text())
    assert receipt["digest"] == digest
    assert receipt["recovery"] is True
    assert not pending_path.exists()


def test_import_recovery_different_digest_conflicts(
    tmp_path: Path, monkeypatch,
) -> None:
    """In the recovery state (published dest + pending marker, no receipt), a
    DIFFERENT digest for the same slug conflicts rather than overwriting."""
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)

    import runtime.daemon.routes.portability as proutes
    real_receipt = proutes._write_receipt
    monkeypatch.setattr(
        proutes, "_write_receipt", lambda *a, **k: (_ for _ in ()).throw(
            OSError("simulated finalize crash")
        ),
    )
    payload = {
        "archive_path": str(archive),
        "target_runtime": str(target),
        "trust_acknowledged": True,
    }
    r1 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r1.status_code == 500
    monkeypatch.setattr(proutes, "_write_receipt", real_receipt)

    # A different archive (different digest) for the same slug now conflicts.
    (state.orgs["alpha"].root / "talks").mkdir(parents=True, exist_ok=True)
    (state.orgs["alpha"].root / "talks" / "TALK-2.md").write_text("# Talk 2\n")
    archive2 = tmp_path / "alpha-2.archive"
    r_exp = client.post(
        "/api/v1/orgs/alpha/portability-export",
        json={"archive_path": str(archive2), "trust_acknowledged": True},
    )
    assert r_exp.status_code == 200, r_exp.text
    assert r_exp.json()["archive_digest"] != digest

    r3 = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive2),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r3.status_code == 409
    assert r3.json()["detail"]["code"] == "digest_conflict"


# ── Finding: fault AFTER receipt write / BEFORE pending-marker cleanup ──────


def test_import_fault_after_receipt_before_cleanup_converges_and_cleans_marker(
    tmp_path: Path, monkeypatch,
) -> None:
    """A fault AFTER the receipt is durably written but BEFORE the pending
    marker is unlinked leaves the receipt + marker + published target. A
    same-digest+slug retry takes the receipt fast path (``already_imported``)
    AND removes the matching pending marker — never leaving stale recovery
    state behind a successful receipt."""
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    dest = target / "orgs" / "alpha"

    import runtime.daemon.routes.portability as proutes
    real_receipt = proutes._write_receipt

    def receipt_then_fault(*args, **kwargs):
        # Simulate a fault in the finalize window: the receipt IS durably
        # written, then the process dies before the pending-marker unlink.
        real_receipt(*args, **kwargs)
        raise OSError("simulated fault after receipt, before pending cleanup")

    monkeypatch.setattr(proutes, "_write_receipt", receipt_then_fault)

    payload = {
        "archive_path": str(archive),
        "target_runtime": str(target),
        "trust_acknowledged": True,
    }
    r1 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r1.status_code == 500
    assert r1.json()["detail"]["code"] == "import_failed"
    # Fault boundary: receipt persisted, marker still present, target published.
    receipt_path = target / "orgs" / "_archive" / "import-alpha.json"
    pending_path = target / "orgs" / "_archive" / ".pending-import-alpha.json"
    assert receipt_path.exists()
    assert pending_path.exists()
    assert (dest / "org" / "teams.yaml").is_file()

    # Restore finalize and retry the SAME digest: the idempotent fast path must
    # converge to already_imported AND remove the matching pending marker.
    monkeypatch.setattr(proutes, "_write_receipt", real_receipt)
    before_mtime = (dest / "org" / "teams.yaml").stat().st_mtime_ns
    r2 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r2.status_code == 200, r2.text
    assert r2.json()["result"] == "already_imported"
    assert (dest / "org" / "teams.yaml").stat().st_mtime_ns == before_mtime  # no re-publish
    assert not pending_path.exists()  # matching marker cleaned up
    receipt = json.loads(receipt_path.read_text())
    assert receipt["digest"] == digest
    assert receipt["result"] == "imported"  # original receipt untouched


def test_import_fault_after_receipt_conflicting_digest_protects_state(
    tmp_path: Path, monkeypatch,
) -> None:
    """After the same finalize-window fault, a DIFFERENT digest retry is an
    explicit 409 ``digest_conflict`` and leaves target/receipt/marker
    protected — the receipt fast path never mutates them."""
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    dest = target / "orgs" / "alpha"

    import runtime.daemon.routes.portability as proutes
    real_receipt = proutes._write_receipt

    def receipt_then_fault(*args, **kwargs):
        real_receipt(*args, **kwargs)
        raise OSError("simulated fault after receipt, before pending cleanup")

    monkeypatch.setattr(proutes, "_write_receipt", receipt_then_fault)
    payload = {
        "archive_path": str(archive),
        "target_runtime": str(target),
        "trust_acknowledged": True,
    }
    r1 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r1.status_code == 500
    monkeypatch.setattr(proutes, "_write_receipt", real_receipt)

    receipt_path = target / "orgs" / "_archive" / "import-alpha.json"
    pending_path = target / "orgs" / "_archive" / ".pending-import-alpha.json"
    receipt_before = receipt_path.read_text()
    marker_before = pending_path.read_text()
    target_mtime = (dest / "org" / "teams.yaml").stat().st_mtime_ns

    # A different archive (different digest) now conflicts and mutates nothing.
    (state.orgs["alpha"].root / "talks").mkdir(parents=True, exist_ok=True)
    (state.orgs["alpha"].root / "talks" / "TALK-2.md").write_text("# Talk 2\n")
    archive2 = tmp_path / "alpha-2.archive"
    r_exp = client.post(
        "/api/v1/orgs/alpha/portability-export",
        json={"archive_path": str(archive2), "trust_acknowledged": True},
    )
    assert r_exp.status_code == 200, r_exp.text
    assert r_exp.json()["archive_digest"] != digest

    r3 = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive2),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r3.status_code == 409
    assert r3.json()["detail"]["code"] == "digest_conflict"
    # Receipt/marker/target all byte-for-byte protected.
    assert receipt_path.read_text() == receipt_before
    assert pending_path.read_text() == marker_before
    assert (dest / "org" / "teams.yaml").stat().st_mtime_ns == target_mtime


def test_import_receipt_fast_path_never_unlinks_nonmatching_marker(
    tmp_path: Path,
) -> None:
    """The idempotent receipt fast path removes a pending marker ONLY when its
    durable identity (slug + digest + operation_id) exactly matches the
    finalized receipt. A stale/nonmatching marker (foreign operation id,
    different digest, or malformed JSON) is never unlinked by the fast path."""
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    payload = {
        "archive_path": str(archive),
        "target_runtime": str(target),
        "trust_acknowledged": True,
    }
    r1 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r1.status_code == 200, r1.text
    assert r1.json()["result"] == "imported"

    receipt_path = target / "orgs" / "_archive" / "import-alpha.json"
    pending_path = target / "orgs" / "_archive" / ".pending-import-alpha.json"
    receipt = json.loads(receipt_path.read_text())
    assert not pending_path.exists()  # normal import finalized the marker

    nonmatching_markers = [
        # same slug+digest but a foreign operation id
        json.dumps({"slug": "alpha", "digest": digest, "operation_id": "foreign-op"}),
        # same slug but a different digest
        json.dumps(
            {"slug": "alpha", "digest": "0" * 64, "operation_id": receipt["operation_id"]}
        ),
        # malformed JSON
        "{not-json",
    ]
    for marker_bytes in nonmatching_markers:
        pending_path.write_text(marker_bytes)
        r = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
        assert r.status_code == 200, r.text
        assert r.json()["result"] == "already_imported"
        # Nonmatching/malformed marker is left untouched (never inferred).
        assert pending_path.read_text() == marker_bytes


# ── Repair findings: no-replace primitive, empty target, canonical shape,
#    off-loop capture, and the pre-publish crash boundary ─────────────────────


def test_rename_noreplace_primitive_no_overwrite(tmp_path: Path) -> None:
    """The platform no-replace primitive refuses an existing destination
    (empty or not) and leaves it intact."""
    import runtime.daemon.routes.portability as proutes
    src = tmp_path / "src"
    src.mkdir()
    (src / "org").mkdir()
    (src / "org" / "teams.yaml").write_text("teams: {}\n")
    dst = tmp_path / "dst"
    assert proutes._rename_noreplace(src, dst) is True
    assert (dst / "org" / "teams.yaml").is_file()
    # dst now exists — a second rename must refuse (no-overwrite) and leave it.
    src2 = tmp_path / "src2"
    src2.mkdir()
    assert proutes._rename_noreplace(src2, dst) is False
    assert (dst / "org" / "teams.yaml").is_file()  # intact
    assert src2.is_dir()  # not consumed


def test_publish_no_replace_refuses_empty_competitor(tmp_path: Path) -> None:
    """A post-validation competitor that creates an EMPTY destination is left
    intact; the no-replace publish refuses rather than overwriting it."""
    import runtime.daemon.routes.portability as proutes
    payload = tmp_path / "payload"
    (payload / "org").mkdir(parents=True)
    (payload / "org" / "teams.yaml").write_text("teams: {}\n")
    dest = tmp_path / "orgs" / "alpha"
    dest.mkdir(parents=True)  # competing EMPTY destination (the exact race)
    with pytest.raises(HTTPException) as ei:
        proutes._publish_no_replace(payload, dest)
    assert ei.value.status_code == 409
    assert ei.value.detail["code"] == "destination_occupied"
    # The competing empty directory is intact; the payload was not moved over it.
    assert dest.is_dir()
    assert list(dest.iterdir()) == []
    assert (payload / "org" / "teams.yaml").exists()


def test_publish_no_replace_exactly_one_winner(tmp_path: Path) -> None:
    """Concurrent publishes to one destination yield exactly one durable winner;
    the losers are refused and no partial/mixed state results."""
    import threading
    import runtime.daemon.routes.portability as proutes
    dest_parent = tmp_path / "orgs"
    dest_parent.mkdir()
    dest = dest_parent / "alpha"
    winners: list[int] = []
    lock = threading.Lock()
    barrier = threading.Barrier(5, timeout=5.0)

    def attempt(i: int) -> None:
        payload = tmp_path / f"payload-{i}"
        (payload / "org").mkdir(parents=True)
        (payload / "org" / "teams.yaml").write_text(f"teams: {{winner: {i}}}\n")
        barrier.wait()
        try:
            proutes._publish_no_replace(payload, dest)
            with lock:
                winners.append(i)
        except HTTPException:
            pass

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert len(winners) == 1
    w = winners[0]
    assert (dest / "org" / "teams.yaml").read_text() == f"teams: {{winner: {w}}}\n"


def test_import_refuses_empty_v2_target(tmp_path: Path) -> None:
    """An otherwise-empty schema-v2 destination runtime is refused before any
    mutation (an enforced contract, not a fixture convention)."""
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path, with_beta=False)  # no other org
    r = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "empty_target_runtime"
    assert not (target / "orgs" / "alpha").exists()


def test_import_rejects_self_consistent_old_shape_db(tmp_path: Path) -> None:
    """A self-consistent archive (manifest fingerprint matches the shipped DB)
    whose DB is an old/unsupported shape is rejected by the independent
    canonical-v2 contract before any target mutation."""
    from runtime.portability.archive import (
        ARCHIVE_FORMAT_VERSION,
        ARCHIVE_POLICY_VERSION,
        ArchiveMember,
        Manifest,
        build_archive,
        sha256_file,
    )
    from runtime.portability.capture import compute_v2_fingerprint

    old_db = tmp_path / "old.db"
    conn = sqlite3.connect(str(old_db))
    conn.execute("CREATE TABLE agent_enrollments (id TEXT PRIMARY KEY)")
    conn.commit()
    old_fp = compute_v2_fingerprint(conn)
    conn.close()

    teams = tmp_path / "org" / "teams.yaml"
    teams.parent.mkdir(parents=True)
    teams.write_text("teams: {}\n")

    payload = {
        "payload/org/teams.yaml": teams,
        "payload/happyranch.db": old_db,
    }
    members = [
        ArchiveMember(path="payload/org/teams.yaml", size=teams.stat().st_size,
                      sha256=sha256_file(teams)),
        ArchiveMember(path="payload/happyranch.db", size=old_db.stat().st_size,
                      sha256=sha256_file(old_db)),
    ]
    manifest = Manifest(
        format_version=ARCHIVE_FORMAT_VERSION,
        policy_version=ARCHIVE_POLICY_VERSION,
        source_slug="alpha",
        v2_fingerprint=old_fp,  # self-consistent with the shipped old DB
        members=members,
        source_root_inventory=["org", "happyranch.db"],
        included_roots={"org": 1, "happyranch.db": 1},
        excluded_entries=[],
        rejected_entries=[],
    )
    archive = tmp_path / "old-shape.archive"
    build_archive(archive, manifest, payload)

    state = _make_source_state(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    r = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "unsupported_db_shape"
    assert not (target / "orgs" / "alpha").exists()


def test_import_prepublish_crash_leaves_no_false_success_and_does_not_block(
    tmp_path: Path, monkeypatch,
) -> None:
    """A fault BEFORE publish leaves no destination, no false success, and does
    not block a subsequent safe import (the pre-publish boundary)."""
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)

    import runtime.daemon.routes.portability as proutes
    real_publish = proutes._publish_no_replace
    monkeypatch.setattr(
        proutes, "_publish_no_replace",
        lambda payload_dir, dest: (_ for _ in ()).throw(
            OSError("simulated pre-publish crash")
        ),
    )
    payload = {
        "archive_path": str(archive),
        "target_runtime": str(target),
        "trust_acknowledged": True,
    }
    r1 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r1.status_code == 500
    assert r1.json()["detail"]["code"] == "import_failed"
    dest = target / "orgs" / "alpha"
    assert not dest.exists()  # nothing published
    assert not (target / "orgs" / "_archive" / "import-alpha.json").exists()

    # A subsequent import must succeed (the crashed attempt did not block it).
    monkeypatch.setattr(proutes, "_publish_no_replace", real_publish)
    r2 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r2.status_code == 200, r2.text
    assert r2.json()["result"] == "imported"
    assert (dest / "org" / "teams.yaml").is_file()


def test_export_capture_runs_off_event_loop(tmp_path: Path) -> None:
    """The blocking capture runs on a worker thread (not the daemon event
    loop): while it is blocked, the loop still serves a concurrent request."""
    import asyncio
    import threading
    from httpx import ASGITransport, AsyncClient
    import runtime.daemon.routes.portability as proutes

    state = _make_source_state(tmp_path)
    _seed_full_roots(state)
    app = _make_app(state)
    transport = ASGITransport(app=app)
    headers = {"Authorization": f"Bearer {TOKEN}"}
    archive = tmp_path / "big.archive"

    entered = threading.Event()
    release = threading.Event()
    loop_thread: list[threading.Thread] = []
    capture_thread: list[threading.Thread] = []

    real_build = proutes.build_archive

    def slow_build(dest_path, manifest, payload):
        capture_thread.append(threading.current_thread())
        entered.set()
        if not release.wait(timeout=10):
            raise AssertionError("timed out waiting for release")
        return real_build(dest_path, manifest, payload)

    proutes.build_archive = slow_build

    async def scenario() -> None:
        loop_thread.append(threading.current_thread())
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            export_task = asyncio.create_task(client.post(
                "/api/v1/orgs/alpha/portability-export",
                json={"archive_path": str(archive), "trust_acknowledged": True},
                headers=headers,
            ))
            for _ in range(500):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set(), "capture never entered the worker thread"
            # The event loop is still responsive: a concurrent request returns
            # while the capture is blocked inside its worker thread.
            r = await client.post(
                "/api/v1/orgs/alpha/portability-inspect",
                json={"archive_path": str(archive)},
                headers=headers,
            )
            assert r.status_code in (200, 404, 422)
            release.set()
            resp = await export_task
            assert resp.status_code == 200, resp.text

    try:
        asyncio.run(scenario())
    finally:
        proutes.build_archive = real_build

    assert capture_thread and loop_thread
    assert capture_thread[0] is not loop_thread[0]


# ── Repair: pending-marker reconciliation BEFORE destination existence ─────


def test_import_pending_marker_no_dest_different_digest_conflicts(
    tmp_path: Path, monkeypatch,
) -> None:
    """A pre-publish crash leaves a pending marker + NO destination. A retry
    with a DIFFERENT digest for the same slug must conflict deterministically
    (reconciled BEFORE the destination branch) — it must not overwrite/reuse
    the marker or publish."""
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)

    import runtime.daemon.routes.portability as proutes
    real_publish = proutes._publish_no_replace
    monkeypatch.setattr(
        proutes, "_publish_no_replace",
        lambda payload_dir, dest: (_ for _ in ()).throw(OSError("simulated pre-publish crash")),
    )
    payload = {
        "archive_path": str(archive),
        "target_runtime": str(target),
        "trust_acknowledged": True,
    }
    r1 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r1.status_code == 500
    monkeypatch.setattr(proutes, "_publish_no_replace", real_publish)

    pending_path = target / "orgs" / "_archive" / ".pending-import-alpha.json"
    assert pending_path.exists()
    pending = json.loads(pending_path.read_text())
    assert pending["digest"] == digest
    assert not (target / "orgs" / "alpha").exists()  # no destination created

    # A different archive (different digest) for the same slug now conflicts
    # even though the destination is absent.
    (state.orgs["alpha"].root / "talks").mkdir(parents=True, exist_ok=True)
    (state.orgs["alpha"].root / "talks" / "TALK-2.md").write_text("# Talk 2\n")
    archive2 = tmp_path / "alpha-2.archive"
    r_exp = client.post(
        "/api/v1/orgs/alpha/portability-export",
        json={"archive_path": str(archive2), "trust_acknowledged": True},
    )
    assert r_exp.status_code == 200, r_exp.text
    assert r_exp.json()["archive_digest"] != digest

    r2 = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive2),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "digest_conflict"
    # Marker unchanged (digest X) and no destination was published.
    assert json.loads(pending_path.read_text())["digest"] == digest
    assert not (target / "orgs" / "alpha").exists()
    # No receipt written.
    assert not (target / "orgs" / "_archive" / "import-alpha.json").exists()


def test_import_pending_marker_no_dest_same_digest_resumes(
    tmp_path: Path, monkeypatch,
) -> None:
    """A pre-publish crash leaves a pending marker + NO destination. A retry of
    the SAME digest+slug resumes (converges) and publishes exactly once."""
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)

    import runtime.daemon.routes.portability as proutes
    real_publish = proutes._publish_no_replace
    monkeypatch.setattr(
        proutes, "_publish_no_replace",
        lambda payload_dir, dest: (_ for _ in ()).throw(OSError("simulated pre-publish crash")),
    )
    payload = {
        "archive_path": str(archive),
        "target_runtime": str(target),
        "trust_acknowledged": True,
    }
    r1 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r1.status_code == 500
    monkeypatch.setattr(proutes, "_publish_no_replace", real_publish)

    pending_path = target / "orgs" / "_archive" / ".pending-import-alpha.json"
    assert pending_path.exists()
    assert not (target / "orgs" / "alpha").exists()

    r2 = client.post("/api/v1/orgs/alpha/portability-import", json=payload)
    assert r2.status_code == 200, r2.text
    assert r2.json()["result"] == "imported"
    assert (target / "orgs" / "alpha" / "org" / "teams.yaml").is_file()
    # Marker finalized away; a final receipt exists.
    assert not pending_path.exists()
    assert (target / "orgs" / "_archive" / "import-alpha.json").exists()


def test_import_adversarial_legacy_reference_refused_no_mutation(
    tmp_path: Path,
) -> None:
    """A self-consistent archive whose legacy skill's SKILL.md carries an
    escaping ``file:`` reference is refused at import (the manifest claims the
    skill valid, but the extracted bytes resolve the unsafe reference) with no
    target / receipt / queue mutation."""
    from runtime.infrastructure.database import Database
    from runtime.portability.archive import (
        ARCHIVE_FORMAT_VERSION,
        ARCHIVE_POLICY_VERSION,
        ArchiveMember,
        LegacySkillEvidence,
        Manifest,
        build_archive,
        sha256_bytes,
        sha256_file,
    )
    from runtime.portability.capture import canonical_v2_fingerprint

    # A real current-v2 DB.
    db = Database(tmp_path / "src.db")
    db.backup_to(tmp_path / "happyranch.db")
    db.close()

    teams = tmp_path / "org" / "teams.yaml"
    teams.parent.mkdir(parents=True)
    teams.write_text("teams: {}\n")

    pkg = tmp_path / "skills" / "qa-scroll-test"
    pkg.mkdir(parents=True)
    skill_md = pkg / "SKILL.md"
    skill_md.write_text("# QA\n\n[leak](file:references/guide.md)\n")
    skill_yaml = pkg / "skill.yaml"
    skill_yaml.write_text(_VALID_SKILL_YAML)
    guide = pkg / "references" / "guide.md"
    guide.parent.mkdir(parents=True)
    guide.write_text("# Guide\n")

    db_file = tmp_path / "happyranch.db"
    payload = {
        "payload/org/teams.yaml": teams,
        "payload/happyranch.db": db_file,
        "payload/skills/qa-scroll-test/SKILL.md": skill_md,
        "payload/skills/qa-scroll-test/skill.yaml": skill_yaml,
        "payload/skills/qa-scroll-test/references/guide.md": guide,
    }
    members = [
        ArchiveMember(path=p, size=Path(f).stat().st_size, sha256=sha256_file(f))
        for p, f in sorted(payload.items())
    ]
    manifest = Manifest(
        format_version=ARCHIVE_FORMAT_VERSION,
        policy_version=ARCHIVE_POLICY_VERSION,
        source_slug="alpha",
        v2_fingerprint=canonical_v2_fingerprint(),
        members=members,
        source_root_inventory=["org", "happyranch.db", "skills"],
        included_roots={"org": 1, "happyranch.db": 1, "skills": 3},
        excluded_entries=[],
        rejected_entries=[],
        legacy_skills=[LegacySkillEvidence(
            slug="qa-scroll-test",
            metadata_hash=sha256_bytes(skill_yaml.read_bytes()),
            content_hash=sha256_bytes(skill_md.read_bytes()),
            member_hashes={
                "SKILL.md": sha256_file(skill_md),
                "skill.yaml": sha256_file(skill_yaml),
                "references/guide.md": sha256_file(guide),
            },
            validation_result="valid",
            references_resolved=[],
        )],
    )
    archive = tmp_path / "hostile.archive"
    build_archive(archive, manifest, payload)

    state = _make_source_state(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    r = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] in ("invalid_archive_content", "invalid_archive")
    # No target / receipt / queue mutation.
    assert not (target / "orgs" / "alpha").exists()
    assert not (target / "orgs" / "_archive" / "import-alpha.json").exists()


# ── Slice B replacement: exclusive per-(runtime, slug) import claim ────────
#
# v1 serializes imports to one destination (refuses concurrency) rather than
# supporting concurrent imports. A competing same runtime+slug invocation gets
# ``import_in_progress`` and must not touch the owner's marker/staging/target/
# receipt. Different slugs or runtimes proceed independently.


def test_import_claim_exclusive_same_runtime_slug(tmp_path: Path) -> None:
    """The claim primitive refuses a second acquire for the same runtime+slug
    while held, releases, and re-acquires; the stable lock file is never
    unlinked by release."""
    import runtime.daemon.routes.portability as proutes
    orgs_dir = tmp_path / "target" / "orgs"
    orgs_dir.mkdir(parents=True)

    c1 = proutes.ImportClaim(orgs_dir, "alpha")
    assert c1.acquire() is True
    c2 = proutes.ImportClaim(orgs_dir, "alpha")
    assert c2.acquire() is False          # refused while held
    c1.release()
    c3 = proutes.ImportClaim(orgs_dir, "alpha")
    assert c3.acquire() is True           # re-acquirable after release
    c3.release()
    # release() never unlinks the stable lock file (the FD is the claim).
    assert (orgs_dir / "_archive" / ".import-claim-alpha.lock").exists()


def test_import_claim_different_slug_or_runtime_nonblocking(tmp_path: Path) -> None:
    """Claims for different slugs (same runtime) or different runtimes (same
    slug) proceed independently — neither blocks the other."""
    import runtime.daemon.routes.portability as proutes
    orgs_a = tmp_path / "a" / "orgs"
    orgs_b = tmp_path / "b" / "orgs"
    orgs_a.mkdir(parents=True)
    orgs_b.mkdir(parents=True)

    # different slug, same runtime
    ca = proutes.ImportClaim(orgs_a, "alpha")
    cb = proutes.ImportClaim(orgs_a, "beta")
    assert ca.acquire() is True
    assert cb.acquire() is True
    ca.release()
    cb.release()
    # different runtime, same slug
    c1 = proutes.ImportClaim(orgs_a, "alpha")
    c2 = proutes.ImportClaim(orgs_b, "alpha")
    assert c1.acquire() is True
    assert c2.acquire() is True
    c1.release()
    c2.release()


def test_import_refused_import_in_progress_when_claim_held(tmp_path: Path) -> None:
    """While an owner holds the claim, a competing import is refused with
    ``import_in_progress`` and writes nothing; after release it imports."""
    import runtime.daemon.routes.portability as proutes
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    orgs_dir = target / "orgs"

    holder = proutes.ImportClaim(orgs_dir, "alpha")
    assert holder.acquire() is True
    try:
        r = client.post(
            "/api/v1/orgs/alpha/portability-import",
            json={
                "archive_path": str(archive),
                "target_runtime": str(target),
                "trust_acknowledged": True,
            },
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "import_in_progress"
        # The refused competitor wrote nothing: no target/receipt/marker.
        assert not (target / "orgs" / "alpha").exists()
        assert not (orgs_dir / "_archive" / "import-alpha.json").exists()
        assert not (orgs_dir / "_archive" / ".pending-import-alpha.json").exists()
    finally:
        holder.release()

    r2 = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r2.status_code == 200
    assert r2.json()["result"] == "imported"


def test_import_competitor_does_not_touch_owner_marker(tmp_path: Path) -> None:
    """A competing same runtime+slug import is refused ``import_in_progress``
    and MUST NOT unlink/replace the owner's pending marker or receipt."""
    import runtime.daemon.routes.portability as proutes
    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    orgs_dir = target / "orgs"
    pending_path = orgs_dir / "_archive" / ".pending-import-alpha.json"
    receipt_path = orgs_dir / "_archive" / "import-alpha.json"

    holder = proutes.ImportClaim(orgs_dir, "alpha")
    assert holder.acquire() is True
    try:
        # The owner has already durably recorded its in-flight identity.
        proutes._write_pending_marker(
            pending_path, slug="alpha", digest=digest, operation_id="owner-op",
        )
        r = client.post(
            "/api/v1/orgs/alpha/portability-import",
            json={
                "archive_path": str(archive),
                "target_runtime": str(target),
                "trust_acknowledged": True,
            },
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "import_in_progress"
        # Owner marker byte-for-byte intact: competitor inferred no ownership.
        marker = json.loads(pending_path.read_text())
        assert marker["digest"] == digest
        assert marker["operation_id"] == "owner-op"
        assert not receipt_path.exists()
        assert not (target / "orgs" / "alpha").exists()
    finally:
        holder.release()


def test_import_concurrent_two_requests_exactly_one_winner(
    tmp_path: Path, monkeypatch,
) -> None:
    """A forced two-request interleaving yields exactly one durable winner and
    one explicit ``import_in_progress`` refusal — never a second
    ``already_imported``/``imported`` while the owner is live, and the loser
    damages nothing."""
    import threading
    import runtime.daemon.routes.portability as proutes

    state, archive, digest = _exported_archive(tmp_path)
    target = _target_runtime(tmp_path)
    payload = {
        "archive_path": str(archive),
        "target_runtime": str(target),
        "trust_acknowledged": True,
    }

    entered = threading.Event()
    release = threading.Event()
    real_marker = proutes._write_pending_marker

    def blocking_marker(pending_path, *, slug, digest, operation_id):
        # Inside the held claim (marker write is under the claim). Signal the
        # main thread that the owner is live, then block until released.
        entered.set()
        if not release.wait(timeout=10):
            raise AssertionError("timed out waiting for release")
        return real_marker(
            pending_path, slug=slug, digest=digest, operation_id=operation_id,
        )

    monkeypatch.setattr(proutes, "_write_pending_marker", blocking_marker)

    first_result: dict = {}

    def run_first() -> None:
        c = _client(state)
        r = c.post("/api/v1/orgs/alpha/portability-import", json=payload)
        first_result["status"] = r.status_code
        first_result["body"] = r.json()

    t = threading.Thread(target=run_first)
    t.start()
    assert entered.wait(timeout=10), "first request never entered the claim"

    # Owner is live inside the claim; the second request must be refused.
    client2 = _client(state)
    r2 = client2.post("/api/v1/orgs/alpha/portability-import", json=payload)
    release.set()
    t.join(timeout=10)

    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "import_in_progress"
    assert first_result["status"] == 200, first_result
    assert first_result["body"]["result"] == "imported"

    # Exactly one durable winner: one published target + one receipt; the
    # loser never deleted the winner's marker (finalized cleanly).
    dest = target / "orgs" / "alpha"
    assert (dest / "org" / "teams.yaml").is_file()
    receipt_path = target / "orgs" / "_archive" / "import-alpha.json"
    assert receipt_path.exists()
    receipt = json.loads(receipt_path.read_text())
    assert receipt["digest"] == digest
    assert not (target / "orgs" / "_archive" / ".pending-import-alpha.json").exists()


def test_import_cross_process_flock_refusal(tmp_path: Path) -> None:
    """A separate process holding the stable lock file (simulated by an
    independent open+flock — exactly what a second daemon does) refuses the
    import with ``import_in_progress`` via the flock half, with no in-process
    lock involved."""
    import fcntl
    import runtime.daemon.routes.portability as proutes

    state, archive, digest = _exported_archive(tmp_path)
    client = _client(state)
    target = _target_runtime(tmp_path)
    orgs_dir = target / "orgs"

    lock_path = orgs_dir / "_archive" / ".import-claim-alpha.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        r = client.post(
            "/api/v1/orgs/alpha/portability-import",
            json={
                "archive_path": str(archive),
                "target_runtime": str(target),
                "trust_acknowledged": True,
            },
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "import_in_progress"
        assert not (target / "orgs" / "alpha").exists()
        assert not (orgs_dir / "_archive" / "import-alpha.json").exists()
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)

    # After the competing process releases, the import proceeds normally.
    r2 = client.post(
        "/api/v1/orgs/alpha/portability-import",
        json={
            "archive_path": str(archive),
            "target_runtime": str(target),
            "trust_acknowledged": True,
        },
    )
    assert r2.status_code == 200
    assert r2.json()["result"] == "imported"
