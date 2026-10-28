"""Async queue + job payload for thread invocations.

Each ThreadJob points to a thread_invocations row by `invocation_token`.
A worker pool consumes jobs and hands them to ThreadInvocationRunner.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from runtime.config import Settings

logger = logging.getLogger(__name__)


@dataclass
class ThreadJob:
    org_slug: str
    invocation_token: str


class ThreadQueue:
    def __init__(self) -> None:
        # Explicit standalone construction retains its offline contract. The
        # daemon binds these original references before exposing production.
        self._startup_state = None
        self._startup_org = None
        self._startup_recovery = None
        self._q: asyncio.Queue[ThreadJob] = asyncio.Queue()
        self._published_once_tokens: set[str] = set()

    def _require_ready(self) -> None:
        from runtime.daemon.startup_recovery import _require_ordinary_ready, StartupUnavailable
        _require_ordinary_ready(self)
        if self._startup_state is not None and self._startup_org.orchestrator._thread_queue is not self:
            raise StartupUnavailable("startup_attachment_changed")

    def _require_publication(self, job: ThreadJob, publication: object | None = None) -> None:
        from runtime.daemon.startup_recovery import StartupUnavailable
        if publication is None:
            self._require_ready()
        else:
            from runtime.daemon.startup_publication import _Handoff
            gate = self._startup_recovery
            if (not isinstance(publication, _Handoff) or gate is None
                    or publication.owner is not gate._publications):
                raise StartupUnavailable("startup_thread_publication_unavailable")
            publication.owner.require_thread_handoff(publication, self, job)
        if self._startup_org is not None and job.org_slug != self._startup_org.slug:
            raise StartupUnavailable("startup_attachment_changed")

    async def put(self, job: ThreadJob, *, _publication: object | None = None) -> None:
        await self.put_once(job, _publication=_publication)

    async def put_once(self, job: ThreadJob, *, _publication: object | None = None) -> str:
        self._require_publication(job, _publication)
        # The queue is unbounded, so put_nowait cannot suspend between enqueue
        # and the ownership mark. Ownership spans queued + in-flight delivery;
        # the worker releases it after either acknowledgement or failure. The
        # durable invocation claim remains the exactly-once launch authority.
        if job.invocation_token in self._published_once_tokens:
            return "already_pending"
        self._q.put_nowait(job)
        self._published_once_tokens.add(job.invocation_token)
        return "inserted"

    async def get(self) -> ThreadJob:
        # Never await Queue.get across the admission fence: its wakeup could
        # pop a token after stop. Check and nonblocking pop have no await.
        while True:
            self._require_ready()
            try:
                return self._q.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)

    def acknowledge_once(self, job: ThreadJob) -> None:
        """Finish queue-owned delivery after the consumer returns normally."""
        self._published_once_tokens.discard(job.invocation_token)

    def release_once(self, job: ThreadJob) -> None:
        """Release failed in-flight delivery so a durable producer can retry."""
        self._published_once_tokens.discard(job.invocation_token)

    @property
    def size(self) -> int:
        return self._q.qsize()


async def thread_worker_loop(state, settings: Settings) -> None:
    """Single worker that drains ThreadJobs across all orgs.

    Round-robins across orgs because each org has its own asyncio.Queue.
    Multiple workers can run in parallel; each picks the next available job.
    """
    from runtime.daemon.thread_runner import run_invocation
    from runtime.orchestrator.team_move import DeferredTeamMove
    from runtime.daemon.startup_recovery import (
        maintenance_service_validation, StartupUnavailable,
    )

    validate_service = maintenance_service_validation(state)
    while True:
        validate_service()
        all_orgs = list(state.orgs.values())
        if not all_orgs:
            await asyncio.sleep(0.5)
            continue
        for org in all_orgs:
            if not state._worker_org_current(org, validate_service):
                continue
            if org.thread_queue.size == 0:
                continue
            try:
                job = await asyncio.wait_for(org.thread_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            except StartupUnavailable:
                if not state._worker_org_current(org, validate_service):
                    continue
                raise
            try:
                await run_invocation(
                    org_state=org,
                    invocation_token=job.invocation_token,
                    settings=settings,
                    host_supervisor=state.host_supervisor,
                )
            except DeferredTeamMove as exc:
                org.thread_queue.release_once(job)
                logger.info("thread admission deferred: %s", exc.code)
            except StartupUnavailable:
                org.thread_queue.release_once(job)
                raise
            except asyncio.CancelledError:
                org.thread_queue.release_once(job)
                raise
            except Exception:
                org.thread_queue.release_once(job)
                logger.exception(
                    "thread_worker_loop: invocation %s crashed",
                    job.invocation_token[:8],
                )
            else:
                org.thread_queue.acknowledge_once(job)
        await asyncio.sleep(0.05)
