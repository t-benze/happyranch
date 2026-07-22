"""Task-scoped attachment storage (THR-109).

Files live under <runtime>/orgs/<slug>/task-attachments/<storage_key>.
Metadata lives in the SQLite `task_attachments` table.

This is a private store, separate from the org-wide shared ArtifactStore.
Access is task-tree scoped: only the owning task and descendants resolving
through parent_task_id may have the bytes materialized.
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path


MAX_TASK_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB per file
MAX_TASK_ATTACHMENTS_PER_TASK = 5

# Allowed content types (v1 allowlist).
_ALLOWED_CONTENT_TYPES: frozenset[str] = frozenset({
    # Images (founder's primary use case — mockups / screenshots).
    "image/png",
    "image/jpeg",
    "image/jpg",  # Accept both jpeg and jpg labels.
    "image/gif",
    "image/webp",
    # Documents.
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
})

# Allowed extensions for content-type fallback resolution.
_ALLOWED_EXTENSIONS: dict[str, str] = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".pdf":  "application/pdf",
    ".txt":  "text/plain",
    ".md":   "text/markdown",
    ".csv":  "text/csv",
    ".json": "application/json",
}

# Sanitization: reject traversal chars, control chars, and empty/too-long names.
_DISPLAY_NAME_RE = re.compile(r"^[^\x00-\x1f\x7f/\\]+$")
_MAX_DISPLAY_NAME_LEN = 200


class TaskAttachmentTooLarge(ValueError):
    """Payload exceeds MAX_TASK_ATTACHMENT_BYTES."""


class TaskAttachmentUnsupportedType(ValueError):
    """Content type not in the v1 allowlist."""


class TaskAttachmentInvalidName(ValueError):
    """Display name fails sanitization rules."""


class TaskAttachmentTooMany(ValueError):
    """Task already has the maximum number of attachments."""


class TaskAttachmentNotFound(KeyError):
    """No attachment with that storage_key exists."""


def sanitize_display_name(name: str) -> str:
    """Validate and return display name. Raises on failure."""
    if not name or len(name) > _MAX_DISPLAY_NAME_LEN:
        raise TaskAttachmentInvalidName(
            f"display name must be 1-{_MAX_DISPLAY_NAME_LEN} chars"
        )
    if not _DISPLAY_NAME_RE.match(name):
        raise TaskAttachmentInvalidName(
            "display name contains invalid characters (no / \\ or control chars)"
        )
    return name


def resolve_content_type(display_name: str, declared: str | None) -> str | None:
    """Resolve the content type from the declared mime-type or file extension."""
    if declared:
        normalized = declared.lower().replace("image/jpg", "image/jpeg")
        if normalized in _ALLOWED_CONTENT_TYPES:
            return normalized
        raise TaskAttachmentUnsupportedType(
            f"unsupported content type: {declared}"
        )
    # Fall back to extension guessing.
    suffix = Path(display_name).suffix.lower()
    if suffix in _ALLOWED_EXTENSIONS:
        return _ALLOWED_EXTENSIONS[suffix]
    raise TaskAttachmentUnsupportedType(
        f"cannot determine content type from name: {display_name}"
    )


def is_allowed_content_type(content_type: str) -> bool:
    return content_type in _ALLOWED_CONTENT_TYPES


class TaskAttachmentStore:
    """File-backed store for task-private attachments.

    Each attachment's bytes live under a storage_key.
    Callers are responsible for authz (task-tree visibility checks).
    """

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def path_for(self, storage_key: str) -> Path:
        """Return the file path for a storage_key.

        Path-traversal guard: storage_key must be a simple safe token
        (letters, digits, hyphens, underscores only), enforced at upload time.
        """
        return self._root / storage_key

    def put(self, storage_key: str, content: bytes) -> int:
        """Write attachment content atomically. Returns size_bytes."""
        if len(content) > MAX_TASK_ATTACHMENT_BYTES:
            raise TaskAttachmentTooLarge(
                f"attachment too large: {len(content)}B > "
                f"{MAX_TASK_ATTACHMENT_BYTES}B"
            )
        target = self.path_for(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True)
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

    def read(self, storage_key: str) -> bytes:
        path = self.path_for(storage_key)
        if not path.exists() or path.is_dir():
            raise TaskAttachmentNotFound(
                f"attachment {storage_key} not found"
            )
        return path.read_bytes()

    def delete(self, storage_key: str) -> None:
        path = self.path_for(storage_key)
        if path.exists() and not path.is_dir():
            path.unlink()
