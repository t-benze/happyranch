"""THR-195 / TASK-5971: advisory workspace-disk context for schedule-spawned
task sessions.

Covers: fresh schedule prompt injection through the existing
``protocol_doc_manifest`` note seam, no persistence into ``normalized_brief``
or any Schedule field, mandatory advisory/stale/non-candidate/re-derive
wording, fail-open timeout/error behavior, suffixed TASK-id worktree status
classification, aggregate/no-path-enumeration behavior, absence of
candidate/safe/removal semantics, and no injection into ordinary sessions.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.config import Settings
from runtime.daemon.sessions import SessionTracker
from runtime.infrastructure.database import Database
from runtime.models import (
    ScheduleKind,
    ScheduleRecord,
    ScheduleStatus,
    TaskStatus,
)
from runtime.orchestrator import run_step, workspace_context
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry


# ── helpers ──────────────────────────────────────────────────────────────

def _fmt(n: int) -> str:
    """1024-based human size, mirroring the note formatter's units."""
    value = float(n)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TiB"


def _make_schedule(db: Database, *, schedule_id: str, spawned: list[str]) -> ScheduleRecord:
    record = ScheduleRecord(
        id=schedule_id,
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.RECURRING,
        status=ScheduleStatus.ARMED,
        fire_at="2026-08-28T00:00:00+00:00",
        timezone="UTC",
        normalized_brief="Perform one bounded agent-workspace cleanup run.",
        source_instruction="founder-approved recurring cleanup",
    )
    db.schedules.insert(record)
    if spawned:
        db.schedules.update(schedule_id, spawned_task_ids=spawned)
    return db.schedules.get(schedule_id)


def _write_file(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)


class _RecordingGitRun:
    """Fake subprocess.run that answers `git worktree list --porcelain`.

    Keyed by the repo cwd so each repo reports only its own registered
    worktrees, matching real ``git worktree list`` semantics.
    """

    def __init__(self, by_repo: dict[str, list[str]] | None = None, *, fail: bool = False):
        self.calls: list[list[str]] = []
        self._by_repo = by_repo or {}
        self._fail = fail

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        if self._fail:
            import subprocess
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=kwargs.get("timeout", 5))
        cwd = str(kwargs.get("cwd", ""))
        paths = self._by_repo.get(cwd, [])
        lines = [f"worktree {p}" for p in paths]
        return type("R", (), {
            "returncode": 0,
            "stdout": ("\n".join(lines) + "\n").encode(),
        })()


# ── (a) ScheduleStore reverse lookup ─────────────────────────────────────

