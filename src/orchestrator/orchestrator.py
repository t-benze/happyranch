from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

if TYPE_CHECKING:
    from src.daemon.queue import TaskQueue
    from src.daemon.sessions import SessionTracker

from src.config import Settings
from src.daemon.agent_config import load_agent_config
from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import (
    CompletionReport,
    NextStep,
    StepRecord,
    TaskRecord,
)
from src.orchestrator._paths import OrgPaths
from src.orchestrator.executors import (
    ClaudeExecutor,
    CodexExecutor,
    ExecutorResult,
    OpencodeExecutor,
)
from src.orchestrator.org_config import load_org_config
from src.orchestrator.teams import TeamsRegistry

logger = logging.getLogger(__name__)


class WorkspaceNotInitialized(RuntimeError):
    """Raised when an agent workspace is missing required skill files.

    Workspace bootstrap is an explicit, operator-driven step (`grassland init-agent`)
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
    def __init__(
        self,
        db: Database,
        settings: Settings,
        paths: OrgPaths,
        slug: str,
        teams: TeamsRegistry,
    ) -> None:
        self._db = db
        self._settings = settings
        self._paths = paths
        self._slug = slug
        self._audit = AuditLogger(db)
        self._teams = teams
        self._queue: "TaskQueue | None" = None  # wired by daemon
        self._sessions: "SessionTracker | None" = None  # wired by daemon
        self._notifier = None  # wired by daemon
        self._thread_queue = None  # wired by daemon (ThreadQueue)
        self._main_loop = None    # wired by daemon (asyncio event loop for cross-thread enqueues)

    @property
    def teams(self) -> TeamsRegistry:
        return self._teams

    @property
    def db(self) -> Database:
        """Read-only handle to the Database — used by the daemon's
        Dispatcher.heartbeat to stamp tasks.last_heartbeat for the
        currently-executing task without reaching into the private attribute."""
        return self._db

    def attach_queue(self, queue: "TaskQueue") -> None:
        """Daemon boot wires its TaskQueue so run_step can enqueue follow-ups.

        Decoupled from __init__ because tests construct an Orchestrator
        without a daemon, and because TaskQueue is owned by DaemonState, not
        the Orchestrator."""
        self._queue = queue

    def attach_sessions(self, tracker: "SessionTracker") -> None:
        """Daemon boot wires its SessionTracker so each spawned subprocess is
        registered as the active session for (task_id, agent) BEFORE the
        agent's `grassland report-completion` callback can land. Without this, the
        completion endpoint rejects every callback as `unknown_session` (409)
        and the task fails silently with note="agent session failed"."""
        self._sessions = tracker

    def attach_notifier(self, notifier) -> None:
        """Wire a notifier (mirrors attach_queue / attach_sessions)."""
        self._notifier = notifier

    def attach_thread_queue(self, thread_queue, main_loop) -> None:
        """Wire the per-org ThreadQueue and the daemon's main event loop.

        ``run_step`` runs on a thread-pool worker with no event loop of its
        own; ``asyncio.run_coroutine_threadsafe`` bridges the boundary.
        Decoupled from ``__init__`` for the same reason as ``attach_queue``
        — tests that build an Orchestrator without a daemon never call this
        and simply get the None-guarded skip path in ``_maybe_post_thread_followup``.
        """
        self._thread_queue = thread_queue
        self._main_loop = main_loop

    def notify_escalated(
        self, *, task_id: str, agent: str, reason: str, last_summary: str = "",
    ) -> None:
        """Schedule an out-of-band notification. Fire-and-forget — the
        orchestration loop never blocks on the network round-trip and never
        sees an exception from the notifier (the notifier swallows + audits
        its own errors)."""
        if self._notifier is None:
            return
        import asyncio
        import threading
        coro_factory = lambda: self._notifier.notify_escalated(
            task_id=task_id, agent=agent, reason=reason,
            last_summary=last_summary,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop in this thread (typical: thread-pool worker
            # driven by run_step). Spawn a daemon thread that owns its own
            # event loop so the worker thread isn't blocked.
            threading.Thread(
                target=lambda: asyncio.run(coro_factory()),
                daemon=True,
            ).start()
        else:
            loop.create_task(coro_factory())

    def notify_failed(
        self, *, task_id: str, agent: str, failure_kind: str,
        failure_note: str, last_summary: str = "",
    ) -> None:
        """Fire-and-forget failure notification. Same threading model as
        notify_escalated: detect running loop, fall back to daemon thread."""
        if self._notifier is None:
            return
        import asyncio
        import threading
        coro_factory = lambda: self._notifier.send_failure(
            task_id=task_id, agent=agent, failure_kind=failure_kind,
            failure_note=failure_note, last_summary=last_summary,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(
                target=lambda: asyncio.run(coro_factory()),
                daemon=True,
            ).start()
        else:
            loop.create_task(coro_factory())

    def notify_job_submitted(
        self,
        *,
        job_id: str,
        agent: str,
        task_id: str,
        title: str,
        rationale: str,
        script_text: str,
        interpreter: str,
        cwd_hint: str | None,
    ) -> None:
        """Fire-and-forget push notification for an agent's script request.

        Same threading model as notify_escalated / notify_failed: detect a
        running event loop and create_task on it; otherwise spawn a daemon
        thread that owns its own asyncio.run.
        """
        if self._notifier is None:
            return
        import asyncio
        import threading
        coro_factory = lambda: self._notifier.send_job_request(
            job_id=job_id, agent=agent, task_id=task_id,
            title=title, rationale=rationale, script_text=script_text,
            interpreter=interpreter, cwd_hint=cwd_hint,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(
                target=lambda: asyncio.run(coro_factory()),
                daemon=True,
            ).start()
        else:
            loop.create_task(coro_factory())

    def notify_job_run_result(
        self,
        *,
        job_id: str,
        task_id: str,
        parent_message_id: str,
        status: str,
        exit_code: int | None,
        duration_ms: int,
        stdout_head: str | None,
        stderr_head: str | None,
        reason: str | None,
    ) -> None:
        """Fire-and-forget threaded reply with the SR run's terminal result."""
        if self._notifier is None:
            return
        import asyncio
        import threading
        coro_factory = lambda: self._notifier.send_job_run_result(
            job_id=job_id, task_id=task_id,
            parent_message_id=parent_message_id,
            status=status, exit_code=exit_code, duration_ms=duration_ms,
            stdout_head=stdout_head, stderr_head=stderr_head, reason=reason,
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(
                target=lambda: asyncio.run(coro_factory()),
                daemon=True,
            ).start()
        else:
            loop.create_task(coro_factory())

    def _build_session_id(self) -> str:
        return f"sess-{uuid.uuid4().hex}"

    def _resolve_executor_name(self, agent_name: str) -> str:
        workspace = self._paths.workspaces_dir / agent_name
        cfg = load_agent_config(workspace)
        return cfg.get("executor") or "claude"

    def _resolve_session_timeout(self, agent_name: str, task_id: str | None = None) -> int:
        """Resolve the per-session timeout, walking task -> org -> settings.

        Per-task override lives on the `tasks` row's `session_timeout_seconds`
        column — set by `grassland revisit --session-timeout-seconds` and inherited
        from parent on delegate / from predecessor root on revisit. Org
        override lives in `<runtime>/org/config.yaml`. Either being absent
        (or NULL) falls through to the next layer; the global Settings default
        is the final floor and is itself overridable via GRASSLAND_SESSION_TIMEOUT_SECONDS.

        ``agent_name`` is unused today but kept on the signature for callers
        and future per-agent overrides we don't have a mechanism for yet.
        """
        del agent_name  # see docstring
        if task_id is not None:
            task = self._db.get_task(task_id)
            if task is not None and task.session_timeout_seconds is not None:
                return task.session_timeout_seconds
        org = load_org_config(self._paths)
        if org.session_timeout_seconds is not None:
            return org.session_timeout_seconds
        return self._settings.session_timeout_seconds

    def _readiness_marker(self, workspace: Path, provider: str) -> Path:
        if provider == "codex":
            return workspace / "AGENTS.md"
        if provider == "opencode":
            # opencode reads AGENTS.md and discovers skills via .agents/skills/.
            # AGENTS.md alone is sufficient as the readiness signal — its
            # presence implies the adapter ran and copied the skills tree.
            return workspace / "AGENTS.md"
        return workspace / ".claude" / "skills" / "start-task" / "SKILL.md"

    def _build_executor(self, provider: str):
        if provider == "codex":
            return CodexExecutor(
                codex_cli_path=self._settings.codex_cli_path,
                sandbox_mode=self._settings.codex_sandbox_mode,
            )
        if provider == "opencode":
            return OpencodeExecutor(
                opencode_cli_path=self._settings.opencode_cli_path,
            )
        return ClaudeExecutor(
            claude_cli_path=self._settings.claude_cli_path,
            permission_mode=self._settings.permission_mode,
            settings=self._settings,
            paths=self._paths,
        )

    def _build_agent_prompt(
        self,
        provider: str,
        agent_name: str,
        task_id: str,
        session_id: str,
        brief: str,
        prompt: str,
    ) -> str:
        if provider == "codex":
            intro = (
                f"You are {agent_name}. Use the injected task parameters directly to handle this task.\n"
            )
        else:
            # Both Claude and opencode have an in-context start-task skill —
            # Claude via auto-loaded ``.claude/skills/``, opencode via its
            # built-in ``skill`` tool that lists and loads skills from
            # ``.agents/skills/`` on demand. The same nudge works for both.
            intro = (
                f"You are {agent_name}. Use the start-task skill to handle this task.\n"
            )
        # role_guidance is the per-task overlay (managers get the capabilities
        # block; workers get nothing extra beyond the brief). Empty prompt =>
        # omit the line entirely so workers don't see a dangling block scalar.
        role_guidance_block = (
            f"  role_guidance: |\n{_indent(prompt, '    ')}\n"
            if prompt and prompt.strip()
            else ""
        )
        return (
            f"{intro}"
            f"\n"
            f"Parameters:\n"
            f"  task_id: {task_id}\n"
            f"  session_id: {session_id}\n"
            f"  brief: {brief}\n"
            f"{role_guidance_block}"
        )

    def _read_completion_from_db(
        self, task_id: str, agent: str, session_id: str,
    ) -> CompletionReport | None:
        row = self._db.get_latest_task_result(task_id, agent, session_id)
        if row is None:
            return None
        decision: NextStep | None = None
        raw_decision = row.get("decision_json")
        if raw_decision:
            # A row with garbage in decision_json is a corruption signal, not
            # a reason to fall through to the legacy prose-JSON path — leave
            # decision None so _parse_next_step escalates with a readable
            # reason instead of silently approving the prose summary.
            try:
                parsed = json.loads(raw_decision)
                if isinstance(parsed, dict):
                    decision = NextStep(**parsed)
            except (json.JSONDecodeError, TypeError, ValueError, ValidationError):
                decision = None
        return CompletionReport(
            task_id=task_id,
            agent=agent,
            status=row.get("status") or "completed",
            confidence=row["confidence_score"] or 0,
            output_summary=row["output_summary"] or "",
            decision=decision,
            risks_flagged=row.get("risks_flagged") or [],
            dependencies=[],
            suggested_reviewer_focus=[],
            artifact_dir=row.get("artifact_dir"),
        )

    def create_task(self, brief: str, team: str = "engineering") -> str:
        """Create a new task and persist it."""
        task_id = self._db.next_task_id()
        task = TaskRecord(id=task_id, brief=brief, team=team)
        self._db.insert_task(task)
        logger.info("Created task %s: %s", task_id, brief)
        return task_id

    def run_step(self, task_id: str, metadata: dict | None = None) -> None:
        """Advance a task one agent-subprocess worth.

        Contract: task MUST be PENDING or BLOCKED(DELEGATED)-with-all-children-
        terminal. Anything else is a stale enqueue and is silently ignored.

        ``metadata`` is an optional trigger-context dict forwarded from the
        queue (e.g. ``{"trigger": "job_terminal", "triggering_job_id": "JOB-5"}``).
        It is passed directly to ``run_step_impl`` as a function parameter —
        no shared mutable state.
        """
        from src.orchestrator.run_step import run_step_impl
        run_step_impl(self, task_id, metadata=metadata)

    def _parse_next_step(self, report: CompletionReport | None) -> NextStep:
        """Parse the team manager's decision from its completion report.

        Preferred path: ``report.decision`` is a structured NextStep supplied
        by the manager alongside a prose ``output_summary``. That separation
        eliminates the old double-encoding trap where ``output_summary``
        itself had to be a JSON decision envelope (see TASK-071 post-mortem).

        Legacy path: if ``decision`` is absent (in-flight workspaces on the
        old skill), we still attempt to parse ``output_summary`` as JSON for
        backwards compatibility. Prose-as-``output_summary`` escalates; we
        refuse to guess intent from prose — the silent-approve fallback was
        the root cause of TASK-013 / TASK-016.
        """
        if report is None:
            return NextStep(action="escalate", reason="No completion report from team manager")
        if report.decision is not None:
            return report.decision
        text = report.output_summary or ""
        stripped = text.strip()
        if not stripped:
            return NextStep(
                action="escalate",
                reason=(
                    "Team manager returned neither a `decision` field nor an "
                    "`output_summary`; no decision to act on."
                ),
            )
        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, TypeError, ValueError):
            preview = stripped.replace("\n", " ")[:200]
            return NextStep(
                action="escalate",
                reason=(
                    "Team manager omitted the `decision` field and its "
                    "`output_summary` is not JSON. The completion payload "
                    "must include a `decision` object "
                    "(delegate/done/escalate). "
                    f"Preview: {preview!r}"
                ),
            )
        if not isinstance(data, dict):
            return NextStep(
                action="escalate",
                reason=(
                    "Team manager legacy output_summary parsed as non-object "
                    f"JSON; expected a decision object. Got: {type(data).__name__}"
                ),
            )
        try:
            return NextStep(**data)
        except (KeyError, ValueError, ValidationError) as exc:
            return NextStep(
                action="escalate",
                reason=f"Malformed team-manager decision: {exc}",
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
        workspace = self._paths.workspaces_dir / agent_name
        provider = self._resolve_executor_name(agent_name)
        executor = self._build_executor(provider)

        # The orchestrator relies on the start-task skill to bridge prompt →
        # agent work → completion callback. If the workspace was bootstrapped
        # before skills existed (or the user wiped it), the agent never calls
        # `grassland report-completion` and the task silently rejects. Fail fast
        # with an actionable message instead.
        skill_marker = self._readiness_marker(workspace, provider)
        if not skill_marker.exists():
            raise WorkspaceNotInitialized(
                f"workspace for {agent_name!r} is not initialized "
                f"(missing {skill_marker}). Run `grassland init-agent {agent_name}` "
                f"to bootstrap it."
            )

        # Workspace is initialized once at `grassland init-agent` — not per session.
        # Brief is injected here:
        brief = task.brief if task else ""
        session_id = self._build_session_id()
        full_prompt = self._build_agent_prompt(
            provider,
            agent_name,
            task_id,
            session_id,
            brief,
            prompt,
        )

        if self._sessions is not None:
            self._sessions.set_active(task_id, agent_name, session_id)
        if on_session_started is not None:
            on_session_started(task_id, agent_name, session_id)

        self._audit.log_session_start(task_id, agent_name, str(workspace))
        self._db.update_task(task_id, assigned_agent=agent_name)

        # Capture pid into SessionTracker the moment Popen returns so the
        # /cancel route can SIGTERM the subprocess mid-session without racing
        # the set_active() call above. Works for both Claude and Codex
        # executors because both delegate to executors._run_command.
        def _on_started(pid: int) -> None:
            if self._sessions is not None:
                self._sessions.set_pid(task_id, agent_name, pid)

        result = executor.run(
            workspace=workspace,
            prompt=full_prompt,
            session_id=session_id,
            timeout_seconds=self._resolve_session_timeout(agent_name, task_id=task_id),
            on_started=_on_started,
        )
        self._audit.log_session_end(
            task_id=task_id,
            agent=agent_name,
            duration_seconds=result.duration_seconds,
            token_usage=result.token_usage,
        )

        report = self._read_completion_from_db(task_id, agent_name, session_id)
        return result, report

    def _log_review_verdicts(self, task_id: str, prior_steps: list[StepRecord]) -> None:
        """Log review verdicts for delegated agents into the audit log.

        Verdicts are the canonical record of delegation outcomes — the
        founder consults them to see which agents need attention.
        """
        task = self._db.get_task(task_id)
        reviewer_team = task.team if task else "engineering"
        try:
            reviewer = self._teams.manager_for_team(reviewer_team).name
        except KeyError:
            reviewer = "unknown_manager"
        for step in prior_steps:
            if step.agent in ("unknown", "orchestrator") or self._teams.is_team_manager(step.agent):
                continue
            verdict = "approved" if step.success else "rejected"
            self._audit.log_review_verdict(
                task_id=task_id,
                reviewer=reviewer,
                verdict=verdict,
                feedback=step.result_summary,
                reviewed_agent=step.agent,
            )

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
        ws = self._paths.workspaces_dir / task.assigned_agent
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
        self._audit.log_completion_report(report=report)
