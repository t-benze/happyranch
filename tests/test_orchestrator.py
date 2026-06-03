import re
from unittest.mock import patch

import pytest

from runtime.daemon.agent_config import set_executor, write_default_agent_config
from runtime.infrastructure.database import Database
from runtime.models import (
    TaskStatus,
)
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry


@pytest.fixture
def orchestrator(test_settings, test_runtime):
    test_runtime.root.mkdir(parents=True, exist_ok=True)
    db = Database(test_runtime.db_path)
    teams = TeamsRegistry.load(test_runtime.root)
    return Orchestrator(
        db=db, settings=test_settings,
        paths=test_runtime, slug="test", teams=teams,
    )


_DEFAULT_AGENTS = ["engineering_head", "product_manager", "dev_agent", "payment_agent"]

def _setup_workspaces(runtime, agents: list[str] | None = None):
    for agent in (agents or _DEFAULT_AGENTS):
        ws = runtime.workspaces_dir / agent
        ws.mkdir(parents=True, exist_ok=True)
        (ws / "task_history.md").write_text(f"# Task History: {agent}\n\n")
        skill = ws / ".claude" / "skills" / "start-task"
        skill.mkdir(parents=True, exist_ok=True)
        (skill / "SKILL.md").write_text("# start-task\n")


def _setup_codex_workspace(runtime, agent: str) -> None:
    ws = runtime.workspaces_dir / agent
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "task_history.md").write_text(f"# Task History: {agent}\n\n")
    write_default_agent_config(ws)
    set_executor(ws, "codex")
    (ws / "AGENTS.md").write_text(f"# Agent: {agent}\n")


def _setup_opencode_workspace(runtime, agent: str) -> None:
    ws = runtime.workspaces_dir / agent
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "task_history.md").write_text(f"# Task History: {agent}\n\n")
    write_default_agent_config(ws)
    set_executor(ws, "opencode")
    (ws / "AGENTS.md").write_text(f"# Agent: {agent}\n")


def _setup_pi_workspace(runtime, agent: str) -> None:
    ws = runtime.workspaces_dir / agent
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "task_history.md").write_text(f"# Task History: {agent}\n\n")
    write_default_agent_config(ws)
    set_executor(ws, "pi")
    (ws / "AGENTS.md").write_text(f"# Agent: {agent}\n")


def test_orchestrator_no_longer_has_run_task():
    """run_task was removed in favor of the async run_step queue model."""
    from runtime.orchestrator.orchestrator import Orchestrator
    assert not hasattr(Orchestrator, "run_task")


def test_create_task(orchestrator):
    task_id = orchestrator.create_task("Explore the codebase")
    assert task_id == "TASK-001"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.PENDING
    assert task.brief == "Explore the codebase"


def test_create_task_with_team(orchestrator):
    task_id = orchestrator.create_task("Add Alipay", team="engineering")
    task = orchestrator._db.get_task(task_id)
    assert task.team == "engineering"


def test_task_metadata_in_agent_prompt(orchestrator, test_runtime, monkeypatch):
    """Agent prompts should include task_id, session_id, and brief.

    Covers the prompt-assembly contract in `_run_agent` — the start-task skill
    parses these keys out of the injected parameters block.
    """
    _setup_workspaces(test_runtime)

    task_id = orchestrator.create_task("Explore payments")

    # Fix the session_id so the prompt is deterministic.
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("runtime.orchestrator.orchestrator.ClaudeExecutor") as MockExecutor:
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = ExecutorResult(
            success=True,
            duration_seconds=30,
            session_id="sess-eh",
        )

        orchestrator._run_agent(task_id, "engineering_head", "Decide what to do next")

        prompt = mock_executor.run.call_args.kwargs["prompt"]
        assert "Use the start-task skill" in prompt
        assert "task_id: TASK-001" in prompt
        assert "brief: Explore payments" in prompt
        assert "session_id:" in prompt
        assert "role_guidance:" in prompt
        # Regression guard: the brief must appear exactly once. Before the
        # role_guidance / capabilities cleanup the brief was rendered both in
        # ``Parameters.brief`` and at the top of the capabilities block
        # (``# Task\n<brief>``), doubling the brief on every manager spawn.
        assert prompt.count("Explore payments") == 1


