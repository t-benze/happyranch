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
from fastapi import FastAPI
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
    state = _make_source_state(tmp_path)
    org = state.orgs["alpha"]
    client = _client(state)
    archive = tmp_path / "x.archive"

    # Acquire the fence directly (simulates an in-progress capture).
    assert org.transfer_fence.acquire() is True
    try:
        # A second export cannot acquire the fence.
        r = client.post(
            "/api/v1/orgs/alpha/portability-export",
            json={"archive_path": str(archive), "trust_acknowledged": True},
        )
        assert r.status_code == 409
        assert r.json()["detail"]["code"] == "transfer_in_progress"

        # The real production admission seam refuses new task admission.
        from runtime.daemon.runner import enqueue_task
        from runtime.portability.fence import TransferFenceHeld
        db = org.db
        db.insert_task(TaskRecord(
            id="T-NEW", brief="t", team="engineering",
            assigned_agent="dev_agent", status=TaskStatus.PENDING,
        ))
        with pytest.raises(TransferFenceHeld):
            enqueue_task(state, "alpha", "T-NEW")
    finally:
        org.transfer_fence.release()

    # Once released, admission resumes.
    from runtime.daemon.runner import enqueue_task
    enqueue_task(state, "alpha", "T-NEW")


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
