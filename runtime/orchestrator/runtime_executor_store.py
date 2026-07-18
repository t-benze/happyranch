"""Runtime-level (machine-local) executor profile store — THR-088.

Stores full ExecutorProfiles at the RUNTIME level (registered once per
machine, visible to EVERY org) at ``<daemon-home>/executor_profiles.yaml``.

THR-107: this store is the SOLE durable definition surface for custom
executor profiles. The legacy per-org ``org/config.yaml``
``executor_profiles`` block is no longer parsed or registered; a one-shot
startup migration (``migrate_legacy_org_profiles``) lifts any lingering
legacy block into this store with a loud deprecation warning.

The store is additive to the existing:
- ``runtime/orchestrator/executor_registry.py`` (process-wide singleton)
- ``runtime/orchestrator/executor_binary_registry.py`` (machine-local binary paths)

Atomic write + YAML serialization mirror the org-config write path.
"""
from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

import yaml

from runtime.runtime import daemon_home

logger = logging.getLogger(__name__)

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


# ---------------------------------------------------------------------------
# THR-107: one-shot migration of the legacy per-org executor_profiles block
# ---------------------------------------------------------------------------


def migrate_legacy_org_profiles(config_path: Path, org_label: str) -> list[str]:
    """Lift a legacy per-org ``executor_profiles`` block into this store.

    THR-107 removed the per-org ``org/config.yaml`` ``executor_profiles``
    surface; the machine-global runtime store is the sole definition
    surface. This one-shot migration protects deployed runtimes whose
    config.yaml still carries the legacy block: each entry is lifted into
    the store via ``save_runtime_profile`` and a LOUD deprecation warning
    names exactly what was migrated. The config block itself is left in
    place but ignored thereafter (a warning fires while it lingers so the
    drop is never silent).

    Collision edge (e.g. two orgs defined the same profile name with
    different definitions): the existing store entry wins; the conflicting
    legacy entry is logged and SKIPPED — never a crash.

    Never raises for malformed input: bad YAML, a non-mapping block, or
    bad entries are warned about and skipped. Returns the list of profile
    names actually lifted into the store by this call.
    """
    if not config_path.exists():
        return []
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (yaml.YAMLError, OSError) as exc:
        logger.warning(
            "org %r: could not read %s while checking for a legacy "
            "executor_profiles block (%s); skipping migration",
            org_label, config_path, exc,
        )
        return []
    if not isinstance(raw, dict):
        return []
    block = raw.get("executor_profiles")
    if not block:
        return []
    if not isinstance(block, dict):
        logger.warning(
            "org %r: legacy executor_profiles block in %s is malformed "
            "(expected a mapping, got %s); the per-org executor_profiles "
            "config surface is removed — the block is ignored",
            org_label, config_path, type(block).__name__,
        )
        return []

    existing = load_runtime_profiles()
    migrated: list[str] = []
    for name, entry in block.items():
        if not isinstance(name, str) or not name or not isinstance(entry, dict):
            logger.warning(
                "org %r: legacy executor_profiles entry %r is malformed; "
                "skipping it during migration",
                org_label, name,
            )
            continue
        if name in existing:
            if existing[name] == entry:
                continue  # already lifted (or identically defined) — no-op
            logger.warning(
                "org %r: legacy executor_profiles entry %r conflicts with "
                "the machine-global runtime store definition; SKIPPING the "
                "legacy entry (the runtime store definition wins). "
                "Re-register it under a different name if both are needed.",
                org_label, name,
            )
            continue
        try:
            save_runtime_profile(name, entry)
        except Exception as exc:
            logger.warning(
                "org %r: failed to lift legacy executor profile %r into "
                "the runtime store: %s",
                org_label, name, exc,
            )
            continue
        existing[name] = entry
        migrated.append(name)

    if migrated:
        logger.warning(
            "org %r: MIGRATED legacy per-org executor_profiles %s from %s "
            "into the machine-global runtime store (%s). The per-org "
            "executor_profiles config surface is removed (THR-107); the "
            "block is now ignored — delete it from config.yaml.",
            org_label, migrated, config_path, _store_path(),
        )
    else:
        logger.warning(
            "org %r: config %s still carries a deprecated executor_profiles "
            "block; the per-org surface is removed (THR-107) and the block "
            "is ignored — delete it from config.yaml (definitions live in "
            "the machine-global runtime store: %s).",
            org_label, config_path, _store_path(),
        )
    return migrated
