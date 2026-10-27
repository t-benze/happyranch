"""Private task-producer arbitration; no platform or executor policy API.

The process-local gate assumes one supported daemon per org. Durable possible
launch evidence survives a crash; absence of a PID is never a no-launch proof.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sys
import uuid
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable

from runtime.infrastructure.task_pause_controls import PauseControlError, TaskPauseStore, now
from runtime.models import BlockKind, TaskRecord, TaskStatus


class DeferredRootPause(Exception):
    """A hold winner, never an ExecutorResult failure or business claim."""


_invocation: ContextVar[TaskInvocation | None] = ContextVar("task_pause_invocation", default=None)


def current_invocation() -> TaskInvocation | None:
    return _invocation.get()


@contextmanager
def task_preparation_effects():
    """Attribute native preparation to the already-reserved real task owner."""
    owner = current_invocation()
    if owner is None:
        yield
    else:
        with owner.preparation_effects():
            yield


def fingerprint(task: TaskRecord) -> dict:
    return {"status": task.status.value, "block_kind": task.block_kind.value if task.block_kind else None,
            "count": task.orchestration_step_count, "session_id": task.current_session_id,
            "agent": task.assigned_agent, "fanout": task.active_fanout,
            "jobs": task.blocked_on_job_ids, "parent": task.parent_task_id,
            "brief_digest": hashlib.sha256(task.brief.encode()).hexdigest()}


def eligible(orch: Any, task: TaskRecord) -> bool:
    """The unchanged ordinary pending / satisfied parked-carrier predicate."""
    from runtime.orchestrator.run_step import _child_has_landed_terminal_result, TERMINAL_STATES
    if task.cancelled_at is not None:
        return False
    if task.status == TaskStatus.PENDING:
        return True
    if task.status != TaskStatus.IN_PROGRESS:
        return False
    if task.block_kind == BlockKind.DELEGATED:
        children = [orch._db.get_task(cid) for cid in orch._db.get_children(task.id)]
        return all(c is not None and (c.status in TERMINAL_STATES or _child_has_landed_terminal_result(orch, c))
                   for c in children)
    if task.block_kind == BlockKind.BLOCKED_ON_JOB:
        try:
            ids = json.loads(task.blocked_on_job_ids or "[]")
        except (ValueError, TypeError):
            return False
        return (isinstance(ids, list) and bool(ids) and all(isinstance(jid, str) and
                orch._db.get_job_status(jid) in {"completed", "failed", "rejected"} for jid in ids))
    return False


def _observe_settled_producers(store: TaskPauseStore, row: dict) -> None:
    """Observe an already-applied exact result, never consume/replay it.

    Unknown tree evidence survives. A past producer with no live result tail
    must not impersonate the next logical task step after authentic settlement.
    """
    retained = []
    for entry in row["journal"]["entries"]:
        retained.append(entry)
        if (entry["owner"] == "job" or entry["phase"] not in {"unknown", "retry_deferred", "recovery_deferred"}
                or entry["context"].get("producer_settled") or entry["id"] in store._live):
            continue
        task = store.db.get_task(entry["task_id"])
        sid = (entry["context"]["recovery_return"]["origin_session_id"]
               if entry["phase"] == "recovery_deferred" else entry["session_id"])
        result = store.db.get_latest_task_result(entry["task_id"], entry["agent"], sid)
        if (task is not None and result is not None and type(result["id"]) is int
                and task.orchestration_step_count >= entry["fingerprint"]["count"] + (entry["owner"] != "recovery")
                and (task.status != TaskStatus.IN_PROGRESS or task.block_kind is not None
                     or task.current_session_id != sid)):
            if entry["phase"] != "unknown" and not entry["context"].get("execution_unknown"):
                retained.pop()  # Known no-live-attempt retry, already settled.
            else:
                entry["phase"] = "unknown"
                entry["context"]["execution_unknown"] = True
                entry["context"]["producer_settled"] = True
    row["journal"]["entries"] = retained


def _authenticate_v2_retry(store: TaskPauseStore, entry: dict) -> None:
    """Recheck the complete original generation proof; never admit it again."""
    generation = entry["owner_identity"].get("authority_v2_generation")
    notification = store.db.get_authority_policy_v2_recovery_notification(generation)
    task = store.db.get_task(entry["task_id"])
    if (notification is None or notification.state != "settled"
            or notification.root_task_id != entry["task_id"]
            or notification.manager_agent != entry["agent"]
            or notification.next_session_id != entry["session_id"]
            or task is None or task.cancelled_at is not None
            or task.status != TaskStatus.IN_PROGRESS or task.block_kind is not None
            or task.assigned_agent != entry["agent"] or task.current_session_id != entry["session_id"]
            or task.orchestration_step_count != entry["fingerprint"]["count"] + 1):
        raise DeferredRootPause("v2 retry owner changed")
    outcome = store.db._settle_v2_continuation_generation_uncommitted(
        root_task_id=task.id, manager_agent=notification.manager_agent,
        manager_session_id=notification.manager_session_id, result_id=notification.result_id,
        generation_id=generation, next_session_id=entry["session_id"], now_dt=datetime.now(timezone.utc))
    if outcome.status != "already_settled_exact":
        # Pause admission originally commits claim + settlement together. A
        # missing/changed proof must not be repaired by a retry publisher.
        raise DeferredRootPause("v2 retry proof unavailable")


@dataclass
class TaskInvocation:
    orch: Any
    task: TaskRecord
    agent: str
    session_id: str
    owner: str = "ordinary"
    metadata: dict = field(default_factory=dict)
    identity: str = field(default_factory=lambda: uuid.uuid4().hex)
    unsettled: bool = False
    admitted: bool = False
    attempt_committed: bool = False
    recovery_deadline: float | None = None
    admission_effects: Callable[[], None] | None = None
    publish_binding: Callable[[], None] | None = None
    final_prompt: Callable[[], Any] | None = None
    last_spec: Any = None
    prompt_reader: Callable[[], str] | None = None
    workflow_dispatcher: Any = None
    move_reservation: Any = None
    refusal_attempt_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    launch_observed: bool = False
    preparation_cause: dict | None = None
    _repository_preparations: dict = field(default_factory=dict, repr=False)
    _repository_effect_intervals: list = field(default_factory=list, repr=False)
    _repository_retained_effects: list = field(default_factory=list, repr=False)
    _repository_worker_active: bool = False
    _startup_validation: Callable[[], None] | None = field(default=None, repr=False)

    def repository_preparation_binding(self):
        from runtime.orchestrator.workspace_adapters import _repo_owner_binding
        return _repo_owner_binding(self)

    def repository_preparation_revalidate(self, evidence):
        from runtime.orchestrator.launch_refusals import _current
        from runtime.orchestrator.workspace_adapters import _repo_preparation_writer
        with _repo_preparation_writer(self, evidence):
            self.store.held_uncommitted(self.task.id)
            self.orch._team_moves.revalidate_uncommitted(self.move_reservation)
            if self.store._live.get(self.identity) is not self or not _current(self) or self.launch_observed:
                raise RuntimeError("repository_preparation_owner_changed")

    def record_repository_preparation(self, evidence, body):
        from runtime.orchestrator.workspace_adapters import _repo_native_record
        return _repo_native_record(self, evidence, body)

    def settle_repository_preparation(self, evidence):
        if evidence.closed:
            return (evidence.owner is self and self._repository_preparations.get(evidence.obligation) is evidence
                and self.move_reservation.repository_obligations.get(evidence.obligation) is True)
        if (evidence.owner is not self or not evidence.worker_returned or evidence.uncertain
                or evidence.pending or self.move_reservation.cleanup_failed):
            return False
        from runtime.orchestrator.workspace_adapters import _repo_effect_inventory
        inventory = _repo_effect_inventory(self)
        evidence.emit("outer_closed", complete=True, inventory=inventory)
        from runtime.orchestrator.workspace_adapters import _repo_close_preparation
        # The original writer owns digest/append and post-COMMIT publication;
        # closing only this obligation needs no later DB reacquisition.
        return _repo_close_preparation(self, evidence)

    @property
    def store(self) -> TaskPauseStore:
        return self.orch._db._task_pause_store

    def _fresh_validation(self, additional: Callable[[], None] | None = None) -> Callable[[], None] | None:
        """Keep one ordinary/v2/recovery selection across the original fresh seams."""
        if self.owner not in {"ordinary", "v2", "recovery"}:
            return additional
        if self._startup_validation is None:
            from runtime.daemon.startup_recovery import ordinary_admission_validation
            self._startup_validation = ordinary_admission_validation(self.orch)
        selected = self._startup_validation

        def validate() -> None:
            selected()
            if additional is not None:
                additional()

        validate()
        return validate

    def prepare(self, *, validate_admission: Callable[[], None] | None = None) -> None:
        validate_admission = self._fresh_validation(validate_admission)
        if self.owner == "coding":
            from runtime.daemon.startup_recovery import coding_admission_validation
            coding_validate = coding_admission_validation(self.orch._workflow_coding_dispatch)
            previous_validation = validate_admission

            def validate_admission() -> None:
                coding_validate()
                if previous_validation is not None:
                    previous_validation()

            self.orch._workflow_coding_dispatch.require_task_ready(self.task.id)
        store, move = self.store, self.orch._team_moves
        committed = False

        def record_commit() -> None:
            nonlocal committed
            committed = True

        try:
            with self.store.writer(self.task.id, admission=True, validate_admission=validate_admission,
                                   committed=record_commit if validate_admission is not None else None) as (root, row):
                self.store.held_uncommitted(self.task.id)
                self.orch._team_moves.check_task_uncommitted(self.task)
                row = row or self.store.ensure_uncommitted(root["id"])
                _observe_settled_producers(self.store, row)
                entries = row["journal"]["entries"]
                if any(e["task_id"] == self.task.id and e["owner"] != "job" and not e["context"].get("producer_settled") for e in entries):
                    raise DeferredRootPause("invocation already owned")
                self.move_reservation = self.orch._team_moves.reserve_task_uncommitted(self)
                entries.append({"id": self.identity, "org": self.store.org_slug, "root_task_id": root["id"],
                                "task_id": self.task.id, "agent": self.agent, "session_id": self.session_id,
                                "owner": self.owner, "owner_identity": {key: self.metadata[key] for key in
                                    ("authority_v2_generation", "trigger", "triggering_job_id") if key in self.metadata},
                                "fingerprint": fingerprint(self.task), "generation": row["generation"],
                                "phase": "prepared", "retry": {}, "context": {}, "started_at": None,
                                "captured_generation": None})
                self.store.save_journal_uncommitted(row)
            with self.store.db._lock:
                if validate_admission is not None:
                    validate_admission()
                self.store._live[self.identity] = self
                coding = getattr(self.orch, "_workflow_coding_dispatch", None)
                if coding is not None:
                    coding.bind_queue_invocation(self)
        except BaseException as primary:
            if validate_admission is not None and committed:
                # No launch can precede prepare returning. This SAME committed
                # preparation still owes its native no-launch journal/move tail
                # when postcommit registry admission closes. Failed settlement
                # retains uncertainty; it never replaces the admission error.
                try:
                    if self.store is not store or self.orch._team_moves is not move:
                        if self.move_reservation is not None:
                            self.move_reservation.failed_cleanup()
                        raise RuntimeError("task_preparation_settlement_owner_changed")
                    release_live(self)
                except BaseException as secondary:
                    if self.move_reservation is not None:
                        self.move_reservation.failed_cleanup()
                    primary.add_note(f"task preparation settlement also failed: {secondary!r}")
            raise

    def _entry(self, row: dict) -> dict:
        entries = [e for e in row["journal"]["entries"] if e["id"] == self.identity]
        if len(entries) != 1:
            raise PauseControlError("pause_control_unavailable")
        return entries[0]

    def phase(self, phase: str, **facts: Any) -> None:
        with self.store.writer(self.task.id) as (_, row):
            if row is None:
                raise PauseControlError("pause_control_unavailable")
            entry = self._entry(row)
            entry["phase"] = phase
            entry.update(facts)
            self.store.save_journal_uncommitted(row)

    def record_preparation_cause(self, stage: str, exc: BaseException | None = None, **facts: Any) -> None:
        if self.preparation_cause is None:
            from runtime.orchestrator.launch_refusals import safe_cause
            self.preparation_cause = safe_cause(stage, exc, **facts)

    def failed_cleanup(self, *, repository_deadline: float | None = None) -> None:
        if repository_deadline is not None:
            # Latch BEFORE even bounded admission. A refused attribution keeps
            # the original active reservation and cannot look clean locally.
            self.move_reservation.cleanup_failed = True
            from runtime.orchestrator.workspace_adapters import _repo_preparation_cleanup
            with _repo_preparation_cleanup(self, repository_deadline):
                row = self.store.row_uncommitted(self.store.root_uncommitted(self.task.id)["id"])
                entry = self._entry(row)
                entry["context"]["execution_unknown"] = True
                entry["phase"] = "unknown"
                entry["started_at"] = entry["captured_generation"] = None
                self.store.save_journal_uncommitted(row)
            return
        # Sticky local ownership precedes any fallible journal/COMMIT. A caught
        # save failure or rollback must never permit clean terminal release.
        self.move_reservation.failed_cleanup()
        with self.store.writer(self.task.id) as (_, row):
            entry = self._entry(row)
            entry["context"]["execution_unknown"] = True
            entry["phase"] = "unknown"
            entry["started_at"] = entry["captured_generation"] = None
            self.store.save_journal_uncommitted(row)

    @contextmanager
    def preparation_effects(self):
        """Errors after native effects retain this SAME task, agent and SID.

        Successful completion proves only this interval. It cannot clear an
        earlier failed preparation, even if a later host/callback is clean.
        """
        # A retry can be safely deferred only until preparation resumes. Commit
        # that existing prepared state BEFORE effects so restart cannot replay
        # an unproved partial preparation after an interrupted evidence save.
        # Never rewrite an already-unknown/possible-launch/ACTION state.
        with self.store.writer(self.task.id) as (_, row):
            entry = self._entry(row)
            if entry["phase"] in {"retry_deferred", "recovery_deferred"}:
                entry["phase"] = "prepared"
                self.store.save_journal_uncommitted(row)
        from runtime.orchestrator.workspace_adapters import _repo_effect_interval
        try:
            with _repo_effect_interval(self):
                yield
        except BaseException as exc:
            try:
                self.failed_cleanup()
            except BaseException:
                exc.add_note("task preparation evidence persistence unavailable; original error retained")
            try:
                self.record_preparation_cause("native_preparation", exc)
            except BaseException:
                exc.add_note("task preparation cause unavailable; original error retained")
            raise

    def _business_uncommitted(self, entry: dict) -> float | None:
        db = self.orch._db
        recovery_deadline = None
        current = db.get_task(self.task.id)
        if (current is None or fingerprint(current) != entry["fingerprint"]
                or (self.owner != "recovery" and not eligible(self.orch, current))):
            raise DeferredRootPause("preparation owner changed")
        if self.owner == "recovery":
            import time
            if "authority_v2_generation" in entry["owner_identity"]:
                _authenticate_deferred_v2(self.store, entry)
            proof = entry["context"]["recovery_return"]
            arguments = dict(task_id=self.task.id, agent=self.agent,
                             origin_session_id=proof["origin_session_id"], recovery_session_id=self.session_id)
            claimed_at = datetime.now(timezone.utc)
            recovery_deadline = time.monotonic() + 120.0
            expires_at = (claimed_at + timedelta(seconds=120)).isoformat()
            if not db._claim_task_completion_recovery_uncommitted(
                    **arguments, provider_session_id=proof["provider_session_id"],
                    claimed_at=claimed_at.isoformat(), expires_at=expires_at):
                raise DeferredRootPause("completion recovery no longer eligible")
            if not db._publish_task_completion_recovery_binding_uncommitted(**arguments):
                raise DeferredRootPause("completion recovery owner lost")
            # The claim ledger, binding and launch journal commit together.
            # Held/preclaim waiting has no live deadline; an old unclaimed
            # preparation's timestamp is not evidence of a spent episode.
            proof["deadline_at"] = expires_at
        elif self.owner == "v2":
            generation = self.metadata["authority_v2_generation"]
            notification = db.get_authority_policy_v2_recovery_notification(generation)
            if (notification is None or notification.root_task_id != self.task.id
                    or notification.manager_agent != self.agent):
                raise DeferredRootPause("v2 generation unavailable")
            arguments = dict(root_task_id=self.task.id, manager_agent=notification.manager_agent,
                             manager_session_id=notification.manager_session_id, result_id=notification.result_id,
                             generation_id=generation, next_session_id=self.session_id)
            claim = db._try_claim_v2_continuation_generation_uncommitted(
                **arguments, now_dt=datetime.now(timezone.utc))
            if claim.status != "claimed":
                raise DeferredRootPause("v2 generation refused")
            settled = db._settle_v2_continuation_generation_uncommitted(**arguments, now_dt=datetime.now(timezone.utc))
            if settled.status not in {"settled", "already_settled_exact"}:
                raise DeferredRootPause("v2 settlement refused")
        elif self.owner in ("draft", "coding"):
            dispatcher = self.workflow_dispatcher or self.orch._workflow_drafts
            dispatcher._claim_launch_uncommitted(self)
        elif self.owner == "ordinary":
            if not db._try_claim_for_step_uncommitted(
                    self.task.id, self.task.status, self.task.block_kind, self.task.orchestration_step_count + 1):
                raise DeferredRootPause("ordinary claim refused")
        else:
            raise PauseControlError("pause_control_unavailable")
        db._conn.execute("UPDATE tasks SET assigned_agent=?,current_session_id=? WHERE id=?",
                         (self.agent, self.session_id, self.task.id))
        audit_ids: list[int] = []
        if self.task.block_kind == BlockKind.BLOCKED_ON_JOB:
            ids = json.loads(self.task.blocked_on_job_ids or "[]")
            audit_ids.append(db.insert_audit_log_uncommitted(
                self.task.id, "orchestrator", "task_resumed_from_jobs",
                {"blocking_job_ids": ids, "trigger": self.metadata.get("trigger", "unknown"),
                 "triggering_job_id": self.metadata.get("triggering_job_id"),
                 "job_outcomes": {jid: db.get_job_status(jid) or "unknown" for jid in ids}}))
        if self.task.active_fanout is not None:
            from runtime.orchestrator.run_step import _prepare_fanout_join_payload
            payload = _prepare_fanout_join_payload(self.orch, self.task.id, self.task.active_fanout)
            if payload is not None:
                audit_ids.append(db.insert_audit_log_uncommitted(self.task.id, "orchestrator", "fanout_join", payload))
                db._conn.execute("UPDATE tasks SET active_fanout=NULL WHERE id=? AND active_fanout=?",
                                 (self.task.id, self.task.active_fanout))
        entry["context"]["audit_ids"] = audit_ids
        if self.admission_effects is not None:
            self.admission_effects()
        if self.owner == "coding":
            self.workflow_dispatcher._session_bound_uncommitted(self)
        return recovery_deadline

    def commit(self, ctx: Any = None) -> bool:
        if self.owner in ("draft", "coding"):
            drafts = self.workflow_dispatcher or self.orch._workflow_drafts
            return drafts._on_loop(drafts._commit_pause_launch(self, ctx))
        return self._commit_local(ctx)

    def _commit_local(self, ctx: Any = None, *, validate_admission: Callable[[], None] | None = None) -> bool:
        """One writer commits with the terminal lock last and still owned."""
        from contextlib import nullcontext
        if self.owner == "coding":
            from runtime.daemon.startup_recovery import coding_admission_validation
            coding_validate = coding_admission_validation(self.orch._workflow_coding_dispatch)
            previous_validation = validate_admission

            def validate_admission() -> None:
                coding_validate()
                if previous_validation is not None:
                    previous_validation()

        validate_admission = self._fresh_validation(validate_admission)
        sessions = self.orch._sessions
        lease = sessions.binding_lease(self.task.id, self.agent) if sessions else nullcontext()
        won = False
        recovery_deadline = self.recovery_deadline
        def record_commit() -> None:
            if won:
                self.admitted = self.attempt_committed = self.unsettled = True
                self.move_reservation.possible_launch = True
                self.move_reservation.quiescent = False
                if self.owner == "recovery":
                    # Publish only the original live deadline of this actual
                    # claim after COMMIT. Rollback publishes no budget, and
                    # wall-clock movement cannot extend the live interval.
                    self.recovery_deadline = recovery_deadline
                if ctx is not None:
                    ctx._task_pause_launch_committed = True
        cancelled_binding = None
        with lease:
            with self.store.writer(self.task.id,
                    commit_guard=ctx._lock if ctx is not None else None, committed=record_commit, admission=True,
                    validate_admission=validate_admission) as (_, row):
                if row is None:
                    raise PauseControlError("pause_control_unavailable")
                try:
                    self.store.held_uncommitted(self.task.id)
                except PauseControlError as exc:
                    if exc.detail["code"] == "root_paused":
                        raise DeferredRootPause("root_paused") from exc
                    raise
                if self.move_reservation is None:
                    from runtime.orchestrator.team_move import DeferredTeamMove
                    raise DeferredTeamMove("team_move_preparation_missing")
                self.orch._team_moves.revalidate_uncommitted(self.move_reservation)
                entry = self._entry(row)
                if ctx is not None and ctx._terminal_reason is not None:
                    return False
                if not self.admitted:
                    recovery_deadline = self._business_uncommitted(entry)
                else:
                    current = self.orch._db.get_task(self.task.id)
                    if (current is None or current.cancelled_at is not None
                            or current.current_session_id != self.session_id or current.assigned_agent != self.agent
                            or current.status != TaskStatus.IN_PROGRESS or current.block_kind is not None
                            or current.orchestration_step_count != entry["fingerprint"]["count"] + (self.owner != "recovery")):
                        raise PauseControlError("pause_control_unavailable")
                    if self.owner == "recovery":
                        import time
                        if (self.recovery_deadline is None or time.monotonic() >= self.recovery_deadline
                                or not self.orch._db.task_completion_recovery_launch_allowed(
                                    task_id=self.task.id, agent=self.agent, recovery_session_id=self.session_id)):
                            raise DeferredRootPause("completion recovery ownership/budget lost")
                    if self.owner == "v2":
                        _authenticate_v2_retry(self.store, entry)
                    if self.orch._db.get_latest_task_result(self.task.id, self.agent, self.session_id) is not None:
                        raise DeferredRootPause("callback already landed")
                entry["phase"] = "possible_launch"
                entry["captured_generation"] = None
                self.store.save_journal_uncommitted(row)
                if self.owner == "coding":
                    # Close both native events, their actual admission record
                    # and the final saved journal before this SAME COMMIT.
                    from runtime.infrastructure.workflow_schema import validate_workflow_schema
                    validate_workflow_schema(self.store.db._conn, expected_org_slug=self.store.org_slug)
                won = True
            if self.publish_binding is not None:
                cancelled_binding = self.publish_binding()
        if cancelled_binding is not None:
            cancelled_binding()
        if getattr(self, "after_binding", None) is not None:
            self.after_binding()
        return True

    def finalize(self) -> Any:
        if self.final_prompt is not None:
            self.last_spec = self.final_prompt()
        return self.last_spec

    def _task_pause_commit(self, ctx: Any) -> Any:
        if not self.commit(ctx):
            return None
        return self.finalize()

    def __call__(self) -> None:
        """Recognized by the private host integration; no public host API."""

    def recovery_prelaunch(self) -> None:
        """Before commitment, validate the original return's unchanged owner."""
        with self.store.db._lock:
            root = self.store.root_uncommitted(self.task.id)
            row = self.store.row_uncommitted(root["id"])
            proof = self._entry(row)["context"]["recovery_return"]
            current = self.orch._db.get_task(self.task.id)
            if (current is None or current.cancelled_at is not None
                    or current.status != TaskStatus.IN_PROGRESS or current.assigned_agent != self.agent
                    or current.current_session_id != proof["origin_session_id"]):
                raise DeferredRootPause("completion recovery owner changed")

    def recover_return(self, result: Any) -> tuple[bool, Any, Any]:
        """Keep an actual successful missing-callback return until admission.

        This records no result/consumption and spends no recovery opportunity.
        Only the original server-observed return reaches this producer.
        """
        from runtime.orchestrator.team_move import DeferredTeamMove
        if not (self.admitted and self.owner == "recovery"):
            self._begin_launch_attempt()
        with self.store.writer(self.task.id, admission=True) as (_, row):
            entry = self._entry(row)
            proof = entry["context"].get("recovery_return")
            if self.admitted and self.owner == "recovery":
                # Duplicate delivery must not convert a spent/live recovery
                # back into an unclaimed preparation or erase its attempt.
                raise PauseControlError("pause_control_unavailable")
            unknown_owner = None
            if proof is None:
                if (self.unsettled or entry["phase"] == "unknown" or entry["context"].get("execution_unknown")
                        or (self.move_reservation is not None and self.move_reservation.cleanup_failed)):
                    # This actual return ends the original producer, not its
                    # uncertain execution tree. Keep its original SID, owner,
                    # generation and context in a separate bounded entry. A
                    # recovery receipt can close only the recovery attempt.
                    original = deepcopy(entry)
                    original["id"] = unknown_owner = uuid.uuid4().hex
                    original["phase"] = "unknown"
                    original["started_at"] = original["captured_generation"] = None
                    original["context"].update(execution_unknown=True, producer_settled=True)
                    row["journal"]["entries"].append(original)
                    entry["context"].pop("execution_unknown", None)
                    entry["context"].pop("producer_settled", None)
                proof = dict(origin_session_id=result.session_id, provider_session_id=result.agent_session_id,
                             duration_seconds=result.duration_seconds, recovery_session_id=self.orch._build_session_id(),
                             deadline_at=None)
                entry["context"]["recovery_return"] = proof
            self.orch._team_moves.recovery_uncommitted(self, proof["recovery_session_id"], unknown_owner)
            current = self.orch._db.get_task(self.task.id)
            entry["phase"], entry["owner"] = "recovery_deferred", "recovery"
            entry["retry"] = {}
            entry["fingerprint"] = fingerprint(current)
            entry["session_id"] = proof["recovery_session_id"]
            entry["started_at"] = entry["captured_generation"] = None
            self.store.save_journal_uncommitted(row)
        self.task, self.owner, self.session_id = current, "recovery", proof["recovery_session_id"]
        self.admitted = self.attempt_committed = self.unsettled = False
        try:
            with self.store.db._lock:
                self.store.held_uncommitted(self.task.id)
        except PauseControlError as exc:
            if exc.detail["code"] == "root_paused":
                raise DeferredRootPause("root_paused") from exc
            raise
        prompt = ("Your previous turn ended without a HappyRanch completion callback. "
                  "Submit the required callback now using the current task/session "
                  f"binding task={self.task.id} session={self.session_id} and the "
                  "work already performed. If waiting on a job, report blocked with "
                  "its actual job ID. Do not claim unverified success. Do not continue "
                  "implementation or start new work.")
        try:
            recovery_result, recovery_report = self.orch._run_agent(
                self.task.id, self.agent, prompt, runtime_session_id=self.session_id,
                resume_session_id=proof["provider_session_id"], origin_runtime_session_id=proof["origin_session_id"],
                timeout_seconds_override=120, recovery=True)
            return self.admitted, recovery_result, recovery_report
        except (DeferredRootPause, DeferredTeamMove, PauseControlError):
            raise
        except Exception as exc:
            result.success = False
            result.error = f"completion recovery launch failed: {exc}"
            return self.admitted, result, None

    def _begin_launch_attempt(self) -> None:
        # A returned producer's positive PID belongs to its previous attempt.
        # Reset only local refusal attribution before fallible preparation;
        # durable launch/unknown evidence and live-attempt protection survive.
        self.launch_observed = False
        self.refusal_attempt_id = uuid.uuid4().hex

    def retry_attempt(self, *, boundary: str, ordinal: int, budget: int,
                      backoff: float = 0.0, schedule: tuple[float, ...] | None = None,
                      enqueued_at: str | None = None) -> dict:
        """Retain the original retry budget; every actual retry arbitrates again."""
        if self.owner == "coding" and ordinal:
            raise PauseControlError("pause_control_unavailable")
        self._begin_launch_attempt()
        if boundary == "host" and ordinal and self.unsettled:
            raise PauseControlError("pause_control_unavailable")
        unknown_owner = None
        def committed() -> None:
            if unknown_owner is not None:
                self.orch._team_moves.retry_uncommitted(self, unknown_owner)
        with self.store.writer(self.task.id, admission=True, committed=committed) as (_, row):
            entry = self._entry(row)
            prior = entry["retry"]
            if prior and prior["boundary"] != boundary:
                raise PauseControlError("pause_control_unavailable")
            reservation = self.move_reservation
            if reservation is not None and reservation.cleanup_failed:
                raise PauseControlError("pause_control_unavailable")
            if ordinal and reservation is not None and reservation.possible_launch and not reservation.quiescent:
                if (self.unsettled or reservation.cleanup_failed or not entry["context"].get("execution_unknown")
                        or self.orch._team_moves._reservations.get(self.identity) is not reservation):
                    raise PauseControlError("pause_control_unavailable")
                # Only the actual returned direct/passthrough attempt reaches
                # this handoff. Its original SID/retry/fingerprint stay durable;
                # a later attempt's receipt cannot erase its unknown tree.
                original = deepcopy(entry)
                original["id"] = unknown_owner = uuid.uuid4().hex
                original["phase"] = "unknown"
                original["started_at"] = original["captured_generation"] = None
                # The copied admitted fields retain attribution. The new id
                # also names the retained reservation after journal COMMIT;
                # no extra context key is part of the strict Pause format.
                original["context"].update(execution_unknown=True, producer_settled=True)
                row["journal"]["entries"].append(original)
                entry["context"].pop("execution_unknown", None)
            entry["retry"] = {"ordinal": ordinal, "budget": budget, "boundary": boundary,
                              "enqueued_at": prior.get("enqueued_at", enqueued_at or now()),
                              "schedule": prior.get("schedule", list(schedule or ())),
                              "not_before": (datetime.now(timezone.utc) + timedelta(seconds=backoff)).isoformat()}
            if ordinal:
                entry["phase"] = "retry_deferred"
                entry["started_at"] = entry["captured_generation"] = None
            self.store.save_journal_uncommitted(row)
        self.attempt_committed = False
        return dict(entry["retry"])

    def host_terminal(self, outcome: Any) -> None:
        from runtime.workflows.coding_dispatch import terminal_observation
        self.native_terminal_observation = terminal_observation(outcome)
        if outcome is not None and outcome.cleanup_error is not None:
            # Failed native backend cleanup can precede first commitment. The
            # current backends' prepare methods only validate/reserve opaque
            # tokens; their pre-effect refusals alone are not cleanup failure.
            self.failed_cleanup()
        if self.move_reservation is not None and self.attempt_committed:
            self.move_reservation.terminal(outcome)
        if not self.attempt_committed:
            return
        receipt = None if outcome is None else outcome.receipt
        if (receipt is not None and receipt.backend != "passthrough" and receipt.quiescent is True
                and receipt.cleanup_status.value != "incomplete" and not receipt.survivors
                and outcome.cleanup_error is None):
            self.unsettled = False
            self.phase("result_processing", started_at=None, captured_generation=None)
        else:
            self.phase("unknown", started_at=None, captured_generation=None)

    def direct_terminal(self) -> None:
        """Actual communicate/wait closes this process, not its descendant tree."""
        self.unsettled = False
        with self.store.writer(self.task.id) as (_, row):
            entry = self._entry(row)
            entry["context"]["execution_unknown"] = True
            entry["phase"] = "result_processing"
            entry["started_at"] = entry["captured_generation"] = None
            self.store.save_journal_uncommitted(row)

    def started(self, pid: int) -> None:
        if pid > 0:
            # Local launch fact precedes fallible storage: an audit/phase save
            # failure after Popen must never be counted as a launch refusal.
            self.launch_observed = True
            from runtime.orchestrator.launch_refusals import record_started
            record_started(self)
            self.phase("running", started_at=now())

    def close(self) -> dict:
        # Read sticky uncertainty and commit its disposition under the SAME
        # native writer. A later read/removal gap cannot erase new uncertainty.
        with self.store.writer(self.task.id) as (_, row):
            entry = self._entry(row)
            reservation = self.move_reservation
            move_unknown = reservation is not None and (reservation.cleanup_failed
                or (reservation.possible_launch and not reservation.quiescent))
            unknown = (entry["context"].get("execution_unknown", False) or entry["phase"] == "unknown" or move_unknown
                or reservation is not None and not all(reservation.repository_obligations.values()))
            if self.unsettled or unknown:
                if move_unknown:
                    entry["context"]["execution_unknown"] = True
                entry["phase"] = "unknown"
                entry["started_at"] = entry["captured_generation"] = None
                task = self.orch._db.get_task(self.task.id)
                if (not self.unsettled and unknown and task is not None
                        and (task.status != TaskStatus.IN_PROGRESS or task.block_kind is not None
                             or task.current_session_id != self.session_id)):
                    entry["context"]["producer_settled"] = True
                removed = False
            else:
                row["journal"]["entries"] = [e for e in row["journal"]["entries"] if e["id"] != self.identity]
                removed = True
            final = deepcopy(entry)
            self.store.save_journal_uncommitted(row)
        return dict(removed=removed, invocation=final)


