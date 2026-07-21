"""THR-105 Phase 2: Schedule lifecycle service — validation, state transitions,
and audit for create / list / get / pause / cancel / edit.

No I/O beyond the ``Database`` (which owns ``ScheduleStore`` and
``insert_audit_log``) and the ``AuditLogger``.  No routes, no scheduler
loop, no wake queue.  This is the non-route foundation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus
from runtime.orchestrator.schedule_rules import (
    default_expires_at,
    validate_caps,
    validate_one_shot_horizon,
    validate_weekly_recurrence,
)


class ScheduleServiceError(Exception):
    """Actionable error from the schedule service (validation,
    state-transition rejection, missing resource)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Terminal statuses where no lifecycle transition is allowed (except
# recovery paths owned by the future scheduler/runner, not the service).
_TERMINAL = frozenset({ScheduleStatus.FIRED, ScheduleStatus.CANCELLED,
                        ScheduleStatus.EXPIRED, ScheduleStatus.FAILED,
                        ScheduleStatus.TIMEOUT})


class ScheduleService:
    """Owns Schedule lifecycle validation and audit.

    Every method that mutates state writes an audit row with
    ``task_id=<SCHEDULE-NNN>`` using the established scope-prefix convention.
    """

    def __init__(self, db: Database, audit: AuditLogger) -> None:
        self._db = db
        self._audit = audit

    # ── create ────────────────────────────────────────────────────────

    def create(
        self,
        *,
        agent_name: str,
        team: str,
        kind: ScheduleKind,
        fire_at: datetime,
        recurrence: dict | None,
        timezone: str,
        normalized_brief: str,
        source_instruction: str,
        scheduling_enabled: bool = True,
        indefinite: bool = False,
    ) -> ScheduleRecord:
        """Validate the request against the v1 envelope, persist, and audit.

        The ``scheduling_enabled`` gate checks the per-agent capability flag
        resolved by the caller (future route layer); the service itself does
        *not* read org config.
        """
        if not scheduling_enabled:
            raise ScheduleServiceError(
                "scheduling is not enabled for this agent"
            )

        # --- mandatory fields ---
        if not (source_instruction and source_instruction.strip()):
            raise ScheduleServiceError(
                "source_instruction is required and must not be blank"
            )
        if not (normalized_brief and normalized_brief.strip()):
            raise ScheduleServiceError(
                "normalized_brief is required and must not be blank"
            )

        # --- kind-specific validation ---
        if kind == ScheduleKind.ONE_SHOT:
            err = validate_one_shot_horizon(fire_at, _now())
            if err:
                raise ScheduleServiceError(err)
        elif kind == ScheduleKind.WEEKLY:
            err = validate_weekly_recurrence(recurrence)
            if err:
                raise ScheduleServiceError(err)
        else:
            raise ScheduleServiceError(
                f"unsupported schedule kind: {kind.value}. "
                "v1 supports one_shot and weekly only."
            )

        # --- caps ---
        agent_count = self._db.schedules.active_count_for_agent(agent_name)
        org_count = self._db.schedules.active_count_org()
        err = validate_caps(agent_count, org_count)
        if err:
            raise ScheduleServiceError(err)

        # --- expiry default ---
        now = _now()
        expires_at = default_expires_at(now, kind, indefinite=indefinite)

        # --- insert ---
        schedule_id = self._db.schedules.next_id()
        record = ScheduleRecord(
            id=schedule_id,
            agent_name=agent_name,
            team=team,
            kind=kind,
            fire_at=fire_at,
            recurrence=recurrence,
            timezone=timezone or "UTC",
            normalized_brief=normalized_brief.strip(),
            source_instruction=source_instruction.strip(),
            status=ScheduleStatus.ARMED,
            active=1,
            expires_at=expires_at,
            indefinite=1 if indefinite else 0,
            created_at=now,
            updated_at=now,
        )
        self._db.schedules.insert(record)

        # --- audit ---
        self._audit.log_schedule_created(
            schedule_id, agent_name,
            kind=kind,
            normalized_brief=record.normalized_brief,
            recurrence=recurrence,
        )

        return self._db.schedules.get(schedule_id)

    # ── read ──────────────────────────────────────────────────────────

    def get(self, schedule_id: str) -> ScheduleRecord | None:
        return self._db.schedules.get(schedule_id)

    def list(
        self,
        *,
        agent: str | None = None,
        status: ScheduleStatus | None = None,
        limit: int = 50,
    ) -> list[ScheduleRecord]:
        return self._db.schedules.list(agent=agent, status=status, limit=limit)

    # ── pause ─────────────────────────────────────────────────────────

    def pause(self, schedule_id: str, agent_name: str) -> ScheduleRecord:
        """Suspend a schedule without deleting it.

        Only ``armed`` schedules may be paused.  ``paused`` → no-op
        (idempotent re-pause is safe).
        """
        record = self._db.schedules.get(schedule_id)
        if record is None:
            raise ScheduleServiceError(f"schedule {schedule_id} not found")

        if record.status == ScheduleStatus.PAUSED:
            return record  # idempotent

        if record.status != ScheduleStatus.ARMED:
            raise ScheduleServiceError(
                f"can only pause armed schedules; {schedule_id} is {record.status.value}"
            )

        self._db.schedules.update(
            schedule_id,
            status=ScheduleStatus.PAUSED,
            active=0,
        )
        self._audit.log_schedule_paused(schedule_id, agent_name)
        return self._db.schedules.get(schedule_id)

    # ── cancel ────────────────────────────────────────────────────────

    def cancel(self, schedule_id: str, agent_name: str) -> ScheduleRecord:
        """Terminate a schedule permanently.

        Accepts ``armed`` and ``paused``; rejects terminal statuses
        (fired, cancelled, expired, failed, timeout).
        """
        record = self._db.schedules.get(schedule_id)
        if record is None:
            raise ScheduleServiceError(f"schedule {schedule_id} not found")

        if record.status in _TERMINAL:
            raise ScheduleServiceError(
                f"cannot cancel {schedule_id}: status {record.status.value} is terminal"
            )

        self._db.schedules.update(
            schedule_id,
            status=ScheduleStatus.CANCELLED,
            active=0,
        )
        self._audit.log_schedule_cancelled(schedule_id, agent_name)
        return self._db.schedules.get(schedule_id)

    # ── edit ──────────────────────────────────────────────────────────

    def edit(
        self,
        schedule_id: str,
        agent_name: str,
        **fields: Any,
    ) -> ScheduleRecord:
        """Edit mutable fields of a schedule, re-validating before re-arming.

        Accepts only ``armed`` and ``paused`` statuses; terminal state edits
        are rejected.  After applying the changes the service re-runs the
        relevant validators on the *new* values.  If validation fails the
        record is left unchanged.

        The method does **not** itself change status back to ``armed`` from
        ``paused`` — the caller can pass ``status=ScheduleStatus.ARMED``
        explicitly if they want to re-arm a paused schedule during edit.
        """
        record = self._db.schedules.get(schedule_id)
        if record is None:
            raise ScheduleServiceError(f"schedule {schedule_id} not found")

        if record.status in _TERMINAL:
            raise ScheduleServiceError(
                f"cannot edit {schedule_id}: status {record.status.value} is terminal"
            )

        if not fields:
            return record

        # Validate mutable fields
        kind = fields.get("kind", record.kind)
        recurrence = fields.get("recurrence", record.recurrence)
        fire_at = fields.get("fire_at", record.fire_at)

        if kind == ScheduleKind.ONE_SHOT:
            err = validate_one_shot_horizon(fire_at, _now())
            if err:
                raise ScheduleServiceError(err)
        elif kind == ScheduleKind.WEEKLY:
            err = validate_weekly_recurrence(recurrence)
            if err:
                raise ScheduleServiceError(err)
        else:
            raise ScheduleServiceError(
                f"unsupported schedule kind: {kind.value if hasattr(kind, 'value') else kind}. "
                "v1 supports one_shot and weekly only."
            )

        # Strip string fields
        for key in ("normalized_brief", "source_instruction"):
            if key in fields and isinstance(fields[key], str):
                fields[key] = fields[key].strip()

        self._db.schedules.update(schedule_id, **fields)
        self._audit.log_schedule_edited(
            schedule_id, agent_name,
            fields=list(fields.keys()),
        )
        return self._db.schedules.get(schedule_id)
