from __future__ import annotations

import json
from datetime import datetime, timezone

from runtime.infrastructure.db._shared import _synchronized


class AuditMixin:
    # --- Audit Log ---

    @_synchronized
    def insert_audit_log(
        self,
        task_id: str,
        agent: str,
        action: str,
        payload: dict | None = None,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)",
            (
                task_id,
                agent,
                action,
                json.dumps(payload) if payload else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()
        return cur.lastrowid

    @_synchronized
    def insert_audit_log_uncommitted(
        self,
        task_id: str,
        agent: str,
        action: str,
        payload: dict | None = None,
    ) -> int:
        """Insert an audit row WITHOUT committing the transaction.

        The caller must call ``commit()`` to make the row durable.
        If the connection is closed without a commit (or an exception
        propagates through a context manager that closes the handle),
        the row is rolled back — no audit residue.

        This exists so that compound operations (e.g. adapter-profile
        binding) can batch the durable profile write, in-memory registry
        update, and audit write in a single logical transaction with a
        clean rollback path.  The standard ``insert_audit_log`` (which
        commits inline) is retained for all existing callers.
        """
        cur = self._conn.execute(
            "INSERT INTO audit_log (task_id, agent, action, payload, timestamp) VALUES (?, ?, ?, ?, ?)",
            (
                task_id,
                agent,
                action,
                json.dumps(payload) if payload else None,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        return cur.lastrowid

    @_synchronized
    def get_audit_logs(self, task_id: str) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT * FROM audit_log WHERE task_id = ? ORDER BY id", (task_id,)
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("payload"):
                d["payload"] = json.loads(d["payload"])
            result.append(d)
        return result

    @_synchronized
    def get_escalation_episode_audit_tail(
        self, task_id: str, *, limit: int = 256,
    ) -> list[dict]:
        """Return the bounded audit subset needed for escalation display.

        Exact ``task_id`` equality keeps task rows separate from ``config:``,
        thread, and other scope-prefixed audit ids. The primary-key ``id``
        order makes the newest rows authoritative. The refusal writer's causal
        decision is adjacent in this action-filtered stream; the hard cap keeps
        a task with a long audit history from expanding the read model without
        bound and fails absent rather than consulting older rows.
        """
        cursor = self._conn.execute(
            "SELECT * FROM audit_log "
            "WHERE task_id = ? AND action IN ('escalation', 'orchestration_step') "
            "ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        )
        result: list[dict] = []
        for row in cursor.fetchall():
            item = dict(row)
            if item.get("payload"):
                item["payload"] = json.loads(item["payload"])
            result.append(item)
        return result

    @_synchronized
    def get_audit_logs_by_action(self, action: str, since: str | None = None) -> list[dict]:
        """Get audit logs filtered by action, optionally since a date."""
        if since:
            cursor = self._conn.execute(
                "SELECT * FROM audit_log WHERE action = ? AND timestamp >= ? ORDER BY id",
                (action, since),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM audit_log WHERE action = ? ORDER BY id", (action,)
            )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("payload"):
                d["payload"] = json.loads(d["payload"])
            result.append(d)
        return result

    def get_audit_logs_for_agent_since(
        self, agent: str, since: str, *, limit: int = 200,
    ) -> list[dict]:
        """Audit rows authored by ``agent`` with ``timestamp >= since`` (ISO),
        capped to the most recent ``limit`` in chronological order.

        Window-scoped accessor for the dream input window (spec "Input Window":
        "audit rows involving the agent since window_start"). Distinct from
        ``get_audit_logs(task_id)``, which is keyed on the scope-id column.
        Delegates to ``query_audit_logs`` to avoid duplicating the filter SQL.
        """
        entries, _ = self.query_audit_logs(agent=agent, since=since, limit=limit)
        return entries

    @_synchronized
    def has_task_completion_report_audit(self, *, task_id: str, agent: str, session_id: str, result_row_id: int) -> bool:
        """Whether recovery re-entry already has a completion-report receipt."""
        row = self._conn.execute(
            """SELECT 1 FROM audit_log WHERE task_id=? AND agent=?
               AND action='completion_report'
               AND json_extract(payload, '$._recovery_session_id')=?
               AND json_extract(payload, '$._result_row_id')=? LIMIT 1""",
            (task_id, agent, session_id, result_row_id),
        ).fetchone()
        return row is not None

    @_synchronized
    def has_orchestration_step_audit(self, *, task_id: str, step_number: int) -> bool:
        """Return whether this durable manager step was already recorded.

        Recovery has one immutable accepted result but the ordinary manager
        consumer records its step audit before applying its decision.  A
        process loss in that gap must re-enter the decision without minting a
        second step audit; the task's already-incremented step number is the
        established durable key for that audit.
        """
        row = self._conn.execute(
            """SELECT 1 FROM audit_log WHERE task_id=? AND agent='orchestrator'
               AND action='orchestration_step'
               AND json_extract(payload, '$.step_number')=? LIMIT 1""",
            (task_id, step_number),
        ).fetchone()
        return row is not None
