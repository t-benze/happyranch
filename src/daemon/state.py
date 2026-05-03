"""Process-wide state holder for the daemon."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from src.config import Settings
from src.daemon.org_state import OrgState
from src.daemon.queue import TaskQueue
from src.runtime import RuntimeDir


@dataclass
class DaemonState:
    runtime: RuntimeDir | None
    settings: Settings
    orgs: dict[str, OrgState] = field(default_factory=dict)
    queue: TaskQueue = field(default_factory=TaskQueue)
    orgs_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @classmethod
    def idle(cls, settings: Settings) -> "DaemonState":
        return cls(runtime=None, settings=settings)

    @classmethod
    def from_runtime(cls, runtime: RuntimeDir, settings: Settings) -> "DaemonState":
        state = cls(runtime=runtime, settings=settings)
        for slug, root in runtime.iter_org_roots():
            org = OrgState.load(slug=slug, root=root, settings=settings)
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
            org = OrgState.load(slug=slug, root=root, settings=self.settings)
            org.orchestrator.attach_queue(self.queue)
            org.orchestrator.attach_sessions(org.sessions)
            self.orgs[slug] = org
            return org

    async def remove_org(self, slug: str) -> None:
        async with self.orgs_lock:
            org = self.orgs.pop(slug, None)
            if org is not None:
                org.close()

    async def close_all(self) -> None:
        async with self.orgs_lock:
            for org in self.orgs.values():
                org.close()
            self.orgs.clear()
