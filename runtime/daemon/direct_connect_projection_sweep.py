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


def _sweep_once(store: DirectConnectAuthorityStore) -> None:
    """Project only the latest accepted candidate per parent lifecycle."""
    for operation_id in store.list_latest_operations_pending_projection():
        try:
            direct_connect_projection.project(store, operation_id)
        except Exception:
            logger.exception("direct-connect projection sweep failed for operation %s", operation_id)


async def direct_connect_projection_sweep_loop(
    state: DaemonState, *, interval_seconds: int = 3,
) -> None:
    """Periodically project direct-connect receipts on the daemon's authority store."""
    while True:
        t0 = time.monotonic()
        store = state.direct_connect_authority_store
        if store is None:
            logger.error("direct-connect projection sweep skipped: authority store unavailable")
        else:
            await asyncio.to_thread(_sweep_once, store)
        duration = time.monotonic() - t0
        state.metrics_registry.record_loop_tick(
            "direct_connect_projection_sweep", interval_seconds, duration,
        )
        await asyncio.sleep(interval_seconds)
