"""Runtime-level (machine-local) executor profile store — THR-088.

Stores full ExecutorProfiles at the RUNTIME level (registered once per
machine, visible to EVERY org), mirroring the org/config.yaml
executor_profiles block but at ``<daemon-home>/executor_profiles.yaml``.

The store is additive to the existing:
- ``runtime/orchestrator/executor_registry.py`` (process-wide singleton)
- ``runtime/orchestrator/org_config.py`` (org-scoped config write)
- ``runtime/orchestrator/executor_binary_registry.py`` (machine-local binary paths)

Atomic write + YAML serialization mirror the org-config write path.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml

from runtime.runtime import daemon_home

# ---------------------------------------------------------------------------
# File path
# ---------------------------------------------------------------------------


def _store_path() -> Path:
    """Resolve the machine-local runtime executor profiles file path.

    Honors ``HAPPYRANCH_DAEMON_HOME`` for test isolation; defaults to
    ``~/.happyranch/executor_profiles.yaml``.
    """
    override = os.environ.get("HAPPYRANCH_DAEMON_HOME")
    base = Path(override) if override else daemon_home()
    return base / "executor_profiles.yaml"


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------


def load_runtime_profiles() -> dict[str, dict]:
    """Load the machine-local runtime executor profiles.

    Returns a dict mapping profile names to profile config dicts
    (each with command, argv_template, adapter). Returns an empty
    dict when the file does not exist yet — no error.
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
    # Validate structure: every value must be a dict
    cleaned: dict[str, dict] = {}
    for key, value in data.items():
        if isinstance(key, str) and key and isinstance(value, dict):
            cleaned[key] = value
    return cleaned


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------


def save_runtime_profile(name: str, entry: dict) -> None:
    """Atomically add or update a single runtime executor profile entry.

    ``name`` is the profile name (non-empty string).
    ``entry`` is the profile config dict with command, argv_template, adapter.

    Uses atomic temp-file + os.replace pattern (same as org-config writer).
    """
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    # Read current profiles, merge in the new entry
    current = load_runtime_profiles()
    current[name] = entry

    fd, tmp = tempfile.mkstemp(
        prefix=".executor-profiles.", suffix=".yaml", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w") as fh:
            yaml.safe_dump(current, fh, sort_keys=False)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise
