"""Org-shared artifact storage. Directory of opaque blobs with nested-key support.

Persistent artifacts produced by agents (reports, exports, screenshots, PDFs)
live here. Visible to every agent in the org via `happyranch artifacts {put,list,get}`.
Keys may use '/' as a path separator for logical folders (e.g. 'reports/2026/q2.pdf').

This module owns name validation, atomic writes, and read/list. It does NOT
touch HTTP, audit, or agent identity — those are concerns of the route layer.
"""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


MAX_ARTIFACT_BYTES = 10 * 1024 * 1024  # 10 MB hard cap per file (v1).
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_NAME_LEN = 200


class InvalidArtifactName(ValueError):
    """Name fails validation rules."""


class ArtifactTooLarge(ValueError):
    """Payload exceeds MAX_ARTIFACT_BYTES."""


class ArtifactNotFound(KeyError):
    """No artifact with that name exists in the store."""


@dataclass(frozen=True, slots=True)
class ArtifactInfo:
    name: str
    size_bytes: int
    modified_at: str  # ISO-8601 UTC, "Z"-suffixed


class ArtifactStore:
    """File-backed blob store with nested-key support."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def validate_name(self, name: str) -> None:
        if not name or len(name) > _MAX_NAME_LEN:
            raise InvalidArtifactName(f"invalid_name: {name!r}")
        # Reject traversal vectors at the name-string level.
        if name.startswith("/") or name.endswith("/") or "//" in name or "\\" in name:
            raise InvalidArtifactName(f"invalid_name: {name!r}")
        segments = name.split("/")
        for seg in segments:
            if not seg or seg == ".." or seg.startswith("."):
                raise InvalidArtifactName(f"invalid_name: {name!r}")
            if not _NAME_RE.match(seg):
                raise InvalidArtifactName(f"invalid_name: {name!r}")

    def path_for(self, name: str) -> Path:
        self.validate_name(name)
        target = self._root / name
        # Path-traversal guard: resolved absolute path must be under root.
        resolved = target.resolve()
        if not resolved.is_relative_to(self._root.resolve()):
            raise InvalidArtifactName(f"path_traversal: {name!r}")
        return target

    def put(self, name: str, content: bytes) -> ArtifactInfo:
        self.validate_name(name)
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ArtifactTooLarge(f"artifact_too_large: {len(content)}B > {MAX_ARTIFACT_BYTES}B")
        target = self.path_for(name)
        # Create intermediate directories for nested keys.
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
        except FileExistsError as exc:
            raise InvalidArtifactName(
                f"name_collides_with_existing_artifact: {name!r}"
            ) from exc
        # Atomic write: create tmp file in the DESTINATION's parent dir
        # so os.replace stays atomic on one filesystem.
        fd, tmp_path_str = tempfile.mkstemp(prefix=".tmp.", dir=str(target.parent))
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            os.replace(tmp_path, target)
        except IsADirectoryError as exc:
            tmp_path.unlink(missing_ok=True)
            raise InvalidArtifactName(
                f"name_collides_with_existing_artifact: {name!r}"
            ) from exc
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise
        stat = target.stat()
        return ArtifactInfo(
            name=name,
            size_bytes=stat.st_size,
            modified_at=_iso(stat.st_mtime),
        )

    def read(self, name: str) -> bytes:
        path = self.path_for(name)
        if not path.exists():
            raise ArtifactNotFound(name)
        if path.is_dir():
            raise ArtifactNotFound(name)
        return path.read_bytes()

    def delete(self, name: str) -> None:
        self.validate_name(name)
        path = self.path_for(name)
        if not path.exists():
            raise ArtifactNotFound(name)
        if path.is_dir():
            raise ArtifactNotFound(name)
        # Single-file unlink is atomic enough; no locking needed.
        path.unlink()

    def exists(self, name: str) -> bool:
        try:
            return self.path_for(name).exists()
        except InvalidArtifactName:
            return False

    def list_artifacts(self, prefix: str = "") -> list[ArtifactInfo]:
        out: list[ArtifactInfo] = []
        # Walk recursively (rglob) returning full relative POSIX keys.
        for entry in sorted(self._root.rglob("*")):
            if not entry.is_file():
                continue
            # Skip dotfiles and tmp files at any depth.
            if any(part.startswith(".") for part in entry.parts[len(self._root.parts):]):
                continue
            # Compute the relative POSIX key from the store root.
            rel = entry.relative_to(self._root)
            name = rel.as_posix()
            # Optional prefix filter.
            if prefix and not name.startswith(prefix):
                continue
            try:
                self.validate_name(name)
            except InvalidArtifactName:
                continue
            stat = entry.stat()
            out.append(ArtifactInfo(
                name=name,
                size_bytes=stat.st_size,
                modified_at=_iso(stat.st_mtime),
            ))
        return out


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
