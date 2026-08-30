"""Schedule fire invocations.

``build_schedule_prompt`` is the pure, unit-testable prompt composition (mirroring
``build_wake_prompt``). The schedule prompt is composed HERE in the daemon runner
— no ``protocol/`` edit is needed to ship the mechanism.
``run_schedule`` is the executor-backed invocation (mirroring ``run_wake``): it
loads the schedule, runs one executor session whose only job is to self-dispatch
via ``schedules spawn``, records token usage under ``scope_type="schedule"``, and
resolves the terminal status. The spawn callback itself (which creates the root
task and marks the schedule ``fired`` / re-arms) lives in
``routes/schedules.py``; on no-callback/failure/timeout this runner is the one
that transitions the row.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Callable

from runtime.config import Settings, settings as global_settings
from runtime.daemon.dream_runner import _executor_name, _is_timeout
from runtime.daemon.thread_runner import _build_executor_for_provider
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import ScheduleKind, ScheduleStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.executor_registry import get_registry
from runtime.orchestrator.host_supervisor import (
    AdmissionRequest,
    HostSessionSupervisor,
    LaunchResult,
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
    prepare_workspace_skills_launch,
    refresh_workspace_repos,
    validate_workspace_skills_integrity,
)
from runtime.skills.system_contracts import SessionContext
from runtime.orchestrator.prompt_loader import load_agent
from runtime.orchestrator.schedule_rules import (
    next_schedule_occurrence,
    recurrence_until_exhausted,
)


def _failure_transition(store, record, now: datetime, error: str) -> None:
    """Advance repeating schedules after an attempted occurrence failed.

    This intentionally does not retry the claimed instant or increment
    ``fire_count``. One-shots retain their established terminal failure state.
    """
    if record.kind not in (ScheduleKind.WEEKLY, ScheduleKind.RECURRING):
        store.update(record.id, status=ScheduleStatus.FAILED, error=error, updated_at=now)
        return
    next_fire = next_schedule_occurrence(record.kind.value, record.recurrence, after=now)
    if next_fire is None and recurrence_until_exhausted(record.recurrence):
        store.update(record.id, status=ScheduleStatus.FIRED, active=0, end_reason="date_ended", error=error, updated_at=now)
    elif (
        next_fire is not None and record.expires_at is not None
        and record.indefinite != 1 and next_fire > record.expires_at
    ):
        store.update(record.id, status=ScheduleStatus.EXPIRED, active=0, error=error, updated_at=now)
    elif next_fire is None:
        store.update(record.id, status=ScheduleStatus.FAILED, active=0, error="recurrence_no_candidate", updated_at=now)
    else:
        store.update(record.id, status=ScheduleStatus.ARMED, active=1, fire_at=next_fire, error=error, updated_at=now)


def _timeout_transition(store, record, now: datetime, error: str) -> None:
    """Keep one-shot timeout semantics while repeating schedules continue."""
    if record.kind == ScheduleKind.ONE_SHOT:
        store.update(record.id, status=ScheduleStatus.TIMEOUT, error=error, updated_at=now)
        return
    _failure_transition(store, record, now, error)


def build_schedule_prompt(
    *,
    org_slug: str,
    schedule_id: str,
    agent_name: str,
    role: str,
    team: str,
    normalized_brief: str,
    kind: str,
    fire_at_iso: str,
    recurrence: dict | None,
    timezone: str,
    org_config: OrgConfig,
    now: Callable[[], datetime] | None = None,
    managed_skills_index: str = "",
    protocol_doc_manifest: str = "",
) -> str:
    """Compose the schedule-fire prompt.

    The fire is a TRIGGER, not the work: the session's only job is to call the
    schedule spawn callback which creates ONE root task from the normalized_brief.

    ``current_time`` is injected (fresh per fire) via the shared renderer using
    the org's effective timezone.
    """
    tz, label = resolve_org_timezone_display(org_config)
    current_time = render_current_time_line(tz, label, now)
    skills_block = f"\n{managed_skills_index}\n" if managed_skills_index else ""
    docs_block = f"\n{protocol_doc_manifest}\n" if protocol_doc_manifest else ""

    recurrence_str = ""
    if recurrence:
        recurrence_str = (
            f"\nRecurrence: {recurrence.get('day', '?')} "
            f"{recurrence.get('time', '?:??')} {recurrence.get('tz', 'UTC')}"
        )
    return f"""# Schedule Fire

