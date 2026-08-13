"""THR-105 Phase 2: Schedule lifecycle service — validation, state transitions,
and audit for create / list / get / pause / cancel / edit.

No I/O beyond the ``Database`` (which owns ``ScheduleStore`` and
``insert_audit_log``).  No routes, no scheduler loop, no wake queue.
This is the non-route foundation.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from runtime.infrastructure.database import Database
from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus
from runtime.orchestrator.schedule_rules import (
    default_expires_at,
    next_recurring_occurrence,
    next_weekly_occurrence,
    validate_caps,
    validate_one_shot_horizon,
    validate_recurring_rule,
    validate_weekly_recurrence,
)


class ScheduleServiceError(Exception):
    """Actionable error from the schedule service (validation,
    state-transition rejection, missing resource)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Fields the service will allow callers to edit in this phase.
# Timing/recurrence fields only.  Content fields (normalized_brief,
# source_instruction) are provenance fields set at creation and immutable
# afterward.  Lifecycle fields, expiry/indefinite are NOT editable
# through this service.
_ALLOWED_EDIT_FIELDS = frozenset({
    "fire_at", "recurrence", "timezone",
})


class ScheduleService:
    """Owns Schedule lifecycle validation and audit.

    Every method that mutates state writes an audit row with
    ``task_id=<SCHEDULE-NNN>`` using the established scope-prefix convention.
    Audit rows are written directly via ``Database.insert_audit_log``.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

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
        indefinite: bool = False,
    ) -> ScheduleRecord:
        """Validate the request against the v1 envelope, persist, and audit.

        This service has no per-agent capability gate. Caller identity and team
        binding are enforced by the callback route; this service owns the
        schedule envelope, cap, persistence, and audit rules.
        """

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
            if recurrence is not None:
                raise ScheduleServiceError(
                    "one-shot schedules must not have recurrence; "
                    "recurrence must be null"
                )
            err = validate_one_shot_horizon(fire_at, _now())
            if err:
                raise ScheduleServiceError(err)
        elif kind == ScheduleKind.WEEKLY:
            err = validate_weekly_recurrence(recurrence)
            if err:
                raise ScheduleServiceError(err)
            # Normalize fire_at: must match the next weekly occurrence.
            now = _now()
            expected = next_weekly_occurrence(
                recurrence["day"], recurrence["time"], recurrence["tz"],
                after=now,
            )
            if expected is None:
                raise ScheduleServiceError(
                    "could not compute next occurrence for weekly schedule"
                )
            if fire_at != expected:
                raise ScheduleServiceError(
                    f"fire_at must match the next weekly occurrence "
                    f"({recurrence['day']} {recurrence['time']} {recurrence['tz']}); "
                    f"expected {expected.isoformat()}, got {fire_at.isoformat()}"
                )
            # Top-level timezone must match recurrence.tz for weekly schedules.
            # Derive from the authoritative recurrence to keep the stored record
            # founder-reviewable with one clear timezone.
            if timezone and timezone != recurrence["tz"]:
                raise ScheduleServiceError(
                    f"timezone {timezone!r} must match recurrence tz "
                    f"{recurrence['tz']!r} for weekly schedules"
                )
            timezone = recurrence["tz"]
        elif kind == ScheduleKind.RECURRING:
            err = validate_recurring_rule(recurrence, context="create", now=_now())
            if err:
                raise ScheduleServiceError(err.code)
            now = _now()
            candidate_rule = dict(recurrence)
            candidate_rule["anchor_date"] = now.astimezone(ZoneInfo(recurrence["tz"])).date().isoformat()
            expected = next_recurring_occurrence(candidate_rule, after=now)
            if expected is None:
                raise ScheduleServiceError("could not compute next occurrence for recurring schedule")
            recurrence = dict(recurrence)
            recurrence["anchor_date"] = expected.astimezone(ZoneInfo(recurrence["tz"])).date().isoformat()
            if fire_at != expected:
                raise ScheduleServiceError(
                    f"fire_at must match the next recurring occurrence; "
                    f"expected {expected.isoformat()}, got {fire_at.isoformat()}"
                )
            if timezone and timezone != recurrence["tz"]:
                raise ScheduleServiceError(
                    f"timezone {timezone!r} must match recurrence tz "
                    f"{recurrence['tz']!r} for recurring schedules"
                )
            timezone = recurrence["tz"]
        else:
            raise ScheduleServiceError(
                f"unsupported schedule kind: {kind.value}. "
                "supports one_shot, weekly, and recurring schedules."
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
        payload: dict = {
            "kind": kind.value,
            "normalized_brief": record.normalized_brief,
        }
        if recurrence is not None:
            payload["recurrence"] = recurrence
        self._db.insert_audit_log(
            task_id=schedule_id,
            agent=agent_name,
            action="schedule_created",
            payload=payload,
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
        self._db.insert_audit_log(
            task_id=schedule_id,
            agent=agent_name,
            action="schedule_paused",
        )
        return self._db.schedules.get(schedule_id)

    # ── cancel ────────────────────────────────────────────────────────

    def cancel(self, schedule_id: str, agent_name: str) -> ScheduleRecord:
        """Terminate a schedule permanently.

        Accepts only ``armed`` and ``paused``; rejects ``firing`` and all
        terminal statuses (fired, cancelled, expired, failed, timeout).
        """
        record = self._db.schedules.get(schedule_id)
        if record is None:
            raise ScheduleServiceError(f"schedule {schedule_id} not found")

        if record.status not in (ScheduleStatus.ARMED, ScheduleStatus.PAUSED):
            raise ScheduleServiceError(
                f"cannot cancel {schedule_id}: status {record.status.value} "
                f"is not armed or paused"
            )

        self._db.schedules.update(
            schedule_id,
            status=ScheduleStatus.CANCELLED,
            active=0,
        )
        self._db.insert_audit_log(
            task_id=schedule_id,
            agent=agent_name,
            action="schedule_cancelled",
        )
        return self._db.schedules.get(schedule_id)

    # ── edit ──────────────────────────────────────────────────────────

    def edit(
        self,
        schedule_id: str,
        agent_name: str,
        **fields: Any,
    ) -> ScheduleRecord:
        """Edit mutable fields of a schedule, re-validating before applying.

        Accepts only ``armed`` and ``paused`` statuses; ``firing`` and
        terminal state edits are rejected.  Editable fields: fire_at,
        recurrence, timezone.
        normalized_brief and source_instruction are provenance fields
        set at creation and immutable afterward.

        After applying the changes the service re-runs the relevant
        validators on the *new* values.  If validation fails the record
        is left unchanged.
        """
        record = self._db.schedules.get(schedule_id)
        if record is None:
            raise ScheduleServiceError(f"schedule {schedule_id} not found")

        if record.status not in (ScheduleStatus.ARMED, ScheduleStatus.PAUSED):
            raise ScheduleServiceError(
                f"cannot edit {schedule_id}: status {record.status.value} "
                f"is not armed or paused"
            )

        if not fields:
            return record

        # Reject fields outside the allowlist
        bad = set(fields) - _ALLOWED_EDIT_FIELDS
        if bad:
            raise ScheduleServiceError(
                f"cannot edit these fields on a schedule: {sorted(bad)}"
            )

        # Validate mutable fields
        recurrence = fields.get("recurrence", record.recurrence)
        fire_at = fields.get("fire_at", record.fire_at)

        if record.kind == ScheduleKind.ONE_SHOT:
            if "recurrence" in fields and fields["recurrence"] is not None:
                raise ScheduleServiceError(
                    "one-shot schedules must not have recurrence; "
                    "recurrence must be null"
                )
            err = validate_one_shot_horizon(fire_at, _now())
            if err:
                raise ScheduleServiceError(err)
        elif record.kind == ScheduleKind.WEEKLY:
            # Build merged candidate from fields + stored record,
            # then validate atomically before applying any change.
            merged_recurrence = fields.get("recurrence", record.recurrence)
            merged_timezone = fields.get("timezone", record.timezone)
            merged_fire_at = fields.get("fire_at", record.fire_at)

            err = validate_weekly_recurrence(merged_recurrence)
            if err:
                raise ScheduleServiceError(err)

            # Top-level timezone must equal recurrence.tz.
            if merged_timezone != merged_recurrence["tz"]:
                raise ScheduleServiceError(
                    f"timezone {merged_timezone!r} must match recurrence tz "
                    f"{merged_recurrence['tz']!r} for weekly schedules"
                )
            # fire_at must be the next occurrence of the merged recurrence.
            now = _now()
            expected = next_weekly_occurrence(
                merged_recurrence["day"], merged_recurrence["time"],
                merged_recurrence["tz"], after=now,
            )
            if expected is None:
                raise ScheduleServiceError(
                    "could not compute next occurrence for weekly schedule"
                )
            if merged_fire_at != expected:
                raise ScheduleServiceError(
                    f"fire_at must match the next weekly occurrence "
                    f"({merged_recurrence['day']} {merged_recurrence['time']} "
                    f"{merged_recurrence['tz']}); "
                    f"expected {expected.isoformat()}, got {merged_fire_at.isoformat()}"
                )
        elif record.kind == ScheduleKind.RECURRING:
            merged_recurrence = dict(record.recurrence or {})
            if "recurrence" in fields:
                merged_recurrence.update(fields["recurrence"])
            merged_timezone = fields.get("timezone", record.timezone)
            merged_fire_at = fields.get("fire_at", record.fire_at)
            shape_fields = {"freq", "interval", "byday", "bymonthday", "ordinal"}
            old_recurrence = record.recurrence or {}
            shape_changed = any(
                merged_recurrence.get(field) != old_recurrence.get(field)
                for field in shape_fields
            )
            if shape_changed:
                # First compute against the existing cadence anchor, then make
                # the newly selected next local date the new immutable anchor.
                provisional = next_recurring_occurrence(merged_recurrence, after=_now())
                if provisional is not None:
                    merged_recurrence["anchor_date"] = provisional.astimezone(
                        ZoneInfo(merged_recurrence["tz"])
                    ).date().isoformat()
            err = validate_recurring_rule(merged_recurrence, now=_now())
            if err:
                raise ScheduleServiceError(err.code)
            if merged_timezone != merged_recurrence["tz"]:
                raise ScheduleServiceError(
                    f"timezone {merged_timezone!r} must match recurrence tz "
                    f"{merged_recurrence['tz']!r} for recurring schedules"
                )
            expected = next_recurring_occurrence(merged_recurrence, after=_now())
            if expected is None:
                raise ScheduleServiceError("could not compute next occurrence for recurring schedule")
            if merged_fire_at != expected:
                raise ScheduleServiceError(
                    f"fire_at must match the next recurring occurrence; "
                    f"expected {expected.isoformat()}, got {merged_fire_at.isoformat()}"
                )
            fields["recurrence"] = merged_recurrence

        else:
            raise ScheduleServiceError(
                f"unsupported schedule kind: {record.kind.value}. "
                "supports one_shot, weekly, and recurring schedules."
            )

        self._db.schedules.update(schedule_id, **fields)
        self._db.insert_audit_log(
            task_id=schedule_id,
            agent=agent_name,
            action="schedule_edited",
            payload={
                "fields": sorted(fields.keys()),
                **({"before": {"recurrence": record.recurrence}, "after": {"recurrence": fields["recurrence"]}}
                   if record.kind == ScheduleKind.RECURRING else {}),
            },
        )
        return self._db.schedules.get(schedule_id)
