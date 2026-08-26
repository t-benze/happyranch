"""TASK-5684 (THR-190 PR-B): session-start containment tests.

Every production session-start family — task (``orchestrator._run_agent``),
thread (``thread_runner.run_invocation``), wake (``wake_runner.run_wake``),
dream (``dream_runner.run_dream``), schedule (``schedule_runner.run_schedule``)
— must fail BEFORE executor launch when a sandboxed agent has pre-positioned a
symlinked workspace/provider path (e.g. ``<ws>/.claude -> external``) that
would otherwise redirect the daemon's materializer writes/unlinks/replaces
outside the real workspace.

Each test drives the REAL production runner seam (not lookalike call
sequences), spies on the executor run surface, and asserts:
- the session persists a terminal failure with the containment error;
- the executor run spy is NEVER invoked;
- an EXTERNAL sentinel directory is byte- and state-identical;
- the pre-positioned symlink survives (not replaced/followed).

Threat principal: sandboxed Codex/Pi agents with workspace-write. An
unsandboxed Claude session is NOT the defended principal.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch as mock_patch

import pytest

from runtime.config import Settings
from runtime.models import (
    ScheduleKind,
    ScheduleRecord,
    ScheduleStatus,
    TaskStatus,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessageKind,
    ThreadRecord,
    WorkHourMode,
    WorkHourRecord,
    WorkHourStatus,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.host_supervisor import build_default_host_supervisor
from runtime.orchestrator.teams import TeamsRegistry

# ── Helpers (mirror tests/test_workspace_link_containment.py) ─────────


def _external_sentinel(base: Path, name: str = "external") -> Path:
    ext = base / name
    ext.mkdir()
    (ext / "sentinel.txt").write_bytes(b"SENTINEL\x00\x01\x02payload")
    (ext / "skills" / "start-task").mkdir(parents=True)
    (ext / "skills" / "decoy-skill").mkdir(parents=True)
    (ext / "skills" / "start-task" / "SKILL.md").write_bytes(
        b"# external start-task decoy\n"
    )
    (ext / "skills" / "decoy-skill" / "SKILL.md").write_bytes(
        b"# external decoy skill\n"
    )
    (ext / "withdraw-decoy.txt").write_bytes(b"do-not-unlink\x00")
    return ext


def _snapshot(path: Path) -> dict:
    import stat as _stat

    out: dict = {}
    for p in sorted(path.rglob("*")):
        rel = p.relative_to(path)
        try:
            st = p.lstat()
        except OSError:
            continue
        if _stat.S_ISLNK(st.st_mode):
            out[str(rel)] = ("link", os.readlink(p))
        elif _stat.S_ISDIR(st.st_mode):
            out[str(rel)] = ("dir",)
        else:
            out[str(rel)] = ("file", p.read_bytes())
    return out


def _assert_sentinel_unchanged(ext: Path, before: dict) -> None:
    after = _snapshot(ext)
    assert after == before, (
        "External sentinel changed:\n"
        f"  delta: {sorted(set(before) ^ set(after))}"
    )


def _install_listing_swap(
    monkeypatch,
    ws: Path,
    ext: Path,
    ancestor: str,
) -> list[bool]:
    """Deterministically swap *ancestor* to an EXTERNAL symlink at the exact
    post-admission/pre-listing seam (TASK-5715).

    The swap fires exactly ONCE, synchronously INSIDE the first fd-based
    enumeration of the admitted skills directory (``os.scandir(fd)`` in the
    corrected repair) — no timing, sleeps, or probabilistic interleaving.
    The original *ancestor* directory is renamed to ``<ancestor>.original``
    and the pathname is replaced by a symlink to *ext* (outside the
    workspace) — the attacker's swap. Returns a one-element list that flips
    to True once fired.
    """
    swapped: list[bool] = [False]
    real_scandir = os.scandir

    def _fire_once() -> None:
        if swapped[0]:
            return
        src = ws / ancestor
        os.rename(src, ws / f"{ancestor}.original")
        os.symlink(ext, src)
        swapped[0] = True

    def _scandir(path="."):
        if isinstance(path, int):
            _fire_once()
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", _scandir)
    return swapped


def _seed_agent(root: Path, name: str = "dev_agent") -> None:
    """Write an active AgentDef frontmatter under ``<root>/org/agents/``."""
    from runtime.orchestrator.agent_def import AgentDef, render_agent_text

    agents_dir = root / "org" / "agents"
    agents_dir.mkdir(parents=True, exist_ok=True)
    agent = AgentDef(
        name=name, team="engineering", role="worker",
        executor="claude", allow_rules=(), repos={},
        enrolled_by=None, enrolled_at_task=None, enrolled_at=None,
        system_prompt=f"You are {name}.", description="", model=None,
    )
    (agents_dir / f"{name}.md").write_text(render_agent_text(agent))


@pytest.fixture(autouse=True)
def _seed_dev_agent_for_session_start_containment(tmp_path):
    """Thread launch is fail-closed: an active AgentDef is required.

    The thread-family test drives ``run_invocation`` from a bare ``tmp_path``
    org root; seed the agent frontmatter there (the wake/dream/schedule
    family seeds its own org_state-rooted agent in-test).
    """
    from tests.conftest import seed_test_agents

    seed_test_agents(OrgPaths(root=tmp_path), ("engineering_head", "dev_agent"))


# ═══════════════════════════════════════════════════════════════════════
# TASK family — orchestrator._run_agent via the REAL run_step loop
# ═══════════════════════════════════════════════════════════════════════


class TestTaskStartContainment:
    def test_task_start_rejected_before_executor_launch(
        self, tmp_path, monkeypatch,
    ):
        """Pre-positioned <ws>/.claude -> external: task FAILED before launch.

        Exercises the REAL task runner (run_step -> run_step_impl ->
        _run_agent) with an executor run() spy; the sentinel stays intact.
        """
        import runtime.orchestrator.workspace_adapters as wa
        from runtime.infrastructure.database import Database
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.runtime import RuntimeDir

        # ── Source skills for the materializer (monkeypatched seam) ──
        src = tmp_path / "protocol" / "skills"
        for sid in [
            "create-skill", "start-task", "jobs", "make-worktree",
            "thread", "dream", "todos",
        ]:
            d = src / sid
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"# {sid}\nskill content\n")
        monkeypatch.setattr(wa, "_SKILLS_SRC", src)

        # ── Minimal orchestrator ──
        rt = RuntimeDir.init(tmp_path / "runtime")
        org_paths = OrgPaths(root=rt.orgs_dir / "test")
        org_paths.root.mkdir(parents=True, exist_ok=True)
        db = Database(org_paths.db_path)
        settings = Settings(project_root=tmp_path)
        teams = TeamsRegistry.load(org_paths.root)
        orch = Orchestrator(
            db=db, settings=settings, paths=org_paths, slug="test",
            teams=teams,
        )

        # Task launch is fail-closed: an active AgentDef is required.
        _seed_agent(org_paths.root, "dev_agent")

        workspace = org_paths.workspaces_dir / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "task_history.md").write_text("# Task History\n")
        (workspace / "repos" / "test" / ".git").mkdir(parents=True, exist_ok=True)

        # ── External sentinel + pre-positioned symlinked provider dir ──
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, workspace / ".claude")

        monkeypatch.setattr(orch, "_build_session_id", lambda: "sess-test")

        mock_executor = MagicMock()
        mock_executor.run = MagicMock(
            return_value=MagicMock(
                success=True, duration_seconds=1, session_id="sess-test",
            )
        )

        with mock_patch.object(orch, "_build_executor", return_value=mock_executor):
            task_id = orch.create_task(
                "containment test", team="engineering",
            )
            db.update_task(task_id, assigned_agent="dev_agent")
            orch.run_step(task_id)

        task = db.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED, (
            f"expected FAILED, got {task.status}"
        )
        note = task.note or ""
        assert "escaped_parent" in note, f"note: {note!r}"
        assert "agent invocation failed" in note

        # Executor run spy untouched — failure happened before launch
        mock_executor.run.assert_not_called()

        # External sentinel byte- and state-identical; symlink not replaced
        _assert_sentinel_unchanged(ext, before)
        assert (workspace / ".claude").is_symlink()
        assert os.readlink(workspace / ".claude") == str(ext)

    def test_task_start_ancestor_swap_after_admission_never_launches(
        self, tmp_path, monkeypatch,
    ):
        """A same-UID swap at the exact post-admission/pre-listing seam:
        repair enumerates only the admitted fd, fails closed at the writer's
        re-admission, the task FAILS, and the executor is never launched."""
        import runtime.orchestrator.workspace_adapters as wa
        from runtime.infrastructure.database import Database
        from runtime.orchestrator.orchestrator import Orchestrator
        from runtime.runtime import RuntimeDir

        src = tmp_path / "protocol" / "skills"
        for sid in [
            "create-skill", "start-task", "jobs", "make-worktree",
            "thread", "dream", "todos",
        ]:
            d = src / sid
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text(f"# {sid}\nskill content\n")
        monkeypatch.setattr(wa, "_SKILLS_SRC", src)

        rt = RuntimeDir.init(tmp_path / "runtime")
        org_paths = OrgPaths(root=rt.orgs_dir / "test")
        org_paths.root.mkdir(parents=True, exist_ok=True)
        db = Database(org_paths.db_path)
        settings = Settings(project_root=tmp_path)
        teams = TeamsRegistry.load(org_paths.root)
        orch = Orchestrator(
            db=db, settings=settings, paths=org_paths, slug="test",
            teams=teams,
        )

        _seed_agent(org_paths.root, "dev_agent")

        workspace = org_paths.workspaces_dir / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "task_history.md").write_text("# Task History\n")
        (workspace / "repos" / "test" / ".git").mkdir(parents=True, exist_ok=True)

        # GENUINE provider skills root — the attacker swaps an ALREADY-
        # ADMITTED ancestor at the post-admission/pre-listing seam instead.
        (workspace / ".claude" / "skills").mkdir(parents=True)
        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        _install_listing_swap(monkeypatch, workspace, ext, ".claude")

        monkeypatch.setattr(orch, "_build_session_id", lambda: "sess-test")

        mock_executor = MagicMock()
        mock_executor.run = MagicMock(
            return_value=MagicMock(
                success=True, duration_seconds=1, session_id="sess-test",
            )
        )

        with mock_patch.object(orch, "_build_executor", return_value=mock_executor):
            task_id = orch.create_task(
                "containment test", team="engineering",
            )
            db.update_task(task_id, assigned_agent="dev_agent")
            orch.run_step(task_id)

        task = db.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.FAILED, (
            f"expected FAILED, got {task.status}"
        )
        note = task.note or ""
        assert "escaped_parent" in note, f"note: {note!r}"

        # Executor run spy untouched — failure happened before launch
        mock_executor.run.assert_not_called()

        # External sentinel unchanged; the swap survives; the pinned original
        # provider root still exists (never written through the swap).
        _assert_sentinel_unchanged(ext, before)
        assert (workspace / ".claude").is_symlink()
        assert os.readlink(workspace / ".claude") == str(ext)
        assert (workspace / ".claude.original" / "skills").is_dir()


# ═══════════════════════════════════════════════════════════════════════
# THREAD family — thread_runner.run_invocation
# ═══════════════════════════════════════════════════════════════════════


class TestThreadStartContainment:
    def test_thread_start_rejected_before_executor_run(
        self, tmp_path, monkeypatch,
    ):
        """Pre-positioned <ws>/.claude -> external: invocation FAILED, no run."""
        from runtime.daemon import thread_runner as runner_mod
        from runtime.infrastructure.database import Database

        db = Database(tmp_path / "happyranch.db")
        db.insert_thread(ThreadRecord(id="THR-001", subject="containment"))
        db.add_thread_participant("THR-001", "engineering_head", added_by="founder")
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="please continue",
        )
        inv = db.mint_thread_invocation(
            thread_id="THR-001", agent_name="engineering_head",
            triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
        )

        ws = tmp_path / "workspaces" / "engineering_head"
        ws.mkdir(parents=True)
        (ws / "agent.yaml").write_text("executor: claude\n")

        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        os.symlink(ext, ws / ".claude")

        run_calls: list[str] = []

        class _SpyExecutor:
            def run(self, prompt, **kwargs):
                run_calls.append(prompt)
                return MagicMock(
                    success=True, error=None, returncode=0,
                    session_id="sess-x", duration_seconds=1,
                    agent_session_id=None, token_usage=None,
                )

        monkeypatch.setattr(
            runner_mod, "_build_executor_for_provider",
            lambda *_a, **_k: _SpyExecutor(),
        )

        class _OrgState:
            pass

        org = _OrgState()
        org.db = db
        org.root = tmp_path
        org.slug = "test-org"

        asyncio.run(runner_mod.run_invocation(
            org_state=org, invocation_token=inv.invocation_token,
            settings=Settings(),
        ))

        assert run_calls == [], "executor.run must never be invoked"

        invs = db.list_thread_invocations("THR-001")
        assert invs[0].status == ThreadInvocationStatus.FAILED
        assert "escaped_parent" in (invs[0].decline_reason or "")

        _assert_sentinel_unchanged(ext, before)
        assert (ws / ".claude").is_symlink()

    def test_thread_start_ancestor_swap_after_admission_never_runs(
        self, tmp_path, monkeypatch,
    ):
        """Thread family: swap at the post-admission/pre-listing seam —
        invocation FAILED, executor.run never invoked, sentinel intact."""
        from runtime.daemon import thread_runner as runner_mod
        from runtime.infrastructure.database import Database

        db = Database(tmp_path / "happyranch.db")
        db.insert_thread(ThreadRecord(id="THR-001", subject="containment"))
        db.add_thread_participant("THR-001", "engineering_head", added_by="founder")
        db.append_thread_message(
            thread_id="THR-001", speaker="founder",
            kind=ThreadMessageKind.MESSAGE, body_markdown="please continue",
        )
        inv = db.mint_thread_invocation(
            thread_id="THR-001", agent_name="engineering_head",
            triggering_seq=1, purpose=ThreadInvocationPurpose.BOOTSTRAP,
        )

        ws = tmp_path / "workspaces" / "engineering_head"
        ws.mkdir(parents=True)
        (ws / "agent.yaml").write_text("executor: claude\n")
        (ws / ".claude" / "skills").mkdir(parents=True)

        ext = _external_sentinel(tmp_path)
        before = _snapshot(ext)
        _install_listing_swap(monkeypatch, ws, ext, ".claude")

        run_calls: list[str] = []

        class _SpyExecutor:
            def run(self, prompt, **kwargs):
                run_calls.append(prompt)
                return MagicMock(
                    success=True, error=None, returncode=0,
                    session_id="sess-x", duration_seconds=1,
                    agent_session_id=None, token_usage=None,
                )

        monkeypatch.setattr(
            runner_mod, "_build_executor_for_provider",
            lambda *_a, **_k: _SpyExecutor(),
        )

        class _OrgState:
            pass

        org = _OrgState()
        org.db = db
        org.root = tmp_path
        org.slug = "test-org"

        asyncio.run(runner_mod.run_invocation(
            org_state=org, invocation_token=inv.invocation_token,
            settings=Settings(),
        ))

        assert run_calls == [], "executor.run must never be invoked"

        invs = db.list_thread_invocations("THR-001")
        assert invs[0].status == ThreadInvocationStatus.FAILED
        assert "escaped_parent" in (invs[0].decline_reason or "")

        _assert_sentinel_unchanged(ext, before)
        assert (ws / ".claude").is_symlink()
        assert os.readlink(ws / ".claude") == str(ext)
        assert (ws / ".claude.original" / "skills").is_dir()


# ═══════════════════════════════════════════════════════════════════════
# WAKE family — wake_runner.run_wake
# ═══════════════════════════════════════════════════════════════════════


class TestWakeStartContainment:
    def test_wake_start_rejected_before_executor_factory(
        self, org_state,
    ):
        """Pre-positioned <ws>/.claude -> external: wake FAILED, no executor."""
        from runtime.daemon.wake_runner import run_wake

        _seed_agent(org_state.root, "dev_agent")
        workspace = org_state.root / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        org_state.db.work_hours.insert(WorkHourRecord(
            id="WORKHOUR-001",
            agent_name="dev_agent",
            local_date="2026-06-11",
            slot="09:00",
            mode=WorkHourMode.WINDOWED,
            scheduled_for=datetime(2026, 6, 11, 1, 0, tzinfo=timezone.utc),
            status=WorkHourStatus.PENDING,
            routine_count=1,
        ))

        ext = _external_sentinel(org_state.root.parent.parent.parent)
        before = _snapshot(ext)
        os.symlink(ext, workspace / ".claude")

        factory_calls: list = []

        def factory(*args, **kwargs):
            factory_calls.append(args)
            return MagicMock()

        asyncio.run(run_wake(
            org_state=org_state,
            work_hour_id="WORKHOUR-001",
            settings=Settings(),
            executor_factory=factory,
        ))

        assert factory_calls == [], "executor factory must never be invoked"
        record = org_state.db.work_hours.get("WORKHOUR-001")
        assert record.status == WorkHourStatus.FAILED
        assert "escaped_parent" in (record.error or "")

        _assert_sentinel_unchanged(ext, before)
        assert (workspace / ".claude").is_symlink()

    def test_wake_start_ancestor_swap_after_admission_never_launches(
        self, org_state, monkeypatch,
    ):
        """Wake family: swap at the post-admission/pre-listing seam — work
        hour FAILED, executor factory never invoked, sentinel intact."""
        from runtime.daemon.wake_runner import run_wake

        _seed_agent(org_state.root, "dev_agent")
        workspace = org_state.root / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / ".claude" / "skills").mkdir(parents=True)
        org_state.db.work_hours.insert(WorkHourRecord(
            id="WORKHOUR-001",
            agent_name="dev_agent",
            local_date="2026-06-11",
            slot="09:00",
            mode=WorkHourMode.WINDOWED,
            scheduled_for=datetime(2026, 6, 11, 1, 0, tzinfo=timezone.utc),
            status=WorkHourStatus.PENDING,
            routine_count=1,
        ))

        ext = _external_sentinel(org_state.root.parent.parent.parent)
        before = _snapshot(ext)
        _install_listing_swap(monkeypatch, workspace, ext, ".claude")

        factory_calls: list = []

        def factory(*args, **kwargs):
            factory_calls.append(args)
            return MagicMock()

        asyncio.run(run_wake(
            org_state=org_state,
            work_hour_id="WORKHOUR-001",
            settings=Settings(),
            executor_factory=factory,
        ))

        assert factory_calls == [], "executor factory must never be invoked"
        record = org_state.db.work_hours.get("WORKHOUR-001")
        assert record.status == WorkHourStatus.FAILED
        assert "escaped_parent" in (record.error or "")

        _assert_sentinel_unchanged(ext, before)
        assert (workspace / ".claude").is_symlink()
        assert os.readlink(workspace / ".claude") == str(ext)
        assert (workspace / ".claude.original" / "skills").is_dir()


# ═══════════════════════════════════════════════════════════════════════
# DREAM family — dream_runner.run_dream
# ═══════════════════════════════════════════════════════════════════════


class TestDreamStartContainment:
    def test_dream_start_rejected_before_executor_factory(
        self, org_state,
    ):
        """Pre-positioned <ws>/.claude -> external: dream FAILED, no executor."""
        from runtime.daemon.dream_runner import run_dream
        from runtime.models import DreamRecord

        _seed_agent(org_state.root, "dev_agent")
        workspace = org_state.root / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        org_state.db.insert_dream(DreamRecord(
            id="DREAM-001",
            agent_name="dev_agent",
            local_date="2026-06-09",
            scheduled_for=datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc),
            window_start=datetime(2026, 6, 9, 1, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc),
        ))

        ext = _external_sentinel(org_state.root.parent.parent.parent)
        before = _snapshot(ext)
        os.symlink(ext, workspace / ".claude")

        factory_calls: list = []

        def factory(*args, **kwargs):
            factory_calls.append(args)
            return MagicMock()

        asyncio.run(run_dream(
            org_state=org_state,
            dream_id="DREAM-001",
            settings=Settings(),
            executor_factory=factory,
        ))

        assert factory_calls == [], "executor factory must never be invoked"
        dream = org_state.db.get_dream("DREAM-001")
        assert dream.status == "failed"
        assert "escaped_parent" in (dream.error or "")

        _assert_sentinel_unchanged(ext, before)
        assert (workspace / ".claude").is_symlink()

    def test_dream_start_ancestor_swap_after_admission_never_launches(
        self, org_state, monkeypatch,
    ):
        """Dream family: swap at the post-admission/pre-listing seam — dream
        FAILED, executor factory never invoked, sentinel intact."""
        from runtime.daemon.dream_runner import run_dream
        from runtime.models import DreamRecord

        _seed_agent(org_state.root, "dev_agent")
        workspace = org_state.root / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / ".claude" / "skills").mkdir(parents=True)
        org_state.db.insert_dream(DreamRecord(
            id="DREAM-001",
            agent_name="dev_agent",
            local_date="2026-06-09",
            scheduled_for=datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc),
            window_start=datetime(2026, 6, 9, 1, 0, tzinfo=timezone.utc),
            window_end=datetime(2026, 6, 9, 2, 0, tzinfo=timezone.utc),
        ))

        ext = _external_sentinel(org_state.root.parent.parent.parent)
        before = _snapshot(ext)
        _install_listing_swap(monkeypatch, workspace, ext, ".claude")

        factory_calls: list = []

        def factory(*args, **kwargs):
            factory_calls.append(args)
            return MagicMock()

        asyncio.run(run_dream(
            org_state=org_state,
            dream_id="DREAM-001",
            settings=Settings(),
            executor_factory=factory,
        ))

        assert factory_calls == [], "executor factory must never be invoked"
        dream = org_state.db.get_dream("DREAM-001")
        assert dream.status == "failed"
        assert "escaped_parent" in (dream.error or "")

        _assert_sentinel_unchanged(ext, before)
        assert (workspace / ".claude").is_symlink()
        assert os.readlink(workspace / ".claude") == str(ext)
        assert (workspace / ".claude.original" / "skills").is_dir()


# ═══════════════════════════════════════════════════════════════════════
# SCHEDULE family — schedule_runner.run_schedule
# ═══════════════════════════════════════════════════════════════════════


class TestScheduleStartContainment:
    def test_schedule_start_rejected_before_executor_factory(
        self, org_state,
    ):
        """Pre-positioned <ws>/.claude -> external: schedule FAILED, no run."""
        from runtime.daemon.schedule_runner import run_schedule

        _seed_agent(org_state.root, "dev_agent")
        workspace = org_state.root / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        now = datetime.now(timezone.utc)
        org_state.db.schedules.insert(ScheduleRecord(
            id="SCHEDULE-001",
            agent_name="dev_agent",
            team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=now - timedelta(minutes=5),
            timezone="UTC",
            normalized_brief="Test brief",
            source_instruction="Test source instruction",
            status=ScheduleStatus.FIRING,
        ))

        ext = _external_sentinel(org_state.root.parent.parent.parent)
        before = _snapshot(ext)
        os.symlink(ext, workspace / ".claude")

        factory_calls: list = []

        def factory(*args, **kwargs):
            factory_calls.append(args)
            return MagicMock()

        asyncio.run(run_schedule(
            org_state=org_state,
            schedule_id="SCHEDULE-001",
            settings=Settings(),
            executor_factory=factory,
            host_supervisor=build_default_host_supervisor(),
        ))

        assert factory_calls == [], "executor factory must never be invoked"
        record = org_state.db.schedules.get("SCHEDULE-001")
        assert record.status == ScheduleStatus.FAILED
        assert "escaped_parent" in (record.error or "")

        _assert_sentinel_unchanged(ext, before)
        assert (workspace / ".claude").is_symlink()

    def test_schedule_start_ancestor_swap_after_admission_never_launches(
        self, org_state, monkeypatch,
    ):
        """Schedule family: swap at the post-admission/pre-listing seam —
        schedule FAILED, executor factory never invoked, sentinel intact."""
        from runtime.daemon.schedule_runner import run_schedule

        _seed_agent(org_state.root, "dev_agent")
        workspace = org_state.root / "workspaces" / "dev_agent"
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / ".claude" / "skills").mkdir(parents=True)
        now = datetime.now(timezone.utc)
        org_state.db.schedules.insert(ScheduleRecord(
            id="SCHEDULE-001",
            agent_name="dev_agent",
            team="engineering",
            kind=ScheduleKind.ONE_SHOT,
            fire_at=now - timedelta(minutes=5),
            timezone="UTC",
            normalized_brief="Test brief",
            source_instruction="Test source instruction",
            status=ScheduleStatus.FIRING,
        ))

        ext = _external_sentinel(org_state.root.parent.parent.parent)
        before = _snapshot(ext)
        _install_listing_swap(monkeypatch, workspace, ext, ".claude")

        factory_calls: list = []

        def factory(*args, **kwargs):
            factory_calls.append(args)
            return MagicMock()

        asyncio.run(run_schedule(
            org_state=org_state,
            schedule_id="SCHEDULE-001",
            settings=Settings(),
            executor_factory=factory,
            host_supervisor=build_default_host_supervisor(),
        ))

        assert factory_calls == [], "executor factory must never be invoked"
        record = org_state.db.schedules.get("SCHEDULE-001")
        assert record.status == ScheduleStatus.FAILED
        assert "escaped_parent" in (record.error or "")

        _assert_sentinel_unchanged(ext, before)
        assert (workspace / ".claude").is_symlink()
        assert os.readlink(workspace / ".claude") == str(ext)
        assert (workspace / ".claude.original" / "skills").is_dir()
