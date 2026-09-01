"""Paired, next-restart-only daemon capacity configuration.

This module deliberately owns only ``queue_workers`` and
``host_global_session_cap``.  It is not a generic YAML editing surface.
"""
from __future__ import annotations

import hashlib
import inspect
import os
import tempfile
import threading
from enum import Enum, auto
from pathlib import Path
from typing import Any, Callable

import yaml

from runtime.config import Settings

CAPACITY_KEYS = ("queue_workers", "host_global_session_cap")
_LOCK = threading.RLock()


class CapacityConfigError(RuntimeError):
    def __init__(self, code: str, message: str, *, artifact_state: str | None = None):
        super().__init__(message)
        self.code = code
        self.artifact_state = artifact_state


class _PublicationUncertain(RuntimeError):
    """The authoritative replace happened, but durability/verification failed."""

    def __init__(self, *, artifact_state: str, cleanup_failed: bool = False):
        super().__init__(artifact_state)
        self.artifact_state = artifact_state
        self.cleanup_failed = cleanup_failed


class _WriteFailed(RuntimeError):
    """A pre-publication failure with observed temporary-artifact state."""

    def __init__(self, *, artifact_state: str):
        super().__init__(artifact_state)
        self.artifact_state = artifact_state


class _PublicationState(Enum):
    """Ordered states of the complete audited publication transaction."""

    INITIAL = auto()
    AUTHORITATIVE_READ = auto()
    SERIALIZED = auto()
    AUDIT_AUTHORIZED = auto()
    PARENT_READY = auto()
    TEMP_CREATED = auto()
    TEMP_CLOSED = auto()
    PUBLISHED = auto()
    DIRECTORY_DURABLE = auto()
    VERIFIED = auto()
    CLEANUP_COMPLETE = auto()
    SNAPSHOT_COMPLETE = auto()
    RETURNED = auto()


class _PublicationTransaction:
    def __init__(self) -> None:
        self.state = _PublicationState.INITIAL

    def advance(self, expected: _PublicationState, target: _PublicationState) -> None:
        if self.state is not expected:
            raise RuntimeError(f"invalid publication transition: {self.state.name} -> {target.name}")
        self.state = target

    @property
    def published(self) -> bool:
        return self.state.value >= _PublicationState.PUBLISHED.value


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


def snapshot(
    path: Path,
    running: Settings,
    *,
    capacity_context: dict[str, Any] | None = None,
    capability_reason: str | None = None,
) -> dict[str, Any]:
    capacity_context = capacity_context or {
        "producer_envelope": running.queue_workers + 7,
        "producer_components": {
            "task_workers": running.queue_workers,
            "thread_workers": 4,
            "dream_workers": 1,
            "wake_workers": 1,
            "schedule_workers": 1,
        },
        "effective_admission_cap": running.host_global_session_cap,
        "effective_admission_reason": capability_reason or "Capability state unavailable.",
    }
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
        "producer_envelope": capacity_context["producer_envelope"],
        "producer_components": capacity_context["producer_components"],
        "effective_admission_cap": capacity_context["effective_admission_cap"],
        "effective_admission_reason": capacity_context["effective_admission_reason"],
        "revision": _revision(raw),
        "restart_required": pending,
        "restart_pending": pending,
        "guidance": {
            "queue_workers": "Empirical starting guidance: 4–6; tune from queue delay and receipts.",
            "host_global_session_cap": "Empirical starting guidance: 11–13; not an aggregate host bound.",
            "enforced": False,
        },
        "authorization": "Local operator; daemon bearer required. Bearer authorization cannot be attributed to a verified person.",
        "warnings": _capacity_warnings(
            next_start["host_global_session_cap"], next_start["queue_workers"] + 7
        ),
    }


def _capacity_warnings(cap: int, envelope: int) -> list[str]:
    if cap < envelope:
        return [
            f"Intentional backpressure: host cap {cap} is below producer envelope {envelope}; "
            "some producer slots cannot run concurrently. Saving remains permitted."
        ]
    if cap > envelope:
        return [
            f"Host cap {cap} is above producer envelope {envelope}; unused admission capacity "
            "does not create additional producers."
        ]
    return []


def _verify_published_revision(actual: bytes, expected: bytes) -> None:
    if _revision(actual) != _revision(expected):
        raise OSError("published config revision mismatch")


def _verify_published_values(mapping: Any, expected: dict[str, int]) -> None:
    if not isinstance(mapping, dict) or any(mapping.get(key) != value for key, value in expected.items()):
        raise OSError("published capacity values mismatch")


