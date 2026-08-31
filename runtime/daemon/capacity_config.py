"""Paired, next-restart-only daemon capacity configuration.

This module deliberately owns only ``queue_workers`` and
``host_global_session_cap``.  It is not a generic YAML editing surface.
"""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Callable

import yaml

from runtime.config import Settings

CAPACITY_KEYS = ("queue_workers", "host_global_session_cap")
_LOCK = threading.RLock()


class CapacityConfigError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _revision(raw: bytes | None) -> str:
    marker = b"missing\0" if raw is None else b"present\0" + raw
    return "sha256:" + hashlib.sha256(marker).hexdigest()


def _read(path: Path) -> tuple[bytes | None, dict[str, Any]]:
    try:
        raw = path.read_bytes() if path.exists() else None
    except OSError as exc:
        raise CapacityConfigError("config_read_failed", "capacity configuration could not be read") from exc
    if raw is None or not raw.strip():
        return raw, {}
    try:
        value = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise CapacityConfigError("config_parse_failed", "capacity configuration YAML is malformed") from exc
    if value is None:
        return raw, {}
    if not isinstance(value, dict):
        raise CapacityConfigError("config_not_mapping", "capacity configuration must be a YAML mapping")
    return raw, value


def _env_shadowed() -> list[str]:
    return [key for key in CAPACITY_KEYS if f"HAPPYRANCH_{key.upper()}" in os.environ]


def snapshot(path: Path, running: Settings, *, capability_reason: str) -> dict[str, Any]:
    raw, mapping = _read(path)
    persisted = {key: mapping.get(key) for key in CAPACITY_KEYS}
    shadowed = _env_shadowed()
    next_start = {
        key: getattr(Settings(), key) if key in shadowed else mapping.get(key, getattr(Settings(), key))
        for key in CAPACITY_KEYS
    }
    running_pair = {key: getattr(running, key) for key in CAPACITY_KEYS}
    pending = next_start != running_pair
    return {
        "running_at_daemon_start": running_pair,
        "running_provenance": "startup-resolved settings snapshot",
        "persisted_yaml": persisted,
        "next_start": next_start,
        "environment_shadowed": shadowed,
        "environment_warning": (
            "Environment overrides win over YAML; restart alone will not make YAML win."
            if shadowed else None
        ),
        "effective_admission_reason": capability_reason,
        "revision": _revision(raw),
        "restart_required": pending,
        "restart_pending": pending,
        "guidance": {
            "queue_workers": "Empirical starting guidance: 4–6; tune from queue delay and receipts.",
            "host_global_session_cap": "Empirical starting guidance: 11–13; not an aggregate host bound.",
            "enforced": False,
        },
        "authorization": "Local operator; daemon bearer required. Bearer authorization cannot be attributed to a verified person.",
    }


def _atomic_write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        if path.read_bytes() != raw:
            raise OSError("config read-back mismatch")
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def save(
    path: Path,
    running: Settings,
    *,
    expected_revision: str,
    queue_workers: int,
    host_global_session_cap: int,
    rationale: str,
    confirm_environment_shadow: bool,
    audit: Callable[[dict[str, Any]], None],
    capability_reason: str,
) -> dict[str, Any]:
    with _LOCK:
        old_raw, mapping = _read(path)
        current_revision = _revision(old_raw)
        if current_revision != expected_revision:
            raise CapacityConfigError("stale_revision", "capacity configuration changed; reload the latest snapshot")
        shadowed = _env_shadowed()
        if shadowed and not confirm_environment_shadow:
            raise CapacityConfigError("environment_confirmation_required", "confirm environment precedence before staging YAML")
        candidate = dict(mapping)
        candidate.update(queue_workers=queue_workers, host_global_session_cap=host_global_session_cap)
        try:
            Settings(**candidate)
        except Exception as exc:
            raise CapacityConfigError("invalid_settings_candidate", "staged values do not form valid daemon settings") from exc
        new_raw = yaml.safe_dump(candidate, sort_keys=False).encode()
        after_revision = _revision(new_raw)
        prior = {key: mapping.get(key) for key in CAPACITY_KEYS}
        event = {
            "prior": prior,
            "new": {"queue_workers": queue_workers, "host_global_session_cap": host_global_session_cap},
            "revision_before": current_revision,
            "revision_after": after_revision,
            "rationale": rationale,
            "outcome": "saved_for_next_restart",
            "provenance": "server-observed config.yaml and startup snapshot",
            "environment_shadowed": shadowed,
        }
        _atomic_write(path, new_raw)
        try:
            audit(event)
        except Exception:
            try:
                if old_raw is None:
                    path.unlink(missing_ok=True)
                    dir_fd = os.open(path.parent, os.O_RDONLY)
                    try:
                        os.fsync(dir_fd)
                    finally:
                        os.close(dir_fd)
                else:
                    _atomic_write(path, old_raw)
            except Exception as compensation:
                raise CapacityConfigError("audit_compensation_failed", "audit failed and authoritative config restoration failed") from compensation
            raise CapacityConfigError("audit_failed", "audit persistence failed; capacity configuration was not changed")
        result = snapshot(path, running, capability_reason=capability_reason)
        result["message"] = "Saved for next daemon restart; no running capacity was changed."
        return result