def assert_origin_unheld(org: Any, task_id: str) -> None:
    store = getattr(org.db, "_task_pause_store", None)
    if store is not None:
        from fastapi import HTTPException
        try:
            with org.db._lock:
                store.held_uncommitted(task_id)
        except PauseControlError as exc:
            raise HTTPException(status_code=404 if exc.detail["code"] == "unknown_task" else 409,
                                detail=exc.detail) from exc


def _authenticate_deferred_v2(store: TaskPauseStore, entry: dict) -> None:
    if entry["owner"] == "recovery":
        proof = entry["context"]["recovery_return"]
        original = {**entry, "session_id": proof["origin_session_id"],
                    "fingerprint": {**entry["fingerprint"], "count": entry["fingerprint"]["count"] - 1}}
        _authenticate_v2_retry(store, original)
    else:
        _authenticate_v2_retry(store, entry)


def _unclaimed_recovery_current(store: TaskPauseStore, entry: dict) -> bool:
    """Read the existing claim exclusions without spending a recovery episode."""
    proof = entry["context"]["recovery_return"]
    return (proof["deadline_at"] is None
        and store.db._conn.execute(
            "SELECT 1 FROM task_completion_recoveries WHERE task_id=? AND agent=? "
            "AND (origin_session_id=? OR state='claimed') LIMIT 1",
            (entry["task_id"], entry["agent"], proof["origin_session_id"])).fetchone() is None)


