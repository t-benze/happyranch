"""Owner-boundary coverage for the task-session teardown audit order."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.agent_def import AgentDef, render_agent_text
from runtime.orchestrator.executors import ExecutorResult
from runtime.orchestrator.orchestrator import Orchestrator
from runtime.orchestrator.teams import TeamsRegistry


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


def _prepare_task_session(
    test_settings: Settings,
    test_runtime: OrgPaths,
    agent: str,
) -> Orchestrator:
    for contract_id in _TASK_CONTEXT_CONTRACT_IDS:
        source = test_settings.get_bundled_skills_dir() / contract_id
        source.mkdir(parents=True, exist_ok=True)
        (source / "SKILL.md").write_text(f"# {contract_id}\n")

    workspace = test_runtime.workspaces_dir / agent
    workspace.mkdir(parents=True)
    (workspace / "task_history.md").write_text(f"# Task History: {agent}\n")
    (workspace / "AGENTS.md").write_text(f"# Agent: {agent}\n")
    os.symlink("AGENTS.md", workspace / "CLAUDE.md")

    test_runtime.agents_dir.mkdir(parents=True, exist_ok=True)
    agent_definition = AgentDef(
        name=agent,
        team="engineering",
        role="worker",
        executor="claude",
        allow_rules=(),
        repos={},
        enrolled_by=None,
        enrolled_at_task=None,
        enrolled_at=None,
        system_prompt=f"You are {agent}.",
        description="",
        model=None,
    )
    (test_runtime.agents_dir / f"{agent}.md").write_text(
        render_agent_text(agent_definition),
    )

    test_runtime.root.mkdir(parents=True, exist_ok=True)
    database = Database(test_runtime.db_path)
    return Orchestrator(
        db=database,
        settings=test_settings,
        paths=test_runtime,
        slug="test",
        teams=TeamsRegistry.load(test_runtime.root),
    )


def test_run_agent_persists_task_scratch_report_before_session_end(
    test_settings: Settings,
    test_runtime: OrgPaths,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The real teardown owners persist report then session-end audit rows."""
    agent = "dev_agent"
    session_id = "sess-teardown-audit-order"
    orchestrator = _prepare_task_session(test_settings, test_runtime, agent)
    task_id = orchestrator.create_task("prove teardown audit order")
    monkeypatch.setattr(orchestrator, "_build_session_id", lambda: session_id)

    class SuccessfulExecutor:
        def run(self, **_kwargs: object) -> ExecutorResult:
            return ExecutorResult(
                success=True,
                duration_seconds=1,
                session_id=session_id,
            )

    with patch.object(
        orchestrator,
        "_build_executor",
        return_value=SuccessfulExecutor(),
    ):
        result, report = orchestrator._run_agent(task_id, agent, "")

    assert result.success is True
    assert report is None

    teardown_rows = [
        row
        for row in orchestrator._db.get_audit_logs(task_id)
        if row["action"] in {"task_scratch_report", "session_end"}
    ]
    assert [row["action"] for row in teardown_rows] == [
        "task_scratch_report",
        "session_end",
    ]
    assert teardown_rows[0]["id"] < teardown_rows[1]["id"]
    assert teardown_rows[0]["agent"] == teardown_rows[1]["agent"] == agent
    assert teardown_rows[0]["task_id"] == teardown_rows[1]["task_id"] == task_id
    assert teardown_rows[0]["payload"]["source"] == "teardown"
    assert teardown_rows[0]["payload"]["producer_observation_id"] == session_id
