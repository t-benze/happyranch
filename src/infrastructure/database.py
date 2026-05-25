from __future__ import annotations

import functools
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from src.models import (
    BlockKind,
    TalkRecord,
    TaskRecord,
    TaskStatus,
    ThreadInvocation,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
    ThreadStatus,
    TokenUsage,
)


class LineageTooDeep(Exception):
    """Ancestor walk exceeded the safety bound; indicates data corruption."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


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

    @property
    def path(self) -> Path:
        """Alias for ``db_path``. Convenience for callers that prefer ``.path``."""
        return self.db_path

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

            CREATE TABLE IF NOT EXISTS session_token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    TEXT NOT NULL,
                agent      TEXT NOT NULL,
                session_id TEXT NOT NULL,
                executor   TEXT NOT NULL,
                model      TEXT,
                input_tokens          INTEGER,
                output_tokens         INTEGER,
                cache_read_tokens     INTEGER,
                cache_creation_tokens INTEGER,
                reasoning_tokens      INTEGER,
                usage_raw_json TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (task_id, agent, session_id)
            );
            CREATE INDEX IF NOT EXISTS idx_session_token_usage_task   ON session_token_usage (task_id);
            CREATE INDEX IF NOT EXISTS idx_session_token_usage_agent  ON session_token_usage (agent, created_at);

            CREATE TABLE IF NOT EXISTS escalation_notifications (
                feishu_message_id TEXT PRIMARY KEY,
                org_slug          TEXT NOT NULL,
                task_id           TEXT NOT NULL,
                chat_id           TEXT NOT NULL,
                created_at        TEXT NOT NULL,
                expires_at        TEXT NOT NULL,
                consumed_at       TEXT,
                consumed_by       TEXT,
                kind              TEXT NOT NULL DEFAULT 'escalation'
            );
            CREATE INDEX IF NOT EXISTS idx_escalation_notifications_task
                ON escalation_notifications (task_id);

            CREATE TABLE IF NOT EXISTS processed_event_ids (
                org_slug          TEXT NOT NULL,
                feishu_event_id   TEXT NOT NULL,
                processed_at      TEXT NOT NULL,
                outcome           TEXT NOT NULL,
                reason            TEXT,
                PRIMARY KEY (org_slug, feishu_event_id)
            );

            CREATE TABLE IF NOT EXISTS threads (
                id TEXT PRIMARY KEY,
                subject TEXT NOT NULL,
                started_at TEXT NOT NULL,
                archived_at TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                forwarded_from_id TEXT,
                forwarded_from_kind TEXT,
                turn_cap INTEGER NOT NULL DEFAULT 500,
                turns_used INTEGER NOT NULL DEFAULT 0,
                summary TEXT,
                new_kb_slugs_json TEXT,
                transcript_path TEXT,
                archive_requested_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);
            CREATE INDEX IF NOT EXISTS idx_threads_started ON threads(started_at);

            CREATE TABLE IF NOT EXISTS thread_participants (
                thread_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                added_at TEXT NOT NULL,
                added_by TEXT NOT NULL,
                PRIMARY KEY (thread_id, agent_name),
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_participants_agent
                ON thread_participants(agent_name);

            CREATE TABLE IF NOT EXISTS thread_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                seq INTEGER NOT NULL,
                speaker TEXT NOT NULL,
                kind TEXT NOT NULL,
                body_markdown TEXT,
                addressed_to_json TEXT,
                decline_reason TEXT,
                system_payload_json TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_thread_messages_thread_seq
                ON thread_messages(thread_id, seq);

            CREATE TABLE IF NOT EXISTS thread_invocations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                invocation_token TEXT NOT NULL UNIQUE,
                triggering_seq INTEGER NOT NULL,
                purpose TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                enqueued_at TEXT NOT NULL,
                started_at TEXT,
                consumed_at TEXT,
                session_id TEXT,
                dispatched_task_id TEXT,
                decline_reason TEXT,
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_invocations_token
                ON thread_invocations(invocation_token);
            CREATE INDEX IF NOT EXISTS idx_thread_invocations_thread
                ON thread_invocations(thread_id);
            CREATE INDEX IF NOT EXISTS idx_thread_invocations_pending
                ON thread_invocations(status) WHERE status = 'pending';

            CREATE TABLE IF NOT EXISTS script_requests (
                id                  TEXT PRIMARY KEY,
                task_id             TEXT NOT NULL,
                agent_name          TEXT NOT NULL,
                title               TEXT NOT NULL,
                rationale           TEXT NOT NULL,
                script_text         TEXT NOT NULL,
                interpreter         TEXT NOT NULL,
                cwd_hint            TEXT,
                status              TEXT NOT NULL DEFAULT 'pending',
                exit_code           INTEGER,
                stdout_head         TEXT,
                stderr_head         TEXT,
                stdout_path         TEXT,
                stderr_path         TEXT,
                duration_ms         INTEGER,
                started_at          TEXT,
                finished_at         TEXT,
                reviewed_at         TEXT,
                reviewed_by         TEXT,
                reject_reason       TEXT,
                cwd_resolved        TEXT,
                timeout_seconds     INTEGER NOT NULL DEFAULT 300,
                created_at          TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_script_requests_task        ON script_requests(task_id);
            CREATE INDEX IF NOT EXISTS idx_script_requests_agent       ON script_requests(agent_name);
            CREATE INDEX IF NOT EXISTS idx_script_requests_status      ON script_requests(status);
            CREATE INDEX IF NOT EXISTS idx_script_requests_created_at  ON script_requests(created_at);
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
            # Manager-only structured decision payload (serialized NextStep
            # JSON). NULL for worker rows. Replaces the prose-in-output_summary
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
            # Talk-dispatch link: tasks dispatched from an open agent talk
            # session record the originating TALK id here. NULL for tasks
            # created via `grassland run` or revisit. Most tasks have no talk
            # provenance, so the index below is partial.
            "ALTER TABLE tasks ADD COLUMN dispatched_from_talk_id TEXT",
            # Liveness heartbeat: queue worker stamps this while a subprocess
            # is alive so `grassland details` can show progress on long-running
            # tasks. Distinct from updated_at (which advances on any write).
            "ALTER TABLE tasks ADD COLUMN last_heartbeat TEXT",
            # Per-task subprocess timeout override. NULL → resolver falls
            # through to org/config.yaml then Settings default. Founder sets
            # via `grassland revisit --session-timeout-seconds`; inherited from
            # parent on delegate and from predecessor root on revisit.
            "ALTER TABLE tasks ADD COLUMN session_timeout_seconds INTEGER",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        # Index the reverse lookup (`WHERE revisit_of_task_id = ?`).
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_revisit_of ON tasks(revisit_of_task_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_dispatched_from_talk_id "
            "ON tasks(dispatched_from_talk_id) "
            "WHERE dispatched_from_talk_id IS NOT NULL"
        )
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN dispatched_from_thread_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(
                "ALTER TABLE threads ADD COLUMN new_learnings_total INTEGER NOT NULL DEFAULT 0"
            )
        except sqlite3.OperationalError:
            pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_dispatched_from_thread_id "
            "ON tasks(dispatched_from_thread_id) "
            "WHERE dispatched_from_thread_id IS NOT NULL"
        )
        # Agent-initiated threads: composer attribution + session binding.
        # Sideways refs — NOT walked by walk_ancestors. Mutually exclusive at
        # insert time (daemon enforces); default 'founder' preserves all
        # existing rows on first migration.
        for ddl in (
            "ALTER TABLE threads ADD COLUMN composed_by TEXT NOT NULL DEFAULT 'founder'",
            "ALTER TABLE threads ADD COLUMN composed_from_task_id TEXT",
            "ALTER TABLE threads ADD COLUMN composed_from_talk_id TEXT",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_threads_composed_from_task "
            "ON threads(composed_from_task_id) "
            "WHERE composed_from_task_id IS NOT NULL"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_threads_composed_from_talk "
            "ON threads(composed_from_talk_id) "
            "WHERE composed_from_talk_id IS NOT NULL"
        )
        # kind column for escalation_notifications: 'escalation' (default) or
        # 'failure'. Additive; existing rows keep the default.
        try:
            self._conn.execute(
                "ALTER TABLE escalation_notifications ADD COLUMN kind "
                "TEXT NOT NULL DEFAULT 'escalation'"
            )
        except sqlite3.OperationalError:
            pass

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
            task.dispatched_from_talk_id,
            task.dispatched_from_thread_id,
            task.block_kind.value if task.block_kind else None,
            task.note,
            task.orchestration_step_count,
            task.session_timeout_seconds,
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
                   revisit_of_task_id, dispatched_from_talk_id, dispatched_from_thread_id,
                   block_kind, note,
                   orchestration_step_count, session_timeout_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (params[0], "general") + params[1:],
            )
        else:
            self._conn.execute(
                """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                   revision_count, created_at, updated_at, completed_at, parent_task_id,
                   revisit_of_task_id, dispatched_from_talk_id, dispatched_from_thread_id,
                   block_kind, note,
                   orchestration_step_count, session_timeout_seconds)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            dispatched_from_talk_id=row["dispatched_from_talk_id"],
            dispatched_from_thread_id=row["dispatched_from_thread_id"],
            block_kind=row["block_kind"],
            note=row["note"],
            orchestration_step_count=row["orchestration_step_count"] or 0,
            final_artifact_dir=row["final_artifact_dir"],
            cancelled_at=row["cancelled_at"],
            last_heartbeat=row["last_heartbeat"],
            session_timeout_seconds=row["session_timeout_seconds"],
        )

    @_synchronized
    def list_tasks(
        self,
        limit: int = 20,
        assigned_agent: str | None = None,
    ) -> list[TaskRecord]:
        if assigned_agent is not None:
            cursor = self._conn.execute(
                "SELECT * FROM tasks WHERE assigned_agent = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (assigned_agent, limit),
            )
        else:
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
                dispatched_from_talk_id=row["dispatched_from_talk_id"],
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_artifact_dir=row["final_artifact_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
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
                dispatched_from_talk_id=row["dispatched_from_talk_id"],
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_artifact_dir=row["final_artifact_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def update_task(self, task_id: str, **fields: object) -> None:
        allowed = {
            "status", "assigned_agent", "revision_count", "completed_at",
            "block_kind", "note", "orchestration_step_count",
            "final_artifact_dir", "cancelled_at", "last_heartbeat",
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
        # MAX(numeric_suffix) over TASK-NNN-shaped rows. Robust to gaps and
        # foreign-shape rows that a COUNT(*)-based allocator would mis-count
        # and then collide with on the next insert.
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 6) AS INTEGER)) AS m "
            "FROM tasks WHERE id GLOB 'TASK-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"TASK-{n:03d}"

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

    # --- Session Token Usage ---

    @_synchronized
    def insert_session_token_usage(
        self,
        task_id: str,
        agent: str,
        session_id: str,
        executor: str,
        token_usage: TokenUsage,
    ) -> None:
        """Insert one row per (task, agent, session). INSERT OR IGNORE on the
        UNIQUE (task_id, agent, session_id) key — first write wins."""
        self._conn.execute(
            """INSERT OR IGNORE INTO session_token_usage
               (task_id, agent, session_id, executor, model,
                input_tokens, output_tokens, cache_read_tokens,
                cache_creation_tokens, reasoning_tokens,
                usage_raw_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id, agent, session_id, executor, token_usage.model,
                token_usage.input_tokens, token_usage.output_tokens,
                token_usage.cache_read_tokens, token_usage.cache_creation_tokens,
                token_usage.reasoning_tokens, token_usage.usage_raw_json,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        self._conn.commit()

    @_synchronized
    def list_session_token_usage(
        self,
        task_id: str | None = None,
        agent: str | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        """Return per-session rows, newest first."""
        where: list[str] = []
        params: list[object] = []
        if task_id is not None:
            where.append("task_id = ?")
            params.append(task_id)
        if agent is not None:
            where.append("agent = ?")
            params.append(agent)
        if since is not None:
            where.append("created_at >= ?")
            params.append(since)
        sql = "SELECT * FROM session_token_usage"
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
    ) -> list[dict]:
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
        sql = """SELECT agent,
                        COUNT(*) AS sessions,
                        SUM(input_tokens)          AS input_tokens,
                        SUM(output_tokens)         AS output_tokens,
                        SUM(cache_read_tokens)     AS cache_read_tokens,
                        SUM(cache_creation_tokens) AS cache_creation_tokens,
                        SUM(reasoning_tokens)      AS reasoning_tokens
                 FROM session_token_usage"""
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
    ) -> list[dict]:
        where: list[str] = []
        params: list[object] = []
        if since is not None:
            where.append("created_at >= ?")
            params.append(since)
        if agent is not None:
            where.append("agent = ?")
            params.append(agent)
        if task_id is not None:
            where.append("task_id = ?")
            params.append(task_id)
        sql = """SELECT task_id,
                        COUNT(*) AS sessions,
                        SUM(input_tokens)          AS input_tokens,
                        SUM(output_tokens)         AS output_tokens,
                        SUM(cache_read_tokens)     AS cache_read_tokens,
                        SUM(cache_creation_tokens) AS cache_creation_tokens,
                        SUM(reasoning_tokens)      AS reasoning_tokens
                 FROM session_token_usage"""
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " GROUP BY task_id ORDER BY task_id"
        rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

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

    # deprecated: use prompt_loader.list_agents
    @_synchronized
    def list_approved_agent_names(self) -> list[str]:
        cur = self._conn.execute(
            "SELECT name FROM agent_enrollments WHERE status='approved' ORDER BY name"
        )
        return [r["name"] for r in cur.fetchall()]

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
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 6) AS INTEGER)) AS m "
            "FROM talks WHERE id GLOB 'TALK-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"TALK-{n:03d}"

    @_synchronized
    def next_thread_id(self) -> str:
        """Return the next available THR-NNN id.

        Callers must hold DaemonState.db_lock across the next_thread_id() +
        insert_thread() pair to avoid duplicate IDs under concurrent requests
        (same requirement as next_task_id / next_talk_id).
        """
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 5) AS INTEGER)) AS m "
            "FROM threads WHERE id GLOB 'THR-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"THR-{n:03d}"

    @_synchronized
    def next_script_request_id(self) -> str:
        """Return the next available SR-NNN id.

        Callers must hold DaemonState.db_lock across the next_script_request_id()
        + insert_script_request() pair to avoid duplicate IDs under concurrent
        requests (same requirement as next_task_id / next_talk_id / next_thread_id).
        """
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 4) AS INTEGER)) AS m "
            "FROM script_requests WHERE id GLOB 'SR-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"SR-{n:03d}"

    @_synchronized
    def insert_script_request(self, r: "ScriptRequestRecord") -> None:
        self._conn.execute(
            """INSERT INTO script_requests (
                id, task_id, agent_name, title, rationale, script_text,
                interpreter, cwd_hint, status, exit_code,
                stdout_head, stderr_head, stdout_path, stderr_path,
                duration_ms, started_at, finished_at,
                reviewed_at, reviewed_by, reject_reason,
                cwd_resolved, timeout_seconds, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r.id, r.task_id, r.agent_name, r.title, r.rationale, r.script_text,
                r.interpreter.value, r.cwd_hint, r.status.value, r.exit_code,
                r.stdout_head, r.stderr_head, r.stdout_path, r.stderr_path,
                r.duration_ms, r.started_at, r.finished_at,
                r.reviewed_at, r.reviewed_by, r.reject_reason,
                r.cwd_resolved, r.timeout_seconds, r.created_at,
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_script_request(self, sr_id: str) -> "ScriptRequestRecord | None":
        row = self._conn.execute(
            "SELECT * FROM script_requests WHERE id = ?", (sr_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_script_request(row)

    @staticmethod
    def _row_to_script_request(row) -> "ScriptRequestRecord":
        from src.models import ScriptRequestRecord, ScriptRequestStatus, ScriptInterpreter
        return ScriptRequestRecord(
            id=row["id"],
            task_id=row["task_id"],
            agent_name=row["agent_name"],
            title=row["title"],
            rationale=row["rationale"],
            script_text=row["script_text"],
            interpreter=ScriptInterpreter(row["interpreter"]),
            cwd_hint=row["cwd_hint"],
            status=ScriptRequestStatus(row["status"]),
            exit_code=row["exit_code"],
            stdout_head=row["stdout_head"],
            stderr_head=row["stderr_head"],
            stdout_path=row["stdout_path"],
            stderr_path=row["stderr_path"],
            duration_ms=row["duration_ms"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            reviewed_at=row["reviewed_at"],
            reviewed_by=row["reviewed_by"],
            reject_reason=row["reject_reason"],
            cwd_resolved=row["cwd_resolved"],
            timeout_seconds=row["timeout_seconds"],
            created_at=row["created_at"],
        )

    @_synchronized
    def list_script_requests(
        self,
        *,
        status: str | list[str] | None = None,
        agent: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list["ScriptRequestRecord"]:
        clauses: list[str] = []
        params: list = []
        if status is not None:
            statuses = [status] if isinstance(status, str) else list(status)
            placeholders = ",".join("?" * len(statuses))
            clauses.append(f"status IN ({placeholders})")
            params.extend(statuses)
        if agent is not None:
            clauses.append("agent_name = ?")
            params.append(agent)
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        rows = self._conn.execute(
            f"SELECT * FROM script_requests {where} "
            f"ORDER BY created_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_script_request(r) for r in rows]

    @_synchronized
    def transition_script_to_rejected(
        self, sr_id: str, *, reviewer: str, reason: str, reviewed_at: str
    ) -> None:
        cur = self._conn.execute(
            "UPDATE script_requests "
            "SET status='rejected', reviewed_by=?, reject_reason=?, reviewed_at=? "
            "WHERE id=? AND status='pending'",
            (reviewer, reason, reviewed_at, sr_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"not_pending: SR {sr_id} cannot be rejected")

    @_synchronized
    def transition_script_to_running(
        self,
        sr_id: str,
        *,
        reviewer: str,
        reviewed_at: str,
        started_at: str,
        cwd_resolved: str,
        timeout_seconds: int,
        stdout_path: str,
        stderr_path: str,
    ) -> None:
        cur = self._conn.execute(
            "UPDATE script_requests SET "
            "status='running', reviewed_by=?, reviewed_at=?, started_at=?, "
            "cwd_resolved=?, timeout_seconds=?, stdout_path=?, stderr_path=? "
            "WHERE id=? AND status='pending'",
            (reviewer, reviewed_at, started_at, cwd_resolved, timeout_seconds,
             stdout_path, stderr_path, sr_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"not_pending: SR {sr_id} cannot transition to running")

    @_synchronized
    def transition_script_to_terminal(
        self,
        sr_id: str,
        *,
        status: "ScriptRequestStatus",
        exit_code: int | None,
        finished_at: str,
        duration_ms: int,
        stdout_head: str | None,
        stderr_head: str | None,
    ) -> None:
        if status.value not in ("completed", "failed"):
            raise ValueError(f"invalid terminal status: {status.value}")
        cur = self._conn.execute(
            "UPDATE script_requests SET "
            "status=?, exit_code=?, finished_at=?, duration_ms=?, "
            "stdout_head=?, stderr_head=? "
            "WHERE id=? AND status='running'",
            (status.value, exit_code, finished_at, duration_ms,
             stdout_head, stderr_head, sr_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"not_running: SR {sr_id} cannot transition to terminal")

    @_synchronized
    def recover_orphaned_running_scripts(self, *, now_iso: str) -> list[str]:
        """Force-transition any SR left in 'running' state to 'failed'.

        Called from the daemon FastAPI lifespan on startup. The subprocess
        and its parent daemon process are gone; partial output on disk is
        preserved but the row is marked failed so the founder UI doesn't
        leave them in a permanent running state.
        """
        rows = self._conn.execute(
            "SELECT id FROM script_requests WHERE status='running'"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            return []
        self._conn.executemany(
            "UPDATE script_requests SET status='failed', finished_at=?, "
            "duration_ms=COALESCE(duration_ms, 0), "
            "stderr_head=COALESCE(stderr_head, '') || '\n[daemon restart killed run]' "
            "WHERE id=?",
            [(now_iso, sr_id) for sr_id in ids],
        )
        self._conn.commit()
        return ids

    @_synchronized
    def insert_thread(self, t: ThreadRecord) -> None:
        # Spec §3.1: composed_from_task_id and composed_from_talk_id are
        # mutually exclusive; daemon enforces at insert time.
        if t.composed_from_task_id is not None and t.composed_from_talk_id is not None:
            raise ValueError(
                "composed_from_task_id and composed_from_talk_id are mutually exclusive"
            )
        self._conn.execute(
            """INSERT INTO threads (
                id, subject, started_at, archived_at, status,
                forwarded_from_id, forwarded_from_kind,
                turn_cap, turns_used, summary, new_kb_slugs_json,
                transcript_path, archive_requested_at,
                composed_by, composed_from_task_id, composed_from_talk_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                t.id,
                t.subject,
                t.started_at.isoformat(),
                t.archived_at.isoformat() if t.archived_at else None,
                t.status.value,
                t.forwarded_from_id,
                t.forwarded_from_kind,
                t.turn_cap,
                t.turns_used,
                t.summary,
                json.dumps(t.new_kb_slugs) if t.new_kb_slugs else None,
                t.transcript_path,
                t.archive_requested_at.isoformat() if t.archive_requested_at else None,
                t.composed_by,
                t.composed_from_task_id,
                t.composed_from_talk_id,
            ),
        )
        self._conn.commit()

    def _row_to_thread(self, row) -> ThreadRecord:
        keys = row.keys()
        return ThreadRecord(
            id=row["id"],
            subject=row["subject"],
            status=ThreadStatus(row["status"]),
            started_at=datetime.fromisoformat(row["started_at"]),
            archived_at=datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None,
            forwarded_from_id=row["forwarded_from_id"],
            forwarded_from_kind=row["forwarded_from_kind"],
            turn_cap=row["turn_cap"],
            turns_used=row["turns_used"],
            summary=row["summary"],
            new_kb_slugs=json.loads(row["new_kb_slugs_json"]) if row["new_kb_slugs_json"] else [],
            new_learnings_total=row["new_learnings_total"] if "new_learnings_total" in keys else 0,
            transcript_path=row["transcript_path"],
            archive_requested_at=datetime.fromisoformat(row["archive_requested_at"]) if row["archive_requested_at"] else None,
            composed_by=row["composed_by"] if "composed_by" in keys else "founder",
            composed_from_task_id=row["composed_from_task_id"] if "composed_from_task_id" in keys else None,
            composed_from_talk_id=row["composed_from_talk_id"] if "composed_from_talk_id" in keys else None,
        )

    @_synchronized
    def get_thread(self, thread_id: str) -> ThreadRecord | None:
        cursor = self._conn.execute(
            "SELECT * FROM threads WHERE id = ?", (thread_id,)
        )
        row = cursor.fetchone()
        return self._row_to_thread(row) if row else None

    @_synchronized
    def list_threads(self, *, status: str | None = None, limit: int = 50) -> list[ThreadRecord]:
        if status:
            cursor = self._conn.execute(
                "SELECT * FROM threads WHERE status = ? ORDER BY started_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM threads ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
        return [self._row_to_thread(r) for r in cursor.fetchall()]

    @_synchronized
    def add_thread_participant(
        self, thread_id: str, agent_name: str, *, added_by: str
    ) -> bool:
        """Insert a participant. Returns True if inserted, False if duplicate."""
        try:
            self._conn.execute(
                "INSERT INTO thread_participants (thread_id, agent_name, added_at, added_by) "
                "VALUES (?, ?, ?, ?)",
                (thread_id, agent_name, _now().isoformat(), added_by),
            )
            self._conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    @_synchronized
    def is_thread_participant(self, thread_id: str, agent_name: str) -> bool:
        cursor = self._conn.execute(
            "SELECT 1 FROM thread_participants WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        )
        return cursor.fetchone() is not None

    @_synchronized
    def list_thread_participants(self, thread_id: str) -> list[ThreadParticipant]:
        cursor = self._conn.execute(
            "SELECT thread_id, agent_name, added_at, added_by "
            "FROM thread_participants WHERE thread_id = ? ORDER BY added_at",
            (thread_id,),
        )
        return [
            ThreadParticipant(
                thread_id=r["thread_id"],
                agent_name=r["agent_name"],
                added_at=datetime.fromisoformat(r["added_at"]),
                added_by=r["added_by"],
            )
            for r in cursor.fetchall()
        ]

    @_synchronized
    def append_thread_message(
        self,
        *,
        thread_id: str,
        speaker: str,
        kind: ThreadMessageKind,
        body_markdown: str | None = None,
        addressed_to: list[str] | None = None,
        decline_reason: str | None = None,
        system_payload: dict | None = None,
    ) -> int:
        """Append a message and return its allocated seq.

        Atomic against concurrent appends — both the seq allocation and the
        insert happen under the connection's transaction, and the unique
        index on (thread_id, seq) guards against any race.
        """
        cursor = self._conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
            "FROM thread_messages WHERE thread_id = ?",
            (thread_id,),
        )
        next_seq = cursor.fetchone()["next_seq"]
        self._conn.execute(
            "INSERT INTO thread_messages (thread_id, seq, speaker, kind, "
            "body_markdown, addressed_to_json, decline_reason, system_payload_json, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                thread_id,
                next_seq,
                speaker,
                kind.value,
                body_markdown,
                json.dumps(addressed_to) if addressed_to else None,
                decline_reason,
                json.dumps(system_payload) if system_payload else None,
                _now().isoformat(),
            ),
        )
        self._conn.commit()
        return next_seq

    @_synchronized
    def list_thread_messages(
        self, thread_id: str, *, since_seq: int = 0, limit: int = 1000
    ) -> list[ThreadMessage]:
        cursor = self._conn.execute(
            "SELECT * FROM thread_messages "
            "WHERE thread_id = ? AND seq > ? ORDER BY seq LIMIT ?",
            (thread_id, since_seq, limit),
        )
        return [
            ThreadMessage(
                id=r["id"],
                thread_id=r["thread_id"],
                seq=r["seq"],
                speaker=r["speaker"],
                kind=ThreadMessageKind(r["kind"]),
                body_markdown=r["body_markdown"],
                addressed_to=json.loads(r["addressed_to_json"]) if r["addressed_to_json"] else None,
                decline_reason=r["decline_reason"],
                system_payload=json.loads(r["system_payload_json"]) if r["system_payload_json"] else None,
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in cursor.fetchall()
        ]

    @_synchronized
    def get_thread_message_by_seq(
        self, thread_id: str, seq: int
    ) -> ThreadMessage | None:
        cursor = self._conn.execute(
            "SELECT * FROM thread_messages WHERE thread_id = ? AND seq = ?",
            (thread_id, seq),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return ThreadMessage(
            id=row["id"],
            thread_id=row["thread_id"],
            seq=row["seq"],
            speaker=row["speaker"],
            kind=ThreadMessageKind(row["kind"]),
            body_markdown=row["body_markdown"],
            addressed_to=json.loads(row["addressed_to_json"]) if row["addressed_to_json"] else None,
            decline_reason=row["decline_reason"],
            system_payload=json.loads(row["system_payload_json"]) if row["system_payload_json"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @_synchronized
    def mint_thread_invocation(
        self,
        *,
        thread_id: str,
        agent_name: str,
        triggering_seq: int,
        purpose: ThreadInvocationPurpose,
    ) -> ThreadInvocation:
        import uuid as _uuid
        token = _uuid.uuid4().hex
        now = _now().isoformat()
        cursor = self._conn.execute(
            "INSERT INTO thread_invocations (thread_id, agent_name, "
            "invocation_token, triggering_seq, purpose, status, enqueued_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (thread_id, agent_name, token, triggering_seq, purpose.value, now),
        )
        self._conn.commit()
        return ThreadInvocation(
            id=cursor.lastrowid,
            thread_id=thread_id,
            agent_name=agent_name,
            invocation_token=token,
            triggering_seq=triggering_seq,
            purpose=purpose,
            status=ThreadInvocationStatus.PENDING,
            enqueued_at=datetime.fromisoformat(now),
        )

    def _row_to_invocation(self, row) -> ThreadInvocation:
        return ThreadInvocation(
            id=row["id"],
            thread_id=row["thread_id"],
            agent_name=row["agent_name"],
            invocation_token=row["invocation_token"],
            triggering_seq=row["triggering_seq"],
            purpose=ThreadInvocationPurpose(row["purpose"]),
            status=ThreadInvocationStatus(row["status"]),
            enqueued_at=datetime.fromisoformat(row["enqueued_at"]),
            started_at=datetime.fromisoformat(row["started_at"]) if row["started_at"] else None,
            consumed_at=datetime.fromisoformat(row["consumed_at"]) if row["consumed_at"] else None,
            session_id=row["session_id"],
            dispatched_task_id=row["dispatched_task_id"],
            decline_reason=row["decline_reason"],
        )

    @_synchronized
    def get_pending_invocation(self, token: str) -> ThreadInvocation | None:
        cursor = self._conn.execute(
            "SELECT * FROM thread_invocations "
            "WHERE invocation_token = ? AND status = 'pending'",
            (token,),
        )
        row = cursor.fetchone()
        return self._row_to_invocation(row) if row else None

    @_synchronized
    def get_invocation_any_status(self, token: str) -> ThreadInvocation | None:
        cursor = self._conn.execute(
            "SELECT * FROM thread_invocations WHERE invocation_token = ?",
            (token,),
        )
        row = cursor.fetchone()
        return self._row_to_invocation(row) if row else None

    @_synchronized
    def consume_invocation(self, token: str) -> bool:
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET status = 'consumed', "
            "consumed_at = ? WHERE invocation_token = ? AND status = 'pending'",
            (_now().isoformat(), token),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def record_dispatch_on_invocation(
        self, token: str, *, task_id: str
    ) -> bool:
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET dispatched_task_id = ? "
            "WHERE invocation_token = ? AND status = 'pending' "
            "AND dispatched_task_id IS NULL",
            (task_id, token),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def fail_invocation(
        self, token: str, *, status: ThreadInvocationStatus, decline_reason: str
    ) -> bool:
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET status = ?, decline_reason = ?, "
            "consumed_at = ? WHERE invocation_token = ? AND status = 'pending'",
            (status.value, decline_reason, _now().isoformat(), token),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def stamp_invocation_started(
        self, token: str, *, session_id: str | None
    ) -> None:
        self._conn.execute(
            "UPDATE thread_invocations SET started_at = ?, session_id = ? "
            "WHERE invocation_token = ? AND status = 'pending'",
            (_now().isoformat(), session_id, token),
        )
        self._conn.commit()

    @_synchronized
    def list_thread_invocations(
        self,
        thread_id: str,
        *,
        status: ThreadInvocationStatus | None = None,
    ) -> list[ThreadInvocation]:
        if status is not None:
            cursor = self._conn.execute(
                "SELECT * FROM thread_invocations "
                "WHERE thread_id = ? AND status = ? ORDER BY id",
                (thread_id, status.value),
            )
        else:
            cursor = self._conn.execute(
                "SELECT * FROM thread_invocations WHERE thread_id = ? ORDER BY id",
                (thread_id,),
            )
        return [self._row_to_invocation(r) for r in cursor.fetchall()]

    @_synchronized
    def reap_pending_invocations(
        self,
        thread_id: str,
        *,
        purposes: list[ThreadInvocationPurpose] | None = None,
        decline_reason: str,
    ) -> int:
        now = _now().isoformat()
        if purposes is None:
            cursor = self._conn.execute(
                "UPDATE thread_invocations SET status = 'failed', "
                "decline_reason = ?, consumed_at = ? "
                "WHERE thread_id = ? AND status = 'pending'",
                (decline_reason, now, thread_id),
            )
        else:
            placeholders = ",".join("?" * len(purposes))
            values = [decline_reason, now, thread_id] + [p.value for p in purposes]
            cursor = self._conn.execute(
                f"UPDATE thread_invocations SET status = 'failed', "
                f"decline_reason = ?, consumed_at = ? "
                f"WHERE thread_id = ? AND status = 'pending' "
                f"AND purpose IN ({placeholders})",
                values,
            )
        self._conn.commit()
        return cursor.rowcount

    @_synchronized
    def increment_thread_turns_used(self, thread_id: str, *, by: int = 1) -> None:
        self._conn.execute(
            "UPDATE threads SET turns_used = turns_used + ? WHERE id = ?",
            (by, thread_id),
        )
        self._conn.commit()

    @_synchronized
    def set_thread_status(
        self,
        thread_id: str,
        *,
        status: ThreadStatus,
        summary: str | None = None,
    ) -> None:
        now = _now().isoformat()
        if status is ThreadStatus.ARCHIVING:
            self._conn.execute(
                "UPDATE threads SET status = ?, summary = COALESCE(?, summary), "
                "archive_requested_at = ? WHERE id = ?",
                (status.value, summary, now, thread_id),
            )
        elif status is ThreadStatus.ABANDONED:
            self._conn.execute(
                "UPDATE threads SET status = ?, archived_at = COALESCE(archived_at, ?) "
                "WHERE id = ?",
                (status.value, now, thread_id),
            )
        else:
            self._conn.execute(
                "UPDATE threads SET status = ? WHERE id = ?",
                (status.value, thread_id),
            )
        self._conn.commit()

    @_synchronized
    def finalize_thread_archived(
        self,
        thread_id: str,
        *,
        transcript_path: str,
        new_kb_slugs: list[str],
    ) -> None:
        self._conn.execute(
            "UPDATE threads SET status = 'archived', archived_at = ?, "
            "transcript_path = ?, new_kb_slugs_json = ? WHERE id = ?",
            (
                _now().isoformat(),
                transcript_path,
                json.dumps(new_kb_slugs) if new_kb_slugs else None,
                thread_id,
            ),
        )
        self._conn.commit()

    @_synchronized
    def set_thread_turn_cap(self, thread_id: str, *, new_cap: int) -> None:
        self._conn.execute(
            "UPDATE threads SET turn_cap = ? WHERE id = ?",
            (new_cap, thread_id),
        )
        self._conn.commit()

    @_synchronized
    def add_thread_kb_slug(self, thread_id: str, slug: str) -> None:
        cursor = self._conn.execute(
            "SELECT new_kb_slugs_json FROM threads WHERE id = ?", (thread_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return
        slugs = json.loads(row["new_kb_slugs_json"]) if row["new_kb_slugs_json"] else []
        if slug in slugs:
            return
        slugs.append(slug)
        self._conn.execute(
            "UPDATE threads SET new_kb_slugs_json = ? WHERE id = ?",
            (json.dumps(slugs), thread_id),
        )
        self._conn.commit()

    @_synchronized
    def add_thread_learnings_count(self, thread_id: str, *, count: int) -> None:
        """Increment new_learnings_total on a thread. Called from close-out callback."""
        if count <= 0:
            return
        self._conn.execute(
            "UPDATE threads SET new_learnings_total = new_learnings_total + ? "
            "WHERE id = ?",
            (count, thread_id),
        )
        self._conn.commit()

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

    # --- Escalation Notifications ---

    @_synchronized
    def mint_escalation_notification(
        self,
        feishu_message_id: str,
        org_slug: str,
        task_id: str,
        chat_id: str,
        expires_at: datetime,
        kind: str = "escalation",
    ) -> None:
        if kind not in ("escalation", "failure", "thread_addressed", "script_request"):
            raise ValueError(
                f"kind must be 'escalation', 'failure', 'thread_addressed', "
                f"or 'script_request', got {kind!r}"
            )
        expires_at_str = expires_at.astimezone(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO escalation_notifications
               (feishu_message_id, org_slug, task_id, chat_id,
                created_at, expires_at, consumed_at, consumed_by, kind)
               VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, ?)""",
            (
                feishu_message_id, org_slug, task_id, chat_id,
                datetime.now(timezone.utc).isoformat(),
                expires_at_str,
                kind,
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_escalation_notification(self, feishu_message_id: str) -> dict | None:
        cur = self._conn.execute(
            """SELECT feishu_message_id, org_slug, task_id, chat_id,
                      created_at, expires_at, consumed_at, consumed_by, kind
               FROM escalation_notifications WHERE feishu_message_id = ?""",
            (feishu_message_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    @_synchronized
    def get_open_notification_for_sr(
        self, sr_id: str, *, kind: str,
    ) -> dict | None:
        """Look up the most-recent escalation_notifications row for an SR.

        Used by the terminal-result follow-up: when a Feishu-initiated script run
        finishes, we post a threaded reply to the original push's message_id.
        Returns consumed rows too — the APPROVE reply consumes the row, but the
        parent message_id is still needed to thread the result post.
        """
        cur = self._conn.execute(
            """SELECT feishu_message_id, org_slug, task_id, chat_id,
                      created_at, expires_at, consumed_at, consumed_by, kind
               FROM escalation_notifications
               WHERE task_id = ? AND kind = ?
               ORDER BY created_at DESC LIMIT 1""",
            (sr_id, kind),
        )
        row = cur.fetchone()
        return dict(row) if row is not None else None

    @_synchronized
    def consume_escalation_notification(
        self, feishu_message_id: str, consumed_by: str,
    ) -> bool:
        """Atomically mark a notification consumed. Returns True on first
        consume, False if already consumed or missing."""
        cur = self._conn.execute(
            """UPDATE escalation_notifications
               SET consumed_at = ?, consumed_by = ?
               WHERE feishu_message_id = ? AND consumed_at IS NULL""",
            (datetime.now(timezone.utc).isoformat(), consumed_by, feishu_message_id),
        )
        self._conn.commit()
        return cur.rowcount == 1

    # --- Processed Event Dedup ---

    @_synchronized
    def record_processed_event(
        self,
        org_slug: str,
        feishu_event_id: str,
        outcome: str,
        reason: str | None,
    ) -> bool:
        """INSERT OR IGNORE into the dedup table. Returns True on first insert,
        False on duplicate."""
        cur = self._conn.execute(
            """INSERT OR IGNORE INTO processed_event_ids
               (org_slug, feishu_event_id, processed_at, outcome, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (
                org_slug, feishu_event_id,
                datetime.now(timezone.utc).isoformat(),
                outcome, reason,
            ),
        )
        self._conn.commit()
        return cur.rowcount == 1

    @_synchronized
    def update_processed_event_outcome(
        self,
        org_slug: str,
        feishu_event_id: str,
        outcome: str,
        reason: str | None = None,
    ) -> None:
        """Update the outcome on an existing processed_event_ids row. Used when
        the listener has decided how the event was disposed (consumed/rejected/ignored)."""
        self._conn.execute(
            """UPDATE processed_event_ids
               SET outcome = ?, reason = ?
               WHERE org_slug = ? AND feishu_event_id = ?""",
            (outcome, reason, org_slug, feishu_event_id),
        )
        self._conn.commit()

    @_synchronized
    def list_open_notifications_for_task(self, task_id: str) -> list[dict]:
        """Return un-consumed notification rows for a task. Used by CLI
        resolve-escalation to mark the matching Feishu row consumed."""
        cur = self._conn.execute(
            """SELECT feishu_message_id, org_slug, task_id, chat_id,
                      created_at, expires_at, consumed_at, consumed_by, kind
               FROM escalation_notifications
               WHERE task_id = ? AND consumed_at IS NULL""",
            (task_id,),
        )
        return [dict(row) for row in cur.fetchall()]

    @_synchronized
    def close(self) -> None:
        self._conn.close()