def _deferred_invocation_uncommitted(orch: Any, task: TaskRecord, metadata: dict | None,
                                    row: dict | None) -> TaskInvocation | None:
    """Authenticate a decoded journal copy; publish no reservation or write."""
    store = orch._db._task_pause_store
    if row is not None:
        _observe_settled_producers(store, row)
    entries = [] if row is None else [e for e in row["journal"]["entries"]
                                      if e["task_id"] == task.id and e["owner"] != "job"
                                      and not e["context"].get("producer_settled")]
    if any(live.task.id == task.id for live in store._live.values()):
        raise DeferredRootPause("invocation already live")
    if not entries:
        store.held_uncommitted(task.id)
        return None
    if len(entries) != 1 or entries[0]["phase"] not in {"retry_deferred", "recovery_deferred"}:
        raise DeferredRootPause("invocation already owned")
    entry = entries[0]
    if entry["owner"] == "coding":
        # K3 control/recovery remains unavailable; never adopt as ordinary.
        raise DeferredRootPause("coding_native_recovery_unavailable")
    f = entry["fingerprint"]
    if (task.parent_task_id != f["parent"]
            or hashlib.sha256(task.brief.encode()).hexdigest() != f["brief_digest"]
            or task.status != TaskStatus.IN_PROGRESS or task.block_kind is not None):
        raise DeferredRootPause("deferred continuation changed")
    consumer_sid = (entry["context"]["recovery_return"]["origin_session_id"]
                    if entry["phase"] == "recovery_deferred" else entry["session_id"])
    landed = store.db.get_latest_task_result(task.id, entry["agent"], consumer_sid)
    consumer_only = landed is not None and type(landed["id"]) is int
    if not consumer_only:
        if entry["context"].get("execution_unknown"):
            raise DeferredRootPause("preparation cleanup unknown")
        store.held_uncommitted(task.id)
    if "authority_v2_generation" in entry["owner_identity"]:
        if (not isinstance(metadata, dict) or metadata.get("authority_v2_generation")
                != entry["owner_identity"].get("authority_v2_generation")):
            raise DeferredRootPause("v2 retry requires original tagged delivery")
        _authenticate_deferred_v2(store, entry)
    elif isinstance(metadata, dict) and "authority_v2_generation" in metadata:
        raise DeferredRootPause("tagged delivery cannot adopt another owner")
    if task.cancelled_at is not None or task.status in {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.SUPERSEDED}:
        raise DeferredRootPause("deferred owner no longer eligible")
    if entry["phase"] == "retry_deferred":
        if (task.current_session_id != entry["session_id"] or task.assigned_agent != entry["agent"]
                or not entry["retry"]
                or task.orchestration_step_count != f["count"] + 1):
            raise PauseControlError("pause_control_unavailable")
        if not consumer_only and datetime.fromisoformat(entry["retry"]["not_before"]) > datetime.now(timezone.utc):
            raise DeferredRootPause("retry backoff pending")
        original = task.model_copy(update={"status": TaskStatus(f["status"]),
            "block_kind": BlockKind(f["block_kind"]) if f["block_kind"] else None,
            "orchestration_step_count": f["count"], "current_session_id": f["session_id"],
            "assigned_agent": f["agent"], "active_fanout": f["fanout"], "blocked_on_job_ids": f["jobs"]})
        admitted = True
    else:
        proof = entry["context"]["recovery_return"]
        if task.current_session_id != proof["origin_session_id"] or task.assigned_agent != entry["agent"]:
            raise PauseControlError("pause_control_unavailable")
        if fingerprint(task) != f or (not consumer_only and not _unclaimed_recovery_current(store, entry)):
            raise DeferredRootPause("recovery continuation changed")
        original, admitted = task, False
    metadata = dict(entry["owner_identity"])
    invocation = TaskInvocation(orch, original, entry["agent"], entry["session_id"],
                                owner=entry["owner"], metadata=metadata, identity=entry["id"], admitted=admitted)
    invocation.deferred_recovery = entry["phase"] == "recovery_deferred"
    invocation.consumer_only = consumer_only
    invocation.consumer_session_id = consumer_sid
    invocation.deferred_adoption_pending = True
    invocation.deferred_entry = deepcopy(entry)
    return invocation


