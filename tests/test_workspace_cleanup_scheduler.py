"""THR-195 / TASK-6036: daemon-managed workspace cleanup scheduler.

Covers the shipping seams of the founder-resolved system-default design
(THR-195 seq 129/130/131 + TASK-6036 defaults): per-agent daily trigger
decision (cadence, per-agent window dedup, at-most-once per window),
per-agent >= 1 GiB trigger/non-trigger, owning-agent routing, exact
first-two report-only behavior, daemon trigger writes (task creation +
enqueue with fresh advisory context through the daemon-composed brief —
never a Schedule brief), the per-agent durable founder-report thread seam
(create-on-first-trigger, participant-authorized task-bound send path, NO
minted token), the enabled-by-default kill switch, mandatory
advisory/stale/non-candidate/re-derive wording, single true wall-clock
deadline across Git collection, every cardinality-cap boundary yielding
unavailable/truncated status, suffixed TASK-id conservative classification,
no candidate/safe-removal semantics, no Schedule or ordinary-session effect,
and SessionTracker live-session aggregation.
"""
from __future__ import annotations

import asyncio
import inspect
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
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
    ThreadRecord,
)
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry


@pytest.fixture(autouse=True)
def _dormant_consumer_tripwires(monkeypatch):
    """Every ordinary scheduler path must remain disconnected from G1's consumer."""
    from runtime.daemon import task_scratch_reclamation

    dormant_consumer_calls = []
    executor_calls = []

    def forbid_dormant_consumer(*args, **kwargs):
        dormant_consumer_calls.append((args, kwargs))
        raise AssertionError("dormant synchronous consumer called")

    def forbid_executor(*args, **kwargs):
        executor_calls.append((args, kwargs))
        raise AssertionError("ledger executor called")

    # Patch the consumer's actual module-global lookup sites. The teardown
    # assertions remain observable even if a scheduler path swallows errors.
    monkeypatch.setattr(
        task_scratch_reclamation,
        "collect_revalidate_seal_consume_disposable",
        forbid_dormant_consumer,
    )
    monkeypatch.setattr(task_scratch_reclamation, "execute_ledger", forbid_executor)
    yield
    assert not dormant_consumer_calls
    assert not executor_calls


def test_fail_open_threshold_contract_is_consistent_across_surviving_surfaces():
    """Unavailable measurements remain advisory; only numeric low values skip."""
    root = Path(__file__).parents[1]
    surfaces = {
        "CLAUDE.md": (root / "CLAUDE.md").read_text(),
        "scheduler module": (
            root / "runtime/daemon/workspace_cleanup_scheduler.py"
        ).read_text(),
    }

    normalized = {
        name: " ".join(source.split()) for name, source in surfaces.items()
    }
    for name, source in normalized.items():
        assert "bypasses only numeric threshold evaluation" in source, name
        assert "otherwise-due spawning continues" in source, name
        assert "honest unavailable advisory context" in source, name
        assert (
            "only an available numeric result below 1 GiB skips" in source
        ), name

    obsolete_claims = {
        "CLAUDE.md": "and the agent's workspace totals >= 1 GiB, triggers",
        "scheduler module": (
            "trigger only when that agent's workspace total is >= 1 GiB"
        ),
    }
    for name, claim in obsolete_claims.items():
        assert claim not in normalized[name], name


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
                   "workers": ("dev_agent", "qa_engineer")}
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
    agent: str = "dev_agent",
    created_at: datetime,
    status: TaskStatus = TaskStatus.COMPLETED,
    brief: str | None = None,
) -> None:
    db.insert_task(TaskRecord(
        id=task_id,
        brief=brief or (wcs._CLEANUP_BRIEF_MARKER + "\nprior cleanup run"),
        team="engineering",
        assigned_agent=agent,
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


# ── (a) per-agent trigger decision: cadence, dedup, at-most-once ─────────

def _daily_0330_utc() -> datetime:
    """Next local 03:30 UTC (deterministic reference for due/not-due)."""
    now = datetime.now(timezone.utc)
    occurrence = now.replace(hour=3, minute=30, second=0, microsecond=0)
    if occurrence <= now:
        occurrence += timedelta(days=1)
    return occurrence


def test_trigger_decision_not_due_before_occurrence(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    occurrence = _daily_0330_utc()
    just_before = occurrence - timedelta(minutes=30)  # local 03:00: today's 03:30 occurrence is still in the future
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=just_before, tz=timezone.utc,
    )
    assert decision.should_trigger is False
    assert decision.reason == "not_due"


def test_trigger_decision_due_with_no_prior_run(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    occurrence = _daily_0330_utc()
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occurrence, tz=timezone.utc,
    )
    assert decision.should_trigger is True
    assert decision.reason is None


def test_trigger_decision_dedup_prior_run_in_flight(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    occurrence = _daily_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100", created_at=occurrence - timedelta(days=7),
        status=TaskStatus.IN_PROGRESS,
    )
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occurrence, tz=timezone.utc,
    )
    assert decision.should_trigger is False
    assert decision.reason == "prior_run_in_flight"


def test_trigger_decision_suppresses_other_nonterminal_statuses(tmp_path):
    """Any non-terminal status (e.g. ESCALATED) suppresses; terminal set is
    exactly COMPLETED/FAILED/SUPERSEDED/CANCELLED."""
    db = Database(tmp_path / "db.sqlite")
    occurrence = _daily_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100", created_at=occurrence - timedelta(days=7),
        status=TaskStatus.ESCALATED,
    )
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occurrence, tz=timezone.utc,
    )
    assert decision.should_trigger is False
    assert decision.reason == "prior_run_in_flight"


def test_trigger_decision_at_most_once_per_window(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    occurrence = _daily_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100", created_at=occurrence + timedelta(seconds=1),
    )
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent",
        now_utc=occurrence + timedelta(seconds=5), tz=timezone.utc,
    )
    assert decision.should_trigger is False
    assert decision.reason == "already_triggered_this_window"


def test_trigger_decision_terminal_prior_run_before_window_triggers(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    occurrence = _daily_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100", created_at=occurrence - timedelta(days=8),
        status=TaskStatus.COMPLETED,
    )
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occurrence, tz=timezone.utc,
    )
    assert decision.should_trigger is True


def test_trigger_decision_ignores_unrelated_tasks(tmp_path):
    """A non-cleanup task (no marker) never counts for dedup or the window."""
    db = Database(tmp_path / "db.sqlite")
    occurrence = _daily_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100", created_at=occurrence + timedelta(seconds=1),
        brief="Ordinary dev_agent work, no cleanup marker.",
    )
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent",
        now_utc=occurrence + timedelta(seconds=2), tz=timezone.utc,
    )
    assert decision.should_trigger is True


def test_trigger_decision_no_rolling_cooldown_prior_window_permits(tmp_path):
    """S11/C2: a terminal prior run created in the PREVIOUS window (even
    seconds after yesterday's boundary) never delays the current window via
    elapsed-hours arithmetic — only the current occurrence boundary decides."""
    db = Database(tmp_path / "db.sqlite")
    occurrence = _daily_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100",
        created_at=occurrence - timedelta(days=1) + timedelta(seconds=42),
        status=TaskStatus.COMPLETED,
    )
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occurrence, tz=timezone.utc,
    )
    assert decision.should_trigger is True
    assert decision.reason is None


def test_trigger_decision_current_window_terminal_run_suppresses(tmp_path):
    """S11/C2: a terminal run inside the current window (seconds late)
    suppresses; there is no elapsed-hours cooldown."""
    db = Database(tmp_path / "db.sqlite")
    occurrence = _daily_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100",
        created_at=occurrence + timedelta(seconds=42),
        status=TaskStatus.COMPLETED,
    )
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent",
        now_utc=occurrence + timedelta(minutes=5),
        previous_scan_utc=occurrence - timedelta(seconds=1),
        tz=timezone.utc,
    )
    assert decision.should_trigger is False
    assert decision.reason == "already_triggered_this_window"


def test_trigger_decision_is_per_agent(tmp_path):
    """Agent A's in-flight cleanup task never suppresses agent B's trigger."""
    db = Database(tmp_path / "db.sqlite")
    occurrence = _daily_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100", agent="dev_agent",
        created_at=occurrence - timedelta(days=7),
        status=TaskStatus.IN_PROGRESS,
    )
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="qa_engineer", now_utc=occurrence, tz=timezone.utc,
    )
    assert decision.should_trigger is True


# ── (b) daemon trigger: per-agent threshold, owning-agent routing ─────────

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


def _org_with_workspaces(tmp_path: Path, db: Database, settings: Settings):
    org_root = tmp_path / "orgs" / "test"
    org_root.mkdir(parents=True, exist_ok=True)
    org = _make_org(org_root, db, settings)
    org.root = org_root
    (org_root / "workspaces" / "dev_agent").mkdir(parents=True, exist_ok=True)
    (org_root / "workspaces" / "qa_engineer").mkdir(parents=True, exist_ok=True)
    return org


@pytest.mark.asyncio
async def test_trigger_creates_task_for_owning_agent_with_fresh_advisory(
    tmp_path, test_settings, monkeypatch,
):
    """A due agent with a >= 1 GiB workspace (threshold monkeypatched small
    for determinism) gets an ordinary task assigned to ITSELF, carrying the
    fresh advisory snapshot in the daemon-composed brief."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(
        org.root / "workspaces" / "dev_agent" / "repos" / "r1" / "file.txt",
        1024 * 3,
    )
    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
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

    # First run: strictly report-only.
    assert "THIS RUN IS STRICTLY REPORT-ONLY" in task.brief
    assert "Do NOT delete" in task.brief


@pytest.mark.asyncio
async def test_trigger_skips_below_1gib_threshold(tmp_path, test_settings):
    """An agent whose workspace measures below the >= 1 GiB founder threshold
    is NOT triggered; the skip is audited and no task/thread is created."""
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(
        org.root / "workspaces" / "dev_agent" / "repos" / "r1" / "file.txt",
        1024 * 3,  # 3 KiB << 1 GiB
    )
    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is None
    assert state.queue.items == []
    audits = db.get_audit_logs("workspace-cleanup:skipped")
    assert any(
        r["action"] == "workspace_cleanup_skipped"
        and r["payload"].get("reason") == "workspace_below_threshold"
        for r in audits
    )


@pytest.mark.asyncio
async def test_trigger_routes_to_owning_agent(tmp_path, test_settings, monkeypatch):
    """Owning-agent routing: qa_engineer's triggered task is assigned to
    qa_engineer, not a fixed dev_agent."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(
        org.root / "workspaces" / "qa_engineer" / "repos" / "r1" / "file.txt",
        1024,
    )
    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="qa_engineer",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is not None
    task = db.get_task(task_id)
    assert task.assigned_agent == "qa_engineer"
    assert f"agent qa_engineer" in task.brief


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("reason", "truncated", "expected_note"),
    [
        (
            "measurement deadline exceeded",
            True,
            "measurement truncated/unavailable",
        ),
        (
            "workspace could not be measured (unreadable)",
            False,
            "measurement unavailable",
        ),
        (
            "workspace cardinality cap exceeded",
            True,
            "measurement truncated/unavailable",
        ),
    ],
)
async def test_trigger_fail_open_when_measurement_unavailable(
    tmp_path, test_settings, monkeypatch, reason, truncated, expected_note,
):
    """Every bounded-unavailable class still creates a task whose advisory
    note honestly carries the unavailable/truncated reason."""
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)

    monkeypatch.setattr(
        wcs, "measure_workspace_context",
        lambda *a, **kw: wcs.WorkspaceContextSnapshot(
            available=False, reason=reason, truncated=truncated,
        ),
    )
    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is not None
    assert state.queue.items == [("test", task_id)]
    task = db.get_task(task_id)
    assert expected_note in task.brief
    assert reason in task.brief
    assert "No sizing data was packed" in task.brief
    assert "workspace total:" not in task.brief
    audits = db.get_audit_logs(task_id)
    assert any(
        r["action"] == "workspace_cleanup_triggered"
        and r["payload"].get("measurement_available") is False
        and r["payload"].get("measurement_reason") == reason
        and r["payload"].get("measurement_truncated") is truncated
        for r in audits
    )


@pytest.mark.asyncio
async def test_due_scheduler_tick_spawns_when_measurement_is_unavailable(
    tmp_path, test_settings, monkeypatch,
):
    """The shipping decision→tick→trigger path must not reinterpret an
    unavailable size as a failed numeric threshold comparison."""
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    from runtime.daemon import task_scratch_report as reports, task_scratch_reclamation
    from runtime.orchestrator.task_scratch import prepare_task_scratch
    from tests.test_task_scratch_report import _proc, _snapshot
    org.sessions = SessionTracker()
    monkeypatch.setattr(reports, "_PROC_ROOT", _proc(tmp_path))
    monkeypatch.setattr(reports, "_STARTED_MONOTONIC", 0)
    workspace = org.root / "workspaces/dev_agent"
    candidate_id = db.next_task_id()
    db.insert_task(TaskRecord(id=candidate_id, brief="missed teardown", assigned_agent="dev_agent",
                             current_session_id="old-session", status=TaskStatus.COMPLETED))
    scratch = prepare_task_scratch(workspace=workspace, task_id=candidate_id,
                                  producer_kind="agent", producer_id="old-session")
    for index in range(100):
        (scratch.root / str(index)).write_bytes(b"x" * 8192)
    (workspace / "repos").mkdir()
    (workspace / "repos/keep").write_bytes(b"repository")
    before = _snapshot(workspace)
    cfg_path = org.root / "org" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("timezone: UTC\n")
    monkeypatch.setattr(
        wcs, "measure_workspace_context",
        lambda *a, **kw: wcs.WorkspaceContextSnapshot(
            available=False,
            reason="workspace measurement did not complete within bounded limits "
            "(deadline exceeded)",
            truncated=True,
        ),
    )
    state = _FakeDaemonState()
    state.orgs = {"test": org}
    occurrence = _daily_0330_utc()

    await wcs._tick_org(
        org,
        state,
        now_utc=occurrence,
        previous_scan_utc=occurrence - timedelta(seconds=1),
    )

    assert len(state.queue.items) == 2
    tasks = [db.get_task(task_id) for _, task_id in state.queue.items]
    assert {task.assigned_agent for task in tasks} == {
        "dev_agent", "qa_engineer",
    }
    assert all(
        "measurement truncated/unavailable" in task.brief for task in tasks
    )
    assert all("deadline exceeded" in task.brief for task in tasks)

    rows = [row for row in db.get_audit_logs(candidate_id) if row["action"] == reports.AUDIT_ACTION]
    assert len(rows) == 1
    payload = rows[0]["payload"]
    assert payload["decision"] == "would_reclaim", payload["reasons"]
    assert payload["source"] == "weekly" and payload["report_only"]
    assert payload["allocated_bytes"] > 0 and payload["entries"] >= 100
    assert payload["actual_reclaimed_bytes"] == payload["actual_reclaimed_inodes"] == 0
    assert payload["candidate_identity"] and payload["boot_id"] == payload["coverage_boot_id"]
    assert before == _snapshot(workspace)


