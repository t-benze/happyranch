"""Worker-side dispatcher: pop (slug, task_id), route to the right Orchestrator."""
from __future__ import annotations

import logging

from src.daemon.state import DaemonState

logger = logging.getLogger("opc.daemon.dispatcher")


class Dispatcher:
    """Resolves ``(slug, task_id)`` to ``OrgState.orchestrator.run_step``.

    Built once per daemon lifetime; not thread-safe except for what
    DaemonState.orgs guarantees (the dict isn't modified concurrently with
    reads under normal operation; ``add_org`` / ``remove_org`` hold the
    orgs_lock).
    """

    def __init__(self, state: DaemonState) -> None:
        self._state = state

    def run_step(self, slug: str, task_id: str) -> None:
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
        org.orchestrator.run_step(task_id)
