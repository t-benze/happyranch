"""THR-195 / TASK-6016: daemon-managed workspace cleanup scheduler.

Covers the shipping seams of the founder-resolved system-default design
(THR-195 seq 129/130/131): weekly trigger decision (cadence, dedup/cooldown,
at-most-once per window), daemon trigger writes (task creation + enqueue with
fresh advisory context through the daemon-composed brief — never a Schedule
brief), the durable founder-report thread seam (create-on-first-trigger +
daemon-minted token in the brief), REPORT-ONLY semantics, mandatory
advisory/stale/non-candidate/re-derive wording, single true wall-clock
deadline across Git collection, every cardinality-cap boundary yielding
unavailable/truncated status, suffixed TASK-id conservative classification,
no candidate/safe-removal semantics, no Schedule or ordinary-session effect,
and SessionTracker live-session aggregation.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from runtime.config import Settings
from runtime.daemon import workspace_cleanup_scheduler as wcs
from runtime.daemon.sessions import SessionTracker
from runtime.infrastructure.database import Database
from runtime.models import (
    ScheduleKind,
    ScheduleRecord,
    ScheduleStatus,
    TaskRecord,
    TaskStatus,
)
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


def _write_file(path: Path, size: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)


def _make_teams(root: Path) -> TeamsRegistry:
    registry = TeamsRegistry.load(root)
    registry._teams["engineering"] = type(
        "TM", (), {"name": "engineering_manager", "team": "engineering",
                   "workers": ("dev_agent",)}
    )()
    return registry


def _make_schedule(db: Database, *, schedule_id: str, spawned: list[str]) -> ScheduleRecord:
    """A user Schedule (used only to prove the daemon never touches it)."""
    record = ScheduleRecord(
        id=schedule_id,
        agent_name="dev_agent",
        team="engineering",
        kind=ScheduleKind.RECURRING,
        status=ScheduleStatus.ARMED,
        fire_at="2026-08-28T00:00:00+00:00",
        timezone="UTC",
        normalized_brief="Unrelated user schedule brief.",
        source_instruction="founder-created user todo",
    )
    db.schedules.insert(record)
    return db.schedules.get(schedule_id)


def _insert_cleanup_task(
    db: Database,
    *,
    task_id: str,
    created_at: datetime,
    status: TaskStatus = TaskStatus.COMPLETED,
    brief: str | None = None,
) -> None:
    db.insert_task(TaskRecord(
        id=task_id,
        brief=brief or (wcs._CLEANUP_BRIEF_MARKER + "\nprior cleanup run"),
        team="engineering",
        assigned_agent=wcs._RESPONSIBLE_AGENT,
        status=status,
        created_at=created_at,
    ))


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


# ── (a) trigger decision: cadence, dedup, at-most-once per window ────────

def _sunday_0330_utc() -> datetime:
    """Next Sunday 03:30 UTC (deterministic reference for due/not-due)."""
    now = datetime.now(timezone.utc)
    days = (6 - now.weekday()) % 7
    sunday = (now + timedelta(days=days)).replace(
        hour=3, minute=30, second=0, microsecond=0,
    )
    if sunday < now:
        sunday += timedelta(days=7)
    return sunday


def test_trigger_decision_not_due_before_occurrence(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    occurrence = _sunday_0330_utc()
    just_before = occurrence - timedelta(minutes=30)  # Sunday 03:00: this week's occurrence is still in the future
    decision = wcs.decide_cleanup_trigger(db=db, now_utc=just_before, tz=timezone.utc)
    assert decision.should_trigger is False
    assert decision.reason == "not_due"


def test_trigger_decision_due_with_no_prior_run(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    occurrence = _sunday_0330_utc()
    decision = wcs.decide_cleanup_trigger(db=db, now_utc=occurrence, tz=timezone.utc)
    assert decision.should_trigger is True
    assert decision.reason is None


def test_trigger_decision_dedup_prior_run_in_flight(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    occurrence = _sunday_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100", created_at=occurrence - timedelta(days=7),
        status=TaskStatus.IN_PROGRESS,
    )
    decision = wcs.decide_cleanup_trigger(db=db, now_utc=occurrence, tz=timezone.utc)
    assert decision.should_trigger is False
    assert decision.reason == "prior_run_in_flight"


def test_trigger_decision_at_most_once_per_window(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    occurrence = _sunday_0330_utc()
    _insert_cleanup_task(db, task_id="TASK-100", created_at=occurrence + timedelta(seconds=1))
    decision = wcs.decide_cleanup_trigger(db=db, now_utc=occurrence + timedelta(minutes=5), tz=timezone.utc)
    assert decision.should_trigger is False
    assert decision.reason == "already_triggered_this_window"


def test_trigger_decision_terminal_prior_run_before_window_triggers(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    occurrence = _sunday_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100", created_at=occurrence - timedelta(days=8),
        status=TaskStatus.COMPLETED,
    )
    decision = wcs.decide_cleanup_trigger(db=db, now_utc=occurrence, tz=timezone.utc)
    assert decision.should_trigger is True


def test_trigger_decision_ignores_unrelated_tasks(tmp_path):
    """A non-cleanup dev_agent task (no marker) never counts for dedup."""
    db = Database(tmp_path / "db.sqlite")
    occurrence = _sunday_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100", created_at=occurrence + timedelta(minutes=1),
        brief="Ordinary dev_agent work, no cleanup marker.",
    )
    decision = wcs.decide_cleanup_trigger(db=db, now_utc=occurrence + timedelta(minutes=2), tz=timezone.utc)
    assert decision.should_trigger is True


# ── (b) daemon trigger: task creation + enqueue + fresh context ──────────

class _FakeQueue:
    def __init__(self) -> None:
        self.items: list[tuple[str, str]] = []

    def enqueue(self, slug: str, task_id: str) -> None:
        self.items.append((slug, task_id))


class _FakeDaemonState:
    def __init__(self) -> None:
        self.is_idle = False
        self.queue = _FakeQueue()
        self.orgs: dict[str, object] = {}


def _make_org(tmp_path: Path, db: Database, settings: Settings):
    from runtime.daemon.org_state import OrgState
    from runtime.orchestrator._paths import OrgPaths

    org_root = OrgPaths(root=tmp_path).org_dir
    org_root.mkdir(parents=True, exist_ok=True)
    return OrgState(
        slug="test", root=tmp_path, db=db, teams=_make_teams(tmp_path),
        settings=settings, orchestrator=None, sessions=SessionTracker(),
    )


@pytest.mark.asyncio
async def test_trigger_creates_report_only_task_with_fresh_advisory(tmp_path, test_settings):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "workspaces" / "dev_agent"
    _write_file(ws / "repos" / "r1" / "file.txt", 1024 * 3)
    org = _make_org(tmp_path / "orgs" / "test", db, test_settings)
    org.root = tmp_path / "orgs" / "test"
    (org.root / "workspaces").mkdir(parents=True, exist_ok=True)
    (org.root / "workspaces" / "dev_agent" / "repos" / "r1").mkdir(parents=True)
    _write_file(org.root / "workspaces" / "dev_agent" / "repos" / "r1" / "file.txt", 1024 * 3)

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is not None
    task = db.get_task(task_id)
    assert task is not None
    assert task.assigned_agent == "dev_agent"
    assert task.team == "engineering"
    assert task.brief.startswith(wcs._CLEANUP_BRIEF_MARKER)
    assert state.queue.items == [("test", task_id)]

    # Fresh advisory context packed into the daemon-composed brief.
    assert "Workspace disk context" in task.brief
    assert "measured_at" in task.brief
    assert "3.0 KiB" in task.brief or "3 KiB" in task.brief

    # REPORT-ONLY semantics.
    assert "REPORT-ONLY" in task.brief
    assert "Do NOT delete" in task.brief


@pytest.mark.asyncio
async def test_trigger_resolves_or_creates_durable_report_thread_and_mints_token(
    tmp_path, test_settings,
):
    db = Database(tmp_path / "db.sqlite")
    org_root = tmp_path / "orgs" / "test"
    org_root.mkdir(parents=True, exist_ok=True)
    org = _make_org(org_root, db, test_settings)
    org.root = org_root
    (org_root / "workspaces" / "dev_agent").mkdir(parents=True)

    state = _FakeDaemonState()
    # First trigger: daemon creates the durable thread with the fixed subject.
    task_id = await wcs.trigger_cleanup(
        org, enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    task = db.get_task(task_id)
    threads = db.list_threads(limit=50)
    matching = [t for t in threads if t.subject == wcs._CLEANUP_REPORT_THREAD_SUBJECT]
    assert len(matching) == 1
    thread_id = matching[0].id
    assert db.is_thread_participant(thread_id, "dev_agent")
    assert f"--thread {thread_id}" in task.brief
    token_match = re.search(r"'invocation_token': '([0-9a-f]{32})'", task.brief)
    assert token_match is not None
    inv = db.get_pending_invocation(token_match.group(1))
    assert inv is not None
    assert inv.thread_id == thread_id
    assert inv.agent_name == "dev_agent"

    # Second trigger reuses the SAME durable thread (no duplicate).
    task2_id = await wcs.trigger_cleanup(
        org, enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    task2 = db.get_task(task2_id)
    assert f"--thread {thread_id}" in task2.brief
    threads2 = db.list_threads(limit=50)
    matching2 = [t for t in threads2 if t.subject == wcs._CLEANUP_REPORT_THREAD_SUBJECT]
    assert len(matching2) == 1
    assert matching2[0].id == thread_id


@pytest.mark.asyncio
async def test_trigger_audits_trigger_and_never_touches_schedules(
    tmp_path, test_settings,
):
    db = Database(tmp_path / "db.sqlite")
    org_root = tmp_path / "orgs" / "test"
    org_root.mkdir(parents=True, exist_ok=True)
    org = _make_org(org_root, db, test_settings)
    org.root = org_root
    (org_root / "workspaces" / "dev_agent").mkdir(parents=True)
    _make_schedule(db, schedule_id="SCHEDULE-001", spawned=[])

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    audits = db.get_audit_logs(task_id)
    assert any(r["action"] == "workspace_cleanup_triggered" for r in audits)
    # The user Schedule row is byte-identical: no spawned_task_ids appended,
    # no status change, no purpose marker.
    schedule = db.schedules.get("SCHEDULE-001")
    assert schedule.spawned_task_ids == []
    assert schedule.status == ScheduleStatus.ARMED
    assert schedule.normalized_brief == "Unrelated user schedule brief."


@pytest.mark.asyncio
async def test_trigger_fail_open_when_measurement_unavailable(
    tmp_path, test_settings, monkeypatch,
):
    db = Database(tmp_path / "db.sqlite")
    org_root = tmp_path / "orgs" / "test"
    org_root.mkdir(parents=True, exist_ok=True)
    org = _make_org(org_root, db, test_settings)
    org.root = org_root
    (org_root / "workspaces" / "dev_agent").mkdir(parents=True)

    monkeypatch.setattr(
        wcs, "measure_workspace_context",
        lambda **kw: wcs.WorkspaceContextSnapshot(
            available=False, reason="measurement deadline exceeded",
        ),
    )
    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is not None
    task = db.get_task(task_id)
    assert "measurement unavailable" in task.brief
    assert "does not affect this run" in task.brief


@pytest.mark.asyncio
async def test_trigger_skips_when_agent_team_unresolved(tmp_path, test_settings):
    db = Database(tmp_path / "db.sqlite")
    org_root = tmp_path / "orgs" / "test"
    org_root.mkdir(parents=True, exist_ok=True)
    # Empty TeamsRegistry: dev_agent has no team → fail-closed skip.
    org = _make_org(org_root, db, test_settings)
    org.root = org_root
    org.teams = TeamsRegistry.load(org_root)

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is None
    assert state.queue.items == []
    audits = db.get_audit_logs("workspace-cleanup:skipped")
    assert any(r["action"] == "workspace_cleanup_skipped" for r in audits)


# ── (c) advisory wording + no candidate/safe-removal semantics ───────────

def test_note_wording_available():
    snap = wcs.WorkspaceContextSnapshot(
        measured_at="2026-08-28T09:32:00+00:00",
        workspaces_count=2,
        workspaces_bytes=5 * 1024 * 1024,
        largest=[("dev_agent", 4 * 1024 * 1024), ("qa_engineer", 1024 * 1024)],
        worktrees_registered=3,
        worktrees_terminal=2,
        worktrees_non_terminal=1,
        worktrees_unclassified=0,
        dep_dirs=2,
        dep_bytes=1024 * 1024,
        dep_dirs_in_worktrees=1,
        dep_bytes_in_worktrees=512 * 1024,
        live_sessions_count=1,
        live_sessions_agents=["dev_agent"],
    )
    note = wcs.format_workspace_context_note(snap)
    for required in (
        "ADVISORY ONLY", "STALE ON ARRIVAL", "NOT an eligibility list",
        "NOT a candidate list", "Re-derive every path and every fact",
        "measured_at", "dev_agent (4.0 MiB)", "2 terminal-task",
        "2 / 1.0 MiB", "1 (dev_agent)",
    ):
        assert required in note
    # No candidate/safe-removal semantics anywhere in the note.
    assert "safe to remove" not in note.lower()
    assert "candidate" not in note.lower().replace("not a candidate list", "")
    assert "remove" not in note.lower().replace("recommends or authorizes removal", "")


def test_note_wording_unavailable():
    snap = wcs.WorkspaceContextSnapshot(
        available=False, reason="measurement deadline exceeded",
    )
    note = wcs.format_workspace_context_note(snap)
    assert "measurement unavailable" in note
    assert "does not affect this run" in note
    assert "ADVISORY ONLY" in note


@pytest.mark.asyncio
async def test_brief_has_no_candidate_or_path_enumeration(tmp_path, test_settings):
    """The daemon-composed brief never enumerates executable candidates or
    concrete paths; it is aggregate-only."""
    db = Database(tmp_path / "db.sqlite")
    org_root = tmp_path / "orgs" / "test"
    org_root.mkdir(parents=True, exist_ok=True)
    org = _make_org(org_root, db, test_settings)
    org.root = org_root
    (org_root / "workspaces" / "dev_agent").mkdir(parents=True)

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    brief = db.get_task(task_id).brief
    assert "safe to remove" not in brief.lower()
    assert "rm -rf" not in brief
    assert "git worktree remove" not in brief
    assert "delete" not in brief.lower().replace("do not delete", "")
    # The brief names no concrete path under the org root.
    assert "workspaces/dev_agent" not in brief
    assert "node_modules" not in brief


# ── (d) measurement: aggregates, no path enumeration, symlink safety ─────

def test_measure_aggregates_sizes_deps_and_worktree_status(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    _insert_cleanup_task(
        db, task_id="TASK-1", created_at=datetime.now(timezone.utc),
        status=TaskStatus.COMPLETED,
    )
    ws = tmp_path / "ws"
    _write_file(ws / "a" / "repos" / "r" / ".git" / "HEAD", 10)
    _write_file(ws / "a" / "repos" / "r" / "file.txt", 1000)
    _write_file(ws / "a" / "repos" / "r" / "node_modules" / "pkg" / "index.js", 2000)
    _write_file(ws / "b" / "repos" / "r" / ".git" / "HEAD", 10)
    _write_file(ws / "b" / "repos" / "r" / "file.txt", 3000)
    paths = type("P", (), {"workspaces_dir": ws})()
    monkeypatch.setattr(
        wcs, "_git_worktree_paths",
        lambda repo_dir, timeout: ([repo_dir.parents[1] / "TASK-1-wt"], False),
    )
    snap = wcs.measure_workspace_context(paths=paths, db=db, sessions=None)
    assert snap.available is True
    assert snap.workspaces_count == 2
    # 6000 bytes of content + two 10-byte .git/HEAD markers used for repo detection.
    assert snap.workspaces_bytes == 1000 + 2000 + 3000 + 20
    assert snap.dep_dirs == 1
    assert snap.dep_bytes == 2000
    assert snap.worktrees_registered == 2
    assert snap.worktrees_terminal == 2
    assert snap.worktrees_unclassified == 0


def test_measure_skips_symlinks_and_bounds_walk(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    _write_file(ws / "a" / "real.txt", 100)
    (ws / "a" / "link").symlink_to(ws / "a")
    paths = type("P", (), {"workspaces_dir": ws})()
    monkeypatch.setattr(wcs, "_git_worktree_paths", lambda repo_dir, timeout: ([], False))
    snap = wcs.measure_workspace_context(paths=paths, db=db, sessions=None)
    assert snap.available is True
    assert snap.workspaces_bytes == 100  # the symlink target is never walked


def test_measure_never_raises_when_workspaces_dir_missing(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    paths = type("P", (), {"workspaces_dir": tmp_path / "nope"})()
    snap = wcs.measure_workspace_context(paths=paths, db=db, sessions=None)
    assert snap.available is True
    assert snap.workspaces_count == 0


def test_measure_deadline_produces_unavailable_note(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    _write_file(ws / "a" / "file.txt", 100)
    paths = type("P", (), {"workspaces_dir": ws})()

    class _Clock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            self.now += 100.0  # each read is already past any deadline
            return self.now

    monkeypatch.setattr(wcs.time, "monotonic", _Clock())
    snap = wcs.measure_workspace_context(paths=paths, db=db, sessions=None)
    assert snap.available is False
    assert snap.reason is not None


def test_measure_never_raises_on_unexpected_error(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    paths = type("P", (), {"workspaces_dir": ws})()

    def boom(**kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(wcs, "_measure", boom)
    snap = wcs.measure_workspace_context(paths=paths, db=db, sessions=None)
    assert snap.available is False
    assert "measurement error" in (snap.reason or "")


# ── (e) deadline: one true wall-clock deadline across Git collection ─────

def test_measure_bounds_git_timeout_by_remaining_deadline_and_marks_unavailable(
    tmp_path, monkeypatch,
):
    """Each git subprocess gets min(per-call cap, remaining deadline); a
    subprocess that consumes the deadline marks the snapshot unavailable.
    """
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    for repo in ("r1", "r2"):
        (ws / "dev_agent" / "repos" / repo / ".git").mkdir(parents=True)

    class _FakeClock:
        def __init__(self):
            self.now = 1000.0

        def __call__(self):
            return self.now

    clock = _FakeClock()
    monkeypatch.setattr(wcs.time, "monotonic", clock)

    git_timeouts: list[float] = []

    def fake_git(repo_dir, timeout):
        git_timeouts.append(timeout)
        clock.now += 9.0  # this git call consumes 9s of wall clock
        return [], False

    monkeypatch.setattr(wcs, "_git_worktree_paths", fake_git)

    snap = wcs.measure_workspace_context(
        paths=type("P", (), {"workspaces_dir": ws})(),
        db=db, sessions=None, deadline_seconds=10.0,
    )
    # First call got the full per-call cap; the second was bounded to the
    # remaining budget (deadline = 1000 + 10 = 1010; after the first call the
    # clock is 1009, so remaining = 1.0 → min(5.0, 1.0) = 1.0).
    assert git_timeouts == [5.0, 1.0]
    assert snap.available is False
    assert "bounded limits" in (snap.reason or "")


def test_measure_expiry_after_last_repo_marks_unavailable(tmp_path, monkeypatch):
    """A git subprocess that finishes at/after the deadline on the LAST
    repository still flips the snapshot to unavailable."""
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    (ws / "dev_agent" / "repos" / "r1" / ".git").mkdir(parents=True)

    class _FakeClock:
        def __init__(self):
            self.now = 1000.0

        def __call__(self):
            return self.now

    clock = _FakeClock()
    monkeypatch.setattr(wcs.time, "monotonic", clock)

    def fake_git(repo_dir, timeout):
        clock.now += 12.0  # single repo call overruns the whole 10s deadline
        return [], False

    monkeypatch.setattr(wcs, "_git_worktree_paths", fake_git)

    snap = wcs.measure_workspace_context(
        paths=type("P", (), {"workspaces_dir": ws})(),
        db=db, sessions=None, deadline_seconds=10.0,
    )
    assert snap.available is False
    assert "bounded limits" in (snap.reason or "")


# ── (f) cardinality caps → truncated/unavailable, boundary tests ─────────

def test_measure_workspace_cap_hit_marks_unavailable(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    for i in range(wcs._MAX_WORKSPACES + 1):
        _write_file(ws / f"ws{i}" / "file.txt", 1)
    paths = type("P", (), {"workspaces_dir": ws})()
    snap = wcs.measure_workspace_context(paths=paths, db=db, sessions=None)
    assert snap.available is False
    assert "bounded limits" in (snap.reason or "")


def test_measure_repo_cap_hit_marks_unavailable(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    repos = ws / "dev_agent" / "repos"
    for i in range(wcs._MAX_REPOS_PER_WORKSPACE + 1):
        (repos / f"r{i}" / ".git").mkdir(parents=True)
    paths = type("P", (), {"workspaces_dir": ws})()
    monkeypatch.setattr(wcs, "_git_worktree_paths", lambda repo_dir, timeout: ([], False))
    snap = wcs.measure_workspace_context(paths=paths, db=db, sessions=None)
    assert snap.available is False
    assert "bounded limits" in (snap.reason or "")


def test_measure_worktree_cap_hit_marks_unavailable(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    (ws / "dev_agent" / "repos" / "r1" / ".git").mkdir(parents=True)
    many = [str(tmp_path / "wt" / str(i)) for i in range(wcs._MAX_WORKTREES_PER_REPO + 1)]
    runner = _RecordingGitRun(by_repo={
        str(ws / "dev_agent" / "repos" / "r1"): many,
    })
    monkeypatch.setattr(wcs.subprocess, "run", runner)
    paths = type("P", (), {"workspaces_dir": ws})()
    snap = wcs.measure_workspace_context(paths=paths, db=db, sessions=None)
    assert snap.available is False
    assert "bounded limits" in (snap.reason or "")


def test_git_worktree_paths_cap_sets_truncated(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    many = [str(tmp_path / "wt" / str(i)) for i in range(wcs._MAX_WORKTREES_PER_REPO + 2)]
    runner = _RecordingGitRun(by_repo={str(repo): many})
    monkeypatch.setattr(wcs.subprocess, "run", runner)
    paths, truncated = wcs._git_worktree_paths(repo, timeout=5.0)
    assert truncated is True
    assert len(paths) == wcs._MAX_WORKTREES_PER_REPO


def test_git_worktree_paths_no_cap_no_truncation(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    three = [str(tmp_path / "wt" / str(i)) for i in range(3)]
    runner = _RecordingGitRun(by_repo={str(repo): three})
    monkeypatch.setattr(wcs.subprocess, "run", runner)
    paths, truncated = wcs._git_worktree_paths(repo, timeout=5.0)
    assert truncated is False
    assert len(paths) == 3


# ── (g) suffixed TASK-id conservative classification ─────────────────────

def test_task_id_from_worktree_name_exact_and_suffixed():
    assert wcs._task_id_from_worktree_name("TASK-5567") == "TASK-5567"
    assert wcs._task_id_from_worktree_name("TASK-5567-base691") == "TASK-5567"
    assert wcs._task_id_from_worktree_name("TASK-5829-base") == "TASK-5829"
    assert wcs._task_id_from_worktree_name("TASK-5603-baseline") == "TASK-5603"
    assert wcs._task_id_from_worktree_name("not-a-task") is None
    assert wcs._task_id_from_worktree_name("") is None


def test_measure_classifies_unknown_worktree_task_as_unclassified(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    (ws / "dev_agent" / "repos" / "r1" / ".git").mkdir(parents=True)
    unknown_wt = str(tmp_path / "wt-unknown")
    runner = _RecordingGitRun(by_repo={
        str(ws / "dev_agent" / "repos" / "r1"): [unknown_wt],
    })
    monkeypatch.setattr(wcs.subprocess, "run", runner)
    paths = type("P", (), {"workspaces_dir": ws})()
    snap = wcs.measure_workspace_context(paths=paths, db=db, sessions=None)
    assert snap.worktrees_registered == 1
    assert snap.worktrees_unclassified == 1
    assert snap.worktrees_terminal == 0
    assert snap.worktrees_non_terminal == 0


def test_terminal_statuses_parity():
    from runtime.orchestrator import run_step
    assert wcs._TERMINAL_TASK_STATUSES == frozenset(run_step.TERMINAL_STATES)


# ── (h) SessionTracker live sessions ─────────────────────────────────────

def test_session_tracker_iter_active_snapshot():
    tracker = SessionTracker()
    tracker.set_active("TASK-1", "dev_agent", "sess-1")
    tracker.set_active("TASK-2", "qa_engineer", "sess-2")
    assert sorted(tracker.iter_active()) == [
        ("TASK-1", "dev_agent", "sess-1"),
        ("TASK-2", "qa_engineer", "sess-2"),
    ]
    count, agents = wcs._live_sessions(tracker)
    assert count == 2
    assert agents == ["dev_agent", "qa_engineer"]


def test_live_sessions_fail_open_on_none_or_error():
    assert wcs._live_sessions(None) == (0, [])

    class _BoomTracker:
        def iter_active(self):
            raise RuntimeError("boom")

    assert wcs._live_sessions(_BoomTracker()) == (0, [])


# ── (i) no Schedule / ordinary-session effect ────────────────────────────

def test_orchestrator_has_no_cleanup_seam():
    """The superseded prompt-seam is gone: the orchestrator no longer imports
    or calls any workspace-cleanup context builder, so ordinary and unrelated
    Schedule-spawned sessions are byte-identical BY CONSTRUCTION."""
    import inspect
    import runtime.orchestrator.orchestrator as orch_module
    source = inspect.getsource(orch_module)
    assert "workspace_context" not in source
    assert "maybe_build_cleanup_context_note" not in source


def test_no_schedule_store_reverse_lookup_survives():
    """find_by_spawned_task_id (the rejected Schedule discriminator) is gone."""
    import inspect
    import runtime.infrastructure.schedule_store as store_module
    assert not hasattr(store_module, "find_by_spawned_task_id")
    source = inspect.getsource(store_module)
    assert "find_by_spawned_task_id" not in source


# ── (j) full _run_agent shipping seam: no advisory note in ANY session ───

_TASK_CONTEXT_CONTRACT_IDS = (
    "start-task",
    "jobs",
    "make-worktree",
    "thread",
    "dream",
    "todos",
    "create-skill",
)


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
        return __import__(
            "runtime.orchestrator.executors", fromlist=["ExecutorResult"],
        ).ExecutorResult(
            success=True, duration_seconds=1, session_id=kwargs["session_id"],
        )

    mock_executor.run.side_effect = fake_executor_run
    with patch.object(orch, "_build_executor", return_value=mock_executor):
        orch._run_agent(task_id, "dev_agent", "")
    return captured["prompt"]


def test_no_session_prompt_contains_cleanup_note(
    test_settings, test_runtime, monkeypatch,
):
    """Full _run_agent shipping seam: no session — ordinary OR
    schedule-spawned — receives any workspace-cleanup advisory note, so every
    session prompt stays byte-identical to pre-feature main."""
    _setup_protocol_skills(test_settings)
    test_runtime.root.mkdir(parents=True, exist_ok=True)
    _setup_agent_workspace(test_runtime, "dev_agent", "claude")

    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime.root)
    orch = Orchestrator(
        db=db, settings=test_settings,
        paths=test_runtime, slug="test", teams=teams,
    )
    ordinary_task_id = orch.create_task("Ordinary work")
    spawned_task_id = orch.create_task("Spawned by a Schedule")
    _make_schedule(db, schedule_id="SCHEDULE-001", spawned=[spawned_task_id])

    mock_executor = MagicMock()

    ordinary_prompt = _run_task_session(orch, ordinary_task_id, mock_executor)
    spawned_prompt = _run_task_session(orch, spawned_task_id, mock_executor)

    assert "Workspace disk context" not in ordinary_prompt
    assert "Workspace disk context" not in spawned_prompt
    assert "ADVISORY ONLY" not in spawned_prompt
    # The shared repo-freshness note is still present for every session.
    assert "Repository freshness" in ordinary_prompt
    assert "Repository freshness" in spawned_prompt


# ── (k) daemon loop: trigger/non-trigger through the async loop ──────────

class _FakeMetricsRegistry:
    def record_loop_tick(self, *args, **kwargs) -> None:
        pass


@pytest.mark.asyncio
async def test_loop_ticks_and_triggers_when_due(tmp_path, test_settings, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    org_root = tmp_path / "orgs" / "test"
    org_root.mkdir(parents=True, exist_ok=True)
    org = _make_org(org_root, db, test_settings)
    org.root = org_root
    (org_root / "workspaces" / "dev_agent").mkdir(parents=True)

    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()

    due = _sunday_0330_utc()
    triggered: list[str] = []

    async def fake_trigger(org, *, enqueue, now_utc=None):
        triggered.append(org.slug)
        return "TASK-1"

    monkeypatch.setattr(wcs, "trigger_cleanup", fake_trigger)
    monkeypatch.setattr(
        wcs, "decide_cleanup_trigger",
        lambda **kw: wcs.CleanupTriggerDecision(True, None),
    )
    await wcs._tick_org(org, state, now_utc=due)
    assert triggered == ["test"]


@pytest.mark.asyncio
async def test_loop_ticks_and_skips_when_not_due(tmp_path, test_settings, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    org_root = tmp_path / "orgs" / "test"
    org_root.mkdir(parents=True, exist_ok=True)
    org = _make_org(org_root, db, test_settings)
    org.root = org_root

    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()

    triggered: list[str] = []

    async def fake_trigger(org, *, enqueue, now_utc=None):
        triggered.append(org.slug)
        return "TASK-1"

    monkeypatch.setattr(wcs, "trigger_cleanup", fake_trigger)
    monkeypatch.setattr(
        wcs, "decide_cleanup_trigger",
        lambda **kw: wcs.CleanupTriggerDecision(False, "not_due"),
    )
    await wcs._tick_org(org, state, now_utc=_sunday_0330_utc())
    assert triggered == []


@pytest.mark.asyncio
async def test_loop_waits_out_boot_warm_up_before_any_tick(
    tmp_path, test_settings, monkeypatch,
):
    """The daemon loop honours a boot warm-up grace: no trigger scan runs
    before ``warm_up_seconds`` elapse (short-lived lifespan contexts — e.g.
    the dashboard lifespan test — never see trigger side effects)."""
    db = Database(tmp_path / "db.sqlite")
    org_root = tmp_path / "orgs" / "test"
    org_root.mkdir(parents=True, exist_ok=True)
    org = _make_org(org_root, db, test_settings)
    org.root = org_root

    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()

    ticks: list[str] = []

    async def fake_tick(org, state, now_utc):
        ticks.append(org.slug)

    monkeypatch.setattr(wcs, "_tick_org", fake_tick)

    task = asyncio.ensure_future(
        wcs.workspace_cleanup_scheduler_loop(
            state, interval_seconds=0.01, warm_up_seconds=5.0,
        )
    )
    await asyncio.sleep(0.05)  # well inside the 5s warm-up
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert ticks == []

    # With a zero warm-up the scan runs on the first tick.
    task2 = asyncio.ensure_future(
        wcs.workspace_cleanup_scheduler_loop(
            state, interval_seconds=0.01, warm_up_seconds=0.0,
        )
    )
    await asyncio.sleep(0.05)
    task2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task2
    assert ticks  # the scan ran (at least once) without a warm-up
