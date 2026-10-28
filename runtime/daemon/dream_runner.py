"""Executor-backed private dream invocations."""
from __future__ import annotations

from runtime.orchestrator.team_move import DeferredTeamMove
from runtime.orchestrator.producer_move import (
    producer_preparation_effects, require_producer_admission, await_producer_worker,
)
from runtime.daemon.startup_recovery import StartupUnavailable

import asyncio
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from runtime.config import Settings, settings as global_settings
from runtime.daemon.thread_runner import _build_executor_for_provider
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.identities.presentation import prompt_name_context
from runtime.identities.registry import read_name_metadata
from runtime.orchestrator.executors import _meaningful_stderr
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
)
from runtime.orchestrator.workspace_adapters import (
    format_repo_refresh_note,
    materialize_workspace_skills,
    refresh_workspace_repos_async,
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
    repo_refresh_note: str = "",
    active_policy_section: str = "",
    name_metadata=None,
) -> str:
    """Compose the private dream-session prompt.

    ``current_time`` is injected (fresh per dream) via the shared renderer using
    the DREAMING effective timezone (dreaming.timezone -> org.timezone ->
    machine-local -> UTC), so dream sessions carry the same local wall clock as
    every other agent session. ``now`` is injectable for tests.
    """
    from runtime.orchestrator.active_authority_policy import assert_no_reserved_team_policy_header
    assert_no_reserved_team_policy_header(task_history, source="dream task history")
    assert_no_reserved_team_policy_header(str(recent_audit), source="dream recent audit")
    tz, label = resolve_dreaming_timezone_display(org_config)
    current_time = render_current_time_line(tz, label, now)
    skills_block = f"\n{managed_skills_index}\n" if managed_skills_index else ""
    repo_refresh_block = f"\n{repo_refresh_note}\n" if repo_refresh_note else ""
    return f"""# Private Nightly Dream

You are {dream.agent_name}. This is private reflection for HappyRanch org `{org_slug}`.
This is not a task or thread. Do not call report-completion.

{prompt_name_context(name_metadata, dream.agent_name)}current_time: {current_time}{skills_block}{repo_refresh_block}
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
{active_policy_section}
"""


def _load_task_history(workspace: Path) -> str:
    path = workspace / "task_history.md"
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")[-20000:]