@pytest.mark.asyncio
async def test_due_tick_spawns_when_partial_traversal_is_unreadable(
    tmp_path, test_settings, monkeypatch,
):
    """A traversal error invalidates already-counted totals at the shipping
    tick seam, so a partial size can never suppress an otherwise-due run."""
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    cfg_path = org.root / "org" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("timezone: UTC\n")
    workspace = org.root / "workspaces" / "dev_agent"
    _write_file(workspace / "readable.txt", 100)
    unreadable = workspace / "unreadable"
    unreadable.mkdir()

    real_scandir = wcs.os.scandir

    def partly_unreadable_scandir(path):
        if Path(path) == unreadable:
            raise PermissionError("permission denied")
        return real_scandir(path)

    monkeypatch.setattr(wcs.os, "scandir", partly_unreadable_scandir)
    state = _FakeDaemonState()
    occurrence = _daily_0330_utc()

    await wcs._tick_org(
        org,
        state,
        now_utc=occurrence,
        previous_scan_utc=occurrence - timedelta(seconds=1),
    )

    assert len(state.queue.items) == 1
    task = db.get_task(state.queue.items[0][1])
    assert task is not None
    assert task.assigned_agent == "dev_agent"
    assert "measurement unavailable" in task.brief
    assert "workspace could not be measured (unreadable)" in task.brief
    assert "No sizing data was packed" in task.brief
    assert "workspace total:" not in task.brief
    skipped = db.get_audit_logs("workspace-cleanup:skipped")
    assert not any(
        row["payload"].get("reason") == "workspace_below_threshold"
        and row["agent"] == "dev_agent"
        for row in skipped
    )
    triggered = db.get_audit_logs(task.id)
    assert any(
        row["action"] == "workspace_cleanup_triggered"
        and row["payload"].get("measurement_available") is False
        and row["payload"].get("measurement_reason")
        == "workspace could not be measured (unreadable)"
        and row["payload"].get("measurement_truncated") is False
        for row in triggered
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_method", ["is_symlink", "is_dir", "stat"])
@pytest.mark.parametrize("partial_count", [False, True])
async def test_due_tick_spawns_when_entry_metadata_is_unreadable(
    tmp_path, test_settings, monkeypatch, failing_method, partial_count,
):
    """Every caught DirEntry metadata failure invalidates zero/partial totals
    through the real walk, measurement, trigger, and due-tick shipping seams.
    """
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    cfg_path = org.root / "org" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("timezone: UTC\n")
    workspace = org.root / "workspaces" / "dev_agent"
    if partial_count:
        _write_file(workspace / "a-readable.txt", 100)
    _write_file(workspace / "z-failure-target", 200)

    real_scandir = wcs.os.scandir

    class FailingEntry:
        def __init__(self, entry):
            self._entry = entry

        def __getattr__(self, name):
            return getattr(self._entry, name)

        def is_symlink(self):
            if failing_method == "is_symlink":
                raise PermissionError("metadata permission denied")
            return self._entry.is_symlink()

        def is_dir(self, *, follow_symlinks=True):
            if failing_method == "is_dir":
                raise PermissionError("metadata permission denied")
            return self._entry.is_dir(follow_symlinks=follow_symlinks)

        def stat(self, *, follow_symlinks=True):
            if failing_method == "stat":
                raise PermissionError("metadata permission denied")
            return self._entry.stat(follow_symlinks=follow_symlinks)

    class OrderedScandir:
        def __init__(self, path):
            self._context = real_scandir(path)

        def __enter__(self):
            entries = sorted(self._context.__enter__(), key=lambda entry: entry.name)
            return iter([
                FailingEntry(entry) if entry.name == "z-failure-target" else entry
                for entry in entries
            ])

        def __exit__(self, *args):
            return self._context.__exit__(*args)

    monkeypatch.setattr(wcs.os, "scandir", OrderedScandir)
    state = _FakeDaemonState()
    occurrence = _daily_0330_utc()

    await wcs._tick_org(
        org,
        state,
        now_utc=occurrence,
        previous_scan_utc=occurrence - timedelta(seconds=1),
    )

    assert len(state.queue.items) == 1
    task = db.get_task(state.queue.items[0][1])
    assert task is not None
    assert "measurement unavailable" in task.brief
    assert "workspace could not be measured (unreadable)" in task.brief
    assert "No sizing data was packed" in task.brief
    assert "workspace total:" not in task.brief
    skipped = db.get_audit_logs("workspace-cleanup:skipped")
    assert not any(
        row["payload"].get("reason") == "workspace_below_threshold"
        and row["agent"] == "dev_agent"
        for row in skipped
    )
    triggered = db.get_audit_logs(task.id)
    assert any(
        row["action"] == "workspace_cleanup_triggered"
        and row["payload"].get("measurement_available") is False
        and row["payload"].get("measurement_reason")
        == "workspace could not be measured (unreadable)"
        and "workspaces_bytes" not in row["payload"]
        for row in triggered
    )


@pytest.mark.asyncio
async def test_trigger_skips_when_agent_team_unresolved(tmp_path, test_settings):
    db = Database(tmp_path / "db.sqlite")
    org_root = tmp_path / "orgs" / "test"
    org_root.mkdir(parents=True, exist_ok=True)
    # Empty TeamsRegistry: no agent has a team → fail-closed skip.
    org = _make_org(org_root, db, test_settings)
    org.root = org_root
    org.teams = TeamsRegistry.load(org_root)
    (org_root / "workspaces" / "dev_agent").mkdir(parents=True)

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is None
    assert state.queue.items == []
    audits = db.get_audit_logs("workspace-cleanup:skipped")
    assert any(
        r["action"] == "workspace_cleanup_skipped"
        and r["payload"].get("reason") == "agent_team_unresolved"
        for r in audits
    )


@pytest.mark.asyncio
async def test_trigger_audits_trigger_and_never_touches_schedules(
    tmp_path, test_settings, monkeypatch,
):
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)
    _make_schedule(db, schedule_id="SCHEDULE-001", spawned=[])

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    audits = db.get_audit_logs(task_id)
    assert any(r["action"] == "workspace_cleanup_triggered" for r in audits)
    # The user Schedule row is byte-identical: no spawned_task_ids appended,
    # no status change, no purpose marker.
    schedule = db.schedules.get("SCHEDULE-001")
    assert schedule.spawned_task_ids == []
    assert schedule.status == ScheduleStatus.ARMED
    assert schedule.normalized_brief == "Unrelated user schedule brief."


# ── (c) exact first-two report-only behavior ──────────────────────────────

@pytest.mark.asyncio
async def _trigger_with_seeded_runs(
    tmp_path, test_settings, monkeypatch, *, seeded: int,
) -> str:
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)
    now = datetime.now(timezone.utc)
    for i in range(seeded):
        _insert_cleanup_task(
            db, task_id=f"TASK-{100 + i}", agent="dev_agent",
            created_at=now - timedelta(days=30 + i),
            status=TaskStatus.COMPLETED,
        )
    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is not None
    return db.get_task(task_id).brief


@pytest.mark.asyncio
async def test_first_two_runs_are_strictly_report_only(tmp_path, test_settings, monkeypatch):
    """Runs #1 and #2 per agent carry the STRICT report-only brief; run #3
    carries the approved TASK-5552 §4 cleanup brief (first-two boundary)."""
    brief1 = await _trigger_with_seeded_runs(
        tmp_path / "case1", test_settings, monkeypatch, seeded=0,
    )
    assert "THIS RUN IS STRICTLY REPORT-ONLY" in brief1
    assert "Allowed cache action" not in brief1
    assert "git -C <primary> worktree remove" not in brief1

    brief2 = await _trigger_with_seeded_runs(
        tmp_path / "case2", test_settings, monkeypatch, seeded=1,
    )
    assert "THIS RUN IS STRICTLY REPORT-ONLY" in brief2
    assert "Allowed cache action" not in brief2

    brief3 = await _trigger_with_seeded_runs(
        tmp_path / "case3", test_settings, monkeypatch, seeded=2,
    )
    assert "THIS RUN IS STRICTLY REPORT-ONLY" not in brief3
    assert "Allowed cache action" in brief3
    assert "git -C <primary> worktree remove" in brief3
    assert "blocked_on_job_ids" in brief3  # §4: jobs are diagnostics only
    assert "report-only" not in brief3.lower().replace(
        "none in report-only", "",
    ) or "STRICTLY REPORT-ONLY" not in brief3


@pytest.mark.asyncio
async def test_report_only_brief_has_no_candidate_or_path_enumeration(
    tmp_path, test_settings, monkeypatch,
):
    """The report-only brief never enumerates executable candidates or
    concrete paths; it is aggregate-only."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    brief = db.get_task(task_id).brief
    assert "safe to remove" not in brief.lower()
    assert "rm -rf" not in brief
    assert "git worktree remove" not in brief
    assert "delete" not in brief.lower().replace("do not delete", "")
    # The brief names no concrete path under the org root.
    assert "workspaces/dev_agent" not in brief
    assert "node_modules" not in brief


# ── (d) advisory wording + no candidate/safe-removal semantics ────────────

def test_note_wording_available():
    snap = wcs.WorkspaceContextSnapshot(
        measured_at="2026-08-28T09:32:00+00:00",
        workspaces_count=1,
        workspaces_bytes=4 * 1024 * 1024 * 1024,
        largest=[("dev_agent", 4 * 1024 * 1024 * 1024)],
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
        "measured_at", "dev_agent (4.0 GiB)", "2 terminal-task",
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


def test_inode_observation_is_fail_open_and_actionable(monkeypatch):
    class Stat:
        f_files = 100
        f_favail = 5

    snap = wcs.WorkspaceContextSnapshot()
    monkeypatch.setattr(wcs.os, "statvfs", lambda _path: Stat())
    wcs._observe_inodes(snap)
    assert (snap.inode_used, snap.inode_free, snap.inode_total) == (95, 5, 100)
    assert snap.inode_percent == 95.0
    assert snap.inode_threshold_state == "alert"
    note = wcs.format_workspace_context_note(snap)
    assert "temporary-file producers and filesystem usage" in note
    assert "advisory and not cleanup authority" in note

    monkeypatch.setattr(wcs.os, "statvfs", lambda _path: (_ for _ in ()).throw(OSError("down")))
    failed = wcs.WorkspaceContextSnapshot()
    wcs._observe_inodes(failed)
    assert failed.available is True
    assert failed.inode_available is False
    assert "down" in wcs.format_workspace_context_note(failed)


def test_scheduler_has_no_temporary_filesystem_mutation_surface():
    source = inspect.getsource(wcs)
    for forbidden in (
        "runtime.daemon.managed_temp",
        "os.rename(",
        "os.replace(",
        "os.unlink(",
        "shutil.rmtree(",
        ".unlink(",
    ):
        assert forbidden not in source


# ── (e) measurement: per-agent aggregates, symlink safety, fail-open ──────

def test_measure_aggregates_sizes_deps_and_worktree_status(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    _insert_cleanup_task(
        db, task_id="TASK-1", created_at=datetime.now(timezone.utc),
        status=TaskStatus.COMPLETED,
    )
    ws = tmp_path / "ws"
    _write_file(ws / "repos" / "r" / ".git" / "HEAD", 10)
    _write_file(ws / "repos" / "r" / "file.txt", 1000)
    _write_file(ws / "repos" / "r" / "node_modules" / "pkg" / "index.js", 2000)
    monkeypatch.setattr(
        wcs, "_git_worktree_paths",
        lambda repo_dir, timeout: ([ws / ".claude" / "worktrees" / "TASK-1-wt"], False),
    )
    snap = wcs.measure_workspace_context(ws, db=db, sessions=None)
    assert snap.available is True
    assert snap.workspaces_count == 1
    assert snap.workspaces_bytes == 10 + 1000 + 2000
    assert snap.largest == [("ws", 10 + 1000 + 2000)]
    assert snap.dep_dirs == 1
    assert snap.dep_bytes == 2000
    assert snap.worktrees_registered == 1
    assert snap.worktrees_terminal == 1
    assert snap.worktrees_unclassified == 0


def test_measure_skips_symlinks_and_bounds_walk(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    _write_file(ws / "real.txt", 100)
    (ws / "link").symlink_to(ws)
    monkeypatch.setattr(wcs, "_git_worktree_paths", lambda repo_dir, timeout: ([], False))
    snap = wcs.measure_workspace_context(ws, db=db, sessions=None)
    assert snap.available is True
    assert snap.workspaces_bytes == 100  # the symlink target is never walked


def test_measure_missing_workspace_dir_is_empty_available(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    snap = wcs.measure_workspace_context(
        tmp_path / "nope", db=db, sessions=None,
    )
    assert snap.available is True
    assert snap.workspaces_bytes == 0


def test_measure_deadline_produces_unavailable_note(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    _write_file(ws / "file.txt", 100)

    class _Clock:
        def __init__(self):
            self.now = 0.0

        def __call__(self):
            self.now += 100.0  # each read is already past any deadline
            return self.now

    monkeypatch.setattr(wcs.time, "monotonic", _Clock())
    snap = wcs.measure_workspace_context(ws, db=db, sessions=None)
    assert snap.available is False
    assert snap.truncated is True
    assert snap.reason is not None


def test_measure_never_raises_on_unexpected_error(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"

    def boom(*a, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(wcs, "_measure", boom)
    snap = wcs.measure_workspace_context(ws, db=db, sessions=None)
    assert snap.available is False
    assert "measurement error" in (snap.reason or "")


# ── (f) deadline: one true wall-clock deadline across Git collection ──────

def test_measure_bounds_git_timeout_by_remaining_deadline_and_marks_unavailable(
    tmp_path, monkeypatch,
):
    """Each git subprocess gets min(per-call cap, remaining deadline); a
    subprocess that consumes the deadline marks the snapshot unavailable.
    """
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    for repo in ("r1", "r2"):
        (ws / "repos" / repo / ".git").mkdir(parents=True)

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
        ws, db=db, sessions=None, deadline_seconds=10.0,
    )
    # First call got the full per-call cap; the second was bounded to the
    # remaining budget (deadline = 1000 + 10 = 1010; after the first call the
    # clock is 1009, so remaining = 1.0 → min(5.0, 1.0) = 1.0).
    assert git_timeouts == [5.0, 1.0]
    assert snap.available is False
    assert snap.truncated is True
    assert "bounded limits" in (snap.reason or "")


def test_measure_expiry_after_last_repo_marks_unavailable(tmp_path, monkeypatch):
    """A git subprocess that finishes at/after the deadline on the LAST
    repository still flips the snapshot to unavailable."""
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    (ws / "repos" / "r1" / ".git").mkdir(parents=True)

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
        ws, db=db, sessions=None, deadline_seconds=10.0,
    )
    assert snap.available is False
    assert snap.truncated is True
    assert "bounded limits" in (snap.reason or "")


# ── (g) cardinality caps → truncated/unavailable, boundary tests ─────────

def test_measure_repo_cap_hit_marks_unavailable(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    repos = ws / "repos"
    for i in range(wcs._MAX_REPOS_PER_WORKSPACE + 1):
        (repos / f"r{i}" / ".git").mkdir(parents=True)
    monkeypatch.setattr(wcs, "_git_worktree_paths", lambda repo_dir, timeout: ([], False))
    snap = wcs.measure_workspace_context(ws, db=db, sessions=None)
    assert snap.available is False
    assert snap.truncated is True
    assert "bounded limits" in (snap.reason or "")


def test_measure_worktree_cap_hit_marks_unavailable(tmp_path, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    ws = tmp_path / "ws"
    (ws / "repos" / "r1" / ".git").mkdir(parents=True)
    many = [str(tmp_path / "wt" / str(i)) for i in range(wcs._MAX_WORKTREES_PER_REPO + 1)]
    runner = _RecordingGitRun(by_repo={
        str(ws / "repos" / "r1"): many,
    })
    monkeypatch.setattr(wcs.subprocess, "run", runner)
    snap = wcs.measure_workspace_context(ws, db=db, sessions=None)
    assert snap.available is False
    assert snap.truncated is True
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


def test_iter_workspaces_cap_sets_truncated(tmp_path):
    ws = tmp_path / "ws"
    for i in range(wcs._MAX_WORKSPACES + 1):
        (ws / f"agent{i}").mkdir(parents=True)
    paths = type("P", (), {"workspaces_dir": ws})()
    dirs, truncated = wcs._iter_workspaces(paths)
    assert truncated is True
    assert len(dirs) == wcs._MAX_WORKSPACES


# ── (h) suffixed TASK-id conservative classification ─────────────────────

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
    (ws / "repos" / "r1" / ".git").mkdir(parents=True)
    unknown_wt = str(tmp_path / "wt-unknown")
    runner = _RecordingGitRun(by_repo={
        str(ws / "repos" / "r1"): [unknown_wt],
    })
    monkeypatch.setattr(wcs.subprocess, "run", runner)
    snap = wcs.measure_workspace_context(ws, db=db, sessions=None)
    assert snap.worktrees_registered == 1
    assert snap.worktrees_unclassified == 1
    assert snap.worktrees_terminal == 0
    assert snap.worktrees_non_terminal == 0


def test_terminal_statuses_parity():
    from runtime.orchestrator import run_step
    assert wcs._TERMINAL_TASK_STATUSES == frozenset(run_step.TERMINAL_STATES)


# ── (i) SessionTracker live sessions ──────────────────────────────────────

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


# ── (j) per-agent durable report thread: no minted token ──────────────────

@pytest.mark.asyncio
async def test_trigger_creates_per_agent_report_thread_without_minted_token(
    tmp_path, test_settings, monkeypatch,
):
    """First trigger creates ONE durable thread per agent (per-agent subject,
    owning agent as participant) and the brief instructs the participant-
    authorized task-bound send path — NO minted invocation token."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    task = db.get_task(task_id)
    subject = wcs.report_thread_subject("dev_agent")
    threads = db.list_threads(limit=50)
    matching = [t for t in threads if t.subject == subject]
    assert len(matching) == 1
    thread_id = matching[0].id
    # Owning agent is a participant (participant-authorized send path).
    assert db.is_thread_participant(thread_id, "dev_agent")
    # Brief carries the thread id + the task-bound send instruction.
    assert f"--thread-id {thread_id}" in task.brief
    assert "happyranch threads send --org" in task.brief
    assert "--task-id" in task.brief and "--session-id" in task.brief
    assert "no invocation token is needed" in task.brief
    # NO minted token anywhere in the brief.
    assert "invocation_token" not in task.brief
    assert "BOOTSTRAP" not in task.brief


