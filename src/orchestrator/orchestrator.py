from __future__ import annotations

import json
import logging
from pathlib import Path

from src.config import Settings
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import (
    AgentName,
    ReviewVerdict,
    TaskRecord,
    TaskStatus,
    TaskStep,
    TaskType,
)
from src.orchestrator.context_builder import ContextBuilder
from src.orchestrator.executor import AgentExecutor, ExecutorResult
from src.orchestrator.performance_tracker import PerformanceTracker
from src.orchestrator.revision_loop import decide_next_action
from src.orchestrator.task_router import build_task_chain

logger = logging.getLogger(__name__)


_DEFAULT_SYSTEM_PROMPTS: dict[str, str] = {
    "engineering_head": "You are the Engineering Head. Review work from your team. Return a JSON verdict in your output_summary: {\"verdict\": \"approve\"|\"revise\"|\"reject\", \"feedback\": \"...\", \"target_agent\": \"...\"}",
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

    def build_chain(self, task_type: TaskType) -> list[TaskStep]:
        """Build a task chain based on current agent tiers."""
        tiers = self._tracker.get_all_tiers()
        return build_task_chain(task_type, tiers)

    def run_task(self, task_id: str) -> str:
        """Run a task through its full lifecycle. Returns final status string."""
        task = self._db.get_task(task_id)
        if task is None:
            raise ValueError(f"Task {task_id} not found")

        chain = self.build_chain(task.type)
        self._db.update_task(task_id, status=TaskStatus.IN_PROGRESS)

        # For payment_change, log cross-audit stub before review
        if task.type == TaskType.PAYMENT_CHANGE:
            self._audit.log_cross_audit_stub(task_id, task.type.value)

        prior_output: str | None = None
        review_step_index = self._find_review_step(chain)

        # Run pre-review steps
        for step in chain[:review_step_index]:
            result = self._run_agent_step(task_id, step, prior_output)
            if not result.success:
                self._db.update_task(task_id, status=TaskStatus.REJECTED)
                return "rejected"
            self._log_step_result(task_id, result)
            prior_output = result.report.output_summary if result.report else None

        # Review loop
        return self._review_loop(task_id, task, chain, review_step_index, prior_output)

    def _find_review_step(self, chain: list[TaskStep]) -> int:
        """Find the index of the final review step."""
        for i in range(len(chain) - 1, -1, -1):
            if chain[i].action == "review":
                return i
        return len(chain) - 1

    def _review_loop(
        self,
        task_id: str,
        task: TaskRecord,
        chain: list[TaskStep],
        review_index: int,
        prior_output: str | None,
    ) -> str:
        """Run the review step, handle revisions, return final status."""
        revision_count = 0
        max_rounds = self._settings.max_revision_rounds

        while True:
            # Run the review step
            review_step = chain[review_index]
            self._db.update_task(task_id, status=TaskStatus.IN_REVIEW)
            result = self._run_agent_step(task_id, review_step, prior_output)

            if not result.success or result.report is None:
                self._db.update_task(task_id, status=TaskStatus.REJECTED)
                return "rejected"

            self._log_step_result(task_id, result)

            # Parse verdict from Engineering Head's output
            verdict, feedback, target_agent = self._parse_review_verdict(result)
            reviewed_agent = target_agent or self._find_last_worker(chain, review_index)
            self._audit.log_review_verdict(
                task_id, review_step.agent.value, verdict, feedback,
                reviewed_agent=reviewed_agent,
            )

            # Use revision loop to decide next action
            action = decide_next_action(
                verdict=ReviewVerdict(verdict),
                revision_count=revision_count,
                max_rounds=max_rounds,
                feedback=feedback,
                target_agent=target_agent,
            )

            if action.action == "approved":
                self._db.update_task(task_id, status=TaskStatus.APPROVED)
                self._update_recent_tasks(task_id)
                return "approved"

            if action.action == "rejected":
                self._db.update_task(task_id, status=TaskStatus.REJECTED)
                self._update_recent_tasks(task_id)
                return "rejected"

            if action.action == "escalated":
                self._db.update_task(task_id, status=TaskStatus.ESCALATED)
                self._audit.log_escalation(task_id, "orchestrator", action.feedback or "Max revisions exceeded")
                self._update_recent_tasks(task_id)
                return "escalated"

            # action.action == "revise"
            revision_count += 1
            self._db.increment_revision_count(task_id)

            # Find the worker to revise and re-run them
            revise_agent = target_agent or self._find_last_worker(chain, review_index)
            revise_step = TaskStep(
                agent=AgentName(revise_agent),
                action="revise",
                description=f"Revise based on feedback: {feedback}",
            )
            revise_result = self._run_agent_step(task_id, revise_step, feedback)
            if revise_result.success and revise_result.report:
                self._log_step_result(task_id, revise_result)
                prior_output = revise_result.report.output_summary

    def _parse_review_verdict(self, result: ExecutorResult) -> tuple[str, str | None, str | None]:
        """Parse the Engineering Head's review verdict from the completion report."""
        if result.report is None:
            return "reject", "No completion report", None
        try:
            data = json.loads(result.report.output_summary)
            return (
                data.get("verdict", "reject"),
                data.get("feedback"),
                data.get("target_agent"),
            )
        except (json.JSONDecodeError, AttributeError):
            if result.report.confidence >= 80:
                return "approve", None, None
            return "revise", result.report.output_summary, None

    def _find_last_worker(self, chain: list[TaskStep], before_index: int) -> str:
        """Find the last non-review agent in the chain before the review step."""
        for i in range(before_index - 1, -1, -1):
            if chain[i].agent != AgentName.ENGINEERING_HEAD:
                return chain[i].agent.value
        return AgentName.DEV_AGENT.value

    def _run_agent_step(
        self,
        task_id: str,
        step: TaskStep,
        prior_output: str | None,
    ) -> ExecutorResult:
        """Set up workspace context and run an agent session."""
        agent_name = step.agent.value
        workspace = self._settings.get_workspaces_dir() / agent_name

        system_prompt = _DEFAULT_SYSTEM_PROMPTS.get(agent_name, "")
        self._context.initialize_workspace(workspace, agent_name, system_prompt)

        task = self._db.get_task(task_id)
        prompt_parts = [
            f"Task ID: {task_id}",
            f"Action: {step.action}",
            f"Description: {step.description}",
            f"Brief: {task.brief}" if task else "",
        ]
        if prior_output:
            prompt_parts.append(f"Input from previous step:\n{prior_output}")
        prompt = "\n\n".join(p for p in prompt_parts if p)

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
        """Append a summary to recent_tasks.md for all agents involved in this task."""
        task = self._db.get_task(task_id)
        if task is None:
            return
        summary = f"- **{task_id}** ({task.type.value}): {task.brief} -- {task.status.value} (revisions: {task.revision_count})\n"
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
