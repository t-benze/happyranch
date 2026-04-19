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
    CompletionReport,
    NextStep,
    StepRecord,
    TaskRecord,
    TaskType,
)
from src.orchestrator.executor import AgentExecutor, ExecutorResult
from src.orchestrator.performance_tracker import PerformanceTracker

logger = logging.getLogger(__name__)


class WorkspaceNotInitialized(RuntimeError):
    """Raised when an agent workspace is missing required skill files.

    Workspace bootstrap is an explicit, operator-driven step (`opc init-agent`)
    rather than an implicit side-effect of task runs. If the orchestrator
    discovers the workspace isn't ready, it fails fast with an actionable
    message instead of silently rejecting the task.
    """


def _indent(text: str, prefix: str) -> str:
    """Indent every line of text with prefix (for YAML block-literal emission)."""
    if not text:
        return prefix
    return "\n".join(prefix + line for line in text.splitlines())


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
        self._queue: "asyncio.Queue[str] | None" = None  # wired by daemon

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
            status=row.get("status") or "completed",
            confidence=row["confidence_score"] or 0,
            output_summary=row["output_summary"] or "",
            risks_flagged=row.get("risks_flagged") or [],
            dependencies=[],
            suggested_reviewer_focus=[],
            artifact_dir=row.get("artifact_dir"),
        )

    def create_task(self, task_type: TaskType, brief: str) -> str:
        """Create a new task and persist it."""
        task_id = self._db.next_task_id()
        task = TaskRecord(id=task_id, type=task_type, brief=brief)
        self._db.insert_task(task)
        logger.info("Created task %s: %s", task_id, brief)
        return task_id

    def run_step(self, task_id: str) -> None:
        """Advance a task one agent-subprocess worth.

        Contract: task MUST be PENDING or BLOCKED(DELEGATED)-with-all-children-
        terminal. Anything else is a stale enqueue and is silently ignored.
        """
        from src.orchestrator.run_step import run_step_impl
        run_step_impl(self, task_id)

    def _parse_next_step(self, report: CompletionReport | None) -> NextStep:
        """Parse the Engineering Head's decision from its completion report.

        The EH must return a single JSON object in ``output_summary``. Anything
        else — prose, empty, JSON-in-a-sentence, valid JSON with the wrong
        schema — escalates to the founder. We refuse to guess intent from
        prose: a prior silent-approve fallback was the root cause of TASK-013
        and TASK-016, where "Delegating to dev_agent..." was interpreted as
        "done" and the worker never ran.
        """
        if report is None:
            return NextStep(action="escalate", reason="No completion report from Engineering Head")
        text = report.output_summary or ""
        stripped = text.strip()
        if not stripped:
            return NextStep(
                action="escalate",
                reason="EH returned an empty output_summary; no decision to act on.",
            )
        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            preview = stripped.replace("\n", " ")[:200]
            return NextStep(
                action="escalate",
                reason=(
                    "EH returned non-JSON output_summary; decision cannot be parsed. "
                    f"Preview: {preview!r}"
                ),
            )
        if not isinstance(data, dict):
            return NextStep(
                action="escalate",
                reason=(
                    "EH output_summary parsed as non-object JSON; expected a decision "
                    f"object. Got: {type(data).__name__}"
                ),
            )
        try:
            return NextStep(**data)
        except (KeyError, ValueError, ValidationError) as exc:
            return NextStep(
                action="escalate",
                reason=f"Malformed EH decision: {exc}",
            )

    def _run_agent(
        self,
        task_id: str,
        agent: str,
        prompt: str,
        on_session_started: Callable[[str, str, str], None] | None = None,
    ) -> tuple[ExecutorResult, CompletionReport | None]:
        """Set up workspace and run an agent session.

        Returns a tuple ``(executor_result, completion_report_or_None)``.
        ``on_session_started`` is invoked with ``(task_id, agent_name, session_id)``
        before the subprocess starts so the daemon can register the active session.
        """
        task = self._db.get_task(task_id)
        agent_name = agent
        workspace = self._runtime.workspaces_dir / agent_name

        # The orchestrator relies on the start-task skill to bridge prompt →
        # agent work → completion callback. If the workspace was bootstrapped
        # before skills existed (or the user wiped it), the agent never calls
        # `opc report-completion` and the task silently rejects. Fail fast
        # with an actionable message instead.
        skill_marker = workspace / ".claude" / "skills" / "start-task" / "SKILL.md"
        if not skill_marker.exists():
            raise WorkspaceNotInitialized(
                f"workspace for {agent_name!r} is not initialized "
                f"(missing {skill_marker}). Run `opc init-agent {agent_name}` "
                f"to bootstrap it."
            )

        # Workspace is initialized once at `opc init-agent` — not per session.
        # Brief is injected here:
        brief = task.brief if task else ""
        session_id = self._build_session_id()
        # The prompt format must match the parsing contract documented in
        # protocol/skills/start-task/SKILL.md. Multi-line values use YAML-style
        # block literals so an agent scanning the text can bracket them cleanly.
        full_prompt = (
            f"You are {agent_name}. Use the start-task skill to handle this task.\n"
            f"\n"
            f"Parameters:\n"
            f"  task_id: {task_id}\n"
            f"  session_id: {session_id}\n"
            f"  brief: {brief}\n"
            f"  role_guidance: |\n"
            f"{_indent(prompt, '    ')}\n"
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

    _HISTORY_CAP = 50
    _HISTORY_HEADER = "# Task History"

    def _update_task_history(self, task_id: str) -> None:
        """Rebuild the assigned_agent's task_history.md from the DB.

        Newest-first, capped at ``_HISTORY_CAP`` entries. Silent no-op if
        the task has no assigned_agent or its workspace is missing.
        """
        task = self._db.get_task(task_id)
        if task is None or not task.assigned_agent:
            return
        ws = self._runtime.workspaces_dir / task.assigned_agent
        if not ws.exists():
            return
        path = ws / "task_history.md"

        recent = self._db.list_agent_tasks(task.assigned_agent, limit=self._HISTORY_CAP)
        header = f"{self._HISTORY_HEADER}: {task.assigned_agent}\n\n"
        lines: list[str] = []
        for t in recent:
            date = t.completed_at or t.updated_at or t.created_at
            date_str = date.date().isoformat() if hasattr(date, "date") else str(date)[:10]
            brief = (t.brief or "").replace("\n", " ").strip()[:120]
            outcome = (t.note or "").replace("\n", " ").strip()[:160]
            lines.append(f"- **{t.id}** ({date_str}, {t.status.value}) — {brief}")
            lines.append(f"  - Outcome: {outcome}" if outcome else "  - Outcome: (none)")
            if t.final_artifact_dir:
                lines.append(f"  - Artifact: `{t.final_artifact_dir}`")
        path.write_text(header + "\n".join(lines) + ("\n" if lines else ""))

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
