"""Persistence for working-hours wake rows (the ``work_hours`` table).

Mirrors the dream CRUD that lives on ``Database`` (``insert_dream`` /
``get_dream`` / ``update_dream`` / ...), but is factored into its own module
per the working-hours design. ``WorkHoursStore`` does **not** open its own
connection: it shares the owning ``Database``'s single ``sqlite3.Connection``
and ``threading.RLock`` so the single-connection serialization invariant (see
``Database._synchronized``) holds across both surfaces. Every method acquires
the SAME lock instance before touching the connection.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone

from runtime.models import WorkHourRecord, WorkHourStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# Columns the caller may patch via ``update``; the scheduling-identity columns
# (agent_name, local_date, slot, mode, scheduled_for) and id/created_at are
# immutable after insert.
_UPDATABLE = {
    "started_at",
    "ended_at",
    "status",
    "routine_count",
    "spawned_task_ids",
    "spawned_task_count",
    "summary",
    "transcript_path",
    "session_id",
    "error",
}


class WorkHoursStore:
    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock) -> None:
        self._conn = conn
        self._lock = lock

    def next_id(self) -> str:
        with self._lock:
            cursor = self._conn.execute(
                "SELECT MAX(CAST(SUBSTR(id, 10) AS INTEGER)) AS m "
                "FROM work_hours WHERE id GLOB 'WORKHOUR-[0-9]*'"
            )
            n = (cursor.fetchone()["m"] or 0) + 1
        return f"WORKHOUR-{n:03d}"

    def _row_to_model(self, row) -> WorkHourRecord:
        return WorkHourRecord(
            id=row["id"],
            agent_name=row["agent_name"],
            local_date=row["local_date"],
            slot=row["slot"],
            mode=row["mode"],
            scheduled_for=_parse_dt(row["scheduled_for"]),
            started_at=_parse_dt(row["started_at"]) if row["started_at"] else None,
            ended_at=_parse_dt(row["ended_at"]) if row["ended_at"] else None,
            status=WorkHourStatus(row["status"]),
            routine_count=row["routine_count"],
            spawned_task_ids=json.loads(row["spawned_task_ids"]) if row["spawned_task_ids"] else [],
            spawned_task_count=row["spawned_task_count"],
            summary=row["summary"],
            transcript_path=row["transcript_path"],
            session_id=row["session_id"],
            error=row["error"],
            created_at=_parse_dt(row["created_at"]),
        )

    def insert(self, record: WorkHourRecord) -> None:
        with self._lock:
            self._conn.execute(
                """INSERT INTO work_hours (
                    id, agent_name, local_date, slot, mode, scheduled_for,
                    started_at, ended_at, status, routine_count,
                    spawned_task_ids, spawned_task_count, summary,
                    transcript_path, session_id, error, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    record.id, record.agent_name, record.local_date, record.slot,
                    record.mode.value, record.scheduled_for.isoformat(),
                    record.started_at.isoformat() if record.started_at else None,
                    record.ended_at.isoformat() if record.ended_at else None,
                    record.status.value, record.routine_count,
                    json.dumps(record.spawned_task_ids), record.spawned_task_count,
                    record.summary, record.transcript_path, record.session_id,
                    record.error, record.created_at.isoformat(),
                ),
            )
            self._conn.commit()

    def get(self, work_hour_id: str) -> WorkHourRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM work_hours WHERE id = ?", (work_hour_id,)
            ).fetchone()
        return self._row_to_model(row) if row else None

    def get_for_agent_date_slot(
        self, agent_name: str, local_date: str, slot: str
    ) -> WorkHourRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM work_hours "
                "WHERE agent_name = ? AND local_date = ? AND slot = ?",
                (agent_name, local_date, slot),
            ).fetchone()
        return self._row_to_model(row) if row else None

    def list(self, *, agent: str | None = None, limit: int = 50) -> list[WorkHourRecord]:
        limit = max(1, min(limit, 500))
        params: list[object] = []
        where = ""
        if agent is not None:
            where = "WHERE agent_name = ?"
            params.append(agent)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM work_hours {where} ORDER BY scheduled_for DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return [self._row_to_model(row) for row in rows]

    def update(self, work_hour_id: str, **fields: object) -> None:
        bad = set(fields) - _UPDATABLE
        if bad:
            raise ValueError(f"unsupported work_hour fields: {sorted(bad)}")
        if not fields:
            return
        assignments = []
        values: list[object] = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            if key == "spawned_task_ids":
                value = json.dumps(value)
            elif hasattr(value, "value"):
                value = value.value
            elif hasattr(value, "isoformat"):
                value = value.isoformat()
            values.append(value)
        values.append(work_hour_id)
        with self._lock:
            self._conn.execute(
                f"UPDATE work_hours SET {', '.join(assignments)} WHERE id = ?",
                values,
            )
            self._conn.commit()

    def recover_running(self) -> int:
        """Mark stale ``running`` rows ``failed`` after a daemon restart.

        Mirrors ``recover_running_dreams``: a wake left ``running`` when the
        daemon died can never receive its callback, so it is terminal-failed
        with ``daemon_restart``. The unique ``(agent, local_date, slot)`` row
        still suppresses a duplicate wake for that slot.
        """
        changed = 0
        for record in self.list(limit=500):
            if record.status == WorkHourStatus.RUNNING:
                self.update(
                    record.id,
                    status=WorkHourStatus.FAILED,
                    error="daemon_restart",
                    ended_at=_now(),
                )
                changed += 1
        return changed
