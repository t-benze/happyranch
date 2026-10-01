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

import threading
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore
from runtime.orchestrator.runtime_executor_store import load_runtime_profiles


_ACTIVE_PROJECTIONS: set[tuple[int, str]] = set()
_ACTIVE_PROJECTIONS_LOCK = threading.Lock()

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
        # A same-process owner may have claimed the operation immediately
        # before its durable plan insert. Keep this bounded and do not start a
        # second probe while that tiny publication window closes.
        for _ in range(50):
            threading.Event().wait(0.02)
            projection = store.get_projection(operation_id)
            if projection is not None:
                break
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
    store: DirectConnectAuthorityStore,
    operation_id: str,
    *,
    now: float | None = None,
    profile_coordinator=None,
) -> ProjectionOutcome:
    """Serialize same-process owners while durable planned rows stay retryable."""
    claim = (id(store), operation_id)
    with _ACTIVE_PROJECTIONS_LOCK:
        if claim in _ACTIVE_PROJECTIONS:
            return _await_concurrent_outcome(store, operation_id)
        _ACTIVE_PROJECTIONS.add(claim)
    try:
        return _project_once(
            store,
            operation_id,
            now=now,
            profile_coordinator=profile_coordinator,
        )
    finally:
        with _ACTIVE_PROJECTIONS_LOCK:
            _ACTIVE_PROJECTIONS.discard(claim)


def _project_once(
    store: DirectConnectAuthorityStore,
    operation_id: str,
    *,
    now: float | None = None,
    profile_coordinator=None,
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

    # Only the latest accepted candidate for this operation's parent authority
    # may be driven forward. Older candidates of the same parent are reported as
    # superseded without starting a probe; candidates of other authorities for
    # the same profile name are ignored.
    latest = store.get_latest_candidate_for_token_fingerprint(artifacts.token_fingerprint)
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

    if existing is None and not store.plan_projection(operation_id, now=now):
        # Another caller won the plan race between our read of `existing`
        # and now. Terminalize from its durable result when possible. A
        # durable planned row is intentionally resumable: a previous owner may
        # have lost ordinary profile-lease contention before any mutation.
        raced = store.get_projection(operation_id)
        if raced is None:
            raise RuntimeError(
                f"concurrent projection disappeared for operation {operation_id!r}"
            )
        if raced.state != "planned":
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

    def projection_is_terminal() -> bool:
        current = store.get_projection(operation_id)
        return current is not None and current.state in {"committed", "failed"}

    profile_span = (
        profile_coordinator.claimed_operation(
            [artifacts.intended_profile_name],
            operation_kind=(
                "rebind"
                if artifacts.intended_profile_name
                in load_runtime_profiles()
                else "register"
            ),
            publisher="direct_connect_projection",
            terminal_check=projection_is_terminal,
        )
        if profile_coordinator is not None
        else nullcontext(True)
    )
    with profile_span as owns_projection:
        if not owns_projection:
            # The cross-process winner terminalized while this caller waited
            # for the profile lease. Return its durable result without creating
            # another U1A operation, fence, generation, publication, or adapter
            # mutation.
            outcome = _await_concurrent_outcome(store, operation_id)
            if outcome.state == "planned":
                raise RuntimeError(
                    f"terminal projection claim disappeared for operation {operation_id!r}"
                )
            return outcome
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
            except Exception:
                if adapter_created:
                    remove_adapter(adapter_id)
                elif replaced_adapter is not None:
                    save_adapter(replaced_adapter)
                # The direct gate persists only a fixed category; arbitrary
                # exception text, paths, hashes, or candidate output must never
                # reach durable rows or the HTTP response.
                store.mark_failed(operation_id, "profile_binding_failed", now=now)
                return ProjectionOutcome(
                    state="failed", adapter_id=None, profile_name=None, reason="profile_binding_failed",
                )
        finally:
            release_store_lock()
        # Close the resumable planned window before releasing the profile
        # lease, so a later route/sweep caller observes the terminal winner
        # instead of redundantly publishing another profile generation.
        if not store.mark_committed(
            operation_id,
            adapter_id=adapter_id,
            profile_name=bind_result["profile_name"],
            now=now,
        ):
            # This is unreachable for supported callers: the terminal re-read,
            # durable U1A operation claim, and stable profile lease jointly own
            # the transition. Never ignore a lost CAS after mutation.
            raise RuntimeError(
                f"projection terminal ownership lost for operation {operation_id!r}"
            )
    return ProjectionOutcome(
        state="committed", adapter_id=adapter_id, profile_name=bind_result["profile_name"], reason=None,
    )