def _same_deferred_owner(left: TaskInvocation, right: TaskInvocation) -> bool:
    return ((left.identity, left.agent, left.session_id, left.owner, left.metadata,
             left.task.team, left.task.task_type, fingerprint(left.task), left.deferred_entry) ==
            (right.identity, right.agent, right.session_id, right.owner, right.metadata,
             right.task.team, right.task.task_type, fingerprint(right.task), right.deferred_entry))


def deferred_invocation(orch: Any, task: TaskRecord, metadata: dict | None = None,
                        *, adopt: bool = True) -> TaskInvocation | None:
    """Identify the durable owner before any fallible adoption write."""
    store = orch._db._task_pause_store
    with store.db._lock:
        current = store.db.get_task(task.id)
        if current is None:
            raise DeferredRootPause("deferred owner disappeared")
        row = store.row_uncommitted(store.root_uncommitted(task.id)["id"])
        invocation = _deferred_invocation_uncommitted(orch, current, metadata, row)
    if invocation is not None and adopt:
        adopt_deferred_invocation(invocation)
    return invocation


def adopt_deferred_invocation(invocation: TaskInvocation) -> None:
    """Revalidate under the native writer; the caller already owns attribution."""
    store, move = invocation.store, invocation.orch._team_moves
    validate = None if invocation.consumer_only else invocation._fresh_validation()
    committed = False

    def record_commit():
        nonlocal committed
        committed = True

    try:
        with store.writer(invocation.task.id, admission=True, validate_admission=validate,
                          committed=record_commit) as (_, row):
            task = store.db.get_task(invocation.task.id)
            if task is None:
                raise DeferredRootPause("deferred owner disappeared")
            current = _deferred_invocation_uncommitted(invocation.orch, task, invocation.metadata, row)
            if current is None or not _same_deferred_owner(invocation, current):
                raise DeferredRootPause("deferred owner changed")
            invocation.move_reservation = invocation.orch._team_moves.reserve_task_uncommitted(invocation)
            invocation.consumer_only = current.consumer_only
            entry = invocation._entry(row)
            entry["phase"] = "result_processing" if current.consumer_only else "prepared"
            store.save_journal_uncommitted(row)
        with store.db._lock:
            if validate is not None:
                validate()
            invocation.deferred_adoption_pending = False
            store._live[invocation.identity] = invocation
    except BaseException as primary:
        if committed:
            try:
                if invocation.store is not store or invocation.orch._team_moves is not move:
                    if invocation.move_reservation is not None:
                        invocation.move_reservation.failed_cleanup()
                    raise RuntimeError("task_adoption_settlement_owner_changed")
                release_live(invocation)
            except BaseException as secondary:
                if invocation.move_reservation is not None:
                    invocation.move_reservation.failed_cleanup()
                primary.add_note(f"task adoption settlement also failed: {secondary!r}")
            raise
        # Reservation alone has no effects. A rolled-back adoption must not
        # leave a pristine local owner_live latch blocking its next delivery;
        # release_task retains every possible-launch/failed-cleanup blocker.
        invocation.orch._team_moves.release_task(invocation)
        raise


