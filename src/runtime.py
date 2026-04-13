from __future__ import annotations

from pathlib import Path


class RuntimeDir:
    """Value object representing a self-describing OPC runtime folder.

    The presence of an ``opc.toml`` marker file distinguishes a valid
    runtime directory from an arbitrary path.
    """

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()

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
        return self._path / "opc.toml"

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
    def init(cls, path: Path) -> RuntimeDir:
        """Create a runtime directory at *path*.

        Creates the directory, writes an empty ``opc.toml`` marker, and
        creates the ``workspaces/`` sub-directory.  Calling this more
        than once on the same path is idempotent — existing files are
        not overwritten.

        Returns the new ``RuntimeDir`` instance.
        """
        instance = cls(path)
        instance.root.mkdir(parents=True, exist_ok=True)
        # Write marker only if it doesn't already exist so we don't
        # overwrite any future content.
        if not instance.marker_file.exists():
            instance.marker_file.write_text("")
        instance.workspaces_dir.mkdir(parents=True, exist_ok=True)
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
