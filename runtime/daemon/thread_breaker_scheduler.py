"""Timer-driven THR-200 thread reply breaker half-open probe producer."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from runtime.config import THREAD_REPLY_BREAKER_COOLDOWN_SECONDS
from runtime.daemon.thread_queue import ThreadJob
from runtime.daemon.thread_runner import _breaker_executor_key
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.prompt_loader import load_agent
from runtime.daemon.startup_recovery import (
    ordinary_admission_validation, require_ready, StartupUnavailable,
)

logger = logging.getLogger(__name__)


def _mint_due_probes(org: Any, keys: dict[tuple[str, str], str], validate) -> Any:
    validate()
    return org.db.mint_due_thread_reply_breaker_probes(
        no_episode_executor_keys=keys,
        cooldown_seconds=THREAD_REPLY_BREAKER_COOLDOWN_SECONDS,
        validate_admission=validate,
    )


async def _native_call(org, validate, function, *args):
    """Register before submission; retire only the same actual native result."""
    from runtime.orchestrator.producer_move import await_producer_worker
    validate()
    owner = object()
    if not org._acquire_native_task(owner):
        raise StartupUnavailable("startup_breaker_admission_closed")

    def run():
        validate()
        return function(*args)

    worker = None
    coroutine = asyncio.to_thread(run)
    try:
        try:
            worker = asyncio.create_task(coroutine)
        except BaseException as exc:
            coroutine.close()
            # Submission uncertainty retains the existing native close barrier.
            if isinstance(exc, Exception):
                raise StartupUnavailable("startup_breaker_submission_unknown") from exc
            raise
        return await await_producer_worker(worker)
    finally:
        if worker is not None and worker.done() and not worker.cancelled():
            org._release_native_task(owner)  # Settlement does not require admission.


async def thread_breaker_scheduler_loop(state, *, interval_seconds: float = 5.0) -> None:
    while True:
        require_ready(state)
        for org in list(state.orgs.values()):
            try:
                require_ready(org)
                validate = ordinary_admission_validation(org)
                move = getattr(org.db, "_team_move_coordinator", None)
                if move is not None:
                    for token in await _native_call(org, validate, move.threads.runnable_tokens):
                        await org.thread_queue.put_once(ThreadJob(org_slug=org.slug, invocation_token=token))
                keys = {}
                deliveries = await _native_call(org, validate, org.db.list_reply_delivery_states)
                require_ready(org)
                for delivery in deliveries:
                    if move is not None:
                        with org.db._lock:
                            if move.threads.held_uncommitted(delivery.agent_name) is not None:
                                continue
                    agent = load_agent(OrgPaths(root=org.root), delivery.agent_name)
                    if agent is None:
                        continue
                    keys[(delivery.thread_id, delivery.agent_name)] = (
                        _breaker_executor_key(
                            agent.executor.lower(), agent.model, state.settings,
                        )
                    )
                entries = await _native_call(
                    org, validate, _mint_due_probes, org, keys, validate,
                )
                for entry in entries:
                    await org.thread_queue.put_once(ThreadJob(
                        org_slug=org.slug, invocation_token=entry.invocation_token,
                    ))
            except StartupUnavailable:
                raise
            except Exception:
                logger.exception("thread breaker probe sweep failed for org %s", org.slug)
        await asyncio.sleep(interval_seconds)
