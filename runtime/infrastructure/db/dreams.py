from __future__ import annotations

from runtime.infrastructure.db._shared import _late_database_now as _now, _parse_dt, _synchronized
from runtime.models import DreamKbCandidate, DreamRecord, DreamStatus


class DreamsMixin:
    @_synchronized
    # --- Dreams ---

    @_synchronized
    def next_dream_id(self) -> str:
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 7) AS INTEGER)) AS m "
            "FROM dreams WHERE id GLOB 'DREAM-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"DREAM-{n:03d}"

    def _dream_row_to_model(self, row) -> DreamRecord:
        return DreamRecord(
            id=row["id"],
            agent_name=row["agent_name"],
            local_date=row["local_date"],
            scheduled_for=_parse_dt(row["scheduled_for"]),
            window_start=_parse_dt(row["window_start"]) if row["window_start"] else None,
            window_end=_parse_dt(row["window_end"]),
            started_at=_parse_dt(row["started_at"]) if row["started_at"] else None,
            ended_at=_parse_dt(row["ended_at"]) if row["ended_at"] else None,
            status=DreamStatus(row["status"]),
            summary=row["summary"],
            transcript_path=row["transcript_path"],
            new_learnings_count=row["new_learnings_count"],
            kb_candidate_count=row["kb_candidate_count"],
            founder_thread_id=row["founder_thread_id"],
            session_id=row["session_id"],
            error=row["error"],
            created_at=_parse_dt(row["created_at"]),
        )

    @_synchronized
    def insert_dream(self, dream: DreamRecord) -> None:
        self._conn.execute(
            """INSERT INTO dreams (
                id, agent_name, local_date, scheduled_for, window_start, window_end,
                started_at, ended_at, status, summary, transcript_path,
                new_learnings_count, kb_candidate_count, founder_thread_id,
                session_id, error, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                dream.id, dream.agent_name, dream.local_date,
                dream.scheduled_for.isoformat(),
                dream.window_start.isoformat() if dream.window_start else None,
                dream.window_end.isoformat(),
                dream.started_at.isoformat() if dream.started_at else None,
                dream.ended_at.isoformat() if dream.ended_at else None,
                dream.status.value, dream.summary, dream.transcript_path,
                dream.new_learnings_count, dream.kb_candidate_count,
                dream.founder_thread_id, dream.session_id, dream.error,
                dream.created_at.isoformat(),
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_dream(self, dream_id: str) -> DreamRecord | None:
        row = self._conn.execute("SELECT * FROM dreams WHERE id = ?", (dream_id,)).fetchone()
        return self._dream_row_to_model(row) if row else None

    @_synchronized
    def get_dream_for_agent_date(self, agent_name: str, local_date: str) -> DreamRecord | None:
        row = self._conn.execute(
            "SELECT * FROM dreams WHERE agent_name = ? AND local_date = ?",
            (agent_name, local_date),
        ).fetchone()
        return self._dream_row_to_model(row) if row else None

    @_synchronized
    def list_dreams(self, *, agent: str | None = None, limit: int = 50) -> list[DreamRecord]:
        limit = max(1, min(limit, 500))
        params: list[object] = []
        where = ""
        if agent is not None:
            where = "WHERE agent_name = ?"
            params.append(agent)
        rows = self._conn.execute(
            f"SELECT * FROM dreams {where} ORDER BY scheduled_for DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [self._dream_row_to_model(row) for row in rows]

    @_synchronized
    def list_dream_ids_by_status(self, statuses: set[str]) -> list[str]:
        """Exhaustive status-filtered dream-id query (no cap, DB-side filter).

        ``list_dreams`` is a presentation list capped at 500 and ordered
        newest-first; using it for a liveness check can hide an old active row
        behind 500 newer terminal rows. This returns every dream id whose
        status is in ``statuses`` so a portability preflight cannot miss an
        active dream. Read-only.
        """
        if not statuses:
            return []
        placeholders = ",".join("?" * len(statuses))
        rows = self._conn.execute(
            f"SELECT id FROM dreams WHERE status IN ({placeholders})",
            tuple(sorted(statuses)),
        ).fetchall()
        return [row["id"] for row in rows]

    @_synchronized
    def get_last_successful_dream(self, agent_name: str) -> DreamRecord | None:
        row = self._conn.execute(
            "SELECT * FROM dreams WHERE agent_name = ? AND status = 'completed' "
            "ORDER BY ended_at DESC LIMIT 1",
            (agent_name,),
        ).fetchone()
        return self._dream_row_to_model(row) if row else None

    @_synchronized
    def update_dream(self, dream_id: str, **fields: object) -> None:
        allowed = {
            "started_at", "ended_at", "status", "summary", "transcript_path",
            "new_learnings_count", "kb_candidate_count", "founder_thread_id",
            "session_id", "error",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unsupported dream fields: {sorted(bad)}")
        if not fields:
            return
        values = []
        assignments = []
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            if hasattr(value, "value"):
                value = value.value
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            values.append(value)
        values.append(dream_id)
        self._conn.execute(
            f"UPDATE dreams SET {', '.join(assignments)} WHERE id = ?",
            values,
        )
        self._conn.commit()

    @_synchronized
    def update_dream_status_if(
        self,
        dream_id: str,
        expected_status: DreamStatus,
        new_status: DreamStatus,
        **fields: object,
    ) -> bool:
        """Atomically transition a dream only if it is still ``expected_status``.

        Returns ``True`` if the row was updated, ``False`` if the expected
        status no longer matched (e.g. a concurrent termination set it to
        SKIPPED). Extra fields are persisted only on a successful transition.
        """
        allowed = {
            "started_at", "ended_at", "summary", "transcript_path",
            "new_learnings_count", "kb_candidate_count", "founder_thread_id",
            "session_id", "error",
        }
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"unsupported dream fields: {sorted(bad)}")
        assignments = ["status = ?"]
        values: list[object] = [new_status.value]
        for key, value in fields.items():
            assignments.append(f"{key} = ?")
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            values.append(value)
        values.append(dream_id)
        values.append(expected_status.value)
        cursor = self._conn.execute(
            f"UPDATE dreams SET {', '.join(assignments)} WHERE id = ? AND status = ?",
            values,
        )
        self._conn.commit()
        return cursor.rowcount == 1

    def _dream_candidate_row_to_model(self, row) -> DreamKbCandidate:
        return DreamKbCandidate(
            id=row["id"],
            dream_id=row["dream_id"],
            agent_name=row["agent_name"],
            slug=row["slug"],
            title=row["title"],
            topic=row["topic"],
            rationale=row["rationale"],
            body_markdown=row["body_markdown"],
            status=row["status"],
            promoted_kb_slug=row["promoted_kb_slug"],
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )

    @_synchronized
    def insert_dream_kb_candidate(self, candidate: DreamKbCandidate) -> None:
        self._conn.execute(
            """INSERT INTO dream_kb_candidates (
                dream_id, agent_name, slug, title, topic, rationale,
                body_markdown, status, promoted_kb_slug, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate.dream_id, candidate.agent_name, candidate.slug,
                candidate.title, candidate.topic, candidate.rationale,
                candidate.body_markdown, candidate.status,
                candidate.promoted_kb_slug, candidate.created_at.isoformat(),
                candidate.updated_at.isoformat(),
            ),
        )
        self._conn.commit()

    @_synchronized
    def list_dream_kb_candidates(
        self,
        *,
        dream_id: str | None = None,
        agent: str | None = None,
        candidate_id: int | None = None,
    ) -> list[DreamKbCandidate]:
        clauses = []
        params: list[object] = []
        if dream_id is not None:
            clauses.append("dream_id = ?")
            params.append(dream_id)
        if agent is not None:
            clauses.append("agent_name = ?")
            params.append(agent)
        if candidate_id is not None:
            clauses.append("id = ?")
            params.append(candidate_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM dream_kb_candidates {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [self._dream_candidate_row_to_model(row) for row in rows]

    @_synchronized
    def update_dream_kb_candidate(
        self,
        candidate_id: int,
        *,
        status: str,
        promoted_kb_slug: str | None = None,
    ) -> None:
        allowed = {"pending", "promoted", "rejected", "superseded"}
        if status not in allowed:
            raise ValueError(f"invalid status: {status!r}, expected one of {sorted(allowed)}")
        now = _now().isoformat()
        params: list[object] = [status, now]
        slug_assign = ""
        if promoted_kb_slug is not None:
            slug_assign = ", promoted_kb_slug = ?"
            params.append(promoted_kb_slug)
        params.append(candidate_id)
        cursor = self._conn.execute(
            f"UPDATE dream_kb_candidates SET status = ?, updated_at = ?{slug_assign} WHERE id = ?",
            params,
        )
        if cursor.rowcount == 0:
            raise ValueError(f"dream_kb_candidate {candidate_id} not found")
        self._conn.commit()
