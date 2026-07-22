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
    def test_no_ancestors(self, db):
        _make_task(db, "TASK-001")
        attachments = db.resolve_ancestor_attachments("TASK-001")
        assert attachments == []

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

    def test_union_multiple_ancestors(self, db):
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", ordinal=0, storage_key="ta-0001", display_name="root.png")
        _make_task(db, "TASK-002", parent_id="TASK-001")
        _make_attachment(db, "TASK-002", ordinal=0, storage_key="ta-0002", display_name="middle.png")
        _make_task(db, "TASK-003", parent_id="TASK-002")

        attachments = db.resolve_ancestor_attachments("TASK-003")
        names = {a.display_name for a in attachments}
        assert names == {"root.png", "middle.png"}

    def test_root_task_gets_nothing(self, db):
        _make_task(db, "TASK-001")
        _make_attachment(db, "TASK-001", ordinal=0, storage_key="ta-0001", display_name="self.png")

        # Root task's own attachments are NOT included via resolve_ancestor_attachments
        # (they are the task's own attachments, not inherited from ancestors).
        attachments = db.resolve_ancestor_attachments("TASK-001")
        assert attachments == []

    def test_no_parent_no_attachments(self, db):
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
