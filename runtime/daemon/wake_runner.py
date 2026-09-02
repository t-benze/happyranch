"""Working-hours wake invocations.

``build_wake_prompt`` is the pure, unit-testable prompt composition (mirroring
``dream_runner.build_dream_prompt``). The wake prompt is composed HERE in the
daemon runner — no ``protocol/`` edit is needed to ship the mechanism.
``run_wake`` is the executor-backed invocation (mirroring ``run_dream``): it
loads the waking agent's routine checklist, runs one executor session whose only
job is to self-dispatch via ``work-hours spawn``, records token usage under
``scope_type="work_hour"``, and resolves the terminal status. The spawn callback
itself (which creates the root tasks and marks the wake ``completed``) lives in
``routes/work_hours.py``; on no-callback/failure/timeout this runner is the one
that transitions the row.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Callable

from runtime.config import Settings, settings as global_settings
from runtime.daemon.dream_runner import _executor_name, _is_timeout
from runtime.daemon.thread_runner import _build_executor_for_provider
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import WorkHourStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.executor_registry import get_registry
from runtime.orchestrator.host_supervisor import (
    AdmissionRequest,
    HostSessionSupervisor,
    LaunchResult,
    TerminalReason,
)
from runtime.orchestrator.org_config import (
    OrgConfig,
    load_org_config,
    render_current_time_line,
    resolve_managed_skills_index,
    resolve_org_timezone_display,
    resolve_protocol_doc_manifest,
)
from runtime.orchestrator.workspace_adapters import (
    format_repo_refresh_note,
    materialize_workspace_skills,
    refresh_workspace_repos,
    validate_workspace_skills_integrity,
    SystemContractMaterializationError,
)
from runtime.orchestrator.prompt_loader import load_agent
from runtime.orchestrator.routine_parser import parse_routines


def build_wake_prompt(
    *,
    org_slug: str,
    work_hour_id: str,
    agent_name: str,
    role: str,
    team: str,
    local_date: str,
    slot: str,
    mode: str,
    preamble: str,
    routines: list[str],
    org_config: OrgConfig,
    dropped: int = 0,
    now: Callable[[], datetime] | None = None,
    managed_skills_index: str = "",
    protocol_doc_manifest: str = "",
    active_policy_section: str = "",
) -> str:
    """Compose the wake-session prompt.

    The wake is a TRIGGER, not the work: the session's only job is to translate
    each routine list item into one concrete root-task brief and submit them in
    a SINGLE ``work-hours spawn --from-file`` callback. The parsed
    ``## Routine Tasks`` section (preamble + list) is injected verbatim, and the
    cadence (local_date, slot, mode) is stated so briefs can be phrased relative
    to the last wake.

    ``current_time`` is injected (fresh per wake) via the shared renderer using
    the org's effective timezone, so wake sessions carry the same local wall
    clock as every other agent session. ``now`` is injectable for tests.
    """
    tz, label = resolve_org_timezone_display(org_config)
    current_time = render_current_time_line(tz, label, now)
    skills_block = f"\n{managed_skills_index}\n" if managed_skills_index else ""
    docs_block = f"\n{protocol_doc_manifest}\n" if protocol_doc_manifest else ""
    routine_block = "\n".join(routines) if routines else "(none)"
    preamble_block = f"{preamble}\n\n" if preamble else ""
    # No silent truncation: if routines were dropped past the cap, tell the
    # session so it does not assume the list below is the agent's full set.
    dropped_block = (
        f"\nNOTE: {dropped} routine(s) beyond the per-wake cap were DROPPED and "
        f"are NOT included below; only the first {len(routines)} are shown.\n"
        if dropped > 0 else ""
    )
    return f"""# Working-Hours Wake

You are {agent_name} ({role}) on the {team} team in HappyRanch org `{org_slug}`.
This is a WORKING-HOURS WAKE: a scheduled trigger to launch your standing
routines. It is NOT the work itself, and it is NOT a reflection. The real work
happens in the root tasks you spawn — do not perform the routines here.

Cadence: local_date {local_date}, slot {slot}, mode {mode}.
current_time: {current_time}{skills_block}{docs_block}
Turn EACH routine below into ONE concrete root-task brief (phrased for the work
due since the last wake at this cadence), then submit them ALL in a SINGLE
callback:

happyranch work-hours spawn --org {org_slug} --work-hour-id {work_hour_id} --from-file /tmp/wake-{work_hour_id}.json

