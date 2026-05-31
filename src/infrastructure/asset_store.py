"""Org-shared asset storage. Flat directory of opaque blobs.

Persistent artifacts produced by agents (reports, exports, screenshots, PDFs)
live here. Visible to every agent in the org via `happyranch assets {put,list,get}`.

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


MAX_ASSET_BYTES = 10 * 1024 * 1024  # 10 MB hard cap per file (v1).
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_MAX_NAME_LEN = 200


class InvalidAssetName(ValueError):
    """Name fails validation rules."""


class AssetTooLarge(ValueError):
    """Payload exceeds MAX_ASSET_BYTES."""


class AssetNotFound(KeyError):
    """No asset with that name exists in the store."""


@dataclass(frozen=True, slots=True)
class AssetInfo:
    name: str
    size_bytes: int
    modified_at: str  # ISO-8601 UTC, "Z"-suffixed


class AssetStore:
    """File-backed flat blob store. Single directory; no nesting."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def validate_name(self, name: str) -> None:
        if not name or len(name) > _MAX_NAME_LEN:
            raise InvalidAssetName(f"invalid_name: {name!r}")
        if name.startswith(".") or ".." in name or "/" in name or "\\" in name:
            raise InvalidAssetName(f"invalid_name: {name!r}")
        if not _NAME_RE.match(name):
            raise InvalidAssetName(f"invalid_name: {name!r}")

    def path_for(self, name: str) -> Path:
        self.validate_name(name)
        return self._root / name

    def put(self, name: str, content: bytes) -> AssetInfo:
        self.validate_name(name)
        if len(content) > MAX_ASSET_BYTES:
            raise AssetTooLarge(f"asset_too_large: {len(content)}B > {MAX_ASSET_BYTES}B")
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
        return AssetInfo(
            name=name,
            size_bytes=stat.st_size,
            modified_at=_iso(stat.st_mtime),
        )

    def read(self, name: str) -> bytes:
        path = self.path_for(name)
        if not path.exists():
            raise AssetNotFound(name)
        return path.read_bytes()

    def exists(self, name: str) -> bool:
        try:
            return self.path_for(name).exists()
        except InvalidAssetName:
            return False

    def list_assets(self) -> list[AssetInfo]:
        out: list[AssetInfo] = []
        for entry in sorted(self._root.iterdir()):
            if entry.name.startswith(".") or not entry.is_file():
                continue
            try:
                self.validate_name(entry.name)
            except InvalidAssetName:
                continue
            stat = entry.stat()
            out.append(AssetInfo(
                name=entry.name,
                size_bytes=stat.st_size,
                modified_at=_iso(stat.st_mtime),
            ))
        return out


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