def _atomic_write(
    path: Path,
    raw: bytes,
    *,
    expected: dict[str, int],
    transaction: _PublicationTransaction,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    transaction.advance(_PublicationState.AUDIT_AUTHORIZED, _PublicationState.PARENT_READY)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    transaction.advance(_PublicationState.PARENT_READY, _PublicationState.TEMP_CREATED)
    failure: Exception | None = None
    artifact_state = "unknown"
    cleanup_failed = False
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        transaction.advance(_PublicationState.TEMP_CREATED, _PublicationState.TEMP_CLOSED)
        os.replace(tmp_name, path)
        transaction.advance(_PublicationState.TEMP_CLOSED, _PublicationState.PUBLISHED)
        try:
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
            transaction.advance(_PublicationState.PUBLISHED, _PublicationState.DIRECTORY_DURABLE)
            published_raw = path.read_bytes()
            published_mapping = yaml.safe_load(published_raw)
            _verify_published_revision(published_raw, raw)
            _verify_published_values(published_mapping, expected)
            transaction.advance(_PublicationState.DIRECTORY_DURABLE, _PublicationState.VERIFIED)
        except Exception as exc:
            failure = exc
    except Exception as exc:
        failure = exc
    finally:
        try:
            if os.path.exists(tmp_name):
                artifact_state = "present"
                os.unlink(tmp_name)
                artifact_state = "absent"
            else:
                artifact_state = "absent"
            if failure is None:
                transaction.advance(_PublicationState.VERIFIED, _PublicationState.CLEANUP_COMPLETE)
        except Exception as exc:
            cleanup_failed = True
            failure = exc

    if failure is not None:
        if transaction.published:
            # Publication is authoritative after os.replace. No cleanup or
            # verification exception may relabel it as a safe pre-write
            # failure, compensate it, or emit another authorization audit.
            raise _PublicationUncertain(
                artifact_state=artifact_state,
                cleanup_failed=cleanup_failed,
            ) from failure
        raise _WriteFailed(artifact_state=artifact_state) from failure


def save(
    path: Path,
    running: Settings,
    *,
    expected_revision: str,
    queue_workers: int,
    host_global_session_cap: int,
    rationale: str,
    confirm_environment_shadow: bool,
    audit: Callable[[str, dict[str, Any]], None],
    capacity_context: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    capability_reason: str | None = None,
) -> dict[str, Any]:
    capacity_context = capacity_context or {
        "producer_envelope": running.queue_workers + 7,
        "producer_components": {
            "task_workers": running.queue_workers,
            "thread_workers": 4,
            "dream_workers": 1,
            "wake_workers": 1,
            "schedule_workers": 1,
        },
        "effective_admission_cap": running.host_global_session_cap,
        "effective_admission_reason": capability_reason or "Capability state unavailable.",
    }
    with _LOCK:
        transaction = _PublicationTransaction()
        requested = {"queue_workers": queue_workers, "host_global_session_cap": host_global_session_cap}
        old_raw: bytes | None = None
        mapping: dict[str, Any] = {}
        current_revision: str | None = None
        after_revision: str | None = None
        prior = {key: None for key in CAPACITY_KEYS}
        base_event = {
            "prior": prior,
            "new": requested,
            "revision_before": current_revision,
            "revision_after": after_revision,
            "rationale": rationale,
            "provenance": "server-observed config.yaml and startup snapshot",
        }
        if correlation_id is not None:
            base_event["correlation_id"] = correlation_id
        try:
            old_raw, mapping = _read(path)
        except CapacityConfigError as exc:
            terminal = {**base_event, "outcome": "rejected", "error_class": exc.code}
            try:
                _emit_audit(audit, "daemon_capacity_config_rejected", terminal)
            except Exception as audit_exc:
                raise CapacityConfigError("audit_failed", "audit persistence failed; capacity configuration was not changed") from audit_exc
            raise
        transaction.advance(_PublicationState.INITIAL, _PublicationState.AUTHORITATIVE_READ)
        current_revision = _revision(old_raw)
        prior = {key: mapping.get(key) for key in CAPACITY_KEYS}
        base_event.update(prior=prior, revision_before=current_revision)
        if current_revision != expected_revision:
            exc = CapacityConfigError("stale_revision", "capacity configuration changed; reload the latest snapshot")
            _emit_audit(audit, "daemon_capacity_config_rejected", {**base_event, "outcome": "rejected", "error_class": exc.code})
            raise exc
        shadowed = _env_shadowed()
        if shadowed and not confirm_environment_shadow:
            exc = CapacityConfigError("environment_confirmation_required", "confirm environment precedence before staging YAML")
            _emit_audit(audit, "daemon_capacity_config_rejected", {**base_event, "outcome": "rejected", "error_class": exc.code})
            raise exc
        candidate = dict(mapping)
        candidate.update(queue_workers=queue_workers, host_global_session_cap=host_global_session_cap)
        try:
            Settings(**candidate)
        except Exception as exc:
            rejected = CapacityConfigError("invalid_settings_candidate", "staged values do not form valid daemon settings")
            _emit_audit(audit, "daemon_capacity_config_rejected", {**base_event, "outcome": "rejected", "error_class": rejected.code})
            raise rejected from exc
        new_raw = yaml.safe_dump(candidate, sort_keys=False).encode()
        transaction.advance(_PublicationState.AUTHORITATIVE_READ, _PublicationState.SERIALIZED)
        after_revision = _revision(new_raw)
        base_event.update(revision_after=after_revision)
        event = {
            **base_event,
            # This row is durable before replacement, so it records only the
            # truth available at that instant. It never fabricates completed
            # application or verified-person attribution.
            "outcome": "validated_write_authorized",
            "provenance": "server-observed config.yaml and startup snapshot",
            "environment_shadowed": shadowed,
        }
        try:
            _emit_audit(audit, "daemon_capacity_config_write_authorized", event)
        except Exception as exc:
            raise CapacityConfigError("audit_failed", "audit persistence failed; capacity configuration was not changed")
        transaction.advance(_PublicationState.SERIALIZED, _PublicationState.AUDIT_AUTHORIZED)
        try:
            _atomic_write(
                path,
                new_raw,
                expected={
                    "queue_workers": queue_workers,
                    "host_global_session_cap": host_global_session_cap,
                },
                transaction=transaction,
            )
        except _PublicationUncertain as exc:
            message = (
                "capacity configuration was published, but durability, verification, or cleanup did not complete; "
                "reload and inspect the authoritative configuration and temporary artifact state before retrying"
                if exc.cleanup_failed else
                "capacity configuration was published, but durability or verification did not complete; "
                "reload and inspect the authoritative configuration before retrying"
            )
            terminal = {**base_event, "outcome": "config_publication_uncertain", "error_class": "config_publication_uncertain"}
            try:
                _emit_audit(audit, "daemon_capacity_config_publication_uncertain", terminal)
            except Exception:
                pass
            raise CapacityConfigError(
                "config_publication_uncertain",
                message,
                artifact_state=exc.artifact_state,
            ) from exc
        except _WriteFailed as exc:
            try:
                _emit_audit(audit, "daemon_capacity_config_failed", {**base_event, "outcome": "failed", "error_class": "config_write_failed"})
            except Exception as audit_exc:
                raise CapacityConfigError("audit_failed", "audit persistence failed; capacity configuration was not changed") from audit_exc
            raise CapacityConfigError(
                "config_write_failed",
                "capacity configuration replacement failed; the prior authoritative bytes remain in use",
                artifact_state=exc.artifact_state,
            ) from exc
        except Exception as exc:
            raise CapacityConfigError("config_write_failed", "capacity configuration replacement failed") from exc
        try:
            result = snapshot(path, running, capacity_context=capacity_context)
        except Exception as exc:
            # The replace and immediate verification succeeded, but the
            # mutation transaction does not end until its response snapshot
            # is constructed. Any failure here still requires reconciliation.
            try:
                _emit_audit(audit, "daemon_capacity_config_publication_uncertain", {**base_event, "outcome": "config_publication_uncertain", "error_class": "snapshot_failed"})
            except Exception:
                pass
            raise CapacityConfigError(
                "config_publication_uncertain",
                "capacity configuration was published, but durability or verification did not complete; reload and inspect the authoritative configuration before retrying",
                artifact_state="absent",
            ) from exc
        transaction.advance(_PublicationState.CLEANUP_COMPLETE, _PublicationState.SNAPSHOT_COMPLETE)
        try:
            _emit_audit(audit, "daemon_capacity_config_succeeded", {**base_event, "outcome": "succeeded", "error_class": None})
        except Exception as exc:
            raise CapacityConfigError(
                "config_publication_uncertain",
                "capacity configuration was published, but its terminal audit receipt could not be persisted; reload and inspect before retrying",
                artifact_state="absent",
            ) from exc
        result["message"] = "Saved for next daemon restart; no running capacity was changed."
        transaction.advance(_PublicationState.SNAPSHOT_COMPLETE, _PublicationState.RETURNED)
        return result


def _emit_audit(audit: Callable[..., None], action: str, payload: dict[str, Any]) -> None:
    """Use the action-aware daemon callback; retain one-argument test fakes."""
    if len(inspect.signature(audit).parameters) == 1:
        if action == "daemon_capacity_config_write_authorized":
            audit(payload)
    else:
        audit(action, payload)
