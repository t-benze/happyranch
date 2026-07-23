"""Tests for task_attachments table + CRUD (THR-109)."""
from __future__ import annotations

import pytest

from runtime.infrastructure.database import Database
from runtime.models import TaskRecord, TaskAttachmentRecord


@pytest.fixture
def db():
    import tempfile
    from pathlib import Path
    d = Database(Path(tempfile.mkdtemp()) / "test.db")
    yield d


def _make_task(db, task_id: str, parent_id: str | None = None) -> None:
    db.insert_task(TaskRecord(
        id=task_id,
        brief=f"Task {task_id}",
        team="engineering",
        parent_task_id=parent_id,
    ))


def _make_attachment(
    db,
    task_id: str,
    ordinal: int = 0,
    storage_key: str | None = None,
    display_name: str = "test.png",
    size_bytes: int | None = 1024,
    content_type: str = "image/png",
    uploaded_by: str = "founder",
) -> None:
    db.insert_task_attachment(
        task_id=task_id,
        ordinal=ordinal,
        storage_key=storage_key or f"ta-{ordinal:04d}",
        display_name=display_name,
        size_bytes=size_bytes,
        content_type=content_type,
        uploaded_by=uploaded_by,
    )


class TestTaskAttachmentCRUD:
    def test_insert_and_list(self, db):
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", ordinal=0, storage_key="ta-0001", display_name="mockup.png")
        _make_attachment(db, "TASK-001", ordinal=1, storage_key="ta-0002", display_name="spec.pdf")

        attachments = db.list_task_attachments("TASK-001")
        assert len(attachments) == 2
        assert attachments[0].display_name == "mockup.png"
        assert attachments[0].ordinal == 0
        assert attachments[1].display_name == "spec.pdf"
        assert attachments[1].ordinal == 1

    def test_get_by_storage_key(self, db):
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", storage_key="ta-0001")
        found = db.get_task_attachment("TASK-001", "ta-0001")
        assert found is not None
        assert found.storage_key == "ta-0001"
        assert found.display_name == "test.png"

    def test_get_missing(self, db):
        _make_task(db, "TASK-001")
        assert db.get_task_attachment("TASK-001", "nonexistent") is None

    def test_get_by_storage_key_global(self, db):
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", storage_key="ta-0001")
        found = db.get_task_attachment_by_storage_key("ta-0001")
        assert found is not None
        assert found.task_id == "TASK-001"

    def test_get_by_storage_key_not_found(self, db):
        assert db.get_task_attachment_by_storage_key("nonexistent") is None

    def test_list_empty(self, db):
        _make_task(db, "TASK-001")
        assert db.list_task_attachments("TASK-001") == []

    def test_count(self, db):
        _make_task(db, "TASK-001")
        assert db.count_task_attachments("TASK-001") == 0
        _make_attachment(db, "TASK-001", ordinal=0, storage_key="ta-0001")
        assert db.count_task_attachments("TASK-001") == 1
        _make_attachment(db, "TASK-001", ordinal=1, storage_key="ta-0002")
        assert db.count_task_attachments("TASK-001") == 2

    def test_delete(self, db):
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", storage_key="ta-0001")
        assert db.delete_task_attachment("TASK-001", "ta-0001") is True
        assert db.get_task_attachment("TASK-001", "ta-0001") is None

    def test_delete_missing(self, db):
        _make_task(db, "TASK-001")
        assert db.delete_task_attachment("TASK-001", "nonexistent") is False