def test_worker_prompt_omits_role_guidance_block(
    orchestrator, test_runtime, monkeypatch,
):
    """Worker spawns receive only ``Parameters.brief`` — no ``role_guidance:``
    line. Before the cleanup, ``run_step._build_agent_prompt`` returned
    ``task.brief`` for workers, which the outer wrapper then re-rendered
    under ``role_guidance: |``, duplicating the brief in every worker spawn.
    """
    _setup_workspaces(test_runtime)
    task_id = orchestrator.create_task("Implement Alipay webhook")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-dev")

    with patch("runtime.orchestrator.orchestrator.ClaudeExecutor") as MockExecutor:
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = ExecutorResult(
            success=True,
            duration_seconds=30,
            session_id="sess-dev",
        )

        # Worker case: inner run_step._build_agent_prompt returns "" for
        # non-managers; _run_agent's outer wrapper must omit the line.
        orchestrator._run_agent(task_id, "dev_agent", "")

        prompt = mock_executor.run.call_args.kwargs["prompt"]
        assert "brief: Implement Alipay webhook" in prompt
        assert prompt.count("Implement Alipay webhook") == 1
        assert "role_guidance:" not in prompt
        # No dangling block-scalar marker should be left behind.
        assert "  |\n" not in prompt


def test_codex_agent_prompt_uses_provider_specific_wording(
    orchestrator, test_runtime, monkeypatch,
):
    _setup_codex_workspace(test_runtime, "engineering_head")
    task_id = orchestrator.create_task("Explore payments")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("runtime.orchestrator.orchestrator.CodexExecutor") as MockExecutor:
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = ExecutorResult(
            success=True,
            duration_seconds=30,
            session_id="sess-eh",
        )

        orchestrator._run_agent(task_id, "engineering_head", "Decide what to do next")

        prompt = mock_executor.run.call_args.kwargs["prompt"]
        assert "Use the start-task skill" not in prompt
        assert "Use the injected task parameters directly" in prompt
        assert "task_id: TASK-001" in prompt
        assert "brief: Explore payments" in prompt


def test_run_agent_registers_active_session_when_tracker_attached(
    orchestrator, test_runtime, monkeypatch,
):
    """Regression for the 8581f26 bug: when the daemon's SessionTracker is
    attached, `_run_agent` must call `set_active(task_id, agent, session_id)`
    BEFORE the subprocess starts. Without this, the agent's
    `happyranch report-completion` callback hits 409 unknown_session and the task
    silently fails with note='agent session failed'."""
    from runtime.daemon.sessions import SessionTracker

    _setup_workspaces(test_runtime)
    tracker = SessionTracker()
    orchestrator.attach_sessions(tracker)

    task_id = orchestrator.create_task("Explore payments")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("runtime.orchestrator.orchestrator.ClaudeExecutor") as MockExecutor:
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = ExecutorResult(
            success=True, duration_seconds=1, session_id="sess-eh",
        )
        orchestrator._run_agent(task_id, "engineering_head", "any prompt")

    assert tracker.get_active(task_id, "engineering_head") == "sess-eh"


def test_run_agent_skips_session_registration_when_tracker_not_attached(
    orchestrator, test_runtime, monkeypatch,
):
    """Tests construct an Orchestrator without attaching a tracker. The call
    must not raise, and the explicit `on_session_started` callback path must
    still fire so legacy test fixtures keep working."""
    _setup_workspaces(test_runtime)
    assert orchestrator._sessions is None

    task_id = orchestrator.create_task("Explore payments")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")
    captured: list[tuple[str, str, str]] = []

    with patch("runtime.orchestrator.orchestrator.ClaudeExecutor") as MockExecutor:
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = ExecutorResult(
            success=True, duration_seconds=1, session_id="sess-eh",
        )
        orchestrator._run_agent(
            task_id, "engineering_head", "any prompt",
            on_session_started=lambda t, a, s: captured.append((t, a, s)),
        )

    assert captured == [(task_id, "engineering_head", "sess-eh")]


def test_run_agent_fails_fast_when_workspace_missing_skill(orchestrator, test_runtime):
    """Workspace bootstrap is an explicit, operator-driven step. If the
    start-task skill file is missing, the orchestrator should raise an
    actionable error instead of silently marking the task rejected."""
    from runtime.orchestrator.orchestrator import WorkspaceNotInitialized

    task_id = orchestrator.create_task("ping")
    eh_workspace = test_runtime.workspaces_dir / "engineering_head"
    assert not eh_workspace.exists()

    with pytest.raises(WorkspaceNotInitialized) as exc_info:
        orchestrator._run_agent(task_id, "engineering_head", "any prompt")

    msg = str(exc_info.value)
    assert "engineering_head" in msg
    assert "happyranch init-agent engineering_head" in msg
    # The executor must never have been invoked against a broken workspace.
    assert not (eh_workspace / ".claude" / "skills" / "start-task" / "SKILL.md").exists()


