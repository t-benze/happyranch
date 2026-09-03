"""Machine-global custom adapter store (THR-107 D3).

Stores registered custom adapter executable entries at
``<daemon-home>/adapters.yaml`` — one file per machine, visible to EVERY org.

This is a NEW file — it does NOT alter ``executor_profiles.yaml``, any
SQLite schema/column/migration, or the existing executor profile store.
Custom adapters and custom executor profiles are independent surfaces.

Atomic write + YAML serialization mirror the executor profile store pattern
(``runtime_executor_store.py``).

D3 scope: PENDING-ONLY. No approval/activation state (D4). No profile
binding or launch integration (D7). No permission/sandbox expansion (D5).

THR-107 seq244: adds dependency-manifest fields to AdapterEntry for
persisting declared child executable dependencies.
"""
from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from runtime.runtime import daemon_home

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Store-level write lock
# ---------------------------------------------------------------------------

_store_lock = threading.RLock()


def acquire_store_lock() -> None:
    """Acquire the exclusive adapter store write lock (reentrant).

    Serializes all write operations (registration, approval, removal)
    against the shared adapters.yaml file.  Callers MUST release it
    via ``release_store_lock()`` — prefer a try/finally block.

    This lock is reentrant so that ``approve_adapter`` and
    ``register_custom_adapter`` can hold it across their critical
    sections while internally calling ``save_adapter``, which also
    acquires it.

    This lock covers the daemon's single-process multi-threaded
    concurrency model (FastAPI thread pool).  For cross-process safety
    an ``fcntl.flock`` would be needed, but the daemon ships as a single
    process.
    """
    _store_lock.acquire()


def release_store_lock() -> None:
    """Release the adapter store write lock."""
    _store_lock.release()

# ---------------------------------------------------------------------------
# Adapter entry model
# ---------------------------------------------------------------------------


@dataclass
class AdapterEntry:
    """A registered custom adapter executable entry.

    D3 fields (this slice):
      - id: unique identifier (derived from adapter name)
      - name: human-readable adapter name
      - executable: absolute path to the adapter executable
      - executable_hash: SHA-256 hex digest of the executable
      - version: adapter version string
      - capabilities: list of server-accepted or server-earned capabilities
      - contract_version: which AdapterInput/AdapterOutput version it speaks
      - workspace_adapter: which workspace prep adapter to use
      - status: "pending" ONLY in D3 (D4 adds "approved")
      - registered_at: ISO-8601 timestamp
      - registered_by: who registered it

    D4 fields (NOT populated in D3, present for forward-compat):
      - approved_at: null until D4 founder approval
      - approved_by: null until D4 founder approval

    THR-107 seq141 adapter-submission fields:
      - intended_profile_name: the profile name the adapter is bound to
        (set during adapter-submission, verified at profile-binding time)

    THR-107 seq244 dependency-manifest fields:
      - dependency_manifest_version: version of the dependency manifest
        (None for legacy entries without this extension)
      - dependencies: list of declared child executable dependency records
        (empty list for legacy entries)
    """

    id: str
    name: str
    executable: str
    executable_hash: str
    version: str
    capabilities: list[str] = field(default_factory=list)
    contract_version: int = 1
    workspace_adapter: str = "pi"
    status: str = "pending"
    registered_at: str = ""
    registered_by: str = ""
    # D4 forward-compat fields — always null in D3
    approved_at: str | None = None
    approved_by: str | None = None
    # THR-107 seq141: intended profile binding
    intended_profile_name: str | None = None
    # THR-107 seq244: dependency manifest
    dependency_manifest_version: int | None = None
    dependencies: list[dict] = field(default_factory=list)
    # THR-200: server-earned resume conformance receipt.  These fields are
    # absent for legacy/fresh-only adapters and never accepted as claims.
    thread_resume_verified_at: str | None = None
    thread_resume_contract_version: int | None = None

    def to_dict(self) -> dict:
        """Serialize to a plain dict for YAML persistence."""
        d: dict = {
            "id": self.id,
            "name": self.name,
            "executable": self.executable,
            "executable_hash": self.executable_hash,
            "version": self.version,
            "capabilities": self.capabilities,
            "contract_version": self.contract_version,
            "workspace_adapter": self.workspace_adapter,
            "status": self.status,
            "registered_at": self.registered_at,
            "registered_by": self.registered_by,
        }
        if self.approved_at is not None:
            d["approved_at"] = self.approved_at
        if self.approved_by is not None:
            d["approved_by"] = self.approved_by
        if self.intended_profile_name is not None:
            d["intended_profile_name"] = self.intended_profile_name
        # THR-107 seq244: dependency manifest (None/empty → omitted for legacy)
        if self.dependency_manifest_version is not None:
            d["dependency_manifest_version"] = self.dependency_manifest_version
        if self.dependencies:
            d["dependencies"] = self.dependencies
        if self.thread_resume_verified_at is not None:
            d["thread_resume_verified_at"] = self.thread_resume_verified_at
        if self.thread_resume_contract_version is not None:
            d["thread_resume_contract_version"] = self.thread_resume_contract_version
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "AdapterEntry":
        """Deserialize from a plain dict (YAML load)."""
        return cls(
            id=d.get("id", ""),
            name=d.get("name", ""),
            executable=d.get("executable", ""),
            executable_hash=d.get("executable_hash", ""),
            version=d.get("version", ""),
            capabilities=d.get("capabilities", []),
            contract_version=d.get("contract_version", 1),
            workspace_adapter=d.get("workspace_adapter", "pi"),
            status=d.get("status", "pending"),
            registered_at=d.get("registered_at", ""),
            registered_by=d.get("registered_by", ""),
            approved_at=d.get("approved_at"),
            approved_by=d.get("approved_by"),
            intended_profile_name=d.get("intended_profile_name"),
            dependency_manifest_version=d.get("dependency_manifest_version"),
            dependencies=d.get("dependencies", []),
            thread_resume_verified_at=d.get("thread_resume_verified_at"),
            thread_resume_contract_version=d.get("thread_resume_contract_version"),
        )