def test_cleanup_report_thread_inserts_without_mention_routing_enabled_field(
    tmp_path,
):
    """TASK-6082 founder ruling: the THR-195 workspace-cleanup seam
    (``insert_cleanup_report_thread_and_task`` -> ``_insert_thread_uncommitted``)
    must insert a ThreadRecord that has NO ``mention_routing_enabled`` field
    (TASK-6027 unconditional routing) without AttributeError, and the
    persisted row keeps the inert legacy column at its shipped SQLite
    DEFAULT with every adjacent field at its own position."""
    db = Database(tmp_path / "db.sqlite")
    task = TaskRecord(
        id="T-CLEAN-1",
        brief="cleanup brief",
        team="engineering",
        assigned_agent="dev_agent",
    )
    thread_id = db.insert_cleanup_report_thread_and_task(
        thread_id="THR-CLEAN-1",
        subject="Cleanup report",
        composer="dev_agent",
        opening_body="opening body",
        initial_recipients=[],
        turn_cap=500,
        task=task,
    )
    assert thread_id == "THR-CLEAN-1"
    t = db.get_thread(thread_id)
    assert t.subject == "Cleanup report"
    assert t.turn_cap == 500
    assert t.composed_by == "dev_agent"
    assert t.composed_from_task_id == "T-CLEAN-1"
    assert not hasattr(t, "mention_routing_enabled")
    # The inert legacy column receives its shipped DEFAULT, not a shift.
    row = db._conn.execute(
        "SELECT mention_routing_enabled FROM threads WHERE id='THR-CLEAN-1'"
    ).fetchone()
    assert row["mention_routing_enabled"] == 1
    # The atomic producer also inserted the task and the composer participant.
    assert db.get_task("T-CLEAN-1") is not None
    assert db.is_thread_participant("THR-CLEAN-1", "dev_agent")


@pytest.mark.asyncio
async def test_trigger_reuses_same_thread_on_next_run(tmp_path, test_settings, monkeypatch):
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    state = _FakeDaemonState()
    task1_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    task1 = db.get_task(task1_id)
    thread_id = wcs._find_report_thread(db, "dev_agent").thread_id
    assert thread_id is not None
    assert f"--thread-id {thread_id}" in task1.brief

    task2_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    task2 = db.get_task(task2_id)
    assert f"--thread-id {thread_id}" in task2.brief
    subject = wcs.report_thread_subject("dev_agent")
    matching = [t for t in db.list_threads(limit=50) if t.subject == subject]
    assert len(matching) == 1
    assert matching[0].id == thread_id


@pytest.mark.asyncio
async def test_two_agents_get_distinct_report_threads(tmp_path, test_settings, monkeypatch):
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)
    _write_file(org.root / "workspaces" / "qa_engineer" / "f.txt", 1024)

    state = _FakeDaemonState()
    await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    await wcs.trigger_cleanup(
        org, agent="qa_engineer",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    dev_thread = wcs._find_report_thread(db, "dev_agent").thread_id
    qa_thread = wcs._find_report_thread(db, "qa_engineer").thread_id
    assert dev_thread is not None and qa_thread is not None
    assert dev_thread != qa_thread
    assert db.is_thread_participant(dev_thread, "dev_agent")
    assert not db.is_thread_participant(dev_thread, "qa_engineer")
    assert db.is_thread_participant(qa_thread, "qa_engineer")


@pytest.mark.asyncio
async def test_trigger_fails_closed_when_thread_creation_fails(
    tmp_path, test_settings, monkeypatch,
):
    """TASK-6046 finding 1: a thread-creation failure inside the atomic
    producer rolls back EVERYTHING — no task, no thread residue, no enqueue —
    and is audited as a skipped trigger (fail closed; a later tick retries
    cleanly with zero residue to resolve)."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    def boom(**kw):
        raise RuntimeError("thread create boom")

    monkeypatch.setattr(
        org.db, "insert_cleanup_report_thread_and_task", boom,
    )
    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is None
    assert state.queue.items == []
    assert db.list_tasks_by_brief_prefix(
        wcs._CLEANUP_BRIEF_MARKER, assigned_agent="dev_agent",
    ) == []
    assert db.list_threads(limit=1000) == []
    audits = db.get_audit_logs("workspace-cleanup:skipped")
    assert any(
        r["action"] == "workspace_cleanup_skipped"
        and r["payload"].get("reason") == "task_insert_failed"
        for r in audits
    )


# ── (k) kill switch (enabled-by-default org config flag) ─────────────────

@pytest.mark.asyncio
async def test_kill_switch_default_enabled_triggers(tmp_path, test_settings, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)

    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()

    triggered: list[str] = []

    async def fake_trigger(org, *, agent, enqueue, now_utc=None):
        triggered.append(agent)
        return "TASK-1"

    monkeypatch.setattr(wcs, "trigger_cleanup", fake_trigger)
    monkeypatch.setattr(
        wcs, "decide_cleanup_trigger",
        lambda **kw: wcs.CleanupTriggerDecision(True, None),
    )
    await wcs._tick_org(org, state, now_utc=_daily_0330_utc())
    assert triggered == ["dev_agent", "qa_engineer"]


@pytest.mark.asyncio
async def test_kill_switch_disabled_skips_org(tmp_path, test_settings, monkeypatch):
    """workspace_cleanup.enabled: false in the org config.yaml disables the
    whole capability for the org (existing daemon/org config mechanism)."""
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    cfg_path = org.root / "org" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("workspace_cleanup:\n  enabled: false\n")

    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()

    triggered: list[str] = []

    async def fake_trigger(org, *, agent, enqueue, now_utc=None):
        triggered.append(agent)
        return "TASK-1"

    monkeypatch.setattr(wcs, "trigger_cleanup", fake_trigger)
    monkeypatch.setattr(
        wcs, "decide_cleanup_trigger",
        lambda **kw: wcs.CleanupTriggerDecision(True, None),
    )
    await wcs._tick_org(org, state, now_utc=_daily_0330_utc())
    assert triggered == []


def test_org_config_kill_switch_parse():
    from runtime.orchestrator.org_config import OrgConfig, OrgConfigError

    assert OrgConfig().workspace_cleanup_enabled is True
    assert OrgConfig.load_from_text("").workspace_cleanup_enabled is True
    assert OrgConfig.load_from_text(
        "workspace_cleanup:\n  enabled: false\n"
    ).workspace_cleanup_enabled is False
    assert OrgConfig.load_from_text(
        "workspace_cleanup:\n  enabled: true\n"
    ).workspace_cleanup_enabled is True
    with pytest.raises(OrgConfigError):
        OrgConfig.load_from_text("workspace_cleanup:\n  enabled: nope\n")
    with pytest.raises(OrgConfigError):
        OrgConfig.load_from_text("workspace_cleanup: 42\n")


@pytest.mark.asyncio
async def test_tick_org_shared_loader_config_failure_escapes_before_scheduling(
    tmp_path, test_settings,
):
    """The scheduler consumer shares the strict org-config loader: malformed
    YAML raises before any due/trigger decision (loop-level isolation lives in
    the scheduler loop, not in ``_tick_org``), so no cleanup task is queued."""
    from runtime.orchestrator.org_config import OrgConfigError

    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    cfg_path = org.root / "org" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("workspace_cleanup: {\n")
    state = _FakeDaemonState()
    state.orgs = {"test": org}

    with pytest.raises(OrgConfigError):
        await wcs._tick_org(org, state, now_utc=_daily_0330_utc())
    assert state.queue.items == []


# ── (l) no Schedule / ordinary-session effect ────────────────────────────

def test_orchestrator_has_no_cleanup_seam():
    """The superseded prompt-seam is gone: the orchestrator no longer imports
    or calls any workspace-cleanup context builder, so ordinary and unrelated
    Schedule-spawned sessions are byte-identical BY CONSTRUCTION."""
    import runtime.orchestrator.orchestrator as orch_module
    source = inspect.getsource(orch_module)
    assert "workspace_context" not in source
    assert "maybe_build_cleanup_context_note" not in source


def test_no_schedule_store_reverse_lookup_survives():
    """find_by_spawned_task_id (the rejected Schedule discriminator) is gone."""
    import runtime.infrastructure.schedule_store as store_module
    assert not hasattr(store_module, "find_by_spawned_task_id")
    source = inspect.getsource(store_module)
    assert "find_by_spawned_task_id" not in source


# ── (m) full _run_agent shipping seam: no advisory note in ANY session ───

_TASK_CONTEXT_CONTRACT_IDS = (
    "start-task",
    "jobs",
    "make-worktree",
    "thread",
    "dream",
    "todos",
    "create-skill",
    "workspace-cleanup",
)


def _setup_protocol_skills(settings: Settings) -> None:
    for sid in _TASK_CONTEXT_CONTRACT_IDS:
        src = settings.get_bundled_skills_dir() / sid
        src.mkdir(parents=True, exist_ok=True)
        (src / "SKILL.md").write_text(f"# {sid}\n\nSkill body for {sid}.\n")


def _setup_agent_workspace(runtime, agent: str, provider: str) -> None:
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    ws = runtime.workspaces_dir / agent
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "task_history.md").write_text(f"# Task History: {agent}\n\n")
    (ws / "AGENTS.md").write_text(f"# Agent: {agent}\n")
    (ws / "CLAUDE.md").symlink_to("AGENTS.md")
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


# ── (n) daemon loop: trigger/non-trigger through the async loop ──────────

class _FakeMetricsRegistry:
    def record_loop_tick(self, *args, **kwargs) -> None:
        pass


@pytest.mark.asyncio
async def test_loop_ticks_and_triggers_when_due(tmp_path, test_settings, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)

    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()

    due = _daily_0330_utc()
    triggered: list[str] = []

    async def fake_trigger(org, *, agent, enqueue, now_utc=None):
        triggered.append(agent)
        return "TASK-1"

    monkeypatch.setattr(wcs, "trigger_cleanup", fake_trigger)
    monkeypatch.setattr(
        wcs, "decide_cleanup_trigger",
        lambda **kw: wcs.CleanupTriggerDecision(True, None),
    )
    await wcs._tick_org(org, state, now_utc=due)
    assert triggered == ["dev_agent", "qa_engineer"]


@pytest.mark.asyncio
async def test_loop_ticks_and_skips_when_not_due(tmp_path, test_settings, monkeypatch):
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)

    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()

    triggered: list[str] = []

    async def fake_trigger(org, *, agent, enqueue, now_utc=None):
        triggered.append(agent)
        return "TASK-1"

    monkeypatch.setattr(wcs, "trigger_cleanup", fake_trigger)
    monkeypatch.setattr(
        wcs, "decide_cleanup_trigger",
        lambda **kw: wcs.CleanupTriggerDecision(False, "not_due"),
    )
    await wcs._tick_org(org, state, now_utc=_daily_0330_utc())
    assert triggered == []


@pytest.mark.asyncio
async def test_tick_org_below_threshold_audits_once_at_daily_boundary(
    tmp_path, test_settings, monkeypatch,
):
    """An unserviced below-threshold agent is observed once at the daily
    decision boundary, not once per minute for the rest of the day."""
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    cfg_path = org.root / "org" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("timezone: UTC\n")
    state = _FakeDaemonState()

    monkeypatch.setattr(
        wcs, "measure_workspace_context",
        lambda *a, **kw: wcs.WorkspaceContextSnapshot(
            available=True, workspaces_bytes=0,
        ),
    )
    occurrence = _daily_0330_utc()
    for minute in range(3):
        await wcs._tick_org(
            org, state, now_utc=occurrence + timedelta(minutes=minute),
        )

    audits = db.get_audit_logs("workspace-cleanup:skipped")
    below_threshold = [
        row for row in audits
        if row["action"] == "workspace_cleanup_skipped"
        and row["agent"] == "dev_agent"
        and row["payload"].get("reason") == "workspace_below_threshold"
    ]
    assert len(below_threshold) == 1


@pytest.mark.asyncio
async def test_tick_org_observes_below_threshold_once_per_daily_boundary(
    tmp_path, test_settings, monkeypatch,
):
    """With the seven-day cooldown removed, an unserviced below-threshold agent
    is observed once per daily boundary (and once per boundary only)."""
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    cfg_path = org.root / "org" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("timezone: UTC\n")
    state = _FakeDaemonState()

    monkeypatch.setattr(
        wcs, "measure_workspace_context",
        lambda *a, **kw: wcs.WorkspaceContextSnapshot(
            available=True, workspaces_bytes=0,
        ),
    )
    occurrence = _daily_0330_utc()
    _insert_cleanup_task(
        db,
        task_id="TASK-100",
        agent="dev_agent",
        created_at=occurrence - timedelta(hours=15),
        status=TaskStatus.COMPLETED,
    )

    await wcs._tick_org(org, state, now_utc=occurrence)
    await wcs._tick_org(org, state, now_utc=occurrence + timedelta(minutes=1))
    later_boundary = occurrence + timedelta(days=1)
    await wcs._tick_org(org, state, now_utc=later_boundary)
    await wcs._tick_org(
        org, state, now_utc=later_boundary + timedelta(minutes=1),
    )

    audits = db.get_audit_logs("workspace-cleanup:skipped")
    below_threshold = [
        row for row in audits
        if row["action"] == "workspace_cleanup_skipped"
        and row["agent"] == "dev_agent"
        and row["payload"].get("reason") == "workspace_below_threshold"
    ]
    assert len(below_threshold) == 2


@pytest.mark.asyncio
async def test_shipping_loop_reaches_occurrence_once_across_phase_and_processing_drift(
    tmp_path, test_settings, monkeypatch,
):
    """The shipping loop cannot skip 03:30 when scan work plus the full sleep
    advances the next scan from just before the boundary to after 03:31."""
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    cfg_path = org.root / "org" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("timezone: UTC\n")
    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()

    monkeypatch.setattr(
        wcs, "measure_workspace_context",
        lambda *a, **kw: wcs.WorkspaceContextSnapshot(
            available=True,
            workspaces_bytes=0,
            measured_at="2026-08-30T03:30:00+00:00",
        ),
    )
    occurrence = _daily_0330_utc()
    # Yesterday's window is already serviced terminal for both agents, so the
    # first-scan catch-up is a no-op and only the real crossing can act.
    for i, agent in enumerate(("dev_agent", "qa_engineer")):
        _insert_cleanup_task(
            db, task_id=f"TASK-{600 + i}", agent=agent,
            created_at=occurrence - timedelta(days=1, minutes=-5),
            status=TaskStatus.COMPLETED,
        )
    scan_times = iter([
        occurrence - timedelta(microseconds=500_000),  # first scan (catch-up)
        occurrence + timedelta(minutes=1, microseconds=500_000),  # crossing
        occurrence + timedelta(minutes=2, seconds=2),
    ])

    class _DriftingDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(scan_times)

    sleeps = 0

    async def fake_sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(wcs, "datetime", _DriftingDateTime)
    monkeypatch.setattr(wcs.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await wcs.workspace_cleanup_scheduler_loop(
            state, interval_seconds=60, warm_up_seconds=0,
        )

    below_threshold = [
        row for row in db.get_audit_logs("workspace-cleanup:skipped")
        if row["action"] == "workspace_cleanup_skipped"
        and row["agent"] == "dev_agent"
        and row["payload"].get("reason") == "workspace_below_threshold"
    ]
    assert len(below_threshold) == 1


@pytest.mark.asyncio
async def test_shipping_loop_catches_up_current_window_once_on_startup(
    tmp_path, test_settings, monkeypatch,
):
    """A daemon starting after 03:30 evaluates the current daily occurrence
    once, without replaying it on later loop ticks."""
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    cfg_path = org.root / "org" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("timezone: UTC\n")
    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()

    monkeypatch.setattr(
        wcs, "measure_workspace_context",
        lambda *a, **kw: wcs.WorkspaceContextSnapshot(
            available=True, workspaces_bytes=0,
            measured_at="2026-08-30T07:30:00+00:00",
        ),
    )
    occurrence = _daily_0330_utc()
    scan_times = iter([
        occurrence + timedelta(hours=4),
        occurrence + timedelta(hours=4, seconds=1),
        occurrence + timedelta(hours=4, minutes=1),
    ])

    class _StartupDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return next(scan_times)

    sleeps = 0

    async def fake_sleep(_seconds):
        nonlocal sleeps
        sleeps += 1
        if sleeps >= 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(wcs, "datetime", _StartupDateTime)
    monkeypatch.setattr(wcs.asyncio, "sleep", fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await wcs.workspace_cleanup_scheduler_loop(
            state, interval_seconds=60, warm_up_seconds=0,
        )

    below_threshold = [
        row for row in db.get_audit_logs("workspace-cleanup:skipped")
        if row["action"] == "workspace_cleanup_skipped"
        and row["agent"] == "dev_agent"
        and row["payload"].get("reason") == "workspace_below_threshold"
    ]
    assert len(below_threshold) == 1


@pytest.mark.asyncio
async def test_loop_waits_out_boot_warm_up_before_any_tick(
    tmp_path, test_settings, monkeypatch,
):
    """The daemon loop honours a boot warm-up grace: no trigger scan runs
    before ``warm_up_seconds`` elapse (short-lived lifespan contexts — e.g.
    the dashboard lifespan test — never see trigger side effects)."""
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)

    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()

    ticks: list[str] = []

    async def fake_tick(org, state, now_utc, previous_scan_utc=None, first_scan=False):
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


# ── (o) TASK-6043 finding 1: history lookup failure/indeterminacy fails closed ──

def test_trigger_decision_suppresses_on_history_lookup_error(tmp_path, monkeypatch):
    """A task-history lookup error must NEVER read as 'no prior run → trigger'.
    The decision seam represents the error as indeterminate and suppresses."""
    db = Database(tmp_path / "db.sqlite")

    def boom(*a, **kw):
        raise RuntimeError("db read failure")

    monkeypatch.setattr(db, "summarize_workspace_cleanup_marker_history", boom)
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent",
        now_utc=_daily_0330_utc(), tz=timezone.utc,
    )
    assert decision.should_trigger is False
    assert decision.reason == "history_indeterminate"


@pytest.mark.asyncio
async def test_tick_org_audits_boundary_history_failure_once_without_task(
    tmp_path, test_settings, monkeypatch,
):
    """The shipping tick records one fail-closed boundary decision while
    adjacent scans remain quiet and no cleanup task is produced."""
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    cfg_path = org.root / "org" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("timezone: UTC\n")
    state = _FakeDaemonState()

    def boom(*a, **kw):
        raise RuntimeError("db read failure")

    monkeypatch.setattr(db, "summarize_workspace_cleanup_marker_history", boom)
    occurrence = _daily_0330_utc()
    await wcs._tick_org(
        org, state,
        now_utc=occurrence - timedelta(seconds=1),
        previous_scan_utc=occurrence - timedelta(minutes=1),
    )
    await wcs._tick_org(
        org, state,
        now_utc=occurrence,
        previous_scan_utc=occurrence - timedelta(seconds=1),
    )
    await wcs._tick_org(
        org, state,
        now_utc=occurrence + timedelta(minutes=1),
        previous_scan_utc=occurrence,
    )

    assert state.queue.items == []
    assert db.list_tasks() == []
    history_skips = [
        row for row in db.get_audit_logs("workspace-cleanup:skipped")
        if row["action"] == "workspace_cleanup_skipped"
        and row["agent"] == "dev_agent"
        and row["payload"].get("reason") == "history_indeterminate"
    ]
    assert len(history_skips) == 1


@pytest.mark.asyncio
async def test_trigger_audits_skip_on_history_indeterminate(
    tmp_path, test_settings, monkeypatch,
):
    """The public trigger seam fails closed on an indeterminate history: no
    task, no enqueue, explicit audit — even when the workspace is huge."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    def boom(*a, **kw):
        raise RuntimeError("db read failure")

    monkeypatch.setattr(db, "summarize_workspace_cleanup_marker_history", boom)
    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is None
    assert state.queue.items == []
    audits = db.get_audit_logs("workspace-cleanup:skipped")
    assert any(
        r["action"] == "workspace_cleanup_skipped"
        and r["payload"].get("reason") == "history_indeterminate"
        for r in audits
    )


# ── (p) TASK-6043 finding 2: authoritative marker filter (no bounded-scan exhaustion) ──

def test_cleanup_task_history_orders_newest_first(tmp_path):
    """The complete marker-history summary returns the newest marker instant
    (UTC), an exact count, and the any-unfinished flag — the inputs of daily
    dedup and the exact run ordinal."""
    db = Database(tmp_path / "db.sqlite")
    now = datetime.now(timezone.utc)
    for i, days_ago in enumerate((60, 30, 7)):
        _insert_cleanup_task(
            db, task_id=f"TASK-{100 + i}", agent="dev_agent",
            created_at=now - timedelta(days=days_ago),
            status=TaskStatus.COMPLETED,
        )
    history = wcs._cleanup_task_history(db, "dev_agent")
    assert history.indeterminate is False
    assert history.count == 3
    assert history.has_unfinished is False
    assert history.newest_utc == now - timedelta(days=7)  # newest (7 days ago)


def test_cleanup_history_finds_marker_row_beyond_former_scan_bound(tmp_path):
    """An old unfinished cleanup row buried under >1000 newer ordinary tasks is
    still visible to the complete reader — the former bounded scan (1000 rows)
    would have hidden it and let the daemon double-trigger."""
    db = Database(tmp_path / "db.sqlite")
    now = datetime.now(timezone.utc)
    # Non-terminal cleanup run 8 days ago (its age is irrelevant: non-terminal
    # suppression applies regardless of age).
    _insert_cleanup_task(
        db, task_id="TASK-100", agent="dev_agent",
        created_at=now - timedelta(days=8), status=TaskStatus.IN_PROGRESS,
    )
    # 1100 NEWER ordinary tasks (no marker) bury it beyond the old 1000-row
    # newest-first scan bound.
    for i in range(1100):
        db.insert_task(TaskRecord(
            id=f"TASK-{2000 + i}",
            brief=f"ordinary work {i}",
            team="engineering",
            assigned_agent="dev_agent",
            created_at=now - timedelta(days=7) + timedelta(
                seconds=i,
            ),
        ))
    history = wcs._cleanup_task_history(db, "dev_agent")
    assert history.indeterminate is False
    assert history.count == 1
    assert history.has_unfinished is True
    assert history.newest_utc == now - timedelta(days=8)

    # The decision seam honors the found non-terminal row: no double trigger.
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent",
        now_utc=_daily_0330_utc(), tz=timezone.utc,
    )
    assert decision.should_trigger is False
    assert decision.reason == "prior_run_in_flight"


