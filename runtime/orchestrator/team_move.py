"""Org-owned task preparation/launch exclusion; apply remains unavailable.

C2/C3/D still own other claims, whole-operation census, release and recovery.
No optional schema is installed here; absent-layout ordinary tasks remain valid.
"""
from __future__ import annotations

import json
import threading
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Literal

from runtime.infrastructure.team_move_store import MoveRequest, Phase, TeamMoveError, TeamMoveStore, validate_team_move_schema, _decode

_task_tail: ContextVar[list | None] = ContextVar("team_move_task_tail", default=None)

CAPABILITY_VERSION = 1
APPLY_AVAILABLE = False
NOT_INTEGRATED = ("thread_delivery", "other_producers_writers",
                  "canonical_context_publication_recovery", "startup_launcher_downgrade")

# Required D completion readback; labels are a contract, never Boolean proofs.
COMPLETION_REQUIREMENTS = (
    "three_exact_canonical_after_images_and_coherent_membership",
    "two_operation_scoped_native_reset_audits_or_validated_zero_row_steps",
    "two_original_workspace_native_context_and_all_context_skill_integrity_receipts",
    "same_owned_publisher_journal_cache_installed_and_matching_ready_pointer_file_cache_digest",
    "current_canonical_profile_dependency_skill_permission_snapshot",
    "settled_producer_callback_host_remote_cleanup_and_writer_ownership",
)
STARTUP_REQUIREMENTS = (
    "discover_validate_operation_before_generic_compatibility_writes_and_roster_validation",
    "recover_exact_owned_before_after_prefix_before_authority_publication",
    "install_durable_affected_hold_before_any_task_thread_job_schedule_recovery",
    "unknown_layout_record_owner_or_cleanup_keeps_org_diagnostic_only",
    "all_supported_runtime_attach_switch_launcher_paths_refuse_incompatible_reader",
)


@dataclass(frozen=True)
class PermanentAgentIdentity:
    """Resolved by the real producer store/AgentDef owner, never logical-ID parsing.

    A type is not authentication: task owners carry the server-selected permanent ID
    from its task/session/workflow owner through fallback and provider retries.
    """
    org_slug: str
    agent_id: str