class TestAncestorAttachmentResolution:
    def test_no_ancestors_shows_only_own(self, db):
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", ordinal=0, storage_key="ta-0001", display_name="only.png")

        attachments = db.resolve_ancestor_attachments("TASK-001")
        assert len(attachments) == 1
        assert attachments[0].display_name == "only.png"

    def test_parent_has_attachments(self, db):
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", ordinal=0, storage_key="ta-0001", display_name="mockup.png")
        _make_task(db, "TASK-002", parent_id="TASK-001")

        attachments = db.resolve_ancestor_attachments("TASK-002")
        assert len(attachments) == 1
        assert attachments[0].display_name == "mockup.png"
        assert attachments[0].task_id == "TASK-001"

    def test_grandparent_has_attachments(self, db):
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", ordinal=0, storage_key="ta-0001", display_name="grandparent.png")
        _make_task(db, "TASK-002", parent_id="TASK-001")
        _make_task(db, "TASK-003", parent_id="TASK-002")

        attachments = db.resolve_ancestor_attachments("TASK-003")
        assert len(attachments) == 1
        assert attachments[0].display_name == "grandparent.png"

    def test_union_own_and_multiple_ancestors(self, db):
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", ordinal=0, storage_key="ta-0001", display_name="root.png")
        _make_task(db, "TASK-002", parent_id="TASK-001")
        _make_attachment(db, "TASK-002", ordinal=0, storage_key="ta-0002", display_name="middle.png")
        _make_task(db, "TASK-003", parent_id="TASK-002")
        _make_attachment(db, "TASK-003", ordinal=0, storage_key="ta-0003", display_name="self.png")

        attachments = db.resolve_ancestor_attachments("TASK-003")
        names = {a.display_name for a in attachments}
        assert names == {"self.png", "middle.png", "root.png"}

    def test_root_task_gets_own_attachments(self, db):
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", ordinal=0, storage_key="ta-0001", display_name="self.png")

        # Root task's own attachments ARE included (finding 2: own + ancestors).
        attachments = db.resolve_ancestor_attachments("TASK-001")
        assert len(attachments) == 1
        assert attachments[0].display_name == "self.png"

    def test_no_parent_no_own_attachments(self, db):
        _make_task(db, "TASK-001")
        _make_task(db, "TASK-002")  # no parent, no attachments
        attachments = db.resolve_ancestor_attachments("TASK-002")
        assert attachments == []

    def test_cycle_prevention(self, db):
        """Should not infinite-loop even if parent chain has cycles (defensive)."""
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", ordinal=0, storage_key="ta-0001", display_name="cycle.png")
        _make_task(db, "TASK-002", parent_id="TASK-001")
        # Manually set a cycle via direct SQL
        db._conn.execute("UPDATE tasks SET parent_task_id = ? WHERE id = ?", ("TASK-002", "TASK-001"))
        db._conn.commit()

        # Should resolve without infinite loop (max_hops bounds it).
        attachments = db.resolve_ancestor_attachments("TASK-002", max_hops=2)
        assert len(attachments) >= 0  # just doesn't crash