def test_cleanup_history_is_per_agent_and_prefix_exact(tmp_path):
    """The marker filter is per agent and matches the daemon marker PREFIX
    exactly (brief.startswith semantics — identical to the pre-change
    in-memory check): a brief that merely CONTAINS the marker mid-text is not
    a daemon-marked cleanup run, while any brief starting with the marker
    is."""
    db = Database(tmp_path / "db.sqlite")
    now = datetime.now(timezone.utc)
    _insert_cleanup_task(
        db, task_id="TASK-100", agent="dev_agent",
        created_at=now - timedelta(days=1), status=TaskStatus.COMPLETED,
    )
    _insert_cleanup_task(
        db, task_id="TASK-101", agent="qa_engineer",
        created_at=now - timedelta(days=1), status=TaskStatus.COMPLETED,
    )
    # Contains the marker mid-text but does not START with it → not matched.
    db.insert_task(TaskRecord(
        id="TASK-102",
        brief="user-written: " + wcs._CLEANUP_BRIEF_MARKER + " (not a daemon run)",
        team="engineering",
        assigned_agent="dev_agent",
        created_at=now,
    ))
    # Starts with the marker (suffixed content) → matched, exactly like the
    # former brief.startswith(marker) check.
    db.insert_task(TaskRecord(
        id="TASK-103",
        brief=wcs._CLEANUP_BRIEF_MARKER + " (suffixed daemon run)",
        team="engineering",
        assigned_agent="dev_agent",
        created_at=now,
    ))
    dev = wcs._cleanup_task_history(db, "dev_agent")
    assert dev.count == 2
    assert dev.newest_utc == now  # newest by UTC instant
    qa = wcs._cleanup_task_history(db, "qa_engineer")
    assert qa.count == 1


# ── (q) TASK-6043 finding 3: atomic task-producer seam ───────────────────

@pytest.mark.asyncio
async def test_trigger_allocates_task_id_after_awaited_measurement(
    tmp_path, test_settings, monkeypatch,
):
    """The task id is allocated only AFTER the awaited measurement. A foreign
    producer inserting a task DURING the measurement cannot claim the id the
    cleanup trigger will use, and the report thread is linked only to the
    real cleanup task — never a falsely linked thread."""
    import threading

    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    entered = threading.Event()
    release = threading.Event()

    def gated_measure(*a, **kw):
        entered.set()
        assert release.wait(10)
        return wcs.WorkspaceContextSnapshot(
            available=True,
            workspaces_bytes=2 ** 30,
            workspaces_count=1,
            largest=[("dev_agent", 2 ** 30)],
        )

    monkeypatch.setattr(wcs, "measure_workspace_context", gated_measure)

    state = _FakeDaemonState()
    trigger_task = asyncio.create_task(
        wcs.trigger_cleanup(
            org, agent="dev_agent",
            enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
        )
    )

    # Wait until the awaited measurement is in flight, then have a foreign
    # producer allocate+insert the next task id — the exact interleaving that
    # used to race the pre-selected id (TASK-6043 finding 3).
    while not entered.is_set():
        await asyncio.sleep(0.001)
    foreign_id = org.db.next_task_id()
    org.db.insert_task(TaskRecord(
        id=foreign_id, brief="foreign ordinary work",
        team="engineering", assigned_agent="dev_agent",
    ))
    release.set()

    task_id = await trigger_task
    assert task_id is not None
    # The cleanup id was allocated AFTER the foreign insert — never the same,
    # never a collision.
    assert task_id != foreign_id
    assert int(task_id.split("-")[-1]) > int(foreign_id.split("-")[-1])

    # The inserted cleanup task is a clean root: no parent, no thread dispatch.
    task = db.get_task(task_id)
    assert task.parent_task_id is None
    assert task.dispatched_from_thread_id is None

    # The report thread is linked ONLY to the real cleanup task.
    subject = wcs.report_thread_subject("dev_agent")
    matching = [t for t in db.list_threads(limit=50) if t.subject == subject]
    assert len(matching) == 1
    assert matching[0].composed_from_task_id == task_id


@pytest.mark.asyncio
async def test_trigger_insert_failure_leaves_zero_residue_then_retry_succeeds(
    tmp_path, test_settings, monkeypatch,
):
    """TASK-6046 finding 1 probe: an insertion failure at the atomic producer
    leaves ZERO durable residue (no thread row, participant, message, turn,
    or thread/task audit rows; no task; no enqueue) and a later retry
    succeeds exactly once — one task, one thread, one enqueue."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    real_producer = org.db.insert_cleanup_report_thread_and_task
    calls = {"n": 0}

    def flaky_producer(**kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("simulated mid-transaction producer failure")
        return real_producer(**kw)

    monkeypatch.setattr(
        org.db, "insert_cleanup_report_thread_and_task", flaky_producer,
    )

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is None
    assert state.queue.items == []

    # ZERO residue across every affected durable table/audit.
    assert db.list_threads(limit=1000) == []
    assert db._conn.execute(
        "SELECT COUNT(*) FROM thread_participants"
    ).fetchone()[0] == 0
    assert db._conn.execute(
        "SELECT COUNT(*) FROM thread_messages"
    ).fetchone()[0] == 0
    assert db._conn.execute(
        "SELECT COUNT(*) FROM audit_log WHERE action IN "
        "('thread_started', 'thread_message_sent')"
    ).fetchone()[0] == 0
    assert db.list_tasks_by_brief_prefix(
        wcs._CLEANUP_BRIEF_MARKER, assigned_agent="dev_agent",
    ) == []
    audits = db.get_audit_logs("workspace-cleanup:skipped")
    assert any(
        r["action"] == "workspace_cleanup_skipped"
        and r["payload"].get("reason") == "task_insert_failed"
        for r in audits
    )

    # A later retry succeeds exactly once: one task, one thread, one enqueue.
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is not None
    assert state.queue.items == [(org.slug, task_id)]
    cleanup = db.list_tasks_by_brief_prefix(
        wcs._CLEANUP_BRIEF_MARKER, assigned_agent="dev_agent",
    )
    assert [t.id for t in cleanup] == [task_id]
    subject = wcs.report_thread_subject("dev_agent")
    matching = [t for t in db.list_threads(limit=1000) if t.subject == subject]
    assert len(matching) == 1
    assert matching[0].composed_from_task_id == task_id
    assert db.is_thread_participant(matching[0].id, "dev_agent")
    opening = db.get_thread_message_by_seq(matching[0].id, 1)
    assert opening is not None
    assert opening.body_markdown.startswith(wcs._REPORT_THREAD_OPENING_PREFIX)
    assert db.get_thread(matching[0].id).turns_used == 1


@pytest.mark.asyncio
async def test_trigger_insert_failure_on_existing_thread_touches_no_thread_rows(
    tmp_path, test_settings, monkeypatch,
):
    """When the durable thread already exists, an insert failure happens on
    the task-only path — there is no thread work that could leave residue:
    the existing thread (row/participant/message/turns) is untouched."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    state = _FakeDaemonState()
    first_task = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    thread_id = wcs._find_report_thread(db, "dev_agent").thread_id
    assert thread_id is not None

    def flaky_insert(task):
        raise Exception("simulated concurrent id collision")

    monkeypatch.setattr(org.db, "insert_task", flaky_insert)
    second = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert second is None
    assert state.queue.items == [(org.slug, first_task)]
    # The existing thread is untouched: exactly one thread, one participant,
    # one opening message, one turn, no extra audits.
    subject = wcs.report_thread_subject("dev_agent")
    matching = [t for t in db.list_threads(limit=1000) if t.subject == subject]
    assert len(matching) == 1
    assert matching[0].id == thread_id
    assert db.get_thread(thread_id).turns_used == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM thread_messages WHERE thread_id = ?",
        (thread_id,),
    ).fetchone()[0] == 1
    audits = db.get_audit_logs("workspace-cleanup:skipped")
    assert any(
        r["action"] == "workspace_cleanup_skipped"
        and r["payload"].get("reason") == "task_insert_failed"
        for r in audits
    )


