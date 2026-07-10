"""Machine-local per-executor-kind binary-path registry.

THR-085: where executor binaries live on THIS machine. Separate from
THR-052 ExecutorRegistry (which executor KINDS/capabilities exist, ORG-portable)
and from `config.yaml` (Settings values). A dedicated file at
``<daemon-home>/executors.json`` keeps the register route's runtime writes cleanly
isolated from the shared config.yaml surface.

The registry SUPPLEMENTS PATH resolution; it does not replace it (invariant 5).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
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
# Read
# ---------------------------------------------------------------------------


def load_registry() -> dict[str, str]:
    """Load the machine-local binary path registry.

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


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def save_registry(entries: dict[str, str]) -> None:
    """Atomically write the machine-local binary path registry.

    ``entries`` is a dict mapping executor kind names to absolute paths.
    Existing entries not present in ``entries`` are preserved (the call updates
    or adds keys; it does not replace the whole file).

    Paths are not validated here — validation is the caller's responsibility.
    """
    path = _registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Preserve existing entries, then overlay the new ones.
    current = load_registry()
    merged = {**current, **entries}

    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Single-entry helpers
# ---------------------------------------------------------------------------


def set_binary(kind: str, binary_path: str) -> None:
    """Register or update the binary path for an executor kind."""
    save_registry({kind.lower(): binary_path})


def get_binary(kind: str) -> str | None:
    """Return the stored binary path for ``kind``, or None."""
    return load_registry().get(kind.lower())


def remove_binary(kind: str) -> None:
    """Remove a stored binary path from the registry.

    No-op when the kind is not registered.
    """
    path = _registry_path()
    current = load_registry()
    key = kind.lower()
    if key in current:
        del current[key]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(current, fh, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_binary(path_str: str) -> str:
    """Validate that ``path_str`` is an absolute path pointing to an executable file.

    Returns the absolute, resolved path on success.

    Raises ``ValueError`` with a user-actionable message on failure.
    """
    if not os.path.isabs(path_str):
        raise ValueError(
            f"Path must be absolute, got {path_str!r}. "
            f"Use an absolute path like '/opt/homebrew/bin/claude'."
        )
    p = Path(path_str)
    if not p.is_file():
        raise ValueError(
            f"Path {path_str!r} does not exist or is not a regular file."
        )
    if not os.access(path_str, os.X_OK):
        raise ValueError(
            f"Path {path_str!r} exists but is not executable."
        )
    return str(p.resolve())


def is_binary_valid(path_str: str) -> bool:
    """Return True when ``path_str`` is an absolute path to an executable file."""
    try:
        validate_binary(path_str)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Auto-detection
# ---------------------------------------------------------------------------

KNOWN_KINDS: tuple[str, ...] = ("claude", "codex", "opencode", "pi")


def _candidate_dirs() -> list[Path]:
    """Return the list of standard directories to scan for executables.

    Order is stable, favouring common install locations first.
    Does not throw when a directory does not exist — callers handle empty lists.
    """
    dirs: list[Path] = []

    # macOS Homebrew (ARM)
    hb_arm = Path("/opt/homebrew/bin")
    if hb_arm.is_dir():
        dirs.append(hb_arm)

    # macOS Homebrew (Intel) / Linux /usr/local
    usr_local = Path("/usr/local/bin")
    if usr_local.is_dir():
        dirs.append(usr_local)

    # User-local bin (~/.local/bin)
    local_bin = Path.home() / ".local" / "bin"
    if local_bin.is_dir():
        dirs.append(local_bin)

    # npm global prefix bin (npm prefix -g)
    try:
        result = subprocess.run(
            ["npm", "prefix", "-g"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            npm_prefix = Path(result.stdout.strip()) / "bin"
            if npm_prefix.is_dir():
                dirs.append(npm_prefix)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # ~/.npm-global/bin (common fallback for npm -g without prefix)
    npm_global = Path.home() / ".npm-global" / "bin"
    if npm_global.is_dir():
        dirs.append(npm_global)

    return dirs


def detect_candidates() -> dict[str, list[str]]:
    """Auto-detect executor binary candidates from standard install locations.

    For each built-in executor kind (claude, codex, opencode, pi), scans
    standard directories and ``shutil.which`` for plausible binary paths.
    Returns a dict mapping each kind to a list of valid (exists + executable)
    absolute paths, de-duplicated and sorted for determinism.

    Kinds with no candidates produce an empty list — the caller or web UI can
    show "nothing detected — enter a path".

    Pure read-only: does NOT read or mutate the stored registry. Detection is
    independent of registration.

    Must not throw on missing directories or a missing ``npm`` binary — the
    target environment is a fresh host.
    """
    dirs = _candidate_dirs()
    result: dict[str, list[str]] = {kind: [] for kind in KNOWN_KINDS}

    for kind in KNOWN_KINDS:
        seen: set[str] = set()

        # 1. Scan standard directories for a file named <kind>
        for d in dirs:
            try:
                candidate = d / kind
                if candidate.is_file():
                    resolved = str(candidate.resolve())
                    if resolved not in seen and is_binary_valid(resolved):
                        seen.add(resolved)
                        result[kind].append(resolved)
            except OSError:
                # Permission denied on a directory entry — skip
                continue

        # 2. shutil.which — delegates to OS PATH and returns the absolute path
        which_hit = shutil.which(kind)
        if which_hit:
            resolved = str(Path(which_hit).resolve())
            if resolved not in seen and is_binary_valid(resolved):
                seen.add(resolved)
                result[kind].append(resolved)

        # Deterministic output
        result[kind].sort()

    return result
