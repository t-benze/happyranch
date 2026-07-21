"""Persistence for scheduled work rows (the ``schedules`` table).

Mirrors ``WorkHoursStore``: shares the owning ``Database``'s single
``sqlite3.Connection`` and ``threading.RLock``; every method acquires the
same lock before touching the connection.

THR-105 Phase 1 — inert additive store; no scheduler/runner/wiring yet.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from runtime.models import ScheduleKind, ScheduleRecord, ScheduleStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# Columns the caller may patch via ``update`` (mutable lifecycle fields).
# Identity columns (id, agent_name, team, kind) and source_instruction /
# created_at are immutable after insert.
_UPDATABLE = {
    "fire_at",
    "recurrence",
    "timezone",
    "status",
    "active",
    "expires_at",
    "indefinite",
    "spawned_task_ids",
    "last_fired_at",
    "fire_count",
    "session_id",
    "error",
    "transcript_path",
    "updated_at",
}


class ScheduleStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    # ------------------------------------------------------------------ id alloc

    def next_id(self) -> str:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT MAX(CAST(SUBSTR(id, 10) AS INTEGER)) AS m "
                "FROM schedules WHERE id GLOB 'SCHEDULE-[0-9]*'"
            )
            n = (cursor.fetchone()["m"] or 0) + 1
        return f"SCHEDULE-{n:03d}"

    # ------------------------------------------------------------------- helpers

    def _row_to_model(self, row) -> ScheduleRecord:
        return ScheduleRecord(
            id=row["id"],
            agent_name=row["agent_name"],
            team=row["team"],
            kind=ScheduleKind(row["kind"]),
            fire_at=_parse_dt(row["fire_at"]),
            recurrence=json.loads(row["recurrence"]) if row["recurrence"] else None,
            timezone=row["timezone"],
            normalized_brief=row["normalized_brief"],
            source_instruction=row["source_instruction"],
            status=ScheduleStatus(row["status"]),
            active=row["active"],
            expires_at=_parse_dt(row["expires_at"]) if row["expires_at"] else None,
            indefinite=row["indefinite"],
            spawned_task_ids=json.loads(row["spawned_task_ids"]) if row["spawned_task_ids"] else [],
            last_fired_at=_parse_dt(row["last_fired_at"]) if row["last_fired_at"] else None,
            fire_count=row["fire_count"],
            session_id=row["session_id"],
            error=row["error"],
            transcript_path=row["transcript_path"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    # ------------------------------------------------------------------ CRUD

    @staticmethod
    def _utc(dt: datetime) -> datetime:
        """Normalize a datetime to UTC, preserving the absolute instant."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def insert(self, record: ScheduleRecord) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO schedules (
                    id, agent_name, team, kind, fire_at, recurrence, timezone,
                    normalized_brief, source_instruction, status, active,
                    expires_at, indefinite, spawned_task_ids, last_fired_at,
                    fire_count, session_id, error, transcript_path,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.agent_name,
                    record.team,
                    record.kind.value,
                    self._utc(record.fire_at).isoformat(),
                    json.dumps(record.recurrence) if record.recurrence else None,
                    record.timezone,
                    record.normalized_brief,
                    record.source_instruction,
                    record.status.value,
                    record.active,
                    record.expires_at.isoformat() if record.expires_at else None,
                    record.indefinite,
                    json.dumps(record.spawned_task_ids),
                    record.last_fired_at.isoformat() if record.last_fired_at else None,
                    record.fire_count,
                    record.session_id,
                    record.error,
                    record.transcript_path,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            self._conn.commit()

    def get(self, schedule_id: str) -> ScheduleRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM schedules WHERE id = ?", (schedule_id,)
            ).fetchone()
        return self._row_to_model(row) if row else None

    def list(
        self,
        *,
        agent: str | None = None,
        status: ScheduleStatus | None = None,
        limit: int = 50,
    ) -> list[ScheduleRecord]:
        limit = max(1, min(limit, 500))
        clauses: list[str] = []
        params: list[object] = []
        if agent is not None:
            clauses.append("agent_name = ?")
            params.append(agent)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM schedules {where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row_to_model(row) for row in rows]

    # ----------------------------------------------------- due / active helpers

    def list_due(self, now: datetime) -> list[ScheduleRecord]:
        """Return ``armed`` rows whose ``fire_at <= now``, ordered by fire_at ASC.

        Both ``fire_at`` (persisted) and ``now`` are normalized to UTC so
        timezone-offset TEXT values compare correctly.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM schedules "
                "WHERE status = ? AND fire_at <= ? "
                "ORDER BY fire_at ASC",
                (ScheduleStatus.ARMED.value, self._utc(now).isoformat()),
            ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def active_count_for_agent(self, agent_name: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM schedules WHERE status = ? AND agent_name = ?",
                (ScheduleStatus.ARMED.value, agent_name),
            ).fetchone()
        return row["n"]

    def active_count_org(self) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM schedules WHERE status = ?",
                (ScheduleStatus.ARMED.value,),
            ).fetchone()
        return row["n"]

    # ------------------------------------------------------------------- update

    def update(self, schedule_id: str, **fields: object) -> None:
        bad = set(fields) - _UPDATABLE
        if bad:
            raise ValueError(f"unsupported schedule fields: {sorted(bad)}")
        if not fields:
            return
        # Always bump updated_at unless the caller explicitly passes it.
        fields.setdefault("updated_at", _now())
        assignments = []
        values: list[object] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            if key in ("recurrence", "spawned_task_ids"):
                value = json.dumps(value)
            elif hasattr(value, "value"):
                value = value.value
            elif hasattr(value, "isoformat"):
                if key in ("fire_at", "expires_at", "last_fired_at") and isinstance(value, datetime):
                    value = self._utc(value)
                value = value.isoformat()
            values.append(value)
        values.append(schedule_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE schedules SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            self._conn.commit()

    def recover_firing(self) -> int:
        """Mark stale ``firing`` rows ``failed`` after a daemon restart.

        Mirrors ``WorkHoursStore.recover_running``: a schedule left ``firing``
        when the daemon died can never receive its callback.
        """
        changed = 0
        for record in self.list(limit=500):
            if record.status == ScheduleStatus.FIRING:
                self.update(
                    record.id,
                    status=ScheduleStatus.FAILED,
                    error="daemon_restart",
                    updated_at=_now(),
                )
                changed += 1
        return changed
