import re
from unittest.mock import patch

import pytest

from src.daemon.agent_config import set_executor, write_default_agent_config
from src.infrastructure.database import Database
from src.models import (
    TaskStatus,
    TaskType,
)
from src.orchestrator.executors import ExecutorResult
from src.orchestrator.orchestrator import Orchestrator


@pytest.fixture
def orchestrator(test_settings, test_runtime):
    db = Database(test_runtime.db_path)
    return Orchestrator(db=db, settings=test_settings, runtime=test_runtime)


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


def test_orchestrator_no_longer_has_run_task():
    """run_task was removed in favor of the async run_step queue model."""
    from src.orchestrator.orchestrator import Orchestrator
    assert not hasattr(Orchestrator, "run_task")


def test_create_task(orchestrator):
    task_id = orchestrator.create_task(TaskType.GENERAL, "Explore the codebase")
    assert task_id == "TASK-001"
    task = orchestrator._db.get_task(task_id)
    assert task.status == TaskStatus.PENDING
    assert task.brief == "Explore the codebase"


def test_create_task_with_type(orchestrator):
    task_id = orchestrator.create_task(TaskType.IMPLEMENT_FEATURE, "Add Alipay")
    task = orchestrator._db.get_task(task_id)
    assert task.type == TaskType.IMPLEMENT_FEATURE


def test_task_metadata_in_agent_prompt(orchestrator, test_runtime, monkeypatch):
    """Agent prompts should include task_id, session_id, and brief.

    Covers the prompt-assembly contract in `_run_agent` — the start-task skill
    parses these keys out of the injected parameters block.
    """
    _setup_workspaces(test_runtime)

    task_id = orchestrator.create_task(TaskType.GENERAL, "Explore payments")

    # Fix the session_id so the prompt is deterministic.
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("src.orchestrator.orchestrator.ClaudeExecutor") as MockExecutor:
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


def test_codex_agent_prompt_uses_provider_specific_wording(
    orchestrator, test_runtime, monkeypatch,
):
    _setup_codex_workspace(test_runtime, "engineering_head")
    task_id = orchestrator.create_task(TaskType.GENERAL, "Explore payments")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("src.orchestrator.orchestrator.CodexExecutor") as MockExecutor:
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
    `opc report-completion` callback hits 409 unknown_session and the task
    silently fails with note='agent session failed'."""
    from src.daemon.sessions import SessionTracker

    _setup_workspaces(test_runtime)
    tracker = SessionTracker()
    orchestrator.attach_sessions(tracker)

    task_id = orchestrator.create_task(TaskType.GENERAL, "Explore payments")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("src.orchestrator.orchestrator.ClaudeExecutor") as MockExecutor:
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

    task_id = orchestrator.create_task(TaskType.GENERAL, "Explore payments")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")
    captured: list[tuple[str, str, str]] = []

    with patch("src.orchestrator.orchestrator.ClaudeExecutor") as MockExecutor:
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
    from src.orchestrator.orchestrator import WorkspaceNotInitialized

    task_id = orchestrator.create_task(TaskType.GENERAL, "ping")
    eh_workspace = test_runtime.workspaces_dir / "engineering_head"
    assert not eh_workspace.exists()

    with pytest.raises(WorkspaceNotInitialized) as exc_info:
        orchestrator._run_agent(task_id, "engineering_head", "any prompt")

    msg = str(exc_info.value)
    assert "engineering_head" in msg
    assert "opc init-agent engineering_head" in msg
    # The executor must never have been invoked against a broken workspace.
    assert not (eh_workspace / ".claude" / "skills" / "start-task" / "SKILL.md").exists()


def test_run_agent_accepts_codex_readiness_marker(orchestrator, test_runtime, monkeypatch):
    _setup_codex_workspace(test_runtime, "engineering_head")
    task_id = orchestrator.create_task(TaskType.GENERAL, "ping")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("src.orchestrator.orchestrator.CodexExecutor") as MockExecutor:
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


def test_run_agent_defaults_missing_executor_to_claude(orchestrator, test_runtime, monkeypatch):
    workspace = test_runtime.workspaces_dir / "engineering_head"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "task_history.md").write_text("# Task History: engineering_head\n\n")
    (workspace / "agent.yaml").write_text("repos: {}\n")
    skill = workspace / ".claude" / "skills" / "start-task"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text("# start-task\n")

    task_id = orchestrator.create_task(TaskType.GENERAL, "ping")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: "sess-eh")

    with patch("src.orchestrator.orchestrator.ClaudeExecutor") as MockExecutor:
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

    orchestrator.create_task(TaskType.GENERAL, "Add Alipay support")
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

    orchestrator.create_task(TaskType.GENERAL, "Review Q1 project status")
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

    orchestrator.create_task(TaskType.GENERAL, "First task")
    orchestrator._db.update_task(
        "TASK-001",
        assigned_agent="engineering_head",
        status=TaskStatus.COMPLETED,
        note="first",
    )
    orchestrator._update_task_history("TASK-001")

    orchestrator.create_task(TaskType.GENERAL, "Second task")
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


def test_read_completion_from_db_preserves_artifact_dir(orchestrator):
    """Reconstructing a CompletionReport from task_results must include
    artifact_dir so the daemon-callback path can persist tasks.final_artifact_dir."""
    orchestrator.create_task(TaskType.GENERAL, "Write the report")
    orchestrator._db.insert_task_result(
        "TASK-001",
        "dev_agent",
        "sess-xyz",
        output_summary="Report done",
        confidence_score=85,
        artifact_dir="artifacts/TASK-001",
    )

    report = orchestrator._read_completion_from_db("TASK-001", "dev_agent", "sess-xyz")
    assert report is not None
    assert report.artifact_dir == "artifacts/TASK-001"