@contextmanager
def origin_guard(org: Any, task_id: str | None):
    """Serialize a synchronous work-starting mutation with the origin hold.

    Call only after awaiting org.db_lock. No await or external operation may
    cross this gate. Existing mutation helpers keep their own DB ownership.
    """
    store = getattr(org.db, "_task_pause_store", None)
    if store is None or task_id is None:
        yield
        return
    with org.db._lock:
        root_id = store.root_uncommitted(task_id)["id"]
    with store.gate(root_id):
        assert_origin_unheld(org, task_id)
        yield


def release_live(invocation: TaskInvocation, *, failed: bool = False) -> None:
    with invocation.store.db._lock:
        invocation.store._live.pop(invocation.identity, None)
        from runtime.orchestrator.team_move import _task_tail
        tail = _task_tail.get()
        if tail is not None:
            tail.append((invocation, failed))
    if tail is None:
        settle_live(invocation, failed=failed)


def settle_live(invocation: TaskInvocation, *, failed: bool = False, cleanup: Any = None) -> None:
    """Close durable attribution only after reconcile and actual outer cleanup."""
    original_error = sys.exception()
    reservation = invocation.move_reservation
    native_entry = None
    close_disposition = None
    if failed and reservation is not None:
        reservation.failed_cleanup()
    try:
        if failed:
            with invocation.store.writer(invocation.task.id) as (_, row):
                entry = invocation._entry(row)
                entry["context"]["execution_unknown"] = True
                facts = dict(
                    reason="producer_tail_failed" if cleanup is None or cleanup.kind != "unknown" else cleanup.reason,
                    processes=[] if cleanup is None else list(cleanup.processes))
                # Append diagnostics within the existing 64KiB UTF8 suffix
                # allowance. Never overwrite earlier cleanup facts or use
                # these lines as process/quiescence proof. Unknown entries
                # cannot be adopted as deferred retry/recovery preparations.
                lines = entry["context"].setdefault("cleanup", {"suffix_lines": []})["suffix_lines"]
                available = 65536 - sum(len(line.encode()) for line in lines)
                diagnostic = "\nProducer tail cleanup unknown: " + json.dumps(facts, sort_keys=True) + "\n"
                truncated = len(diagnostic.encode()) > available
                if truncated:
                    marker = " [truncated]\n"
                    diagnostic = (diagnostic.encode()[:max(0, available - len(marker))].decode("utf8", errors="ignore")
                                  + marker) if available >= len(marker) else ""
                if diagnostic:
                    lines.append(diagnostic)
                invocation.store.save_journal_uncommitted(row)
            if truncated:
                logging.getLogger(__name__).warning(
                    "task %s session %s owner %s cleanup suffix capacity; new diagnostics truncated: %s",
                    invocation.task.id, invocation.session_id, invocation.identity, facts)
        with invocation.store.db._lock:
            row = invocation.store.row_uncommitted(invocation.store.root_uncommitted(invocation.task.id)["id"])
            native_entry = deepcopy(invocation._entry(row))
            phase = native_entry["phase"]
        # Genuine deferred retries/recovery keep their exact owner for the
        # existing redelivery owner. Failure must first become durable unknown.
        if (failed or (reservation is not None and reservation.cleanup_failed)
                or phase not in {"retry_deferred", "recovery_deferred"}):
            close_disposition = invocation.close()
            native_entry = close_disposition["invocation"]
    except BaseException as exc:
        failed = True
        if reservation is not None:
            reservation.failed_cleanup()
        if original_error is None:
            raise
        # Finalization must retain the original launch/reconcile/cleanup
        # exception. A refused save is a failed retained settlement, never a
        # successful release; expose it without replacing the original cause.
        original_error.add_note(f"Pause tail settlement failed for {invocation.identity}: {exc!r}")
        logging.getLogger(__name__).exception(
            "task %s session %s owner %s Pause tail settlement failed; original error preserved",
            invocation.task.id, invocation.session_id, invocation.identity)
    finally:
        tail_error = sys.exception() or original_error
        release_disposition = None
        try:
            # Observe the actual final release, under its native ownership.
            # Absence alone is insufficient: close must also have committed
            # the positive removal of this exact original native owner.
            with invocation.store.db._lock:
                owned = (reservation is not None and
                    invocation.orch._team_moves._reservations.get(reservation.owner_id) is reservation)
                invocation.orch._team_moves.release_task(invocation, failed=failed)
                if reservation is not None:
                    release_disposition = dict(owner_id=reservation.owner_id, task_id=reservation.task_id,
                        agent=reservation.identity.agent_id, session_id=reservation.session_id,
                        owner_live=reservation.owner_live, cleanup_failed=reservation.cleanup_failed,
                        possible_launch=reservation.possible_launch, quiescent=reservation.quiescent,
                        removed=owned and invocation.orch._team_moves._reservations.get(reservation.owner_id) is None)
        except BaseException as exc:
            failed = True
            if tail_error is None:
                raise
            tail_error.add_note(f"Move tail release failed for {invocation.identity}: {exc!r}")
            logging.getLogger(__name__).exception("move tail release failed; original error preserved")
        finally:
            active_error = sys.exception() or tail_error
            coding = getattr(invocation.orch, "_workflow_coding_dispatch", None)
            if coding is not None:
                try:
                    coding.outer_settled(invocation, failed=failed, cleanup=cleanup, native_entry=native_entry,
                        close_disposition=close_disposition, release_disposition=release_disposition)
                except BaseException as exc:
                    if active_error is None:
                        raise
                    active_error.add_note(f"Coding tail observation failed for {invocation.identity}: {exc!r}")
                    logging.getLogger(__name__).exception("coding tail observation failed; original error preserved")


