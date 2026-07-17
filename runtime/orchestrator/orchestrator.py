from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

if TYPE_CHECKING:
    from runtime.daemon.queue import TaskQueue
    from runtime.daemon.sessions import SessionTracker

from runtime.config import Settings
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.orchestrator.executor_registry import build_executor, get_registry
from runtime.models import (
    CompletionReport,
    NextStep,
    StepRecord,
    TaskRecord,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.executors import (
    ClaudeExecutor,
    CodexExecutor,
    ExecutorResult,
    OpencodeExecutor,
    PiExecutor,
)
from runtime.orchestrator.org_config import (
    load_org_config,
    render_current_time_line,
    resolve_managed_skills_index,
    resolve_org_timezone_display,
    resolve_protocol_doc_manifest,
)
from runtime.orchestrator.workspace_adapters import (
    ensure_system_contracts_materialized,
    inject_managed_skills,
    inject_system_contracts,
    refresh_session_skills,
    refresh_workspace_repos,
)
from runtime.orchestrator.teams import TeamsRegistry

logger = logging.getLogger(__name__)


class WorkspaceNotInitialized(RuntimeError):
    """Raised when an agent workspace is missing required skill files.

    Workspace bootstrap is an explicit, operator-driven step (`happyranch init-agent`)
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
        agent's `happyranch report-completion` callback can land. Without this, the
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
        """Resolve the per-agent executor from org/agents/<name>.md frontmatter.

        THR-095: AgentDef.executor is the SINGLE authoritative store.
        agent.yaml is no longer read for executor resolution.
        """
        from runtime.orchestrator.prompt_loader import load_agent
        agent_def = load_agent(self._paths, agent_name)
        return agent_def.executor if agent_def else "claude"

    def _resolve_model_name(self, agent_name: str) -> str | None:
        """Resolve the per-agent model from org/agents/<name>.md frontmatter.

        THR-095: AgentDef.model is the SINGLE authoritative store.
        agent.yaml is no longer read for model resolution.
        Returns the model string if set, or None when absent.
        """
        from runtime.orchestrator.prompt_loader import load_agent
        agent_def = load_agent(self._paths, agent_name)
        return agent_def.model if agent_def else None

    def _resolve_session_timeout(self, agent_name: str, task_id: str | None = None) -> int:
        """Resolve the per-session timeout, walking task -> org_settings DB ->
        code default.

        Per-task override lives on the `tasks` row's `session_timeout_seconds`
        column — set by `happyranch revisit --session-timeout-seconds` and inherited
        from parent on delegate / from predecessor root on revisit.  The org-level
        override lives in the `org_settings` DB table (THR-095 single-store).
        Either being absent (or NULL) falls through to the next layer; the global
        Settings default is the final floor and is itself overridable via
        HAPPYRANCH_SESSION_TIMEOUT_SECONDS.

        ``agent_name`` is unused today but kept on the signature for callers
        and future per-agent overrides we don't have a mechanism for yet.
        """
        del agent_name  # see docstring
        if task_id is not None:
            task = self._db.get_task(task_id)
            if task is not None and task.session_timeout_seconds is not None:
                return task.session_timeout_seconds
        # THR-095: DB-backed org setting tier (replaces config.yaml read).
        from runtime.orchestrator.org_config import resolve_org_setting_session_timeout
        db_value = resolve_org_setting_session_timeout(
            self._db, code_default=None,
        )
        if db_value is not None:
            return db_value
        return self._settings.session_timeout_seconds

    def _readiness_marker(self, workspace: Path, provider: str) -> Path:
        """Return the readiness marker path for a registered executor profile."""
        profile = get_registry().get_profile(provider)
        if profile is not None:
            return workspace / profile.readiness_marker_fragment
        return workspace / "AGENTS.md"

    def _build_executor(self, provider: str):
        """Build an executor instance for a registered profile."""
        return build_executor(provider, self._settings, self._paths)

    def _current_time_line(self, now: Callable[[], datetime] | None) -> str:
        """Render the localized current-time value injected into every agent
        prompt: ISO-8601 with offset plus the zone label, e.g.
        ``2026-06-27T12:47+08:00 (Asia/Shanghai)`` or, when only an offset is
        derivable, ``2026-06-27T12:47+08:00 (UTC+08:00)``.

        ``now`` is injectable so prompt snapshot tests can freeze the wall
        clock; it must return a tz-aware UTC datetime. The zone is resolved from
        org config (org.timezone -> machine-local -> UTC), then rendered by the
        shared ``render_current_time_line`` reused across every prompt builder."""
        tz, label = resolve_org_timezone_display(load_org_config(self._paths))
        return render_current_time_line(tz, label, now)

    def _build_agent_prompt(
        self,
        provider: str,
        agent_name: str,
        task_id: str,
        session_id: str,
        brief: str,
        prompt: str,
        now: Callable[[], datetime] | None = None,
        memory_digest: str | None = None,
        managed_skills_index: str = "",
        protocol_doc_manifest: str = "",
    ) -> str:
        if provider == "codex":
            intro = (
                f"You are {agent_name}. Use the injected task parameters directly to handle this task.\n"
            )
        else:
            # Claude, opencode, and Pi have an in-context start-task skill —
            # Claude via auto-loaded ``.claude/skills/``, opencode via its
            # built-in ``skill`` tool that lists and loads skills from
            # ``.agents/skills/`` on demand, and Pi via AGENTS.md. The same
            # nudge works for all three.
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
        # current_time sits in the SHARED block so all providers (claude,
        # codex, opencode, pi) receive the local wall-clock + zone on every
        # spawn and wake.
        current_time = self._current_time_line(now)
        # THR-032 Phase 2: PUSH memory digest — salience-ranked, pointer-only,
        # budgeted block injected after brief/role_guidance on every agent
        # spawn/wake. Harness-agnostic by construction: emitted as plain text
        # in the one literal prompt string shared by every executor.
        # Precedent: BLOCKED-JOBS-RESULTS in run_step.py.
        digest_block = f"\n{memory_digest}\n" if memory_digest else ""
        # THR-055 managed skills index — compact manifest of eligible skills
        skills_block = f"\n{managed_skills_index}\n" if managed_skills_index else ""
        # THR-070 protocol doc manifest — bundled-path one-liner per doc
        docs_block = f"\n{protocol_doc_manifest}\n" if protocol_doc_manifest else ""
        return (
            f"{intro}"
            f"\n"
            f"Parameters:\n"
            f"  task_id: {task_id}\n"
            f"  session_id: {session_id}\n"
            f"  current_time: {current_time}\n"
            f"  brief: {brief}\n"
            f"{role_guidance_block}"
            f"{digest_block}"
            f"{skills_block}"
            f"{docs_block}"
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
            verdict=row.get("verdict"),
            decision=decision,
            risks_flagged=row.get("risks_flagged") or [],
            dependencies=[],
            suggested_reviewer_focus=[],
            output_dir=row.get("output_dir"),
            waiting_on_job_ids=row.get("waiting_on_job_ids") or [],
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

        Contract: task MUST be pending or in_progress(delegated)-with-all-children-
        terminal. Anything else is a stale enqueue and is silently ignored.

        ``metadata`` is an optional trigger-context dict forwarded from the
        queue (e.g. ``{"trigger": "job_terminal", "triggering_job_id": "JOB-5"}``).
        It is passed directly to ``run_step_impl`` as a function parameter —
        no shared mutable state.
        """
        from runtime.orchestrator.run_step import run_step_impl
        run_step_impl(self, task_id, metadata=metadata)

    def _parse_next_step(self, report: CompletionReport | None) -> NextStep:
        """Parse the task owner's decision from its completion report.

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
            return NextStep(action="escalate", reason="No completion report from task owner")
        if report.decision is not None:
            return report.decision
        text = report.output_summary or ""
        stripped = text.strip()
        if not stripped:
            return NextStep(
                action="escalate",
                reason=(
                    "Task owner returned neither a `decision` field nor an "
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
                    "Task owner omitted the `decision` field and its "
                    "`output_summary` is not JSON. The completion payload "
                    "must include a `decision` object "
                    "(delegate/done/escalate/fanout). "
                    f"Preview: {preview!r}"
                ),
            )
        if not isinstance(data, dict):
            return NextStep(
                action="escalate",
                reason=(
                    "Task owner legacy output_summary parsed as non-object "
                    f"JSON; expected a decision object. Got: {type(data).__name__}"
                ),
            )
        try:
            return NextStep(**data)
        except (KeyError, ValueError, ValidationError) as exc:
            return NextStep(
                action="escalate",
                reason=f"Malformed task-owner decision: {exc}",
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
        model_name = self._resolve_model_name(agent_name)
        executor = self._build_executor(provider)

        # TASK-2511: materialize system-contract skills BEFORE the readiness
        # marker check. Post-Phase-4 cutover, inject_system_contracts is the
        # sole delivery path; a fresh/reset workspace has no marker until
        # injection runs. The ensure call injects + verifies, raising a named
        # SystemContractMaterializationError on failure (retry-eligible),
        # never a bare Errno 2.
        ensure_system_contracts_materialized(
            workspace, self._settings, slug=self._slug, context="task",
            provider=provider,
        )

        # The orchestrator relies on the start-task skill to bridge prompt →
        # agent work → completion callback. If the workspace was bootstrapped
        # before skills existed (or the user wiped it), the agent never calls
        # `happyranch report-completion` and the task silently rejects. Fail fast
        # with an actionable message instead.
        skill_marker = self._readiness_marker(workspace, provider)
        if not skill_marker.exists():
            raise WorkspaceNotInitialized(
                f"workspace for {agent_name!r} is not initialized "
                f"(missing {skill_marker}). Run `happyranch init-agent {agent_name}` "
                f"to bootstrap it."
            )

        # Workspace is initialized once at `happyranch init-agent` — not per session.
        # Brief is injected here:
        brief = task.brief if task else ""
        session_id = self._build_session_id()

        # THR-032 Phase 2: build the per-task memory digest from the agent's
        # MemoryStore. Ancestor task ids boost memories authored in the same
        # task lineage. Budget is org-configurable; 0 disables the digest.
        memory_digest: str | None = None
        org_config = load_org_config(self._paths)
        budget = org_config.memory_digest_budget
        if budget > 0:
            memory_dir = workspace / "memory"
            if memory_dir.exists():
                from runtime.infrastructure.learnings_store import MemoryStore
                store = MemoryStore(memory_dir)
                # Walk ancestor chain for source_task boost.
                try:
                    ancestors = self._db.walk_ancestors(task_id)
                except Exception:
                    ancestors = []
                ancestor_ids = {a.id for a in ancestors} if ancestors else None
                memory_digest = store.build_memory_digest(
                    brief=brief,
                    budget=budget,
                    ancestor_task_ids=ancestor_ids,
                    scope="agent",
                )

        managed_skills_index = resolve_managed_skills_index(
            paths=self._paths, agent_name=agent_name,
        )

        # Refresh on-disk skill bodies from the bundled protocol/skills/ on EVERY
        # session so edits to system/contract skills reach agents without a
        # lifecycle event (THR-070).
        refresh_session_skills(workspace, self._settings, slug=self._slug)

        # THR-103: fast-forward-refresh every cloned repo so the agent has
        # fresh code regardless of executor (claude/codex/opencode/pi).
        # Must run BEFORE the executor subprocess starts. Failure is non-
        # blocking: offline / dirty / non-ff / timeout are swallowed.
        refresh_workspace_repos(workspace)

        # System-contract injection was done above by ensure_system_contracts_materialized
        # (TASK-2511). Do NOT call inject_system_contracts here — it would be a
        # redundant second injection.

        # Managed-catalog skill injection (THR-055 Phase 4).
        # Resolves the two-gated catalog + eligibility policy and injects
        # managed skills (review, manage-agent, manage-repo) into the
        # workspace alongside system contracts.
        try:
            from runtime.orchestrator.prompt_loader import load_agent
            agent_def = load_agent(self._paths, agent_name)
            team = agent_def.team if agent_def else "engineering"
        except Exception:
            team = "engineering"
        skills_root = self._settings.project_root / "runtime" / "skills"
        org_root = self._paths.root
        inject_managed_skills(
            workspace, self._settings,
            slug=self._slug,
            agent_name=agent_name,
            team=team,
            skills_root=skills_root,
            org_root=org_root,
            db=self.db,
        )

        # Protocol doc manifest — bundled-path one-liner per doc (THR-070).
        protocol_doc_manifest = resolve_protocol_doc_manifest(settings=self._settings)

        full_prompt = self._build_agent_prompt(
            provider,
            agent_name,
            task_id,
            session_id,
            brief,
            prompt,
            memory_digest=memory_digest,
            managed_skills_index=managed_skills_index,
            protocol_doc_manifest=protocol_doc_manifest,
        )

        if self._sessions is not None:
            self._sessions.set_active(task_id, agent_name, session_id)
        if on_session_started is not None:
            on_session_started(task_id, agent_name, session_id)

        self._audit.log_session_start(task_id, agent_name, str(workspace))
        self._db.update_task(task_id, assigned_agent=agent_name)

        # Capture pid into SessionTracker the moment Popen returns so the
        # /cancel route can SIGTERM the subprocess mid-session without racing
        # the set_active() call above. Works for every executor because they
        # delegate to executors._run_command.
        def _on_started(pid: int) -> None:
            if self._sessions is not None:
                self._sessions.set_pid(task_id, agent_name, pid)
            # THR-079: persist executor OS pid for daemon-restart liveness probe.
            # THR-090 Track A: also persist the current session id so the
            # daemon-restart sweep can scope orphaned-result detection.
            self._db.update_task(task_id, executor_pid=pid, current_session_id=session_id)

        # Layer-1 throttle audit surfacing (issue #85): the per-provider throttle
        # in executors._run_command calls this on a slot wait or a 429 backoff.
        # Additive action+payload via the existing insert_audit_log — no new
        # columns, no row-shape change. Scoped to the task_id + agent in hand.
        def _on_throttle_event(action: str, payload: dict) -> None:
            self._db.insert_audit_log(task_id, agent_name, action, payload)

        result = executor.run(
            workspace=workspace,
            prompt=full_prompt,
            session_id=session_id,
            timeout_seconds=self._resolve_session_timeout(agent_name, task_id=task_id),
            on_started=_on_started,
            on_throttle_event=_on_throttle_event,
            model=model_name,
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
            if t.final_output_dir:
                lines.append(f"  - Output: `{t.final_output_dir}`")
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