def test_run_agent_accepts_codex_readiness_marker(orchestrator, test_runtime, monkeypatch):
    _setup_codex_workspace(test_runtime, "engineering_head")
    task_id = orchestrator.create_task("ping")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("runtime.orchestrator.orchestrator.CodexExecutor") as MockExecutor:
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = ExecutorResult(
            success=True,
            duration_seconds=1,
            session_id="sess-eh",
        )

        result, report = orchestrator._run_agent(task_id, "engineering_head", "any prompt")

    assert result.success is True
    assert report is None
    assert mock_executor.run.call_count == 1


def test_run_agent_routes_opencode_workspace_to_opencode_executor(
    orchestrator, test_runtime, monkeypatch,
):
    """An opencode-configured workspace must dispatch to OpencodeExecutor,
    not Claude or Codex. Readiness is the AGENTS.md marker — same as Codex —
    because opencode reads AGENTS.md and discovers skills under
    .agents/skills/."""
    _setup_opencode_workspace(test_runtime, "engineering_head")
    task_id = orchestrator.create_task("ping")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("runtime.orchestrator.orchestrator.OpencodeExecutor") as MockExecutor:
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = ExecutorResult(
            success=True,
            duration_seconds=1,
            session_id="sess-eh",
        )

        result, report = orchestrator._run_agent(task_id, "engineering_head", "any prompt")

    assert result.success is True
    assert report is None
    assert mock_executor.run.call_count == 1
    # opencode shares the Claude-style "use the start-task skill" nudge
    # because opencode's `skill` tool resolves the skill on demand.
    prompt = mock_executor.run.call_args.kwargs["prompt"]
    assert "Use the start-task skill" in prompt
    assert "Use the injected task parameters directly" not in prompt


def test_run_agent_routes_pi_workspace_to_pi_executor(
    orchestrator, test_runtime, monkeypatch,
):
    _setup_pi_workspace(test_runtime, "engineering_head")
    task_id = orchestrator.create_task("ping")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("runtime.orchestrator.orchestrator.PiExecutor") as MockExecutor:
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = ExecutorResult(
            success=True,
            duration_seconds=1,
            session_id="sess-eh",
        )

        result, report = orchestrator._run_agent(task_id, "engineering_head", "any prompt")

    assert result.success is True
    assert report is None
    assert mock_executor.run.call_count == 1
    prompt = mock_executor.run.call_args.kwargs["prompt"]
    assert "Use the start-task skill" in prompt
    assert "Use the injected task parameters directly" not in prompt


def test_run_agent_defaults_missing_executor_to_claude(orchestrator, test_runtime, monkeypatch):
    workspace = test_runtime.workspaces_dir / "engineering_head"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "task_history.md").write_text("# Task History: engineering_head\n\n")
    (workspace / "agent.yaml").write_text("repos: {}\n")
    skill = workspace / ".claude" / "skills" / "start-task"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("# start-task\n")

    task_id = orchestrator.create_task("ping")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("runtime.orchestrator.orchestrator.ClaudeExecutor") as MockExecutor:
        mock_executor = MockExecutor.return_value
        mock_executor.run.return_value = ExecutorResult(
            success=True,
            duration_seconds=1,
            session_id="sess-eh",
        )

        result, _ = orchestrator._run_agent(task_id, "engineering_head", "any prompt")

    assert result.success is True
    assert mock_executor.run.call_count == 1


def test_task_history_written_per_agent_only(orchestrator, test_runtime):
    """_update_task_history writes the file to the assigned_agent's workspace only."""
    _setup_workspaces(test_runtime)

    orchestrator.create_task("Add Alipay support")
    orchestrator._db.update_task(
        "TASK-001",
        assigned_agent="dev_agent",
        status=TaskStatus.COMPLETED,
        note="dev did it",
    )
    orchestrator._update_task_history("TASK-001")

    dev_hist = (test_runtime.workspaces_dir / "dev_agent" / "task_history.md").read_text()
    pm_hist = (test_runtime.workspaces_dir / "product_manager" / "task_history.md").read_text()

    assert "TASK-001" in dev_hist
    assert "TASK-001" not in pm_hist


