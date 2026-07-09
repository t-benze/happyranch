from __future__ import annotations

import functools
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from runtime.models import (
    BlockKind,
    DreamKbCandidate,
    DreamRecord,
    DreamStatus,
    TaskRecord,
    TaskStatus,
    ThreadAttachment,
    ThreadInvocation,
    ThreadInvocationPurpose,
    ThreadInvocationStatus,
    ThreadMessage,
    ThreadMessageKind,
    ThreadParticipant,
    ThreadRecord,
    ThreadScopedAttachment,
    ThreadStatus,
    TokenUsage,
)
from runtime.infrastructure.work_hours_store import WorkHoursStore


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


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


def _rebuild_indexes_for(
    table: str,
    conn: sqlite3.Connection,
    statements: list[tuple[str, list]],
    dropped_col: str | None = None,
) -> None:
    """Append CREATE INDEX statements for *table* after a table-rebuild.

    Called during the old-SQLite fallback path of the talk-removal migration.
    The rebuild drops all indexes on the original table; this helper re-creates
    them by reading sqlite_master. When *dropped_col* is set, skip any index
    whose SQL references it (the index is already dropped in step 1 of the
    migration).
    """
    rows = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=? AND sql IS NOT NULL",
        (table,),
    ).fetchall()
    for (sql,) in rows:
        # Keep CREATE UNIQUE INDEX / CREATE INDEX as-is.
        if not sql.upper().startswith("CREATE "):
            continue
        if dropped_col and dropped_col in sql:
            continue
        statements.append((sql, []))


# ── Keyset cursor helpers for audit-log pagination ────────────────────────

import base64 as _base64


