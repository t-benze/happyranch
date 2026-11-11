"""Org-owned native preparation and Pause ownership under a historical name.

The online operator service and D operation are retired. Reserved thread,
Founder-settlement and disk-sweeper readers still share this owner.
No optional schema is installed here; absent-layout ordinary tasks remain valid.
"""
from __future__ import annotations

import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable

from runtime.infrastructure.team_move_store import TeamMoveError, TeamMoveStore, validate_team_move_schema

_task_tail: ContextVar[list | None] = ContextVar("team_move_task_tail", default=None)


@dataclass(frozen=True)
class PermanentAgentIdentity:
    """Resolved by the real producer store/AgentDef owner, never logical-ID parsing.

    A type is not authentication: task owners carry the server-selected permanent ID
    from its task/session/workflow owner through fallback and provider retries.
    """
    org_slug: str
    agent_id: str


class DeferredTeamMove(Exception):
    """Durable work stays unclaimed; this is never an executor callback."""
    def __init__(self, code: str, operation_id: str | None = None) -> None:
        self.code, self.operation_id = code, operation_id
        super().__init__(code)


@dataclass
class TaskPreparation:
    coordinator: Any
    identity: PermanentAgentIdentity
    owner_id: str
    task_id: str
    session_id: str
    owner_live: bool = True
    possible_launch: bool = False
    quiescent: bool = False
    cleanup_failed: bool = False
    repository_obligations: dict[str, bool] = field(default_factory=dict, repr=False)
    repository_evidence: dict[str, Any] = field(default_factory=dict, repr=False)

    def before_fifo(self) -> None:
        # No host/controller/tracker lock is held here. Do not launch using a
        # prompt prepared by another owner or reuse it after a durable hold.
        with self.coordinator.gate, self.coordinator.store.db._lock:
            self.coordinator.revalidate_uncommitted(self)

    def terminal(self, outcome: Any) -> None:
        # Called AFTER backend finish, outside host terminal/tracker locks.
        # A missing outcome/receipt or passthrough cannot prove tree quiescence.
        with self.coordinator.store.db._lock:
            receipt = None if outcome is None else outcome.receipt
            self.cleanup_failed |= outcome is not None and outcome.cleanup_error is not None
            self.quiescent = bool(receipt is not None and receipt.backend != "passthrough"
                and receipt.quiescent is True and receipt.cleanup_status.value != "incomplete"
                and not receipt.survivors and outcome.cleanup_error is None)

    def failed_cleanup(self) -> None:
        # Leaf settlement: never reacquire move gate beneath callback locks.
        with self.coordinator.store.db._lock:
            self.cleanup_failed = True


