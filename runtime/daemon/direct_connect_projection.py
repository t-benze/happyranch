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

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

@dataclass(frozen=True)
class ProjectionOutcome:
    state: Literal["planned", "committed", "failed"]
    adapter_id: str | None
    profile_name: str | None
    reason: str | None


def _await_concurrent_outcome(store: DirectConnectAuthorityStore, operation_id: str) -> ProjectionOutcome:
    """Reconcile a durable concurrent winner without starting another probe.

    A failed plan insert proves the winner has already durably created its
    projection row.  Returning that row's ``planned`` state keeps callers
    bounded while the owner continues the probe; terminal rows remain
    idempotent outcomes.
    """
    projection = store.get_projection(operation_id)
    if projection is None:
        raise RuntimeError(f"concurrent projection disappeared for operation {operation_id!r}")
    if projection.state == "committed":
        return ProjectionOutcome(
            state="committed", adapter_id=projection.adapter_id,
            profile_name=projection.profile_name, reason=None,
        )
    if projection.state == "failed":
        return ProjectionOutcome(state="failed", adapter_id=None, profile_name=None, reason=projection.reason)
    return ProjectionOutcome(state="planned", adapter_id=None, profile_name=None, reason=None)


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
        remove_adapter,
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

    # Only the latest accepted candidate for a parent may be driven forward.
    # Older candidates are reported as superseded without starting a probe.
    latest = store.get_latest_candidate_for_profile(artifacts.intended_profile_name)
    if latest is not None and latest.operation_id != operation_id:
        return ProjectionOutcome(
            state="failed", adapter_id=None, profile_name=None,
            reason="superseded_by_later_candidate",
        )

    # Enforce exactly one active probe per parent lifecycle.  If another
    # candidate of the same parent is already planned or being retried, report
    # this one as in-flight without racing it.
    active_other = store.active_operation_for_parent(operation_id)
    if active_other is not None:
        other_projection = store.get_projection(active_other)
        if other_projection is not None:
            return _await_concurrent_outcome(store, active_other)
        return ProjectionOutcome(state="planned", adapter_id=None, profile_name=None, reason=None)

    if not store.plan_projection(operation_id, now=now):
        # Another caller won the plan race between our read of `existing`
        # and now. Reconcile its durable state instead of racing the
        # conformance probe / durable writes a second time.
        return _await_concurrent_outcome(store, operation_id)

    adapter_id = custom_adapter_registry.generate_adapter_id(
        f"{artifacts.intended_profile_name}-adapter"
    )

    try:
        probe_output = custom_adapter_registry.run_conformance_probe(
            str(artifacts.wrapper_path), adapter_id, require_prompt_delivery=True,
        )
    except Exception:
        # The direct gate deliberately persists a category rather than any
        # candidate-controlled output, diagnostics, or per-probe canary.
        reason = "direct conformance probe failed"
        store.mark_failed(operation_id, f"conformance_probe_failed: {reason}", now=now)
        return ProjectionOutcome(state="failed", adapter_id=None, profile_name=None, reason=reason)

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
            bind_result = custom_adapter_registry._perform_adapter_profile_binding(
                adapter_id=adapter_id,
                profile_name=artifacts.intended_profile_name,
                workspace_adapter=artifacts.workspace_adapter_id,
            )
        except Exception as exc:
            if adapter_created:
                remove_adapter(adapter_id)
            elif replaced_adapter is not None:
                save_adapter(replaced_adapter)
            store.mark_failed(operation_id, f"profile_binding_failed: {exc}", now=now)
            return ProjectionOutcome(state="failed", adapter_id=None, profile_name=None, reason=str(exc))
    finally:
        release_store_lock()

    store.mark_committed(
        operation_id, adapter_id=adapter_id, profile_name=bind_result["profile_name"], now=now,
    )
    return ProjectionOutcome(
        state="committed", adapter_id=adapter_id, profile_name=bind_result["profile_name"], reason=None,
    )
