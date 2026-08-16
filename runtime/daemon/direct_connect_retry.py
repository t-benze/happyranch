"""Retry validation for an immutable terminal-failed direct-connect receipt."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore, DirectConnectReceiptArtifacts
from runtime.daemon.routes.direct_connect import _artifact_facts

_CONCURRENT_RETRY_POLL_ATTEMPTS = 50
_CONCURRENT_RETRY_POLL_INTERVAL_SECONDS = 0.02


@dataclass(frozen=True)
class RetryValidationOutcome:
    state: Literal["committed", "failed"]
    adapter_id: str | None
    profile_name: str | None
    reason: str | None


def _validate_persisted_snapshot(artifacts: DirectConnectReceiptArtifacts) -> None:
    """Fail closed against exactly the receipt's stored wrapper and children."""
    if len(artifacts.wrapper_sha256) != 64 or not artifacts.children:
        raise ValueError("snapshot_invalid")
    wrapper_hash, _ = _artifact_facts(artifacts.wrapper_path, expected_path=artifacts.wrapper_path)
    if wrapper_hash != artifacts.wrapper_sha256:
        raise ValueError("artifact_validation_failed")
    slots: set[str] = set()
    paths: set[Path] = set()
    for child in artifacts.children:
        slot = child.get("slot")
        executable = child.get("executable")
        expected_hash = child.get("sha256")
        if (
            not isinstance(slot, str) or not slot or slot in slots
            or not isinstance(executable, str) or not executable
            or not isinstance(expected_hash, str) or len(expected_hash) != 64
        ):
            raise ValueError("snapshot_invalid")
        child_path = Path(executable)
        if child_path in paths or child_path == artifacts.wrapper_path:
            raise ValueError("snapshot_invalid")
        child_hash, _ = _artifact_facts(child_path)
        if child_hash != expected_hash:
            raise ValueError("artifact_validation_failed")
        slots.add(slot)
        paths.add(child_path)


def _await_running_retry(
    store: DirectConnectAuthorityStore, attempt_id: str,
) -> RetryValidationOutcome:
    for _ in range(_CONCURRENT_RETRY_POLL_ATTEMPTS):
        attempt = store.get_retry_attempt(attempt_id)
        if attempt is not None and attempt.state == "succeeded":
            return RetryValidationOutcome(
                state="committed", adapter_id=attempt.adapter_id,
                profile_name=attempt.profile_name, reason=None,
            )
        if attempt is not None and attempt.state == "failed":
            return RetryValidationOutcome(state="failed", adapter_id=None, profile_name=None, reason=attempt.reason)
        time.sleep(_CONCURRENT_RETRY_POLL_INTERVAL_SECONDS)
    return RetryValidationOutcome(
        state="failed", adapter_id=None, profile_name=None, reason="concurrent_retry_incomplete",
    )


def _bind_persisted_snapshot(artifacts: DirectConnectReceiptArtifacts, probe_output: object) -> tuple[str, str]:
    """Use the projection's approved adapter/profile persistence primitives."""
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import (
        AdapterEntry,
        acquire_store_lock,
        get_adapter,
        remove_adapter,
        release_store_lock,
        save_adapter,
    )

    adapter_id = custom_adapter_registry.generate_adapter_id(f"{artifacts.intended_profile_name}-adapter")
    entry = AdapterEntry(
        id=adapter_id,
        name=artifacts.intended_profile_name,
        executable=str(artifacts.wrapper_path),
        executable_hash=artifacts.wrapper_sha256,
        version=probe_output.adapter_metadata.adapter_version,
        capabilities=[],
        contract_version=probe_output.adapter_metadata.contract_version,
        workspace_adapter=artifacts.workspace_adapter_id,
        status="approved",
        registered_at=datetime.now(timezone.utc).isoformat(),
        registered_by="direct-connect-retry",
        approved_at=datetime.now(timezone.utc).isoformat(),
        approved_by="direct-connect-retry",
        intended_profile_name=artifacts.intended_profile_name,
        dependency_manifest_version=1,
        dependencies=[{"executable": c["executable"], "sha256": c["sha256"]} for c in artifacts.children],
    )
    adapter_created = False
    replaced_adapter: AdapterEntry | None = None
    acquire_store_lock()
    try:
        existing_adapter = get_adapter(adapter_id)
        if existing_adapter is None:
            save_adapter(entry)
            adapter_created = True
        elif existing_adapter.executable_hash != entry.executable_hash:
            save_adapter(entry)
            replaced_adapter = existing_adapter
        try:
            bound = custom_adapter_registry._perform_adapter_profile_binding(
                adapter_id=adapter_id,
                profile_name=artifacts.intended_profile_name,
                workspace_adapter=artifacts.workspace_adapter_id,
            )
        except Exception:
            if adapter_created:
                remove_adapter(adapter_id)
            elif replaced_adapter is not None:
                save_adapter(replaced_adapter)
            raise
    finally:
        release_store_lock()
    return adapter_id, bound["profile_name"]


def retry_validate(
    store: DirectConnectAuthorityStore, operation_id: str, *, now: float | None = None,
) -> RetryValidationOutcome:
    """Probe and bind only a terminal-failed receipt's exact stored snapshot."""
    from runtime.orchestrator import custom_adapter_registry

    attempt, claimed = store.claim_retry_attempt(operation_id, now=now)
    if not claimed:
        if attempt.state == "succeeded":
            return RetryValidationOutcome(
                state="committed", adapter_id=attempt.adapter_id,
                profile_name=attempt.profile_name, reason=None,
            )
        return _await_running_retry(store, attempt.attempt_id)

    artifacts = store.get_receipt_artifacts(operation_id)
    if artifacts is None:
        store.finish_retry_attempt(attempt.attempt_id, state="failed", reason="snapshot_invalid", now=now)
        return RetryValidationOutcome(state="failed", adapter_id=None, profile_name=None, reason="snapshot_invalid")
    try:
        _validate_persisted_snapshot(artifacts)
    except Exception as exc:
        reason = str(exc) if str(exc) in {"snapshot_invalid", "artifact_validation_failed"} else "artifact_validation_failed"
        store.finish_retry_attempt(attempt.attempt_id, state="failed", reason=reason, now=now)
        return RetryValidationOutcome(state="failed", adapter_id=None, profile_name=None, reason=reason)
    adapter_id = custom_adapter_registry.generate_adapter_id(f"{artifacts.intended_profile_name}-adapter")
    try:
        probe_output = custom_adapter_registry.run_conformance_probe(
            str(artifacts.wrapper_path), adapter_id, require_prompt_delivery=True,
        )
    except Exception:
        store.finish_retry_attempt(attempt.attempt_id, state="failed", reason="conformance_probe_failed", now=now)
        return RetryValidationOutcome(
            state="failed", adapter_id=None, profile_name=None, reason="conformance_probe_failed",
        )
    try:
        adapter_id, profile_name = _bind_persisted_snapshot(artifacts, probe_output)
    except Exception:
        store.finish_retry_attempt(attempt.attempt_id, state="failed", reason="profile_binding_failed", now=now)
        return RetryValidationOutcome(
            state="failed", adapter_id=None, profile_name=None, reason="profile_binding_failed",
        )
    store.finish_retry_attempt(
        attempt.attempt_id, state="succeeded", adapter_id=adapter_id, profile_name=profile_name, now=now,
    )
    return RetryValidationOutcome(state="committed", adapter_id=adapter_id, profile_name=profile_name, reason=None)