def _encode_cursor(timestamp: str, row_id: int) -> str:
    """Encode (timestamp, id) into an opaque base64 cursor string."""
    raw = f"{timestamp}|{row_id}"
    return _base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[str, int]:
    """Decode an opaque cursor string back to (timestamp, id).

    Raises ``ValueError`` on malformed cursors so callers can reject them
    cleanly (422 at the HTTP layer).
    """
    try:
        raw = _base64.urlsafe_b64decode(cursor.encode()).decode()
        ts, id_str = raw.rsplit("|", 1)
        return ts, int(id_str)
    except Exception:
        raise ValueError(f"Invalid cursor: {cursor!r}")


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
        self._migrate_jobs_table_if_needed()
        self._migrate_drop_talk_surface_if_needed()
        self._create_tables()
        # Working-hours CRUD lives in its own module but shares THIS connection
        # and lock so the single-connection serialization invariant (see
        # `_synchronized`) is preserved across both surfaces.
        self.work_hours = WorkHoursStore(self._conn, self._lock)

    @property
    def path(self) -> Path:
        """Alias for ``db_path``. Convenience for callers that prefer ``.path``."""
        return self.db_path

    def _migrate_jobs_table_if_needed(self) -> None:
        """Rename legacy ``script_requests`` table to ``jobs`` and ripple the
        rename through audit_log + escalation_notifications.

        Idempotent: if ``jobs`` already exists OR ``script_requests`` does not
        exist, this is a no-op. Must run BEFORE ``_create_tables`` so the
        ``CREATE TABLE IF NOT EXISTS jobs`` below becomes a no-op on an
        already-migrated DB.

        See spec docs/superpowers/specs/2026-05-26-jobs-design.md §6.2.
        """
        # `executescript` does not return rows, so use a plain execute+fetchall
        # to inspect the schema first.
        existing = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name IN ('script_requests', 'jobs')"
            ).fetchall()
        }
        if "script_requests" not in existing or "jobs" in existing:
            return

        # Drive the migration as one explicit transaction. `executescript`
        # would issue an implicit COMMIT at start AND swallow rollback on
        # mid-script failure — leaving the DB half-migrated and the
        # idempotency check above tripping on the next startup (jobs table
        # exists but audit/notifications still reference SR-NNN). Each
        # statement goes through `execute` so any failure raises with the
        # full transaction rolled back.
        # SQLite 3.35+ supports DROP COLUMN; we rely on that for
        # `timeout_seconds`.
        migration_statements = [
            "ALTER TABLE script_requests RENAME TO jobs",

            "ALTER TABLE jobs ADD COLUMN review_required INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN persistent INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE jobs ADD COLUMN max_output_bytes INTEGER NOT NULL DEFAULT 52428800",
            "ALTER TABLE jobs ADD COLUMN stdout_bytes INTEGER",
            "ALTER TABLE jobs ADD COLUMN stderr_bytes INTEGER",
            "ALTER TABLE jobs ADD COLUMN reason TEXT",
            "ALTER TABLE jobs ADD COLUMN max_runtime_seconds INTEGER",

            "UPDATE jobs SET max_runtime_seconds = timeout_seconds"
            " WHERE timeout_seconds IS NOT NULL",
            "ALTER TABLE jobs DROP COLUMN timeout_seconds",

            # Backfill: every legacy script_request was a founder-approved one-shot.
            "UPDATE jobs SET review_required = 1 WHERE review_required = 0",

            # Force-fail orphaned 'running' rows (daemon has clearly exited by now).
            "UPDATE jobs"
            "   SET status = 'failed',"
            "       reason = 'daemon_crash',"
            "       finished_at = COALESCE(finished_at, started_at, created_at)"
            " WHERE status = 'running'",

            # ID rewrite SR-NNN -> JOB-NNN.
            "UPDATE jobs SET id = 'JOB-' || SUBSTR(id, 4) WHERE id LIKE 'SR-%'",

            # File-path rewrite scripts/SR- -> jobs/JOB-.
            "UPDATE jobs"
            "   SET stdout_path = REPLACE(REPLACE(stdout_path, '/scripts/SR-', '/jobs/JOB-'),"
            "                             '/scripts/', '/jobs/')"
            " WHERE stdout_path IS NOT NULL",
            "UPDATE jobs"
            "   SET stderr_path = REPLACE(REPLACE(stderr_path, '/scripts/SR-', '/jobs/JOB-'),"
            "                             '/scripts/', '/jobs/')"
            " WHERE stderr_path IS NOT NULL",

            # Ripple through cross-referencing tables.
            "UPDATE escalation_notifications"
            "   SET task_id = 'JOB-' || SUBSTR(task_id, 4)"
            " WHERE kind = 'script_request' AND task_id LIKE 'SR-%'",
            "UPDATE escalation_notifications"
            "   SET kind = 'job_request'"
            " WHERE kind = 'script_request'",

            # Audit rewrites. NB: real columns are `action` and `payload`
            # (NOT `kind`/`payload_json` — spec §6.2 corrected).
            # task_id values in audit_log never contain SR-NNN — only audit
            # payloads do (via script_id references), so the broad REPLACE
            # on payload below is safe.
            "UPDATE audit_log"
            "   SET action = 'job_' || SUBSTR(action, 8)"
            " WHERE action LIKE 'script_%'",
            "UPDATE audit_log"
            "   SET payload = REPLACE(payload, '\"script_id\"', '\"job_id\"')"
            " WHERE payload LIKE '%\"script_id\"%'",
            "UPDATE audit_log"
            "   SET payload = REPLACE(payload, '\"SR-', '\"JOB-')"
            " WHERE payload LIKE '%\"SR-%'",

            # Rename indexes.
            "DROP INDEX IF EXISTS idx_script_requests_task",
            "DROP INDEX IF EXISTS idx_script_requests_agent",
            "DROP INDEX IF EXISTS idx_script_requests_status",
            "DROP INDEX IF EXISTS idx_script_requests_created_at",
            "CREATE INDEX IF NOT EXISTS jobs_task_id_idx ON jobs(task_id)",
            "CREATE INDEX IF NOT EXISTS jobs_status_idx  ON jobs(status)",
        ]
        try:
            self._conn.execute("BEGIN")
            for stmt in migration_statements:
                self._conn.execute(stmt)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise


    def _migrate_drop_talk_surface_if_needed(self) -> None:
        """Drop the talks table, four talk-reference columns, and five talk indexes.

        Idempotent: inspects PRAGMA table_info and sqlite_master; if the talk
        columns/table are already absent, returns immediately (no-op).

        Wraps every statement in one explicit BEGIN/COMMIT with rollback on
        exception. Uses the version-guarded DROP COLUMN / table-rebuild hybrid
        from the spec (runtime already hard-requires SQLite >= 3.35, so the
        fallback branch is belt-and-suspenders).

        Must run BEFORE ``_create_tables`` so ``CREATE TABLE IF NOT EXISTS``
        becomes a no-op on the already-dropped table/columns.

        Migration ordering (single transaction):
        1. Drop the 5 talk-related indexes.
        2. Drop the 3 columns (tasks/jobs/threads) + table-rebuild fallback.
        3. Reconcile session_token_usage.talk_id (DROP COLUMN or rebuild).
        4. DROP TABLE IF EXISTS talks.
        5. Leave audit_log untouched (talk_* rows preserved per decision #6).
        """
        # Idempotency guard: verify ALL FOUR targets are already gone
        # (talks table + the 3 talk_id columns on tasks/jobs/threads).
        # session_token_usage.talk_id is checked per-column below.
        existing_tables = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "talks" not in existing_tables:
            all_gone = True
            for table, col in (
                ("tasks", "dispatched_from_talk_id"),
                ("jobs", "submitted_from_talk_id"),
                ("threads", "composed_from_talk_id"),
                ("session_token_usage", "talk_id"),
            ):
                tbl_cols = {
                    row["name"]
                    for row in self._conn.execute(
                        f"PRAGMA table_info({table})"
                    ).fetchall()
                }
                if col in tbl_cols:
                    all_gone = False
                    break
            if all_gone:
                return

        sqlite_version = sqlite3.sqlite_version_info
        can_drop_column = sqlite_version >= (3, 35, 0)

        statements: list[tuple[str, list]] = []

        # 1. Drop talk-related indexes.
        for idx in (
            "idx_talks_agent_status",
            "idx_talks_started",
            "idx_tasks_dispatched_from_talk_id",
            "idx_threads_composed_from_talk",
            "idx_session_token_usage_talk",
        ):
            statements.append((f"DROP INDEX IF EXISTS {idx}", []))

        # 2. Drop the three talk-reference columns.
        for table, col in (
            ("tasks", "dispatched_from_talk_id"),
            ("jobs", "submitted_from_talk_id"),
            ("threads", "composed_from_talk_id"),
        ):
            cols = {
                row["name"]
                for row in self._conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
            }
            if col not in cols:
                continue
            if can_drop_column:
                statements.append((f"ALTER TABLE {table} DROP COLUMN {col}", []))
            else:
                # Table-rebuild fallback: explicit CREATE TABLE (
                # full DDL minus the talk column), INSERT SELECT explicit
                # cols, DROP old, RENAME new, recreate indexes.
                info_rows = self._conn.execute(
                    f"PRAGMA table_info({table})"
                ).fetchall()
                keep_cols = [r["name"] for r in info_rows if r["name"] != col]
                col_list = ", ".join(keep_cols)
                # Build the new-column DDL from PRAGMA info.
                col_defs = []
                for r in info_rows:
                    if r["name"] == col:
                        continue
                    cname = r["name"]
                    ctype = r["type"]
                    notnull = r["notnull"]
                    dflt = r["dflt_value"]
                    pk = r["pk"]
                    parts = [cname, ctype]
                    if notnull:
                        parts.append("NOT NULL")
                    if dflt is not None:
                        parts.append(f"DEFAULT {dflt}")
                    col_def = " ".join(parts)
                    # PRIMARY KEY handled separately in the table DDL.
                    col_defs.append(col_def)
                # Collect PK columns.
                pk_cols = [r["name"] for r in info_rows if r["pk"] and r["name"] != col]
                pk_clause = ""
                if pk_cols:
                    pk_clause = f", PRIMARY KEY ({', '.join(pk_cols)})"
                stmt_create = (
                    f"CREATE TABLE {table}_new (\n  "
                    + ",\n  ".join(col_defs)
                    + f"{pk_clause}\n)"
                )
                stmt_insert = (
                    f"INSERT INTO {table}_new ({col_list}) "
                    f"SELECT {col_list} FROM {table}"
                )
                statements.append((stmt_create, []))
                statements.append((stmt_insert, []))
                statements.append((f"DROP TABLE {table}", []))
                statements.append((f"ALTER TABLE {table}_new RENAME TO {table}", []))
                # Recreate indexes lost by the rebuild, skipping talk-column indexes.
                _rebuild_indexes_for(table, self._conn, statements, dropped_col=col)

        # 3. session_token_usage.talk_id.
        stu_cols = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(session_token_usage)"
            ).fetchall()
        }
        if "talk_id" in stu_cols:
            if can_drop_column:
                statements.append(
                    ("ALTER TABLE session_token_usage DROP COLUMN talk_id", [])
                )
            else:
                info_rows = self._conn.execute(
                    "PRAGMA table_info(session_token_usage)"
                ).fetchall()
                keep_cols = [r["name"] for r in info_rows if r["name"] != "talk_id"]
                col_list = ", ".join(keep_cols)
                # Build the new-column DDL from PRAGMA info.
                col_defs = []
                for r in info_rows:
                    if r["name"] == "talk_id":
                        continue
                    cname = r["name"]
                    ctype = r["type"]
                    notnull = r["notnull"]
                    dflt = r["dflt_value"]
                    pk = r["pk"]
                    parts = [cname, ctype]
                    if notnull:
                        parts.append("NOT NULL")
                    if dflt is not None:
                        parts.append(f"DEFAULT {dflt}")
                    col_def = " ".join(parts)
                    col_defs.append(col_def)
                # Collect PK columns.
                pk_cols = [r["name"] for r in info_rows if r["pk"] and r["name"] != "talk_id"]
                pk_clause = ""
                if pk_cols:
                    pk_clause = f", PRIMARY KEY ({', '.join(pk_cols)})"
                stmt_create = (
                    f"CREATE TABLE session_token_usage_new (\n  "
                    + ",\n  ".join(col_defs)
                    + f"{pk_clause}\n)"
                )
                stmt_insert = (
                    f"INSERT INTO session_token_usage_new ({col_list}) "
                    f"SELECT {col_list} FROM session_token_usage"
                )
                statements.append((stmt_create, []))
                statements.append((stmt_insert, []))
                statements.append(("DROP TABLE session_token_usage", []))
                statements.append((
                    "ALTER TABLE session_token_usage_new RENAME TO session_token_usage", []
                ))
                # Recreate indexes lost by the rebuild, skipping talk-column indexes.
                _rebuild_indexes_for(
                    "session_token_usage", self._conn, statements, dropped_col="talk_id"
                )

        # 4. Drop the talks table.
        if "talks" in existing_tables:
            statements.append(("DROP TABLE IF EXISTS talks", []))

        # Execute as one transaction.
        if not statements:
            return
        try:
            self._conn.execute("BEGIN")
            for stmt, params in statements:
                self._conn.execute(stmt, params)
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise


    def _create_tables(self) -> None:
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'pending',
                assigned_agent TEXT,
                team TEXT NOT NULL DEFAULT 'engineering',
                brief TEXT NOT NULL,
                task_type TEXT NOT NULL DEFAULT 'task',
                revision_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                parent_task_id TEXT,
                final_output_summary TEXT,
                final_output_dir TEXT
            );

            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                agent TEXT NOT NULL,
                action TEXT NOT NULL,
                payload TEXT,
                timestamp TEXT NOT NULL
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
                output_dir TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS dreams (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                local_date TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                window_start TEXT,
                window_end TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                summary TEXT,
                transcript_path TEXT,
                new_learnings_count INTEGER NOT NULL DEFAULT 0,
                kb_candidate_count INTEGER NOT NULL DEFAULT 0,
                founder_thread_id TEXT,
                session_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(agent_name, local_date)
            );
            CREATE INDEX IF NOT EXISTS idx_dreams_agent_date
                ON dreams(agent_name, local_date);
            CREATE INDEX IF NOT EXISTS idx_dreams_status
                ON dreams(status);

            CREATE TABLE IF NOT EXISTS dream_kb_candidates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                dream_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                slug TEXT NOT NULL,
                title TEXT NOT NULL,
                topic TEXT NOT NULL,
                rationale TEXT NOT NULL,
                body_markdown TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                promoted_kb_slug TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(dream_id, slug),
                FOREIGN KEY (dream_id) REFERENCES dreams(id)
            );
            CREATE INDEX IF NOT EXISTS idx_dream_candidates_dream
                ON dream_kb_candidates(dream_id);
            CREATE INDEX IF NOT EXISTS idx_dream_candidates_status
                ON dream_kb_candidates(status);

            CREATE TABLE IF NOT EXISTS work_hours (
                id TEXT PRIMARY KEY,
                agent_name TEXT NOT NULL,
                local_date TEXT NOT NULL,
                slot TEXT NOT NULL,
                mode TEXT NOT NULL,
                scheduled_for TEXT NOT NULL,
                started_at TEXT,
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                routine_count INTEGER NOT NULL DEFAULT 0,
                dropped_count INTEGER NOT NULL DEFAULT 0,
                spawned_task_ids TEXT,
                spawned_task_count INTEGER NOT NULL DEFAULT 0,
                summary TEXT,
                transcript_path TEXT,
                session_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(agent_name, local_date, slot)
            );
            CREATE INDEX IF NOT EXISTS idx_work_hours_agent_date
                ON work_hours(agent_name, local_date);
            CREATE INDEX IF NOT EXISTS idx_work_hours_status
                ON work_hours(status);

            CREATE TABLE IF NOT EXISTS session_token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id    TEXT,
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
                scope_type TEXT,
                scope_id TEXT,
                thread_id TEXT,
                invocation_purpose TEXT,
                created_at TEXT NOT NULL,
                UNIQUE (task_id, agent, session_id)
            );

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
                transcript_path TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_threads_status ON threads(status);
            CREATE INDEX IF NOT EXISTS idx_threads_started ON threads(started_at);

            CREATE TABLE IF NOT EXISTS thread_participants (
                thread_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                added_at TEXT NOT NULL,
                added_by TEXT NOT NULL,
                agent_session_id TEXT,
                last_resumed_seq INTEGER NOT NULL DEFAULT 0,
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
                sent_from_task_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_thread_messages_thread_seq
                ON thread_messages(thread_id, seq);

            CREATE TABLE IF NOT EXISTS thread_message_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id TEXT NOT NULL,
                message_seq INTEGER NOT NULL,
                ordinal INTEGER NOT NULL,
                artifact_name TEXT NOT NULL,
                display_name TEXT NOT NULL,
                size_bytes INTEGER,
                content_type TEXT,
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES threads(id),
                UNIQUE(thread_id, message_seq, ordinal)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_message_attachments_message
                ON thread_message_attachments(thread_id, message_seq);

            CREATE TABLE IF NOT EXISTS thread_scoped_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attachment_id TEXT NOT NULL UNIQUE,
                thread_id TEXT NOT NULL,
                display_name TEXT NOT NULL,
                size_bytes INTEGER,
                content_type TEXT,
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (thread_id) REFERENCES threads(id)
            );
            CREATE INDEX IF NOT EXISTS idx_thread_scoped_attachments_thread
                ON thread_scoped_attachments(thread_id);

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

            CREATE TABLE IF NOT EXISTS jobs (
                id                       TEXT PRIMARY KEY,
                task_id                  TEXT NOT NULL,
                agent_name               TEXT NOT NULL,
                title                    TEXT NOT NULL,
                rationale                TEXT,
                script_text              TEXT NOT NULL,
                interpreter              TEXT NOT NULL,
                cwd_hint                 TEXT,
                review_required          INTEGER NOT NULL DEFAULT 0,
                persistent               INTEGER NOT NULL DEFAULT 0,
                max_runtime_seconds      INTEGER,
                max_output_bytes         INTEGER NOT NULL DEFAULT 52428800,
                status                   TEXT NOT NULL DEFAULT 'pending',
                exit_code                INTEGER,
                reason                   TEXT,
                duration_ms              INTEGER,
                stdout_head              TEXT,
                stderr_head              TEXT,
                stdout_path              TEXT,
                stderr_path              TEXT,
                stdout_bytes             INTEGER,
                stderr_bytes             INTEGER,
                cwd_resolved             TEXT,
                started_at               TEXT,
                finished_at              TEXT,
                reviewed_at              TEXT,
                reviewed_by              TEXT,
                reject_reason            TEXT,
                created_at               TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS jobs_task_id_idx ON jobs(task_id);
            CREATE INDEX IF NOT EXISTS jobs_status_idx  ON jobs(status);

            CREATE TABLE IF NOT EXISTS kb_views (
                slug           TEXT PRIMARY KEY,
                view_count     INTEGER NOT NULL DEFAULT 0,
                last_viewed_at TEXT
            );
        """)
        self._migrate_session_token_usage_scope_columns()
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
        # Thread attachment thread_attachment_id column (additive, TASK-1616).
        try:
            self._conn.execute(
                "ALTER TABLE thread_message_attachments ADD COLUMN thread_attachment_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        # NOTE: the for-loop below contains DDL (RENAME COLUMN) that has no
        # explicit commit; the commit() following the UPDATE team='engineering'
        # block durably persists those DDLs. Don't insert returning code between.
        for ddl in (
            "ALTER TABLE tasks ADD COLUMN final_output_summary TEXT",
            # Manager-only structured decision payload (serialized NextStep
            # JSON). NULL for worker rows. Replaces the prose-in-output_summary
            # double-encoding contract — see TASK-071 post-mortem.
            "ALTER TABLE task_results ADD COLUMN decision_json TEXT",
            # crew → team rename (SQLite >= 3.25). Idempotent: fails on
            # DBs where the column is already `team` or already renamed.
            "ALTER TABLE tasks RENAME COLUMN crew TO team",
            # Per-agent output-dir rename (2026-06-02). Idempotent: fails on DBs
            # where the column is already `final_output_dir`/`output_dir` (fresh or
            # already-renamed). See docs/superpowers/plans/2026-06-01-rename-assets-to-artifacts.md.
            "ALTER TABLE tasks RENAME COLUMN final_artifact_dir TO final_output_dir",
            "ALTER TABLE task_results RENAME COLUMN artifact_dir TO output_dir",
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

        # Path-string rewrite: stored relative paths under 'artifacts/' point at the
        # pre-rename per-agent dir. Rewrite to 'output/' so recall resolves correctly.
        # Idempotent: re-running matches no rows once paths have been rewritten.
        try:
            self._conn.execute(
                "UPDATE tasks SET final_output_dir = 'output/' || substr(final_output_dir, length('artifacts/') + 1) "
                "WHERE final_output_dir LIKE 'artifacts/%'"
            )
            self._conn.execute(
                "UPDATE task_results SET output_dir = 'output/' || substr(output_dir, length('artifacts/') + 1) "
                "WHERE output_dir LIKE 'artifacts/%'"
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
            # Liveness heartbeat: queue worker stamps this while a subprocess
            # is alive so `happyranch details` can show progress on long-running
            # tasks. Distinct from updated_at (which advances on any write).
            "ALTER TABLE tasks ADD COLUMN last_heartbeat TEXT",
            # Per-task subprocess timeout override. NULL → resolver falls
            # through to org/config.yaml then Settings default. Founder sets
            # via `happyranch revisit --session-timeout-seconds`; inherited from
            # parent on delegate and from predecessor root on revisit.
            "ALTER TABLE tasks ADD COLUMN session_timeout_seconds INTEGER",
            # Job-blocking link: spec §3.1. JSON array of JOB-NNN IDs that must
            # complete before this task can proceed. NULL means unblocked.
            "ALTER TABLE tasks ADD COLUMN blocked_on_job_ids TEXT",
            # Completion-report job-wait list: JSON array of JOB-NNN IDs the
            # agent asked to block on. Persisted alongside the task_result row
            # so run_step can read it back via _read_completion_from_db.
            "ALTER TABLE task_results ADD COLUMN waiting_on_job_ids TEXT",
            # Worker-reported verdict (free string: APPROVE, PASS, REQUEST_CHANGES,
            # etc.). Used by inline delegation chains to gate auto-advance to the
            # next leg without consuming the manager's orchestration_step_count.
            # NULL for non-chain or non-verdict workers.
            "ALTER TABLE task_results ADD COLUMN verdict TEXT",
            # Thread agent-session resume (issue #53). agent_session_id holds the
            # resumable agent session for this (thread, agent); NULL = none yet /
            # evicted. last_resumed_seq is the highest thread message seq the stored
            # session has been shown — the delta watermark, advanced only on a
            # successful turn.
            "ALTER TABLE thread_participants ADD COLUMN agent_session_id TEXT",
            "ALTER TABLE thread_participants ADD COLUMN last_resumed_seq INTEGER NOT NULL DEFAULT 0",
            # Legacy cleanup: drop the dead `type` column (dropped from the
            # current schema in the Task-4 refactor; never read, only a
            # "general" sentinel was written). Idempotent via the try/except
            # below — DROP of an absent column raises OperationalError.
            "ALTER TABLE tasks DROP COLUMN type",
        ):
            try:
                self._conn.execute(ddl)
            except sqlite3.OperationalError:
                pass
        # task_type column + one-time provenance backfill. Coupled in a single
        # try/except so the backfill UPDATE runs EXACTLY ONCE — when ADD COLUMN
        # succeeds on the first upgrade. On later startups (and on fresh DBs,
        # where CREATE TABLE already defines the column) ADD raises
        # duplicate-column and the whole block is skipped. Existing rows with a
        # parent were spawned from an ongoing task, so under the new model they
        # are subtasks (leaf); roots keep the 'task' default. Without this
        # backfill an in-flight pre-existing child would be mis-typed 'task' and
        # run_step would parse its plain completion as a NextStep decision and
        # escalate. (A task_type='task' row never has a parent, so the predicate
        # is provenance-correct and safe even if it ever re-ran.)
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'task'"
            )
            self._conn.execute(
                "UPDATE tasks SET task_type='subtask' WHERE parent_task_id IS NOT NULL"
            )
        except sqlite3.OperationalError:
            pass
        # Index the reverse lookup (`WHERE revisit_of_task_id = ?`).
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_revisit_of ON tasks(revisit_of_task_id)"
        )
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN dispatched_from_thread_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN active_chain TEXT"
            )
        except sqlite3.OperationalError:
            pass
        try:
            self._conn.execute(
                "ALTER TABLE tasks ADD COLUMN active_fanout TEXT"
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
        # Dream-originated threads: dream attribution marker (design-overhaul A4).
        # Additive nullable; existing rows stay NULL.
        try:
            self._conn.execute(
                "ALTER TABLE threads ADD COLUMN composed_from_dream_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_threads_composed_from_dream "
            "ON threads(composed_from_dream_id) "
            "WHERE composed_from_dream_id IS NOT NULL"
        )
        # Task-session post-to-existing-thread provenance (THR-027): the task id
        # whose live session appended a message via POST /threads/{id}/post-as-agent.
        # Additive nullable; existing rows + founder/compose/reply messages stay
        # NULL. No index — provenance is read by message, never queried by task.
        try:
            self._conn.execute(
                "ALTER TABLE thread_messages ADD COLUMN sent_from_task_id TEXT"
            )
        except sqlite3.OperationalError:
            pass
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
            # --- THR-037 Change B (Path B) live-row migration ---
            # Collapse the surfaced `blocked` vocabulary into the stored model:
            #   blocked(escalated)    → escalated (top-level), block_kind cleared
            #   blocked(delegated)    → in_progress, reason kept in block_kind
            #   blocked(blocked_on_job) → in_progress, reason kept in block_kind
            # Idempotent: each UPDATE's WHERE matches zero rows on re-run.
            # LIVE rows only — historical terminal rows (failed + cancelled_at)
            # are LEFT AS-IS; only new cancellations write status='cancelled'
            # (derivations read cancelled_at, not the status label). Forward-only
            # posture; the reverse migration is published in the Path-B spec
            # (docs/superpowers/specs/2026-06-27-task-status-pathB-stored-design.md).
            # No DDL: neither status nor block_kind has a CHECK constraint, so
            # the new values are application-enum-only.
            self._conn.execute(
                "UPDATE tasks SET status='escalated', block_kind=NULL "
                "WHERE status='blocked' AND block_kind='escalated'"
            )
            self._conn.execute(
                "UPDATE tasks SET status='in_progress' "
                "WHERE status='blocked' AND block_kind='delegated'"
            )
            self._conn.execute(
                "UPDATE tasks SET status='in_progress' "
                "WHERE status='blocked' AND block_kind='blocked_on_job'"
            )
            self._conn.commit()

    def _migrate_session_token_usage_scope_columns(self) -> None:
        """Add scope columns and make task_id nullable for conversation usage."""
        columns = {
            row["name"]: row
            for row in self._conn.execute(
                "PRAGMA table_info(session_token_usage)"
            ).fetchall()
        }
        if columns.get("task_id") and columns["task_id"]["notnull"]:
            self._conn.execute(
                "ALTER TABLE session_token_usage RENAME TO session_token_usage_old"
            )
            self._conn.execute(
                """CREATE TABLE session_token_usage (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id    TEXT,
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
                    scope_type TEXT,
                    scope_id TEXT,
                    thread_id TEXT,
                    invocation_purpose TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE (task_id, agent, session_id)
                )"""
            )
            self._conn.execute(
                """INSERT INTO session_token_usage
                   (id, task_id, agent, session_id, executor, model,
                    input_tokens, output_tokens, cache_read_tokens,
                    cache_creation_tokens, reasoning_tokens, usage_raw_json,
                    scope_type, scope_id, created_at)
                   SELECT id, task_id, agent, session_id, executor, model,
                          input_tokens, output_tokens, cache_read_tokens,
                          cache_creation_tokens, reasoning_tokens, usage_raw_json,
                          'task', task_id, created_at
                     FROM session_token_usage_old"""
            )
            self._conn.execute("DROP TABLE session_token_usage_old")
            columns = {
                row["name"]: row
                for row in self._conn.execute(
                    "PRAGMA table_info(session_token_usage)"
                ).fetchall()
            }

        for name in (
            "scope_type",
            "scope_id",
            "thread_id",
            "invocation_purpose",
        ):
            if name not in columns:
                try:
                    self._conn.execute(
                        f"ALTER TABLE session_token_usage ADD COLUMN {name} TEXT"
                    )
                except sqlite3.OperationalError:
                    pass

        self._conn.execute(
            "UPDATE session_token_usage SET scope_type = 'task' "
            "WHERE scope_type IS NULL"
        )
        self._conn.execute(
            "UPDATE session_token_usage SET scope_id = task_id "
            "WHERE scope_id IS NULL AND task_id IS NOT NULL"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_token_usage_task "
            "ON session_token_usage (task_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_token_usage_agent "
            "ON session_token_usage (agent, created_at)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_token_usage_scope "
            "ON session_token_usage (scope_type, scope_id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_session_token_usage_thread "
            "ON session_token_usage (thread_id) WHERE thread_id IS NOT NULL"
        )
        self._conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_session_token_usage_scope_unique "
            "ON session_token_usage ("
            "COALESCE(scope_type, 'task'), COALESCE(scope_id, task_id), "
            "agent, session_id)"
        )
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
            task.dispatched_from_thread_id,
            task.block_kind.value if task.block_kind else None,
            task.note,
            task.orchestration_step_count,
            task.session_timeout_seconds,
            task.task_type,
            task.active_fanout,
        )
        self._conn.execute(
            """INSERT INTO tasks (id, status, assigned_agent, team, brief,
               revision_count, created_at, updated_at, completed_at, parent_task_id,
               revisit_of_task_id, dispatched_from_thread_id,
               block_kind, note,
               orchestration_step_count, session_timeout_seconds, task_type, active_fanout)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            dispatched_from_thread_id=row["dispatched_from_thread_id"],
            block_kind=row["block_kind"],
            blocked_on_job_ids=row["blocked_on_job_ids"],
            active_chain=row["active_chain"],
            active_fanout=row["active_fanout"],
            note=row["note"],
            orchestration_step_count=row["orchestration_step_count"] or 0,
            final_output_dir=row["final_output_dir"],
            cancelled_at=row["cancelled_at"],
            last_heartbeat=row["last_heartbeat"],
            session_timeout_seconds=row["session_timeout_seconds"],
            task_type=row["task_type"],
        )

    @_synchronized
    def list_tasks(
        self,
        limit: int = 20,
        assigned_agent: str | None = None,
        before_task_id: str | None = None,
        status: TaskStatus | str | None = None,
        block_kind: BlockKind | str | None = None,
        blocked_on_job_id: str | None = None,
    ) -> list[TaskRecord]:
        # Cursor pagination: callers pass the last task_id of the previous page
        # as `before_task_id`; we resolve its created_at and emit the next page
        # using (created_at, id) DESC for a stable tiebreak. `status` and
        # `block_kind` are optional equality filters (read-only backlog queries).
        # `blocked_on_job_id` is a DERIVE filter for the Jobs "if-approved"
        # cascade — finds tasks blocked on a specific job id.
        cursor_created_at: str | None = None
        if before_task_id is not None:
            row = self._conn.execute(
                "SELECT created_at FROM tasks WHERE id = ?", (before_task_id,),
            ).fetchone()
            if row is None:
                return []
            cursor_created_at = row["created_at"]

        # Assemble the WHERE clause dynamically: with four optional filter
        # dimensions (agent, status, block_kind, cursor) an if/elif tree would
        # be 2**4 branches. StrEnum members stringify to their value, so str()
        # accepts both the enum and a raw query-param string.
        conditions: list[str] = []
        params: list = []
        if assigned_agent is not None:
            conditions.append("assigned_agent = ?")
            params.append(assigned_agent)
        if status is not None:
            conditions.append("status = ?")
            params.append(str(status))
        if block_kind is not None:
            conditions.append("block_kind = ?")
            params.append(str(block_kind))
        if blocked_on_job_id is not None:
            # Mirror jobs_runner.py canonic pred: status + block_kind + LIKE.
            # Without the status/block_kind guard a task that was once
            # blocked on JOB-X but is now done/running leaks into the
            # "if approved" cascade. Path B changed the parked carrier
            # from blocked(blocked_on_job) to in_progress(blocked_on_job).
            conditions.append(
                "status = ? AND block_kind = ? AND blocked_on_job_ids LIKE ?"
            )
            params.extend([
                TaskStatus.IN_PROGRESS.value,
                BlockKind.BLOCKED_ON_JOB.value,
                f'%"{blocked_on_job_id}"%',
            ])
        if cursor_created_at is not None:
            conditions.append("(created_at, id) < (?, ?)")
            params.extend([cursor_created_at, before_task_id])
        where = f"WHERE {' AND '.join(conditions)} " if conditions else ""
        params.append(limit)
        cursor = self._conn.execute(
            f"SELECT * FROM tasks {where}"
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
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
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                blocked_on_job_ids=row["blocked_on_job_ids"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_output_dir=row["final_output_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
                task_type=row["task_type"],
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
    def get_descendant_task_ids(self, root_task_id: str) -> list[str]:
        """Return all descendant task IDs in the parent_task_id subtree
        (direct children, grandchildren, etc.). Excludes the root itself.

        Uses the same iterative get_children() walk as get_subtree_statuses().
        """
        ids: list[str] = []
        stack = list(self.get_children(root_task_id))
        while stack:
            child_id = stack.pop()
            ids.append(child_id)
            stack.extend(self.get_children(child_id))
        return ids

    # Severity ranking for subtree rollup: lower = worse.
    # escalated is the attention-grabbing worst (genuine founder attention);
    # resolved_superseded is the calmest. Under the Path-B stored model
    # (THR-037 Change B) a delegating/parked parent is in_progress (rank 2),
    # so a healthy delegating parent NO LONGER dominates its subtree to amber —
    # only a real escalated (0) or failed (1) descendant pulls the rollup up.
    # cancelled is a deliberate terminal stop with no pending work, so it ranks
    # calmer than completed. The deprecated 'blocked' value is intentionally
    # absent: any lingering blocked row falls to the default rank (99, calmest).
    _SEVERITY_RANK: dict[str, int] = {
        "escalated": 0,
        "failed": 1,
        "in_progress": 2,
        "pending": 3,
        "completed": 4,
        "cancelled": 5,
        "resolved_superseded": 6,
    }

    @_synchronized
    def get_subtree_statuses(self, root_task_id: str) -> list[str]:
        """Return the status values of all descendant tasks in the
        parent_task_id subtree (direct children, grandchildren, etc.).

        Walks the tree recursively via get_children(). Excludes the root
        task itself; only descendants are collected. An empty list means the
        root has no children (rollup = the root's own status).

        This is a DERIVE — no schema change; uses existing parent_task_id
        and get_children().
        """
        statuses: list[str] = []
        stack = list(self.get_children(root_task_id))
        while stack:
            child_id = stack.pop()
            child = self.get_task(child_id)
            if child is not None:
                statuses.append(child.status.value)
                stack.extend(self.get_children(child_id))
        return statuses

    def _worst_subtree_status(self, root_status: str, child_statuses: list[str]) -> str:
        """Return the worst status among a root's own status and its
        descendants' statuses.

        The rollup of a singleton subtree is the root's own status (P1: no
        guessed severity). Uses _SEVERITY_RANK — lowest rank wins.
        """
        worst = root_status
        worst_rank = self._SEVERITY_RANK.get(worst, 99)
        for s in child_statuses:
            rank = self._SEVERITY_RANK.get(s, 99)
            if rank < worst_rank:
                worst = s
                worst_rank = rank
        return worst

    @_synchronized
    def list_roots(
        self,
        limit: int = 20,
        assigned_agent: str | None = None,
        before_task_id: str | None = None,
        status: TaskStatus | str | None = None,
        block_kind: BlockKind | str | None = None,
    ) -> list[TaskRecord]:
        """Return root tasks (parent_task_id IS NULL) with cursor pagination,
        same filter parameters as list_tasks(), plus a per-root _severity_rollup.

        The _severity_rollup attribute (str) is the worst status among the
        root's own status and its entire parent_task_id subtree. A root
        without children shows its own status. Set as a dynamic attribute on
        the TaskRecord (not a model field — DERIVE, no schema).
        """
        cursor_created_at: str | None = None
        if before_task_id is not None:
            row = self._conn.execute(
                "SELECT created_at FROM tasks WHERE id = ?", (before_task_id,),
            ).fetchone()
            if row is None:
                return []
            cursor_created_at = row["created_at"]

        conditions = ["parent_task_id IS NULL"]
        params: list = []
        if assigned_agent is not None:
            conditions.append("assigned_agent = ?")
            params.append(assigned_agent)
        if status is not None:
            conditions.append("status = ?")
            params.append(str(status))
        if block_kind is not None:
            conditions.append("block_kind = ?")
            params.append(str(block_kind))
        if cursor_created_at is not None:
            conditions.append("(created_at, id) < (?, ?)")
            params.extend([cursor_created_at, before_task_id])
        where = f"WHERE {' AND '.join(conditions)} "
        params.append(limit)
        cursor = self._conn.execute(
            f"SELECT * FROM tasks {where}"
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            tuple(params),
        )
        results: list[TaskRecord] = []
        for row in cursor.fetchall():
            task = TaskRecord(
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
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                blocked_on_job_ids=row["blocked_on_job_ids"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_output_dir=row["final_output_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
                task_type=row["task_type"],
            )
            child_statuses = self.get_subtree_statuses(task.id)
            object.__setattr__(
                task, '_severity_rollup',
                self._worst_subtree_status(task.status.value, child_statuses),
            )
            results.append(task)
        return results

    @_synchronized
    def list_tasks_by_thread(
        self, thread_id: str,
    ) -> list[dict]:
        """Return tasks dispatched from a thread, newest-first.

        Uses the existing idx_tasks_dispatched_from_thread_id partial index.
        Returns lightweight summary dicts with the fields the frontend needs:
        id, status, brief, assigned_agent, created_at, parent_task_id.
        """
        cursor = self._conn.execute(
            "SELECT id, status, brief, assigned_agent, created_at, parent_task_id "
            "FROM tasks WHERE dispatched_from_thread_id = ? "
            "ORDER BY created_at DESC",
            (thread_id,),
        )
        return [
            {
                "id": row["id"],
                "status": row["status"],
                "brief": row["brief"],
                "assigned_agent": row["assigned_agent"],
                "created_at": row["created_at"],
                "parent_task_id": row["parent_task_id"],
            }
            for row in cursor.fetchall()
        ]

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
    def batch_get_direct_revisits(
        self, task_ids: list[str],
    ) -> dict[str, list[str]]:
        """Return direct revisits for multiple task_ids in a single query.

        Avoids the N+1 pattern when a list route needs direct_revisits for
        every returned item. Uses idx_tasks_revisit_of.
        """
        if not task_ids:
            return {}
        placeholders = ','.join(['?'] * len(task_ids))
        cursor = self._conn.execute(
            f"SELECT revisit_of_task_id, id FROM tasks"
            f" WHERE revisit_of_task_id IN ({placeholders})"
            f" ORDER BY created_at",
            tuple(task_ids),
        )
        result: dict[str, list[str]] = {tid: [] for tid in task_ids}
        for row in cursor.fetchall():
            root_id = row["revisit_of_task_id"]
            result.setdefault(root_id, []).append(row["id"])
        return result

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
            "output_dir": task.final_output_dir,
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
                dispatched_from_thread_id=row["dispatched_from_thread_id"],
                block_kind=row["block_kind"],
                blocked_on_job_ids=row["blocked_on_job_ids"],
                note=row["note"],
                orchestration_step_count=row["orchestration_step_count"] or 0,
                final_output_dir=row["final_output_dir"],
                cancelled_at=row["cancelled_at"],
                last_heartbeat=row["last_heartbeat"],
                session_timeout_seconds=row["session_timeout_seconds"],
                task_type=row["task_type"],
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def update_task(self, task_id: str, **fields: object) -> None:
        allowed = {
            "status", "assigned_agent", "revision_count", "completed_at",
            "block_kind", "blocked_on_job_ids", "note", "orchestration_step_count",
            "final_output_dir", "cancelled_at", "last_heartbeat",
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
    def update_task_active_chain(self, task_id: str, active_chain: str | None) -> None:
        """Set or clear tasks.active_chain. Pass None to clear (chain finished,
        aborted, or never declared)."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE tasks SET active_chain = ?, updated_at = ? WHERE id = ?",
            (active_chain, now, task_id),
        )
        self._conn.commit()

    @_synchronized
    def update_task_active_fanout(self, task_id: str, active_fanout: str | None) -> None:
        """Set or clear tasks.active_fanout. Pass None to clear (fan-out join
        claimed or parent terminal)."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE tasks SET active_fanout = ?, updated_at = ? WHERE id = ?",
            (active_fanout, now, task_id),
        )
        self._conn.commit()

    @_synchronized
    def try_delegate_many(
        self, parent_id: str, children: list, *, parent_note: str,
        active_fanout_json: str | None = None,
    ) -> bool:
        """Atomic CAS: insert N child tasks + transition parent to
        IN_PROGRESS(DELEGATED) under a single explicit SQL transaction.

        All child inserts, parent status/block_kind/active_fanout update,
        and note write happen in one transaction. On any exception the
        transaction rolls back — no partial children, no orphan rows.

        Same cancel-race semantics as try_delegate (single-child): if the
        parent is cancelled or already terminal at the time of the guarded
        SELECT, no children are inserted and the parent is not overwritten.

        On True: all children exist and parent has transitioned.
        On False: no DB changes were made.

        Children must already have their IDs allocated (caller calls
        next_task_id() N times before invoking this method).
        """
        cursor = self._conn.execute(
            "SELECT status, cancelled_at FROM tasks WHERE id = ?", (parent_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return False
        if row["cancelled_at"] is not None or row["status"] in (
            "completed", "failed", "resolved_superseded", "cancelled",
        ):
            return False
        now = datetime.now(timezone.utc).isoformat()
        try:
            # One explicit transaction: all child inserts + parent transition.
            self._conn.execute("BEGIN IMMEDIATE")
            for child in children:
                self._conn.execute(
                    """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                       revision_count, created_at, updated_at, completed_at, parent_task_id,
                       revisit_of_task_id, dispatched_from_thread_id,
                       block_kind, note,
                       orchestration_step_count, session_timeout_seconds, task_type, active_fanout)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        child.id,
                        child.status.value,
                        child.assigned_agent,
                        child.team,
                        child.brief,
                        child.revision_count,
                        child.created_at.isoformat(),
                        child.updated_at.isoformat(),
                        child.completed_at.isoformat() if child.completed_at else None,
                        child.parent_task_id,
                        child.revisit_of_task_id,
                        child.dispatched_from_thread_id,
                        child.block_kind.value if child.block_kind else None,
                        child.note,
                        child.orchestration_step_count,
                        child.session_timeout_seconds,
                        child.task_type,
                        child.active_fanout,
                    ),
                )
            self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = ?, note = ?, active_fanout = ?, updated_at = ?
                   WHERE id = ?""",
                (TaskStatus.IN_PROGRESS.value, BlockKind.DELEGATED.value, parent_note,
                 active_fanout_json, now, parent_id),
            )
            self._conn.commit()
            return True
        except Exception:
            self._conn.rollback()
            raise

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
    def try_escalate(self, task_id: str, *, reason: str) -> bool:
        """Atomic CAS: transition task to ESCALATED (Path B top-level status,
        block_kind cleared) only if it isn't cancelled or already terminal.

        Closes the post-_is_already_terminal race in the escalate decision
        branch — the Python-level check + UPDATE pair was non-atomic with the
        cancel route's UPDATE. By gating the transition with a SQL `WHERE
        cancelled_at IS NULL AND status NOT IN (...)` predicate under the
        Database RLock (same lock the cancel route's update_task uses), the
        operation serializes against cancel: either cancel ran first and we
        see cancelled_at != NULL → bail, or we ran first and cancel observes
        escalated → transitions cleanly to FAILED on its own.

        Returns True iff the row transitioned.

        See docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.3
        (Codex review of PR #34 surfaced the residual race).
        """
        now = datetime.now(timezone.utc).isoformat()
        cursor = self._conn.execute(
            """UPDATE tasks
               SET status = ?, block_kind = NULL, note = ?, updated_at = ?
               WHERE id = ?
                 AND cancelled_at IS NULL
                 AND status NOT IN ('completed', 'failed', 'resolved_superseded', 'cancelled')""",
            (TaskStatus.ESCALATED.value, reason, now, task_id),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def try_escalate_over_budget(
        self,
        task_id: str,
        *,
        expected_status: TaskStatus,
        expected_block_kind: BlockKind | None,
        reason: str,
    ) -> bool:
        """Atomic CAS for the run_step max-steps budget guard.

        Transitions the row to ESCALATED (Path B top-level status, block_kind
        cleared) with note=reason, but ONLY if it still matches
        (expected_status, expected_block_kind) — the eligible pre-state observed
        at run_step step 1. Returns True iff it transitioned.

        Why this exists: the budget guard runs BEFORE try_claim_for_step, so it
        has no upstream CAS. Two duplicate queue deliveries can both read the
        same stale at-cap eligible row and both escalate, double-posting the
        thread `task_escalated` message + TASK_FOLLOWUP invocation. The
        conditional WHERE makes only the first writer win; the loser matches
        zero rows and bails. A /cancel landing in the window also moves the row
        out of the expected pre-state, so the CAS rejects it for free.
        """
        now = datetime.now(timezone.utc).isoformat()
        if expected_block_kind is None:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?, updated_at = ?
                   WHERE id = ? AND status = ? AND block_kind IS NULL""",
                (TaskStatus.ESCALATED.value, reason, now,
                 task_id, expected_status.value),
            )
        else:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?, updated_at = ?
                   WHERE id = ? AND status = ? AND block_kind = ?""",
                (TaskStatus.ESCALATED.value, reason, now,
                 task_id, expected_status.value, expected_block_kind.value),
            )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def try_fail_over_budget(
        self,
        task_id: str,
        *,
        expected_status: TaskStatus,
        expected_block_kind: BlockKind | None,
        note: str,
    ) -> bool:
        """Atomic CAS for the run_step max-steps budget guard — non-root variant.

        Mirror of ``try_escalate_over_budget`` (the root variant), but transitions
        the row to FAILED (block_kind NULL, completed_at set — FAILED is terminal,
        unlike the ESCALATED template) ONLY if it still matches
        (expected_status, expected_block_kind). Returns True iff it transitioned.

        Per THR-033 Change A a NON-root task that hits the step budget must not
        escalate directly to the founder — it fails and hands back to its parent
        (bounded failure-recovery carries it up). The CAS is required for the same
        reason as ``try_escalate_over_budget``: the budget guard runs BEFORE
        try_claim_for_step, so it has no upstream CAS. Two duplicate queue
        deliveries can both read the same stale at-cap eligible row; the
        conditional WHERE makes only the first writer win, so the parent enqueue +
        thread followup fire exactly once. A /cancel landing in the window moves
        the row out of the expected pre-state and the CAS rejects it for free.
        """
        now = datetime.now(timezone.utc).isoformat()
        if expected_block_kind is None:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?,
                       completed_at = ?, updated_at = ?
                   WHERE id = ? AND status = ? AND block_kind IS NULL""",
                (TaskStatus.FAILED.value, note, now, now,
                 task_id, expected_status.value),
            )
        else:
            cursor = self._conn.execute(
                """UPDATE tasks
                   SET status = ?, block_kind = NULL, note = ?,
                       completed_at = ?, updated_at = ?
                   WHERE id = ? AND status = ? AND block_kind = ?""",
                (TaskStatus.FAILED.value, note, now, now,
                 task_id, expected_status.value, expected_block_kind.value),
            )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def try_delegate(
        self, parent_id: str, child: TaskRecord, *, parent_note: str,
    ) -> bool:
        """Atomic CAS: insert child task + transition parent to
        IN_PROGRESS(DELEGATED) (Path B: a parent waiting on its own children is
        in progress, with the waiting reason kept in block_kind), rejecting if
        parent is cancelled or already terminal.

        Closes the spawn-new-work race documented in
        docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.3.
        Atomicity guarantee: both the child INSERT and the parent UPDATE
        happen under a single @_synchronized acquisition (threading.RLock,
        reentrant). The cancel route's update_task also acquires this lock,
        so the only two interleavings are:
        - cancel before us: our SELECT sees cancelled_at != NULL → bail, no writes
        - us before cancel: cancel sees parent in in_progress(delegated), transitions
          to FAILED, and its cascade walks our newly-inserted child for cleanup

        On True: parent has transitioned and child exists.
        On False: no DB changes were made (no orphan child, no parent overwrite).
        """
        cursor = self._conn.execute(
            "SELECT status, cancelled_at FROM tasks WHERE id = ?", (parent_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return False
        if row["cancelled_at"] is not None or row["status"] in (
            "completed", "failed", "resolved_superseded",
        ):
            return False
        # Both writes under same RLock — atomic vs cancel route.
        # insert_task() commits, but the lock is still held until this method
        # returns, so no other writer can interleave between insert and update.
        self.insert_task(child)
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """UPDATE tasks
               SET status = ?, block_kind = ?, note = ?, updated_at = ?
               WHERE id = ?""",
            (TaskStatus.IN_PROGRESS.value, BlockKind.DELEGATED.value, parent_note,
             now, parent_id),
        )
        self._conn.commit()
        return True

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
        # Path B: blocked dropped (no live row is `blocked` after the boot
        # migration); escalated added so the restart sweep visits escalated
        # rows to leave them alone (§B Branch 5). cancelled is terminal →
        # excluded.
        nonterminal = (
            TaskStatus.PENDING.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.ESCALATED.value,
        )
        cursor = self._conn.execute(
            f"SELECT id FROM tasks WHERE status IN ({','.join('?' * len(nonterminal))})",
            nonterminal,
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def list_blocked_with_kind(self, kind) -> list[str]:
        """Return IDs of parked tasks with the given block_kind.

        Queries by in_progress + block_kind — the stored Path-B representation.
        """
        kind_value = kind.value if hasattr(kind, "value") else kind
        cursor = self._conn.execute(
            "SELECT id FROM tasks "
            "WHERE status = 'in_progress' AND block_kind = ?",
            (kind_value,),
        )
        return [row["id"] for row in cursor.fetchall()]

    @_synchronized
    def list_tasks_blocked_on_jobs(self) -> list[str]:
        """Return ids of tasks currently parked waiting on jobs (BLOCKED_ON_JOB).

        Used by startup recovery (spec §5.7) to re-evaluate the predicate after
        `recover_orphaned_running_jobs` force-fails any leftovers.
        """
        rows = self._conn.execute(
            "SELECT id FROM tasks "
            "WHERE status = ? AND block_kind = ?",
            (TaskStatus.IN_PROGRESS.value, BlockKind.BLOCKED_ON_JOB.value),
        ).fetchall()
        return [row["id"] for row in rows]

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
    def fetch_one_readonly(
        self, sql: str, params: tuple = ()
    ) -> "sqlite3.Row | None":
        """Run a read-only SELECT and return the first row or None.

        For use by modules outside ``Database`` (e.g. ``dashboard_summary``)
        that need to issue read aggregations without bypassing ``_lock``.
        Holds the same ``RLock`` as every other public Database method.
        """
        return self._conn.execute(sql, params).fetchone()

    @_synchronized
    def fetch_all_readonly(
        self, sql: str, params: tuple = ()
    ) -> "list[sqlite3.Row]":
        """Run a read-only SELECT and return all rows.

        See ``fetch_one_readonly`` for the threading rationale.
        """
        return self._conn.execute(sql, params).fetchall()

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
        cursor: str | None = None,
    ) -> tuple[list[dict], str | None]:
        """Filtered audit-log query used by the /audit route.

        All filters are optional and AND-composed. ``limit`` returns the most
        recent N rows (ORDER BY timestamp DESC, id DESC) but the result is
        re-sorted ascending so callers still see chronological order.

        Supports KEYSET cursor pagination: pass the ``cursor`` returned by a
        prior call to get the next older page.  The cursor is an opaque string
        encoding the (timestamp, id) of the last row in the prior page.
        ``next_cursor`` is ``None`` exactly when the result set is exhausted.
        """
        import base64

        clauses: list[str] = []
        params: list[object] = []

        # Decode cursor into a keyset filter (rows BEFORE the cursor anchor)
        if cursor is not None:
            cursor_ts, cursor_id = _decode_cursor(cursor)
            clauses.append(
                "(timestamp < ? OR (timestamp = ? AND id < ?))"
            )
            params.extend([cursor_ts, cursor_ts, cursor_id])

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

        # Defensive guard: non-positive limit short-circuits to empty result.
        # Without this, limit=0 returns next_cursor anchored to a row that was
        # never returned, and limit<0 produces an IndexError on result[limit-1].
        if limit is not None and limit <= 0:
            return [], None

        if limit is not None:
            # Fetch limit+1 to detect whether another page exists
            sql = (
                f"SELECT * FROM audit_log {where} "
                f"ORDER BY timestamp DESC, id DESC LIMIT ?"
            )
            params.append(limit + 1)
        else:
            sql = f"SELECT * FROM audit_log {where} ORDER BY timestamp DESC, id DESC"

        db_cursor = self._conn.execute(sql, tuple(params))
        rows = db_cursor.fetchall()

        result: list[dict] = []
        for row in rows:
            d = dict(row)
            if d.get("payload"):
                d["payload"] = json.loads(d["payload"])
            result.append(d)

        next_cursor: str | None = None
        if limit is not None and len(result) > limit:
            # The extra row tells us there is a next page.
            # Encode the (timestamp, id) of the last actual-page row as next_cursor.
            last_of_page = result[limit - 1]
            next_cursor = _encode_cursor(
                last_of_page["timestamp"], last_of_page["id"]
            )
            # Trim to exactly the requested page size
            result = result[:limit]

        # Re-sort ascending so callers see chronological (oldest-first) order.
        result.sort(key=lambda d: d["id"])

        return result, next_cursor

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
        output_dir: str | None = None,
        decision_json: str | None = None,
        waiting_on_job_ids: list[str] | None = None,
        verdict: str | None = None,
    ) -> None:
        self._conn.execute(
            """INSERT INTO task_results
               (task_id, agent, session_id, status, output_summary, decision_json,
                confidence_score, learnings, risks_flagged, duration_seconds,
                token_count, estimated_cost, output_dir, waiting_on_job_ids,
                verdict, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                output_dir,
                json.dumps(waiting_on_job_ids) if waiting_on_job_ids is not None else None,
                verdict,
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
            if d.get("waiting_on_job_ids"):
                d["waiting_on_job_ids"] = json.loads(d["waiting_on_job_ids"])
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
            if d.get("waiting_on_job_ids"):
                d["waiting_on_job_ids"] = json.loads(d["waiting_on_job_ids"])
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
        if d.get("waiting_on_job_ids"):
            d["waiting_on_job_ids"] = json.loads(d["waiting_on_job_ids"])
        return d

    @_synchronized
    def get_latest_completion_report(self, task_id: str):
        """Return the most-recent task_results row for the given task as a
        CompletionReport, or None if no row exists.

        Used by the chain-advance logic in run_step to read the just-completed
        child's verdict without requiring the caller to know agent/session_id.
        """
        from runtime.models import CompletionReport
        row = self._conn.execute(
            "SELECT * FROM task_results WHERE task_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row is None:
            return None
        keys = row.keys()
        return CompletionReport(
            task_id=task_id,
            agent=row["agent"],
            status=row["status"] or "completed",
            confidence=row["confidence_score"] or 0,
            output_summary=row["output_summary"] or "",
            verdict=row["verdict"] if "verdict" in keys else None,
            output_dir=row["output_dir"] if "output_dir" in keys else None,
            risks_flagged=(
                json.loads(row["risks_flagged"])
                if row["risks_flagged"]
                else []
            ),
            waiting_on_job_ids=(
                json.loads(row["waiting_on_job_ids"])
                if "waiting_on_job_ids" in keys and row["waiting_on_job_ids"]
                else []
            ),
        )

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

    # --- KB views ---

    @_synchronized
    def record_kb_view(self, slug: str) -> None:
        """Increment the view counter for a KB entry, stamping last_viewed_at.

        UPSERT: inserts the row at count 1 on first view, otherwise increments
        the existing count. Caller decides *when* to record (agent-CLI reads
        only — see kb-view-tracking-caller-signal). This is a metric write, not
        an audit row; it never routes through audit_log.
        """
        now = _now().isoformat()
        self._conn.execute(
            """INSERT INTO kb_views (slug, view_count, last_viewed_at)
               VALUES (?, 1, ?)
               ON CONFLICT(slug) DO UPDATE SET
                 view_count = view_count + 1,
                 last_viewed_at = excluded.last_viewed_at""",
            (slug, now),
        )
        self._conn.commit()

    @_synchronized
    def kb_view_stats(self) -> list[dict]:
        """Return per-slug view tallies, most-viewed first.

        Ordered by view_count DESC, then last_viewed_at DESC so ties surface
        the most recently read entry first.
        """
        rows = self._conn.execute(
            """SELECT slug, view_count, last_viewed_at
               FROM kb_views
               ORDER BY view_count DESC, last_viewed_at DESC"""
        ).fetchall()
        return [dict(r) for r in rows]

    # --- Thread IDs ---

    @_synchronized
    def next_thread_id(self) -> str:
        """Return the next available THR-NNN id.

        Callers must hold DaemonState.db_lock across the next_thread_id() +
        insert_thread() pair to avoid duplicate IDs under concurrent requests
        (same requirement as next_task_id).
        """
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 5) AS INTEGER)) AS m "
            "FROM threads WHERE id GLOB 'THR-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"THR-{n:03d}"

    @_synchronized
    def next_job_id(self) -> str:
        """Return the next available JOB-NNN id.

        Callers must hold DaemonState.db_lock across the next_job_id()
        + insert_job() pair to avoid duplicate IDs under concurrent
        requests (same requirement as next_task_id / next_thread_id).
        """
        cursor = self._conn.execute(
            "SELECT MAX(CAST(SUBSTR(id, 5) AS INTEGER)) AS m "
            "FROM jobs WHERE id GLOB 'JOB-[0-9]*'"
        )
        n = (cursor.fetchone()["m"] or 0) + 1
        return f"JOB-{n:03d}"

    @_synchronized
    def insert_job(self, r: "JobRecord") -> None:
        self._conn.execute(
            """INSERT INTO jobs (
                id, task_id, agent_name, title, rationale, script_text,
                interpreter, cwd_hint, status, exit_code,
                stdout_head, stderr_head, stdout_path, stderr_path,
                duration_ms, started_at, finished_at,
                reviewed_at, reviewed_by, reject_reason,
                cwd_resolved, max_runtime_seconds, max_output_bytes,
                review_required, persistent, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                r.id, r.task_id, r.agent_name, r.title, r.rationale, r.script_text,
                r.interpreter.value, r.cwd_hint, r.status.value, r.exit_code,
                r.stdout_head, r.stderr_head, r.stdout_path, r.stderr_path,
                r.duration_ms, r.started_at, r.finished_at,
                r.reviewed_at, r.reviewed_by, r.reject_reason,
                r.cwd_resolved, r.max_runtime_seconds, r.max_output_bytes,
                int(r.review_required), int(r.persistent), r.created_at,
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_job(self, job_id: str) -> "JobRecord | None":
        row = self._conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    @staticmethod
    def _row_to_job(row) -> "JobRecord":
        from runtime.models import JobRecord, JobStatus, JobInterpreter
        # ``reason`` may be missing on rows from pre-migration installs that
        # never hit a terminal transition with the new schema — use defensive
        # key access via SQLite's Row mapping interface.
        keys = row.keys() if hasattr(row, "keys") else ()
        reason = row["reason"] if "reason" in keys else None

        return JobRecord(
            id=row["id"],
            task_id=row["task_id"],
            agent_name=row["agent_name"],
            title=row["title"],
            rationale=row["rationale"],
            script_text=row["script_text"],
            interpreter=JobInterpreter(row["interpreter"]),
            cwd_hint=row["cwd_hint"],
            status=JobStatus(row["status"]),
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
            max_runtime_seconds=row["max_runtime_seconds"],
            max_output_bytes=row["max_output_bytes"],
            review_required=bool(row["review_required"]),
            persistent=bool(row["persistent"]),
            reason=reason,
            created_at=row["created_at"],
        )

    @_synchronized
    def get_job_status(self, job_id: str) -> str | None:
        """Return jobs.status for the given job id, or None if not present.

        Used by the blocked-on-job predicate-check in _maybe_resume_blocked_task
        and by run_step_impl's entry-state branch (spec §5.1, §5.4).
        """
        row = self._conn.execute(
            "SELECT status FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return row["status"] if row is not None else None

    @_synchronized
    def get_job_owner_task_id(self, job_id: str) -> str | None:
        """Return jobs.task_id for the given job id, or None if not present.

        Used by the completion-route validation to verify that the agent
        submitting a blocked completion actually owns the referenced jobs.
        """
        row = self._conn.execute(
            "SELECT task_id FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return row["task_id"] if row is not None else None

    @_synchronized
    def list_jobs_db(
        self,
        *,
        status: str | list[str] | None = None,
        agent: str | None = None,
        task_id: str | None = None,
        review_required: bool | None = None,
        persistent: bool | None = None,
        limit: int = 50,
    ) -> list["JobRecord"]:
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
        if review_required is not None:
            clauses.append("review_required = ?")
            params.append(1 if review_required else 0)
        if persistent is not None:
            clauses.append("persistent = ?")
            params.append(1 if persistent else 0)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(int(limit))
        rows = self._conn.execute(
            f"SELECT * FROM jobs {where} "
            f"ORDER BY created_at DESC, id DESC LIMIT ?",
            params,
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    @_synchronized
    def transition_job_to_rejected(
        self, job_id: str, *, reviewer: str, reason: str, reviewed_at: str
    ) -> None:
        cur = self._conn.execute(
            "UPDATE jobs "
            "SET status='rejected', reviewed_by=?, reject_reason=?, reviewed_at=? "
            "WHERE id=? AND status='pending'",
            (reviewer, reason, reviewed_at, job_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"not_pending: job {job_id} cannot be rejected")

    @_synchronized
    def transition_job_to_running(
        self,
        job_id: str,
        *,
        reviewer: str,
        reviewed_at: str,
        started_at: str,
        cwd_resolved: str,
        max_runtime_seconds: int | None,
        stdout_path: str,
        stderr_path: str,
    ) -> None:
        cur = self._conn.execute(
            "UPDATE jobs SET "
            "status='running', reviewed_by=?, reviewed_at=?, started_at=?, "
            "cwd_resolved=?, max_runtime_seconds=?, stdout_path=?, stderr_path=? "
            "WHERE id=? AND status='pending'",
            (reviewer, reviewed_at, started_at, cwd_resolved, max_runtime_seconds,
             stdout_path, stderr_path, job_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"not_pending: job {job_id} cannot transition to running")

    @_synchronized
    def transition_job_to_terminal(
        self,
        job_id: str,
        *,
        status: "JobStatus",
        exit_code: int | None,
        finished_at: str,
        duration_ms: int,
        stdout_head: str | None,
        stderr_head: str | None,
        reason: str | None = None,
    ) -> None:
        if status.value not in ("completed", "failed"):
            raise ValueError(f"invalid terminal status: {status.value}")
        cur = self._conn.execute(
            "UPDATE jobs SET "
            "status=?, exit_code=?, finished_at=?, duration_ms=?, "
            "stdout_head=?, stderr_head=?, reason=? "
            "WHERE id=? AND status='running'",
            (status.value, exit_code, finished_at, duration_ms,
             stdout_head, stderr_head, reason, job_id),
        )
        self._conn.commit()
        if cur.rowcount == 0:
            raise ValueError(f"not_running: job {job_id} cannot transition to terminal")

    @_synchronized
    def recover_orphaned_running_jobs(self, *, now_iso: str) -> list[str]:
        """Force-transition any SR left in 'running' state to 'failed'.

        Called from the daemon FastAPI lifespan on startup. The subprocess
        and its parent daemon process are gone; partial output on disk is
        preserved but the row is marked failed so the founder UI doesn't
        leave them in a permanent running state.
        """
        rows = self._conn.execute(
            "SELECT id FROM jobs WHERE status='running'"
        ).fetchall()
        ids = [r["id"] for r in rows]
        if not ids:
            return []
        self._conn.executemany(
            "UPDATE jobs SET status='failed', reason='daemon_crash', finished_at=?, "
            "duration_ms=COALESCE(duration_ms, 0), "
            "stderr_head=COALESCE(stderr_head, '') || '\n[daemon restart killed run]' "
            "WHERE id=?",
            [(now_iso, job_id) for job_id in ids],
        )
        self._conn.commit()
        return ids

    @_synchronized
    def insert_thread(self, t: ThreadRecord) -> None:
        # Spec §3.1: composed_from_task_id is the sole composer attribution.
        self._conn.execute(
            """INSERT INTO threads (
                id, subject, started_at, archived_at, status,
                forwarded_from_id, forwarded_from_kind,
                turn_cap, turns_used, summary,
                transcript_path,
                composed_by, composed_from_task_id, composed_from_dream_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                t.transcript_path,
                t.composed_by,
                t.composed_from_task_id,
                t.composed_from_dream_id,
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
            transcript_path=row["transcript_path"],
            composed_by=row["composed_by"] if "composed_by" in keys else "founder",
            composed_from_task_id=row["composed_from_task_id"] if "composed_from_task_id" in keys else None,
            composed_from_dream_id=row["composed_from_dream_id"] if "composed_from_dream_id" in keys else None,
            last_speaker=row["last_speaker"] if "last_speaker" in keys else None,
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
        query = (
            "SELECT t.*, "
            "(SELECT tm.speaker FROM thread_messages tm "
            " WHERE tm.thread_id = t.id ORDER BY tm.seq DESC LIMIT 1) AS last_speaker "
            "FROM threads t "
        )
        params: tuple
        if status:
            if status == "archived":
                query += "WHERE t.status = ? ORDER BY COALESCE(t.archived_at, t.started_at) DESC LIMIT ?"
            else:
                query += "WHERE t.status = ? ORDER BY t.started_at DESC LIMIT ?"
            params = (status, limit)
        else:
            query += "ORDER BY t.started_at DESC LIMIT ?"
            params = (limit,)
        cursor = self._conn.execute(query, params)
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
    def remove_thread_participant(
        self, thread_id: str, agent_name: str
    ) -> bool:
        """Hard-delete a participant row. Returns True if a row was deleted."""
        cursor = self._conn.execute(
            "DELETE FROM thread_participants WHERE thread_id = ? AND agent_name = ?",
            (thread_id, agent_name),
        )
        self._conn.commit()
        return cursor.rowcount == 1

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
    def append_thread_message(
        self,
        *,
        thread_id: str,
        speaker: str,
        kind: ThreadMessageKind,
        body_markdown: str | None = None,
        decline_reason: str | None = None,
        system_payload: dict | None = None,
        attachments: list[ThreadAttachment] | None = None,
        sent_from_task_id: str | None = None,
    ) -> int:
        """Append a message and return its allocated seq.

        Atomic against concurrent appends — both the seq allocation and the
        insert happen under the connection's transaction, and the unique
        index on (thread_id, seq) guards against any race.
        """
        try:
            self._conn.execute("BEGIN")
            cursor = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq "
                "FROM thread_messages WHERE thread_id = ?",
                (thread_id,),
            )
            next_seq = cursor.fetchone()["next_seq"]
            self._conn.execute(
                "INSERT INTO thread_messages (thread_id, seq, speaker, kind, "
                "body_markdown, decline_reason, system_payload_json, "
                "sent_from_task_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    thread_id,
                    next_seq,
                    speaker,
                    kind.value,
                    body_markdown,
                    decline_reason,
                    json.dumps(system_payload) if system_payload else None,
                    sent_from_task_id,
                    _now().isoformat(),
                ),
            )
            for ordinal, attachment in enumerate(attachments or []):
                self._conn.execute(
                    "INSERT INTO thread_message_attachments ("
                    "thread_id, message_seq, ordinal, artifact_name, display_name, "
                    "size_bytes, content_type, uploaded_by, created_at, "
                    "thread_attachment_id"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        thread_id,
                        next_seq,
                        ordinal,
                        attachment.artifact_name,
                        attachment.display_name,
                        attachment.size_bytes,
                        attachment.content_type,
                        attachment.uploaded_by,
                        _now().isoformat(),
                        attachment.thread_attachment_id,
                    ),
                )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        return next_seq

    def _attachments_for_messages(
        self, thread_id: str, seqs: list[int]
    ) -> dict[int, list[ThreadAttachment]]:
        if not seqs:
            return {}
        placeholders = ",".join("?" for _ in seqs)
        cursor = self._conn.execute(
            "SELECT * FROM thread_message_attachments "
            f"WHERE thread_id = ? AND message_seq IN ({placeholders}) "
            "ORDER BY message_seq, ordinal",
            (thread_id, *seqs),
        )
        out: dict[int, list[ThreadAttachment]] = {seq: [] for seq in seqs}
        for row in cursor.fetchall():
            out.setdefault(row["message_seq"], []).append(
                ThreadAttachment(
                    artifact_name=row["artifact_name"],
                    display_name=row["display_name"],
                    size_bytes=row["size_bytes"],
                    content_type=row["content_type"],
                    uploaded_by=row["uploaded_by"],
                    thread_attachment_id=row["thread_attachment_id"],
                )
            )
        return out

    @_synchronized
    def list_thread_messages(
        self, thread_id: str, *, since_seq: int = 0, limit: int = 1000
    ) -> list[ThreadMessage]:
        cursor = self._conn.execute(
            "SELECT * FROM thread_messages "
            "WHERE thread_id = ? AND seq > ? ORDER BY seq LIMIT ?",
            (thread_id, since_seq, limit),
        )
        rows = cursor.fetchall()
        attachments_by_seq = self._attachments_for_messages(
            thread_id,
            [r["seq"] for r in rows],
        )
        return [
            ThreadMessage(
                id=r["id"],
                thread_id=r["thread_id"],
                seq=r["seq"],
                speaker=r["speaker"],
                kind=ThreadMessageKind(r["kind"]),
                body_markdown=r["body_markdown"],
                decline_reason=r["decline_reason"],
                system_payload=json.loads(r["system_payload_json"]) if r["system_payload_json"] else None,
                attachments=attachments_by_seq.get(r["seq"], []),
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
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
        attachments_by_seq = self._attachments_for_messages(thread_id, [seq])
        return ThreadMessage(
            id=row["id"],
            thread_id=row["thread_id"],
            seq=row["seq"],
            speaker=row["speaker"],
            kind=ThreadMessageKind(row["kind"]),
            body_markdown=row["body_markdown"],
            decline_reason=row["decline_reason"],
            system_payload=json.loads(row["system_payload_json"]) if row["system_payload_json"] else None,
            attachments=attachments_by_seq.get(seq, []),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # --- Thread-scoped attachments (TASK-1616) ---

    @_synchronized
    def next_thread_attachment_id(self) -> str:
        cursor = self._conn.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM thread_scoped_attachments"
        )
        n = cursor.fetchone()[0]
        return f"att-{n:03d}"

    @_synchronized
    def insert_thread_scoped_attachment(
        self,
        *,
        attachment_id: str,
        thread_id: str,
        display_name: str,
        size_bytes: int | None,
        content_type: str | None,
        uploaded_by: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO thread_scoped_attachments "
            "(attachment_id, thread_id, display_name, size_bytes, "
            "content_type, uploaded_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                attachment_id,
                thread_id,
                display_name,
                size_bytes,
                content_type,
                uploaded_by,
                _now().isoformat(),
            ),
        )
        self._conn.commit()

    @_synchronized
    def get_thread_scoped_attachment(
        self, thread_id: str, attachment_id: str
    ) -> ThreadScopedAttachment | None:
        cursor = self._conn.execute(
            "SELECT * FROM thread_scoped_attachments "
            "WHERE thread_id = ? AND attachment_id = ?",
            (thread_id, attachment_id),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return ThreadScopedAttachment(
            attachment_id=row["attachment_id"],
            thread_id=row["thread_id"],
            display_name=row["display_name"],
            size_bytes=row["size_bytes"],
            content_type=row["content_type"],
            uploaded_by=row["uploaded_by"],
            created_at=row["created_at"],
        )

    @_synchronized
    def list_thread_scoped_attachments(
        self, thread_id: str
    ) -> list[ThreadScopedAttachment]:
        cursor = self._conn.execute(
            "SELECT * FROM thread_scoped_attachments "
            "WHERE thread_id = ? ORDER BY created_at",
            (thread_id,),
        )
        return [
            ThreadScopedAttachment(
                attachment_id=row["attachment_id"],
                thread_id=row["thread_id"],
                display_name=row["display_name"],
                size_bytes=row["size_bytes"],
                content_type=row["content_type"],
                uploaded_by=row["uploaded_by"],
                created_at=row["created_at"],
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def delete_thread_scoped_attachment(
        self, thread_id: str, attachment_id: str
    ) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM thread_scoped_attachments "
            "WHERE thread_id = ? AND attachment_id = ?",
            (thread_id, attachment_id),
        )
        self._conn.commit()
        return cursor.rowcount > 0

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
    def mark_invocation_declined(
        self, token: str, *, decline_reason: str | None = None
    ) -> bool:
        """Set invocation status to 'declined' with an optional reason.

        Returns True if the row was updated (was pending), False otherwise.
        """
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET status = 'declined', "
            "consumed_at = ?, decline_reason = ? "
            "WHERE invocation_token = ? AND status = 'pending'",
            (_now().isoformat(), decline_reason, token),
        )
        self._conn.commit()
        return cursor.rowcount == 1

    @_synchronized
    def decline_pending_invocations_for_agent(
        self, thread_id: str, agent_name: str,
        *, decline_reason: str | None = None,
    ) -> int:
        """Bulk-decline all pending invocations for (thread_id, agent_name).

        Returns the count of rows updated.
        """
        now = _now().isoformat()
        cursor = self._conn.execute(
            "UPDATE thread_invocations SET status = 'declined', "
            "consumed_at = ?, decline_reason = ? "
            "WHERE thread_id = ? AND agent_name = ? AND status = 'pending'",
            (now, decline_reason, thread_id, agent_name),
        )
        self._conn.commit()
        return cursor.rowcount

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
    def list_invocations_for_thread_grouped_by_seq(
        self, thread_id: str
    ) -> dict[int, list[dict[str, object]]]:
        """Return {triggering_seq: [{agent_name, status, consumed_at}, ...]}
        for every REPLY and TASK_FOLLOWUP invocation in this thread.

        Used by GET /threads/{id} to build the per-message responder_status
        strip. Status values are the raw DB values (pending/consumed/declined/
        failed); the route's response builder renames consumed → replied.

        REPLY invocations hang off MESSAGE rows; TASK_FOLLOWUP invocations hang
        off the SYSTEM row (task_completed / task_failed / task_escalated) that
        wakes a thread-dispatched agent (run_step._append_followup_system_and_reinvoke).
        Including TASK_FOLLOWUP lets the in-flight strip surface the woken agent
        on its system row. BOOTSTRAP is deliberately excluded — it has no
        triggering message row to attach a responder strip to.

        Note: ``consumed_at`` is set by both reply (``status='consumed'``) and
        decline (``status='declined'``) paths — the schema has no separate
        ``declined_at`` column. The wire ``responded_at`` field is sourced from
        this single timestamp regardless of which path consumed the invocation.
        """
        rows = self._conn.execute(
            "SELECT triggering_seq, agent_name, status, consumed_at, started_at, "
            "decline_reason "
            "FROM thread_invocations "
            "WHERE thread_id = ? AND purpose IN ('reply', 'task_followup') "
            "ORDER BY triggering_seq, agent_name",
            (thread_id,),
        ).fetchall()
        grouped: dict[int, list[dict[str, object]]] = {}
        for r in rows:
            entry = {
                "agent_name": r["agent_name"],
                "status": r["status"],
                "consumed_at": r["consumed_at"],
                "started_at": r["started_at"],
                "decline_reason": r["decline_reason"],
            }
            grouped.setdefault(r["triggering_seq"], []).append(entry)
        return grouped

    @_synchronized
    def count_pending_turn_obligations(self, thread_id: str) -> int:
        """Count pending invocations that represent future turn obligations.

        REPLY, BOOTSTRAP, TASK_FOLLOWUP count.

        No current callers in production routes — kept as a documented API.
        After the broadcast-only routing change (spec §7, "invite is free"),
        the /invite projection was dropped entirely; /send and /compose use
        a simpler turns_used + 1 projection; the task-followup auto-extend
        path (mint_followup_invocation_with_cap_extend) inlines its own
        pending-count SQL. Unit tests exercise this helper directly.
        """
        counted = (
            ThreadInvocationPurpose.REPLY.value,
            ThreadInvocationPurpose.BOOTSTRAP.value,
            ThreadInvocationPurpose.TASK_FOLLOWUP.value,
        )
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM thread_invocations "
            "WHERE thread_id = ? AND status = ? AND purpose IN ({})".format(
                ",".join("?" * len(counted))
            ),
            (thread_id, ThreadInvocationStatus.PENDING.value, *counted),
        ).fetchone()
        return int(row["n"])

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
        if status is ThreadStatus.ARCHIVED:
            self._conn.execute(
                "UPDATE threads SET status = ?, summary = COALESCE(?, summary), "
                "archived_at = COALESCE(archived_at, ?) WHERE id = ?",
                (status.value, summary, now, thread_id),
            )
        else:
            # OPEN (resume): plain status flip; archived_at + summary preserved as historical record.
            self._conn.execute(
                "UPDATE threads SET status = ? WHERE id = ?",
                (status.value, thread_id),
            )
        self._conn.commit()

    @_synchronized
    def set_thread_transcript_path(
        self, thread_id: str, transcript_path: str,
    ) -> None:
        """Persist the transcript path for an archived thread."""
        self._conn.execute(
            "UPDATE threads SET transcript_path = ? WHERE id = ?",
            (transcript_path, thread_id),
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
    def bump_thread_turn_cap(self, thread_id: str, *, delta: int = 1) -> int:
        """Atomically increment turn_cap by ``delta`` and return the new value.

        Used by the task-followup hook to make room for the system-triggered
        re-invocation when the projected turn count would exceed the current
        cap.  Each bump is audited at the call site via
        log_thread_turn_cap_auto_extended.
        """
        cursor = self._conn.execute(
            "UPDATE threads SET turn_cap = turn_cap + ? WHERE id = ? "
            "RETURNING turn_cap",
            (delta, thread_id),
        )
        row = cursor.fetchone()
        self._conn.commit()
        if row is None:
            raise KeyError(f"thread {thread_id} not found")
        return int(row["turn_cap"])

    @_synchronized
    def mint_followup_invocation_with_cap_extend(
        self,
        thread_id: str,
        *,
        agent_name: str,
        triggering_seq: int,
        cap_delta_if_over: int = 1,
    ) -> "tuple[ThreadInvocation, int | None]":
        """Atomically mint a TASK_FOLLOWUP invocation, auto-extending turn_cap
        by ``cap_delta_if_over`` if the projection (turns_used + pending + 1)
        would exceed the current cap.

        Returns (minted_invocation, new_cap_if_bumped_else_None).

        Closes the TOCTOU race where two concurrent root-task completions on the
        same thread both observe pending=N, both skip the bump, both mint, and
        leave the thread with more counted obligations than turn_cap permits.
        The @_synchronized lock on this method (backed by threading.RLock)
        serializes the read-compare-bump-mint sequence.

        Because Database._lock is an RLock (re-entrant), calling
        self.mint_thread_invocation from within this @_synchronized method is
        safe — the same thread can re-acquire the lock without deadlock.
        """
        # Read thread state under the @_synchronized lock.
        cur = self._conn.execute(
            "SELECT turns_used, turn_cap FROM threads WHERE id = ?",
            (thread_id,),
        )
        row = cur.fetchone()
        if row is None:
            raise KeyError(f"thread {thread_id} not found")
        turns_used = int(row["turns_used"])
        turn_cap = int(row["turn_cap"])

        counted = (
            ThreadInvocationPurpose.REPLY.value,
            ThreadInvocationPurpose.BOOTSTRAP.value,
            ThreadInvocationPurpose.TASK_FOLLOWUP.value,
        )
        cur = self._conn.execute(
            "SELECT COUNT(*) AS n FROM thread_invocations "
            "WHERE thread_id = ? AND status = ? AND purpose IN ({})".format(
                ",".join("?" * len(counted))
            ),
            (thread_id, ThreadInvocationStatus.PENDING.value, *counted),
        )
        pending = int(cur.fetchone()["n"])

        projected = turns_used + pending + 1
        new_cap: int | None = None
        if projected > turn_cap:
            self._conn.execute(
                "UPDATE threads SET turn_cap = turn_cap + ? WHERE id = ?",
                (cap_delta_if_over, thread_id),
            )
            new_cap = turn_cap + cap_delta_if_over

        # Delegate to mint_thread_invocation — safe because RLock is re-entrant.
        inv = self.mint_thread_invocation(
            thread_id=thread_id,
            agent_name=agent_name,
            triggering_seq=triggering_seq,
            purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
        )
        # No separate commit needed: mint_thread_invocation commits inside its
        # own @_synchronized acquisition. The cap UPDATE above is committed by
        # mint_thread_invocation's commit (SQLite commits all pending changes).
        return inv, new_cap

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
        if kind not in ("escalation", "failure", "job_request"):
            raise ValueError(
                f"kind must be 'escalation', 'failure', or 'job_request', got {kind!r}"
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
    def get_latest_notification_for_sr(
        self, job_id: str, *, kind: str,
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
            (job_id, kind),
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