def pause_deferred_owner(db: Any, task_id: str) -> bool:
    store = getattr(db, "_task_pause_store", None)
    if store is None:
        return False
    with db._lock:
        task = db.get_task(task_id)
        if task is not None and task.assigned_agent and task.current_session_id:
            if (db.get_latest_task_result(task_id, task.assigned_agent, task.current_session_id) is not None
                    or db.get_accepted_task_completion_recovery_result(task_id=task_id, agent=task.assigned_agent) is not None):
                return False
            recovery = db.get_claimed_task_completion_recovery(task_id=task_id, agent=task.assigned_agent)
            if recovery is not None and task.current_session_id in {
                    recovery["origin_session_id"], recovery["recovery_session_id"]}:
                # An admitted episode belongs to the existing recovery-ledger
                # settlement, including crash-before-launch. Unknown journal
                # evidence remains a drain blocker, not a deferral that can
                # hide the spent claim from startup forever.
                return False
        root = store.root_uncommitted(task_id)
        row = store.row_uncommitted(root["id"])
        return bool(row and any(e["task_id"] == task_id and e["owner"] != "job"
            and not e["context"].get("producer_settled") and e["phase"] in {"prepared", "retry_deferred", "recovery_deferred", "action_started", "possible_launch", "unknown"}
            for e in row["journal"]["entries"]))


def restore_pause_preparations(orch: Any) -> None:
    """Cold boot retains unclosed task preparation and launch uncertainty.

    Prepared is not affirmative no-effect evidence. Possible launch/ACTION
    and missing closure remain unknown. A deferred
    retry/recovery retains its original identity and budget, without replaying
    a consumed result or treating missing PID as a no-launch proof.
    """
    store = getattr(orch._db, "_task_pause_store", None)
    if store is None:
        return
    with store.db._lock:
        root_ids = [r[0] for r in store.db._conn.execute("SELECT root_task_id FROM task_pause_controls")]
    for root_id in root_ids:
        with store.writer(root_id) as (_, row):
            retained = []
            for entry in row["journal"]["entries"]:
                task = orch._db.get_task(entry["task_id"])
                if entry["owner"] != "job" and (entry["phase"] == "prepared"
                        or entry["context"].get("execution_unknown")):
                    # The process died with native preparation unclosed, or
                    # with already-recorded uncertainty. Neither a terminal
                    # row, recovery metadata nor retry budget proves cleanup.
                    entry["phase"] = "unknown"
                    entry["context"]["execution_unknown"] = True
                    entry["started_at"] = entry["captured_generation"] = None
                elif (task is not None and (task.cancelled_at is not None or task.status.value in {"completed", "failed", "cancelled", "superseded"})
                        and entry["phase"] in {"retry_deferred", "recovery_deferred"}
                        and not entry["context"].get("execution_unknown")):
                    continue
                elif entry["phase"] in {"possible_launch", "running", "action_started", "result_processing"}:
                    entry["phase"] = "unknown"
                    entry["started_at"] = entry["captured_generation"] = None
                retained.append(entry)
            row["journal"]["entries"] = retained
            store.save_journal_uncommitted(row)


