"""Org-shared artifact storage. Flat directory of opaque blobs.

Persistent artifacts produced by agents (reports, exports, screenshots, PDFs)
live here. Visible to every agent in the org via `happyranch artifacts {put,list,get}`.

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
    """File-backed flat blob store. Single directory; no nesting."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def validate_name(self, name: str) -> None:
        if not name or len(name) > _MAX_NAME_LEN:
            raise InvalidArtifactName(f"invalid_name: {name!r}")
        if name.startswith(".") or ".." in name or "/" in name or "\\" in name:
            raise InvalidArtifactName(f"invalid_name: {name!r}")
        if not _NAME_RE.match(name):
            raise InvalidArtifactName(f"invalid_name: {name!r}")

    def path_for(self, name: str) -> Path:
        self.validate_name(name)
        return self._root / name

    def put(self, name: str, content: bytes) -> ArtifactInfo:
        self.validate_name(name)
        if len(content) > MAX_ARTIFACT_BYTES:
            raise ArtifactTooLarge(f"artifact_too_large: {len(content)}B > {MAX_ARTIFACT_BYTES}B")
        target = self._root / name
        # Atomic write: write to .tmp.* sibling, then os.replace into place.
        fd, tmp_path_str = tempfile.mkstemp(prefix=".tmp.", dir=str(self._root))
        tmp_path = Path(tmp_path_str)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
            os.replace(tmp_path, target)
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
        return path.read_bytes()

    def exists(self, name: str) -> bool:
        try:
            return self.path_for(name).exists()
        except InvalidArtifactName:
            return False

    def list_artifacts(self) -> list[ArtifactInfo]:
        out: list[ArtifactInfo] = []
        for entry in sorted(self._root.iterdir()):
            if entry.name.startswith(".") or not entry.is_file():
                continue
            try:
                self.validate_name(entry.name)
            except InvalidArtifactName:
                continue
            stat = entry.stat()
            out.append(ArtifactInfo(
                name=entry.name,
                size_bytes=stat.st_size,
                modified_at=_iso(stat.st_mtime),
            ))
        return out


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