@pytest.mark.asyncio
async def test_trigger_fails_closed_on_intermittent_identity_read_then_recovers(
    tmp_path, test_settings, monkeypatch,
):
    """TASK-6046 finding 2 probe: a transient report-thread IDENTITY read
    failure with an existing valid thread must NOT create a duplicate. The
    history read succeeds but the subsequent identity read fails once: the
    trigger fails closed (no task, no enqueue, audited reason), the existing
    open thread is untouched, and the next attempt recovers — one open
    thread, no duplicate, the task references the existing thread."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    # A valid existing report thread behind a terminal cleanup task — the
    # state after an earlier successful trigger + completed run.
    prior_task_id = "TASK-900"
    _insert_cleanup_task(
        db, task_id=prior_task_id, agent="dev_agent",
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
        status=TaskStatus.COMPLETED,
    )
    subject = wcs.report_thread_subject("dev_agent")
    existing_tid = _insert_thread_via_shared_helper(
        org, agent="dev_agent", subject=subject,
        body_text=wcs._REPORT_THREAD_OPENING_PREFIX + " daemon opening",
        task_id=prior_task_id,
    )
    assert wcs._find_report_thread(db, "dev_agent").state == "found"

    # Intermittent identity read: history succeeds, provenance read fails
    # exactly once.
    real_identity_read = org.db.list_threads_by_composed_from_task_id
    calls = {"n": 0}

    def flaky_identity_read(task_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Exception("simulated transient identity-read failure")
        return real_identity_read(task_id)

    monkeypatch.setattr(
        org.db, "list_threads_by_composed_from_task_id", flaky_identity_read,
    )

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is None
    assert state.queue.items == []
    audits = db.get_audit_logs("workspace-cleanup:skipped")
    assert any(
        r["action"] == "workspace_cleanup_skipped"
        and r["payload"].get("reason") == "report_thread_indeterminate"
        and r["payload"].get("detail") == "provenance_lookup_failed"
        for r in audits
    )
    # Exactly ONE open report thread — no duplicate was created.
    matching = [t for t in db.list_threads(limit=1000) if t.subject == subject]
    assert len(matching) == 1
    assert matching[0].id == existing_tid
    assert matching[0].status.value == "open"

    # Successful recovery: the next trigger resolves the existing thread and
    # creates the task referencing it — still one open thread.
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is not None
    assert state.queue.items == [(org.slug, task_id)]
    task = db.get_task(task_id)
    assert f"--thread-id {existing_tid}" in task.brief
    matching = [t for t in db.list_threads(limit=1000) if t.subject == subject]
    assert len(matching) == 1
    assert matching[0].id == existing_tid
    assert wcs._find_report_thread(db, "dev_agent").thread_id == existing_tid


# ── (r) TASK-6043 finding 4: authoritative durable thread identity ───────

def _insert_thread_via_shared_helper(
    org, *, agent, subject, body_text, task_id,
) -> str:
    """Create a thread through the exact shared compose helper the daemon
    uses (participant/turn/audit semantics), with arbitrary provenance — the
    shape a user-created subject collision would take."""
    from runtime.daemon.routes.threads import _create_agent_thread_locked
    from runtime.orchestrator.org_config import (
        OrgConfig,
        resolve_org_setting_threads,
    )

    turn_cap = resolve_org_setting_threads(
        org.db, code_default=OrgConfig(),
    )["default_turn_cap"]
    thread_id, _seq, _tokens, _addr = _create_agent_thread_locked(
        org,
        composer=agent,
        subject=subject,
        body_text=body_text,
        recipients=["@founder"],
        turn_cap=turn_cap,
        composed_from_task_id=task_id,
    )
    return thread_id


@pytest.mark.asyncio
async def test_find_report_thread_rejects_user_subject_collisions(
    tmp_path, test_settings, monkeypatch,
):
    """A user-created thread with the fixed subject is NEVER selected as the
    durable report thread — whether its provenance is an ordinary task or
    even a cleanup task (the daemon's opening message is the tiebreaker)."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)
    subject = wcs.report_thread_subject("dev_agent")

    # Collision A: ordinary-task provenance, same subject.
    ordinary_task_id = "TASK-900"
    db.insert_task(TaskRecord(
        id=ordinary_task_id, brief="ordinary work",
        team="engineering", assigned_agent="dev_agent",
    ))
    user_tid_a = _insert_thread_via_shared_helper(
        org, agent="dev_agent", subject=subject,
        body_text="a user's own thread, not the daemon's",
        task_id=ordinary_task_id,
    )

    # Collision B: cleanup-task provenance but a non-daemon opening message.
    _insert_cleanup_task(
        db, task_id="TASK-901", agent="dev_agent",
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
        status=TaskStatus.COMPLETED,
    )
    user_tid_b = _insert_thread_via_shared_helper(
        org, agent="dev_agent", subject=subject,
        body_text="user content that happens to match the subject",
        task_id="TASK-901",
    )

    # Neither collision resolves to the durable report thread.
    assert wcs._find_report_thread(db, "dev_agent").state == "absent"

    # The daemon's own trigger creates the real thread and resolves to it.
    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    found = wcs._find_report_thread(db, "dev_agent")
    assert found.state == "found"
    assert found.thread_id not in (user_tid_a, user_tid_b)
    assert db.get_thread(found.thread_id).composed_from_task_id == task_id


@pytest.mark.asyncio
async def test_find_report_thread_requires_participant_membership(
    tmp_path, test_settings, monkeypatch,
):
    """A thread the owning agent is not a participant of can never be
    selected — the participant-authorized send path would otherwise fail."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    daemon_tid = wcs._find_report_thread(db, "dev_agent").thread_id
    assert daemon_tid is not None
    assert db.is_thread_participant(daemon_tid, "dev_agent")

    # Remove the owning agent from the thread → it is no longer a valid
    # identity and must NOT be selected.
    db.remove_thread_participant(daemon_tid, "dev_agent")
    assert wcs._find_report_thread(db, "dev_agent").state == "absent"


@pytest.mark.asyncio
async def test_find_report_thread_located_beyond_open_presentation_limit(
    tmp_path, test_settings, monkeypatch,
):
    """The durable thread is found even when buried under >500 newer open
    threads — the old 500-open-row presentation scan would have missed it and
    duplicated it on the next trigger."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    state = _FakeDaemonState()
    first_task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    daemon_tid = wcs._find_report_thread(db, "dev_agent").thread_id
    assert daemon_tid is not None

    # 549 newer unrelated open threads push the daemon thread beyond any
    # 500-row open presentation page.
    for i in range(549):
        db.insert_thread(ThreadRecord(
            id=db.next_thread_id(),
            subject=f"unrelated thread {i}",
            turn_cap=500,
            composed_by="someone_else",
        ))

    assert wcs._find_report_thread(db, "dev_agent").thread_id == daemon_tid

    # The next trigger reuses the SAME thread — no duplicate is created.
    second_task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    subject = wcs.report_thread_subject("dev_agent")
    matching = [t for t in db.list_threads(limit=1000) if t.subject == subject]
    assert len(matching) == 1
    assert matching[0].id == daemon_tid
    assert first_task_id != second_task_id


@pytest.mark.asyncio
async def test_find_report_thread_does_not_reuse_closed_thread(
    tmp_path, test_settings, monkeypatch,
):
    """A closed (archived) report thread is never reused: the daemon creates
    a fresh open thread on the next trigger."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)

    state = _FakeDaemonState()
    await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    daemon_tid = wcs._find_report_thread(db, "dev_agent").thread_id
    assert daemon_tid is not None
    db.archive_thread_and_reset_sessions(
        daemon_tid, summary="rollup complete",
        audit_scope_id="workspace-cleanup:test", audit_agent="dev_agent",
    )
    assert wcs._find_report_thread(db, "dev_agent").state == "absent"

    # A new trigger creates a fresh open thread (the closed one stays).
    await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    new_tid = wcs._find_report_thread(db, "dev_agent").thread_id
    assert new_tid is not None
    assert new_tid != daemon_tid
    subject = wcs.report_thread_subject("dev_agent")
    matching = [t for t in db.list_threads(limit=100) if t.subject == subject]
    assert len(matching) == 2
    assert db.get_thread(new_tid).status.value == "open"


# ── (s) TASK-6043 finding 5: all agents handled beyond the 64 cap ────────

@pytest.mark.asyncio
async def test_tick_processes_all_agents_beyond_cap(
    tmp_path, test_settings, monkeypatch,
):
    """An org with more than _MAX_WORKSPACES agents: the tick pages through
    every registered workspace — no agent is alphabetically starved."""
    db = Database(tmp_path / "db.sqlite")
    org_root = tmp_path / "orgs" / "test"
    org_root.mkdir(parents=True, exist_ok=True)
    org = _make_org(org_root, db, test_settings)
    org.root = org_root

    agents = [f"agent{i:03d}" for i in range(wcs._MAX_WORKSPACES + 6)]
    teams = TeamsRegistry.load(org_root)
    teams._teams["engineering"] = type(
        "TM", (), {"name": "engineering_manager", "team": "engineering",
                   "workers": tuple(agents)}
    )()
    org.teams = teams
    for agent in agents:
        (org_root / "workspaces" / agent).mkdir(parents=True)

    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()

    triggered: list[str] = []

    async def fake_trigger(org, *, agent, enqueue, now_utc=None):
        triggered.append(agent)
        return "TASK-1"

    monkeypatch.setattr(wcs, "trigger_cleanup", fake_trigger)
    monkeypatch.setattr(
        wcs, "decide_cleanup_trigger",
        lambda **kw: wcs.CleanupTriggerDecision(True, None),
    )
    await wcs._tick_org(org, state, now_utc=_daily_0330_utc())
    assert len(triggered) == len(agents)
    assert set(triggered) == set(agents)


def test_iter_workspaces_pages_across_batches(tmp_path):
    """The workspace iterator pages deterministically: batch N+1 starts where
    batch N ended, and only the last batch reports no truncation."""
    ws = tmp_path / "ws"
    total = wcs._MAX_WORKSPACES * 2 + 3
    for i in range(total):
        (ws / f"agent{i:04d}").mkdir(parents=True)
    paths = type("P", (), {"workspaces_dir": ws})()
    seen: list[str] = []
    offset = 0
    while True:
        batch, truncated = wcs._iter_workspaces(paths, offset=offset)
        if not batch:
            break
        seen.extend(p.name for p in batch)
        if not truncated:
            break
        offset += wcs._MAX_WORKSPACES
    assert len(seen) == total
    assert seen == sorted(f"agent{i:04d}" for i in range(total))


# ══════════════════════════════════════════════════════════════════════════
# TASK-8479 daily-cadence finite cases C1-C11, C13 (accepted revision 3)
# ══════════════════════════════════════════════════════════════════════════

def _install_clock(monkeypatch, times, holder):
    iterator = iter(times)

    class _Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = next(iterator)
            holder["now"] = value
            return value

    monkeypatch.setattr(wcs, "datetime", _Clock)


def _install_sleep_cancel(monkeypatch, after):
    counter = {"n": 0}

    async def fake_sleep(_seconds):
        counter["n"] += 1
        if counter["n"] >= after:
            raise asyncio.CancelledError

    monkeypatch.setattr(wcs.asyncio, "sleep", fake_sleep)


def _install_stamped_task_record(monkeypatch, holder):
    real = wcs.TaskRecord

    def factory(**kwargs):
        kwargs.setdefault("created_at", holder["now"])
        return real(**kwargs)

    monkeypatch.setattr(wcs, "TaskRecord", factory)


def _install_measurement(
    monkeypatch, calls, *, available=True, workspaces_bytes=2048, reason="",
):
    def _measure(*args, **kwargs):
        calls.append(1)
        return wcs.WorkspaceContextSnapshot(
            measured_at="2026-09-20T03:31:00+00:00",
            available=available, workspaces_bytes=workspaces_bytes,
            reason=reason, truncated=False,
        )

    monkeypatch.setattr(wcs, "measure_workspace_context", _measure)


def _seeded_org(tmp_path, test_settings):
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    cfg_path = org.root / "org" / "config.yaml"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("timezone: UTC\n")
    state = _FakeDaemonState()
    state.orgs = {"test": org}
    state.metrics_registry = _FakeMetricsRegistry()
    return db, org, state


# ── C1: all weekdays, exact crossing vs no crossing ───────────────────────

@pytest.mark.parametrize("weekday", range(7))
def test_c1_daily_crossing_all_weekdays_exact_and_boundaries(tmp_path, weekday):
    db = Database(tmp_path / "db.sqlite")
    base = datetime(2026, 9, 14, 3, 30, tzinfo=timezone.utc)  # Monday
    occ = base + timedelta(days=weekday)
    assert occ.weekday() == weekday

    before = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ - timedelta(seconds=1),
        previous_scan_utc=occ - timedelta(seconds=2), tz=timezone.utc,
    )
    assert before.should_trigger is False
    assert before.reason == "not_due"

    exact = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ,
        previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
    )
    assert exact.should_trigger is True

    after = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(seconds=30),
        previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
    )
    assert after.should_trigger is True

    same_window = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(seconds=30),
        previous_scan_utc=occ + timedelta(seconds=29), tz=timezone.utc,
    )
    assert same_window.should_trigger is False
    assert same_window.reason == "not_due"


# ── C2: UTC dedup across all rows, microsecond boundary, text-sort ────────

@pytest.mark.parametrize(
    ("offset_us", "expected_trigger", "expected_reason"),
    [
        (-1, True, None),
        (0, False, "already_triggered_this_window"),
        (1, False, "already_triggered_this_window"),
        (1_000_000, False, "already_triggered_this_window"),
        (3_600_000_000, False, "already_triggered_this_window"),
    ],
)
def test_c2_utc_dedup_microsecond_boundary(
    tmp_path, offset_us, expected_trigger, expected_reason,
):
    db = Database(tmp_path / "db.sqlite")
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    _insert_cleanup_task(
        db, task_id="TASK-100",
        created_at=occ + timedelta(microseconds=offset_us),
        status=TaskStatus.COMPLETED,
    )
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(seconds=5),
        previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
    )
    assert decision.should_trigger is expected_trigger
    assert decision.reason == expected_reason


def test_c2_utc_dedup_not_text_sort_winner(tmp_path):
    """D2: `12:00+09:00` (03:00Z) must not outrank the exact `03:30Z` boundary
    merely because its stored string sorts later."""
    db = Database(tmp_path / "db.sqlite")
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    db.insert_task(TaskRecord(
        id="TASK-EARLIER-TEXT-LATER",
        brief=wcs._CLEANUP_BRIEF_MARKER + "\noffset representation",
        team="engineering", assigned_agent="dev_agent",
        status=TaskStatus.COMPLETED,
        created_at=datetime(2026, 9, 20, 12, 0, tzinfo=timezone(timedelta(hours=9))),
    ))
    db.insert_task(TaskRecord(
        id="TASK-AT-BOUNDARY",
        brief=wcs._CLEANUP_BRIEF_MARKER + "\nboundary",
        team="engineering", assigned_agent="dev_agent",
        status=TaskStatus.COMPLETED, created_at=occ,
    ))
    history = wcs._cleanup_task_history(db, "dev_agent")
    assert history.count == 2
    assert history.newest_utc == occ  # 03:00Z < 03:30Z despite lexical order

    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(minutes=10),
        previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
    )
    assert decision.should_trigger is False
    assert decision.reason == "already_triggered_this_window"


# ── C3: first resumed post-boundary scan creates once per agent ───────────

@pytest.mark.asyncio
async def test_c3_real_loop_resumed_scan_after_boundary_once_per_agent(
    tmp_path, test_settings, monkeypatch,
):
    db, org, state = _seeded_org(tmp_path, test_settings)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    occ = _daily_0330_utc()
    for i, agent in enumerate(("dev_agent", "qa_engineer")):
        _insert_cleanup_task(
            db, task_id=f"TASK-{700 + i}", agent=agent,
            created_at=occ - timedelta(days=1) + timedelta(minutes=5),
            status=TaskStatus.COMPLETED,
        )
    measurement_calls: list[int] = []
    _install_measurement(monkeypatch, measurement_calls)
    holder = {"now": occ}
    _install_stamped_task_record(monkeypatch, holder)
    _install_clock(monkeypatch, [
        occ - timedelta(microseconds=500_000),          # 03:29:59.5, first scan
        occ + timedelta(seconds=60, microseconds=500_000),  # 03:31:00.5
        occ + timedelta(seconds=122),                   # 03:32:02
    ], holder)
    _install_sleep_cancel(monkeypatch, 3)

    with pytest.raises(asyncio.CancelledError):
        await wcs.workspace_cleanup_scheduler_loop(
            state, interval_seconds=60, warm_up_seconds=0,
        )

    assert len(state.queue.items) == 2
    tasks = [db.get_task(task_id) for _, task_id in state.queue.items]
    assert {task.assigned_agent for task in tasks} == {"dev_agent", "qa_engineer"}
    resumed = occ + timedelta(seconds=60, microseconds=500_000)
    assert all(task.created_at == resumed for task in tasks)
    for task in tasks:
        assert task.brief.startswith(wcs._CLEANUP_BRIEF_MARKER)
        triggered = [
            row for row in db.get_audit_logs(task.id)
            if row["action"] == "workspace_cleanup_triggered"
        ]
        assert len(triggered) == 1
        assert triggered[0]["payload"]["run_number"] == 2
    # measurement ran only on the resumed post-boundary scan (2 agents).
    assert len(measurement_calls) == 2


# ── C4: empty-history pre-boundary startup catch-up (labeled) ─────────────

@pytest.mark.asyncio
async def test_c4_real_loop_empty_history_pre_boundary_catch_up(
    tmp_path, test_settings, monkeypatch,
):
    """Proves the first_scan catch-up of the PREVIOUS window, then actually
    crosses the imminent boundary: the still-unfinished catch-up run suppresses
    the crossing (and the following ordinary scan) so nothing is created twice."""
    db, org, state = _seeded_org(tmp_path, test_settings)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    occ = _daily_0330_utc()
    measurement_calls: list[int] = []
    _install_measurement(monkeypatch, measurement_calls)
    holder = {"now": occ}
    _install_stamped_task_record(monkeypatch, holder)
    _install_clock(monkeypatch, [
        occ - timedelta(seconds=30),   # first scan: catch-up previous window
        occ + timedelta(seconds=10),   # crosses the imminent 03:30 boundary
        occ + timedelta(seconds=70),   # ordinary post-boundary scan
    ], holder)
    _install_sleep_cancel(monkeypatch, 3)

    with pytest.raises(asyncio.CancelledError):
        await wcs.workspace_cleanup_scheduler_loop(
            state, interval_seconds=1, warm_up_seconds=0,
        )

    assert len(state.queue.items) == 2  # one per registered agent
    catch_up = occ - timedelta(seconds=30)
    assert all(
        db.get_task(task_id).created_at == catch_up
        for _, task_id in state.queue.items
    )
    # The crossing and the later scan added no work (the unfinished catch-up
    # suppresses the crossing; the next scan is simply not due).
    assert len(measurement_calls) == 2


# ── C5: multi-day jump evaluates the latest window only ───────────────────

@pytest.mark.asyncio
async def test_c5_real_loop_multiday_jump_latest_window_only(
    tmp_path, test_settings, monkeypatch,
):
    db, org, state = _seeded_org(tmp_path, test_settings)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    occ = _daily_0330_utc()
    for i, agent in enumerate(("dev_agent", "qa_engineer")):
        _insert_cleanup_task(
            db, task_id=f"TASK-{800 + i}", agent=agent,
            created_at=occ + timedelta(seconds=5), status=TaskStatus.COMPLETED,
        )
    measurement_calls: list[int] = []
    _install_measurement(monkeypatch, measurement_calls)
    holder = {"now": occ}
    _install_stamped_task_record(monkeypatch, holder)
    _install_clock(monkeypatch, [
        occ + timedelta(seconds=60),   # startup window serviced terminal
        occ + timedelta(days=3),       # jump across two unserviced windows
    ], holder)
    _install_sleep_cancel(monkeypatch, 2)

    with pytest.raises(asyncio.CancelledError):
        await wcs.workspace_cleanup_scheduler_loop(
            state, interval_seconds=1, warm_up_seconds=0,
        )

    assert len(state.queue.items) == 2  # latest window only, no per-day replay
    assert all(
        db.get_task(task_id).created_at == occ + timedelta(days=3)
        for _, task_id in state.queue.items
    )
    assert len(measurement_calls) == 2


# ── C6: warm-up + terminal serviced restart (incl. reopened DB) ───────────

def test_c6_terminal_serviced_marker_restart_reopened_db(tmp_path):
    db_path = tmp_path / "db.sqlite"
    db = Database(db_path)
    occ = _daily_0330_utc()
    # A catch-up task terminalized without rewriting created_at.
    _insert_cleanup_task(
        db, task_id="TASK-100", created_at=occ - timedelta(minutes=90),
        status=TaskStatus.PENDING,
    )
    db.update_task("TASK-100", status=TaskStatus.COMPLETED)
    before = db.get_task("TASK-100")

    reopened = Database(db_path)  # fresh loop reopening the same DB
    after = reopened.get_task("TASK-100")
    assert after.created_at == before.created_at == occ - timedelta(minutes=90)
    assert after.status == TaskStatus.COMPLETED

    decision = wcs.decide_cleanup_trigger(
        db=reopened, agent="dev_agent",
        now_utc=occ - timedelta(minutes=80), first_scan=True, tz=timezone.utc,
    )
    assert decision.should_trigger is False
    assert decision.reason == "already_triggered_this_window"


def test_c6_fresh_restart_terminal_current_window_marker_no_work(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    occ = _daily_0330_utc()
    _insert_cleanup_task(
        db, task_id="TASK-100", created_at=occ + timedelta(minutes=5),
        status=TaskStatus.COMPLETED,
    )
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(hours=4),
        first_scan=True, tz=timezone.utc,
    )
    assert decision.should_trigger is False
    assert decision.reason == "already_triggered_this_window"


# ── C7: reason precedence + both continuing/restart final transitions ─────

def test_c7_reason_precedence_and_final_transitions(tmp_path):
    occ = _daily_0330_utc()
    db = Database(tmp_path / "db.sqlite")
    # A: pre-occurrence unfinished marker suppresses at the crossing.
    _insert_cleanup_task(
        db, task_id="TASK-100",
        created_at=occ - timedelta(days=1) + timedelta(minutes=5),
        status=TaskStatus.PENDING,
    )
    decision = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ,
        previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
    )
    assert decision.reason == "prior_run_in_flight"

    # Terminalizing without rewriting created_at turns the ordinary later scan
    # into not_due (no creation) and the restart into an eligible window.
    db.update_task("TASK-100", status=TaskStatus.COMPLETED)
    assert db.get_task("TASK-100").created_at == (
        occ - timedelta(days=1) + timedelta(minutes=5)
    )
    later = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(minutes=30),
        previous_scan_utc=occ + timedelta(minutes=29), tz=timezone.utc,
    )
    assert later.should_trigger is False
    assert later.reason == "not_due"

    # B: continuing to the next boundary creates exactly once.
    next_boundary = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(days=1),
        previous_scan_utc=occ + timedelta(days=1) - timedelta(seconds=1),
        tz=timezone.utc,
    )
    assert next_boundary.should_trigger is True

    # C: restart within the still-unserviced current window creates once.
    restart = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(minutes=10),
        first_scan=True, tz=timezone.utc,
    )
    assert restart.should_trigger is True

    # D: a marker at/after the occurrence suppresses both unfinished and
    # terminal, with current-window precedence over prior_run_in_flight.
    db2 = Database(tmp_path / "db2.sqlite")
    _insert_cleanup_task(
        db2, task_id="TASK-200", created_at=occ + timedelta(seconds=1),
        status=TaskStatus.PENDING,
    )
    due_unfinished = wcs.decide_cleanup_trigger(
        db=db2, agent="dev_agent", now_utc=occ + timedelta(minutes=1),
        previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
    )
    assert due_unfinished.reason == "already_triggered_this_window"
    db2.update_task("TASK-200", status=TaskStatus.COMPLETED)
    due_terminal = wcs.decide_cleanup_trigger(
        db=db2, agent="dev_agent", now_utc=occ + timedelta(minutes=2),
        previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
    )
    assert due_terminal.reason == "already_triggered_this_window"


# ── C8: legacy weekly ordinals 1/2 -> daily ordinal 3, same thread ────────

@pytest.mark.asyncio
async def test_c8_legacy_weekly_ordinals_to_daily_ordinal_three_same_thread(
    tmp_path, test_settings, monkeypatch,
):
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)
    state = _FakeDaemonState()

    first = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    thread_id = wcs._find_report_thread(db, "dev_agent").thread_id
    assert thread_id is not None
    db.update_task(first, status=TaskStatus.COMPLETED)
    first_row = db.get_task(first)

    # A preserved legacy weekly marker row (ordinal 2) with an old timestamp.
    legacy_created = datetime.now(timezone.utc) - timedelta(days=180)
    _insert_cleanup_task(
        db, task_id="TASK-LEGACY-2", agent="dev_agent",
        created_at=legacy_created, status=TaskStatus.COMPLETED,
    )
    legacy_row = db.get_task("TASK-LEGACY-2")

    third = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    third_task = db.get_task(third)
    assert f"--thread-id {thread_id}" in third_task.brief
    audit = [
        row for row in db.get_audit_logs(third)
        if row["action"] == "workspace_cleanup_triggered"
    ]
    assert audit[0]["payload"]["run_number"] == 3
    assert audit[0]["payload"]["brief_kind"] == "cleanup"

    # Both prior marker rows are unchanged (brief/status/created_at).
    assert db.get_task(first).brief == first_row.brief
    assert db.get_task(first).status == first_row.status
    assert db.get_task(first).created_at == first_row.created_at
    assert db.get_task("TASK-LEGACY-2").brief == legacy_row.brief
    assert db.get_task("TASK-LEGACY-2").status == legacy_row.status
    assert db.get_task("TASK-LEGACY-2").created_at == legacy_row.created_at


# ── C9: DST valid 23h/25h pairs, fold-before-second, gap, fractional ──────

def test_c9_dst_fold_gap_and_valid_pairs():
    from datetime import date
    from zoneinfo import ZoneInfo

    hel = ZoneInfo("Europe/Helsinki")
    ny = ZoneInfo("America/New_York")
    chatham = ZoneInfo("Pacific/Chatham")
    shanghai = ZoneInfo("Asia/Shanghai")

    ny_before = wcs._local_occurrence_utc(date(2026, 3, 7), ny)
    ny_after = wcs._local_occurrence_utc(date(2026, 3, 8), ny)
    assert ny_before == datetime(2026, 3, 7, 8, 30, tzinfo=timezone.utc)
    assert ny_after == datetime(2026, 3, 8, 7, 30, tzinfo=timezone.utc)
    assert ny_after - ny_before == timedelta(hours=23)

    ny_fall_before = wcs._local_occurrence_utc(date(2026, 10, 31), ny)
    ny_fall_after = wcs._local_occurrence_utc(date(2026, 11, 1), ny)
    assert ny_fall_before == datetime(2026, 10, 31, 7, 30, tzinfo=timezone.utc)
    assert ny_fall_after == datetime(2026, 11, 1, 8, 30, tzinfo=timezone.utc)
    assert ny_fall_after - ny_fall_before == timedelta(hours=25)

    # Fall-back ambiguous 03:30 -> first instance only (fold=0).
    first_instance = wcs._local_occurrence_utc(date(2026, 10, 25), hel)
    assert first_instance == datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    # fold-before-second 03:30 (01:15Z local 03:15+02:00 fold=1) still resolves
    # to the first instance.
    assert wcs._latest_due_occurrence_utc(
        datetime(2026, 10, 25, 1, 15, tzinfo=timezone.utc), hel,
    ) == first_instance
    # at the second 03:30 there is no second occurrence.
    assert wcs._latest_due_occurrence_utc(
        datetime(2026, 10, 25, 1, 30, tzinfo=timezone.utc), hel,
    ) == first_instance

    # Spring-forward gap: 2027-03-28 03:30 does not exist; latest due resolves
    # to the previous existing occurrence (47h window).
    assert wcs._local_occurrence_utc(date(2027, 3, 28), hel) is None
    gap_due = wcs._latest_due_occurrence_utc(
        datetime(2027, 3, 28, 12, 0, tzinfo=timezone.utc), hel,
    )
    assert gap_due == datetime(2027, 3, 27, 1, 30, tzinfo=timezone.utc)
    assert wcs._local_occurrence_utc(date(2027, 3, 29), hel) == datetime(
        2027, 3, 29, 0, 30, tzinfo=timezone.utc,
    )
    assert wcs._local_occurrence_utc(date(2027, 3, 29), hel) - gap_due == timedelta(hours=47)

    # Fractional-offset zone: Chatham fold0, then a gap.
    assert wcs._local_occurrence_utc(date(2026, 4, 5), chatham) == datetime(
        2026, 4, 4, 13, 45, tzinfo=timezone.utc,
    )
    assert wcs._local_occurrence_utc(date(2026, 9, 27), chatham) is None

    # Fixed offset zone.
    assert wcs._local_occurrence_utc(date(2026, 9, 20), shanghai) == datetime(
        2026, 9, 19, 19, 30, tzinfo=timezone.utc,
    )


# ── C10: inclusive today+3 lookup; exhausted search fails closed ─────────

def test_c10_lookback_inclusive_and_exhausted_fails_closed(tmp_path, monkeypatch):
    from datetime import date

    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    calls: list[object] = []

    def only_today_minus_three(local_date, tz):
        calls.append(local_date)
        if local_date == date(2026, 9, 17):  # today-3 inclusive
            return datetime.combine(
                local_date, wcs._OCCURRENCE_TIME, tzinfo=timezone.utc,
            )
        return None

    monkeypatch.setattr(wcs, "_local_occurrence_utc", only_today_minus_three)
    found = wcs._latest_due_occurrence_utc(now, timezone.utc)
    assert found == datetime(2026, 9, 17, 3, 30, tzinfo=timezone.utc)
    assert calls == [
        date(2026, 9, 20), date(2026, 9, 19),
        date(2026, 9, 18), date(2026, 9, 17),
    ]

    def only_today_minus_four(local_date, tz):
        calls.append(local_date)
        if local_date == date(2026, 9, 16):  # today-4 is NOT searched
            return datetime.combine(
                local_date, wcs._OCCURRENCE_TIME, tzinfo=timezone.utc,
            )
        return None

    calls.clear()
    monkeypatch.setattr(wcs, "_local_occurrence_utc", only_today_minus_four)
    assert wcs._latest_due_occurrence_utc(now, timezone.utc) is None
    assert calls == [
        date(2026, 9, 20), date(2026, 9, 19),
        date(2026, 9, 18), date(2026, 9, 17),
    ]

    monkeypatch.setattr(wcs, "_local_occurrence_utc", lambda *a, **k: None)
    decision = wcs.decide_cleanup_trigger(
        db=Database(tmp_path / "db.sqlite"), agent="dev_agent",
        now_utc=now, tz=timezone.utc,
    )
    assert decision.should_trigger is False
    assert decision.reason == "occurrence_search_exhausted"


# ── C11: exact count/ordinal beyond page + decisive old unfinished ───────

@pytest.mark.asyncio
@pytest.mark.parametrize("total", [999, 1000, 1001, 1500])
async def test_c11_exact_ordinal_n_plus_one_no_saturation(
    tmp_path, test_settings, monkeypatch, total,
):
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)
    occ = _daily_0330_utc()
    base = occ - timedelta(days=2)
    for i in range(total):
        _insert_cleanup_task(
            db, task_id=f"TASK-{1000 + i}", agent="dev_agent",
            created_at=base + timedelta(seconds=i), status=TaskStatus.COMPLETED,
        )
    history = wcs._cleanup_task_history(db, "dev_agent")
    assert history.count == total
    assert history.has_unfinished is False

    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    assert task_id is not None
    audit = [
        row for row in db.get_audit_logs(task_id)
        if row["action"] == "workspace_cleanup_triggered"
    ]
    assert audit[0]["payload"]["run_number"] == total + 1


@pytest.mark.asyncio
async def test_c11_decisive_old_unfinished_beyond_page_and_newer_stamps(
    tmp_path, test_settings, monkeypatch,
):
    """C11: the decisive old unfinished marker (final rowid, beyond both the
    first 1000-row page and the 1000 newest timestamps, all before the window)
    blocks a REAL due crossing through `_tick_org` with zero created-task /
    measurement / enqueue / triggered-audit deltas. Terminalizing ONLY that row
    then permits one independent first-scan through the same tick/producer seam
    with the exact ordinal 1003 and preserved original rows/timestamps."""
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    db, org, state = _seeded_org(tmp_path, test_settings)
    _write_file(org.root / "workspaces" / "dev_agent" / "f.txt", 1024)
    measurement_calls: list[int] = []
    _install_measurement(monkeypatch, measurement_calls)
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    before = occ - timedelta(days=2)
    # 1001 newer terminal rows (the first ascending page), all before the window.
    for i in range(1001):
        _insert_cleanup_task(
            db, task_id=f"TASK-{2000 + i}", agent="dev_agent",
            created_at=before + timedelta(seconds=i), status=TaskStatus.COMPLETED,
        )
    # The decisive older unfinished marker is the FINAL rowid, beyond both the
    # first 1000-row page and the 1000 newest timestamps.
    old_created = before - timedelta(days=100)
    _insert_cleanup_task(
        db, task_id="TASK-OLD-UNFINISHED", agent="dev_agent",
        created_at=old_created, status=TaskStatus.IN_PROGRESS,
    )
    # qa_engineer's window is already serviced, so it never triggers here.
    _insert_cleanup_task(
        db, task_id="TASK-QA-SERVICED", agent="qa_engineer",
        created_at=occ, status=TaskStatus.COMPLETED,
    )
    history = wcs._cleanup_task_history(db, "dev_agent")
    assert history.count == 1002
    assert history.has_unfinished is True

    snapshot_sql = "SELECT id, status, created_at FROM tasks ORDER BY rowid"
    seed_rows = [tuple(row) for row in db.execute(snapshot_sql).fetchall()]
    audit_sql = "SELECT action, task_id, agent FROM audit_log ORDER BY rowid"
    tasks_before = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    audits_before = [tuple(row) for row in db.execute(audit_sql).fetchall()]

    # Real due crossing blocked by the old unfinished row: zero deltas.
    await wcs._tick_org(
        org, state, now_utc=occ, previous_scan_utc=occ - timedelta(seconds=1),
    )
    assert state.queue.items == []
    assert measurement_calls == []
    assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == tasks_before
    assert [tuple(row) for row in db.execute(audit_sql).fetchall()] == audits_before

    # Terminalize ONLY the decisive old row (no ordinary same-window crossing).
    db.update_task("TASK-OLD-UNFINISHED", status=TaskStatus.COMPLETED)

    # An independent first-scan opportunity through the tick/producer seam.
    await wcs._tick_org(
        org, state, now_utc=occ + timedelta(minutes=1), first_scan=True,
    )
    assert len(state.queue.items) == 1
    assert state.queue.items[0][0] == "test"
    task_id = state.queue.items[0][1]
    assert db.get_task(task_id).assigned_agent == "dev_agent"
    assert len(measurement_calls) == 1
    assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == tasks_before + 1
    after_audits = [tuple(row) for row in db.execute(audit_sql).fetchall()]
    assert after_audits[: len(audits_before)] == audits_before
    new_audits = after_audits[len(audits_before):]
    # Exactly the trigger plus the first-report-thread creation audits; the
    # serviced qa_engineer skip writes nothing.
    assert sorted(row[0] for row in new_audits) == [
        "thread_message_sent", "thread_started", "workspace_cleanup_triggered",
    ]
    triggered_new = [row for row in new_audits
                     if row[0] == "workspace_cleanup_triggered"]
    assert len(triggered_new) == 1
    assert triggered_new[0][2] == "dev_agent"
    audit = [
        row for row in db.get_audit_logs(task_id)
        if row["action"] == "workspace_cleanup_triggered"
    ]
    assert len(audit) == 1
    assert audit[0]["payload"]["run_number"] == 1003

    # Original rows/timestamps are preserved; only the decisive row changed.
    after_rows = {
        row[0]: (row[1], row[2])
        for row in db.execute(snapshot_sql).fetchall()
    }
    for tid, status, created_at in seed_rows:
        assert after_rows[tid][1] == created_at
        if tid == "TASK-OLD-UNFINISHED":
            assert after_rows[tid][0] != status
        else:
            assert after_rows[tid][0] == status
    assert db.get_task("TASK-OLD-UNFINISHED").status == TaskStatus.COMPLETED
    assert db.get_task("TASK-OLD-UNFINISHED").created_at == old_created


# ── C13: exact 1 GiB threshold and unavailable measurement ───────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("workspaces_bytes", "should_trigger"),
    [((2 ** 30) - 1, False), (2 ** 30, True)],
)
async def test_c13_exact_threshold_boundary(
    tmp_path, test_settings, monkeypatch, workspaces_bytes, should_trigger,
):
    db = Database(tmp_path / "db.sqlite")
    org = _org_with_workspaces(tmp_path, db, test_settings)
    calls: list[int] = []
    _install_measurement(
        monkeypatch, calls, workspaces_bytes=workspaces_bytes,
    )
    state = _FakeDaemonState()
    task_id = await wcs.trigger_cleanup(
        org, agent="dev_agent",
        enqueue=lambda slug, tid: state.queue.enqueue(slug, tid),
    )
    if should_trigger:
        assert task_id is not None
        assert state.queue.items == [("test", task_id)]
    else:
        assert task_id is None
        assert state.queue.items == []
        audits = [
            row for row in db.get_audit_logs("workspace-cleanup:skipped")
            if row["payload"].get("reason") == "workspace_below_threshold"
        ]
        assert len(audits) == 1


# ══════════════════════════════════════════════════════════════════════════
# TASK-8483 F1/F2/F3: retained regression cases closing code_reviewer
# TASK-8482 F1-F3 (manager step5 disposition). Test-only correction; no
# production change. C3/C5/C12d-e/C14/C15 remain unchanged.
# ══════════════════════════════════════════════════════════════════════════


class _PageFailProxy:
    """Delegating connection that fails ``fetchall`` on the Nth page SELECT."""

    def __init__(self, real, *, fail_fetch_page):
        self._real = real
        self._fail_fetch_page = fail_fetch_page
        self.pages = 0

    @property
    def in_transaction(self):
        return self._real.in_transaction

    def execute(self, sql, params=()):
        if sql.startswith("SELECT rowid"):
            self.pages += 1
            cursor = self._real.execute(sql, params)
            if self.pages == self._fail_fetch_page:
                class _BrokenFetch:
                    def fetchall(self):
                        raise sqlite3.OperationalError(
                            "injected page-two fetchall failure"
                        )
                return _BrokenFetch()
            return cursor
        return self._real.execute(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


# ── F3 C1: fixed-week matrix across timezones + real seven-boundary walk ──

@pytest.mark.parametrize("tz_name", ["UTC", "Asia/Shanghai", "America/New_York"])
def test_c1_fixed_week_all_weekdays_across_timezones(tmp_path, tz_name):
    """C1: no weekday gate. A fixed seven-day week has the exact same
    before/exact/after/same-window decision shape in UTC, Shanghai and
    New York; each occurrence is local 03:30 on its civil date."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tz_name)
    db = Database(tmp_path / "db.sqlite")
    monday = date(2026, 9, 14)
    for weekday in range(7):
        local_date = monday + timedelta(days=weekday)
        occ = wcs._local_occurrence_utc(local_date, tz)
        assert occ is not None
        local = occ.astimezone(tz)
        assert local.date() == local_date
        assert (local.hour, local.minute) == (3, 30)

        before = wcs.decide_cleanup_trigger(
            db=db, agent="dev_agent", now_utc=occ - timedelta(seconds=1),
            previous_scan_utc=occ - timedelta(seconds=2), tz=tz,
        )
        assert before.should_trigger is False
        assert before.reason == "not_due"

        exact = wcs.decide_cleanup_trigger(
            db=db, agent="dev_agent", now_utc=occ,
            previous_scan_utc=occ - timedelta(seconds=1), tz=tz,
        )
        assert exact.should_trigger is True

        after = wcs.decide_cleanup_trigger(
            db=db, agent="dev_agent", now_utc=occ + timedelta(seconds=30),
            previous_scan_utc=occ - timedelta(seconds=1), tz=tz,
        )
        assert after.should_trigger is True

        same_window = wcs.decide_cleanup_trigger(
            db=db, agent="dev_agent", now_utc=occ + timedelta(seconds=30),
            previous_scan_utc=occ + timedelta(seconds=29), tz=tz,
        )
        assert same_window.should_trigger is False
        assert same_window.reason == "not_due"


@pytest.mark.asyncio
async def test_c1_real_seven_boundary_tick_walk_terminal_and_unfinished(
    tmp_path, test_settings, monkeypatch,
):
    """C1: a real seven-boundary `_tick_org` walk. Terminalizing each newly
    created task before the next daily boundary yields seven tasks; leaving the
    first unfinished suppresses all six later boundaries (one task)."""
    base = datetime(2026, 9, 14, 3, 30, tzinfo=timezone.utc)  # Monday
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)

    async def walk(label, terminalize):
        root = tmp_path / label
        root.mkdir()
        db, org, state = _seeded_org(root, test_settings)
        calls: list[int] = []
        _install_measurement(monkeypatch, calls)
        holder = {"now": base}
        _install_stamped_task_record(monkeypatch, holder)
        for day in range(7):
            occ = base + timedelta(days=day)
            holder["now"] = occ + timedelta(seconds=30)
            before = len(state.queue.items)
            await wcs._tick_org(
                org, state, now_utc=holder["now"],
                previous_scan_utc=occ - timedelta(seconds=1),
            )
            created = [tid for _, tid in state.queue.items[before:]]
            dev_created = [
                tid for tid in created
                if db.get_task(tid).assigned_agent == "dev_agent"
            ]
            if terminalize:
                for tid in dev_created:
                    db.update_task(tid, status=TaskStatus.COMPLETED)
        return db, state

    db_terminal, _ = await walk("terminal", True)
    dev_terminal = [
        task for task in db_terminal.list_tasks(limit=1000, assigned_agent="dev_agent")
        if wcs._CLEANUP_BRIEF_MARKER in task.brief
    ]
    assert len(dev_terminal) == 7

    db_unfinished, _ = await walk("unfinished", False)
    dev_unfinished = [
        task for task in db_unfinished.list_tasks(limit=1000, assigned_agent="dev_agent")
        if wcs._CLEANUP_BRIEF_MARKER in task.brief
    ]
    assert len(dev_unfinished) == 1


