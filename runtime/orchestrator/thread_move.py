"""Thread-specific admission, captured-turn drain and durable redelivery seams.

C2 never writes an operation, reset, roster or COMPLETE. D owns those decisions.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import Any, Callable, Iterator

from runtime.infrastructure.thread_admission import ThreadAdmissionStore, ThreadAttempt, ThreadLifecycle
from runtime.infrastructure.team_move_store import MoveRequest, _decode
from runtime.models import ThreadInvocationPurpose, ThreadInvocationStatus
from runtime.orchestrator.team_move import DeferredTeamMove, PermanentAgentIdentity

logger = logging.getLogger(__name__)

_current: ContextVar[ThreadPreparation | None] = ContextVar("team_move_thread_preparation", default=None)


def current_thread_preparation() -> ThreadPreparation | None:
    return _current.get()


@contextmanager
def bind_thread_preparation(owner: ThreadPreparation) -> Iterator[None]:
    token = _current.set(owner)
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def thread_preparation_effects() -> Iterator[None]:
    """Carry the actual thread or task owner into native filesystem helpers.

    Keep this existing facade for cache/direct/legacy executor callers; a task
    keeps its own Pause journal and reservation, never a thread/producer alias.
    """
    from runtime.orchestrator.task_pause import current_invocation
    owner = current_thread_preparation() or current_invocation()
    if owner is None:
        yield
    else:
        with owner.preparation_effects():
            yield


class ThreadPreparation:
    def __init__(self, admission: ThreadMoveAdmission, inv: Any, state: ThreadLifecycle) -> None:
        self.admission, self.inv, self.state = admission, inv, state
        self.identity = PermanentAgentIdentity(state.org_slug, state.agent_id)
        self.owner_live = True
        self.last_spec: Any = None
        self.executor: str | None = None
        self.model: str | None = None
        self.reasoning_effort: str | None = None
        self.claim: Any = None
        self.claimed = inv.started_at is not None
        self.attempt_open = False
        self.safe_selflaunch_defer = False

        self._repository_preparations = {}
        self._repository_effect_intervals = []
        self._repository_retained_effects = []
        self._repository_worker_active = False

    def repository_preparation_binding(self):
        from runtime.orchestrator.workspace_adapters import _repo_owner_binding
        return _repo_owner_binding(self)

    def repository_preparation_revalidate(self, evidence):
        from runtime.orchestrator.workspace_adapters import _repo_preparation_writer
        with _repo_preparation_writer(self, evidence):
            self.admission.revalidate_uncommitted(self)

    def record_repository_preparation(self, evidence, body):
        from runtime.orchestrator.workspace_adapters import _repo_native_record
        return _repo_native_record(self, evidence, body)

    def settle_repository_preparation(self, evidence):
        from runtime.orchestrator.workspace_adapters import _repo_native_settle
        return _repo_native_settle(self, evidence)

    @property
    def move_reservation(self) -> ThreadPreparation:
        return self

    @property
    def session_id(self) -> str:
        return self.state.session_id

    def _save(self, **changes: Any) -> None:
        candidate = self.state.model_copy(update=changes)
        self.admission.store.save_uncommitted(self.inv, candidate)
        self.state = candidate

    @contextmanager
    def preparation_effects(self) -> Iterator[None]:
        """Own native effects before a caught error can settle the invocation.

        The existing active lifecycle covers interruption. Only errors inside
        this effect interval become sticky cleanup uncertainty; ordinary early
        no-effect refusals do not. A later successful interval cannot clear it.
        """
        from runtime.orchestrator.workspace_adapters import _repo_effect_interval
        try:
            with _repo_effect_interval(self):
                yield
        except BaseException as exc:
            try:
                self.failed_cleanup()
            except BaseException as evidence:
                exc.add_note(f"thread preparation evidence failed: {evidence}")
            raise

    def before_fifo(self) -> None:
        with self.admission.move.gate, self.admission.db._lock:
            self.admission.revalidate_uncommitted(self)

    def start_phase(self, phase: str) -> None:
        with self.admission.db._lock:
            if self.state.retry_initialized is None:
                raise DeferredTeamMove("team_move_thread_retry_initialization_unknown")
            self.admission.write(self, phase=phase, ordinal=0, schedule=(), retry_initialized=False)
        self.before_fifo()

    def retry_schedule(self, boundary: str, schedule: tuple[float, ...]) -> tuple[int, tuple[float, ...]]:
        with self.admission.move.gate, self.admission.db._lock:
            self.admission.revalidate_uncommitted(self)
            if self.state.retry_initialized is None:
                raise DeferredTeamMove("team_move_thread_retry_initialization_unknown")
            if self.state.retry_initialized is False:
                self.admission.write(self, retry_initialized=True, boundary=boundary, schedule=tuple(float(x) for x in schedule))
            elif self.state.boundary != boundary:
                raise DeferredTeamMove("team_move_thread_retry_owner_mismatch")
            return self.state.ordinal, self.state.schedule

    def next_attempt(self, ordinal: int) -> None:
        # Persist original retry budget BEFORE sleeping/reacquiring; no gate in
        # the host terminal leaf. Each unknown attempt stays in the audit.
        with self.admission.db._lock:
            if self.state.retry_initialized is not True or not self.state.ordinal < ordinal <= len(self.state.schedule):
                raise DeferredTeamMove("team_move_thread_retry_budget")
            self.admission.write(self, ordinal=ordinal)

    def commit(self, ctx: Any = None) -> bool:
        with self.admission.move.gate, self.admission.db._lock:
            self.admission.revalidate_uncommitted(self)
            with ctx._lock if ctx is not None else nullcontext():
                if ctx is not None and ctx._terminal_reason is not None:
                    return False
                if self.attempt_open:
                    raise DeferredTeamMove("team_move_thread_attempt_unsettled")
                if self.state.retry_initialized is not True:
                    raise DeferredTeamMove("team_move_thread_retry_initialization_unknown")
                self.admission.db._conn.execute("BEGIN IMMEDIATE")
                previous = self.state
                try:
                    # The actual claim owner stamps lifecycle together with the
                    # durable possible-launch audit, under the terminal leaf.
                    if not self.claimed:
                        if self.inv.purpose is ThreadInvocationPurpose.REPLY:
                            self.claim = self.admission.db.claim_conversational_reply(
                                self.inv.invocation_token, executor=self.executor, model=self.model,
                                _move_owner=self,
                            )
                            if self.claim is None:
                                raise DeferredTeamMove("team_move_thread_claim_lost")
                        else:
                            self.admission.db._conn.execute(
                                "UPDATE thread_invocations SET started_at=COALESCE(started_at,?),"
                                "session_id=?,executor=?,model=? WHERE id=? AND status='pending'",
                                (self.admission.now(), self.session_id, self.executor, self.model, self.inv.id))
                        if self.reasoning_effort is not None:
                            self.admission.db.insert_audit_log_uncommitted(self.inv.thread_id, self.identity.agent_id,
                                "thread_reasoning_selection", {"invocation_token": self.inv.invocation_token,
                                "session_id": self.session_id, "executor": self.executor, "model": self.model,
                                "reasoning_effort": self.reasoning_effort})
                        self.claimed = True
                    attempts = self.state.attempts + (ThreadAttempt(attempt_id=uuid.uuid4().hex,
                        phase=self.state.phase, boundary=self.state.boundary, ordinal=self.state.ordinal,
                        executor=self.executor, model=self.model, possible_launch=True),)
                    self._save(attempts=attempts, deferred=False, producer_settled=False)
                    self.admission.db._conn.commit()
                    self.attempt_open = True
                    self.safe_selflaunch_defer = False
                except BaseException as exc:
                    self.admission.db._conn.rollback()
                    self.state = previous
                    self.claimed = self.inv.started_at is not None
                    if isinstance(exc, Exception) and not isinstance(exc, DeferredTeamMove):
                        raise DeferredTeamMove("team_move_thread_commit_unavailable") from exc
                    raise
        return True

    def _thread_move_commit(self, ctx: Any) -> Any:
        return self.last_spec if self.commit(ctx) else None

    def __call__(self) -> None:
        # Host recognizes only the private commitment protocol above.
        pass

    def terminal(self, outcome: Any) -> None:
        # Called only after backend finish; DB only, NEVER move gate.
        with self.admission.db._lock:
            if outcome is not None:
                from runtime.platform.session_backend import TerminalReason
                if outcome.cleanup_error is not None or outcome.terminal_reason is TerminalReason.PREPARE_FAILURE:
                    # Backend preparation/abandonment can fail before the first
                    # thread launch commitment. No attempt is not cleanup proof.
                    self.failed_cleanup()
            if not self.attempt_open:
                return
            receipt = None if outcome is None else outcome.receipt
            clean = bool(receipt is not None and receipt.backend != "passthrough"
                and receipt.quiescent is True and receipt.cleanup_status.value != "incomplete"
                and not receipt.survivors and outcome.cleanup_error is None)
            no_launch = self.safe_selflaunch_defer and not self.state.attempts[-1].launched
            clean = clean or bool(no_launch and not self.state.failed_cleanup
                and (outcome is None or outcome.cleanup_error is None))
            last = self.state.attempts[-1].model_copy(update={"settled": True, "quiescent": clean,
                "no_launch_deferred": bool(no_launch)})
            self.admission.write(self, attempts=self.state.attempts[:-1] + (last,))
            self.attempt_open = False

    def before_selflaunch(self) -> None:
        # Real executors call this AFTER throttle and immediately BEFORE
        # Popen. A passthrough host reservation is not a running process.
        try:
            self.before_fifo()
        except DeferredTeamMove:
            self.safe_selflaunch_defer = self.attempt_open
            raise
        if not self.attempt_open and not self.commit():
            raise DeferredTeamMove("team_move_thread_selflaunch_cancelled")

    def on_started(self) -> None:
        with self.admission.db._lock:
            if not self.attempt_open:
                raise DeferredTeamMove("team_move_thread_process_owner_missing")
            last = self.state.attempts[-1].model_copy(update={"launched": True})
            try:
                self.admission.write(self, attempts=self.state.attempts[:-1] + (last,))
            except Exception:
                # A process already exists. Keep its actual observation and
                # failed attribution without interrupting containment cleanup.
                self.state = self.state.model_copy(update={"failed_cleanup": True,
                    "attempts": self.state.attempts[:-1] + (last,)})
                logger.exception("Thread launched observation persistence failed; retaining unknown owner")

    def direct_terminal(self) -> None:
        # Provider return cannot certify the uncontained tree.
        self.terminal(None)

    def failed_cleanup(self, *, repository_deadline: float | None = None) -> None:
        if repository_deadline is not None:
            # An expired/busy persistence gate cannot delay the local latch.
            self.state = self.state.model_copy(update={"failed_cleanup": True})
            from runtime.orchestrator.workspace_adapters import _repo_preparation_cleanup
            with _repo_preparation_cleanup(self, repository_deadline):
                self.admission.write(self, failed_cleanup=True)
            return
        with self.admission.db._lock:
            # Retain uncertainty BEFORE persistence: interruption of that write
            # must not let a caught preparation error finish as a clean owner.
            # The writer's rollback also restores this already-unknown state.
            self.state = self.state.model_copy(update={"failed_cleanup": True})
            try:
                self.admission.write(self, failed_cleanup=True)
            except Exception:
                logger.exception("Thread cleanup attribution persistence failed; retaining unknown owner")

    def finish(self, *, deferred: bool, failed: bool) -> None:
        with self.admission.db._lock:
            failed = failed or any(not v.closed for v in self._repository_preparations.values())
            self.owner_live = False
            self.admission.write(self, deferred=deferred, producer_settled=True,
                failed_cleanup=self.state.failed_cleanup or failed)
            if all(v.closed for v in self._repository_preparations.values()):
                self.admission.live.pop(self.inv.id, None)


class ThreadMoveAdmission:
    def __init__(self, move: Any) -> None:
        self.move, self.db = move, move.store.db
        self.store = ThreadAdmissionStore(self.db, move.store.org_slug)
        self.live: dict[int, ThreadPreparation] = {}
        self.captured: dict[str, frozenset[tuple[int, str, str]]] = {}

    @staticmethod
    def now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    def held_uncommitted(self, agent_id: str) -> dict | None:
        row = self.move.active_uncommitted()
        return row if row is not None and agent_id in _decode(MoveRequest, row["request_bytes"]).affected_agents else None

    def check_agent_uncommitted(self, agent_id: str) -> None:
        row = self.held_uncommitted(agent_id)
        if row is not None:
            raise DeferredTeamMove("team_move_thread_held", row["operation_id"])

    def write(self, owner: ThreadPreparation, **changes: Any) -> None:
        # Leaf writer may be called under DB, never obtains the move gate.
        previous = owner.state
        self.db._conn.execute("BEGIN IMMEDIATE")
        try:
            owner._save(**changes)
            self.db._conn.commit()
        except BaseException as exc:
            self.db._conn.rollback()
            owner.state = previous
            if isinstance(exc, Exception) and not isinstance(exc, DeferredTeamMove):
                raise DeferredTeamMove("team_move_thread_evidence_unavailable") from exc
            raise

    def reserve(self, token: str) -> ThreadPreparation | None:
        with self.move.gate, self.db._lock:
            inv = self.db.get_pending_invocation(token)
            if inv is None:
                return None
            self.check_agent_uncommitted(inv.agent_name)
            if not self.db._thread_release_pair_permitted_uncommitted(inv.thread_id, inv.agent_name):
                raise DeferredTeamMove("thread_delivery_release_permission_revoked")
            # These are trusted persisted permanent owners. No request actor,
            # logical-ID parsing, prompt file or display name supplies identity.
            thread = self.db.get_thread(inv.thread_id)
            if thread is None or thread.status != "open" or not self.db.is_thread_participant(inv.thread_id, inv.agent_name):
                raise DeferredTeamMove("team_move_thread_identity_unavailable")
            if inv.id in self.live:
                raise DeferredTeamMove("team_move_thread_owner_live")
            state = self.store.read_uncommitted(inv)
            if state is not None:
                if state.retry_initialized is None:
                    raise DeferredTeamMove("team_move_thread_retry_initialization_unknown")
                if state.unknown or not (state.deferred and state.producer_settled):
                    raise DeferredTeamMove("team_move_thread_owner_unsettled")
            elif inv.started_at is not None:
                raise DeferredTeamMove("team_move_thread_legacy_owner_unknown")
            else:
                state = ThreadLifecycle(org_slug=self.move.store.org_slug,
                    invocation_id=inv.id, thread_id=inv.thread_id, agent_id=inv.agent_name,
                    session_id=f"sess-{uuid.uuid4().hex}", preparation_id=uuid.uuid4().hex, retry_initialized=False)
            owner = ThreadPreparation(self, inv, state)
            self.write(owner, deferred=False, producer_settled=False)
            self.live[inv.id] = owner
            return owner

    def revalidate_uncommitted(self, owner: ThreadPreparation) -> None:
        if self.live.get(owner.inv.id) is not owner or not owner.owner_live:
            raise DeferredTeamMove("team_move_thread_preparation_missing")
        self.check_agent_uncommitted(owner.identity.agent_id)
        if not self.db._thread_release_pair_permitted_uncommitted(owner.inv.thread_id, owner.identity.agent_id):
            raise DeferredTeamMove("thread_delivery_release_permission_revoked")
        inv = self.db.get_pending_invocation(owner.inv.invocation_token)
        if inv is None or (inv.id, inv.thread_id, inv.agent_name) != (owner.inv.id, owner.inv.thread_id, owner.identity.agent_id):
            raise DeferredTeamMove("team_move_thread_invocation_changed")
        if inv.session_id is not None and inv.session_id != owner.session_id:
            raise DeferredTeamMove("team_move_thread_session_changed")
        if owner.claimed and inv.purpose is ThreadInvocationPurpose.REPLY:
            delivery = self.db.get_reply_delivery_state(inv.thread_id, inv.agent_name)
            if (delivery is None or owner.claim is None or delivery.running_invocation_token != inv.invocation_token
                    or (delivery.running_from_seq, delivery.running_through_seq) !=
                    (owner.claim.running_from_seq, owner.claim.running_through_seq)):
                raise DeferredTeamMove("team_move_thread_running_claim_changed")
        thread = self.db.get_thread(inv.thread_id)
        if thread is None or thread.status != "open" or not self.db.is_thread_participant(inv.thread_id, inv.agent_name):
            raise DeferredTeamMove("team_move_thread_participant_changed")

    def callback_uncommitted(self, token: str) -> None:
        inv = self.db.get_invocation_any_status(token)
        if inv is None:
            return  # Existing callback validator owns missing-token response.
        row = self.held_uncommitted(inv.agent_name)
        if row is None:
            return
        owner = self.live.get(inv.id)
        captured = self.captured.get(row["operation_id"], frozenset())
        if (row["phase"] != "waiting" or owner is None or not owner.attempt_open
                or (inv.id, owner.session_id, owner.state.preparation_id) not in captured):
            raise DeferredTeamMove("team_move_thread_callback_not_captured", row["operation_id"])

    def settlement_uncommitted(self, token: str) -> None:
        inv = self.db.get_invocation_any_status(token)
        if inv is None:
            return
        row = self.held_uncommitted(inv.agent_name)
        if row is None:
            return
        owner = self.live.get(inv.id)
        if (row["phase"] != "waiting" or owner is None
                or (inv.id, owner.session_id, owner.state.preparation_id) not in self.captured.get(row["operation_id"], ())):
            raise DeferredTeamMove("team_move_thread_settlement_not_captured", row["operation_id"])

    def eviction_uncommitted(
        self, owner: ThreadPreparation, *, thread_id: str, agent_id: str,
        executor: str,
    ) -> None:
        """Validate native eviction observation under DB, without launch admission.

        A winning hold cannot erase an already-observed provider eviction.
        Only this actual live producer's durable launched/settled initial
        attempt qualifies. This grants no callback, retry, reset or release;
        earlier uncertain cleanup stays in the unchanged lifecycle history.
        """
        inv = self.db.get_invocation_any_status(owner.inv.invocation_token)
        state = self.store.read_uncommitted(owner.inv)
        if (owner.admission is not self or self.live.get(owner.inv.id) is not owner
                or not owner.owner_live or not owner.claimed or owner.attempt_open
                or (owner.inv.thread_id, owner.identity.agent_id) != (thread_id, agent_id)
                or inv is None or (inv.id, inv.thread_id, inv.agent_name, inv.session_id) !=
                (owner.inv.id, thread_id, agent_id, owner.session_id)
                or state != owner.state or state is None or state.phase != "initial"
                or not state.attempts or state.attempts[-1].phase != "initial"
                or state.attempts[-1].executor != executor
                or not state.attempts[-1].launched or not state.attempts[-1].settled):
            raise DeferredTeamMove("team_move_thread_eviction_owner_unavailable")

    def participant_write_uncommitted(self, agent_id: str, *, token: str | None = None) -> None:
        row = self.held_uncommitted(agent_id)
        if row is None:
            return
        if row["decision_bytes"] is not None:
            reset = getattr(self, "_reset_owner", None)
            if reset == (row["operation_id"], agent_id) and row["phase"] == "reset":
                return  # Only the synchronous audited native reset owner below.
            raise DeferredTeamMove("team_move_thread_participant_write_held", row["operation_id"])
        if token is not None:
            self.callback_uncommitted(token)

    def reset_operation_agent(self, operation_id: str, agent_id: str):
        """Native audited reset, with exact operation replay and post-state proof.

        No caller actor/rows/success flags. Attribution comes only from the saved
        future-service admission; this method does not itself admit a founder.
        The existing DB reset owner retains its actual transaction and row count.
        """
        from runtime.infrastructure.team_move_store import ResetEvidence, TeamMoveError, _digest
        import json
        with self.db._lock:
            row = self.move.active_uncommitted()
            if (row is None or row["operation_id"] != operation_id or row["phase"] != "reset"
                    or row["principal"] != "founder" or getattr(self, "_reset_owner", None) is not None
                    or agent_id not in _decode(MoveRequest, row["request_bytes"]).affected_agents):
                raise TeamMoveError("team_move_reset_owner")
            scope = "config:team-move:" + operation_id + ":" + agent_id
            audits = self.db._conn.execute(
                "SELECT id,agent,payload FROM audit_log WHERE task_id=? AND action='thread_session_invalidated' ORDER BY id", (scope,)
            ).fetchall()
            def post_state():
                rows = self.db._conn.execute(
                    "SELECT thread_id,agent_name,agent_session_id,last_resumed_seq FROM thread_participants WHERE agent_name=? ORDER BY thread_id", (agent_id,)
                ).fetchall()
                if any(r["agent_session_id"] is not None or r["last_resumed_seq"] != 0 for r in rows):
                    raise TeamMoveError("team_move_reset_post_state")
                return rows
            if audits:
                if len(audits) != 1 or audits[0]["agent"] != row["principal"]:
                    raise TeamMoveError("team_move_reset_audit_unknown")
                from runtime.infrastructure.team_move_store import _unique_object
                payload = json.loads(audits[0]["payload"], object_pairs_hook=_unique_object)
                rows = post_state()
                if payload != {"reason": "team_move", "rows": len(rows), "name": agent_id} or not rows:
                    raise TeamMoveError("team_move_reset_audit_unknown")
                audit_id = audits[0]["id"]
            else:
                # The guarded owner is meaningful ONLY under this same RLock.
                self._reset_owner = (operation_id, agent_id)
                try:
                    count = self.db.reset_thread_sessions_for_agent(agent_id,
                        audit_scope_id=scope, audit_agent=row["principal"], audit_reason="team_move")
                finally:
                    self._reset_owner = None
                rows = post_state()
                if count != len(rows):
                    raise TeamMoveError("team_move_reset_count_changed")
                saved = self.db._conn.execute(
                    "SELECT id FROM audit_log WHERE task_id=? AND action='thread_session_invalidated' ORDER BY id", (scope,)
                ).fetchall()
                if len(saved) != (1 if count else 0):
                    raise TeamMoveError("team_move_reset_audit_unknown")
                audit_id = saved[0]["id"] if saved else None
            raw = json.dumps([tuple(r) for r in rows], separators=(",", ":"), ensure_ascii=False).encode()
            return ResetEvidence(rows=len(rows), audit_id=audit_id, post_state_digest=_digest(raw))

    def capture_running_uncommitted(self, operation_id: str) -> tuple[dict, ...]:
        """D calls under move gate/DB AFTER durable WAITING intent commits.

        Capture is not a launch entitlement; commit always checks hold. Only
        this exact live attempt may finish callback/cleanup under old context.
        """
        if not self.move.gate._is_owned() or not self.db._lock._is_owned():
            raise DeferredTeamMove("team_move_thread_capture_lock_owner")
        row = self.move.active_uncommitted()
        if row is None or row["operation_id"] != operation_id or row["phase"] != "waiting":
            raise DeferredTeamMove("team_move_thread_capture_owner")
        request = _decode(MoveRequest, row["request_bytes"])
        if operation_id not in self.captured:
            self.captured[operation_id] = frozenset((o.inv.id, o.session_id, o.state.preparation_id)
                for o in self.live.values() if request.drain_threads and o.attempt_open
                and o.state.attempts[-1].launched
                and o.identity.agent_id in request.affected_agents)
        return tuple(dict(invocation_id=i, session_id=s, preparation_id=p)
            for i, s, p in sorted(self.captured[operation_id]))

    def census(self, agent_ids: tuple[str, ...]) -> tuple[dict, ...]:
        """Complete thread evidence; never an org-wide/process idle certificate."""
        with self.db._lock:
            result = []
            for raw in self.db._conn.execute("SELECT * FROM thread_invocations ORDER BY id"):
                inv = self.db._row_to_invocation(raw)
                if inv.agent_name not in agent_ids:
                    continue
                state = self.store.read_uncommitted(inv)
                owner = self.live.get(inv.id)
                code = None
                if state and state.retry_initialized is None and inv.status is ThreadInvocationStatus.PENDING:
                    code = "thread_retry_initialization_unknown"
                elif state and (state.unknown or not state.producer_settled):
                    code = "thread_cleanup_unknown" if state.unknown else ("thread_running" if owner and owner.attempt_open and owner.state.attempts[-1].launched else "thread_preparation_or_lost_owner")
                elif inv.status is ThreadInvocationStatus.PENDING:
                    code = "thread_pending_obligation" if inv.started_at is None or (state and state.deferred) else "thread_legacy_owner_unknown"
                if code and (self.db.get_thread(inv.thread_id) is None
                        or not self.db.is_thread_participant(inv.thread_id, inv.agent_name)):
                    code = "thread_identity_unavailable"
                if code:
                    result.append(dict(code=code, invocation_id=inv.id, thread_id=inv.thread_id,
                        agent_id=inv.agent_name, session_id=inv.session_id,
                        preparation_id=state.preparation_id if state else None,
                        attempts=tuple(a.model_dump() for a in state.attempts) if state else ()))
            for table, predicate, code in (
                    ("thread_reply_breaker_episodes", "state='probe' OR probe_lease_id IS NOT NULL", "thread_breaker_probe"),
                    ("thread_exchange_deferrals", "state='held' OR catchup_pending=1", "thread_exchange_or_catchup"),
                    ("thread_reply_delivery_state", "running_invocation_token IS NOT NULL", "thread_running_delivery")):
                for row in self.db._conn.execute(f"SELECT * FROM {table} WHERE {predicate}"):
                    if row["agent_name"] in agent_ids:
                        result.append(dict(code=code, thread_id=row["thread_id"], agent_id=row["agent_name"]))
            for row in self.db._conn.execute("SELECT thread_id,agent_name,acknowledged_through_seq,required_through_seq FROM thread_reply_delivery_state WHERE required_through_seq>acknowledged_through_seq"):
                if row["agent_name"] in agent_ids:
                    result.append(dict(code="thread_pending_obligation", thread_id=row["thread_id"],
                        agent_id=row["agent_name"], acknowledged_through_seq=row["acknowledged_through_seq"],
                        required_through_seq=row["required_through_seq"]))
            # Exchanges may include an affected cohort with no deferral row.
            for row in self.db._conn.execute("SELECT thread_id,exchange_id FROM thread_reply_exchange WHERE state='open'"):
                if any(self.db.is_thread_participant(row["thread_id"], a) for a in agent_ids):
                    result.append(dict(code="thread_open_exchange", thread_id=row["thread_id"], exchange_id=row["exchange_id"]))
            return tuple(result)

    async def bounded_drain(self, operation_id: str, *, timeout_seconds: float = 30.0) -> tuple[dict, ...]:
        if not 0 <= timeout_seconds <= 30:
            raise ValueError("thread drain deadline must be bounded by 30 seconds")
        deadline = time.monotonic() + timeout_seconds
        while True:
            with self.move.gate, self.db._lock:
                row = self.move.active_uncommitted()
                if row is None or row["operation_id"] != operation_id or row["phase"] != "waiting":
                    raise DeferredTeamMove("team_move_thread_drain_owner")
                if operation_id not in self.captured:
                    raise DeferredTeamMove("team_move_thread_capture_missing")
                ids = _decode(MoveRequest, row["request_bytes"]).affected_agents
                blockers = tuple(b for b in self.census(ids) if b["code"] != "thread_pending_obligation")
            if not blockers or time.monotonic() >= deadline:
                return blockers  # D owns deadline refusal/forward decision.
            await asyncio.sleep(min(0.05, max(0.0, deadline - time.monotonic())))

    def _settled_without_owner(self, inv: Any, state: Any) -> bool:
        # The producer finished with certain cleanup and no deferral, and no
        # live owner remains (e.g. its runner was cancelled by shutdown after
        # the provider attempt settled quiescent). Nothing owns the pending
        # receipt any more, so ordinary pre-C2 restart recovery applies.
        return bool(state.producer_settled and not state.deferred and not state.unknown
            and state.retry_initialized is not None and inv.id not in self.live)

    def preserve_recovery_uncommitted(self, inv: Any) -> bool:
        # Ordinary pre-C2 restart rules remain when no C2 state/hold exists.
        # Held/uncertain evidence is NEVER reaped/rekeyed to claim clearance.
        if self.held_uncommitted(inv.agent_name) is not None:
            return True
        from runtime.infrastructure.thread_delivery_release import delivery_view, pair_permission
        view = delivery_view(self.db, inv.thread_id)
        if not pair_permission(view, inv.thread_id, inv.agent_name):
            return True
        state = self.store.read_uncommitted(inv)
        return state is not None and not self._settled_without_owner(inv, state)

    def preserve_pair_uncommitted(self, thread_id: str, agent_id: str) -> bool:
        from runtime.infrastructure.thread_delivery_release import delivery_view
        return self._preserve_pair_uncommitted(thread_id, agent_id,
            view=lambda: delivery_view(self.db, thread_id), read_state=self.store.read_uncommitted)

    def _preserve_pair_uncommitted(
        self, thread_id: str, agent_id: str, *, view: Callable[[], Any],
        read_state: Callable[[Any], ThreadLifecycle | None],
    ) -> bool:
        # Cutover supplies only local lazy readers, never permission/live/hold
        # conclusions. Every other caller retains fresh ordinary reads.
        if self.held_uncommitted(agent_id) is not None:
            return True
        from runtime.infrastructure.thread_delivery_release import pair_permission
        materialized = view()
        if not pair_permission(materialized, thread_id, agent_id):
            return True
        for raw in self.db._conn.execute("SELECT * FROM thread_invocations WHERE thread_id=? AND agent_name=?",
                (thread_id, agent_id)):
            inv = self.db._row_to_invocation(raw)
            if materialized is not None and materialized.released(inv.id):
                continue  # Delivery interpretation only; census remains raw.
            state = read_state(inv)
            if state is None:
                continue
            if inv.status is ThreadInvocationStatus.PENDING and self._settled_without_owner(inv, state):
                continue
            if inv.status is ThreadInvocationStatus.PENDING or state.unknown or not state.producer_settled:
                return True
        return False

    def recovery_protected_ids(self) -> tuple[int, ...]:
        with self.db._lock:
            return tuple(inv.id for inv in self.db.list_pending_thread_invocations()
                if self.preserve_recovery_uncommitted(inv))

    async def redeliver_after_release(self, queue: Any) -> None:
        with self.move.gate, self.db._lock:
            if self.move.active_uncommitted() is not None:
                raise DeferredTeamMove("team_move_release_not_committed")
            tokens = self.runnable_tokens()
        from runtime.daemon.thread_queue import ThreadJob
        for token in tokens:
            await queue.put_once(ThreadJob(org_slug=self.move.store.org_slug, invocation_token=token))

    def runnable_tokens(self) -> tuple[str, ...]:
        """Durable release/redelivery census, no process-local defer list.

        Safe unstarted/correctly deferred obligations only. Unknown attempts,
        active owners and legacy started rows require their actual recovery owner.
        """
        with self.move.gate, self.db._lock:
            self.move.active_uncommitted()  # Malformed presence fails closed.
            result = []
            for inv in self.db.list_pending_thread_invocations():
                if self.held_uncommitted(inv.agent_name) is not None or inv.id in self.live:
                    continue
                if not self.db._thread_release_pair_permitted_uncommitted(inv.thread_id, inv.agent_name):
                    continue
                state = self.store.read_uncommitted(inv)
                if state is not None:
                    if state.retry_initialized is None or state.unknown or not (state.deferred and state.producer_settled):
                        continue
                elif inv.started_at is not None:
                    continue
                result.append(inv.invocation_token)
            return tuple(result)
