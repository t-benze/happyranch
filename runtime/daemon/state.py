"""Process-wide state holder for the daemon."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

from runtime.config import Settings
from runtime.daemon.assistant_pty import AssistantSessionManager
from runtime.daemon.org_state import OrgState
from runtime.daemon.queue import TaskQueue
from runtime.orchestrator.org_validation import OrgConsistencyError
from runtime.orchestrator.executor_registry import (
    ExecutorProfileCollisionError,
)
from runtime.runtime import RuntimeDir

logger = logging.getLogger(__name__)


@dataclass
class DaemonState:
    runtime: RuntimeDir | None
    settings: Settings
    orgs: dict[str, OrgState] = field(default_factory=dict)
    # Orgs whose folder is on disk but failed to attach (typically an
    # OrgConsistencyError from validate_team_membership, an
    # ExecutorProfileCollisionError from custom profile registration,
    # or a ValueError / AgentParseError from agent file validation).
    # Surfaced via GET /orgs so the founder isn't left guessing why an
    # org went missing after a restart.
    broken_orgs: dict[str, str] = field(default_factory=dict)
    queue: TaskQueue = field(default_factory=TaskQueue)
    orgs_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    assistant_lifecycle_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    assistant_sessions: AssistantSessionManager = field(
        default_factory=AssistantSessionManager
    )

    @classmethod
    def idle(cls, settings: Settings) -> "DaemonState":
        return cls(runtime=None, settings=settings)

    @classmethod
    def from_runtime(cls, runtime: RuntimeDir, settings: Settings) -> "DaemonState":
        state = cls(runtime=runtime, settings=settings)
        for slug, root in runtime.iter_org_roots():
            try:
                org = OrgState.load(slug=slug, root=root, settings=settings)
            except (OrgConsistencyError, ExecutorProfileCollisionError,
                    ValueError) as exc:
                # One broken org must not crash the daemon. Record the
                # error for GET /orgs and skip; the folder stays intact
                # on disk so the founder can fix teams.yaml and restart.
                state.broken_orgs[slug] = str(exc)
                logger.error("org %r failed consistency check: %s", slug, exc)
                continue
            # Attach the global queue + per-org sessions so the orchestrator can
            # re-enqueue tasks (e.g. parent wake-up after a child resolves).
            # The lifespan wiring also does this, but `from_runtime` is used by
            # tests that bypass lifespan, so we do it here too — idempotent.
            org.orchestrator.attach_queue(state.queue)
            org.orchestrator.attach_sessions(org.sessions)
            state.orgs[slug] = org
        return state

    @property
    def is_idle(self) -> bool:
        return self.runtime is None

    def get_org(self, slug: str) -> OrgState:
        try:
            return self.orgs[slug]
        except KeyError as exc:
            raise KeyError(slug) from exc

    async def add_org(self, slug: str) -> OrgState:
        """Lazy-load an org's OrgState. Idempotent — returns the existing
        instance if the slug is already loaded.

        The orchestrator's queue and per-org session tracker are attached
        here so a freshly-added org is immediately runnable (the lifespan
        ``_attach_org_runtime_wiring`` only runs over orgs present at boot).
        """
        async with self.orgs_lock:
            if slug in self.orgs:
                return self.orgs[slug]
            assert self.runtime is not None
            root = self.runtime.orgs_dir / slug
            # OrgConsistencyError propagates — add_org is an explicit
            # action (init / unload+reload) and must fail loudly so the
            # founder sees the reason at the HTTP layer.
            org = OrgState.load(slug=slug, root=root, settings=self.settings)
            org.orchestrator.attach_queue(self.queue)
            org.orchestrator.attach_sessions(org.sessions)
            self.orgs[slug] = org
            self.broken_orgs.pop(slug, None)
            # Wire the thread queue + main loop so run_step workers can
            # cross the async boundary via run_coroutine_threadsafe when
            # posting task-followup invocations.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                org.orchestrator.attach_thread_queue(org.thread_queue, loop)
            return org

    async def remove_org(self, slug: str) -> None:
        async with self.orgs_lock:
            org = self.orgs.pop(slug, None)
            if org is not None:
                org.close()

    async def close_all(self) -> None:
        await self.assistant_sessions.close_all()
        async with self.orgs_lock:
            for org in self.orgs.values():
                org.close()
            self.orgs.clear()