def test_task_history_entry_format(orchestrator, test_runtime):
    """task_history.md entries follow the `**TASK-id** (date, status) — brief` format."""
    _setup_workspaces(test_runtime)

    orchestrator.create_task("Review Q1 project status")
    orchestrator._db.update_task(
        "TASK-001",
        assigned_agent="engineering_head",
        status=TaskStatus.COMPLETED,
        note="Reviewed Q1. Three risks, five actions.",
    )
    orchestrator._update_task_history("TASK-001")

    hist = (test_runtime.workspaces_dir / "engineering_head" / "task_history.md").read_text()
    assert re.search(r"\*\*TASK-001\*\* \(\d{4}-\d{2}-\d{2}, completed\) — Review Q1", hist)
    assert "Outcome: Reviewed Q1. Three risks, five actions." in hist
    assert "Artifact:" not in hist


def test_task_history_newest_first(orchestrator, test_runtime):
    """task_history.md lists entries newest-first."""
    _setup_workspaces(test_runtime)

    orchestrator.create_task("First task")
    orchestrator._db.update_task(
        "TASK-001",
        assigned_agent="engineering_head",
        status=TaskStatus.COMPLETED,
        note="first",
    )
    orchestrator._update_task_history("TASK-001")

    orchestrator.create_task("Second task")
    orchestrator._db.update_task(
        "TASK-002",
        assigned_agent="engineering_head",
        status=TaskStatus.COMPLETED,
        note="second",
    )
    orchestrator._update_task_history("TASK-002")

    hist = (test_runtime.workspaces_dir / "engineering_head" / "task_history.md").read_text()
    idx2 = hist.index("TASK-002")
    idx1 = hist.index("TASK-001")
    assert idx2 < idx1


def test_read_completion_from_db_preserves_output_dir(orchestrator):
    """Reconstructing a CompletionReport from task_results must include
    output_dir so the daemon-callback path can persist tasks.final_output_dir."""
    orchestrator.create_task("Write the report")
    orchestrator._db.insert_task_result(
        "TASK-001",
        "dev_agent",
        "sess-xyz",
        output_summary="Report done",
        confidence_score=85,
        output_dir="output/TASK-001",
    )

    report = orchestrator._read_completion_from_db("TASK-001", "dev_agent", "sess-xyz")
    assert report is not None
    assert report.output_dir == "output/TASK-001"


def test_read_completion_from_db_hydrates_decision(orchestrator):
    """EH's structured decision is persisted as JSON on task_results.decision_json
    and must be rehydrated into report.decision as a NextStep so the parser
    consumes it directly — no prose inference."""
    import json as _json
    from runtime.models import NextStep

    orchestrator.create_task("Clean up stale PR/issue")
    orchestrator._db.insert_task_result(
        "TASK-001",
        "engineering_head",
        "sess-eh",
        output_summary="Prose recap: closed issue #93 and PR #105.",
        confidence_score=95,
        decision_json=_json.dumps({
            "action": "done",
            "summary": "Cleanup complete.",
        }),
    )

    report = orchestrator._read_completion_from_db(
        "TASK-001", "engineering_head", "sess-eh",
    )
    assert report is not None
    assert isinstance(report.decision, NextStep)
    assert report.decision.action == "done"
    assert report.decision.summary == "Cleanup complete."
    # Prose summary still round-trips unchanged — separating the two is the
    # whole point of the decision field.
    assert "closed issue #93" in report.output_summary


def test_parse_next_step_prefers_decision_field_over_prose(orchestrator):
    """TASK-071 regression: when `decision` is populated, the parser must use
    it verbatim and never fall through to JSON-decoding the prose summary.
    This is the fix that eliminates the double-encoding trap — prose in
    `output_summary` is no longer an escalation trigger if the structured
    decision is present.
    """
    from runtime.models import CompletionReport, NextStep

    # Prose output_summary + structured decision — the TASK-071 shape.
    report = CompletionReport(
        task_id="TASK-071",
        agent="engineering_head",
        status="completed",
        confidence=98,
        output_summary=(
            "Cleanup pass complete. Issue #93 closed with reason=completed "
            "plus resolution comment. Stale PR #105 closed."
        ),
        decision=NextStep(action="done", summary="Cleanup landed."),
    )
    decision = orchestrator._parse_next_step(report)
    assert decision.action == "done"
    assert decision.summary == "Cleanup landed."