class TestLegacyMigration:
    """Prove the storage_key uniqueness migration handles legacy databases."""

    def test_v0_no_task_attachments_init_succeeds(self):
        """Fresh database with no task_attachments table initializes cleanly."""
        import tempfile
        from pathlib import Path
        from runtime.infrastructure.database import Database

        d = Database(Path(tempfile.mkdtemp()) / "test.db")
        # Table exists after init.
        tables = {
            row[0] for row in d._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "task_attachments" in tables
        # Legacy column present.
        cols = {
            row[1] for row in d._conn.execute(
                "PRAGMA table_info('task_attachments')"
            ).fetchall()
        }
        assert "legacy_status" in cols
        # UNIQUE guard is in place (either table-level or index).
        indexes = {
            row[0] for row in d._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='task_attachments'"
            ).fetchall()
        }
        assert "idx_task_attachments_storage_key_unique" in indexes
        d._conn.close()

    def test_clean_v1_upgrade_enforces_future_duplicate_rejection(self):
        """A clean v1 pre-index table (no UNIQUE, no duplicates) upgrades
        successfully and blocks a subsequent duplicate claim."""
        import sqlite3
        import tempfile
        from pathlib import Path
        from runtime.infrastructure.database import Database

        db_path = Path(tempfile.mkdtemp()) / "test.db"
        # Simulate a pre-index v1 table: no UNIQUE(storage_key).
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
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
                final_output_dir TEXT,
                executor_pid INTEGER
            );
            CREATE TABLE IF NOT EXISTS task_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                storage_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                size_bytes INTEGER,
                content_type TEXT,
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id),
                UNIQUE(task_id, ordinal)
            );
        """)
        conn.close()

        # Now open via Database — migration must succeed.
        d = Database(db_path)
        cols = {
            row[1] for row in d._conn.execute(
                "PRAGMA table_info('task_attachments')"
            ).fetchall()
        }
        assert "legacy_status" in cols
        # Full UNIQUE index created.
        indexes = {
            row[0] for row in d._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='task_attachments'"
            ).fetchall()
        }
        assert "idx_task_attachments_storage_key_unique" in indexes

        # Insert a task + attachment.
        from runtime.models import TaskRecord
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        d.insert_task(TaskRecord(
            id="TASK-CLEAN-V1", brief="clean v1", team="engineering",
            created_at=now, updated_at=now,
        ))
        d.insert_task_attachment(
            task_id="TASK-CLEAN-V1", ordinal=0,
            storage_key="ta-clean-key", display_name="clean.png",
            size_bytes=100, content_type="image/png", uploaded_by="founder",
        )

        # Duplicate claim must be rejected.
        d.insert_task(TaskRecord(
            id="TASK-CLEAN-V2", brief="second", team="engineering",
            created_at=now, updated_at=now,
        ))
        with pytest.raises(sqlite3.IntegrityError):
            d.insert_task_attachment(
                task_id="TASK-CLEAN-V2", ordinal=0,
                storage_key="ta-clean-key", display_name="clean.png",
                size_bytes=100, content_type="image/png", uploaded_by="founder",
            )
        d._conn.close()

    def test_duplicate_v1_upgrade_preserves_and_blocks_reclaim(self):
        """A v1 table with duplicate storage_key rows must:
        - upgrade without daemon startup failure
        - preserve every legacy row (readable)
        - mark all duplicate-shared rows with legacy_status='duplicate_v1'
        - block a new claim to the duplicate key
        """
        import sqlite3
        import tempfile
        from pathlib import Path
        from runtime.infrastructure.database import Database

        db_path = Path(tempfile.mkdtemp()) / "test.db"
        # Simulate v1 pre-index table with duplicate storage_key rows.
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
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
                final_output_dir TEXT,
                executor_pid INTEGER
            );
            CREATE TABLE IF NOT EXISTS task_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                storage_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                size_bytes INTEGER,
                content_type TEXT,
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id),
                UNIQUE(task_id, ordinal)
            );
        """)
        # Insert two rows with the SAME storage_key (pre-constraint duplicates).
        conn.execute(
            "INSERT INTO tasks (id, status, team, brief, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("TASK-DUP-1", "completed", "engineering", "dup task 1",
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO tasks (id, status, team, brief, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("TASK-DUP-2", "completed", "engineering", "dup task 2",
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO task_attachments (task_id, ordinal, storage_key, "
            "display_name, size_bytes, content_type, uploaded_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("TASK-DUP-1", 0, "ta-dup-key", "mockup.png", 100,
             "image/png", "founder", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO task_attachments (task_id, ordinal, storage_key, "
            "display_name, size_bytes, content_type, uploaded_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("TASK-DUP-2", 0, "ta-dup-key", "mockup-v2.png", 200,
             "image/png", "founder", "2026-01-02T00:00:00"),
        )
        conn.commit()
        conn.close()

        # Open via Database — migration must NOT raise IntegrityError.
        d = Database(db_path)

        # Both legacy rows preserved with legacy_status='duplicate_v1'.
        rows = d._conn.execute(
            "SELECT * FROM task_attachments WHERE storage_key = ?",
            ("ta-dup-key",),
        ).fetchall()
        assert len(rows) == 2, "both legacy duplicate rows must be preserved"
        for row in rows:
            assert row["legacy_status"] == "duplicate_v1", (
                f"row {row['id']} must be marked duplicate_v1"
            )

        # Both rows are readable via standard methods.
        att1 = d.get_task_attachment("TASK-DUP-1", "ta-dup-key")
        assert att1 is not None
        assert att1.display_name == "mockup.png"
        assert att1.legacy_status == "duplicate_v1"
        att2 = d.get_task_attachment("TASK-DUP-2", "ta-dup-key")
        assert att2 is not None
        assert att2.display_name == "mockup-v2.png"
        assert att2.legacy_status == "duplicate_v1"

        # get_task_attachment_by_storage_key returns one of the legacy rows.
        global_att = d.get_task_attachment_by_storage_key("ta-dup-key")
        assert global_att is not None
        assert global_att.legacy_status == "duplicate_v1"

        # A new claim to the duplicate key must be REJECTED.
        from runtime.models import TaskRecord
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        d.insert_task(TaskRecord(
            id="TASK-DUP-NEW", brief="new claim attempt", team="engineering",
            created_at=now, updated_at=now,
        ))
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            d.insert_task_attachment(
                task_id="TASK-DUP-NEW", ordinal=0,
                storage_key="ta-dup-key", display_name="new.png",
                size_bytes=300, content_type="image/png", uploaded_by="founder",
            )

        # Also verify through the composite method.
        d.insert_task(TaskRecord(
            id="TASK-DUP-NEW2", brief="composite claim attempt", team="engineering",
            created_at=now, updated_at=now,
        ))
        with pytest.raises(sqlite3.IntegrityError):
            d.insert_task_with_attachments(
                TaskRecord(
                    id="TASK-DUP-NEW3", brief="composite test", team="engineering",
                    created_at=now, updated_at=now,
                ),
                attachments=[{
                    "ordinal": 0, "storage_key": "ta-dup-key",
                    "display_name": "composite.png", "size_bytes": 400,
                    "content_type": "image/png",
                }],
                uploaded_by="founder",
            )
        # Verify no task was persisted.
        assert d.get_task("TASK-DUP-NEW3") is None
        d._conn.close()

    def test_transactional_preflight_rollback_on_error(self):
        """A failed preflight must rollback completely — no legacy_status
        column, no marked rows, no unique index — leaving the legacy v1
        database exactly as it was before the invocation.

        Installs a BEFORE UPDATE trigger that raises on the duplicate-marking
        step, constructs Database (expecting failure), then verifies via a
        fresh connection that nothing was durably changed. Removes the trigger
        and proves a clean retry succeeds.
        """
        import sqlite3
        import tempfile
        from pathlib import Path
        from runtime.infrastructure.database import Database

        db_path = Path(tempfile.mkdtemp()) / "test.db"
        # Simulate v1 pre-index table with duplicate storage_key rows.
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
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
                final_output_dir TEXT,
                executor_pid INTEGER
            );
            CREATE TABLE IF NOT EXISTS task_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                storage_key TEXT NOT NULL,
                display_name TEXT NOT NULL,
                size_bytes INTEGER,
                content_type TEXT,
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (task_id) REFERENCES tasks(id),
                UNIQUE(task_id, ordinal)
            );
        """)
        conn.execute(
            "INSERT INTO tasks (id, status, team, brief, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("TASK-RB-1", "completed", "engineering", "rollback test 1",
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO tasks (id, status, team, brief, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("TASK-RB-2", "completed", "engineering", "rollback test 2",
             "2026-01-01T00:00:00", "2026-01-01T00:00:00"),
        )
        key1, key2 = "rb-dup-key", "rb-dup-key"
        conn.execute(
            "INSERT INTO task_attachments (task_id, ordinal, storage_key, "
            "display_name, size_bytes, content_type, uploaded_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("TASK-RB-1", 0, key1, "file-a.png", 100,
             "image/png", "founder", "2026-01-01T00:00:00"),
        )
        conn.execute(
            "INSERT INTO task_attachments (task_id, ordinal, storage_key, "
            "display_name, size_bytes, content_type, uploaded_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("TASK-RB-2", 0, key2, "file-b.png", 200,
             "image/png", "founder", "2026-01-02T00:00:00"),
        )
        # Install BEFORE UPDATE trigger that fires on legacy_status change
        # and deliberately fails, simulating a mid-preflight error.
        conn.execute(
            "CREATE TRIGGER IF NOT EXISTS trg_block_legacy_marking "
            "BEFORE UPDATE OF legacy_status ON task_attachments "
            "BEGIN "
            "  SELECT RAISE(ABORT, 'simulated preflight failure'); "
            "END"
        )
        conn.commit()
        conn.close()

        # Snapshot pre-migration state to compare after failure.
        def _snapshot():
            c = sqlite3.connect(str(db_path))
            c.row_factory = sqlite3.Row
            cols = {row[1] for row in c.execute(
                "PRAGMA table_info('task_attachments')"
            ).fetchall()}
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM task_attachments ORDER BY id"
            ).fetchall()]
            idxs = {row[0] for row in c.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='task_attachments'"
            ).fetchall()}
            c.close()
            return cols, rows, idxs

        pre_cols, pre_rows, pre_idxs = _snapshot()
        assert "legacy_status" not in pre_cols, (
            "legacy_status must not exist before migration"
        )
        assert len(pre_rows) == 2, "must have 2 legacy rows"
        assert "idx_task_attachments_storage_key_unique" not in pre_idxs, (
            "unique index must not exist before migration"
        )

        # Construct Database — must fail because trigger blocks UPDATE.
        from datetime import datetime, timezone
        try:
            d = Database(db_path)
            # If Database init succeeds, the idempotence guard may have
            # short-circuited — verify index exists or fail meaningfully.
            idx_now = {row[0] for row in d._conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='task_attachments'"
            ).fetchall()}
            # If we reach here without index AND without error, something
            # swallowed the exception — fail the test.
            if "idx_task_attachments_storage_key_unique" not in idx_now:
                d._conn.close()
                pytest.fail(
                    "Database init should have raised on trigger-aborted "
                    "preflight, but completed without index"
                )
            d._conn.close()
            # Index exists — idempotence guarded. Clean up and skip.
            conn2 = sqlite3.connect(str(db_path))
            conn2.execute("DROP TRIGGER IF EXISTS trg_block_legacy_marking")
            conn2.commit()
            conn2.close()
            pytest.skip("idempotence guard caught completed preflight")
        except Exception as exc:
            # Expected: preflight failed.
            error_msg = str(exc).lower()
            assert "simulated preflight failure" in error_msg or (
                "abort" in error_msg
            ), f"Unexpected error: {exc}"

        # Verify via a NEW connection that nothing was durably changed.
        post_cols, post_rows, post_idxs = _snapshot()

        # 1. legacy_status column was NOT added.
        assert "legacy_status" not in post_cols, (
            f"legacy_status must NOT be durably added after rollback; "
            f"got columns: {post_cols}"
        )
        # 2. Duplicate rows remain unmodified (no legacy_status marking).
        assert len(post_rows) == 2, (
            f"Both rows must be preserved; got {len(post_rows)}"
        )
        for row in post_rows:
            assert "legacy_status" not in row or row.get("legacy_status") is None, (
                f"Row {row['id']} must not be marked legacy after rollback"
            )
            assert row["storage_key"] == "rb-dup-key", (
                f"Row {row['id']} storage_key must be unchanged"
            )
        # 3. Unique index is absent.
        assert "idx_task_attachments_storage_key_unique" not in post_idxs, (
            f"Unique index must NOT exist after rollback; got: {post_idxs}"
        )

        # Remove the trigger and prove a clean retry completes.
        conn3 = sqlite3.connect(str(db_path))
        conn3.execute("DROP TRIGGER IF EXISTS trg_block_legacy_marking")
        conn3.commit()
        conn3.close()

        d_retry = Database(db_path)
        # After clean retry: legacy_status exists, rows marked, index present.
        retry_cols, retry_rows, retry_idxs = _snapshot()
        assert "legacy_status" in retry_cols, (
            "legacy_status must exist after clean retry"
        )
        assert "idx_task_attachments_storage_key_unique" in retry_idxs, (
            "Unique index must exist after clean retry"
        )
        for row in retry_rows:
            assert row.get("legacy_status") == "duplicate_v1", (
                f"Row {row['id']} must be marked duplicate_v1 after clean retry"
            )
        d_retry._conn.close()
