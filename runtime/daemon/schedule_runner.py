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
from datetime import datetime, timezone
from typing import Callable

from runtime.config import Settings, settings as global_settings
from runtime.daemon.dream_runner import _executor_name, _is_timeout
from runtime.daemon.thread_runner import _build_executor_for_provider
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import ScheduleKind, ScheduleStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.executor_registry import get_registry
from runtime.orchestrator.org_config import (
    OrgConfig,
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
)
from runtime.orchestrator.prompt_loader import load_agent
from runtime.orchestrator.schedule_rules import next_weekly_occurrence


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
) -> None:
    """Run one schedule fire session.

    Mirrors ``run_wake``: transition ``firing → running`` (already FIRING from
    scheduler claim), compose the fire prompt, invoke the agent's executor in
    its workspace, record token usage under the ``schedule`` scope, and resolve
    the terminal status. The ``schedules spawn`` callback marks the row
    ``completed`` (one-shot → fired, weekly → re-armed); if the session returns
    without calling it, the row is failed (``no_callback``) or timed out.
    """
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
        store.update(
            schedule_id,
            status=ScheduleStatus.FAILED,
            error="agent_not_found",
            updated_at=now,
        )
        org_state.db.insert_audit_log(
            task_id=schedule_id,
            agent=record.agent_name,
            action="schedule_failed",
            payload={"reason": "agent_not_found"},
        )
        return

    workspace = org_state.root / "workspaces" / record.agent_name
    try:
        org_config = load_org_config(paths)
    except Exception:
        org_config = OrgConfig()
    managed_skills_index = resolve_managed_skills_index(
        paths=paths, agent_name=record.agent_name,
    )

    refresh_session_skills(workspace, settings, slug=org_state.slug)

    _prov = _executor_name(paths, record.agent_name)
    if not get_registry().is_registered(_prov):
        _prov = "claude"

    ensure_system_contracts_materialized(
        workspace, settings, slug=org_state.slug, context="wake",
        provider=_prov,
    )

    try:
        skills_root = settings.project_root / "runtime" / "skills"
        inject_managed_skills(
            workspace, settings,
            slug=org_state.slug,
            agent_name=record.agent_name,
            team=agent_def.team,
            skills_root=skills_root,
            org_root=org_state.root,
            db=org_state.db,
        )
    except Exception as e:
        store.update(
            schedule_id,
            status=ScheduleStatus.FAILED,
            error=f"managed_skills_materialization_failed: {e}",
            updated_at=now,
        )
        org_state.db.insert_audit_log(
            task_id=schedule_id,
            agent=record.agent_name,
            action="schedule_failed",
            payload={"reason": f"managed_skills_materialization_failed: {e}"},
        )
        return

    protocol_doc_manifest = resolve_protocol_doc_manifest(settings=settings)

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

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, lambda: executor.run(
        workspace=workspace,
        prompt=prompt,
        session_id=None,
        timeout_seconds=settings.session_timeout_seconds,
    ))

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
    if refreshed.status in (ScheduleStatus.FIRED, ScheduleStatus.ARMED):
        # The spawn callback already drove the row to its terminal/re-armed
        # state. Nothing to do.
        return
    if refreshed.status == ScheduleStatus.FAILED:
        # The spawn callback already failed the row.
        return
    if result.success:
        # The session exited 0 but never called `schedules spawn`.
        store.update(
            schedule_id,
            status=ScheduleStatus.FAILED,
            error="no_callback",
            updated_at=datetime.now(timezone.utc),
        )
        org_state.db.insert_audit_log(
            task_id=schedule_id,
            agent=record.agent_name,
            action="schedule_failed",
            payload={"reason": "no_callback"},
        )
        return
    error = str(getattr(result, "error", "") or "executor_failed")
    if _is_timeout(result):
        store.update(
            schedule_id,
            status=ScheduleStatus.TIMEOUT,
            error=error,
            updated_at=datetime.now(timezone.utc),
        )
        org_state.db.insert_audit_log(
            task_id=schedule_id,
            agent=record.agent_name,
            action="schedule_timeout",
            payload={"reason": error},
        )
        return
    store.update(
        schedule_id,
        status=ScheduleStatus.FAILED,
        error=error,
        updated_at=datetime.now(timezone.utc),
    )
    org_state.db.insert_audit_log(
        task_id=schedule_id,
        agent=record.agent_name,
        action="schedule_failed",
        payload={"reason": error},
    )
