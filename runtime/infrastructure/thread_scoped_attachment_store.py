"""Thread-scoped attachment storage.

Files live under <runtime>/orgs/<slug>/threads/<thread_id>/attachments/<attachment_id>.
Metadata lives in the SQLite `thread_scoped_attachments` table.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path


MAX_THREAD_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB hard cap


class ThreadScopedAttachmentStore:
    """File-backed store for thread-private attachments.

    Each thread's attachments live in its own subdirectory.
    Callers are responsible for authz (thread participation checks).
    """

    def __init__(self, threads_root: Path) -> None:
        self._root = threads_root

    def _attachments_dir(self, thread_id: str) -> Path:
        d = self._root / thread_id / "attachments"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def path_for(self, thread_id: str, attachment_id: str) -> Path:
        return self._attachments_dir(thread_id) / attachment_id

    def put(self, thread_id: str, attachment_id: str, content: bytes) -> int:
        """Write attachment content atomically. Returns size_bytes."""
        if len(content) > MAX_THREAD_ATTACHMENT_BYTES:
            raise ValueError(
                f"attachment too large: {len(content)}B > {MAX_THREAD_ATTACHMENT_BYTES}B"
            )
        target = self.path_for(thread_id, attachment_id)
        fd, tmp_path_str = tempfile.mkstemp(
            prefix=".tmp.", dir=str(target.parent)
        )
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            os.replace(tmp_path, target)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        return target.stat().st_size

    def read(self, thread_id: str, attachment_id: str) -> bytes:
        path = self.path_for(thread_id, attachment_id)
        if not path.exists() or path.is_dir():
            raise KeyError(
                f"attachment {attachment_id} not found in thread {thread_id}"
            )
        return path.read_bytes()

    def delete(self, thread_id: str, attachment_id: str) -> None:
        path = self.path_for(thread_id, attachment_id)
        if path.exists() and not path.is_dir():
            path.unlink()
