"""Worker-side dispatcher: pop (slug, task_id), route to the right Orchestrator."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from runtime.daemon.state import DaemonState

logger = logging.getLogger("happyranch.daemon.dispatcher")


class Dispatcher:
    """Resolves ``(slug, task_id)`` to ``OrgState.orchestrator.run_step``.

    Built once per daemon lifetime; not thread-safe except for what
    DaemonState.orgs guarantees (the dict isn't modified concurrently with
    reads under normal operation; ``add_org`` / ``remove_org`` hold the
    orgs_lock).
    """

    def __init__(self, state: DaemonState) -> None:
        self._state = state

    def run_step(self, slug: str, task_id: str, metadata: dict | None = None) -> None:
        try:
            org = self._state.get_org(slug)
        except KeyError:
            logger.warning(
                "dropping run_step for unknown org %r (task %s) — "
                "org may have been unloaded",
                slug,
                task_id,
            )
            return
        org.orchestrator.run_step(task_id, metadata=metadata)

    def heartbeat(self, slug: str, task_id: str) -> None:
        """Stamp ``tasks.last_heartbeat`` on the per-org DB.

        Called periodically by the queue's heartbeat coroutine while
        ``run_step`` is in flight. Silently no-ops if the org has been
        unloaded since the task was enqueued.
        """
        try:
            org = self._state.get_org(slug)
        except KeyError:
            return
        now = datetime.now(timezone.utc).isoformat()
        org.db.update_task(task_id, last_heartbeat=now)
