"""Timer-driven THR-200 thread reply breaker half-open probe producer."""
from __future__ import annotations

import asyncio
import logging
import threading
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


class _BreakerNativeOwner:
    """One original callable, including submission with no returned Future."""

    def __init__(self, org, loop):
        self.org, self.loop = org, loop
        self.lock = threading.Lock()
        self.finished = self.handed_off = self.retirement_started = False
        self.primary = self.traceback = None
        # Fixed slots retain actual exceptions even when outer notes are full.
        self.errors = dict.fromkeys(("wrapper", "native", "handoff", "retirement", "coroutine_close"))
        self.error_tracebacks = self.errors.copy()
        self.error_order = []
        self.noted = set()
        self.diagnostic_error = None

    def _annotate_locked(self):
        if self.primary is None:
            return
        try:
            BaseException.__setattr__(self.primary, "_startup_breaker_native_owner", self)
        except BaseException as error:
            self.diagnostic_error = self.diagnostic_error or error
            return
        for kind in self.error_order:
            error = self.errors[kind]
            if error is not None and error is not self.primary and kind not in self.noted:
                try:
                    BaseException.add_note(self.primary,
                        f"startup breaker {kind} failed: {type(error).__name__[:128]}; "
                        "actual exception retained by original native owner")
                except BaseException as diagnostic:
                    # Optional diagnostics cannot replace a real cause or skip
                    # retirement; the fixed actual exception slots survive.
                    self.diagnostic_error = self.diagnostic_error or diagnostic
                self.noted.add(kind)

    def _fail_locked(self, kind, error):
        if self.errors[kind] is None:
            self.errors[kind] = error
            self.error_tracebacks[kind] = error.__traceback__
            self.error_order.append(kind)  # At most the five fixed slots.
        self._annotate_locked()

    def fail(self, kind, error):
        with self.lock:
            self._fail_locked(kind, error)

    def remember_primary(self, error):
        with self.lock:
            if self.primary is None:
                self.primary, self.traceback = error, error.__traceback__
            self._annotate_locked()

    def complete(self):
        # Only the actual worker calls this, after validation/function exit.
        # Serialize posting with acknowledgement: a callback cannot retire from
        # a post that enqueued and then failed before returning to this worker.
        with self.lock:
            self.finished = True
            try:
                self.loop.call_soon_threadsafe(self.retire)
            except BaseException as error:
                self._fail_locked("handoff", error)
            else:
                self.handed_off = True

    def retire(self):
        # Called ONLY on the original loop, by its callback or original waiter.
        # No native-thread mutation of the org's native-user registry.
        with self.lock:
            if not self.finished or not self.handed_off or self.retirement_started:
                return
            self.retirement_started = True
            try:
                self.org._release_native_task(self)
            except BaseException as error:
                self._fail_locked("retirement", error)

    def outcome_error(self):
        with self.lock:
            if self.primary is None:
                error = self.errors["handoff"] or self.errors["retirement"]
                if error is not None:
                    self.primary, self.traceback = error, error.__traceback__
            self._annotate_locked()
            return self.primary, self.traceback


async def _native_call(org, validate, function, *args):
    """Register before submission; wrapper failure alone never retires custody."""
    validate()
    owner = _BreakerNativeOwner(org, asyncio.get_running_loop())
    if not org._acquire_native_task(owner):
        raise StartupUnavailable("startup_breaker_admission_closed")

    def run():
        try:
            validate()
            return function(*args)
        except BaseException as error:
            owner.fail("native", error)
            raise
        finally:
            owner.complete()

    coroutine = asyncio.to_thread(run)
    try:
        worker = asyncio.create_task(coroutine)
    except BaseException as error:
        owner.remember_primary(error)
        owner.fail("wrapper", error)
        try:
            coroutine.close()
        except BaseException as cleanup:
            owner.fail("coroutine_close", cleanup)
        # A task factory may have scheduled before raising; no no-run claim.
        raise

    while True:
        try:
            await asyncio.shield(worker)
            break
        except asyncio.CancelledError as error:
            owner.remember_primary(error)
            if worker.done():
                break
        except BaseException as error:
            owner.remember_primary(error)
            break
    result = None
    try:
        result = worker.result()
    except BaseException as error:
        owner.remember_primary(error)
        owner.fail("wrapper", error)
    # No wait for a possibly nonexistent worker after exceptional submission.
    # A later genuine completion can acknowledge only this identical owner.
    owner.retire()
    error, traceback = owner.outcome_error()
    if error is not None:
        raise error.with_traceback(traceback)
    return result


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