def test_parse_next_step_legacy_path_still_works_for_json_in_output_summary(
    orchestrator,
):
    """Workspaces on older skill copies keep speaking the pre-TASK-071
    contract: JSON decision embedded directly in output_summary, no `decision`
    field. Parser must continue to honor that during the transition."""
    import json as _json
    from runtime.models import CompletionReport

    report = CompletionReport(
        task_id="TASK-050",
        agent="engineering_head",
        status="completed",
        confidence=80,
        output_summary=_json.dumps({
            "action": "delegate",
            "agent": "dev_agent",
            "prompt": "Implement X",
        }),
        decision=None,
    )
    decision = orchestrator._parse_next_step(report)
    assert decision.action == "delegate"
    assert decision.agent == "dev_agent"
    assert decision.prompt == "Implement X"


def test_parse_next_step_prose_without_decision_still_escalates(orchestrator):
    """The guardrail against silent-approve-from-prose (TASK-013 / TASK-016)
    must remain: if EH sends prose AND omits `decision`, escalate. The new
    escalation message points at the missing `decision` field so the fix is
    obvious from the audit log."""
    from runtime.models import CompletionReport

    report = CompletionReport(
        task_id="TASK-099",
        agent="engineering_head",
        status="completed",
        confidence=80,
        output_summary="Delegating to dev_agent now.",
        decision=None,
    )
    decision = orchestrator._parse_next_step(report)
    assert decision.action == "escalate"
    assert "decision" in (decision.reason or "").lower()


def test_read_completion_from_db_tolerates_garbage_decision_json(orchestrator):
    """A corrupt decision_json row must not crash the orchestrator — leave
    decision None so the parser escalates with a readable reason, rather than
    silently falling through to prose inference."""
    orchestrator.create_task("Task with corrupt row")
    orchestrator._db.insert_task_result(
        "TASK-001",
        "engineering_head",
        "sess-eh",
        output_summary="prose",
        confidence_score=70,
        decision_json="not-json{",
    )
    report = orchestrator._read_completion_from_db(
        "TASK-001", "engineering_head", "sess-eh",
    )
    assert report is not None
    assert report.decision is None


def test_orchestrator_requires_teams() -> None:
    import pytest
    from pathlib import Path
    from runtime.config import Settings
    from runtime.infrastructure.database import Database
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.runtime import RuntimeDir
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        rt = RuntimeDir.init(Path(td) / "rt")
        paths = OrgPaths(root=rt.orgs_dir / "x")
        db = Database(paths.db_path)
        settings = Settings()
        with pytest.raises(TypeError):
            Orchestrator(db=db, settings=settings, paths=paths, slug="x")  # missing teams


def test_orchestrator_notifier_default_none(tmp_path, test_settings):
    from runtime.infrastructure.database import Database
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.teams import TeamsRegistry

    root = tmp_path / "orgs" / "x"
    root.mkdir(parents=True)
    db = Database(root / "happyranch.db")
    orch = Orchestrator(
        db=db, settings=test_settings,
        paths=OrgPaths(root=root), slug="x",
        teams=TeamsRegistry.load(root),
    )
    assert orch._notifier is None


def test_orchestrator_notify_escalated_no_op_when_unset(tmp_path, test_settings):
    from runtime.infrastructure.database import Database
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.teams import TeamsRegistry

    root = tmp_path / "orgs" / "x"
    root.mkdir(parents=True)
    db = Database(root / "happyranch.db")
    orch = Orchestrator(
        db=db, settings=test_settings,
        paths=OrgPaths(root=root), slug="x",
        teams=TeamsRegistry.load(root),
    )
    orch.notify_escalated(task_id="TASK-X", agent="a", reason="r")  # must not raise


def test_orchestrator_notify_does_not_block_synchronous_caller(tmp_path, test_settings):
    """When called from a thread without an event loop, notify_escalated
    must spawn a background worker rather than blocking on asyncio.run."""
    import threading
    import time

    from runtime.infrastructure.database import Database
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.teams import TeamsRegistry

    root = tmp_path / "orgs" / "x"
    root.mkdir(parents=True)
    db = Database(root / "happyranch.db")
    orch = Orchestrator(
        db=db, settings=test_settings,
        paths=OrgPaths(root=root), slug="x",
        teams=TeamsRegistry.load(root),
    )

    started = threading.Event()
    finish = threading.Event()
    finished = threading.Event()

    class _SlowNotifier:
        async def notify_escalated(self, **kwargs):
            started.set()
            finish.wait(timeout=5.0)
            finished.set()

    orch.attach_notifier(_SlowNotifier())

    t0 = time.monotonic()
    orch.notify_escalated(task_id="TASK-X", agent="a", reason="r")
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0, f"notify_escalated blocked for {elapsed:.2f}s"
    assert started.wait(timeout=2.0), "background notifier never ran"
    finish.set()
    assert finished.wait(timeout=2.0)
