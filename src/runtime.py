from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import yaml

_RESERVED_ORG_SLUGS = frozenset({"_pending", "_archive"})
_SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")


class RuntimeDir:
    """A multi-org runtime container.

    The container itself has no slug — orgs live under ``orgs/<slug>/`` and
    each org's slug is its directory name. The container's ``happyranch.yaml``
    marker only carries ``schema_version: 2`` and ``type: multi-org-runtime``.
    """

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()

    @property
    def root(self) -> Path:
        return self._path

    @property
    def marker_file(self) -> Path:
        return self._path / "happyranch.yaml"

    @property
    def orgs_dir(self) -> Path:
        return self._path / "orgs"

    def is_valid(self) -> bool:
        return self.marker_file.exists()

    def iter_org_roots(self) -> Iterator[tuple[str, Path]]:
        """Yield ``(slug, org_root)`` for every valid org subdirectory.

        Reserved names (``_pending``, ``_archive``) are skipped. A directory
        without ``org/teams.yaml`` is treated as not-yet-initialized and
        skipped silently — this is what lets ``happyranch orgs init`` materialize
        the skeleton lazily.
        """
        if not self.orgs_dir.is_dir():
            return
        for entry in sorted(self.orgs_dir.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in _RESERVED_ORG_SLUGS:
                continue
            if not _SLUG_RE.match(entry.name):
                continue
            if not (entry / "org" / "teams.yaml").is_file():
                continue
            yield entry.name, entry

    @classmethod
    def init(cls, path: Path) -> RuntimeDir:
        instance = cls(path)
        instance.root.mkdir(parents=True, exist_ok=True)
        instance.orgs_dir.mkdir(parents=True, exist_ok=True)
        if not instance.marker_file.exists():
            instance.marker_file.write_text(yaml.safe_dump({
                "schema_version": 2,
                "type": "multi-org-runtime",
                "created_at": datetime.now(timezone.utc).isoformat().replace(
                    "+00:00", "Z"
                ),
            }, sort_keys=False))
        return instance

    @classmethod
    def load(cls, path: Path) -> RuntimeDir:
        instance = cls(path)
        if not instance.is_valid():
            raise ValueError(
                f"{path} is not a valid HappyRanch runtime directory "
                f"(missing {instance.marker_file})"
            )
        data = yaml.safe_load(instance.marker_file.read_text()) or {}
        version = data.get("schema_version")
        if version != 2:
            raise ValueError(
                f"runtime at {path} is schema_version {version!r}; "
                f"only schema_version 2 (multi-org) is supported. "
                f"re-init from scratch with `happyranch init <new-path>`."
            )
        return instance
