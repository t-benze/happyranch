"""THR-107 Slice 1: direct-connect projection coordinator.

Turns a durable, non-launchable direct-connect receipt into a durably
COMMITTED, launch-eligible custom-adapter executor profile. Reuses the
existing custom-adapter persistence primitives (adapter_store,
custom_adapter_registry._perform_adapter_profile_binding) rather than
inventing a second profile/registry write path — a direct-connect
adapter and a legacy founder-approved adapter are indistinguishable to
the launch fence (build_executor / resolve_adapter) once this
coordinator durably commits them.

Called only by two trusted paths: the master-bearer ``/commit`` route and the
daemon-owned periodic projection sweep. It is never invoked by receipt-only
``/connect``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

_CONCURRENT_WINNER_POLL_ATTEMPTS = 50
_CONCURRENT_WINNER_POLL_INTERVAL_SECONDS = 0.02


@dataclass(frozen=True)
class ProjectionOutcome:
    state: Literal["committed", "failed"]
    adapter_id: str | None
    profile_name: str | None
    reason: str | None


def _await_concurrent_outcome(store: DirectConnectAuthorityStore, operation_id: str) -> ProjectionOutcome | None:
    """Poll a bounded number of times for a concurrent winner's terminal state.

    Returns ``None`` if no terminal state is observed within the bound —
    the caller should treat that as its own failure rather than hang.
    """
    for _ in range(_CONCURRENT_WINNER_POLL_ATTEMPTS):
        projection = store.get_projection(operation_id)
        if projection is not None and projection.state == "committed":
            return ProjectionOutcome(
                state="committed", adapter_id=projection.adapter_id,
                profile_name=projection.profile_name, reason=None,
            )
        if projection is not None and projection.state == "failed":
            return ProjectionOutcome(state="failed", adapter_id=None, profile_name=None, reason=projection.reason)
        time.sleep(_CONCURRENT_WINNER_POLL_INTERVAL_SECONDS)
    return None


def project(
    store: DirectConnectAuthorityStore, operation_id: str, *, now: float | None = None
) -> ProjectionOutcome:
    """Drive one direct-connect receipt to COMMITTED, or fail closed.

    Idempotent: if this operation is already committed or failed, returns
    the existing outcome without redoing any work. Every failure path
    compensates so no partial adapter/profile/registry state survives.
    """
    from runtime.orchestrator import custom_adapter_registry
    from runtime.orchestrator.adapter_store import (
        AdapterEntry,
        acquire_store_lock,
        get_adapter,
        release_store_lock,
        save_adapter,
    )

    existing = store.get_projection(operation_id)
    if existing is not None and existing.state == "committed":
        return ProjectionOutcome(
            state="committed", adapter_id=existing.adapter_id,
            profile_name=existing.profile_name, reason=None,
        )
    if existing is not None and existing.state == "failed":
        return ProjectionOutcome(state="failed", adapter_id=None, profile_name=None, reason=existing.reason)

    artifacts = store.get_receipt_artifacts(operation_id)
    if artifacts is None:
        raise RuntimeError(f"no receipt found for direct-connect operation {operation_id!r}")

    if not store.plan_projection(operation_id, now=now):
        # Another caller won the plan race between our read of `existing`
        # and now. Poll for its terminal result instead of racing the
        # conformance probe / durable writes a second time.
        outcome = _await_concurrent_outcome(store, operation_id)
        if outcome is not None:
            return outcome
        # No terminal state showed up within the bound — surface as a
        # failure rather than silently proceeding past a live winner.
        return ProjectionOutcome(
            state="failed", adapter_id=None, profile_name=None,
            reason="concurrent projection did not reach a terminal state in time",
        )

    adapter_id = custom_adapter_registry.generate_adapter_id(
        f"{artifacts.intended_profile_name}-adapter"
    )

    try:
        probe_output = custom_adapter_registry.run_conformance_probe(
            str(artifacts.wrapper_path), adapter_id
        )
    except Exception as exc:
        store.mark_failed(operation_id, f"conformance_probe_failed: {exc}", now=now)
        return ProjectionOutcome(state="failed", adapter_id=None, profile_name=None, reason=str(exc))

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
        registered_by="direct-connect",
        approved_at=datetime.now(timezone.utc).isoformat(),
        approved_by="direct-connect",
        intended_profile_name=artifacts.intended_profile_name,
        dependency_manifest_version=1,
        dependencies=[{"executable": c["executable"], "sha256": c["sha256"]} for c in artifacts.children],
    )

    acquire_store_lock()
    adapter_created = False
    replaced_adapter: AdapterEntry | None = None
    try:
        existing_adapter = get_adapter(adapter_id)
        if existing_adapter is None:
            save_adapter(entry)
            adapter_created = True
        elif existing_adapter.executable_hash != entry.executable_hash:
            save_adapter(entry)
            replaced_adapter = existing_adapter
    finally:
        release_store_lock()

    try:
        bind_result = custom_adapter_registry._perform_adapter_profile_binding(
            adapter_id=adapter_id,
            profile_name=artifacts.intended_profile_name,
            workspace_adapter=artifacts.workspace_adapter_id,
        )
    except Exception as exc:
        if adapter_created:
            from runtime.orchestrator.adapter_store import remove_adapter

            remove_adapter(adapter_id)
        elif replaced_adapter is not None:
            from runtime.orchestrator.adapter_store import save_adapter

            save_adapter(replaced_adapter)
        store.mark_failed(operation_id, f"profile_binding_failed: {exc}", now=now)
        return ProjectionOutcome(state="failed", adapter_id=None, profile_name=None, reason=str(exc))

    store.mark_committed(
        operation_id, adapter_id=adapter_id, profile_name=bind_result["profile_name"], now=now,
    )
    return ProjectionOutcome(
        state="committed", adapter_id=adapter_id, profile_name=bind_result["profile_name"], reason=None,
    )
