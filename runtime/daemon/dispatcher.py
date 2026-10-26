"""Worker-side dispatcher: pop (slug, task_id), route to the right Orchestrator."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.daemon.org_state import OrgState
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

    def task_attachment(self, slug: str) -> OrgState | None:
        """Capture the immutable OrgState before native queue submission."""
        try:
            return self._state.get_org(slug)
        except KeyError:
            return None

    def run_step(self, slug: str, task_id: str, metadata: dict | None = None, *, attachment: OrgState | None = None) -> None:
        from runtime.daemon.startup_recovery import require_ready
        require_ready(self._state)
        if attachment is not None:
            require_ready(attachment)
            if self._state.orgs.get(slug) is not attachment:
                from runtime.daemon.startup_recovery import StartupUnavailable
                raise StartupUnavailable("startup_attachment_changed")
        try:
            org = attachment if attachment is not None else self._state.get_org(slug)
        except KeyError:
            logger.warning(
                "dropping run_step for unknown org %r (task %s) — "
                "org may have been unloaded",
                slug,
                task_id,
            )
            return
        from runtime.orchestrator.team_move import DeferredTeamMove
        from runtime.infrastructure.team_move_store import TeamMoveError
        require_ready(org)
        try:
            org.orchestrator.run_step(task_id, metadata=metadata)
        except (DeferredTeamMove, TeamMoveError):
            org.orchestrator._team_moves.defer_task(task_id, metadata)

    def task_admission_allowed(self, slug: str, task_id: str, metadata: dict | None = None, *, attachment: OrgState | None = None) -> bool:
        from runtime.daemon.startup_recovery import require_ready
        require_ready(self._state)
        if attachment is not None:
            require_ready(attachment)
            if self._state.orgs.get(slug) is not attachment:
                from runtime.daemon.startup_recovery import StartupUnavailable
                raise StartupUnavailable("startup_attachment_changed")
        from runtime.orchestrator.team_move import DeferredTeamMove
        from runtime.infrastructure.team_move_store import TeamMoveError
        try:
            org = attachment if attachment is not None else self._state.get_org(slug)
        except KeyError:
            return False
        from runtime.workflows.recovery import classify_task
        require_ready(org)
        ownership = classify_task(org.db, task_id, org_slug=slug)
        if ownership.kind == 'coding_attempt':
            return org.workflow_coding_dispatch.queue_admission_allowed(task_id)
        if ownership.kind.startswith('coding_') or ownership.kind == 'reconciliation_required':
            return False
        move = org.orchestrator._team_moves
        try:
            with move.gate, org.db._lock:
                org.db._check_team_move_task_uncommitted(task_id)
        except (DeferredTeamMove, TeamMoveError):
            move.defer_task(task_id, metadata)
            return False
        return True

    def worker_started(self, slug: str, task_id: str, *, attachment: OrgState | None = None) -> object:
        from runtime.daemon.startup_recovery import require_ready
        require_ready(self._state)
        if attachment is not None:
            require_ready(attachment)
            if self._state.orgs.get(slug) is not attachment:
                from runtime.daemon.startup_recovery import StartupUnavailable
                raise StartupUnavailable("startup_attachment_changed")
        try:
            org = attachment if attachment is not None else self._state.get_org(slug)
            require_ready(org)
            coding = org.workflow_coding_dispatch
        except KeyError:
            return None
        return coding, coding.queue_started(task_id)

    def worker_finished(self, slug: str, task_id: str, owner: object) -> None:
        coding, identity = owner
        coding.queue_finished(task_id, identity)

    def heartbeat(self, slug: str, task_id: str, queue_owner: object = None, *, attachment: OrgState | None = None) -> None:
        """Stamp ``tasks.last_heartbeat`` on the per-org DB.

        Called against the queue's captured org while ``run_step`` is in
        flight. Unbound legacy callers still no-op for an unloaded slug.
        """
        try:
            org = attachment if attachment is not None else self._state.get_org(slug)
        except KeyError:
            return
        from runtime.workflows.recovery import classify_task
        owner = classify_task(org.db, task_id, org_slug=slug)
        if owner.kind in ('coding_source_waiting', 'coding_attempt'):
            if queue_owner is None or queue_owner[0] is not org.workflow_coding_dispatch:
                return
            org.workflow_coding_dispatch.heartbeat(task_id, queue_owner[1])
            return
        if owner.kind.startswith('coding_') or owner.kind == 'reconciliation_required':
            return
        now = datetime.now(timezone.utc).isoformat()
        org.db.update_task(task_id, last_heartbeat=now)