def _discover_pause_roots(org: Any, queue: Any, *, validate_admission=None):
    """Bounded durable rediscovery through the existing owner publishers."""
    from runtime.orchestrator.run_step import _task_followon_validation
    selected = _task_followon_validation(org.orchestrator)
    def validate():
        selected()
        if validate_admission is not None:
            validate_admission()
    if queue is not org.orchestrator._queue:
        from runtime.daemon.startup_recovery import StartupUnavailable
        raise StartupUnavailable("startup_attachment_changed")
    store = getattr(org.db, "_task_pause_store", None)
    if store is None or queue is None:
        return
    from runtime.workflows.recovery import classify_task
    from runtime.orchestrator.authority import enqueue_task_generation_aware
    from runtime.daemon.startup_publication import current_publication
    publication = current_publication(org.orchestrator)
    # One bounded page per sweep, retaining cursor only as a traversal aid.
    # Release intent is acknowledged only for a fully inspected actual root.
    cursor = getattr(store, "_discovery_cursor", "")
    with org.db._lock:
        validate()
        roots = org.db._conn.execute("SELECT root_task_id,generation FROM task_pause_controls "
                                    "WHERE root_task_id>? ORDER BY root_task_id LIMIT 32", (cursor,)).fetchall()
    if not roots:
        validate()
        store._discovery_cursor = ""
        return
    for root_id, generation in roots:
        validate()
        if publication is not None:
            publication.gate.require_redelivery_owner(org)
        held_before = publication.snapshot()["held"] if publication is not None else 0
        with org.db.coherent_read_view() as conn:
            validate()
            root = store.root_uncommitted(root_id)
            row = store.row_uncommitted(root_id)
            held = row["held"] and root["status"] not in {"completed", "failed", "cancelled", "superseded"}
            task_rows = conn.execute("WITH RECURSIVE tree(id) AS (SELECT ? UNION ALL "
                "SELECT t.id FROM tasks t JOIN tree ON t.parent_task_id=tree.id) "
                "SELECT id FROM tree LIMIT 10001", (root_id,)).fetchall()
            complete = len(task_rows) <= 10000
            entries = list(row["journal"]["entries"])
        # A pending job rejected without ever committing launch is closed by
        # that actual row. A terminal record for possible/unknown launch is
        # not a quiescence receipt and is deliberately retained.
        with store.writer(root_id, validate_admission=validate) as (_, current):
            validate()
            _observe_settled_producers(store, current)
            validate()
            retained = []
            root_unknown = False
            for entry in current["journal"]["entries"]:
                job = org.db.get_job(entry["owner_identity"]["job_id"]) if entry["owner"] == "job" else None
                task = org.db.get_task(entry["task_id"])
                closed_pending = (entry["phase"] == "pending_job" and job is not None
                                  and job.status.value in {"completed", "failed", "rejected"})
                closed_deferred = (entry["owner"] != "job" and entry["phase"] in {"retry_deferred", "recovery_deferred"}
                                   and task is not None and (task.cancelled_at is not None or task.status.value in {"completed", "failed", "cancelled", "superseded"})
                                   and not entry["context"].get("execution_unknown"))
                if not closed_pending and not closed_deferred:
                    retained.append(entry)
                    root_unknown |= entry["phase"] == "unknown" or bool(entry["context"].get("execution_unknown"))
            current["journal"]["entries"] = retained
            validate()
            store.save_journal_uncommitted(current)
            entries = list(retained)
        if not complete:
            yield root_id, generation, held, entries, False, False
            continue
        # Exact landed callbacks settle through the existing consumer even
        # while held. Delivery itself grants no provider-launch permission.
        deferred_callbacks = {e["task_id"]: e for e in entries if e["owner"] != "job"
            and not e["context"].get("producer_settled")
            and e["phase"] in {"retry_deferred", "recovery_deferred"}}
        if held:
            for entry in deferred_callbacks.values():
                task = org.db.get_task(entry["task_id"])
                if task is None or task.cancelled_at is not None or task.status.value in {"completed", "failed", "cancelled", "superseded"}:
                    continue
                sid = (entry["context"]["recovery_return"]["origin_session_id"]
                       if entry["phase"] == "recovery_deferred" else entry["session_id"])
                result = org.db.get_latest_task_result(entry["task_id"], entry["agent"], sid)
                if result is not None and type(result["id"]) is int:
                    if "authority_v2_generation" in entry["owner_identity"]:
                        try:
                            with org.db.coherent_read_view():
                                _authenticate_deferred_v2(store, entry)
                        except DeferredRootPause:
                            if publication is not None:
                                raise  # Unproved original v2 owner is not delivery.
                            continue
                    validate()
                    _publish_pause_task(org, queue, entry["task_id"], metadata=dict(entry["owner_identity"]),
                                        generation_aware=False)
        else:
            deferred = {e["task_id"]: e for e in entries if e["owner"] != "job"
                        and not e["context"].get("producer_settled")
                        and e["phase"] in {"retry_deferred", "recovery_deferred"}}
            for (task_id,) in task_rows:
                validate()
                task = org.db.get_task(task_id)
                # Retain unknown evidence, but terminal owners cannot launch
                # or replay consumed callbacks through the deferred branch.
                if task is None or task.cancelled_at is not None or task.status.value in {"completed", "failed", "cancelled", "superseded"}:
                    continue
                if task_id in deferred:
                    entry = deferred[task_id]
                    if "authority_v2_generation" in entry["owner_identity"]:
                        try:
                            with org.db.coherent_read_view():
                                _authenticate_deferred_v2(store, entry)
                        except DeferredRootPause:
                            if publication is not None:
                                raise  # Unproved original v2 owner is not delivery.
                            continue
                        # This is delivery for the already admitted SAME
                        # owner, not publication/admission of a pending G.
                        _publish_pause_task(org, queue, task_id, metadata=dict(entry["owner_identity"]),
                                            generation_aware=False)
                    else:
                        if publication is not None:
                            _publish_pause_task(org, queue, task_id,
                                                metadata=dict(entry["owner_identity"]))
                        else:
                            _publish_pause_task(org, queue, task_id,
                                publisher=lambda task_id=task_id, entry=entry: enqueue_task_generation_aware(
                                    org.orchestrator, queue, org.slug, task_id, metadata=dict(entry["owner_identity"])))
                elif task is not None and eligible(org.orchestrator, task):
                    ownership = classify_task(org.db, task_id, org_slug=org.slug)
                    if ownership.kind == "draft":
                        _pause_workflow_notification(org.workflow_drafts, task_id, queue, publication)
                    elif ownership.kind == "f5":
                        _pause_workflow_notification(org.workflow_reviews, task_id, queue, publication)
                    elif ownership.kind == 'coding_attempt':
                        _pause_workflow_notification(org.workflow_coding_dispatch, task_id, queue, publication)
                    elif ownership.kind.startswith('coding_'):
                        if publication is not None:
                            publication.count("held")
                        continue
                    elif ownership.kind == "legacy":
                        if publication is not None:
                            from runtime.orchestrator.run_step import _publish_redelivery_task
                            _publish_redelivery_task(org.orchestrator, task_id)
                        else:
                            enqueue_task_generation_aware(org.orchestrator, queue, org.slug, task_id)
                    elif publication is not None:
                        raise RuntimeError("startup_pause_task_ownership_unavailable")
        validate()
        if publication is not None and root_unknown:
            publication.count("held")  # Authenticated retained root-local uncertainty.
        task_hold = publication is not None and publication.snapshot()["held"] > held_before
        yield root_id, generation, held, entries, True, task_hold


def _publish_pause_task(org, queue, task_id, *, metadata=None, publisher=None,
                        generation_aware=True):
    from runtime.daemon.startup_publication import current_publication
    if current_publication(org.orchestrator) is not None:
        from runtime.orchestrator.run_step import _publish_redelivery_task
        if queue is not org.orchestrator._queue:
            raise RuntimeError("startup_pause_queue_changed")
        return _publish_redelivery_task(org.orchestrator, task_id, metadata=metadata,
            publisher=publisher, generation_aware=generation_aware)
    return queue.enqueue_if_absent(org.slug, task_id, metadata=metadata, publisher=publisher)