def test_find_by_spawned_task_id_exact_match(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    _make_schedule(db, schedule_id="SCHEDULE-001", spawned=["TASK-100", "TASK-200"])
    _make_schedule(db, schedule_id="SCHEDULE-002", spawned=[])

    found = db.schedules.find_by_spawned_task_id("TASK-200")
    assert found is not None and found.id == "SCHEDULE-001"

    # Exact match only: a spawned TASK-100 must not match TASK-1000 lookups.
    assert db.schedules.find_by_spawned_task_id("TASK-1000") is None
    assert db.schedules.find_by_spawned_task_id("TASK-999") is None
    assert db.schedules.find_by_spawned_task_id("") is None


def test_find_by_spawned_task_id_skips_bad_json_and_unknown_rows(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    _make_schedule(db, schedule_id="SCHEDULE-001", spawned=["TASK-100"])
    with db._lock:
        db._conn.execute(
            "UPDATE schedules SET spawned_task_ids = ? WHERE id = ?",
            ("{not-json", "SCHEDULE-001"),
        )
        db._conn.commit()
    assert db.schedules.find_by_spawned_task_id("TASK-100") is None


# ── (b) SessionTracker live-session accessor ─────────────────────────────

def test_session_tracker_iter_active_snapshot():
    tracker = SessionTracker()
    tracker.set_active("TASK-1", "dev_agent", "sess-a", org_slug="test")
    tracker.set_active("TASK-2", "qa_engineer", "sess-b", org_slug="test")
    active = tracker.iter_active()
    assert sorted(active) == [
        ("TASK-1", "dev_agent", "sess-a"),
        ("TASK-2", "qa_engineer", "sess-b"),
    ]
    tracker.clear("TASK-1", "dev_agent")
    assert tracker.iter_active() == [("TASK-2", "qa_engineer", "sess-b")]


# ── (c) bounded measurement: aggregates, no path enumeration ─────────────

def test_measure_aggregates_sizes_deps_and_worktree_status(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    root = tmp_path / "runtime" / "orgs" / "test"
    ws = root / "workspaces"
    # dev_agent: 100 B file + node_modules (200 B) in primary checkout
    _write_file(ws / "dev_agent" / "repos" / "happyranch" / "src" / "a.py", 100)
    _write_file(
        ws / "dev_agent" / "repos" / "happyranch" / "src" / "node_modules" / "pkg" / "index.js",
        200,
    )
    # qa_engineer: 50 B file + .venv (300 B) inside a worktree
    _write_file(ws / "qa_engineer" / "repos" / "happyranch" / ".claude" / "worktrees" / "TASK-5567-base691" / "f.txt", 50)
    _write_file(
        ws / "qa_engineer" / "repos" / "happyranch" / ".claude" / "worktrees" / "TASK-5567-base691" / ".venv" / "bin" / "x",
        300,
    )
    # Register both clones so ``git worktree list`` is consulted per repo.
    (ws / "dev_agent" / "repos" / "happyranch" / ".git").mkdir(parents=True)
    (ws / "qa_engineer" / "repos" / "happyranch" / ".git").mkdir(parents=True)

    # Task-status join: TASK-5567 completed (terminal), TASK-5829 failed
    # (terminal), TASK-5603 missing (unclassified), TASK-7000 in_progress.
    from runtime.models import TaskRecord

    def _task(task_id: str, status: TaskStatus) -> None:
        db.insert_task(TaskRecord(
            id=task_id, brief="b", team="engineering",
            assigned_agent="dev_agent", status=status,
        ))

    _task("TASK-5567", TaskStatus.COMPLETED)
    _task("TASK-5829", TaskStatus.FAILED)
    _task("TASK-7000", TaskStatus.IN_PROGRESS)

    fake_git = _RecordingGitRun(by_repo={
        str(ws / "dev_agent" / "repos" / "happyranch"): [
            str(ws / "dev_agent" / "repos" / "happyranch" / ".claude" / "worktrees" / "TASK-5567-base691"),
        ],
        str(ws / "qa_engineer" / "repos" / "happyranch"): [
            str(ws / "qa_engineer" / "repos" / "happyranch" / ".claude" / "worktrees" / "TASK-5829-base"),
            str(ws / "qa_engineer" / "repos" / "happyranch" / ".claude" / "worktrees" / "TASK-5603-baseline"),
            str(ws / "qa_engineer" / "repos" / "happyranch" / ".claude" / "worktrees" / "TASK-7000-zzz"),
            # A worktree with no TASK- prefix → unclassified
            str(ws / "qa_engineer" / "repos" / "happyranch" / ".claude" / "worktrees" / "scratch"),
        ],
    })
    monkeypatch.setattr(workspace_context.subprocess, "run", fake_git)

    snap = workspace_context.measure_workspace_context(
        paths=type("P", (), {"workspaces_dir": ws})(),  # minimal OrgPaths stand-in
        db=db,
        sessions=None,
        deadline_seconds=30,
    )

    assert snap.available is True
    # Aggregate workspace bytes: 100 + 200 + 50 + 300 = 650
    assert snap.workspaces_count == 2
    assert snap.workspaces_bytes == 650
    # Largest workspace first: qa_engineer = 50 + 300 = 350, dev_agent = 300.
    assert snap.largest[0][0] == "qa_engineer"
    assert snap.largest[0][1] == 350
    assert snap.largest[1][0] == "dev_agent"
    assert snap.largest[1][1] == 300
    # Dependency dirs: 2 total (node_modules 200 B + .venv 300 B), one in worktrees.
    assert snap.dep_dirs == 2
    assert snap.dep_bytes == 500
    assert snap.dep_dirs_in_worktrees == 1
    assert snap.dep_bytes_in_worktrees == 300
    # Worktree classification: TASK-5567-base691 → terminal; TASK-5829-base →
    # terminal; TASK-5603-baseline → unclassified (missing task); TASK-7000-zzz
    # → non-terminal; scratch → unclassified.
    assert snap.worktrees_registered == 5
    assert snap.worktrees_terminal == 2
    assert snap.worktrees_non_terminal == 1
    assert snap.worktrees_unclassified == 2
    assert snap.live_sessions_count == 0


def test_measure_skips_symlinks_and_bounds_walk(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    target = tmp_path / "target"
    _write_file(target / "big.bin", 4096)
    (ws / "workspace").mkdir(parents=True)
    (ws / "workspace" / "link").symlink_to(target, target_is_directory=True)
    _write_file(ws / "workspace" / "real.txt", 10)

    snap = workspace_context.measure_workspace_context(
        paths=type("P", (), {"workspaces_dir": ws})(), db=db, sessions=None,
        deadline_seconds=30,
    )
    assert snap.workspaces_count == 1
    assert snap.workspaces_bytes == 10  # symlinked subtree NOT traversed


def test_measure_deadline_produces_unavailable_note(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    _write_file(ws / "w" / "f", 10)

    # Simulate the bounded walk hitting its deadline mid-measurement: the
    # strict contract turns any truncation into an explicit unavailable note.
    def _truncated_walk(workspace, *, deadline, max_entries, max_depth):
        return workspace_context._WorkspaceWalk(truncated=True)

    monkeypatch.setattr(workspace_context, "_walk_workspace", _truncated_walk)
    snap = workspace_context.measure_workspace_context(
        paths=type("P", (), {"workspaces_dir": ws})(), db=db, sessions=None,
        deadline_seconds=30,
    )
    assert snap.available is False
    note = workspace_context.format_workspace_context_note(snap)
    assert "measurement unavailable" in note
    assert "does not affect this session" in note


def test_measure_never_raises_when_workspaces_dir_missing(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    snap = workspace_context.measure_workspace_context(
        paths=type("P", (), {"workspaces_dir": tmp_path / "nope"})(),
        db=db, sessions=None, deadline_seconds=30,
    )
    assert snap.available is True
    assert snap.workspaces_count == 0


# ── (d) advisory wording ─────────────────────────────────────────────────

def test_note_wording_available(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    _write_file(ws / "dev_agent" / "f", 1024)
    snap = workspace_context.measure_workspace_context(
        paths=type("P", (), {"workspaces_dir": ws})(), db=db, sessions=None,
        deadline_seconds=30,
    )
    note = workspace_context.format_workspace_context_note(snap)

    assert "ADVISORY ONLY" in note
    assert "STALE ON ARRIVAL" in note
    assert "NOT an eligibility list" in note
    assert "NOT a candidate list" in note
    assert "Re-derive every path and every fact" in note
    assert "measured_at" in note
    # No path enumeration, no candidate/safe/removal semantics. The advisory
    # warning itself negates "eligibility" / "candidate" / "removal", so only
    # positive executable phrasing is banned here.
    for banned in (
        "safe to", "safe for", "recommended removals", "recommend removal",
        "eligible:", "candidate:", "delete ", "rm -rf", "blocked_on_job",
        "pending job", "executable candidate", "\n  /",
    ):
        assert banned not in note.lower()


def test_note_wording_unavailable():
    note = workspace_context.format_workspace_context_note(
        workspace_context.WorkspaceContextSnapshot(
            available=False, reason="measurement deadline exceeded",
            measured_at="2026-08-28T00:00:00+00:00",
        )
    )
    assert "ADVISORY ONLY" in note
    assert "NOT an eligibility list" in note
    assert "measurement unavailable" in note
    assert "does not affect this session" in note


# ── (e) seam helper: scoping + fail-open ─────────────────────────────────

def test_maybe_build_returns_empty_for_ordinary_task(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    _make_schedule(db, schedule_id="SCHEDULE-001", spawned=[])
    note = workspace_context.maybe_build_cleanup_context_note(
        db=db,
        paths=type("P", (), {"workspaces_dir": tmp_path / "ws"})(),
        sessions=None,
        task_id="TASK-500",
    )
    assert note == ""


def test_maybe_build_returns_note_only_for_spawned_task(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    _make_schedule(db, schedule_id="SCHEDULE-001", spawned=["TASK-500"])
    ws = tmp_path / "ws"
    _write_file(ws / "dev_agent" / "f", 10)
    note = workspace_context.maybe_build_cleanup_context_note(
        db=db,
        paths=type("P", (), {"workspaces_dir": ws})(),
        sessions=None,
        task_id="TASK-500",
    )
    assert "ADVISORY ONLY" in note
    # Ordinary task: no measurement, no note.
    assert workspace_context.maybe_build_cleanup_context_note(
        db=db,
        paths=type("P", (), {"workspaces_dir": ws})(),
        sessions=None,
        task_id="TASK-501",
    ) == ""


def test_maybe_build_fail_open_on_lookup_error(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    db.schedules.find_by_spawned_task_id = MagicMock(
        side_effect=RuntimeError("boom"),
    )
    note = workspace_context.maybe_build_cleanup_context_note(
        db=db,
        paths=type("P", (), {"workspaces_dir": tmp_path / "ws"})(),
        sessions=None,
        task_id="TASK-500",
    )
    assert note == ""


def test_maybe_build_never_raises_when_measurement_errors(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    _make_schedule(db, schedule_id="SCHEDULE-001", spawned=["TASK-500"])

    def _boom(**kwargs):
        raise RuntimeError("measurement exploded")

    monkeypatch.setattr(workspace_context, "measure_workspace_context", _boom)
    note = workspace_context.maybe_build_cleanup_context_note(
        db=db,
        paths=type("P", (), {"workspaces_dir": tmp_path / "ws"})(),
        sessions=None,
        task_id="TASK-500",
    )
    # Fail-open: an unavailable advisory note, never an exception, never ''.
    assert "measurement unavailable" in note


def test_no_persistence_into_schedule_fields(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    before = _make_schedule(db, schedule_id="SCHEDULE-001", spawned=["TASK-500"])
    ws = tmp_path / "ws"
    _write_file(ws / "dev_agent" / "f", 10)
    workspace_context.maybe_build_cleanup_context_note(
        db=db,
        paths=type("P", (), {"workspaces_dir": ws})(),
        sessions=None,
        task_id="TASK-500",
    )
    after = db.schedules.get("SCHEDULE-001")
    assert after.normalized_brief == before.normalized_brief
    assert after.source_instruction == before.source_instruction
    assert after.spawned_task_ids == before.spawned_task_ids
    assert after.kind == before.kind


def test_terminal_statuses_parity():
    assert workspace_context._TERMINAL_TASK_STATUSES == run_step.TERMINAL_STATES


# ── (f) full orchestrator seam: injection only for schedule-spawned tasks ─

_TASK_CONTEXT_CONTRACT_IDS = ["start-task", "jobs", "make-worktree", "thread", "dream", "todos"]


def _setup_protocol_skills(settings: Settings) -> None:
    for sid in _TASK_CONTEXT_CONTRACT_IDS:
        src = settings.get_protocol_dir() / "skills" / sid
        src.mkdir(parents=True, exist_ok=True)
        (src / "SKILL.md").write_text(f"# {sid}\n\nSkill body for {sid}.\n")


def _setup_agent_workspace(runtime, agent: str, provider: str) -> None:
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    ws = runtime.workspaces_dir / agent
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "task_history.md").write_text(f"# Task History: {agent}\n\n")
    (ws / "AGENTS.md").write_text(f"# Agent: {agent}\n")
    ad = AgentDef(
        name=agent, team="engineering", role="worker",
        executor=provider, allow_rules=(), repos={},
        enrolled_by=None, enrolled_at_task=None, enrolled_at=None,
        system_prompt=f"You are {agent}.", description="", model=None,
    )
    runtime.agents_dir.mkdir(parents=True, exist_ok=True)
    (runtime.agents_dir / f"{agent}.md").write_text(render_agent_text(ad))


def _run_task_session(orch: Orchestrator, task_id: str, mock_executor) -> str:
    """Run one task session, capturing the composed executor prompt."""
    captured: dict[str, str] = {}

    def fake_executor_run(**kwargs):
        captured["prompt"] = kwargs["prompt"]
        return ExecutorResult(
            success=True, duration_seconds=1, session_id=kwargs["session_id"],
        )

    mock_executor.run.side_effect = fake_executor_run
    with patch.object(orch, "_build_executor", return_value=mock_executor):
        orch._run_agent(task_id, "dev_agent", "")
    return captured["prompt"]


def test_orchestrator_injects_note_only_for_schedule_spawned_task(
    test_settings, test_runtime, monkeypatch,
):
    _setup_protocol_skills(test_settings)
    test_runtime.root.mkdir(parents=True, exist_ok=True)
    _setup_agent_workspace(test_runtime, "dev_agent", "claude")

    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime.root)
    orch = Orchestrator(
        db=db, settings=test_settings,
        paths=test_runtime, slug="test", teams=teams,
    )
    spawned_task_id = orch.create_task("Spawned cleanup work")
    ordinary_task_id = orch.create_task("Ordinary work")
    _make_schedule(db, schedule_id="SCHEDULE-001", spawned=[spawned_task_id])

    # Keep the session prompt deterministic: no real repos in tmp workspace,
    # so refresh is a no-op; measurement runs only for the spawned task.
    mock_executor = MagicMock()

    spawned_prompt = _run_task_session(orch, spawned_task_id, mock_executor)
    ordinary_prompt = _run_task_session(orch, ordinary_task_id, mock_executor)

    assert "Workspace disk context" in spawned_prompt
    assert "ADVISORY ONLY" in spawned_prompt
    assert "Workspace disk context" not in ordinary_prompt
    # Ordinary prompt is otherwise unchanged by the new seam (repo note still
    # present for every task session).
    assert "Repository freshness" in ordinary_prompt
    assert "Repository freshness" in spawned_prompt
