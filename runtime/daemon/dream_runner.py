"""Executor-backed private dream invocations."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

from runtime.config import Settings, settings as global_settings
from runtime.daemon.thread_runner import _build_executor_for_provider
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.orchestrator.executor_registry import get_registry
from runtime.models import DreamRecord, DreamStatus
from runtime.orchestrator.host_supervisor import (
    AdmissionRequest,
    HostSessionSupervisor,
    LaunchResult,
    TerminalReason,
)
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.prompt_loader import is_terminated, load_agent
from runtime.orchestrator.org_config import (
    OrgConfig,
    load_org_config,
    render_current_time_line,
    resolve_dreaming_timezone_display,
    resolve_managed_skills_index,
    resolve_protocol_doc_manifest,
)
from runtime.orchestrator.workspace_adapters import (
    format_repo_refresh_note,
    materialize_workspace_skills,
    prepare_workspace_skills_launch,
    refresh_workspace_repos,
    validate_workspace_skills_integrity,
    SystemContractMaterializationError,
)

# Cap on the agent's window audit rows folded into the dream prompt. The most
# recent N (chronological); keeps the prompt bounded on busy agents.
_AUDIT_WINDOW_CAP = 200


def _is_timeout(result) -> bool:
    """Distinguish an executor timeout from an ordinary non-zero exit. Timeouts
    leave returncode=None and carry the executor's 'timed out' error string
    (see runtime/orchestrator/executors.py)."""
    err = str(getattr(result, "error", "") or "").lower()
    return "timed out" in err or "timeout" in err


def build_dream_prompt(
    *,
    org_slug: str,
    dream: DreamRecord,
    workspace: Path,
    recent_audit: list[dict],
    task_history: str,
    org_config: OrgConfig,
    now: Callable[[], datetime] | None = None,
    managed_skills_index: str = "",
    protocol_doc_manifest: str = "",
) -> str:
    """Compose the private dream-session prompt.

    ``current_time`` is injected (fresh per dream) via the shared renderer using
    the DREAMING effective timezone (dreaming.timezone -> org.timezone ->
    machine-local -> UTC), so dream sessions carry the same local wall clock as
    every other agent session. ``now`` is injectable for tests.
    """
    tz, label = resolve_dreaming_timezone_display(org_config)
    current_time = render_current_time_line(tz, label, now)
    skills_block = f"\n{managed_skills_index}\n" if managed_skills_index else ""
    docs_block = f"\n{protocol_doc_manifest}\n" if protocol_doc_manifest else ""
    return f"""# Private Nightly Dream

You are {dream.agent_name}. This is private reflection for HappyRanch org `{org_slug}`.
This is not a task or thread. Do not call report-completion.

current_time: {current_time}{skills_block}{docs_block}
Dream id: {dream.id}
Window start: {dream.window_start.isoformat() if dream.window_start else "last 24 hours"}
Window end: {dream.window_end.isoformat()}

Review recent work, recurring friction, stale assumptions, contradictions, and durable lessons.
Write KB candidate bodies to temporary markdown files, then complete with:

happyranch dreams complete --org {org_slug} --dream-id {dream.id} --from-file /tmp/dream-result-{dream.id}.json

Task history:
{task_history}

