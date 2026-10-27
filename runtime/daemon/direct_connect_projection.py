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
from concurrent.futures import Future
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


def _selected_validation(store, profiles):
    """Capture one authentic production attachment, or the standalone default."""
    from runtime.daemon.startup_recovery import maintenance_admission_validation, StartupUnavailable
    owner = getattr(store, "_projection_startup_owner", None)
    refs = getattr(store, "_projection_startup_refs", None)
    profile_owner = getattr(profiles, "_projection_startup_owner", None)
    if owner is None and refs is None and profile_owner is None:
        return lambda: None, None
    if (owner is None or not isinstance(refs, tuple) or len(refs) != 4
            or refs[0] is not owner or refs[1] is None or refs[2] is not store
            or refs[3] is not profiles or profiles is None or profile_owner is not owner
            or getattr(owner, "_startup_recovery", None) is not refs[1]
            or getattr(owner, "direct_connect_authority_store", None) is not store
            or getattr(owner, "profile_coordinator", None) is not profiles):
        raise StartupUnavailable("startup_projection_attachment_changed")
    gate = refs[1]
    validate_state = maintenance_admission_validation(owner)

    def validate():
        validate_state()
        if (getattr(store, "_projection_startup_owner", None) is not owner
                or getattr(store, "_projection_startup_refs", None) is not refs
                or owner._startup_recovery is not gate
                or owner.direct_connect_authority_store is not store
                or owner.profile_coordinator is not profiles
                or getattr(profiles, "_projection_startup_owner", None) is not owner):
            raise StartupUnavailable("startup_projection_attachment_changed")
    validate()
    return validate, owner


def project(
    store: DirectConnectAuthorityStore,
    operation_id: str,
    *,
    now: float | None = None,
    profile_coordinator=None,
    validate_admission=None,
) -> ProjectionOutcome:
    """Serialize same-process owners while durable planned rows stay retryable."""
    selected, owner = _selected_validation(store, profile_coordinator)

    def validate():
        selected()
        if validate_admission is not None:
            validate_admission()
        # A supplied callback cannot replace or revoke the bound selection.
        selected()

    validate()
    original = Future() if owner is not None else None
    if original is not None:
        original.set_running_or_notify_cancel()
        with owner._maintenance_workers_lock:
            owner._maintenance_workers.add(original)
    claim = (id(store), operation_id)
    claimed = False
    try:
        with _ACTIVE_PROJECTIONS_LOCK:
            validate()
            claimed = claim not in _ACTIVE_PROJECTIONS
            if claimed:
                _ACTIVE_PROJECTIONS.add(claim)
        if claimed:
            outcome = _project_once(
                store,
                operation_id,
                now=now,
                profile_coordinator=profile_coordinator,
                validate_admission=(validate if owner is not None or validate_admission is not None else None),
            )
        else:
            outcome = _await_concurrent_outcome(store, operation_id)
            validate()
        if original is not None:
            original.set_result(outcome)
        return outcome
    except BaseException as exc:
        if original is not None:
            # Preserve the actual failure and Process custody, even if the
            # request wrapper is cancelled or the post-submit handoff fails.
            original.set_exception(exc)
        raise
    finally:
        if claimed:
            with _ACTIVE_PROJECTIONS_LOCK:
                _ACTIVE_PROJECTIONS.discard(claim)
        if original is not None:
            owner._retire_maintenance_worker(original)


def _project_once(
    store: DirectConnectAuthorityStore,
    operation_id: str,
    *,
    now: float | None = None,
    profile_coordinator=None,
    validate_admission=None,
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

    validate = validate_admission or (lambda: None)
    validate()
    existing = store.get_projection(operation_id)
    validate()
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

    validate()
    with store._lock:
        validate()
        if existing is None and validate_admission is not None:
            # The original plan method still owns its INSERT and COMMIT. Take
            # its SQLite writer admission first so closure during that wait is
            # observed before the fresh plan, rather than after commitment.
            began = not store._conn.in_transaction
            try:
                if began:
                    store._conn.execute("BEGIN IMMEDIATE")
                validate()
                planned = store.plan_projection(operation_id, now=now)
            except BaseException as exc:
                if began:
                    try:
                        store._conn.rollback()
                    except BaseException as secondary:
                        exc.add_note("projection plan rollback also failed: " + repr(secondary)[:512])
                raise
        else:
            planned = existing is not None or store.plan_projection(operation_id, now=now)
    if not planned:
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

    validate()
    adapter_id = custom_adapter_registry.generate_adapter_id(
        f"{artifacts.intended_profile_name}-adapter"
    )

    try:
        probe_output = custom_adapter_registry.run_conformance_probe(
            str(artifacts.wrapper_path), adapter_id, require_prompt_delivery=True,
            **({"validate_admission": validate_admission} if validate_admission is not None else {}),
        )
    except Exception as exc:
        from runtime.daemon.startup_recovery import StartupUnavailable
        if isinstance(exc, StartupUnavailable) or getattr(exc, "maintenance_native_unsettled", False):
            raise
        # The direct gate deliberately persists a category rather than any
        # candidate-controlled output, diagnostics, or per-probe canary.
        reason = "direct conformance probe failed"
        with store._lock:
            validate()
            store.mark_failed(operation_id, f"conformance_probe_failed: {reason}", now=now)
        return ProjectionOutcome(state="failed", adapter_id=None, profile_name=None, reason=reason)

    validate()
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
            **({"validate_admission": validate_admission} if validate_admission is not None else {}),
        )
        if profile_coordinator is not None
        else nullcontext(True)
    )
    validate()
    with profile_span as owns_projection:
        validate()
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
            validate()
            existing_adapter = get_adapter(adapter_id)
            validate()
            if existing_adapter is None:
                save_adapter(entry)
                adapter_created = True
            elif existing_adapter.executable_hash != entry.executable_hash:
                save_adapter(entry)
                replaced_adapter = existing_adapter
            try:
                validate()
                bind_result = custom_adapter_registry._perform_adapter_profile_binding(
                    adapter_id=adapter_id,
                    profile_name=artifacts.intended_profile_name,
                    workspace_adapter=artifacts.workspace_adapter_id,
                    **({"validate_admission": validate_admission} if validate_admission is not None else {}),
                )
            except Exception as exc:
                if adapter_created:
                    remove_adapter(adapter_id)
                elif replaced_adapter is not None:
                    save_adapter(replaced_adapter)
                from runtime.daemon.startup_recovery import StartupUnavailable
                if isinstance(exc, StartupUnavailable):
                    raise
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
