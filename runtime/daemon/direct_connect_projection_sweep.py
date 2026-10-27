"""THR-160 daemon-owned periodic direct-connect projection recovery.

The receipt-only ``/connect`` boundary never invokes this module. Instead,
the daemon periodically projects unprojected receipts so browser closure cannot
leave a direct-connect operation permanently nonlaunchable.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from runtime.daemon import direct_connect_projection
from runtime.daemon.direct_connect_store import DirectConnectAuthorityStore

if TYPE_CHECKING:
    from runtime.daemon.state import DaemonState


logger = logging.getLogger("happyranch.daemon.direct_connect_projection_sweep")


def bind_startup_references(state):
    """Reference-only attachment for the selected periodic/direct sweep owner."""
    store = state.direct_connect_authority_store
    if store is None:
        return
    from runtime.daemon.startup_recovery import StartupUnavailable
    owner = getattr(store, "_projection_startup_owner", None)
    previous = getattr(store, "_projection_startup_refs", None)
    if (owner is not None and owner is not state) or (previous is not None and (
            not isinstance(previous, tuple) or len(previous) != 4
            or previous[0] is not state or previous[1] is not state._startup_recovery)):
        raise StartupUnavailable("startup_projection_attachment_changed")
    profiles = state.profile_coordinator
    profile_owner = getattr(profiles, "_projection_startup_owner", None)
    if profile_owner is not None and profile_owner is not state:
        raise StartupUnavailable("startup_projection_attachment_changed")
    if profiles is not None:
        profiles._projection_startup_owner = state
    store._projection_startup_owner = state
    store._projection_startup_refs = (state, state._startup_recovery, store, state.profile_coordinator)


def _sweep_once(
    store: DirectConnectAuthorityStore,
    profile_coordinator=None,
    *, validate_admission=None,
) -> None:
    """Project only the latest accepted candidate per parent lifecycle."""
    selected, owner = direct_connect_projection._selected_validation(store, profile_coordinator)
    def validate():
        selected()
        if validate_admission is not None:
            validate_admission()
    validate()
    selected_kwargs = ({"validate_admission": validate}
        if validate_admission is not None or owner is not None else {})
    pending = store.list_latest_operations_pending_projection()
    validate()
    for operation_id in pending:
        validate()
        try:
            if profile_coordinator is None:
                direct_connect_projection.project(store, operation_id, **selected_kwargs)
            else:
                direct_connect_projection.project(
                    store,
                    operation_id,
                    profile_coordinator=profile_coordinator,
                    **selected_kwargs,
                )
        except Exception as exc:
            from runtime.daemon.startup_recovery import StartupUnavailable
            if isinstance(exc, StartupUnavailable) or getattr(exc, "maintenance_native_unsettled", False):
                raise
            logger.exception("direct-connect projection sweep failed for operation %s", operation_id)


async def direct_connect_projection_sweep_loop(
    state: DaemonState, *, interval_seconds: int = 3,
) -> None:
    """Periodically project direct-connect receipts on the daemon's authority store."""
    from runtime.daemon.startup_recovery import (
        maintenance_admission_validation, maintenance_service_validation,
        maintenance_runtime_replaced, StartupUnavailable,
    )
    validate_service = maintenance_service_validation(state)
    while True:
        validate_service()
        t0 = time.monotonic()
        runtime = state.runtime
        validate_state = maintenance_admission_validation(state)
        store, profiles = state.direct_connect_authority_store, state.profile_coordinator
        def validate():
            validate_state()
            if state.direct_connect_authority_store is not store or state.profile_coordinator is not profiles:
                raise StartupUnavailable("startup_projection_attachment_changed")
        try:
            if store is None:
                logger.error("direct-connect projection sweep skipped: authority store unavailable")
            else:
                validate()
                worker = asyncio.create_task(asyncio.to_thread(
                    _sweep_once, store, profiles, validate_admission=validate))
                await state._await_maintenance_worker(worker)
                validate()
        except StartupUnavailable as exc:
            if not maintenance_runtime_replaced(state, runtime, validate_service, exc):
                raise
            logger.warning("direct-connect projection selected runtime retired: %s", exc)
        duration = time.monotonic() - t0
        state.metrics_registry.record_loop_tick(
            "direct_connect_projection_sweep", interval_seconds, duration,
        )
        await asyncio.sleep(interval_seconds)
        validate_service()