Recent audit:
{recent_audit}
"""


def _load_task_history(workspace: Path) -> str:
    path = workspace / "task_history.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")[-20000:]


def _executor_name(paths: OrgPaths, agent_name: str) -> str:
    """THR-095: resolve executor from org/agents/<name>.md (single source).

    Kept for schedule_runner / wake_runner compatibility. Dreams use
    ``_active_agent_def`` for fail-closed admission.
    """
    try:
        agent_def = load_agent(paths, agent_name)
        return (agent_def.executor if agent_def else "claude").lower()
    except Exception:
        return "claude"


def _active_agent_def(paths: OrgPaths, agent_name: str) -> "AgentDef | None":
    """Return the active AgentDef, or None if missing or terminated.

    Dreams must fail-closed: a missing or archived agent must never fall back
    to the claude executor.
    """
    agent_def = load_agent(paths, agent_name)
    if agent_def is None or is_terminated(paths, agent_name):
        return None
    return agent_def


async def run_dream(
    *,
    org_state,
    dream_id: str,
    settings: Settings = global_settings,
    executor_factory: Callable | None = None,
    admission_gate: Callable[[], Awaitable[None]] | None = None,
    host_supervisor: HostSessionSupervisor | None = None,
) -> None:
    dream = org_state.db.get_dream(dream_id)
    if dream is None or dream.status != DreamStatus.PENDING:
        return

    paths = OrgPaths(root=org_state.root)

    # ── Lifecycle admission: the active-AgentDef read and the conditional
    # PENDING->RUNNING / PENDING->SKIPPED claim must serialize with a
    # concurrent termination's archive/cleanup under the shared
    # org.teams_lock so termination-vs-dream has a single durable winner.
    # The lock is held ONLY across the re-read + claim; audit,
    # materialization, repo refresh, and executor construction happen after
    # release. ``admission_gate`` is a narrow test hook awaited at the
    # admission boundary (before lock acquisition) for deterministic
    # interleaving tests.
    if admission_gate is not None:
        await admission_gate()

    now = datetime.now(timezone.utc)
    async with org_state.teams_lock:
        agent_def = _active_agent_def(paths, dream.agent_name)
        if agent_def is None:
            # Missing/terminated agent: terminal non-execution (SKIPPED).
            # Conditional so a concurrent termination's own terminal winner is
            # never overwritten; never a claude fallback.
            transitioned = org_state.db.update_dream_status_if(
                dream_id,
                DreamStatus.PENDING,
                DreamStatus.SKIPPED,
                ended_at=now,
                error="agent_unavailable",
            )
        else:
            # Atomic PENDING -> RUNNING claim. If a concurrent termination
            # terminalized the dream before this lock was acquired, the
            # conditional update fails and we return without an executor.
            transitioned = org_state.db.update_dream_status_if(
                dream_id,
                DreamStatus.PENDING,
                DreamStatus.RUNNING,
                started_at=now,
            )

    if agent_def is None:
        if transitioned:
            AuditLogger(org_state.db).log_dream_failed(
                dream_id, dream.agent_name, reason="agent_unavailable",
            )
        return
    if not transitioned:
        return
    AuditLogger(org_state.db).log_dream_started(dream_id, dream.agent_name)

    # Spec "Input Window": include the agent's audit rows since window_start,
    # not only the dream-scoped rows. window_start is set by the scheduler; fall
    # back to no lower bound (capped recent rows) if absent.
    if dream.window_start is not None:
        recent_audit = org_state.db.get_audit_logs_for_agent_since(
            dream.agent_name, dream.window_start.isoformat(), limit=_AUDIT_WINDOW_CAP,
        )
    else:
        recent_audit, _ = org_state.db.query_audit_logs(
            agent=dream.agent_name, limit=_AUDIT_WINDOW_CAP,
        )
    try:
        org_config = load_org_config(paths)
    except Exception:
        org_config = OrgConfig()
    managed_skills_index = resolve_managed_skills_index(
        paths=paths, agent_name=dream.agent_name,
    )

    # TASK-2511: resolve executor name from the active AgentDef. Missing agents
    # were already rejected above, so this path never silently falls back to
    # claude because of a missing or terminated AgentDef.
    _prov = agent_def.executor.lower()
    if not get_registry().is_registered(_prov):
        _prov = "claude"

    agent_team = agent_def.team

    # Issue #568: forward AgentDef.model to executor.run for dream invocations.
    model_name: str | None = agent_def.model

    workspace = org_state.root / "workspaces" / dream.agent_name

    # Issue #536: serialize the complete pre-spawn skill materialization
    # transaction under a process-local workspace lock.
    # FAIL-CLOSED: a materialization error must persist a terminal failure
    # and return BEFORE executor spawn (REVISE TASK-2829).
    session_id = f"sess-{uuid.uuid4().hex}"
    try:
        skills_root = settings.project_root / "runtime" / "skills"
        expected_specs = materialize_workspace_skills(
            workspace, settings,
            slug=org_state.slug,
            context="dream",
            provider=_prov,
            agent_name=dream.agent_name,
            team=agent_team,
            skills_root=skills_root,
            org_root=org_state.root,
            db=org_state.db,
            session_id=session_id,
        )

        # ── Pre-launch integrity validation ─────────────────────
        validate_workspace_skills_integrity(
            workspace, expected_specs,
            settings=settings,
            db=org_state.db,
            agent_name=dream.agent_name,
            task_id=dream_id,
        )
    except Exception as e:
        org_state.db.update_dream(
            dream_id,
            status=DreamStatus.FAILED,
            ended_at=datetime.now(timezone.utc),
            error=f"materialization_failed: {e}",
        )
        AuditLogger(org_state.db).log_dream_failed(
            dream_id, dream.agent_name,
            reason=f"materialization_failed: {e}",
        )
        return

    # THR-103: fast-forward-refresh every cloned repo so the agent has
    # fresh code regardless of executor (claude/codex/opencode/pi).
    # Must run BEFORE the executor subprocess starts. Failure is non-
    # blocking: offline / dirty / non-ff / timeout are swallowed.
    repo_refresh_results = refresh_workspace_repos(workspace)

    protocol_doc_manifest = "\n".join(filter(None, (
        resolve_protocol_doc_manifest(settings=settings),
        format_repo_refresh_note(repo_refresh_results),
    )))

    # ── Per-retry launch validator ───────────────────────────────
    def _pre_launch_validator():
        return prepare_workspace_skills_launch(
            workspace, settings, slug=org_state.slug, context="dream",
            provider=_prov, agent_name=dream.agent_name, team=agent_def.team,
            skills_root=skills_root, org_root=org_state.root, db=org_state.db,
            task_id=dream_id,
        )

    prompt = build_dream_prompt(
        org_slug=org_state.slug,
        dream=dream,
        workspace=workspace,
        recent_audit=recent_audit,
        task_history=_load_task_history(workspace),
        org_config=org_config,
        managed_skills_index=managed_skills_index,
        protocol_doc_manifest=protocol_doc_manifest,
    )

    executor_name = _prov  # already resolved above (TASK-2511)
    executor = executor_factory(executor_name, settings, paths) if executor_factory else _build_executor_for_provider(executor_name, settings, paths)

    # ── D7B: CustomAdapterExecutor invocation context ──────────────────
    if hasattr(executor, 'set_invocation_context'):
        executor.set_invocation_context(
            agent=dream.agent_name,
            org=org_state.slug,
            invocation_kind="dream",
            task_id=None,
        )

    loop = asyncio.get_running_loop()
    result = None
    if host_supervisor is None:
        # ── Legacy uncontained path (unchanged) ──
        result = await loop.run_in_executor(None, lambda: executor.run(
            workspace=workspace,
            prompt=prompt,
            session_id=session_id,
            timeout_seconds=settings.session_timeout_seconds,
            pre_launch_validator=_pre_launch_validator,
            org_slug=org_state.slug,
            model=model_name,
        ))
    else:
        # ── THR-207 supervised wiring: the dream session runs through the
        # daemon-wide HostSessionSupervisor (admission lease, atomic ownership
        # at grant, real backend launch into containment, opaque cancellation,
        # containment cleanup before exactly-once lease release). The executor
        # and its per-provider throttle stay inside the launch body unchanged.
        spec_builder = getattr(executor, "build_launch_spec", None)
        if spec_builder is None:
            # Fail closed: no contained-launch seam — mirror the task
            # producer's fail-closed behavior.
            no_seam_error = (
                f"executor {type(executor).__name__!r} does not support "
                "contained launch (build_launch_spec missing)"
            )
            org_state.db.update_dream(
                dream_id,
                status=DreamStatus.FAILED,
                ended_at=datetime.now(timezone.utc),
                error=no_seam_error,
            )
            AuditLogger(org_state.db).log_dream_failed(
                dream_id, dream.agent_name, reason=no_seam_error,
            )
            return
        try:
            launch_spec = spec_builder(
                workspace=workspace,
                prompt=prompt,
                session_id=session_id,
                model=model_name,
                org_slug=org_state.slug,
                timeout_seconds=settings.session_timeout_seconds,
            )
        except Exception as exc:
            org_state.db.update_dream(
                dream_id,
                status=DreamStatus.FAILED,
                ended_at=datetime.now(timezone.utc),
                error=f"launch_spec_failed: {exc}",
            )
            AuditLogger(org_state.db).log_dream_failed(
                dream_id, dream.agent_name, reason=f"launch_spec_failed: {exc}",
            )
            return

        def _launch_body(running) -> LaunchResult:
            # Real backend: the subprocess was already launched into
            # containment — the executor communicates + parses only. Honest
            # passthrough: no containment capability — the executor
            # self-launches exactly as the legacy path, with the throttle's
            # internal 429 retry disabled so the supervisor owns the
            # finish/release/sleep/reacquire lifecycle.
            contained = running.process is not None
            res = executor.run(
                workspace=workspace,
                prompt=prompt,
                session_id=session_id,
                timeout_seconds=settings.session_timeout_seconds,
                pre_launch_validator=_pre_launch_validator if not contained else None,
                org_slug=org_state.slug,
                model=model_name,
                running=running if contained else None,
                throttle_backoff_seconds=() if not contained else None,
            )
            return LaunchResult(
                success=res.success,
                duration_seconds=float(getattr(res, "duration_seconds", 0) or 0),
                returncode=getattr(res, "returncode", None),
                error=getattr(res, "error", None),
                rate_limited=bool(getattr(res, "rate_limited", False)),
                timed_out=_is_timeout(res),
                payload=res,
            )

        outcome = await loop.run_in_executor(
            None,
            lambda: host_supervisor.run(
                AdmissionRequest(
                    org=org_state.slug,
                    invocation_kind="dream",
                    logical_id=dream_id,
                    executor_profile=executor_name,
                    enqueued_at=time.monotonic(),
                ),
                launch_spec=launch_spec,
                launch_body=_launch_body,
                pre_launch_validator=_pre_launch_validator,
            ),
        )
        if outcome.terminal_reason in (TerminalReason.SHUTDOWN, TerminalReason.CANCELLED):
            # Daemon drain / cancellation interrupted the invocation: leave the
            # RUNNING row for daemon-restart recovery
            # (``recover_running_dreams``) — the pre-wiring shutdown semantics
            # when a worker was cancelled mid-run.
            return
        launch = outcome.payload
        if launch is None:
            # Pre-launch terminal winner (validator failure / prepare or spawn
            # failure): nothing ran — fail closed with the durable first-wins
            # reason, mirroring the materialization-failure path.
            reason = f"session {outcome.terminal_reason.value} before launch"
            if outcome.error:
                reason = f"{reason}: {outcome.error}"
            org_state.db.update_dream(
                dream_id,
                status=DreamStatus.FAILED,
                ended_at=datetime.now(timezone.utc),
                error=reason,
            )
            AuditLogger(org_state.db).log_dream_failed(
                dream_id, dream.agent_name, reason=reason,
            )
            return
        result = launch.payload

    if getattr(result, "token_usage", None) is not None:
        org_state.db.insert_session_token_usage(
            task_id=None,
            agent=dream.agent_name,
            session_id=getattr(result, "agent_session_id", None) or getattr(result, "session_id", None) or dream_id,
            executor=executor_name,
            token_usage=result.token_usage,
            scope_type="dream",
            scope_id=dream_id,
        )

    refreshed = org_state.db.get_dream(dream_id)
    if refreshed is None:
        return
    if refreshed.status == DreamStatus.COMPLETED:
        return
    if result.success:
        org_state.db.update_dream(
            dream_id,
            status=DreamStatus.FAILED,
            ended_at=datetime.now(timezone.utc),
            session_id=getattr(result, "agent_session_id", None) or getattr(result, "session_id", None),
            error="no_callback",
        )
        AuditLogger(org_state.db).log_dream_failed(dream_id, dream.agent_name, reason="no_callback")
        return
    error = str(getattr(result, "error", "") or "executor_failed")
    # THR-116: prefer a classified terminal reason extracted from structured
    # executor output (e.g. Claude's JSON result envelope) over the raw
    # stderr-based error summary so dream failures carry a deterministic
    # reason instead of incidental noise.
    reason = (getattr(result, "terminal_error", None) or error)
    if _is_timeout(result):
        # Spec "Failure Handling": timeout is a distinct terminal status; the
        # successful-dream window is not advanced (get_last_successful_dream
        # only counts COMPLETED).
        org_state.db.update_dream(
            dream_id,
            status=DreamStatus.TIMEOUT,
            ended_at=datetime.now(timezone.utc),
            error=error,
        )
        AuditLogger(org_state.db).log_dream_timeout(dream_id, dream.agent_name, reason=error)
        return
    org_state.db.update_dream(
        dream_id,
        status=DreamStatus.FAILED,
        ended_at=datetime.now(timezone.utc),
        error=reason,
    )
    AuditLogger(org_state.db).log_dream_failed(dream_id, dream.agent_name, reason=reason)
