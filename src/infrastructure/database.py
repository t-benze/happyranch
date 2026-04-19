from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.models import TaskRecord, TaskStatus


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # FastAPI dispatches sync route handlers on a threadpool, so the
        # connection is read from threads other than its creator. Serialize
        # writes via DaemonState.db_lock.
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_tables()

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_agent TEXT,
                team TEXT NOT NULL DEFAULT 'product_engineering',
                brief TEXT NOT NULL,
                revision_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                parent_task_id TEXT,
                final_output_summary TEXT,
                final_artifact_dir TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                action TEXT NOT NULL,
                payload TEXT,
                timestamp TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS scorecards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL UNIQUE,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                acceptance_rate REAL NOT NULL,
                revision_rate REAL NOT NULL,
                error_count INTEGER NOT NULL,
                tier TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS task_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                session_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'completed',
                output_summary TEXT,
                confidence_score INTEGER,
                learnings TEXT,
                risks_flagged TEXT,
                duration_seconds INTEGER,
                token_count INTEGER,
                estimated_cost REAL,
                artifact_dir TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_enrollments (
                name TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                system_prompt TEXT NOT NULL,
                repos TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)
        # Best-effort migration for DBs created before `status` existed. SQLite
        # has no IF NOT EXISTS for ADD COLUMN; swallow the duplicate-column
        # error so this is idempotent across restarts.
        try:
            self._conn.execute(
                "ALTER TABLE task_results ADD COLUMN status TEXT NOT NULL DEFAULT 'completed'"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute("ALTER TABLE tasks ADD COLUMN parent_task_id TEXT")
        except sqlite3.OperationalError:
            pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_task_id)"
        )
        for ddl in (
            "ALTER TABLE tasks ADD COLUMN final_output_summary TEXT",
            "ALTER TABLE tasks ADD COLUMN final_artifact_dir TEXT",
            "ALTER TABLE task_results ADD COLUMN artifact_dir TEXT",
            # crew → team rename (SQLite >= 3.25). Idempotent: fails on
            # DBs where the column is already `team` or already renamed.
            "ALTER TABLE tasks RENAME COLUMN crew TO team",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass

        # --- Task-status redesign migration (idempotent) ---
        # Add new columns; swallow duplicate errors on subsequent startups.
        for ddl in (
            "ALTER TABLE tasks ADD COLUMN block_kind TEXT",
            "ALTER TABLE tasks ADD COLUMN note TEXT",
            "ALTER TABLE tasks ADD COLUMN orchestration_step_count INTEGER DEFAULT 0",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass

        # One-shot data remap. Guard with a sentinel so re-runs are no-ops.
        applied = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='tasks' "
            "AND sql LIKE '%block_kind%'"
        ).fetchone()
        if applied is not None:
            # Fold final_output_summary → note where not already set.
            self._conn.execute(
                "UPDATE tasks SET note = final_output_summary "
                "WHERE note IS NULL AND final_output_summary IS NOT NULL"
            )
            # Old-world → new-world status mapping. Each UPDATE is narrow so
            # re-running is a no-op (no rows match the WHERE clause the 2nd time).
            self._conn.execute("UPDATE tasks SET status='completed' WHERE status='approved'")
            self._conn.execute("UPDATE tasks SET status='failed'    WHERE status='rejected'")
            self._conn.execute(
                "UPDATE tasks SET status='blocked', block_kind='escalated' "
                "WHERE status='escalated'"
            )
            # Normalize dead legacy values.
            self._conn.execute("UPDATE tasks SET status='failed' WHERE status='in_review'")
            self._conn.commit()

    def list_tables(self) -> list[str]:
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [row["name"] for row in cursor.fetchall()]

    # --- Tasks ---

    def insert_task(self, task: TaskRecord) -> None:
        self._conn.execute(
            """INSERT INTO tasks (id, type, status, assigned_agent, team, brief,
               revision_count, created_at, updated_at, completed_at, parent_task_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task.id,
                task.type.value,
                task.status.value,
                task.assigned_agent,
                task.team,
                task.brief,
                task.revision_count,
                task.created_at.isoformat(),
                task.updated_at.isoformat(),
                task.completed_at.isoformat() if task.completed_at else None,
                task.parent_task_id,
            ),
        )
        self._conn.commit()

    def get_task(self, task_id: str) -> TaskRecord | None:
        cursor = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return TaskRecord(
            id=row["id"],
            type=row["type"],
            status=row["status"],
            assigned_agent=row["assigned_agent"],
            team=row["team"],
            brief=row["brief"],
            revision_count=row["revision_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            parent_task_id=row["parent_task_id"],
            final_output_summary=row["final_output_summary"],
            final_artifact_dir=row["final_artifact_dir"],
        )

    def list_tasks(self, limit: int = 20) -> list[TaskRecord]:
        cursor = self._conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [
            TaskRecord(
                id=row["id"],
                type=row["type"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                final_output_summary=row["final_output_summary"],
                final_artifact_dir=row["final_artifact_dir"],
            )
            for row in cursor.fetchall()
        ]

    def get_children(self, parent_task_id: str) -> list[str]:
        """Return direct children of a task, ordered by creation time."""
        cursor = self._conn.execute(
            "SELECT id FROM tasks WHERE parent_task_id = ? ORDER BY created_at",
            (parent_task_id,),
        )
        return [row["id"] for row in cursor.fetchall()]

    def get_recall_payload(self, task_id: str) -> dict | None:
        """Return a flat dict suitable for the /recall endpoint, or None.

        ``children`` is the list of direct child task ids — the route layer
        promotes them to full payloads when ``tree=true``.
        """
        task = self.get_task(task_id)
        if task is None:
            return None
        created_at = (
            task.created_at.isoformat()
            if hasattr(task.created_at, "isoformat")
            else task.created_at
        )
        completed_at = (
            task.completed_at.isoformat()
            if hasattr(task.completed_at, "isoformat")
            else task.completed_at
        )
        return {
            "task_id": task.id,
            "parent_task_id": task.parent_task_id,
            "assigned_agent": task.assigned_agent,
            "brief": task.brief,
            "status": task.status.value,
            "created_at": created_at,
            "completed_at": completed_at,
            "output_summary": task.final_output_summary,
            "artifact_dir": task.final_artifact_dir,
            "children": self.get_children(task.id),
        }

    def list_agent_tasks(self, agent: str, limit: int = 50) -> list[TaskRecord]:
        """Return tasks assigned to an agent, newest-first.

        Orders by the latest available timestamp (completed_at > updated_at >
        created_at) as a lexicographic string compare — our ISO-8601 values
        include microseconds and +00:00 which SQLite's ``datetime()`` parser
        rejects, but they sort correctly as raw strings.
        """
        cursor = self._conn.execute(
            """SELECT * FROM tasks WHERE assigned_agent = ?
               ORDER BY COALESCE(completed_at, updated_at, created_at) DESC
               LIMIT ?""",
            (agent, limit),
        )
        return [
            TaskRecord(
                id=row["id"],
                type=row["type"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                final_output_summary=row["final_output_summary"],
                final_artifact_dir=row["final_artifact_dir"],
            )
            for row in cursor.fetchall()
        ]

    def update_task(self, task_id: str, **fields: object) -> None:
        allowed = {
            "status", "assigned_agent", "revision_count", "completed_at",
            "final_output_summary", "final_artifact_dir",
        }
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        for k, v in updates.items():
            if hasattr(v, "value"):
                updates[k] = v.value
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        self._conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        self._conn.commit()

    def increment_revision_count(self, task_id: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET revision_count = revision_count + 1, updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), task_id),
        )
        self._conn.commit()

    def next_task_id(self) -> str:
        cursor = self._conn.execute("SELECT COUNT(*) as cnt FROM tasks")
        count = cursor.fetchone()["cnt"]
        return f"TASK-{count + 1:03d}"

    def get_nonterminal_task_ids(self) -> list[str]:
        nonterminal = (TaskStatus.PENDING.value, TaskStatus.IN_PROGRESS.value)
        cursor = self._conn.execute(
            f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(nonterminal))})",
            nonterminal,
        )
        return [row["id"] for row in cursor.fetchall()]

    # --- Audit Log ---

    def insert_audit_log(
        self,
        task_id: str,
        agent: str,
        action: str,
        payload: dict | None = None,
    ) -> None:
        self._conn.execute(
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

    def query_audit_logs(
        self,
        task_id: str | None = None,
        agent: str | None = None,
        action: str | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Filtered audit-log query used by the /audit route.

        All filters are optional and AND-composed. ``limit`` returns the most
        recent N rows (ORDER BY id DESC) but the result is re-sorted ascending
        so callers still see chronological order.
        """
        clauses: list[str] = []
        params: list[object] = []
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if agent is not None:
            clauses.append("agent = ?")
            params.append(agent)
        if action is not None:
            clauses.append("action = ?")
            params.append(action)
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        if limit is not None:
            sql = f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT ?"
            params.append(limit)
        else:
            sql = f"SELECT * FROM audit_log {where} ORDER BY id ASC"
        cursor = self._conn.execute(sql, tuple(params))
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("payload"):
                d["payload"] = json.loads(d["payload"])
            result.append(d)
        # When `limit` forces DESC, re-sort ascending so the CLI renders the
        # oldest-first timeline readers expect.
        if limit is not None:
            result.sort(key=lambda d: d["id"])
        return result

    # --- Task Results ---

    def insert_task_result(
        self,
        task_id: str,
        agent: str,
        session_id: str,
        output_summary: str,
        confidence_score: int,
        status: str = "completed",
        risks_flagged: list[str] | None = None,
        learnings: str | None = None,
        duration_seconds: int | None = None,
        token_count: int | None = None,
        estimated_cost: float | None = None,
        artifact_dir: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO task_results
               (task_id, agent, session_id, status, output_summary, confidence_score,
                learnings, risks_flagged, duration_seconds, token_count, estimated_cost,
                artifact_dir, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                agent,
                session_id,
                status,
                output_summary,
                confidence_score,
                learnings,
                json.dumps(risks_flagged) if risks_flagged is not None else None,
                duration_seconds,
                token_count,
                estimated_cost,
                artifact_dir,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    def get_task_results(self, task_id: str) -> list[dict]:
        cursor = self._conn.execute(
            "SELECT * FROM task_results WHERE task_id = ? ORDER BY id", (task_id,)
        )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("risks_flagged"):
                d["risks_flagged"] = json.loads(d["risks_flagged"])
            result.append(d)
        return result

    def get_agent_task_results(self, agent: str, since: str | None = None) -> list[dict]:
        if since:
            cursor = self._conn.execute(
                "SELECT * FROM task_results WHERE agent = ? AND created_at >= ? ORDER BY id",
                (agent, since),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM task_results WHERE agent = ? ORDER BY id", (agent,)
            )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("risks_flagged"):
                d["risks_flagged"] = json.loads(d["risks_flagged"])
            result.append(d)
        return result

    def get_latest_task_result(
        self, task_id: str, agent: str, session_id: str,
    ) -> dict | None:
        cursor = self._conn.execute(
            """SELECT * FROM task_results
               WHERE task_id = ? AND agent = ? AND session_id = ?
               ORDER BY id DESC LIMIT 1""",
            (task_id, agent, session_id),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        if d.get("risks_flagged"):
            d["risks_flagged"] = json.loads(d["risks_flagged"])
        return d

    # --- Scorecards ---

    def upsert_scorecard(
        self,
        agent: str,
        period_start: str,
        period_end: str,
        acceptance_rate: float,
        revision_rate: float,
        error_count: int,
        tier: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO scorecards (agent, period_start, period_end, acceptance_rate,
               revision_rate, error_count, tier, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(agent) DO UPDATE SET
               period_start=excluded.period_start, period_end=excluded.period_end,
               acceptance_rate=excluded.acceptance_rate, revision_rate=excluded.revision_rate,
               error_count=excluded.error_count, tier=excluded.tier, updated_at=excluded.updated_at""",
            (agent, period_start, period_end, acceptance_rate, revision_rate, error_count, tier, now),
        )
        self._conn.commit()

    def get_scorecard(self, agent: str) -> dict | None:
        cursor = self._conn.execute("SELECT * FROM scorecards WHERE agent = ?", (agent,))
        row = cursor.fetchone()
        return dict(row) if row else None

    # --- Agent Enrollments ---

    def insert_enrollment(
        self,
        name: str,
        description: str,
        system_prompt: str,
        repos: dict[str, str] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO agent_enrollments (name, description, system_prompt, repos, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
            (name, description, system_prompt, json.dumps(repos or {}), now, now),
        )
        self._conn.commit()

    def get_enrollment(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM agent_enrollments WHERE name = ?", (name,),
        ).fetchone()
        return dict(row) if row else None

    def list_enrollments(self, status: str | None = None) -> list[dict]:
        if status:
            rows = self._conn.execute(
                "SELECT * FROM agent_enrollments WHERE status = ? ORDER BY created_at",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM agent_enrollments ORDER BY created_at",
            ).fetchall()
        return [dict(r) for r in rows]

    def update_enrollment_status(self, name: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE agent_enrollments SET status = ?, updated_at = ? WHERE name = ?",
            (status, now, name),
        )
        self._conn.commit()

    def update_enrollment_fields(
        self,
        name: str,
        description: str | None = None,
        system_prompt: str | None = None,
        repos: dict[str, str] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        updates = ["updated_at = ?"]
        params: list = [now]
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        if system_prompt is not None:
            updates.append("system_prompt = ?")
            params.append(system_prompt)
        if repos is not None:
            updates.append("repos = ?")
            params.append(json.dumps(repos))
        params.append(name)
        self._conn.execute(
            f"UPDATE agent_enrollments SET {', '.join(updates)} WHERE name = ?",
            params,
        )
        self._conn.commit()

    def delete_enrollment(self, name: str) -> None:
        self._conn.execute("DELETE FROM agent_enrollments WHERE name = ?", (name,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