# ── F3 C2: naive and equal-instant offset representations ────────────────

def test_c2_naive_and_equal_instant_offsets(tmp_path):
    """C2: a naive-UTC timestamp and an equal instant written with a non-UTC
    offset both normalize to the same instant for window dedup."""
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    cases = {
        "naive": (occ + timedelta(seconds=5)).replace(tzinfo=None),
        # 12:30+09:00 is exactly 03:30Z (equal instant, different offset).
        "equal-instant": datetime(
            2026, 9, 20, 12, 30, tzinfo=timezone(timedelta(hours=9)),
        ),
    }
    for label, created in cases.items():
        db = Database(tmp_path / f"{label}.sqlite")
        _insert_cleanup_task(
            db, task_id="TASK-X", created_at=created, status=TaskStatus.COMPLETED,
        )
        decision = wcs.decide_cleanup_trigger(
            db=db, agent="dev_agent", now_utc=occ + timedelta(minutes=1),
            previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
        )
        assert decision.should_trigger is False, label
        assert decision.reason == "already_triggered_this_window", label

    # A naive pre-boundary timestamp still permits (there is no rolling gate).
    db = Database(tmp_path / "naive-before.sqlite")
    _insert_cleanup_task(
        db, task_id="TASK-Y",
        created_at=(occ - timedelta(seconds=1)).replace(tzinfo=None),
        status=TaskStatus.COMPLETED,
    )
    permitted = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(minutes=1),
        previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
    )
    assert permitted.should_trigger is True


