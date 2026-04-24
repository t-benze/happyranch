from __future__ import annotations

import functools
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.models import BlockKind, TalkRecord, TaskRecord, TaskStatus


class LineageTooDeep(Exception):
    """Ancestor walk exceeded the safety bound; indicates data corruption."""


def _synchronized(method):
    """Serialize every public ``Database`` call through ``self._lock``.

    Why: the daemon shares ONE sqlite3 connection across the event-loop thread
    (async routes) and the threadpool thread running ``Orchestrator.run_step``
    (see ``src/daemon/queue.py``). ``DaemonState.db_lock`` is an ``asyncio.Lock``
    and can't serialize against threads; ``check_same_thread=False`` on the
    connection allows cross-thread access but not concurrent cursor/exec ops —
    overlap raises ``sqlite3.InterfaceError`` or hands back rows with None-valued
    columns. A ``threading.RLock`` inside ``Database`` closes that gap without
    per-thread connections or a migration.
    """
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapper


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        # See `_synchronized` for the threading model. RLock (not Lock) because
        # e.g. `walk_ancestors` → `get_task` and `get_recall_payload` → `get_task`
        # both re-enter public methods while already holding the lock.
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._tasks_has_legacy_type_column: bool = False
        self._create_tables()
        self._detect_legacy_columns()

    def _detect_legacy_columns(self) -> None:
        """Detect legacy columns that may still exist on upgraded DBs.

        Called once after _create_tables() completes. Fresh DBs never have the
        ``type`` column (dropped in the Task-4 schema refactor). Runtimes
        created before that change retain it as ``TEXT NOT NULL`` with no SQL
        default — insert_task must supply a sentinel value or SQLite raises
        IntegrityError.
        """
        cursor = self._conn.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in cursor.fetchall()}
        self._tasks_has_legacy_type_column = "type" in columns

    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_agent TEXT,
                team TEXT NOT NULL DEFAULT 'engineering',
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
                decision_json TEXT,
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
                executor TEXT NOT NULL DEFAULT 'claude',
                allow_rules TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS talks (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                summary TEXT,
                topic_list_json TEXT,
                new_learnings_count INTEGER NOT NULL DEFAULT 0,
                new_kb_slugs_json TEXT,
                transcript_path TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_talks_agent_status ON talks(agent_name, status);
            CREATE INDEX IF NOT EXISTS idx_talks_started ON talks(started_at);
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
            # EH-only structured decision payload (serialized NextStep JSON).
            # NULL for worker rows. Replaces the prose-in-output_summary
            # double-encoding contract — see TASK-071 post-mortem.
            "ALTER TABLE task_results ADD COLUMN decision_json TEXT",
            "ALTER TABLE agent_enrollments ADD COLUMN executor TEXT NOT NULL DEFAULT 'claude'",
            "ALTER TABLE agent_enrollments ADD COLUMN allow_rules TEXT NOT NULL DEFAULT '[]'",
            # crew → team rename (SQLite >= 3.25). Idempotent: fails on
            # DBs where the column is already `team` or already renamed.
            "ALTER TABLE tasks RENAME COLUMN crew TO team",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass

        # Remap legacy team value: 'product_engineering' → 'engineering'.
        try:
            self._conn.execute(
                "UPDATE tasks SET team='engineering' WHERE team='product_engineering'"
            )
            self._conn.commit()
        except sqlite3.OperationalError:
            pass

        # --- Task-status redesign migration (idempotent) ---
        # Add new columns; swallow duplicate errors on subsequent startups.
        for ddl in (
            "ALTER TABLE tasks ADD COLUMN block_kind TEXT",
            "ALTER TABLE tasks ADD COLUMN note TEXT",
            "ALTER TABLE tasks ADD COLUMN orchestration_step_count INTEGER DEFAULT 0",
            # cancelled_at: founder-initiated cancellation marker. Distinct
            # from completed_at/status=failed so run_step can recognise a
            # SIGTERM'd session as "cancelled" (not a retryable failure) and
            # idempotent _fail calls don't overwrite the founder's note.
            "ALTER TABLE tasks ADD COLUMN cancelled_at TEXT",
            # Revisit link: see docs/superpowers/specs/2026-04-23-revisit-root-link-design.md.
            # Sideways reference to the predecessor root of a revisit; NULL for
            # non-revisit tasks. walk_ancestors MUST NOT follow this column —
            # that's the attempt-isolation invariant from the v2 revisit spec.
            "ALTER TABLE tasks ADD COLUMN revisit_of_task_id TEXT",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        # Index the reverse lookup (`WHERE revisit_of_task_id = ?`).
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_revisit_of ON tasks(revisit_of_task_id)"
        )

        # --- Revisit link backfill ---
        # Historical revisit rows (created before revisit_of_task_id existed)
        # have the column but no value; the link lives only in audit_log's
        # revisit_of entry. Populate the column from those entries.
        # IS NULL guard makes this safely idempotent across restarts.
        self._backfill_revisit_of_task_id()

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

    def _backfill_revisit_of_task_id(self) -> None:
        # Called from _create_tables during __init__, which is single-threaded
        # by construction (Database is instantiated once per daemon, before
        # any worker threads start). Accessing self._conn directly without
        # @_synchronized is therefore safe here; do not call from elsewhere.
        cursor = self._conn.execute(
            "SELECT task_id, payload FROM audit_log WHERE action = 'revisit_of'"
        )
        for row in cursor.fetchall():
            if not row["payload"]:
                continue
            try:
                payload = json.loads(row["payload"])
            except json.JSONDecodeError:
                continue
            predecessor_root = payload.get("predecessor_root")
            if not predecessor_root:
                continue
            self._conn.execute(
                "UPDATE tasks SET revisit_of_task_id = ? "
                "WHERE id = ? AND revisit_of_task_id IS NULL",
                (predecessor_root, row["task_id"]),
            )
        self._conn.commit()

    @_synchronized
    def list_tables(self) -> list[str]:
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        return [row["name"] for row in cursor.fetchall()]

    # --- Tasks ---

    @_synchronized
    def insert_task(self, task: TaskRecord) -> None:
        params = (
            task.id,
            task.status.value,
            task.assigned_agent,
            task.team,
            task.brief,
            task.revision_count,
            task.created_at.isoformat(),
            task.updated_at.isoformat(),
            task.completed_at.isoformat() if task.completed_at else None,
            task.parent_task_id,
            task.revisit_of_task_id,
            task.block_kind.value if task.block_kind else None,
            task.note,
            task.orchestration_step_count,
        )
        if self._tasks_has_legacy_type_column:
            # Legacy DBs (created before the Task-4 schema refactor) retain a
            # `type TEXT NOT NULL` column with no SQL default. Supply a sentinel
            # value to satisfy the NOT NULL constraint without re-adding the
            # column to the current schema.
            # params[0] = id; insert type="general" after id, then the rest.
            self._conn.execute(
                """INSERT INTO tasks (id, type, status, assigned_agent, team, brief,
                   revision_count, created_at, updated_at, completed_at, parent_task_id,
                   revisit_of_task_id, block_kind, note, orchestration_step_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (params[0], "general") + params[1:],
            )
        else:
            self._conn.execute(
                """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                   revision_count, created_at, updated_at, completed_at, parent_task_id,
                   revisit_of_task_id, block_kind, note, orchestration_step_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                params,
            )
        self._conn.commit()

    @_synchronized
    def get_task(self, task_id: str) -> TaskRecord | None:
        cursor = self._conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return TaskRecord(
            id=row["id"],
            status=row["status"],
            assigned_agent=row["assigned_agent"],
            team=row["team"],
            brief=row["brief"],
            revision_count=row["revision_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            parent_task_id=row["parent_task_id"],
            revisit_of_task_id=row["revisit_of_task_id"],
            block_kind=row["block_kind"],
            note=row["note"],
            orchestration_step_count=row["orchestration_step_count"] or 0,
            final_artifact_dir=row["final_artifact_dir"],
            cancelled_at=row["cancelled_at"],
        )

    @_synchronized
    def list_tasks(self, limit: int = 20) -> list[TaskRecord]:
        cursor = self._conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [
            TaskRecord(
                id=row["id"],
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                revisit_of_task_id=row["revisit_of_task_id"],
                block_kind=row["block_kind"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_artifact_dir=row["final_artifact_dir"],
                cancelled_at=row["cancelled_at"],
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def get_children(self, parent_task_id: str) -> list[str]:
        """Return direct children of a task, ordered by creation time."""
        cursor = self._conn.execute(
            "SELECT id FROM tasks WHERE parent_task_id = ? ORDER BY created_at",
            (parent_task_id,),
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def get_direct_revisits(self, task_id: str) -> list[str]:
        """Return IDs of tasks whose revisit_of_task_id points at this task,
        ordered by creation. Uses idx_tasks_revisit_of.
        """
        cursor = self._conn.execute(
            "SELECT id FROM tasks WHERE revisit_of_task_id = ? ORDER BY created_at",
            (task_id,),
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def walk_ancestors(self, task_id: str, max_hops: int = 20) -> list[TaskRecord]:
        """Return [task, parent, ..., root] by following parent_task_id.

        Raises LineageTooDeep if the walk exceeds max_hops (defensive bound;
        real lineages are 2-4 deep). A missing intermediate task truncates the
        walk silently — callers see the chain they could reconstruct.
        """
        chain: list[TaskRecord] = []
        current_id: str | None = task_id
        for _ in range(max_hops):
            if current_id is None:
                return chain
            task = self.get_task(current_id)
            if task is None:
                return chain
            chain.append(task)
            current_id = task.parent_task_id
        if current_id is not None:
            raise LineageTooDeep(f"walk from {task_id} exceeded {max_hops} hops")
        return chain

    @_synchronized
    def walk_revisit_chain(
        self, task_id: str, max_hops: int = 20, truncate: bool = False,
    ) -> list[TaskRecord]:
        """Return [task, predecessor, ..., original] by following revisit_of_task_id.

        Sideways edge — does NOT cross into parent_task_id ancestor space.
        Non-revisit tasks return [task]. Missing task returns []. Overruns
        raise LineageTooDeep by default (same pattern as walk_ancestors); pass
        truncate=True to return the first max_hops entries instead — read
        paths use this because revisit history grows naturally over a task's
        lifetime and must not 500 once it exceeds the defensive bound.
        """
        chain: list[TaskRecord] = []
        current_id: str | None = task_id
        for _ in range(max_hops):
            if current_id is None:
                return chain
            task = self.get_task(current_id)
            if task is None:
                return chain
            chain.append(task)
            current_id = task.revisit_of_task_id
        if current_id is not None and not truncate:
            raise LineageTooDeep(
                f"revisit chain from {task_id} exceeded {max_hops} hops"
            )
        return chain

    @_synchronized
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
            "revisit_of_task_id": task.revisit_of_task_id,
            "assigned_agent": task.assigned_agent,
            "brief": task.brief,
            "status": task.status.value,
            "created_at": created_at,
            "completed_at": completed_at,
            "output_summary": task.note,
            "artifact_dir": task.final_artifact_dir,
            "children": self.get_children(task.id),
        }

    @_synchronized
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
                status=row["status"],
                assigned_agent=row["assigned_agent"],
                team=row["team"],
                brief=row["brief"],
                revision_count=row["revision_count"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                parent_task_id=row["parent_task_id"],
                revisit_of_task_id=row["revisit_of_task_id"],
                block_kind=row["block_kind"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_artifact_dir=row["final_artifact_dir"],
                cancelled_at=row["cancelled_at"],
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def update_task(self, task_id: str, **fields: object) -> None:
        allowed = {
            "status", "assigned_agent", "revision_count", "completed_at",
            "block_kind", "note", "orchestration_step_count",
            "final_artifact_dir", "cancelled_at",
        }
        # NOTE: filter on membership, not on None-ness — block_kind must be
        # resettable to NULL when a task unblocks.
        updates: dict[str, object] = {}
        for k, v in fields.items():
            if k not in allowed:
                continue
            if hasattr(v, "value"):
                updates[k] = v.value
            else:
                updates[k] = v
        if not updates:
            return
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [task_id]
        self._conn.execute(f"UPDATE tasks SET {set_clause} WHERE id = ?", values)
        self._conn.commit()

    @_synchronized
    def try_claim_for_step(
        self,
        task_id: str,
        expected_status: TaskStatus,
        expected_block_kind: BlockKind | None,
        new_count: int,
    ) -> bool:
        """Atomic compare-and-swap for the run_step entry transition.

        Transitions the row to status=in_progress, clears block_kind/note, and
        sets orchestration_step_count=new_count, but ONLY if the row currently
        matches (expected_status, expected_block_kind). Returns True iff the
        transition occurred.

        Why this exists: two workers can pop the same task_id (e.g. a multi-
        child fan-in double-enqueued the parent). Without this CAS, both pass
        the check-then-update at run_step steps 1→3 and both spawn an agent
        subprocess. The conditional WHERE ensures only the first writer wins.
        """
        now = datetime.now(timezone.utc).isoformat()
        if expected_block_kind is None:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = NULL,
                       orchestration_step_count = ?, updated_at = ?
                   WHERE id = ? AND status = ? AND block_kind IS NULL""",
                (TaskStatus.IN_PROGRESS.value, new_count, now,
                 task_id, expected_status.value),
            )
        else:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = NULL,
                       orchestration_step_count = ?, updated_at = ?
                   WHERE id = ? AND status = ? AND block_kind = ?""",
                (TaskStatus.IN_PROGRESS.value, new_count, now,
                 task_id, expected_status.value, expected_block_kind.value),
            )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def increment_revision_count(self, task_id: str) -> None:
        self._conn.execute(
            "UPDATE tasks SET revision_count = revision_count + 1, updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), task_id),
        )
        self._conn.commit()

    @_synchronized
    def next_task_id(self) -> str:
        cursor = self._conn.execute("SELECT COUNT(*) as cnt FROM tasks")
        count = cursor.fetchone()["cnt"]
        return f"TASK-{count + 1:03d}"

    @_synchronized
    def get_nonterminal_task_ids(self) -> list[str]:
        nonterminal = (
            TaskStatus.PENDING.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.BLOCKED.value,
        )
        cursor = self._conn.execute(
            f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(nonterminal))})",
            nonterminal,
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def list_blocked_with_kind(self, kind) -> list[str]:
        """Return IDs of tasks in status=blocked with the given block_kind."""
        kind_value = kind.value if hasattr(kind, "value") else kind
        cursor = self._conn.execute(
            "SELECT id FROM tasks WHERE status = 'blocked' AND block_kind = ?",
            (kind_value,),
        )
        return [row["id"] for row in cursor.fetchall()]

    # --- Audit Log ---

    @_synchronized
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

    @_synchronized
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

    @_synchronized
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
        decision_json: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO task_results
               (task_id, agent, session_id, status, output_summary, decision_json,
                confidence_score, learnings, risks_flagged, duration_seconds,
                token_count, estimated_cost, artifact_dir, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                agent,
                session_id,
                status,
                output_summary,
                decision_json,
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

    @_synchronized
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

    @_synchronized
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

    @_synchronized
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

    @_synchronized
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

    @_synchronized
    def get_scorecard(self, agent: str) -> dict | None:
        cursor = self._conn.execute("SELECT * FROM scorecards WHERE agent = ?", (agent,))
        row = cursor.fetchone()
        return dict(row) if row else None

    # --- Agent Enrollments ---

    @_synchronized
    def insert_enrollment(
        self,
        name: str,
        description: str,
        system_prompt: str,
        repos: dict[str, str] | None = None,
        executor: str | None = None,
        status: str = "pending",
        allow_rules: list[str] | None = None,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT INTO agent_enrollments (name, description, system_prompt, repos, executor, allow_rules, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (name, description, system_prompt, json.dumps(repos or {}), executor or "claude",
             json.dumps(allow_rules or []), status, now, now),
        )
        self._conn.commit()

    @_synchronized
    def get_enrollment(self, name: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM agent_enrollments WHERE name = ?", (name,),
        ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["allow_rules"] = json.loads(d.get("allow_rules") or "[]")
        return d

    @_synchronized
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

    @_synchronized
    def update_enrollment_status(self, name: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE agent_enrollments SET status = ?, updated_at = ? WHERE name = ?",
            (status, now, name),
        )
        self._conn.commit()

    @_synchronized
    def update_enrollment_fields(
        self,
        name: str,
        description: str | None = None,
        system_prompt: str | None = None,
        repos: dict[str, str] | None = None,
        executor: str | None = None,
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
        if executor is not None:
            updates.append("executor = ?")
            params.append(executor)
        params.append(name)
        self._conn.execute(
            f"UPDATE agent_enrollments SET {', '.join(updates)} WHERE name = ?",
            params,
        )
        self._conn.commit()

    @_synchronized
    def delete_enrollment(self, name: str) -> None:
        self._conn.execute("DELETE FROM agent_enrollments WHERE name = ?", (name,))
        self._conn.commit()

    # --- Talks ---

    @_synchronized
    def next_talk_id(self) -> str:
        """Return the next available TALK-NNN id.

        Callers must hold DaemonState.db_lock across the next_talk_id() +
        insert_talk() pair to avoid duplicate IDs under concurrent requests
        (same requirement as next_task_id).
        """
        cursor = self._conn.execute("SELECT COUNT(*) as cnt FROM talks")
        count = cursor.fetchone()["cnt"]
        return f"TALK-{count + 1:03d}"

    @_synchronized
    def insert_talk(self, talk: TalkRecord) -> None:
        self._conn.execute(
            """INSERT INTO talks (id, agent_name, started_at, ended_at, status,
               summary, topic_list_json, new_learnings_count, new_kb_slugs_json,
               transcript_path)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                talk.id,
                talk.agent_name,
                talk.started_at.isoformat(),
                talk.ended_at.isoformat() if talk.ended_at else None,
                talk.status.value if hasattr(talk.status, "value") else talk.status,
                talk.summary,
                # Empty lists serialize to NULL; read back as [].
                json.dumps(talk.topic_list) if talk.topic_list else None,
                talk.new_learnings_count,
                json.dumps(talk.new_kb_slugs) if talk.new_kb_slugs else None,
                talk.transcript_path,
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_talk(self, talk_id: str) -> TalkRecord | None:
        cursor = self._conn.execute("SELECT * FROM talks WHERE id = ?", (talk_id,))
        row = cursor.fetchone()
        if row is None:
            return None
        return TalkRecord(
            id=row["id"],
            agent_name=row["agent_name"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            status=row["status"],
            summary=row["summary"],
            # Empty lists serialize to NULL; read back as [].
            topic_list=json.loads(row["topic_list_json"]) if row["topic_list_json"] else [],
            new_learnings_count=row["new_learnings_count"],
            new_kb_slugs=json.loads(row["new_kb_slugs_json"]) if row["new_kb_slugs_json"] else [],
            transcript_path=row["transcript_path"],
        )

    @_synchronized
    def update_talk(self, talk_id: str, **fields: object) -> None:
        """Update talk fields. Unknown keys are silently ignored (intentional — lets
        callers forward dict payloads without crashing on extras). Auto-stamps
        `ended_at` when status transitions to closed/abandoned and the caller did
        not explicitly supply ended_at.

        Callers must hold DaemonState.db_lock when combining with other writes.
        """
        allowed = {
            "status", "summary", "topic_list", "new_learnings_count",
            "new_kb_slugs", "transcript_path", "ended_at",
        }
        updates: dict[str, object] = {}
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k == "status":
                updates[k] = v.value if hasattr(v, "value") else v
            elif k == "topic_list":
                updates["topic_list_json"] = json.dumps(v) if v else None
            elif k == "new_kb_slugs":
                updates["new_kb_slugs_json"] = json.dumps(v) if v else None
            else:
                updates[k] = v
        # Auto-stamp ended_at on terminal transitions if caller didn't specify.
        if updates.get("status") in ("closed", "abandoned") and "ended_at" not in updates:
            updates["ended_at"] = datetime.now(timezone.utc).isoformat()
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [talk_id]
        self._conn.execute(f"UPDATE talks SET {set_clause} WHERE id = ?", values)
        self._conn.commit()

    @_synchronized
    def list_talks(
        self,
        agent: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[TalkRecord]:
        """List talks newest-first. Hard cap at 500 to protect the route layer."""
        limit = min(max(limit, 1), 500)
        clauses: list[str] = []
        params: list[object] = []
        if agent is not None:
            clauses.append("agent_name = ?")
            params.append(agent)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        cursor = self._conn.execute(
            f"SELECT * FROM talks {where} ORDER BY started_at DESC LIMIT ?",
            tuple(params),
        )
        return [
            TalkRecord(
                id=r["id"],
                agent_name=r["agent_name"],
                started_at=r["started_at"],
                ended_at=r["ended_at"],
                status=r["status"],
                summary=r["summary"],
                topic_list=json.loads(r["topic_list_json"]) if r["topic_list_json"] else [],
                new_learnings_count=r["new_learnings_count"],
                new_kb_slugs=json.loads(r["new_kb_slugs_json"]) if r["new_kb_slugs_json"] else [],
                transcript_path=r["transcript_path"],
            )
            for r in cursor.fetchall()
        ]

    def list_open_talks_for_agent(self, agent: str) -> list[TalkRecord]:
        return self.list_talks(agent=agent, status="open", limit=500)

    def last_closed_talk_for_agent(self, agent: str) -> TalkRecord | None:
        rows = self.list_talks(agent=agent, status="closed", limit=1)
        return rows[0] if rows else None

    @_synchronized
    def close(self) -> None:
        self._conn.close()
