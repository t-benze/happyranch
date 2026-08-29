"""TASK-5966 strict mention-led exchange — bounded-hold reaper.

The exchange's absolute fail-open bound (``MAX_PRIORITY_WAIT_SECONDS`` = 4h,
founder-approved G1) and the quiescence+grace idle close (``EXCHANGE_GRACE``
= 5 min, founder verdict) are enforced by this periodic loop, mirroring the
``zombie_reaper_loop`` pattern: 30s tick, per-org iteration, metrics
recording. Every sweep is a store-owned atomic transaction whose closure
evaluation is CAS-protected — a concurrent close (settlement/write/reconcile)
is a silent miss, never a duplicate release. Catch-up tokens minted by a
closure are enqueued AFTER the transaction commits via the org's thread
queue, exactly like every other wake.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from runtime.daemon.zombie_reaper import REAPER_INTERVAL_SECONDS

logger = logging.getLogger(__name__)


async def exchange_reaper_loop(
    state,
    *,
    interval_seconds: int = REAPER_INTERVAL_SECONDS,
) -> None:
    """Periodic exchange-bound reaper (TASK-5966). Registered in
    runtime/daemon/app.py ``_lifespan`` alongside zombie_reaper_loop."""
    while True:
        t0 = _monotonic()
        for org in list(state.orgs.values()):
            try:
                arrivals = org.db.reaper_sweep_reply_exchanges()
                for a in arrivals:
                    if a.invocation_token is not None:
                        from runtime.daemon.thread_queue import ThreadJob
                        await org.thread_queue.put(ThreadJob(
                            org_slug=org.slug,
                            invocation_token=a.invocation_token,
                        ))
            except Exception:
                logger.exception(
                    "exchange reaper sweep failed for org %s", org.slug,
                )
        duration = _monotonic() - t0
        state.metrics_registry.record_loop_tick(
            "exchange_reaper", interval_seconds, duration,
        )
        await asyncio.sleep(interval_seconds)


def _monotonic() -> float:
    import time as _time
    return _time.monotonic()
