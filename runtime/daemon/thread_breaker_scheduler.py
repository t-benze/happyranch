"""Timer-driven THR-200 thread reply breaker half-open probe producer."""
from __future__ import annotations

import asyncio
import logging

from runtime.config import THREAD_REPLY_BREAKER_COOLDOWN_SECONDS
from runtime.daemon.thread_queue import ThreadJob
from runtime.daemon.thread_runner import _breaker_executor_key
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.prompt_loader import load_agent

logger = logging.getLogger(__name__)


async def thread_breaker_scheduler_loop(state, *, interval_seconds: float = 5.0) -> None:
    while True:
        for org in list(state.orgs.values()):
            try:
                keys = {}
                for delivery in org.db.list_reply_delivery_states():
                    agent = load_agent(OrgPaths(root=org.root), delivery.agent_name)
                    if agent is None:
                        continue
                    keys[(delivery.thread_id, delivery.agent_name)] = (
                        _breaker_executor_key(
                            agent.executor.lower(), agent.model, state.settings,
                        )
                    )
                entries = org.db.mint_due_thread_reply_breaker_probes(
                    no_episode_executor_keys=keys,
                    cooldown_seconds=THREAD_REPLY_BREAKER_COOLDOWN_SECONDS,
                )
                for entry in entries:
                    await org.thread_queue.put_once(ThreadJob(
                        org_slug=org.slug, invocation_token=entry.invocation_token,
                    ))
            except Exception:
                logger.exception("thread breaker probe sweep failed for org %s", org.slug)
        await asyncio.sleep(interval_seconds)