Do not call create_task directly and do not dispatch other agents: the spawn
endpoint creates the root tasks on your own team, targeted to you as executor.

## Routine Tasks (verbatim from your agent file)
{dropped_block}
{preamble_block}{routine_block}
{active_policy_section}
"""


async def run_wake(
    *,
    org_state,
    work_hour_id: str,
    settings: Settings = global_settings,
    executor_factory: Callable | None = None,
    host_supervisor: HostSessionSupervisor | None = None,
) -> None:
    """Run one working-hours wake session.

    Mirrors ``run_dream``: transition ``pending -> running``, compose the wake
    prompt (with the parsed ``## Routine Tasks`` section), invoke the agent's
    executor in its workspace, record token usage under the ``work_hour`` scope,
    and resolve the terminal status. The ``work-hours spawn`` callback marks the
    row ``completed``; if the session returns without calling it, the row is
    failed (``no_callback``) or timed out, and no tasks are spawned.
    """
    store = org_state.db.work_hours
    record = store.get(work_hour_id)
    if record is None or record.status != WorkHourStatus.PENDING:
        return

    paths = OrgPaths(root=org_state.root)
    agent_def = load_agent(paths, record.agent_name)
    now = datetime.now(timezone.utc)
    store.update(work_hour_id, status=WorkHourStatus.RUNNING, started_at=now)
    AuditLogger(org_state.db).log_work_hour_started(work_hour_id, record.agent_name)

    if agent_def is None:
        # The agent file vanished between scheduling and running. Fail cleanly;
        # the unique (agent, local_date, slot) row blocks a re-attempt.
        store.update(
            work_hour_id, status=WorkHourStatus.FAILED,
            ended_at=datetime.now(timezone.utc), error="agent_not_found",
        )
        AuditLogger(org_state.db).log_work_hour_failed(
            work_hour_id, record.agent_name, reason="agent_not_found",
        )
        return

    # Issue #568: forward AgentDef.model to executor.run for wake invocations.
    model_name: str | None = agent_def.model

    parsed = parse_routines(agent_def.system_prompt)
    workspace = org_state.root / "workspaces" / record.agent_name
    session_id = f"sess-{uuid.uuid4().hex}"
    try:
        org_config = load_org_config(paths)
    except Exception:
        org_config = OrgConfig()
    managed_skills_index = resolve_managed_skills_index(
        paths=paths, agent_name=record.agent_name,
    )

    # TASK-2511: resolve executor name early so we can pass provider to the
    # materialization guard before spawn.
    _prov = _executor_name(paths, record.agent_name)
    if not get_registry().is_registered(_prov):
        _prov = "claude"

    # Issue #536: serialize the complete pre-spawn skill materialization
    # transaction under a process-local workspace lock.
    # FAIL-CLOSED: a materialization error must persist a terminal failure
    # and return BEFORE executor spawn (REVISE TASK-2829).
    try:
        skills_root = settings.project_root / "runtime" / "skills"
        expected_specs = materialize_workspace_skills(
            workspace, settings,
            slug=org_state.slug,
            context="wake",
            provider=_prov,
            agent_name=record.agent_name,
            team=agent_def.team,
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
            agent_name=record.agent_name,
            task_id=work_hour_id,
        )
    except Exception as e:
        store.update(
            work_hour_id, status=WorkHourStatus.FAILED,
            ended_at=datetime.now(timezone.utc),
            error=f"materialization_failed: {e}",
        )
        AuditLogger(org_state.db).log_work_hour_failed(
            work_hour_id, record.agent_name,
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
        validate_workspace_skills_integrity(
            workspace, expected_specs,
            settings=settings,
            db=org_state.db,
            agent_name=record.agent_name,
            task_id=work_hour_id,
        )

    from runtime.orchestrator.active_authority_policy import resolve_active_team_policy_section
    from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
    active_policy_section = resolve_active_team_policy_section(
        store=AuthorityPolicyStore(org_state.db), team=agent_def.team,
        agent_name=record.agent_name,
        eligible=bool(getattr(org_state, "teams", None) and org_state.teams.is_team_manager(record.agent_name)),
    )
    prompt = build_wake_prompt(
        org_slug=org_state.slug,
        work_hour_id=work_hour_id,
        agent_name=record.agent_name,
        role=str(agent_def.role),
        team=agent_def.team,
        local_date=record.local_date,
        slot=record.slot,
        mode=record.mode.value,
        preamble=parsed.preamble,
        routines=parsed.routines,
        org_config=org_config,
        dropped=parsed.dropped,
        managed_skills_index=managed_skills_index,
        protocol_doc_manifest=protocol_doc_manifest,
        active_policy_section=active_policy_section,
    )

    executor_name = _prov  # already resolved above (TASK-2511)
    executor = (
        executor_factory(executor_name, settings, paths) if executor_factory
        else _build_executor_for_provider(executor_name, settings, paths)
    )

    # ── D7B: CustomAdapterExecutor invocation context ──────────────────
    if hasattr(executor, 'set_invocation_context'):
        executor.set_invocation_context(
            agent=record.agent_name,
            org=org_state.slug,
            invocation_kind="wake",
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
        # ── THR-207 supervised wiring: the wake session runs through the
        # daemon-wide HostSessionSupervisor (admission lease, atomic ownership
        # at grant, real backend launch into containment, opaque cancellation,
        # containment cleanup before exactly-once lease release). The executor
        # and its per-provider throttle stay inside the launch body unchanged.
        spec_builder = getattr(executor, "build_launch_spec", None)
        if spec_builder is None:
            # Fail closed: no contained-launch seam — mirror the task
            # producer's fail-closed behavior.
            no_seam = (
                f"executor {type(executor).__name__!r} does not support "
                "contained launch (build_launch_spec missing)"
            )
            store.update(
                work_hour_id, status=WorkHourStatus.FAILED,
                ended_at=datetime.now(timezone.utc), error=no_seam,
            )
            AuditLogger(org_state.db).log_work_hour_failed(
                work_hour_id, record.agent_name, reason=no_seam,
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
            store.update(
                work_hour_id, status=WorkHourStatus.FAILED,
                ended_at=datetime.now(timezone.utc),
                error=f"launch_spec_failed: {exc}",
            )
            AuditLogger(org_state.db).log_work_hour_failed(
                work_hour_id, record.agent_name,
                reason=f"launch_spec_failed: {exc}",
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
                    invocation_kind="wake",
                    logical_id=work_hour_id,
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
            # (``org.db.work_hours.recover_running()``) — the pre-wiring shutdown
            # semantics when a worker was cancelled mid-run.
            return
        launch = outcome.payload
        if launch is None:
            # Pre-launch terminal winner (validator failure / prepare or spawn
            # failure): nothing ran — fail closed with the durable first-wins
            # reason, mirroring the materialization-failure path.
            reason = f"session {outcome.terminal_reason.value} before launch"
            if outcome.error:
                reason = f"{reason}: {outcome.error}"
            store.update(
                work_hour_id, status=WorkHourStatus.FAILED,
                ended_at=datetime.now(timezone.utc), error=reason,
            )
            AuditLogger(org_state.db).log_work_hour_failed(
                work_hour_id, record.agent_name, reason=reason,
            )
            return
        result = launch.payload

    if getattr(result, "token_usage", None) is not None:
        org_state.db.insert_session_token_usage(
            task_id=None,
            agent=record.agent_name,
            session_id=getattr(result, "agent_session_id", None) or getattr(result, "session_id", None) or work_hour_id,
            executor=executor_name,
            token_usage=result.token_usage,
            scope_type="work_hour",
            scope_id=work_hour_id,
        )

    refreshed = store.get(work_hour_id)
    if refreshed is None or refreshed.status == WorkHourStatus.COMPLETED:
        # The spawn callback already drove the row to completed (or it vanished).
        return
    if result.success:
        # The session exited 0 but never called `work-hours spawn`.
        store.update(
            work_hour_id, status=WorkHourStatus.FAILED,
            ended_at=datetime.now(timezone.utc),
            session_id=getattr(result, "agent_session_id", None) or getattr(result, "session_id", None),
            error="no_callback",
        )
        AuditLogger(org_state.db).log_work_hour_failed(
            work_hour_id, record.agent_name, reason="no_callback",
        )
        return
    error = str(getattr(result, "error", "") or "executor_failed")
    if _is_timeout(result):
        store.update(
            work_hour_id, status=WorkHourStatus.TIMEOUT,
            ended_at=datetime.now(timezone.utc), error=error,
        )
        AuditLogger(org_state.db).log_work_hour_timeout(
            work_hour_id, record.agent_name, reason=error,
        )
        return
    store.update(
        work_hour_id, status=WorkHourStatus.FAILED,
        ended_at=datetime.now(timezone.utc), error=error,
    )
    AuditLogger(org_state.db).log_work_hour_failed(
        work_hour_id, record.agent_name, reason=error,
    )
