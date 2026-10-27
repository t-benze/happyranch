"""Async queue for dream invocations."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from runtime.config import Settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DreamJob:
    org_slug: str
    dream_id: str


class DreamQueue:
    def __init__(self) -> None:
        self._startup_state = None
        self._startup_org = None
        self._startup_recovery = None
        self._q: asyncio.Queue[DreamJob] = asyncio.Queue()
        self._owned: set[tuple[str, str]] = set()

    def _require_ready(self) -> None:
        from runtime.daemon.startup_recovery import _require_ordinary_ready
        _require_ordinary_ready(self)

    def _require_publication(self, job: DreamJob, publication: object | None) -> None:
        from runtime.daemon.startup_recovery import StartupUnavailable
        if publication is None:
            self._require_ready()
        else:
            from runtime.daemon.startup_publication import _Handoff
            gate = self._startup_recovery
            if (not isinstance(publication, _Handoff) or gate is None
                    or publication.owner is not gate._publications):
                raise StartupUnavailable("startup_producer_publication_unavailable")
            publication.owner.require_producer_handoff(publication, self, job)
        if self._startup_org is not None and job.org_slug != self._startup_org.slug:
            raise StartupUnavailable("startup_attachment_changed")

    async def put(self, job: DreamJob, *, _publication: object | None = None) -> None:
        self._put_once(job, _publication=_publication)

    async def put_once(self, job: DreamJob, *, _publication: object | None = None) -> str:
        """Positive insertion/SAME original owner acknowledgement on the real loop."""
        return self._put_once(job, _publication=_publication)

    def _put_once(self, job: DreamJob, *, _publication: object | None = None) -> str:
        self._require_publication(job, _publication)
        key = (job.org_slug, job.dream_id)
        if key in self._owned:
            return "already_pending"
        # No suspension: a failed insertion cannot manufacture ownership.
        self._q.put_nowait(job)
        self._owned.add(key)
        return "inserted"

    def put_nowait(self, job: DreamJob) -> None:
        """Keep ordinary synchronous dedup; native startup uses real-loop put_once."""
        self._put_once(job)

    def release(self, job):
        self._owned.discard((job.org_slug, job.dream_id))

    async def get(self) -> DreamJob:
        # Check and nonblocking pop share one unsuspended admission segment.
        while True:
            self._require_ready()
            try:
                return self._q.get_nowait()
            except asyncio.QueueEmpty:
                await asyncio.sleep(0.05)

    @property
    def size(self) -> int:
        return self._q.qsize()


async def dream_worker_loop(state, settings: Settings) -> None:
    from runtime.daemon.dream_runner import run_dream

    from runtime.daemon.startup_recovery import (
        maintenance_service_validation, StartupUnavailable,
    )

    validate_service = maintenance_service_validation(state)
    while True:
        validate_service()
        for org in list(state.orgs.values()):
            if not state._worker_org_current(org, validate_service):
                continue
            if org.dream_queue.size == 0:
                continue
            try:
                job = await asyncio.wait_for(org.dream_queue.get(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            except StartupUnavailable:
                if not state._worker_org_current(org, validate_service):
                    continue
                raise
            try:
                await run_dream(
                    org_state=org, dream_id=job.dream_id, settings=settings,
                    host_supervisor=state.host_supervisor,
                )
            except StartupUnavailable:
                raise
            except Exception:
                logger.exception("dream_worker_loop: dream %s crashed", job.dream_id)
            finally:
                org.dream_queue.release(job)
        await asyncio.sleep(0.05)
