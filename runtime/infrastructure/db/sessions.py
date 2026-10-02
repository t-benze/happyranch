from __future__ import annotations

import json
from datetime import datetime, timezone

from runtime.infrastructure.db._shared import _synchronized
from runtime.models import TaskStatus, TokenUsage


class SessionsMixin:
    # --- Session Token Usage ---

    @_synchronized
    def insert_session_token_usage(
        self,
        task_id: str | None,
        agent: str,
        session_id: str,
        executor: str,
        token_usage: TokenUsage,
        scope_type: str = "task",
        scope_id: str | None = None,
        thread_id: str | None = None,
        invocation_purpose: str | None = None,
    ) -> None:
        """Insert one token usage row. INSERT OR IGNORE: first write wins."""
        if scope_id is None and scope_type == "task":
            scope_id = task_id
        self._conn.execute(
            """INSERT OR IGNORE INTO session_token_usage
               (task_id, agent, session_id, executor, model,
                input_tokens, output_tokens, cache_read_tokens,
                cache_creation_tokens, reasoning_tokens,
                usage_raw_json, scope_type, scope_id, thread_id,
                invocation_purpose, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, agent, session_id, executor, token_usage.model,
                token_usage.input_tokens, token_usage.output_tokens,
                token_usage.cache_read_tokens, token_usage.cache_creation_tokens,
                token_usage.reasoning_tokens, token_usage.usage_raw_json,
                scope_type, scope_id, thread_id, invocation_purpose,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    @_synchronized
    def query_usage_lifecycle_snapshot(
        self, *, start_utc: str, end_utc: str,
    ) -> dict[str, list[dict]]:
        """Return the bounded, read-only lifecycle facts used by Usage v1.

        Membership is selected from lifecycle tables first.  Usage rows are
        optional candidates for those lifecycle rows; callers resolve the
        documented task/thread/dream keys and reject ambiguous candidates.
        """
        audit_rows = self._conn.execute(
            """SELECT id, task_id, agent, action, payload, timestamp
               FROM audit_log
               WHERE (action IN ('session_start', 'session_end')
                      AND timestamp < ?)
                  OR (action = 'dream_started'
                      AND timestamp >= ? AND timestamp < ?)
               ORDER BY id""",
            (end_utc, start_utc, end_utc),
        ).fetchall()
        thread_rows = self._conn.execute(
            """SELECT id, thread_id, agent_name, invocation_token, purpose,
                      status, started_at, consumed_at, session_id, executor,
                      model, decline_reason, reply_message_seq
               FROM thread_invocations
               WHERE (started_at >= ? AND started_at < ?)
                  OR (purpose = 'reply'
                      AND consumed_at >= ? AND consumed_at < ?)
               ORDER BY id""",
            (start_utc, end_utc, start_utc, end_utc),
        ).fetchall()
        usage_rows = self._conn.execute(
            """SELECT * FROM session_token_usage
               WHERE created_at >= ? AND created_at < ?
                 AND COALESCE(scope_type, 'task') IN ('task', 'thread', 'dream')
               ORDER BY id""",
            (start_utc, end_utc),
        ).fetchall()
        result_rows = self._conn.execute(
            """SELECT r.id, r.task_id, r.agent, r.session_id, r.status,
                      r.created_at, t.status AS task_status
               FROM task_results AS r
               JOIN tasks AS t ON t.id = r.task_id
               WHERE r.created_at >= ? AND r.created_at < ?
               ORDER BY r.id""",
            (start_utc, end_utc),
        ).fetchall()
        recovery_rows = self._conn.execute(
            """SELECT task_id, agent, recovery_session_id
               FROM task_completion_recoveries"""
        ).fetchall()
        return {
            "audit": [dict(row) for row in audit_rows],
            "threads": [dict(row) for row in thread_rows],
            "usage": [dict(row) for row in usage_rows],
            "results": [dict(row) for row in result_rows],
            "recoveries": [dict(row) for row in recovery_rows],
        }

    def _session_token_usage_filters(
        self,
        *,
        since: str | None = None,
        task_id: str | None = None,
        agent: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        thread_id: str | None = None,
        purpose: str | None = None,
    ) -> tuple[list[str], list[object]]:
        where: list[str] = []
        params: list[object] = []
        if since is not None:
            where.append("created_at >= ?")
            params.append(since)
        if task_id is not None:
            where.append("task_id = ?")
            params.append(task_id)
        if agent is not None:
            where.append("agent = ?")
            params.append(agent)
        if scope_type is not None:
            where.append("COALESCE(scope_type, 'task') = ?")
            params.append(scope_type)
        if scope_id is not None:
            where.append("COALESCE(scope_id, task_id) = ?")
            params.append(scope_id)
        if thread_id is not None:
            where.append("thread_id = ?")
            params.append(thread_id)
        if purpose is not None:
            where.append("invocation_purpose = ?")
            params.append(purpose)
        return where, params

    @staticmethod
    def _token_usage_rollup_select(
        group_expr: str,
        group_alias: str,
        *,
        include_model_classification: bool = False,
    ) -> str:
        # Cutover-INDEPENDENT primitives a renderer applies the model-name
        # precedence over (the MODEL_FIX_CUTOVER_TS comparison itself is a
        # presentation concern, never in SQL). total_tokens is unaffected.
        model_cols = ""
        if include_model_classification:
            model_cols = """,
                         COUNT(DISTINCT model) AS model_distinct,
                         MAX(model) AS model_any,
                         SUM(CASE WHEN model IS NOT NULL THEN 1 ELSE 0 END) AS non_null_sessions,
                         SUM(CASE WHEN model IS NULL AND executor = 'codex' THEN 1 ELSE 0 END) AS null_codex_sessions,
                         SUM(CASE WHEN model IS NULL AND executor = 'claude' THEN 1 ELSE 0 END) AS null_claude_sessions,
                         MIN(CASE WHEN model IS NULL AND executor = 'claude' THEN created_at END) AS null_claude_min_created_at,
                         MAX(CASE WHEN model IS NULL AND executor = 'claude' THEN created_at END) AS null_claude_max_created_at"""
        return f"""SELECT {group_expr} AS {group_alias},
                         COUNT(*) AS sessions,
                         COALESCE(SUM(input_tokens), 0)          AS input_tokens,
                         COALESCE(SUM(output_tokens), 0)         AS output_tokens,
                         COALESCE(SUM(cache_read_tokens), 0)     AS cache_read_tokens,
                         COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
                         COALESCE(SUM(reasoning_tokens), 0)      AS reasoning_tokens,
                         COALESCE(SUM(input_tokens), 0)
                           + COALESCE(SUM(output_tokens), 0)
                           + COALESCE(SUM(reasoning_tokens), 0)  AS total_tokens,
                         COALESCE(SUM(input_tokens), 0)
                           + COALESCE(SUM(output_tokens), 0)
                           + COALESCE(SUM(reasoning_tokens), 0)  AS churn_tokens,
                         COALESCE(SUM(input_tokens), 0)
                           + COALESCE(SUM(output_tokens), 0)
                           + COALESCE(SUM(reasoning_tokens), 0)
                           + COALESCE(SUM(cache_read_tokens), 0)
                           + COALESCE(SUM(cache_creation_tokens), 0)  AS context_tokens{model_cols}
                  FROM session_token_usage"""

    @_synchronized
    def list_session_token_usage(
        self,
        task_id: str | None = None,
        agent: str | None = None,
        since: str | None = None,
        limit: int | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        thread_id: str | None = None,
        purpose: str | None = None,
    ) -> list[dict]:
        """Return per-session rows, newest first."""
        where, params = self._session_token_usage_filters(
            since=since,
            task_id=task_id,
            agent=agent,
            scope_type=scope_type,
            scope_id=scope_id,
            thread_id=thread_id,
            purpose=purpose,
        )
        sql = """SELECT *,
                        COALESCE(scope_type, 'task') AS scope_type,
                        COALESCE(scope_id, task_id) AS scope_id,
                        COALESCE(input_tokens, 0)
                          + COALESCE(output_tokens, 0)
                          + COALESCE(reasoning_tokens, 0) AS total_tokens,
                        COALESCE(input_tokens, 0)
                          + COALESCE(output_tokens, 0)
                          + COALESCE(reasoning_tokens, 0) AS churn_tokens,
                        COALESCE(input_tokens, 0)
                          + COALESCE(output_tokens, 0)
                          + COALESCE(reasoning_tokens, 0)
                          + COALESCE(cache_read_tokens, 0)
                          + COALESCE(cache_creation_tokens, 0) AS context_tokens
                 FROM session_token_usage"""
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def aggregate_session_token_usage_by_agent(
        self,
        since: str | None = None,
        task_id: str | None = None,
        agent: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        thread_id: str | None = None,
        purpose: str | None = None,
    ) -> list[dict]:
        where, params = self._session_token_usage_filters(
            since=since,
            task_id=task_id,
            agent=agent,
            scope_type=scope_type,
            scope_id=scope_id,
            thread_id=thread_id,
            purpose=purpose,
        )
        sql = self._token_usage_rollup_select(
            "agent", "agent", include_model_classification=True
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY agent ORDER BY agent"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def aggregate_session_token_usage_by_task(
        self,
        since: str | None = None,
        agent: str | None = None,
        task_id: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        thread_id: str | None = None,
        purpose: str | None = None,
    ) -> list[dict]:
        where, params = self._session_token_usage_filters(
            since=since,
            task_id=task_id,
            agent=agent,
            scope_type=scope_type,
            scope_id=scope_id,
            thread_id=thread_id,
            purpose=purpose,
        )
        sql = self._token_usage_rollup_select("task_id", "task_id")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY task_id ORDER BY task_id"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def aggregate_session_token_usage_by_failed_task(
        self,
        since: str | None = None,
        agent: str | None = None,
        task_id: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        thread_id: str | None = None,
        purpose: str | None = None,
    ) -> list[dict]:
        """Per-(task, agent) token rollup for FAILED tasks only.

        Read-only INNER JOIN of ``session_token_usage`` to ``tasks`` on the
        canonical ``task_id`` (= ``tasks.id``), keeping only usage tied to a
        task in the terminal ``failed`` status. Caller filters AND-compose via
        the shared filter helper, applied inside the subquery so the JOIN
        cannot collide on ``created_at`` (a column both tables carry).
        """
        where, params = self._session_token_usage_filters(
            since=since,
            task_id=task_id,
            agent=agent,
            scope_type=scope_type,
            scope_id=scope_id,
            thread_id=thread_id,
            purpose=purpose,
        )
        subquery = "SELECT * FROM session_token_usage"
        if where:
            subquery += " WHERE " + " AND ".join(where)
        sql = f"""SELECT s.task_id AS task_id,
                         s.agent AS agent,
                         COUNT(*) AS sessions,
                         COALESCE(SUM(s.input_tokens), 0)          AS input_tokens,
                         COALESCE(SUM(s.output_tokens), 0)         AS output_tokens,
                         COALESCE(SUM(s.cache_read_tokens), 0)     AS cache_read_tokens,
                         COALESCE(SUM(s.cache_creation_tokens), 0) AS cache_creation_tokens,
                         COALESCE(SUM(s.reasoning_tokens), 0)      AS reasoning_tokens,
                         COALESCE(SUM(s.input_tokens), 0)
                           + COALESCE(SUM(s.output_tokens), 0)
                           + COALESCE(SUM(s.reasoning_tokens), 0)  AS total_tokens,
                         COALESCE(SUM(s.input_tokens), 0)
                           + COALESCE(SUM(s.output_tokens), 0)
                           + COALESCE(SUM(s.reasoning_tokens), 0)  AS churn_tokens,
                         COALESCE(SUM(s.input_tokens), 0)
                           + COALESCE(SUM(s.output_tokens), 0)
                           + COALESCE(SUM(s.reasoning_tokens), 0)
                           + COALESCE(SUM(s.cache_read_tokens), 0)
                           + COALESCE(SUM(s.cache_creation_tokens), 0)  AS context_tokens
                  FROM ({subquery}) s
                  JOIN tasks t ON t.id = s.task_id
                  WHERE t.status = ?
                  GROUP BY s.task_id, s.agent
                  ORDER BY s.task_id, s.agent"""
        params.append(TaskStatus.FAILED.value)
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def aggregate_session_token_usage_by_scope(
        self,
        since: str | None = None,
        task_id: str | None = None,
        agent: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        thread_id: str | None = None,
        purpose: str | None = None,
    ) -> list[dict]:
        where, params = self._session_token_usage_filters(
            since=since,
            task_id=task_id,
            agent=agent,
            scope_type=scope_type,
            scope_id=scope_id,
            thread_id=thread_id,
            purpose=purpose,
        )
        sql = """SELECT COALESCE(scope_type, 'task') AS scope_type,
                        COALESCE(scope_id, task_id) AS scope_id,
                        COUNT(*) AS sessions,
                        COALESCE(SUM(input_tokens), 0)          AS input_tokens,
                        COALESCE(SUM(output_tokens), 0)         AS output_tokens,
                        COALESCE(SUM(cache_read_tokens), 0)     AS cache_read_tokens,
                        COALESCE(SUM(cache_creation_tokens), 0) AS cache_creation_tokens,
                        COALESCE(SUM(reasoning_tokens), 0)      AS reasoning_tokens,
                        COALESCE(SUM(input_tokens), 0)
                          + COALESCE(SUM(output_tokens), 0)
                          + COALESCE(SUM(reasoning_tokens), 0)  AS total_tokens,
                        COALESCE(SUM(input_tokens), 0)
                          + COALESCE(SUM(output_tokens), 0)
                          + COALESCE(SUM(reasoning_tokens), 0)  AS churn_tokens,
                        COALESCE(SUM(input_tokens), 0)
                          + COALESCE(SUM(output_tokens), 0)
                          + COALESCE(SUM(reasoning_tokens), 0)
                          + COALESCE(SUM(cache_read_tokens), 0)
                          + COALESCE(SUM(cache_creation_tokens), 0)  AS context_tokens
                 FROM session_token_usage"""
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY COALESCE(scope_type, 'task'), COALESCE(scope_id, task_id)"
        sql += " ORDER BY scope_type, scope_id"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def aggregate_session_token_usage_by_thread(
        self,
        since: str | None = None,
        task_id: str | None = None,
        agent: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        thread_id: str | None = None,
        purpose: str | None = None,
    ) -> list[dict]:
        where, params = self._session_token_usage_filters(
            since=since,
            task_id=task_id,
            agent=agent,
            scope_type=scope_type,
            scope_id=scope_id,
            thread_id=thread_id,
            purpose=purpose,
        )
        where.append("thread_id IS NOT NULL")
        sql = self._token_usage_rollup_select(
            "thread_id", "thread_id", include_model_classification=True
        )
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY thread_id ORDER BY thread_id"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def aggregate_session_token_usage_by_purpose(
        self,
        since: str | None = None,
        task_id: str | None = None,
        agent: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        thread_id: str | None = None,
        purpose: str | None = None,
    ) -> list[dict]:
        where, params = self._session_token_usage_filters(
            since=since,
            task_id=task_id,
            agent=agent,
            scope_type=scope_type,
            scope_id=scope_id,
            thread_id=thread_id,
            purpose=purpose,
        )
        where.append("invocation_purpose IS NOT NULL")
        sql = self._token_usage_rollup_select("invocation_purpose", "purpose")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY invocation_purpose ORDER BY invocation_purpose"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @_synchronized
    def aggregate_session_token_usage_by_model(
        self,
        since: str | None = None,
        task_id: str | None = None,
        agent: str | None = None,
        scope_type: str | None = None,
        scope_id: str | None = None,
        thread_id: str | None = None,
        purpose: str | None = None,
    ) -> list[dict]:
        """Roll up session_token_usage grouped by model.

        NULL models are honest (not blank, not a guessed correction).
        The ``since`` window AND-composes with every other filter.
        """
        where, params = self._session_token_usage_filters(
            since=since,
            task_id=task_id,
            agent=agent,
            scope_type=scope_type,
            scope_id=scope_id,
            thread_id=thread_id,
            purpose=purpose,
        )
        sql = self._token_usage_rollup_select("model", "model")
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY model ORDER BY COALESCE(model, '')"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    # --- Thread Sessions ---

    @_synchronized
    def get_thread_session(
        self, thread_id: str, agent_name: str
    ) -> tuple[str | None, int]:
        """Return (agent_session_id, last_resumed_seq) for a (thread, agent).

        Returns (None, 0) when the participant row is absent — the safe
        turn-1 default that drives a full-context first invocation.
        """
        cursor = self._conn.execute(
            "SELECT agent_session_id, last_resumed_seq FROM thread_participants "
            "WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        )
        row = cursor.fetchone()
        if row is None:
            return (None, 0)
        return (row["agent_session_id"], row["last_resumed_seq"] or 0)

    @_synchronized
    def update_thread_session(
        self,
        thread_id: str,
        agent_name: str,
        *,
        agent_session_id: str | None,
        last_resumed_seq: int,
    ) -> None:
        """Persist the resumable session id + delta watermark for a participant."""
        self._conn.execute(
            "UPDATE thread_participants SET agent_session_id = ?, last_resumed_seq = ? "
            "WHERE thread_id = ? AND agent_name = ?",
            (agent_session_id, last_resumed_seq, thread_id, agent_name),
        )
        self._conn.commit()

    @_synchronized
    def invalidate_thread_session_evicted(
        self,
        thread_id: str,
        agent_name: str,
        *,
        stale_session_id: str,
        error: str,
        executor: str = "claude",
    ) -> None:
        """One transaction: eviction audit + durable session-id invalidation.

        THR-200: fires at the provider-declared session-not-found boundary,
        BEFORE the full-prompt fallback launch. The audit row and the
        ``agent_session_id = NULL`` update commit atomically, so a failed
        fallback can never leave the stale id durable for the next wake.
        ``last_resumed_seq`` is intentionally preserved — a failed fallback
        must not advance delivery state; the next wake re-attempts the same
        required range (the id being NULL forces a full-prompt launch).
        """
        payload = {
            "executor": executor,
            "stale_session_id": stale_session_id,
            "error": (error or "")[:500],
        }
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    thread_id,
                    agent_name,
                    "agent_session_evicted_fallback",
                    json.dumps(payload),
                    now,
                ),
            )
            self._conn.execute(
                "UPDATE thread_participants SET agent_session_id = NULL "
                "WHERE thread_id = ? AND agent_name = ?",
                (thread_id, agent_name),
            )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def reset_thread_session(
        self, thread_id: str, agent_name: str
    ) -> None:
        """Clear one participant's resume state: id NULL, watermark 0.

        Used by lifecycle invalidation (archive, executor switch, agent
        termination) where any later thread resume must start fresh with a
        full-prompt launch.
        """
        self._conn.execute(
            "UPDATE thread_participants SET agent_session_id = NULL, "
            "last_resumed_seq = 0 WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        )
        self._conn.commit()

    @_synchronized
    def reset_thread_sessions_for_agent(
        self,
        agent_name: str,
        *,
        audit_scope_id: str | None = None,
        audit_agent: str | None = None,
        audit_reason: str | None = None,
    ) -> int:
        """Clear resume state (id NULL, watermark 0) for every thread
        participant row owned by ``agent_name``. Returns the row count.

        Executor-switch and agent-termination lifecycle: a participant whose
        executor changes must not resume a provider session minted under a
        different executor profile; a terminated agent must not resume at all.

        THR-200: when ``audit_scope_id`` is given, the participant reset and
        the ``thread_session_invalidated`` audit row commit in ONE database
        transaction, so a reset/audit failure can never leave a partially
        committed lifecycle state (a new executor installed over stale
        sessions, or cleared sessions with no audit). The audit is only
        written when at least one row was reset.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self._reset_thread_sessions_for_agent_uncommitted(agent_name)
            if rows and audit_scope_id is not None:
                self.insert_audit_log_uncommitted(
                    task_id=audit_scope_id,
                    agent=audit_agent,
                    action="thread_session_invalidated",
                    payload={
                        "reason": audit_reason,
                        "rows": rows,
                        "name": agent_name,
                    },
                )
            self._conn.commit()
            return rows
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def _reset_thread_sessions_for_agent_uncommitted(self, agent_name: str) -> int:
        """UPDATE every participant row owned by ``agent_name`` WITHOUT
        committing. The owning transaction (``reset_thread_sessions_for_agent``,
        ``terminate_agent_cleanups``) commits or rolls back."""
        cursor = self._conn.execute(
            "UPDATE thread_participants SET agent_session_id = NULL, "
            "last_resumed_seq = 0 WHERE agent_name = ?",
            (agent_name,),
        )
        return cursor.rowcount

    @_synchronized
    def reset_thread_sessions_for_thread(
        self,
        thread_id: str,
        *,
        audit_scope_id: str | None = None,
        audit_agent: str | None = None,
        audit_reason: str | None = None,
    ) -> int:
        """Clear resume state (id NULL, watermark 0) for every participant of
        one thread. Returns the row count.

        Archive lifecycle: the thread is closed; if it is ever re-opened,
        every participant resumes from a fresh full-prompt launch.

        THR-200: when ``audit_scope_id`` is given, the participant reset and
        the ``thread_session_invalidated`` audit row commit in ONE database
        transaction, so a reset/audit failure can never leave a partially
        committed lifecycle state (cleared sessions with no audit). The audit
        is only written when at least one row was reset.
        """
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            rows = self._reset_thread_sessions_for_thread_uncommitted(thread_id)
            if rows and audit_scope_id is not None:
                self.insert_audit_log_uncommitted(
                    task_id=audit_scope_id,
                    agent=audit_agent,
                    action="thread_session_invalidated",
                    payload={"reason": audit_reason, "rows": rows},
                )
            self._conn.commit()
            return rows
        except Exception:
            self._conn.rollback()
            raise

    @_synchronized
    def _reset_thread_sessions_for_thread_uncommitted(self, thread_id: str) -> int:
        """UPDATE every participant row of one thread WITHOUT committing. The
        owning transaction (``reset_thread_sessions_for_thread``,
        ``archive_thread_and_reset_sessions``) commits or rolls back."""
        cursor = self._conn.execute(
            "UPDATE thread_participants SET agent_session_id = NULL, "
            "last_resumed_seq = 0 WHERE thread_id = ?",
            (thread_id,),
        )
        return cursor.rowcount
