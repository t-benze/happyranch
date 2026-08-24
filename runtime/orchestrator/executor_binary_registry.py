"""Machine-local per-executor-kind binary-path registry.

THR-085: where executor binaries live on THIS machine. Separate from
THR-052 ExecutorRegistry (which executor KINDS/capabilities exist, ORG-portable)
and from `config.yaml` (Settings values). A dedicated file at
``<daemon-home>/executors.json`` keeps the register route's runtime writes cleanly
isolated from the shared config.yaml surface.

The registry is the SOLE resolution source for executor binaries (THR-107 seq155).
PATH discovery, shutil.which, and auto-pinning are never used.

CONCURRENCY: Every read-modify-write path (save_registry, set_binary,
remove_binary, remove_binary_conditional) and any direct writer shares
ONE lock (_registry_lock).  Public APIs acquire the lock once and delegate
to unlocked internal helpers — callers inside the module that already hold
the lock use the unlocked forms directly to avoid non-reentrant deadlock.

TEST ISOLATION (THR-204 issue 3): every write path fails closed with
``RegistryIsolationError`` when a pytest process targets the DEFAULT
production registry (~/.happyranch/executors.json) by any spelling —
including a ``HAPPYRANCH_DAEMON_HOME`` symlink/alias that resolves there —
without isolating ``HAPPYRANCH_DAEMON_HOME`` to a temporary daemon home.
The guard compares CANONICAL (symlink-resolved) targets so an alias can
never bypass the check.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File path
# ---------------------------------------------------------------------------


def _registry_path() -> Path:
    """Resolve the machine-local registry file path.

    Honors ``HAPPYRANCH_DAEMON_HOME`` for test isolation; defaults to
    ``~/.happyranch/executors.json``.
    """
    override = os.environ.get("HAPPYRANCH_DAEMON_HOME")
    base = Path(override) if override else Path.home() / ".happyranch"
    return base / "executors.json"


# ---------------------------------------------------------------------------
# Write lock (single-process multi-threaded daemon model)
# ---------------------------------------------------------------------------

_registry_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Test-process isolation guard (THR-204 issue 3)
# ---------------------------------------------------------------------------


class RegistryIsolationError(RuntimeError):
    """Raised when a test process attempts to write the production
    machine-local executor-binary registry without isolating the daemon home.

    THR-204 issue 3: tests/integration repros that registered fake binaries
    under PRODUCTION executor names (``claude``, ``codex``) without setting
    ``HAPPYRANCH_DAEMON_HOME`` to a temporary daemon home twice overwrote the
    live production registry (``~/.happyranch/executors.json``) and took down
    agent invocations.  This guard fails closed so a missed isolation can
    never mutate production state.
    """


def _is_test_process() -> bool:
    """Return True when this process is running under pytest.

    Detection uses the ``PYTEST_CURRENT_TEST`` env var pytest exports during
    test execution plus the presence of the ``pytest`` module in this
    interpreter.  The daemon never runs under pytest, so normal operator
    registration is unaffected by the write guard.
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return True
    return "pytest" in sys.modules


def _assert_write_target_safe() -> None:
    """Fail-closed write guard for the DEFAULT machine-local registry.

    Under a test process, refuse to write the default production registry at
    ``~/.happyranch/executors.json`` when ``HAPPYRANCH_DAEMON_HOME`` is not
    explicitly set (or explicitly points at the default, by any spelling —
    including a symlink/alias that canonicalizes there).  A test or
    integration repro that registers a fake binary under a production
    executor name without isolating the daemon home would otherwise overwrite
    the live registry — this has twice broken live agent invocations
    (THR-204 issue 3).

    Isolation is simply setting ``HAPPYRANCH_DAEMON_HOME`` to a temporary
    directory; every test that intentionally writes the registry already does
    this.  The daemon (the only legitimate writer of the default registry)
    never runs under pytest, so normal operator registration and fail-closed
    launch behavior are preserved.

    The candidate and protected targets are compared after ``resolve()``
    (non-strict), so symlinked/aliased spellings are canonicalized without
    requiring the registry file or its parent to exist.
    """
    if not _is_test_process():
        return
    candidate = _registry_path()
    protected = Path.home() / ".happyranch" / "executors.json"
    if candidate.resolve() == protected.resolve():
        raise RegistryIsolationError(
            "refusing to write the production executor-binary registry at "
            f"{candidate} from a test process. Set HAPPYRANCH_DAEMON_HOME to an "
            "isolated temporary daemon home (e.g. a pytest tmp_path) before "
            "registering executor binaries in tests."
        )


# ---------------------------------------------------------------------------
# Internal unlocked helpers — assume caller holds _registry_lock
# ---------------------------------------------------------------------------