def _pause_workflow_notification(dispatcher, task_id, queue, publication):
    outcome = dispatcher.notify_queued(task_id, queue)
    if publication is not None:
        from runtime.daemon.startup_recovery import RecoveryOutcome
        if not isinstance(outcome, RecoveryOutcome) or not outcome.positive:
            raise RuntimeError("startup_pause_workflow_notification_unavailable")


def _pending_pause_jobs(org, entries):
    for entry in entries:
        if entry["owner"] == "job" and entry["phase"] == "pending_job":
            job_id = entry["owner_identity"]["job_id"]
            job = org.db.get_job(job_id)
            if job is not None and job.status.value == "pending" and not job.review_required:
                yield job_id, entry


def _ack_pause_release(org, root_id, generation, *, validate_admission=None):
    from runtime.orchestrator.run_step import _task_followon_validation
    selected = _task_followon_validation(org.orchestrator)
    def validate():
        selected()
        if validate_admission is not None:
            validate_admission()
    store = org.db._task_pause_store
    with store.writer(root_id, validate_admission=validate) as (_, current):
        validate()
        if current["generation"] == generation and not current["held"]:
            org.db._conn.execute("UPDATE task_pause_controls SET release_pending_generation=NULL WHERE root_task_id=?", (root_id,))
            return True
        return False


async def discover_pause_work(org: Any, queue: Any) -> None:
    """Original ordinary per-root publication -> job attempt -> release ack."""
    from runtime.orchestrator.run_step import _task_followon_validation
    validate = _task_followon_validation(org.orchestrator)
    if queue is not org.orchestrator._queue:
        from runtime.daemon.startup_recovery import StartupUnavailable
        raise StartupUnavailable("startup_attachment_changed")
    if getattr(org.db, "_task_pause_store", None) is None or queue is None:
        return
    from runtime.daemon.routes.jobs import _run_job_core
    from runtime.daemon.jobs_runner import require_job_admission
    from runtime.daemon.startup_recovery import StartupUnavailable
    for root_id, generation, held, entries, complete, task_hold in _discover_pause_roots(org, queue, validate_admission=validate):
        validate()
        if not complete:
            continue
        if not held:
            for job_id, entry in _pending_pause_jobs(org, entries):
                try:
                    require_job_admission(org)
                    validate()
                    await _run_job_core(org, job_id=job_id, cwd_override=None, timeout_override=None,
                        trigger="agent", trigger_actor=entry["agent"], submit_session_id=entry["session_id"])
                except StartupUnavailable:
                    # Preserve the original release intent AND cursor. Native
                    # P6 has its separate authenticated held outcome.
                    raise
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception("pause rediscovery job remains pending: %s", job_id)
            require_job_admission(org)
            _ack_pause_release(org, root_id, generation, validate_admission=validate)
        validate()
        org.db._task_pause_store._discovery_cursor = root_id


def discover_pause_work_native(org: Any, queue: Any, recovery):
    """Same reconciliation, no job coroutine/registration or async primitive.

    The eligible original pending job remains durable, including its release
    intent, until the ordinary ready-mode suffix can actually attempt launch.
    """
    from runtime.daemon.startup_publication import current_publication
    from runtime.daemon.startup_recovery import RecoveryOutcome
    recovery.require_redelivery_owner(org)
    publication = current_publication(org.orchestrator)
    if (publication is None or publication.gate is not recovery
            or queue is not recovery.state.queue
            or getattr(org.db, "_task_pause_store", None) is None):
        raise RuntimeError("startup_pause_owner_unavailable")
    held_any = False
    for root_id, generation, held, entries, complete, task_hold in _discover_pause_roots(org, queue):
        recovery.require_redelivery_owner(org)
        if not complete:
            raise RuntimeError("startup_pause_root_incomplete")
        pending_job = False
        if not held:
            for _job_id, _entry in _pending_pause_jobs(org, entries):
                pending_job = True
                break
            if not pending_job and not task_hold:
                task_hold = not _ack_pause_release(org, root_id, generation)
        if held or pending_job or task_hold:
            publication.count("held")
            held_any = True
        org.db._task_pause_store._discovery_cursor = root_id
    recovery.require_redelivery_owner(org)
    return RecoveryOutcome("local_hold" if held_any else "settled")


def prepared_cancel_controls(db: Any, task_id: str) -> list[tuple[str, Callable[[], None]]]:
    store = getattr(db, "_task_pause_store", None)
    if store is None:
        return []
    with db._lock:
        return [(inv.agent, inv.cancel_control) for inv in store._live.values()
                if inv.task.id == task_id and getattr(inv, "cancel_control", None) is not None]


def pause_overview(org: Any, *, limit: int, before: str | None) -> dict:
    """Read a stable org-scoped root page; never discover or mutate work."""
    from runtime.workflows.recovery import classify_task
    store = org.db._task_pause_store
    capture = None
    try:
        capture = org.workflow_authority.capture_admission()
    except Exception:
        pass  # Current workflow readiness is unavailable, never inferred.
    sessions = tuple(org.sessions.iter_active())
    import time
    deadline = time.monotonic() + 1.0
    sections = {name: [] for name in ("unheld_runnable", "pausing", "paused", "terminal_drain", "unavailable_roots")}
    with org.db.coherent_read_view() as conn:
        rows = conn.execute("SELECT id FROM tasks WHERE parent_task_id IS NULL AND id>? ORDER BY id LIMIT ?",
                            (before or "", limit + 1)).fetchall()
        more = len(rows) > limit
        complete = not more and before is None
        stamp = now()
        scanned = 0
        for (task_id,) in rows[:limit]:
            if scanned and time.monotonic() >= deadline:
                complete = False
                break
            scanned += 1
            task = org.db.get_task(task_id)
            projection = store.projection(task_id, deadline=min(deadline, time.monotonic() + 0.25))
            projection["observed_at"] = stamp
            complete = complete and projection["evidence_complete"]
            if task.status.value in {"completed", "failed", "cancelled", "superseded"}:
                if projection["blockers"] or not projection["evidence_complete"]:
                    sections["terminal_drain"].append(projection)
            elif projection["effective_hold"]:
                sections[projection["control_state"]].append(projection)
            else:
                ownership = classify_task(org.db, task_id, org_slug=org.slug)
                runnable = False
                if time.monotonic() >= deadline:
                    projection["evidence_complete"] = False
                elif ownership.kind == "legacy":
                    runnable = eligible(org.orchestrator, task)
                    dispatch = org.db.classify_authority_policy_v2_root_dispatch_for_enqueue(task_id)
                    if dispatch.kind == "pending":
                        notification = org.db.get_authority_policy_v2_recovery_notification(dispatch.generation_id)
                        code = "unavailable"
                        if notification is not None:
                            code, _ = org.db._authenticate_v2_admission_ready_uncommitted(
                                root_task_id=task_id, manager_agent=notification.manager_agent,
                                manager_session_id=notification.manager_session_id, result_id=notification.result_id)
                        runnable = runnable and code is None
                        if code is not None:
                            projection["evidence_complete"] = False
                    elif dispatch.kind not in {"absent", "retired"}:
                        runnable = False
                        projection["evidence_complete"] = False
                elif ownership.kind.startswith('coding_'):
                    # Complete saved ownership is known; downstream execution
                    # is unavailable. It cannot become an ordinary runnable root.
                    projection["evidence_complete"] = False
                elif ownership.kind in ("draft", "f5") and ownership.state == "queued" and capture is not None:
                    try:
                        # This is a snapshot observation, never an admission
                        # validator/lease. The final writer still uses the sole
                        # authoritative captured-value validator.
                        if org.workflow_authority._pointer(conn, org.workflow_authority.namespace) != capture.pointer:
                            raise PauseControlError("pause_control_unavailable")
                        dispatcher = org.workflow_reviews if ownership.kind == "f5" else org.workflow_drafts
                        intent = dispatcher._intent(conn, task_id)
                        dispatcher._eligible(conn, intent, capture, sessions)
                        runnable = True
                    except Exception:
                        pass
                if runnable:
                    sections["unheld_runnable"].append(projection)
                elif ownership.kind != "legacy" or not projection["evidence_complete"]:
                    projection["evidence_complete"] = False
                    sections["unavailable_roots"].append(projection)
                    complete = False
        return dict(org_slug=org.slug, observed_at=stamp, evidence_complete=complete,
                    next_cursor=rows[scanned - 1][0] if scanned and (more or scanned < len(rows)) else None,
                    counts={name: len(values) for name, values in sections.items()} if complete else None,
                    **sections)
