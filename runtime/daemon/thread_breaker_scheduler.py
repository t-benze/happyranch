"""Timer-driven THR-200 thread reply breaker half-open probe producer."""
from __future__ import annotations

import asyncio
import logging

from runtime.daemon.thread_queue import ThreadJob

logger = logging.getLogger(__name__)


async def thread_breaker_scheduler_loop(state, *, interval_seconds: float = 5.0) -> None:
    while True:
        for org in list(state.orgs.values()):
            try:
                entries = org.db.mint_due_thread_reply_breaker_probes()
                for entry in entries:
                    await org.thread_queue.put(ThreadJob(
                        org_slug=org.slug, invocation_token=entry.invocation_token,
                    ))
            except Exception:
                logger.exception("thread breaker probe sweep failed for org %s", org.slug)
        await asyncio.sleep(interval_seconds)
