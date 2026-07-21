"""Persistence for schedule rows (the ``schedules`` table).

Mirrors ``WorkHoursStore``: shares the owning ``Database``'s single
``sqlite3.Connection`` and ``threading.RLock`` so the single-connection
serialization invariant holds. Every method acquires the SAME lock
instance before touching the connection.
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


# Columns the caller may patch via ``update``. The identity columns
# (id, agent_name, created_at) are immutable after insert.
_UPDATABLE = {
    "fire_at",
    "recurrence",
    "timezone",
    "normalized_brief",
    "source_instruction",
    "status",
    "active",
    "expires_at",
    "indefinite",
    "spawned_task_ids",
    "last_fired_at",
    "fire_count",
    "updated_at",
}


class ScheduleStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    # -- id allocation --------------------------------------------------------

    def next_id(self) -> str:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT MAX(CAST(SUBSTR(id, 10) AS INTEGER)) AS m "
                "FROM schedules WHERE id GLOB 'SCHEDULE-[0-9]*'"
            )
            n = (cursor.fetchone()["m"] or 0) + 1
        return f"SCHEDULE-{n:03d}"

    # -- row ↔ model ----------------------------------------------------------

    def _row_to_model(self, row: sqlite3.Row) -> ScheduleRecord:
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
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    # -- CRUD ----------------------------------------------------------------

    def insert(self, record: ScheduleRecord) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO schedules (
                    id, agent_name, team, kind, fire_at, recurrence, timezone,
                    normalized_brief, source_instruction, status, active,
                    expires_at, indefinite, spawned_task_ids, last_fired_at,
                    fire_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id,
                    record.agent_name,
                    record.team,
                    record.kind.value,
                    record.fire_at.isoformat(),
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

    def list(self, *, agent: str | None = None, limit: int = 50) -> list[ScheduleRecord]:
        limit = max(1, min(limit, 500))
        params: list[object] = []
        where = ""
        if agent is not None:
            where = "WHERE agent_name = ?"
            params.append(agent)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM schedules {where} ORDER BY fire_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def update(self, schedule_id: str, **fields: object) -> None:
        bad = set(fields) - _UPDATABLE
        if bad:
            raise ValueError(f"unsupported schedule fields: {sorted(bad)}")
        if not fields:
            return
        assignments = []
        values: list[object] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            if key in ("recurrence", "spawned_task_ids"):
                value = json.dumps(value)
            elif hasattr(value, "value"):
                value = value.value
            elif hasattr(value, "isoformat"):
                value = value.isoformat()
            values.append(value)
        values.append(schedule_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE schedules SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            self._conn.commit()

    # -- armed counting -------------------------------------------------------

    def count_armed(self, *, agent: str | None = None) -> int:
        """Count ``armed`` / ``firing`` rows, scoped per-agent or org-wide."""
        with self._lock:
            if agent is not None:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM schedules "
                    "WHERE agent_name = ? AND status IN ('armed', 'firing')",
                    (agent,),
                ).fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS c FROM schedules "
                    "WHERE status IN ('armed', 'firing')",
                ).fetchone()
        return row["c"] if row else 0