# ---------------------------------------------------------------------------
# File path
# ---------------------------------------------------------------------------


def _store_path() -> Path:
    """Resolve the machine-local adapter store file path.

    Honors ``HAPPYRANCH_DAEMON_HOME`` for test isolation; defaults to
    ``~/.happyranch/adapters.yaml``.
    """
    override = os.environ.get("HAPPYRANCH_DAEMON_HOME")
    base = Path(override) if override else daemon_home()
    return base / "adapters.yaml"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def load_adapters() -> dict[str, AdapterEntry]:
    """Load all registered custom adapters from the machine-global store.

    Returns a dict mapping adapter id → ``AdapterEntry``.
    Returns an empty dict when the file does not exist yet — no error.
    Corrupt/malformed entries are logged and skipped.
    """
    path = _store_path()
    if not path.exists():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError):
        return {}
    if not isinstance(data, dict):
        return {}
    result: dict[str, AdapterEntry] = {}
    for key, value in data.items():
        if not isinstance(key, str) or not key:
            continue
        if not isinstance(value, dict):
            continue
        try:
            result[key] = AdapterEntry.from_dict(value)
        except Exception:
            logger.warning("Skipping malformed adapter entry %r in %s", key, path)
    return result


def get_adapter(adapter_id: str) -> AdapterEntry | None:
    """Look up a single adapter by id. Returns None if not found."""
    return load_adapters().get(adapter_id)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def save_adapter(entry: AdapterEntry) -> None:
    """Atomically add or update a single custom adapter entry.

    Uses atomic temp-file + ``os.replace`` pattern (same as
    ``runtime_executor_store.save_runtime_profile``).

    The entry's ``id`` is used as the store key.

    Acquires the store write lock internally (reentrant) so that
    direct callers (tests, D3 path) are safe.
    """
    acquire_store_lock()
    try:
        _save_adapter_locked(entry)
    finally:
        release_store_lock()


def _save_adapter_locked(entry: AdapterEntry) -> None:
    """Internal: write entry assuming the caller already holds the lock."""
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    current = load_adapters()
    current[entry.id] = entry

    # Serialize as {id: dict, ...}
    serialized: dict[str, dict] = {}
    for aid, aentry in current.items():
        serialized[aid] = aentry.to_dict()

    fd, tmp = tempfile.mkstemp(
        prefix=".adapters.", suffix=".yaml", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(serialized, fh, sort_keys=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def remove_adapter(adapter_id: str) -> bool:
    """Atomically remove a single custom adapter entry.

    Returns True if the entry was removed, False if it was not found
    (no-op). Uses the same atomic temp-file + ``os.replace`` pattern.

    Acquires the store write lock internally.
    """
    acquire_store_lock()
    try:
        path = _store_path()
        current = load_adapters()
        if adapter_id not in current:
            return False
        del current[adapter_id]

        serialized: dict[str, dict] = {}
        for aid, aentry in current.items():
            serialized[aid] = aentry.to_dict()

        fd, tmp = tempfile.mkstemp(
            prefix=".adapters.", suffix=".yaml", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w") as fh:
                yaml.safe_dump(serialized, fh, sort_keys=False)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        return True
    finally:
        release_store_lock()


# ---------------------------------------------------------------------------
# SHA-256 computation
# ---------------------------------------------------------------------------


def compute_sha256(filepath: str | Path) -> str:
    """Compute the SHA-256 hex digest of a file's contents.

    Reads the file in 64 KB chunks to handle large executables without
    loading them entirely into memory.
    """
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()
