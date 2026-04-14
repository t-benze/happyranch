from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable

from pydantic import ValidationError

from src.config import Settings
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.runtime import RuntimeDir
from src.models import (
    AgentName,
    CompletionReport,
    NextStep,
    StepRecord,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from src.orchestrator.capabilities import build_capabilities_prompt
from src.orchestrator.executor import AgentExecutor, ExecutorResult
from src.orchestrator.performance_tracker import PerformanceTracker

logger = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self, db: Database, settings: Settings, runtime: RuntimeDir) -> None:
        self._db = db
        self._settings = settings
        self._runtime = runtime
        self._audit = AuditLogger(db)
        self._tracker = PerformanceTracker(db, settings)
        self._executor = AgentExecutor(
            claude_cli_path=settings.claude_cli_path,
            permission_mode=settings.permission_mode,
        )

    def _build_session_id(self) -> str:
        return f"sess-{uuid.uuid4().hex}"

    def _read_completion_from_db(
        self, task_id: str, agent: str, session_id: str,
    ) -> CompletionReport | None:
        row = self._db.get_latest_task_result(task_id, agent, session_id)
        if row is None:
            return None
        return CompletionReport(
            task_id=task_id,
            agent=agent,
            status="completed",
            confidence=row["confidence_score"] or 0,
            output_summary=row["output_summary"] or "",
            risks_flagged=row.get("risks_flagged") or [],
            dependencies=[],
            suggested_reviewer_focus=[],
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

            eh_result, eh_report = self._run_agent(task_id, AgentName.ENGINEERING_HEAD, eh_prompt)
            if not eh_result.success or eh_report is None:
                self._db.update_task(task_id, status=TaskStatus.REJECTED)
                self._update_recent_tasks(task_id)
                return "rejected"

            self._log_step_result(task_id, eh_result, eh_report)
            next_step = self._parse_next_step(eh_report)

            self._audit.log_orchestration_step(
                task_id, step_num, next_step.model_dump(exclude_none=True),
            )

            if next_step.action == "done":
                self._db.update_task(task_id, status=TaskStatus.APPROVED)
                self._log_review_verdicts(task_id, prior_steps)
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
                if next_step.agent is None:
                    prior_steps.append(StepRecord(
                        step_number=step_num,
                        agent="unknown",
                        action="delegate: missing agent name",
                        result_summary="Delegate action had no agent specified",
                        success=False,
                    ))
                    continue

                try:
                    delegate_agent = AgentName(next_step.agent)
                except ValueError:
                    prior_steps.append(StepRecord(
                        step_number=step_num,
                        agent=next_step.agent,
                        action=f"delegate: {(next_step.prompt or '')[:100]}",
                        result_summary=f"Unknown agent name: {next_step.agent!r}",
                        success=False,
                    ))
                    continue

                delegate_result, delegate_report = self._run_agent(
                    task_id, delegate_agent, next_step.prompt or "",
                )
                if delegate_result.success and delegate_report is not None:
                    self._log_step_result(task_id, delegate_result, delegate_report)

                result_summary = (
                    delegate_report.output_summary
                    if delegate_report
                    else "Agent session failed"
                )
                prior_steps.append(StepRecord(
                    step_number=step_num,
                    agent=next_step.agent,
                    action=f"delegate: {(next_step.prompt or '')[:100]}",
                    result_summary=result_summary,
                    success=delegate_result.success and delegate_report is not None,
                ))

        # Max steps exceeded — escalate
        self._db.update_task(task_id, status=TaskStatus.ESCALATED)
        self._audit.log_escalation(
            task_id, "orchestrator",
            f"Max orchestration steps ({max_steps}) exceeded",
        )
        self._update_recent_tasks(task_id)
        return "escalated"

    def _parse_next_step(self, report: CompletionReport | None) -> NextStep:
        """Parse the Engineering Head's decision from its completion report."""
        if report is None:
            return NextStep(action="escalate", reason="No completion report from Engineering Head")
        text = report.output_summary
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError, ValueError):
            # Plain text output — treat as "done" with the text as summary
            return NextStep(action="done", summary=text)
        try:
            return NextStep(**data)
        except (KeyError, ValueError, ValidationError) as exc:
            # Valid JSON but invalid schema — don't silently approve
            return NextStep(
                action="escalate",
                reason=f"Malformed EH decision: {exc}",
            )

    def _run_agent(
        self,
        task_id: str,
        agent: AgentName,
        prompt: str,
        on_session_started: Callable[[str, str, str], None] | None = None,
    ) -> tuple[ExecutorResult, CompletionReport | None]:
        """Set up workspace and run an agent session.

        Returns a tuple ``(executor_result, completion_report_or_None)``.
        ``on_session_started`` is invoked with ``(task_id, agent_name, session_id)``
        before the subprocess starts so the daemon can register the active session.
        """
        task = self._db.get_task(task_id)
        agent_name = agent.value
        workspace = self._runtime.workspaces_dir / agent_name

        # Workspace is initialized once at `opc init-agent` — not per session.
        # Brief is injected here:
        brief = task.brief if task else ""
        session_id = self._build_session_id()
        full_prompt = (
            f"Task ID: {task_id}\nSession ID: {session_id}\nBrief: {brief}\n\n{prompt}"
        )

        if on_session_started is not None:
            on_session_started(task_id, agent_name, session_id)

        self._audit.log_session_start(task_id, agent_name, str(workspace))
        self._db.update_task(task_id, assigned_agent=agent_name)

        result = self._executor.run(
            workspace=workspace,
            prompt=full_prompt,
            session_id=session_id,
            timeout_seconds=self._settings.session_timeout_seconds,
        )
        self._audit.log_session_end(task_id, agent_name, result.duration_seconds)

        report = self._read_completion_from_db(task_id, agent_name, session_id)
        return result, report

    def _log_review_verdicts(self, task_id: str, prior_steps: list[StepRecord]) -> None:
        """Log review verdicts for delegated agents so performance tiers stay current."""
        for step in prior_steps:
            if step.agent in ("unknown", "engineering_head", "orchestrator"):
                continue
            verdict = "approved" if step.success else "rejected"
            self._audit.log_review_verdict(
                task_id=task_id,
                reviewer="engineering_head",
                verdict=verdict,
                feedback=step.result_summary,
                reviewed_agent=step.agent,
            )
            self._tracker.update_scorecard(step.agent)

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
            workspace = self._runtime.workspaces_dir / agent.value
            recent_path = workspace / "recent_tasks.md"
            if recent_path.exists():
                content = recent_path.read_text()
                recent_path.write_text(content + summary)

    def _log_step_result(
        self,
        task_id: str,
        result: ExecutorResult,
        report: CompletionReport | None,
    ) -> None:
        if report is None:
            return
        self._audit.log_completion_report(
            report=report,
            session_id=result.session_id,
            duration_seconds=result.duration_seconds,
        )