# ── F1 C6: real loop creation/terminalization/close/reopen/restart ───────

@pytest.mark.asyncio
async def test_c6_real_loop_catch_up_terminalize_close_reopen_restart(
    tmp_path, test_settings, monkeypatch,
):
    """C6: real loop/tick/producer catch-up creation, explicit terminalization
    without rewriting created_at, close/reopen the same DB, restart dedup,
    next-window creation, and terminalized-restart dedup."""
    db, org, state = _seeded_org(tmp_path, test_settings)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    measurement_calls: list[int] = []
    holder = {"now": occ}
    _install_measurement(monkeypatch, measurement_calls)
    _install_stamped_task_record(monkeypatch, holder)

    async def run_once(stamp):
        _install_clock(monkeypatch, [stamp], holder)
        _install_sleep_cancel(monkeypatch, 1)
        try:
            await wcs.workspace_cleanup_scheduler_loop(
                state, interval_seconds=60, warm_up_seconds=0,
            )
        except asyncio.CancelledError:
            pass

    # (1) first-scan catch-up creates exactly one task per registered agent.
    await run_once(occ - timedelta(minutes=90))
    assert len(state.queue.items) == 2
    assert len(measurement_calls) == 2
    ids = [tid for _, tid in state.queue.items]
    threads = {
        agent: wcs._find_report_thread(db, agent).thread_id
        for agent in ("dev_agent", "qa_engineer")
    }
    stamps = {tid: db.get_task(tid).created_at for tid in ids}
    assert all(stamps[tid] == occ - timedelta(minutes=90) for tid in ids)
    assert all(
        len([
            row for row in db.get_audit_logs(tid)
            if row["action"] == "workspace_cleanup_triggered"
        ]) == 1
        for tid in ids
    )

    # Explicitly terminalize every new task without touching created_at.
    for tid in ids:
        db.update_task(tid, status=TaskStatus.COMPLETED)
    db._conn.close()

    # (2) a fresh loop over the reopened DB: the terminal catch-up services the
    # previous window, so restart adds zero work with unchanged id/time/thread.
    reopened = Database(tmp_path / "db.sqlite")
    org.db = reopened
    await run_once(occ - timedelta(minutes=80))
    assert len(state.queue.items) == 2
    assert len(measurement_calls) == 2
    assert [reopened.get_task(tid).id for tid in ids] == ids
    assert [reopened.get_task(tid).created_at for tid in ids] == [
        stamps[tid] for tid in ids
    ]
    assert {
        agent: wcs._find_report_thread(reopened, agent).thread_id
        for agent in threads
    } == threads
    restart_decision = wcs.decide_cleanup_trigger(
        db=reopened, agent="dev_agent", now_utc=occ - timedelta(minutes=80),
        first_scan=True, tz=timezone.utc,
    )
    assert restart_decision.should_trigger is False
    assert restart_decision.reason == "already_triggered_this_window"

    # (3) crossing the boundary creates exactly one more per agent.
    await run_once(occ + timedelta(minutes=10))
    assert len(state.queue.items) == 4
    assert len(measurement_calls) == 4
    for _, tid in state.queue.items[2:]:
        reopened.update_task(tid, status=TaskStatus.COMPLETED)

    # (4) a terminal current-window restart adds nothing.
    await run_once(occ + timedelta(minutes=20))
    assert len(state.queue.items) == 4
    assert len(measurement_calls) == 4
    reopened._conn.close()


# ── F1 C7: nonterminal statuses, parked carrier, final transitions ───────

def _assert_c7_marker_precedence(db, occ, **task_kwargs):
    db.insert_task(TaskRecord(
        id="TASK-PRE",
        brief=wcs._CLEANUP_BRIEF_MARKER + "\npre-occurrence",
        team="engineering", assigned_agent="dev_agent",
        created_at=occ - timedelta(days=2),
        **task_kwargs,
    ))
    before = db.get_task("TASK-PRE")
    crossing = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ,
        previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
    )
    assert crossing.should_trigger is False
    assert crossing.reason == "prior_run_in_flight"

    # Terminalizing without touching created_at: the ordinary later scan is
    # not_due (the crossing is in the past) and creates nothing.
    db.update_task("TASK-PRE", status=TaskStatus.COMPLETED)
    assert db.get_task("TASK-PRE").created_at == before.created_at
    later = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(minutes=30),
        previous_scan_utc=occ + timedelta(minutes=29), tz=timezone.utc,
    )
    assert later.should_trigger is False
    assert later.reason == "not_due"

    # An at/after-occurrence marker takes current-window precedence over the
    # in-flight branch, both while unfinished and after terminalization.
    db.insert_task(TaskRecord(
        id="TASK-AT",
        brief=wcs._CLEANUP_BRIEF_MARKER + "\nat-occurrence",
        team="engineering", assigned_agent="dev_agent",
        created_at=occ + timedelta(seconds=1),
        **task_kwargs,
    ))
    due_unfinished = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(minutes=31),
        previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
    )
    assert due_unfinished.should_trigger is False
    assert due_unfinished.reason == "already_triggered_this_window"
    db.update_task("TASK-AT", status=TaskStatus.COMPLETED)
    due_terminal = wcs.decide_cleanup_trigger(
        db=db, agent="dev_agent", now_utc=occ + timedelta(minutes=32),
        previous_scan_utc=occ - timedelta(seconds=1), tz=timezone.utc,
    )
    assert due_terminal.should_trigger is False
    assert due_terminal.reason == "already_triggered_this_window"


@pytest.mark.parametrize(
    "status", [TaskStatus.PENDING, TaskStatus.IN_PROGRESS, TaskStatus.ESCALATED],
)
def test_c7_nonterminal_statuses_precedence(tmp_path, status):
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    db = Database(tmp_path / f"db-{status.value}.sqlite")
    _assert_c7_marker_precedence(db, occ, status=status)


def test_c7_valid_parked_carrier_precedence(tmp_path):
    from runtime.models import BlockKind

    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    db = Database(tmp_path / "parked.sqlite")
    _assert_c7_marker_precedence(
        db, occ, status=TaskStatus.IN_PROGRESS,
        block_kind=BlockKind.BLOCKED_ON_JOB, blocked_on_job_ids='["JOB-1"]',
    )


_C7_LIFECYCLE_FIXTURES = [
    pytest.param(TaskStatus.PENDING, False, id="pending"),
    pytest.param(TaskStatus.IN_PROGRESS, False, id="in-progress"),
    pytest.param(TaskStatus.ESCALATED, False, id="escalated"),
    pytest.param(TaskStatus.IN_PROGRESS, True, id="in-progress-parked"),
]


async def _tick_at(org, state, holder, now, previous=None, first=False):
    """One real `_tick_org` pass at a stamped instant (no new loop object)."""
    holder["now"] = now
    await wcs._tick_org(
        org, state, now_utc=now, previous_scan_utc=previous, first_scan=first,
    )


async def _drive_c7_real_tick_lifecycle(
    tmp_path, test_settings, monkeypatch, *, status, parked, restart,
):
    """C7: A -> real crossing blocked by an earlier unfinished marker; then
    terminalize ONLY those rows and run an ordinary same-window scan with no
    work; then either B (a real next-boundary continuation with an actual
    preceding scan) or C (close/reopen the same DB + first-scan catch-up in the
    still-unserviced current window). A helper that builds a fresh loop each
    time is deliberately NOT used as "continuation"; both branches drive the
    real `_tick_org` seam with explicit previous-scan instants.
    """
    db, org, state = _seeded_org(tmp_path, test_settings)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    next_occ = occ + timedelta(days=1)
    measurement_calls: list[int] = []
    holder = {"now": occ}
    _install_measurement(monkeypatch, measurement_calls)
    _install_stamped_task_record(monkeypatch, holder)

    def count(table: str) -> int:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def audit_counts() -> tuple[int, int]:
        return (
            count("audit_log"),
            db.execute(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE action='workspace_cleanup_triggered'"
            ).fetchone()[0],
        )

    kwargs: dict = {"status": status}
    if parked:
        from runtime.models import BlockKind

        kwargs.update(
            block_kind=BlockKind.BLOCKED_ON_JOB,
            blocked_on_job_ids='["JOB-1"]',
        )
    seed_created = occ - timedelta(days=2)
    agents = ("dev_agent", "qa_engineer")
    seed_ids = [f"TASK-SEED-{agent}" for agent in agents]
    for agent, task_id in zip(agents, seed_ids):
        db.insert_task(TaskRecord(
            id=task_id, brief=wcs._CLEANUP_BRIEF_MARKER + "\nseed",
            team="engineering", assigned_agent=agent,
            created_at=seed_created, **kwargs,
        ))
    seed_before = {tid: db.get_task(tid) for tid in seed_ids}
    tasks_before = count("tasks")
    audits_before = audit_counts()

    # A: the real crossing is blocked by the earlier unfinished markers:
    # zero created task, measurement, enqueue and triggered audit.
    await _tick_at(org, state, holder, occ, occ - timedelta(seconds=1))
    assert state.queue.items == []
    assert measurement_calls == []
    assert count("tasks") == tasks_before
    assert audit_counts() == audits_before

    # Terminalize ONLY those rows; created_at and ids are untouched.
    for tid in seed_ids:
        db.update_task(tid, status=TaskStatus.COMPLETED)
    for tid, snapshot in seed_before.items():
        after = db.get_task(tid)
        assert after.id == tid
        assert after.created_at == snapshot.created_at == seed_created
        assert after.status == TaskStatus.COMPLETED

    # Ordinary same-window scan (crossing already consumed): no work, no audit.
    await _tick_at(
        org, state, holder, occ + timedelta(minutes=30),
        occ + timedelta(minutes=29),
    )
    assert state.queue.items == []
    assert measurement_calls == []
    assert count("tasks") == tasks_before
    assert audit_counts() == audits_before

    # B: genuine next-boundary continuation of the same loop.
    # C: close/reopen the same DB and first-scan catch-up in the current window.
    if restart:
        db._conn.close()
        db = Database(tmp_path / "db.sqlite")
        org.db = db
        stamp = occ + timedelta(minutes=31)
        await _tick_at(org, state, holder, stamp, first=True)
    else:
        stamp = next_occ + timedelta(seconds=30)
        await _tick_at(
            org, state, holder, stamp, next_occ - timedelta(seconds=1),
        )

    # Exactly one new task per registered agent, stamped at the transition.
    assert len(state.queue.items) == 2
    assert all(slug == "test" for slug, _ in state.queue.items)
    assert len(measurement_calls) == 2
    assert count("tasks") == tasks_before + 2
    created = [tid for _, tid in state.queue.items]
    assert all(db.get_task(tid).created_at == stamp for tid in created)
    assert sorted(db.get_task(tid).assigned_agent for tid in created) == sorted(agents)
    # Continuous per-agent ordinal (one prior seed marker -> run 2).
    for tid in created:
        triggered = [
            row for row in db.get_audit_logs(tid)
            if row["action"] == "workspace_cleanup_triggered"
        ]
        assert len(triggered) == 1
        assert triggered[0]["payload"]["run_number"] == 2
    # Durable thread provenance exists and is carried into each brief.
    thread_ids = {
        agent: wcs._find_report_thread(db, agent).thread_id for agent in agents
    }
    assert all(thread_id is not None for thread_id in thread_ids.values())
    for tid in created:
        agent = db.get_task(tid).assigned_agent
        assert f"--thread-id {thread_ids[agent]}" in db.get_task(tid).brief

    for tid in created:
        db.update_task(tid, status=TaskStatus.COMPLETED)
    audits_after_create = audit_counts()

    # After C's new task is terminalized, close/reopen AGAIN and prove zero
    # additional task, measurement, triggered audit and enqueue. For B, an
    # ordinary scan of the same continuing loop adds nothing either.
    if restart:
        db._conn.close()
        db = Database(tmp_path / "db.sqlite")
        org.db = db
        await _tick_at(
            org, state, holder, stamp + timedelta(minutes=1), first=True,
        )
    else:
        await _tick_at(org, state, holder, stamp + timedelta(minutes=1), stamp)

    assert len(state.queue.items) == 2
    assert all(slug == "test" for slug, _ in state.queue.items)
    assert len(measurement_calls) == 2
    assert count("tasks") == tasks_before + 2
    assert audit_counts() == audits_after_create
    # Thread provenance and every original seed row survive the final restart.
    assert {
        agent: wcs._find_report_thread(db, agent).thread_id for agent in agents
    } == thread_ids
    for tid, snapshot in seed_before.items():
        assert db.get_task(tid).id == tid
        assert db.get_task(tid).created_at == snapshot.created_at