def _load_registry_unlocked() -> dict[str, str]:
    """Load the machine-local binary path registry (caller must hold ``_registry_lock``).

    Returns a dict mapping executor kind names (lowercase bare strings like
    'claude', 'codex', 'opencode', 'pi') to absolute binary paths.

    Returns an empty dict when the file does not exist yet — no error.
    """
    path = _registry_path()
    if not path.exists():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("executor_binary_registry: could not read %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("executor_binary_registry: %s is not a JSON object", path)
        return {}
    # Validate values are strings.
    cleaned: dict[str, str] = {}
    for key, value in data.items():
        if isinstance(value, str) and value:
            cleaned[key.lower()] = value
        else:
            logger.warning(
                "executor_binary_registry: skipping entry %r with non-string value", key
            )
    return cleaned


def _save_registry_unlocked(entries: dict[str, str]) -> None:
    """Atomically write the machine-local binary path registry (caller must hold
    ``_registry_lock``).

    ``entries`` is a dict mapping executor kind names to absolute paths.
    Existing entries not present in ``entries`` are preserved (the call updates
    or adds keys; it does not replace the whole file).

    Paths are not validated here — validation is the caller's responsibility.
    """
    _assert_write_target_safe()
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    current = _load_registry_unlocked()
    merged = {**current, **entries}

    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
    tmp.replace(path)


def _remove_binary_unlocked(kind: str) -> None:
    """Remove a stored binary path from the registry (caller must hold
    ``_registry_lock``).

    No-op when the kind is not registered.
    """
    _assert_write_target_safe()
    path = _registry_path()
    current = _load_registry_unlocked()
    key = kind.lower()
    if key in current:
        del current[key]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Conditional delete outcome
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RemoveBinaryResult:
    """Atomic conditional-delete outcome returned by ``remove_binary_conditional``.

    Never re-reads the registry after a write — the result is derived entirely
    from the in-memory state observed during the locked critical section.
    """
    removed: bool
    """``True`` when the entry was present AND matched AND was removed."""

    stored_value: str | None = None
    """The value that was stored at the time of the conditional check, if any.
    ``None`` when the key was absent.  When ``removed`` is ``True`` this is
    the now-deleted value; when ``removed`` is ``False`` and the key exists
    this is the value that did NOT match ``expected_path``."""


# ---------------------------------------------------------------------------
# Public APIs — acquire _registry_lock once, delegate to unlocked helpers
# ---------------------------------------------------------------------------


def load_registry() -> dict[str, str]:
    """Load the machine-local binary path registry.

    Returns a dict mapping executor kind names (lowercase bare strings like
    'claude', 'codex', 'opencode', 'pi') to absolute binary paths.

    Returns an empty dict when the file does not exist yet — no error.
    """
    with _registry_lock:
        return _load_registry_unlocked()


def save_registry(entries: dict[str, str]) -> None:
    """Atomically write the machine-local binary path registry.

    ``entries`` is a dict mapping executor kind names to absolute paths.
    Existing entries not present in ``entries`` are preserved (the call updates
    or adds keys; it does not replace the whole file).

    Paths are not validated here — validation is the caller's responsibility.
    """
    with _registry_lock:
        _save_registry_unlocked(entries)


def set_binary(kind: str, binary_path: str) -> None:
    """Register or update the binary path for an executor kind."""
    with _registry_lock:
        _save_registry_unlocked({kind.lower(): binary_path})


def get_binary(kind: str) -> str | None:
    """Return the stored binary path for ``kind``, or None."""
    with _registry_lock:
        return _load_registry_unlocked().get(kind.lower())


def remove_binary(kind: str) -> None:
    """Remove a stored binary path from the registry.

    No-op when the kind is not registered.
    """
    with _registry_lock:
        _remove_binary_unlocked(kind)


def remove_binary_conditional(kind: str, expected_path: str) -> RemoveBinaryResult:
    """Atomically remove a binary path ONLY when the stored path exactly
    matches ``expected_path``.

    The load-compare-delete-write cycle is guarded by ``_registry_lock`` so
    a concurrent writer cannot replace the record between the check and the
    removal.

    Returns a ``RemoveBinaryResult`` dataclass with:
    - ``removed``: True when the entry was present AND matched AND was removed.
    - ``stored_value``: the value stored at check time (None if absent).
    """
    with _registry_lock:
        _assert_write_target_safe()
        path = _registry_path()
        current = _load_registry_unlocked()
        key = kind.lower()
        stored = current.get(key)
        if stored is None:
            return RemoveBinaryResult(removed=False, stored_value=None)
        if stored != expected_path:
            return RemoveBinaryResult(removed=False, stored_value=stored)
        del current[key]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, sort_keys=True)
        return RemoveBinaryResult(removed=True, stored_value=stored)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_binary(path_str: str) -> str:
    """Validate that ``path_str`` is an absolute path pointing to an executable file.

    Returns the validated absolute path on success. For symlinks, the
    supplied spelling is preserved (the operator-registered symlink path,
    such as /opt/homebrew/bin/claude → ../Cellar/.../bin/claude) so stored
    and displayed paths remain stable across target version bumps.
    Validation follows the symlink target for safety (existence, regular
    file, executable checks) but the returned path is the supplied spelling.

    Raises ``ValueError`` with a user-actionable message on failure.
    """
    if not os.path.isabs(path_str):
        raise ValueError(
            f"Path must be absolute, got {path_str!r}. "
            f"Use an absolute path like '/opt/homebrew/bin/claude'."
        )
    p = Path(path_str)
    # THR-107: validate through symlink for safety, but return
    # the operator-supplied spelling so stable Homebrew symlinks
    # survive version bumps in the Cellar target.
    _resolved = p.resolve()  # used only for safety checks below
    if not _resolved.is_file():
        raise ValueError(
            f"Path {path_str!r} does not exist or is not a regular file."
        )
    if not os.access(str(_resolved), os.X_OK):
        raise ValueError(
            f"Path {path_str!r} exists but is not executable."
        )
    return path_str


def is_binary_valid(path_str: str) -> bool:
    """Return True when ``path_str`` is an absolute path to an executable file."""
    try:
        validate_binary(path_str)
        return True
    except ValueError:
        return False
