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
    PerformanceTier,
    StepRecord,
    TaskRecord,
    TaskStatus,
    TaskType,
)
from src.orchestrator.capabilities import build_capabilities_prompt
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

    def _finalize_task(
        self,
        task_id: str,
        report: CompletionReport | None,
        override_summary: str | None = None,
    ) -> None:
        """Populate tasks.final_output_summary / final_artifact_dir.

        ``override_summary`` wins if set (used for escalation reasons and for
        the parsed EH 'summary' of root tasks). Otherwise we read from the
        report. Silent no-op if there's nothing to persist.
        """
        summary = override_summary
        artifact: str | None = None
        if report is not None:
            if summary is None:
                summary = report.output_summary
            artifact = report.artifact_dir
        fields: dict[str, object] = {}
        if summary is not None:
            fields["final_output_summary"] = summary
        if artifact is not None:
            fields["final_artifact_dir"] = artifact
        if fields:
            self._db.update_task(task_id, **fields)

    def _spawn_delegate_task(
        self, parent_task_id: str, agent: str, prompt: str, task_type: TaskType,
    ) -> str:
        """Persist a child task for a delegated work unit.

        Inherits ``task_type`` from the parent so downstream consumers see a
        consistent type across the tree.
        """
        child_id = self._db.next_task_id()
        child = TaskRecord(
            id=child_id,
            type=task_type,
            brief=prompt,
            assigned_agent=agent,
            parent_task_id=parent_task_id,
        )
        self._db.insert_task(child)
        return child_id

    def run_step(self, task_id: str) -> None:
        """Advance a task one agent-subprocess worth.

        Contract: task MUST be PENDING or BLOCKED(DELEGATED)-with-all-children-
        terminal. Anything else is a stale enqueue and is silently ignored.
        """
        from src.orchestrator.run_step import run_step_impl
        run_step_impl(self, task_id)

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
        # EH owns every root task — record it up front so task_history.md
        # attributes the work to engineering_head even if EH handles directly
        # (no delegation) and regardless of terminal outcome.
        if task.assigned_agent is None:
            self._db.update_task(task_id, assigned_agent="engineering_head")

        # Build dynamic agent list from workspaces
        agent_names = [
            d.name for d in self._runtime.workspaces_dir.iterdir()
            if d.is_dir() and d.name != "engineering_head"
        ] if self._runtime.workspaces_dir.exists() else []
        tiers = self._tracker.get_all_tiers(agent_names)

        prior_steps: list[StepRecord] = []
        max_steps = self._settings.max_orchestration_steps

        for step_num in range(1, max_steps + 1):
            # Ask the Engineering Head what to do next
            agents_for_prompt = []
            for name in agent_names:
                enrollment = self._db.get_enrollment(name)
                desc = enrollment["description"] if enrollment else name
                tier = tiers.get(name, PerformanceTier.GREEN)
                agents_for_prompt.append({"name": name, "description": desc, "tier": tier.value})
            eh_prompt = build_capabilities_prompt(
                brief=task.brief,
                agents=agents_for_prompt,
                step_number=step_num,
                max_steps=max_steps,
                prior_steps=prior_steps,
            )

            eh_result, eh_report = self._run_agent(task_id, "engineering_head", eh_prompt)
            if not eh_result.success or eh_report is None:
                self._db.update_task(task_id, status=TaskStatus.REJECTED)
                self._finalize_task(task_id, report=None, override_summary="EH session failed")
                self._update_task_history(task_id)
                return "rejected"

            self._log_step_result(task_id, eh_result, eh_report)
            next_step = self._parse_next_step(eh_report)

            self._audit.log_orchestration_step(
                task_id, step_num, next_step.model_dump(exclude_none=True),
            )

            if next_step.action == "done":
                self._db.update_task(task_id, status=TaskStatus.APPROVED)
                self._finalize_task(task_id, report=eh_report, override_summary=next_step.summary)
                self._log_review_verdicts(task_id, prior_steps)
                self._update_task_history(task_id)
                return "approved"

            if next_step.action == "escalate":
                self._db.update_task(task_id, status=TaskStatus.ESCALATED)
                self._audit.log_escalation(
                    task_id, "engineering_head",
                    next_step.reason or "Escalated by Engineering Head",
                )
                self._finalize_task(
                    task_id, report=None,
                    override_summary=next_step.reason or "Escalated by Engineering Head",
                )
                self._update_task_history(task_id)
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

                delegate_workspace = self._runtime.workspaces_dir / next_step.agent
                if not delegate_workspace.exists():
                    prior_steps.append(StepRecord(
                        step_number=step_num,
                        agent=next_step.agent,
                        action=f"delegate: {(next_step.prompt or '')[:100]}",
                        result_summary=f"No workspace for agent: {next_step.agent!r}",
                        success=False,
                    ))
                    continue

                child_task_id = self._spawn_delegate_task(
                    parent_task_id=task_id,
                    agent=next_step.agent,
                    prompt=next_step.prompt or "",
                    task_type=task.type,
                )

                delegate_result, delegate_report = self._run_agent(
                    child_task_id, next_step.agent, next_step.prompt or "",
                )
                if delegate_result.success and delegate_report is not None:
                    self._log_step_result(child_task_id, delegate_result, delegate_report)

                # A "blocked" completion is a real signal from the agent that
                # the work did not finish. Treat it as unsuccessful so the EH
                # sees it as a failed step on the next decision.
                delegate_blocked = (
                    delegate_report is not None and delegate_report.status == "blocked"
                )
                if delegate_report is None:
                    result_summary = "Agent session failed"
                elif delegate_blocked:
                    result_summary = f"blocked: {delegate_report.output_summary}"
                else:
                    result_summary = delegate_report.output_summary
                prior_steps.append(StepRecord(
                    step_number=step_num,
                    agent=next_step.agent,
                    action=f"delegate: {(next_step.prompt or '')[:100]}",
                    result_summary=result_summary,
                    success=(
                        delegate_result.success
                        and delegate_report is not None
                        and not delegate_blocked
                    ),
                ))
                self._finalize_task(
                    child_task_id,
                    report=delegate_report,
                    override_summary=(
                        "Agent session failed" if delegate_report is None
                        else f"blocked: {delegate_report.output_summary}" if delegate_blocked
                        else None
                    ),
                )
                # Each completed sub-task becomes an entry in the worker's
                # per-agent history — drilling in via `opc recall <child>` is
                # how the agent retrieves brief + outcome later.
                self._db.update_task(
                    child_task_id,
                    status=(
                        TaskStatus.APPROVED
                        if delegate_result.success and delegate_report is not None and not delegate_blocked
                        else TaskStatus.REJECTED
                    ),
                )
                self._update_task_history(child_task_id)

        # Max steps exceeded — escalate
        self._db.update_task(task_id, status=TaskStatus.ESCALATED)
        self._audit.log_escalation(
            task_id, "orchestrator",
            f"Max orchestration steps ({max_steps}) exceeded",
        )
        self._finalize_task(
            task_id, report=None,
            override_summary=f"Max orchestration steps ({max_steps}) exceeded",
        )
        self._update_task_history(task_id)
        return "escalated"

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
            outcome = (t.final_output_summary or "").replace("\n", " ").strip()[:160]
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