class TeamMoveCoordinator:
    """One org-owned task gate, composed with the existing Pause writer.

    The class name is historical; shared Pause/preparation ownership remains.

    Task producer serialization: producer ownership/binding lease ->
    producer's root/Pause gate where applicable -> this synchronous gate -> DB
    RLock -> SQLite claim/intent transaction. The host terminal leaf mutex
    stays innermost through commit. Terminal callbacks
    must settle under their existing ownership and must not acquire this gate
    beneath DB/tracker locks. Avoid a reverse callback dependency.

    Profile/publication async writer order stays profile -> workflow -> roster;
    acquire those leases outside the short gate, then revalidate under gate/DB
    for only the synchronous decision or mutation segment. Release gate and DB
    before await, filesystem scan/write, network, provider, bootstrap or acquiring
    any publication lease. Never nest two single-consumer writer intervals.

    Task owners register preparation before prompt/role/permission reads or work
    claims, revalidate at final launch, and retain that ownership through
    retries/fallback/terminal host cleanup. A durable hold is not a mutex lease:
    no TTL, timeout or missing PID releases it. Held agents stay outside host
    FIFO on the retained thread path; non-thread launches no longer check the
    move hold. Reserved callback/settlement and delivery-release readers retain
    their existing ownership checks; dead move-only projection/drain APIs are
    removed. Native producer/writer ownership remains shared.
    """
    def __init__(self, store: TeamMoveStore) -> None:
        # Construction is DB-owned, before any producer uses the gate. This
        # is not a callback path or generic Database initialization hook.
        with store.db._lock:
            if getattr(store.db, "_team_move_coordinator", None) is not None:
                raise ValueError("team_move_duplicate_org_owner")
            self.store = store
            self.gate = threading.RLock()
            self._teams_reader: Callable[[], Any] | None = None
            self._reservations: dict[str, TaskPreparation] = {}
            self._deferred: dict[str, dict | None] = {}
            store.db._team_move_coordinator = self
            from runtime.orchestrator.thread_move import ThreadMoveAdmission
            self.threads = ThreadMoveAdmission(self)
            from runtime.orchestrator.producer_move import ProducerMoveAdmission
            self.producers = ProducerMoveAdmission(self)

    @classmethod
    def for_org(cls, store: TeamMoveStore) -> TeamMoveCoordinator:
        with store.db._lock:
            existing = getattr(store.db, "_team_move_coordinator", None)
            if existing is None:
                return cls(store)
            if not isinstance(existing, cls) or existing.store.org_slug != store.org_slug:
                raise ValueError("team_move_identity_owner_mismatch")
            return existing

    def bind_teams(self, reader: Callable[[], Any]) -> None:
        # Server registry, no request/display-name resolver and no file reads.
        with self.store.db._lock:
            self._teams_reader = reader

    def active_uncommitted(self) -> dict | None:
        conn = self.store.db._conn
        try:
            if not validate_team_move_schema(conn, org_slug=self.store.org_slug):
                return None
        except TeamMoveError as exc:
            # Installed-state admission belongs to this owner, not the task's
            # business failure consumer. Preserve the actionable store code.
            raise DeferredTeamMove(exc.code) from exc
        row = conn.execute("SELECT * FROM team_move_operations WHERE active_slot=1").fetchone()
        return dict(row) if row is not None else None

    def _task_execution_agent_uncommitted(self, task: Any) -> str:
        """Current execution eligibility, never historical causal attribution."""
        teams = self._teams_reader() if self._teams_reader is not None else None
        if teams is None:
            raise DeferredTeamMove("team_move_task_owner_unavailable")
        try:
            manager = teams.executable_manager_for_team(task.team)
        except KeyError as exc:
            raise DeferredTeamMove("team_move_task_team_unavailable") from exc
        agent = task.assigned_agent if task.assigned_agent is not None else manager
        if not agent or agent not in teams.all_agents():
            raise DeferredTeamMove("team_move_task_owner_unavailable")
        return agent

    def recovery_uncommitted(self, invocation: Any, session_id: str, unknown_owner: str | None) -> None:
        """Actual return hands preparation to its genuine recovery SID.

        An uncertain original tree/callback keeps its separate original Pause
        entry and reservation. A recovery receipt can close only recovery.
        """
        prior = invocation.move_reservation
        if prior is None or self._reservations.get(invocation.identity) is not prior:
            raise DeferredTeamMove("team_move_preparation_missing")
        if unknown_owner is not None:
            self._reservations.pop(invocation.identity)
            prior.owner_id, prior.owner_live = unknown_owner, False
            self._reservations[unknown_owner] = prior
            invocation.move_reservation = TaskPreparation(self, prior.identity,
                invocation.identity, invocation.task.id, session_id)
            self._reservations[invocation.identity] = invocation.move_reservation
        elif prior.cleanup_failed or (prior.possible_launch and not prior.quiescent):
            raise DeferredTeamMove("team_move_preparation_unsettled")
        else:
            prior.session_id = session_id
            prior.possible_launch = prior.quiescent = False

    def reserve_task_uncommitted(self, invocation: Any) -> TaskPreparation:
        if self._task_execution_agent_uncommitted(invocation.task) != invocation.agent:
            raise DeferredTeamMove("team_move_task_identity_unavailable")
        prior = self._reservations.get(invocation.identity)
        if prior is not None:
            if (prior.owner_live or prior.cleanup_failed or (prior.possible_launch and not prior.quiescent)
                    or (prior.task_id, prior.session_id, prior.identity.agent_id) !=
                    (invocation.task.id, invocation.session_id, invocation.agent)):
                raise DeferredTeamMove("team_move_preparation_unsettled")
            prior.owner_live = True
            return prior
        reservation = TaskPreparation(self, PermanentAgentIdentity(self.store.org_slug, invocation.agent),
            invocation.identity, invocation.task.id, invocation.session_id)
        self._reservations[invocation.identity] = reservation
        return reservation

    def revalidate_uncommitted(self, reservation: TaskPreparation) -> None:
        if (self._reservations.get(reservation.owner_id) is not reservation or not reservation.owner_live
                or reservation.cleanup_failed or (reservation.possible_launch and not reservation.quiescent)):
            raise DeferredTeamMove("team_move_preparation_unsettled")
        task = self.store.db.get_task(reservation.task_id)
        if task is None:
            raise DeferredTeamMove("team_move_task_owner_unavailable")
        if self._task_execution_agent_uncommitted(task) != reservation.identity.agent_id:
            raise DeferredTeamMove("team_move_task_identity_unavailable")

    def retry_uncommitted(self, invocation: Any, unknown_owner: str) -> None:
        """After journal COMMIT, keep the returned attempt as a separate blocker.

        Caller owns root/move/DB in that order, outside host terminal locks.
        The next attempt still checks durable admission before FIFO and launch.
        Return from a provider does not settle the original descendant tree.
        """
        prior = invocation.move_reservation
        self._reservations.pop(invocation.identity)
        prior.owner_id, prior.owner_live = unknown_owner, False
        self._reservations[unknown_owner] = prior
        invocation.move_reservation = TaskPreparation(self, prior.identity,
            invocation.identity, invocation.task.id, invocation.session_id)
        self._reservations[invocation.identity] = invocation.move_reservation

    def release_task(self, invocation: Any, *, failed: bool = False) -> None:
        # Result consumer / workflow dispatcher tail, no gate under their locks.
        reservation = getattr(invocation, "move_reservation", None)
        if reservation is None:
            return
        with self.store.db._lock:
            reservation.owner_live = False
            reservation.cleanup_failed |= failed
            if (not reservation.cleanup_failed and all(reservation.repository_obligations.values()) and not invocation.unsettled
                    and (not reservation.possible_launch or reservation.quiescent)):
                self._reservations.pop(reservation.owner_id, None)

    def _thread_release_task_preparations_uncommitted(self, selection, budget):
        """Bounded read under caller-held move/DB, no policy or writer change."""
        from runtime.infrastructure.thread_delivery_release import ReleaseUnavailable
        budget.population(self._reservations)
        records = []
        for key, reservation in self._reservations.items():
            if type(reservation) is not TaskPreparation or reservation.coordinator is not self:
                raise ReleaseUnavailable("task_preparation_unavailable")
            identity = reservation.identity
            if type(identity) is not PermanentAgentIdentity:
                raise ReleaseUnavailable("task_preparation_unavailable")
            for value in (key, reservation.task_id, reservation.session_id,
                          identity.org_slug, identity.agent_id):
                budget.text(value)
            if (reservation.owner_id != key or identity.org_slug != selection.org_slug
                    or any(type(v) is not bool for v in (reservation.owner_live,
                        reservation.possible_launch, reservation.quiescent, reservation.cleanup_failed))):
                raise ReleaseUnavailable("task_preparation_unavailable")
            unsafe = (reservation.owner_live or reservation.cleanup_failed
                      or reservation.possible_launch and not reservation.quiescent)
            if identity.agent_id == selection.agent_id and unsafe:
                raise ReleaseUnavailable("task_preparation_current_owner")
            records.append((key, identity.agent_id, reservation.task_id,
                reservation.session_id, reservation.owner_live, reservation.possible_launch,
                reservation.quiescent, reservation.cleanup_failed))
        budget.check()
        return tuple(records)

    def blockers(self) -> tuple[dict, ...]:
        """Complete preparation registry for later D census, never idle proof.

        Persistent Pause journals, durable tasks/workflows, host/remote/session
        owners must ALSO be joined by D, particularly after process loss.
        """
        with self.store.db._lock:
            return tuple(dict(owner_id=r.owner_id, task_id=r.task_id, session_id=r.session_id,
                agent_id=r.identity.agent_id, owner_live=r.owner_live,
                possible_launch=r.possible_launch, quiescent=r.quiescent,
                cleanup_failed=r.cleanup_failed) for r in self._reservations.values())

    def defer_task(self, task_id: str, metadata: dict | None) -> None:
        with self.store.db._lock:
            # Tagged v2 generation metadata is retained exactly, never replaced
            # with an ordinary wake. Persistent generation/Pause records remain
            # authoritative if this process loses its notification copy.
            self._deferred.setdefault(task_id, metadata)

    def redeliver_tasks(self, enqueue: Any) -> None:
        """Publish native deferred work; callback is outside gate/DB locks.

        Existing generation-aware queue owner decides dedup/eligibility. No
        result token consumption, task allocation or release of the operation.
        """
        for task_id, metadata in self._released_task_notifications():
            if not enqueue(task_id, metadata):
                continue  # Refused/pending publication retains its notification.
            self._ack_released_task(task_id, metadata)

    def _released_task_notifications(self) -> tuple:
        with self.gate, self.store.db._lock:
            return tuple(self._deferred.items())

    def _ack_released_task(self, task_id: str, metadata: dict | None) -> None:
        with self.store.db._lock:
            if self._deferred.get(task_id) is metadata:
                self._deferred.pop(task_id, None)

    async def _redeliver_tasks_native(self, enqueue: Any, *, native: Any) -> None:
        deferred = await native(self._released_task_notifications)
        for task_id, metadata in deferred:
            if await enqueue(task_id, metadata):
                await native(lambda: self._ack_released_task(task_id, metadata))
