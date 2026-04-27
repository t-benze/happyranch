from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import yaml


class RuntimeDir:
    """Value object representing a self-describing OPC runtime folder.

    The presence of an ``opc.yaml`` marker file distinguishes a valid
    runtime directory from an arbitrary path. The marker file carries the
    runtime's slug, creation timestamp, and schema version.
    """

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._cached_slug: str | None = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self._path

    @property
    def db_path(self) -> Path:
        return self._path / "opc.db"

    @property
    def workspaces_dir(self) -> Path:
        return self._path / "workspaces"

    @property
    def marker_file(self) -> Path:
        return self._path / "opc.yaml"

    @property
    def org_dir(self) -> Path:
        return self._path / "org"

    @property
    def agents_dir(self) -> Path:
        return self.org_dir / "agents"

    @property
    def pending_agents_dir(self) -> Path:
        return self.agents_dir / "_pending"

    @property
    def teams_config_path(self) -> Path:
        return self.org_dir / "teams.yaml"

    @property
    def slug(self) -> str:
        if self._cached_slug is not None:
            return self._cached_slug
        if not self.marker_file.exists():
            raise ValueError(f"{self.marker_file} missing")
        data = yaml.safe_load(self.marker_file.read_text()) or {}
        slug = data.get("slug")
        if not isinstance(slug, str) or not slug:
            raise ValueError(f"{self.marker_file} missing slug")
        self._cached_slug = slug
        return slug

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def is_valid(self) -> bool:
        """Return True if the marker file exists."""
        return self.marker_file.exists()

    # ------------------------------------------------------------------
    # Constructors
    # ------------------------------------------------------------------

    @classmethod
    def init(cls, path: Path, *, slug: str | None = None) -> RuntimeDir:
        """Create a runtime directory at *path*.

        On first creation, writes ``opc.yaml`` with the supplied ``slug``,
        a ``created_at`` timestamp, and ``schema_version: 1``. Subsequent
        calls are idempotent — the existing slug is preserved.

        Creates the ``workspaces/`` and ``org/agents/_pending/`` sub-directories.
        """
        instance = cls(path)
        instance.root.mkdir(parents=True, exist_ok=True)

        if not instance.marker_file.exists():
            if slug is None:
                raise ValueError("slug is required to initialize a new runtime")
            payload = {
                "slug": slug,
                "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "schema_version": 1,
            }
            instance.marker_file.write_text(yaml.safe_dump(payload, sort_keys=False))

        instance.workspaces_dir.mkdir(parents=True, exist_ok=True)
        instance.org_dir.mkdir(parents=True, exist_ok=True)
        instance.agents_dir.mkdir(parents=True, exist_ok=True)
        instance.pending_agents_dir.mkdir(parents=True, exist_ok=True)

        # Deferred import: teams.py imports RuntimeDir, so the import lives
        # inside the function to avoid a cycle at module-load time.
        from src.orchestrator.teams import TeamsRegistry
        TeamsRegistry.seed_empty(instance)
        return instance

    @classmethod
    def load(cls, path: Path) -> RuntimeDir:
        """Load an existing runtime directory from *path*.

        Raises ``ValueError`` if the marker file is absent.
        """
        instance = cls(path)
        if not instance.is_valid():
            raise ValueError(
                f"{path} is not a valid OPC runtime directory "
                f"(missing {instance.marker_file})"
            )
        return instance