You are {agent_name} ({role}) on the {team} team in HappyRanch org `{org_slug}`.
This is a SCHEDULE FIRE: a scheduled trigger to dispatch ONE root task from the
stored normalized_brief. It is NOT the work itself. The real work happens in the
root task you spawn — do not perform it here.

current_time: {current_time}{skills_block}{docs_block}
Schedule: {schedule_id}
Kind: {kind}  Fire-at (UTC): {fire_at_iso}{recurrence_str}
Timezone: {timezone}

Your only job: call the schedule spawn callback to create ONE root task from the
normalized_brief below, targeted to yourself on your own team.

happyranch schedules spawn --org {org_slug} --schedule-id {schedule_id} --from-file /tmp/schedule-{schedule_id}.json

Do not call create_task directly and do not dispatch other agents: the spawn
endpoint creates the root task on your own team, targeted to you as executor.

## Normalized Brief (the task that fires)
{normalized_brief}
"""


async def run_schedule(
    *,
    org_state,
    schedule_id: str,
    settings: Settings = global_settings,
    executor_factory: Callable | None = None,
    host_supervisor: HostSessionSupervisor | None = None,
) -> None:
    """Run one schedule fire session.

    Mirrors ``run_wake``: transition ``firing → running`` (already FIRING from
    scheduler claim), compose the fire prompt, invoke the agent's executor in
    its workspace, record token usage under the ``schedule`` scope, and resolve
    the terminal status. The ``schedules spawn`` callback marks the row
    ``completed`` (one-shot → fired, weekly → re-armed); if the session returns
    without calling it, the row is failed (``no_callback``) or timed out.

    THR-207 real-caller wiring: the fire runs through the daemon-wide
    ``HostSessionSupervisor`` (``host_supervisor`` is REQUIRED — schedule is
    the single wired producer). The supervisor owns admission, the atomic
    first-wins terminal protocol, bounded receipt publication, and exactly-once
    lease release; the executor and its per-provider throttle stay inside the
    launch body unchanged."""
    store = org_state.db.schedules
    record = store.get(schedule_id)
    if record is None or record.status != ScheduleStatus.FIRING:
        return

    paths = OrgPaths(root=org_state.root)
    agent_def = load_agent(paths, record.agent_name)
    now = datetime.now(timezone.utc)

    # Audit: schedule_fired (the firing lifecycle started)
    org_state.db.insert_audit_log(
        task_id=schedule_id,
        agent=record.agent_name,
        action="schedule_fired",
        payload={"kind": record.kind.value},
    )

    # Write schedule_fired audit via direct insert (mirrors Phase 2 approach,
    # not editing CRITICAL AuditLogger).

    if agent_def is None:
        _failure_transition(store, record, now, "agent_not_found")
        org_state.db.insert_audit_log(
            task_id=schedule_id,
            agent=record.agent_name,
            action="schedule_failed",
            payload={"reason": "agent_not_found"},
        )
        return

    # Issue #568: forward AgentDef.model to executor.run for schedule invocations.
    model_name: str | None = agent_def.model

    workspace = org_state.root / "workspaces" / record.agent_name
    try:
        org_config = load_org_config(paths)
    except Exception:
        org_config = OrgConfig()
    managed_skills_index = resolve_managed_skills_index(
        paths=paths, agent_name=record.agent_name,
    )

    _prov = _executor_name(paths, record.agent_name)
    if not get_registry().is_registered(_prov):
        _prov = "claude"

    # Issue #536: serialize the complete pre-spawn skill materialization
    # transaction under a process-local workspace lock.
    try:
        skills_root = settings.project_root / "runtime" / "skills"
        expected_specs = materialize_workspace_skills(
            workspace, settings,
            slug=org_state.slug,
            context=SessionContext.SCHEDULE,
            provider=_prov,
            agent_name=record.agent_name,
            team=agent_def.team,
            skills_root=skills_root,
            org_root=org_state.root,
            db=org_state.db,
        )

        # ── Pre-launch integrity validation ─────────────────────
        validate_workspace_skills_integrity(
            workspace, expected_specs,
            settings=settings,
            db=org_state.db,
            agent_name=record.agent_name,
            task_id=schedule_id,
        )
    except Exception as e:
        _failure_transition(store, record, now, f"materialization_failed: {e}")
        org_state.db.insert_audit_log(
            task_id=schedule_id,
            agent=record.agent_name,
            action="schedule_failed",
            payload={"reason": f"materialization_failed: {e}"},
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
            workspace, settings, slug=org_state.slug, context="schedule",
            provider=_prov, agent_name=record.agent_name, team=agent_def.team,
            skills_root=skills_root, org_root=org_state.root, db=org_state.db,
            task_id=schedule_id,
        )

    prompt = build_schedule_prompt(
        org_slug=org_state.slug,
        schedule_id=schedule_id,
        agent_name=record.agent_name,
        role=str(agent_def.role),
        team=agent_def.team,
        normalized_brief=record.normalized_brief,
        kind=record.kind.value,
        fire_at_iso=record.fire_at.isoformat(),
        recurrence=record.recurrence,
        timezone=record.timezone,
        org_config=org_config,
        managed_skills_index=managed_skills_index,
        protocol_doc_manifest=protocol_doc_manifest,
    )

    executor_name = _prov
    executor = (
        executor_factory(executor_name, settings, paths) if executor_factory
        else _build_executor_for_provider(executor_name, settings, paths)
    )

    # ── D7B: CustomAdapterExecutor invocation context ──────────────────
    if hasattr(executor, 'set_invocation_context'):
        executor.set_invocation_context(
            agent=record.agent_name,
            org=org_state.slug,
            invocation_kind="schedule",
            task_id=None,
        )

    loop = asyncio.get_running_loop()

    # ── THR-207 real-caller wiring: schedule fires run through the daemon-
    # wide HostSessionSupervisor (admission + atomic terminal protocol + lease
    # release). The executor + per-provider throttle stay inside the launch
    # body; a terminal winner that refuses launch (daemon shutdown drained at/
    # after grant, cancellation) yields a payload-less outcome and the row is
    # left FIRING for daemon-restart recovery — identical to the pre-wiring
    # behavior when a shutdown cancelled the worker mid-run. ──
    if host_supervisor is None:
        raise RuntimeError(
            "run_schedule requires the daemon-wide HostSessionSupervisor "
            "(schedule fires are the THR-207 wired producer)"
        )

    # Build the backend LaunchSpec via the executor when it exposes the seam
    # (real argv — previously a placeholder that no real backend could
    # execute). The honest passthrough backend ignores the spec entirely
    # (its launch returns a handle with no process and the launch body falls
    # back to the executor's uncontained self-launch).
    launch_spec = None
    spec_builder = getattr(executor, "build_launch_spec", None)
    if spec_builder is not None:
        try:
            launch_spec = spec_builder(
                workspace=workspace,
                prompt=prompt,
                session_id=None,
                model=model_name,
                org_slug=org_state.slug,
                timeout_seconds=settings.session_timeout_seconds,
            )
        except Exception as exc:
            _failure_transition(
                store, record, datetime.now(timezone.utc),
                f"launch_spec_failed: {exc}",
            )
            org_state.db.insert_audit_log(
                task_id=schedule_id,
                agent=record.agent_name,
                action="schedule_failed",
                payload={"reason": f"launch_spec_failed: {exc}"},
            )
            return
    if launch_spec is None:
        from runtime.platform.session_backend import LaunchSpec as _LaunchSpec
        launch_spec = _LaunchSpec(argv=(record.agent_name,))

    def _launch_body(running) -> LaunchResult:
        # Real backend: the fire session is launched into containment — the
        # executor communicates + parses only. Honest passthrough: no
        # containment capability — the executor self-launches exactly as
        # before, with the throttle's internal 429 retry DISABLED so the
        # supervisor is the single 429 retry owner (each attempt fully
        # finishes, publishes its bounded receipt, releases the lease, sleeps
        # without capacity, and reacquires with the original enqueue age and
        # a fresh handle) — mirroring the task producer's passthrough seam.
        contained = running.process is not None
        result = executor.run(
            workspace=workspace,
            prompt=prompt,
            session_id=None,
            timeout_seconds=settings.session_timeout_seconds,
            pre_launch_validator=_pre_launch_validator if not contained else None,
            org_slug=org_state.slug,
            model=model_name,
            running=running if contained else None,
            throttle_backoff_seconds=() if not contained else None,
        )
        return LaunchResult(
            success=result.success,
            duration_seconds=float(getattr(result, "duration_seconds", 0) or 0),
            returncode=getattr(result, "returncode", None),
            error=getattr(result, "error", None),
            rate_limited=bool(getattr(result, "rate_limited", False)),
            timed_out=_is_timeout(result),
            payload=result,
        )

    outcome = await loop.run_in_executor(
        None,
        lambda: host_supervisor.run(
            AdmissionRequest(
                org=org_state.slug,
                invocation_kind="schedule",
                logical_id=schedule_id,
                executor_profile=executor_name,
                enqueued_at=time.monotonic(),
            ),
            launch_spec=launch_spec,
            launch_body=_launch_body,
            pre_launch_validator=_pre_launch_validator,
        ),
    )
    # ``outcome.payload`` is the launch body's LaunchResult; its own
    # ``payload`` is the executor's ExecutorResult. A pre-launch terminal
    # winner (SHUTDOWN/CANCELLED) leaves ``outcome.payload`` None: nothing
    # ran, so no executor result exists and the row is left FIRING for
    # daemon-restart recovery.
    launch = outcome.payload
    if launch is None:
        return
    result = launch.payload
    if getattr(result, "token_usage", None) is not None:
        org_state.db.insert_session_token_usage(
            task_id=None,
            agent=record.agent_name,
            session_id=getattr(result, "agent_session_id", None) or getattr(result, "session_id", None) or schedule_id,
            executor=executor_name,
            token_usage=result.token_usage,
            scope_type="schedule",
            scope_id=schedule_id,
        )

    refreshed = store.get(schedule_id)
    if refreshed is None:
        return
    if refreshed.status in (ScheduleStatus.FIRED, ScheduleStatus.ARMED, ScheduleStatus.EXPIRED):
        # The spawn callback already drove the row to its terminal/re-armed
        # state. Nothing to do.
        return
    if refreshed.status == ScheduleStatus.FAILED:
        # The spawn callback already failed the row.
        return
    if result.success:
        # The session exited 0 but never called `schedules spawn`.
        _failure_transition(store, record, datetime.now(timezone.utc), "no_callback")
        org_state.db.insert_audit_log(
            task_id=schedule_id,
            agent=record.agent_name,
            action="schedule_failed",
            payload={"reason": "no_callback"},
        )
        return
    error = str(getattr(result, "error", "") or "executor_failed")
    if _is_timeout(result):
        _timeout_transition(store, record, datetime.now(timezone.utc), error)
        org_state.db.insert_audit_log(
            task_id=schedule_id,
            agent=record.agent_name,
            action="schedule_timeout",
            payload={"reason": error},
        )
        return
    _failure_transition(store, record, datetime.now(timezone.utc), error)
    org_state.db.insert_audit_log(
        task_id=schedule_id,
        agent=record.agent_name,
        action="schedule_failed",
        payload={"reason": error},
    )