@dataclass(frozen=True)
class AdmissionProjection:
    org_slug: str
    agent_id: str
    held: bool
    operation_id: str | None
    state_revision: int | None
    phase: str | None
    committed: bool
    apply_available: Literal[False] = False


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
    FIFO; unaffected traffic keeps normal admission. Captured running thread
    drain is C2-owned and gives no new claim entitlement. Other producer claims
    and whole-operation recovery remain C2/C3/D-owned.
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

    def _task_closure_uncommitted(self, task: Any, request: MoveRequest) -> bool:
        """Complete causal tree, declared chain targets and immutable bindings.

        No LIMIT, logical-ID inference, role files or filesystem work. Unknown
        ownership/JSON refuses; historical task/team attribution is read only.
        """
        db = self.store.db
        affected = set(request.affected_agents)
        teams = self._teams_reader() if self._teams_reader is not None else None
        pending = [task]
        seen: set[str] = set()
        def referenced(value: Any) -> bool:
            if isinstance(value, dict):
                if any(value.get(key) in affected for key in ("agent", "manager_agent", "principal")
                       if isinstance(value.get(key), str)):
                    return True
                if value.get("team") == request.source_team:
                    return True
                return any(referenced(v) for v in value.values())
            return isinstance(value, list) and any(referenced(v) for v in value)
        while pending:
            current = pending.pop()
            if current.id in seen:
                continue
            seen.add(current.id)
            if current.assigned_agent in affected or current.team == request.source_team:
                return True
            if teams is None:
                raise DeferredTeamMove("team_move_task_owner_unavailable")
            try:
                manager = teams.executable_manager_for_team(current.team)
            except KeyError as exc:
                raise DeferredTeamMove("team_move_task_team_unavailable") from exc
            if manager in affected:
                return True
            if current.assigned_agent is not None and current.assigned_agent not in teams.all_agents():
                raise DeferredTeamMove("team_move_task_owner_unavailable")
            if current.assigned_agent is None and manager is None:
                raise DeferredTeamMove("team_move_task_owner_unavailable")
            for kind, raw in (("chain", current.active_chain), ("fanout", current.active_fanout)):
                if raw is not None:
                    try:
                        data = json.loads(raw)
                        if kind == "chain":
                            from runtime.orchestrator.chain import ChainState
                            chain = ChainState.deserialize(raw)
                            if type(chain.step_index) is not int or not 0 <= chain.step_index <= len(chain.legs):
                                raise ValueError("unknown chain owner")
                        else:
                            from runtime.orchestrator.fanout import FanoutState
                            fanout = FanoutState.deserialize(raw)
                            if (not isinstance(fanout.manager_agent, str) or fanout.manager_agent not in teams.all_agents()
                                    or type(fanout.width) is not int or fanout.width != len(fanout.children_ids)
                                    or len(fanout.children_details) != fanout.width):
                                raise ValueError("unknown fanout owner")
                    except (ValueError, TypeError, KeyError) as exc:
                        raise DeferredTeamMove("team_move_task_graph_unavailable") from exc
                    if not isinstance(data, dict):
                        raise DeferredTeamMove("team_move_task_graph_unavailable")
                    if referenced(data):
                        return True
            ids = list(db.get_children(current.id))
            ids.extend(db.get_direct_revisits(current.id))
            ids.extend(x for x in (current.parent_task_id, current.revisit_of_task_id) if x)
            for tid in ids:
                related = db.get_task(tid)
                if related is None:
                    raise DeferredTeamMove("team_move_task_lineage_unavailable")
                pending.append(related)
        tables = {r[0] for r in db._conn.execute("SELECT name FROM sqlite_schema WHERE type='table'")}
        if "workflow_instances" in tables:
            for row in db._conn.execute("SELECT i.id,i.root_task_id,b.binding_bytes FROM workflow_instances i "
                    "JOIN workflow_binding_snapshots b ON b.id=i.binding_snapshot_id"):
                linked = row["root_task_id"] in seen or any(r[0] in seen for r in db._conn.execute(
                    "SELECT task_id FROM workflow_instance_tasks WHERE instance_id=?", (row["id"],)))
                if linked:
                    try:
                        if referenced(json.loads(row["binding_bytes"])):
                            return True
                    except (ValueError, TypeError) as exc:
                        raise DeferredTeamMove("team_move_workflow_owner_unavailable") from exc
        return False

    def check_bindings_uncommitted(self, raw: bytes) -> None:
        row = self.active_uncommitted()
        if row is None:
            return
        request = _decode(MoveRequest, row["request_bytes"])
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            raise DeferredTeamMove("team_move_workflow_owner_unavailable") from exc
        def affected(value: Any) -> bool:
            if isinstance(value, dict):
                return (value.get("principal") in request.affected_agents
                    or value.get("team") == request.source_team
                    or any(affected(v) for v in value.values()))
            return isinstance(value, list) and any(affected(v) for v in value)
        if affected(data):
            raise DeferredTeamMove("team_move_workflow_held", row["operation_id"])

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

    def check_task_uncommitted(self, task: Any) -> None:
        """Real allocation/claim owner calls within its DB transaction.

        Nested graph writers already own DB: they MUST NOT acquire the move
        gate here. The operation writer also owns DB through its COMMIT, so
        this check and allocation are indivisible with durable reservation.
        """
        row = self.active_uncommitted()
        if row is not None and self._task_closure_uncommitted(task, _decode(MoveRequest, row["request_bytes"])):
            raise DeferredTeamMove("team_move_task_held", row["operation_id"])

    def reserve_task_uncommitted(self, invocation: Any) -> TaskPreparation:
        self.check_task_uncommitted(invocation.task)
        teams = self._teams_reader() if self._teams_reader is not None else None
        if teams is None or not invocation.agent or invocation.agent not in teams.all_agents():
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
        self.check_task_uncommitted(task)

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
            if (not reservation.cleanup_failed and not invocation.unsettled
                    and (not reservation.possible_launch or reservation.quiescent)):
                self._reservations.pop(reservation.owner_id, None)

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
        """D calls AFTER durable release; callback is outside gate/DB locks.

        Existing generation-aware queue owner decides dedup/eligibility. No
        result token consumption, task allocation or release of the operation.
        """
        with self.gate, self.store.db._lock:
            if self.active_uncommitted() is not None:
                raise DeferredTeamMove("team_move_release_not_committed")
            deferred = tuple(self._deferred.items())
        for task_id, metadata in deferred:
            if not enqueue(task_id, metadata):
                continue  # Refused/pending publication retains its notification.
            with self.store.db._lock:
                if self._deferred.get(task_id) is metadata:
                    self._deferred.pop(task_id, None)

    def project(self, identity: PermanentAgentIdentity) -> AdmissionProjection:
        if identity.org_slug != self.store.org_slug or not identity.agent_id:
            raise ValueError("team_move_identity_owner_mismatch")
        row = self.store.read()
        held = row is not None and identity.agent_id in _decode(MoveRequest, row["request_bytes"]).affected_agents
        return AdmissionProjection(
            org_slug=identity.org_slug, agent_id=identity.agent_id, held=held,
            operation_id=row["operation_id"] if held else None,
            state_revision=row["state_revision"] if held else None,
            phase=row["phase"] if held else None,
            committed=bool(held and row["decision_bytes"] is not None),
        )

    def startup_contract(self) -> dict:
        """Data only, not an implemented attach/downgrade admission guard."""
        row = self.store.read()
        return {
            "capability_version": CAPABILITY_VERSION,
            "apply_available": APPLY_AVAILABLE,
            "not_integrated": NOT_INTEGRATED,
            "required_reader": "online-team-move@1" if row is not None else None,
            "unresolved_phase": Phase(row["phase"]).value if row is not None else None,
            "startup_requirements": STARTUP_REQUIREMENTS,
            "completion_requirements": COMPLETION_REQUIREMENTS,
        }
