from __future__ import annotations

from runtime.infrastructure.db._shared import _synchronized
from runtime.models import TaskAttachmentRecord, ThreadScopedAttachment


class AttachmentsMixin:
    # --- Attachments ---

    @_synchronized
    def next_thread_attachment_id(self) -> str:
        cursor = self._conn.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM thread_scoped_attachments"
        )
        n = cursor.fetchone()[0]
        return f"att-{n:03d}"

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
    def next_task_attachment_id(self) -> str:
        cursor = self._conn.execute(
            "SELECT COALESCE(MAX(id), 0) + 1 FROM task_attachments"
        )
        n = cursor.fetchone()[0]
        return f"ta-{n:04d}"

    @_synchronized
    def get_task_attachment(
        self, task_id: str, storage_key: str
    ) -> TaskAttachmentRecord | None:
        cursor = self._conn.execute(
            "SELECT * FROM task_attachments "
            "WHERE task_id = ? AND storage_key = ?",
            (task_id, storage_key),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return TaskAttachmentRecord(
            id=row["id"],
            task_id=row["task_id"],
            ordinal=row["ordinal"],
            storage_key=row["storage_key"],
            display_name=row["display_name"],
            size_bytes=row["size_bytes"],
            content_type=row["content_type"],
            uploaded_by=row["uploaded_by"],
            created_at=row["created_at"],
            legacy_status=row["legacy_status"] if "legacy_status" in row.keys() else None,
        )

    @_synchronized
    def list_task_attachments(self, task_id: str) -> list[TaskAttachmentRecord]:
        cursor = self._conn.execute(
            "SELECT * FROM task_attachments "
            "WHERE task_id = ? ORDER BY ordinal",
            (task_id,),
        )
        return [
            TaskAttachmentRecord(
                id=row["id"],
                task_id=row["task_id"],
                ordinal=row["ordinal"],
                storage_key=row["storage_key"],
                display_name=row["display_name"],
                size_bytes=row["size_bytes"],
                content_type=row["content_type"],
                uploaded_by=row["uploaded_by"],
                created_at=row["created_at"],
                legacy_status=row["legacy_status"] if "legacy_status" in row.keys() else None,
            )
            for row in cursor.fetchall()
        ]

    @_synchronized
    def resolve_ancestor_attachments(
        self, task_id: str, max_hops: int = 20
    ) -> list[TaskAttachmentRecord]:
        """Walk the parent_task_id chain and union OWN + ancestor attachments.

        Returns the spawning task's own attachments plus every ancestor's
        attachments up to root, in deterministic order (own first, then
        nearest ancestor to root). No rows are copied into child tasks.
        The owning task_id is preserved per record so callers know which
        task each attachment came from.
        """
        result: list[TaskAttachmentRecord] = []
        # 1. Own attachments first.
        own = self.list_task_attachments(task_id)
        result.extend(own)
        # 2. Walk up to root, unioning ancestor attachments.
        seen: set[str] = {task_id}
        current_id = task_id
        for _ in range(max_hops):
            row = self._conn.execute(
                "SELECT parent_task_id FROM tasks WHERE id = ?",
                (current_id,),
            ).fetchone()
            if row is None:
                break
            parent_id = row["parent_task_id"]
            if parent_id is None or parent_id in seen:
                break
            seen.add(parent_id)
            # Collect attachments from this ancestor.
            attachments = self.list_task_attachments(parent_id)
            result.extend(attachments)
            current_id = parent_id
        return result

    @_synchronized
    def delete_task_attachment(
        self, task_id: str, storage_key: str
    ) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM task_attachments "
            "WHERE task_id = ? AND storage_key = ?",
            (task_id, storage_key),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    @_synchronized
    def count_task_attachments(self, task_id: str) -> int:
        cursor = self._conn.execute(
            "SELECT COUNT(*) FROM task_attachments WHERE task_id = ?",
            (task_id,),
        )
        return cursor.fetchone()[0]

    @_synchronized
    def get_task_attachment_by_storage_key(
        self, storage_key: str
    ) -> TaskAttachmentRecord | None:
        cursor = self._conn.execute(
            "SELECT * FROM task_attachments WHERE storage_key = ?",
            (storage_key,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        return TaskAttachmentRecord(
            id=row["id"],
            task_id=row["task_id"],
            ordinal=row["ordinal"],
            storage_key=row["storage_key"],
            display_name=row["display_name"],
            size_bytes=row["size_bytes"],
            content_type=row["content_type"],
            uploaded_by=row["uploaded_by"],
            created_at=row["created_at"],
            legacy_status=row["legacy_status"] if "legacy_status" in row.keys() else None,
        )