@pytest.mark.asyncio
@pytest.mark.parametrize("status, parked", _C7_LIFECYCLE_FIXTURES)
async def test_c7_real_tick_crossing_then_next_boundary_continuation(
    tmp_path, test_settings, monkeypatch, status, parked,
):
    """C7 B: real crossing blocked -> terminalize -> same-window no work ->
    actual next-boundary continuation creates once per agent."""
    await _drive_c7_real_tick_lifecycle(
        tmp_path, test_settings, monkeypatch,
        status=status, parked=parked, restart=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("status, parked", _C7_LIFECYCLE_FIXTURES)
async def test_c7_real_tick_restart_current_window_catch_up(
    tmp_path, test_settings, monkeypatch, status, parked,
):
    """C7 C: real crossing blocked -> terminalize -> same-window no work ->
    close/reopen same DB -> first-scan catch-up creates once per agent, then a
    further close/reopen adds nothing."""
    await _drive_c7_real_tick_lifecycle(
        tmp_path, test_settings, monkeypatch,
        status=status, parked=parked, restart=True,
    )


# ── F3 C8: legacy terminal timestamps through the real decision/tick ─────

@pytest.mark.asyncio
async def test_c8_legacy_timestamps_drive_real_decision_and_tick(
    tmp_path, test_settings, monkeypatch,
):
    """C8: two prior terminal marker rows (one a preserved legacy weekly row
    with an old timestamp) drive real `decide_cleanup_trigger` + `_tick_org` to
    the next daily ordinal 3, the same report thread, an unchanged created_at/
    status/brief for both prior rows, and `brief_kind="cleanup"`."""
    db, org, state = _seeded_org(tmp_path, test_settings)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    measurement_calls: list[int] = []
    _install_measurement(monkeypatch, measurement_calls)
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    holder = {"now": occ}
    _install_stamped_task_record(monkeypatch, holder)

    await wcs._tick_org(
        org, state, now_utc=occ, previous_scan_utc=occ - timedelta(seconds=1),
    )
    dev_created = [
        tid for _, tid in state.queue.items
        if db.get_task(tid).assigned_agent == "dev_agent"
    ]
    assert len(dev_created) == 1
    first_id = dev_created[0]
    thread_id = wcs._find_report_thread(db, "dev_agent").thread_id
    assert thread_id is not None
    db.update_task(first_id, status=TaskStatus.COMPLETED)

    # Preserved legacy weekly row (ordinal 2) with an old timestamp.
    legacy_created = occ - timedelta(days=180)
    _insert_cleanup_task(
        db, task_id="TASK-LEGACY-2", agent="dev_agent",
        created_at=legacy_created, status=TaskStatus.COMPLETED,
    )
    first_before = db.get_task(first_id)
    legacy_before = db.get_task("TASK-LEGACY-2")

    next_occ = occ + timedelta(days=1)
    holder["now"] = next_occ + timedelta(seconds=30)
    await wcs._tick_org(
        org, state, now_utc=holder["now"],
        previous_scan_utc=next_occ - timedelta(seconds=1),
    )
    dev_all = [
        tid for _, tid in state.queue.items
        if db.get_task(tid).assigned_agent == "dev_agent"
    ]
    assert len(dev_all) == 2
    third_id = dev_all[1]
    third = db.get_task(third_id)
    assert f"--thread-id {thread_id}" in third.brief
    audit = [
        row for row in db.get_audit_logs(third_id)
        if row["action"] == "workspace_cleanup_triggered"
    ]
    assert len(audit) == 1
    assert audit[0]["payload"]["run_number"] == 3
    assert audit[0]["payload"]["brief_kind"] == "cleanup"

    for task_id, before in ((first_id, first_before), ("TASK-LEGACY-2", legacy_before)):
        after = db.get_task(task_id)
        assert after.brief == before.brief
        assert after.status == before.status
        assert after.created_at == before.created_at


# ── F3 C9: serviced/unserviced restart on gap and fold dates ─────────────

@pytest.mark.asyncio
async def test_c9_serviced_and_unserviced_restart_gap_and_fold(
    tmp_path, test_settings, monkeypatch,
):
    """C9: through real `_tick_org`, an unserviced fall-back (fold) and a
    spring-forward gap date each create once and are then suppressed by their
    own durable marker; a pre-serviced marker creates nothing."""
    from zoneinfo import ZoneInfo

    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    helsinki = ZoneInfo("Europe/Helsinki")
    assert wcs._local_occurrence_utc(date(2027, 3, 28), helsinki) is None

    async def drive(label, now, marker_at):
        root = tmp_path / label
        root.mkdir()
        db, org, state = _seeded_org(root, test_settings)
        (org.root / "org" / "config.yaml").write_text(
            "timezone: Europe/Helsinki\n",
        )
        calls: list[int] = []
        _install_measurement(monkeypatch, calls)
        holder = {"now": now}
        _install_stamped_task_record(monkeypatch, holder)
        if marker_at is not None:
            for agent in ("dev_agent", "qa_engineer"):
                _insert_cleanup_task(
                    db, task_id=f"TASK-SERVICED-{agent}", agent=agent,
                    created_at=marker_at, status=TaskStatus.COMPLETED,
                )
        await wcs._tick_org(org, state, now_utc=now, first_scan=True)
        return db, org, state, calls

    # Fall-back (ambiguous) date: first instance 2026-10-25T00:30Z only.
    fold_now = datetime(2026, 10, 25, 1, 15, tzinfo=timezone.utc)
    fold_occ = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    _, org_fold, state_fold, calls_fold = await drive(
        "fold-unserviced", fold_now, None,
    )
    assert len(state_fold.queue.items) == 2
    assert len(calls_fold) == 2
    await wcs._tick_org(
        org_fold, state_fold,
        now_utc=fold_now + timedelta(minutes=5), first_scan=True,
    )
    assert len(state_fold.queue.items) == 2  # marker services the window
    _, _, state_fold_serviced, calls_fold_serviced = await drive(
        "fold-serviced", fold_now, fold_occ,
    )
    assert state_fold_serviced.queue.items == []
    assert calls_fold_serviced == []

    # Spring-forward gap: 2027-03-28 03:30 does not exist; latest due is
    # 2027-03-27T01:30Z (47h window).
    gap_now = datetime(2027, 3, 28, 12, 0, tzinfo=timezone.utc)
    gap_occ = datetime(2027, 3, 27, 1, 30, tzinfo=timezone.utc)
    _, org_gap, state_gap, calls_gap = await drive("gap-unserviced", gap_now, None)
    assert len(state_gap.queue.items) == 2
    await wcs._tick_org(
        org_gap, state_gap,
        now_utc=gap_now + timedelta(minutes=5), first_scan=True,
    )
    assert len(state_gap.queue.items) == 2
    _, _, state_gap_serviced, calls_gap_serviced = await drive(
        "gap-serviced", gap_now, gap_occ,
    )
    assert state_gap_serviced.queue.items == []
    assert calls_gap_serviced == []


# ── F3 C10: exhausted occurrence lookup through the tick ─────────────────

@pytest.mark.asyncio
async def test_c10_exhausted_lookup_due_and_ordinary_tick_zero_work(
    tmp_path, test_settings, monkeypatch,
):
    """C10: an exhausted occurrence search (no existing 03:30 in today..-3)
    fails closed on both a due crossing and an ordinary scan: zero tasks,
    zero enqueue, zero audit rows (no per-tick spam)."""
    db, org, state = _seeded_org(tmp_path, test_settings)
    monkeypatch.setattr(wcs, "_local_occurrence_utc", lambda *a, **k: None)
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)

    await wcs._tick_org(
        org, state, now_utc=occ, previous_scan_utc=occ - timedelta(seconds=1),
    )
    await wcs._tick_org(
        org, state, now_utc=occ + timedelta(minutes=1),
        previous_scan_utc=occ,
    )

    assert state.queue.items == []
    assert db.list_tasks(limit=1000) == []
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == 0


# ── F2 C11: reader partial-progress failure through the tick ─────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fetchall", "parse"])
async def test_c11_reader_partial_progress_failure_through_tick(
    tmp_path, test_settings, monkeypatch, mode,
):
    """F2/C11: a real second-page fetchall failure and a real second-page
    created_at parse failure after page-one accumulation flow through
    `_tick_org`: zero task/enqueue, exactly one due-boundary
    `history_indeterminate` audit for the affected agent, and no later
    ordinary-scan audit."""
    root = tmp_path / mode
    root.mkdir()
    db, org, state = _seeded_org(root, test_settings)
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    base = occ - timedelta(days=2)
    # 1001 matching dev_agent rows force a second page (page_size default 1000).
    for i in range(1001):
        _insert_cleanup_task(
            db, task_id=f"TASK-{4000 + i}", agent="dev_agent",
            created_at=base + timedelta(seconds=i), status=TaskStatus.COMPLETED,
        )
    # qa_engineer's window is serviced so it never triggers in this case.
    _insert_cleanup_task(
        db, task_id="TASK-QA-SERVICED", agent="qa_engineer",
        created_at=occ, status=TaskStatus.COMPLETED,
    )
    tasks_before = db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    audits_before = db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]

    real = db._conn
    if mode == "fetchall":
        monkeypatch.setattr(db, "_conn", _PageFailProxy(real, fail_fetch_page=2))
    else:
        # The malformed timestamp sits on the second page (rowid 4000 is page
        # one; 5000 is the sole second-page row).
        real.execute(
            "UPDATE tasks SET created_at = 'not-a-timestamp' WHERE id = 'TASK-5000'",
        )
        real.commit()

    await wcs._tick_org(
        org, state, now_utc=occ, previous_scan_utc=occ - timedelta(seconds=1),
    )
    if mode == "fetchall":
        monkeypatch.setattr(db, "_conn", real)

    assert state.queue.items == []
    assert db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == tasks_before
    dev_skips = [
        row for row in db.get_audit_logs("workspace-cleanup:skipped")
        if row["agent"] == "dev_agent"
        and row["payload"].get("reason") == "history_indeterminate"
    ]
    assert len(dev_skips) == 1
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == (
        audits_before + 1
    )

    # A later ordinary scan (crossing already consumed) adds no audit.
    await wcs._tick_org(
        org, state, now_utc=occ + timedelta(minutes=1), previous_scan_utc=occ,
    )
    assert db.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0] == (
        audits_before + 1
    )


# ── F3 C13: actions flag neutral at the scheduler seam ───────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("actions_enabled", ["true", "false"])
async def test_c13_actions_flag_is_cadence_neutral(
    tmp_path, test_settings, monkeypatch, actions_enabled,
):
    """C13: `reclamation_actions_enabled=true` with `enabled=true` does not
    alter the cadence (identical due-trigger result as `false`), and the
    autouse consumer tripwires prove no deletion consumer is invoked."""
    root = tmp_path / f"actions-{actions_enabled}"
    root.mkdir()
    db, org, state = _seeded_org(root, test_settings)
    (org.root / "org" / "config.yaml").write_text(
        "timezone: UTC\n"
        "workspace_cleanup:\n"
        "  enabled: true\n"
        f"  reclamation_actions_enabled: {actions_enabled}\n"
    )
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    calls: list[int] = []
    _install_measurement(monkeypatch, calls)
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    holder = {"now": occ}
    _install_stamped_task_record(monkeypatch, holder)

    await wcs._tick_org(
        org, state, now_utc=occ + timedelta(seconds=30),
        previous_scan_utc=occ - timedelta(seconds=1),
    )
    assert len(state.queue.items) == 2
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_c13_disabled_with_actions_true_creates_nothing(
    tmp_path, test_settings, monkeypatch,
):
    """C13: `enabled=false` with `reclamation_actions_enabled=true` still
    creates nothing — the action flag never bypasses the kill switch."""
    db, org, state = _seeded_org(tmp_path, test_settings)
    (org.root / "org" / "config.yaml").write_text(
        "timezone: UTC\n"
        "workspace_cleanup:\n"
        "  enabled: false\n"
        "  reclamation_actions_enabled: true\n"
    )
    monkeypatch.setattr(wcs, "_MIN_WORKSPACE_TRIGGER_BYTES", 1)
    occ = datetime(2026, 9, 20, 3, 30, tzinfo=timezone.utc)
    await wcs._tick_org(
        org, state, now_utc=occ + timedelta(seconds=30),
        previous_scan_utc=occ - timedelta(seconds=1),
    )
    assert state.queue.items == []
    assert db.list_tasks(limit=1000) == []


# ── (n) shared workspace-cleanup skill wording (THR-259 seq171/seq185) ────

class TestComposeCleanupBriefSharedSkillWording:
    """The daemon-composed brief names the ONE shared workspace-cleanup skill
    and carries the approved seq171/seq185 current-use rule; the retired
    live-session/task-process identity veto is gone."""

    def _brief(self, run_number: int, *, thread_id: str | None = "THR-TEST") -> str:
        snapshot = wcs.WorkspaceContextSnapshot(
            available=True, workspaces_count=1, workspaces_bytes=1234,
            largest=[("dev_agent", 1234)], worktrees_registered=2,
            worktrees_terminal=1,
        )
        return wcs.compose_cleanup_brief(
            org_slug="test", agent="dev_agent", task_id="TASK-TEST-1",
            run_number=run_number, snapshot=snapshot, thread_id=thread_id,
        )

    def test_first_line_is_daemon_marker(self):
        for run in (1, 3):
            assert self._brief(run).splitlines()[0] == wcs._CLEANUP_BRIEF_MARKER

    def test_names_shared_skill_and_manual_marker(self):
        brief = self._brief(3)
        assert "runtime/skills/bundled/workspace-cleanup/SKILL.md" in brief
        assert "HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)" in brief

    def test_action_body_carries_seq171_185_rule_and_helper(self):
        brief = self._brief(3)
        assert "seq171/seq185" in brief
        assert "check_path_use.py" in brief
        assert "clear_observation" in brief
        assert "exact readable process name AND exact bounded cgroup role" in brief

    def test_retired_liveness_veto_absent(self):
        for run in (1, 3):
            brief = self._brief(run)
            assert "Liveness requires both runtime and OS checks" not in brief
            assert "validated executor PID identity" not in brief

    def test_first_two_runs_remain_report_only(self):
        for run in (1, 2):
            brief = self._brief(run)
            assert "STRICTLY REPORT-ONLY" in brief
            assert "No cleanup action is authorized" in brief

    def test_third_run_authorizes_non_force_only(self):
        brief = self._brief(3)
        assert "THIS RUN MAY PERFORM BOUNDED CLEANUP ACTIONS" in brief
        assert "without --force" in brief.replace("\n", " ")