def _executor_name(paths: OrgPaths, agent_name: str) -> str:
    """THR-095: resolve executor from org/agents/<name>.md (single source).

    Legacy standalone lookup; invocation owners select executor/model/effort
    from their already-loaded definition. Dreams use ``_active_agent_def``
    for fail-closed admission.
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


async def _run_dream(
    *,
    org_state,
    dream_id: str,
    settings: Settings = global_settings,
    executor_factory: Callable | None = None,
    admission_gate: Callable[[], Awaitable[None]] | None = None,
    host_supervisor: HostSessionSupervisor | None = None,
    _move_owner=None,
) -> None:
    require_producer_admission(org_state)
    if (org_state._startup_state is not None
            and host_supervisor is not org_state._startup_state.host_supervisor):
        raise StartupUnavailable("startup_attachment_changed")
    dream = org_state.db.get_dream(dream_id)
    if dream is None or (dream.status != DreamStatus.PENDING and not (_move_owner is not None and dream.status == DreamStatus.RUNNING)):
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
        require_producer_admission(org_state)
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
            transitioned = dream.status == DreamStatus.RUNNING or org_state.db.update_dream_status_if(
                dream_id, DreamStatus.PENDING, DreamStatus.RUNNING, started_at=now,
            )

    if agent_def is None:
        if transitioned:
            AuditLogger(org_state.db).log_dream_failed(
                dream_id, dream.agent_name, reason="agent_unavailable",
            )
        return
    if not transitioned:
        return

    # Resolve the same effective tuple later passed to executor.run before
    # emitting the lifecycle start event. A missing/terminated agent returned
    # above, so this fallback applies only to an active agent whose configured
    # provider is not registered.
    _prov = agent_def.executor.lower()
    if not get_registry().is_registered(_prov):
        _prov = "claude"
    model_name: str | None = agent_def.model
    reasoning_effort = agent_def.reasoning_effort
    AuditLogger(org_state.db).log_dream_started(
        dream_id,
        dream.agent_name,
        executor=_prov,
        model=model_name, reasoning_effort=reasoning_effort,
    )

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

    agent_team = agent_def.team

    workspace = org_state.root / "workspaces" / dream.agent_name

    # Issue #536: serialize the complete pre-spawn skill materialization
    # transaction under a process-local workspace lock.
    # FAIL-CLOSED: a materialization error must persist a terminal failure
    # and return BEFORE executor spawn (REVISE TASK-2829).
    session_id = _move_owner.session_id if _move_owner is not None else f"sess-{uuid.uuid4().hex}"
    try:
        require_producer_admission(org_state)
        with producer_preparation_effects(_move_owner):
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
    except (StartupUnavailable, DeferredTeamMove):
        raise
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
    # Await the joined offload BEFORE executor launch, inside the SAME native
    # effects scope. Failed/skipped refreshes warn of stale source; executed
    # failures retain uncertainty. Cancellation waits for worker completion.
    require_producer_admission(org_state)
    repo_refresh_results = await refresh_workspace_repos_async(workspace, org=org_state,
        preparation=_move_owner, host_supervisor=host_supervisor,
        materialization=(settings, expected_specs))

    require_producer_admission(org_state)
    repo_refresh_note = format_repo_refresh_note(repo_refresh_results)

    # ── Per-retry launch validator ───────────────────────────────
    def _pre_launch_validator():
        require_producer_admission(org_state)
        with producer_preparation_effects(_move_owner):
            validate_workspace_skills_integrity(
                workspace, expected_specs,
                settings=settings,
                db=org_state.db,
                agent_name=dream.agent_name,
                task_id=dream_id,
            )

    from runtime.orchestrator.active_authority_policy import resolve_active_team_policy_section
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
    policy_teams = getattr(org_state, "teams", None)
    active_policy_section = "" if policy_teams is None else resolve_active_team_policy_section(
        store=AuthorityPolicyStore(org_state.db), root=org_state.root,
        teams=policy_teams, team=agent_def.team,
        agent_name=dream.agent_name,
        eligible=policy_teams.is_team_manager(dream.agent_name),
    )
    prompt = build_dream_prompt(
        org_slug=org_state.slug,
        dream=dream,
        workspace=workspace,
        recent_audit=recent_audit,
        task_history=_load_task_history(workspace),
        org_config=org_config,
        managed_skills_index=managed_skills_index,
        repo_refresh_note=repo_refresh_note,
        active_policy_section=active_policy_section,
        name_metadata=read_name_metadata(org_state),
    )

    executor_name = _prov  # already resolved above (TASK-2511)
    executor = executor_factory(executor_name, settings, paths) if executor_factory else _build_executor_for_provider(executor_name, settings, paths)

    from runtime.orchestrator.producer_move import bind_producer_executor
    if _move_owner is not None:
        _move_owner.executor, _move_owner.model = executor_name, model_name
    executor = bind_producer_executor(executor, _move_owner)

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
        def _run_uncontained() -> Any:
            require_producer_admission(org_state)
            return executor.run(
                workspace=workspace,
                prompt=prompt,
                session_id=session_id,
                timeout_seconds=settings.session_timeout_seconds,
                pre_launch_validator=_pre_launch_validator,
                org_slug=org_state.slug,
                model=model_name, reasoning_effort=reasoning_effort,
            )
        result = await await_producer_worker(
            _run_uncontained, org=org_state, preparation=_move_owner, loop=loop,
        )
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
            require_producer_admission(org_state)
            with producer_preparation_effects(_move_owner):
                launch_spec = spec_builder(
                    workspace=workspace,
                    prompt=prompt,
                    session_id=session_id,
                    model=model_name, reasoning_effort=reasoning_effort,
                    org_slug=org_state.slug,
                    timeout_seconds=settings.session_timeout_seconds,
                )
        except (StartupUnavailable, DeferredTeamMove):
            raise
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
            if not contained:
                try:
                    if _move_owner is not None:
                        _move_owner.require_admission()
                    require_producer_admission(org_state)
                except StartupUnavailable as exc:
                    if _move_owner is not None:
                        _move_owner.admission_refused(exc)
                    raise
            res = executor.run(
                workspace=workspace,
                prompt=prompt,
                session_id=session_id,
                timeout_seconds=settings.session_timeout_seconds,
                pre_launch_validator=_pre_launch_validator if not contained else None,
                org_slug=org_state.slug,
                model=model_name, reasoning_effort=reasoning_effort,
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

        outcome = await await_producer_worker(
            lambda: host_supervisor.run(
                AdmissionRequest(
                    org=org_state.slug,
                    invocation_kind="dream",
                    move_reservation=_move_owner,
                    on_started=(lambda _pid: _move_owner.on_started()) if _move_owner is not None else None,
                    agent_id=dream.agent_name,
                    logical_id=dream_id,
                    executor_profile=executor_name,
                    enqueued_at=_move_owner.enqueued_at if _move_owner is not None else time.monotonic(),
                ),
                launch_spec=launch_spec,
                launch_body=_launch_body,
                pre_launch_validator=_pre_launch_validator,
                final_prelaunch_validator=_move_owner,
            ),
            org=org_state, preparation=_move_owner, loop=loop,
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
    terminal_error = str(getattr(result, "terminal_error", "") or "").strip()
    human_error = str(getattr(result, "human_error", "") or "")
    stderr = human_error or (
        "" if getattr(result, "human_error_inspected", False) else _meaningful_stderr(
            str(getattr(result, "stderr_tail", "") or "")
        )
    )
    notice = str(getattr(result, "terminal_error_notice", "") or "").strip()
    # Dreams have no diagnostic-tail columns.  Preserve the classified cause,
    # reset notice, and any meaningful stderr together on their existing
    # error/reason surfaces; do not replace a real provider error with a token.
    if terminal_error:
        reason = stderr[:1000] if stderr else terminal_error
        if stderr:
            reason = f"{reason} (terminal_error: {terminal_error})"
        if notice:
            reason = f"{reason}; notice: {notice[:1000]}"
    else:
        reason = error[:1000]
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
        AuditLogger(org_state.db).log_dream_timeout(
            dream_id, dream.agent_name, reason=error,
        )
        return
    org_state.db.update_dream(
        dream_id,
        status=DreamStatus.FAILED,
        ended_at=datetime.now(timezone.utc),
        error=reason,
    )
    AuditLogger(org_state.db).log_dream_failed(dream_id, dream.agent_name, reason=reason)


async def run_dream(
    *, org_state, dream_id: str, settings: Settings = global_settings,
    executor_factory: Callable | None = None,
    admission_gate: Callable[[], Awaitable[None]] | None = None,
    host_supervisor: HostSessionSupervisor | None = None,
) -> None:
    require_producer_admission(org_state)
    if (org_state._startup_state is not None
            and host_supervisor is not org_state._startup_state.host_supervisor):
        raise StartupUnavailable("startup_attachment_changed")
    with org_state._runner_lifetime() as admitted:
        if not admitted:
            return
        from runtime.orchestrator.producer_move import run_native_producer
        await run_native_producer(org_state, 'dream', dream_id, lambda owner: _run_dream(
            org_state=org_state, dream_id=dream_id, settings=settings,
            executor_factory=executor_factory, admission_gate=admission_gate, host_supervisor=host_supervisor, _move_owner=owner))
