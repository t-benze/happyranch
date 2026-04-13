from __future__ import annotations

import json
import logging

from src.config import Settings
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import (
    AgentName,
    NextStep,
    StepRecord,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from src.orchestrator.capabilities import build_capabilities_prompt
from src.orchestrator.context_builder import ContextBuilder
from src.orchestrator.executor import AgentExecutor, ExecutorResult
from src.orchestrator.performance_tracker import PerformanceTracker

logger = logging.getLogger(__name__)


_DEFAULT_SYSTEM_PROMPTS: dict[str, str] = {
    "engineering_head": (
        "You are the Engineering Head for a tourism services company. "
        "You decide how to handle incoming tasks -- doing work yourself, "
        "delegating to your team, or escalating to the founder. "
        "Follow the instructions in your task prompt for the expected response format."
    ),
    "product_manager": "You are the Product Manager. Write specs and triage bugs.",
    "dev_agent": "You are the Dev Agent. Implement features and fix bugs.",
    "payment_agent": "You are the Payment Agent. Draft payment change proposals with compliance considerations.",
}


class Orchestrator:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._audit = AuditLogger(db)
        self._tracker = PerformanceTracker(db, settings)
        self._context = ContextBuilder(settings)
        self._executor = AgentExecutor(
            claude_cli_path=settings.claude_cli_path,
            permission_mode=settings.permission_mode,
        )

    def create_task(self, task_type: TaskType, brief: str) -> str:
        """Create a new task and persist it."""
        task_id = self._db.next_task_id()
        task = TaskRecord(id=task_id, type=task_type, brief=brief)
        self._db.insert_task(task)
        logger.info("Created task %s: %s", task_id, brief)
        return task_id

    def run_task(self, task_id: str) -> str:
        """Run a task through the EH-driven orchestration loop.

        The Engineering Head decides each step: delegate to a worker,
        handle directly, or escalate. The loop continues until the EH
        says "done", "escalate", or the max steps guardrail fires.
        """
        task = self._db.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        self._db.update_task(task_id, status=TaskStatus.IN_PROGRESS)
        tiers = self._tracker.get_all_tiers()
        prior_steps: list[StepRecord] = []
        max_steps = self._settings.max_orchestration_steps

        for step_num in range(1, max_steps + 1):
            # Ask the Engineering Head what to do next
            eh_prompt = build_capabilities_prompt(
                brief=task.brief,
                agent_tiers=tiers,
                step_number=step_num,
                max_steps=max_steps,
                prior_steps=prior_steps,
            )

            eh_result = self._run_agent(task_id, AgentName.ENGINEERING_HEAD, eh_prompt)

            if not eh_result.success:
                self._db.update_task(task_id, status=TaskStatus.REJECTED)
                return "rejected"

            self._log_step_result(task_id, eh_result)
            next_step = self._parse_next_step(eh_result)

            self._audit.log_orchestration_step(
                task_id, step_num, next_step.model_dump(exclude_none=True),
            )

            if next_step.action == "done":
                self._db.update_task(task_id, status=TaskStatus.APPROVED)
                self._update_recent_tasks(task_id)
                return "approved"

            if next_step.action == "escalate":
                self._db.update_task(task_id, status=TaskStatus.ESCALATED)
                self._audit.log_escalation(
                    task_id, "engineering_head",
                    next_step.reason or "Escalated by Engineering Head",
                )
                self._update_recent_tasks(task_id)
                return "escalated"

            if next_step.action == "delegate":
                delegate_result = self._run_agent(
                    task_id, AgentName(next_step.agent), next_step.prompt or "",
                )
                if delegate_result.success:
                    self._log_step_result(task_id, delegate_result)

                result_summary = (
                    delegate_result.report.output_summary
                    if delegate_result.report
                    else "Agent session failed"
                )
                prior_steps.append(StepRecord(
                    step_number=step_num,
                    agent=next_step.agent or "unknown",
                    action=f"delegate: {(next_step.prompt or '')[:100]}",
                    result_summary=result_summary,
                    success=delegate_result.success,
                ))

        # Max steps exceeded — escalate
        self._db.update_task(task_id, status=TaskStatus.ESCALATED)
        self._audit.log_escalation(
            task_id, "orchestrator",
            f"Max orchestration steps ({max_steps}) exceeded",
        )
        self._update_recent_tasks(task_id)
        return "escalated"

    def _parse_next_step(self, result: ExecutorResult) -> NextStep:
        """Parse the Engineering Head's decision from its completion report."""
        if result.report is None:
            return NextStep(action="escalate", reason="No completion report from Engineering Head")
        try:
            data = json.loads(result.report.output_summary)
            return NextStep(**data)
        except (json.JSONDecodeError, TypeError, KeyError, ValueError):
            # Plain text output — treat as "done" with the text as summary
            return NextStep(action="done", summary=result.report.output_summary)

    def _run_agent(
        self,
        task_id: str,
        agent: AgentName,
        prompt: str,
    ) -> ExecutorResult:
        """Set up workspace and run an agent session."""
        agent_name = agent.value
        workspace = self._settings.get_workspaces_dir() / agent_name

        system_prompt = _DEFAULT_SYSTEM_PROMPTS.get(agent_name, "")
        self._context.initialize_workspace(workspace, agent_name, system_prompt)

        self._audit.log_session_start(task_id, agent_name, str(workspace))
        self._db.update_task(task_id, assigned_agent=agent_name)

        result = self._executor.run(
            workspace=workspace,
            prompt=prompt,
            timeout_seconds=self._settings.session_timeout_seconds,
        )

        self._audit.log_session_end(task_id, agent_name, result.duration_seconds)
        return result

    def _update_recent_tasks(self, task_id: str) -> None:
        """Append a summary to recent_tasks.md for all agents."""
        task = self._db.get_task(task_id)
        if task is None:
            return
        summary = (
            f"- **{task_id}** ({task.type.value}): {task.brief} "
            f"-- {task.status.value}\n"
        )
        for agent in AgentName:
            workspace = self._settings.get_workspaces_dir() / agent.value
            recent_path = workspace / "recent_tasks.md"
            if recent_path.exists():
                content = recent_path.read_text()
                recent_path.write_text(content + summary)

    def _log_step_result(self, task_id: str, result: ExecutorResult) -> None:
        """Log a successful step result to audit trail."""
        if result.report:
            self._audit.log_completion_report(
                report=result.report,
                session_id=result.session_id,
                duration_seconds=result.duration_seconds,
            )
