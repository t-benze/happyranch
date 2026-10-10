"""Private task-producer arbitration; no platform or executor policy API.

The process-local gate assumes one supported daemon per org. Durable possible
launch evidence survives a crash; absence of a PID is never a no-launch proof.
"""
from __future__ import annotations

import hashlib
import json
import uuid
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
    admission_effects: Callable[[], None] | None = None
    publish_binding: Callable[[], None] | None = None
    final_prompt: Callable[[], Any] | None = None
    last_spec: Any = None
    prompt_reader: Callable[[], str] | None = None

    @property
    def store(self) -> TaskPauseStore:
        return self.orch._db._task_pause_store

    def prepare(self) -> None:
        with self.store.writer(self.task.id) as (root, row):
            self.store.held_uncommitted(self.task.id)
            row = row or self.store.ensure_uncommitted(root["id"])
            _observe_settled_producers(self.store, row)
            entries = row["journal"]["entries"]
            if any(e["task_id"] == self.task.id and e["owner"] != "job" and not e["context"].get("producer_settled") for e in entries):
                raise DeferredRootPause("invocation already owned")
            entries.append({"id": self.identity, "org": self.store.org_slug, "root_task_id": root["id"],
                            "task_id": self.task.id, "agent": self.agent, "session_id": self.session_id,
                            "owner": self.owner, "owner_identity": {key: self.metadata[key] for key in
                                ("authority_v2_generation", "trigger", "triggering_job_id") if key in self.metadata},
                            "fingerprint": fingerprint(self.task), "generation": row["generation"],
                            "phase": "prepared", "retry": {}, "context": {}, "started_at": None,
                            "captured_generation": None})
            self.store.save_journal_uncommitted(row)
        with self.store.db._lock:
            self.store._live[self.identity] = self

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

    def _business_uncommitted(self, entry: dict) -> None:
        db = self.orch._db
        current = db.get_task(self.task.id)
        if (current is None or fingerprint(current) != entry["fingerprint"]
                or (self.owner != "recovery" and not eligible(self.orch, current))):
            raise DeferredRootPause("preparation owner changed")
        if self.owner == "recovery":
            import time
            if "authority_v2_generation" in entry["owner_identity"]:
                _authenticate_deferred_v2(self.store, entry)
            if time.monotonic() >= self.recovery_deadline:
                raise RuntimeError("completion recovery live budget expired")
            proof = entry["context"]["recovery_return"]
            arguments = dict(task_id=self.task.id, agent=self.agent,
                             origin_session_id=proof["origin_session_id"], recovery_session_id=self.session_id)
            claimed_at = datetime.now(timezone.utc)
            if not db._claim_task_completion_recovery_uncommitted(
                    **arguments, provider_session_id=proof["provider_session_id"],
                    claimed_at=claimed_at.isoformat(),
                    expires_at=(claimed_at + timedelta(seconds=max(0, self.recovery_deadline - time.monotonic()))).isoformat()):
                raise DeferredRootPause("completion recovery no longer eligible")
            if not db._publish_task_completion_recovery_binding_uncommitted(**arguments):
                raise DeferredRootPause("completion recovery owner lost")
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
        elif self.owner == "draft":
            self.orch._workflow_drafts._claim_launch_uncommitted(self)
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

    def commit(self, ctx: Any = None) -> bool:
        if self.owner == "draft":
            drafts = self.orch._workflow_drafts
            return drafts._on_loop(drafts._commit_pause_launch(self, ctx))
        return self._commit_local(ctx)

    def _commit_local(self, ctx: Any = None) -> bool:
        """One writer commits with the terminal lock last and still owned."""
        from contextlib import nullcontext
        sessions = self.orch._sessions
        lease = sessions.binding_lease(self.task.id, self.agent) if sessions else nullcontext()
        won = False
        def record_commit() -> None:
            if won:
                self.admitted = self.attempt_committed = self.unsettled = True
                if ctx is not None:
                    ctx._task_pause_launch_committed = True
        cancelled_binding = None
        with lease:
            with self.store.writer(self.task.id,
                    commit_guard=ctx._lock if ctx is not None else None, committed=record_commit) as (_, row):
                if row is None:
                    raise PauseControlError("pause_control_unavailable")
                try:
                    self.store.held_uncommitted(self.task.id)
                except PauseControlError as exc:
                    if exc.detail["code"] == "root_paused":
                        raise DeferredRootPause("root_paused") from exc
                    raise
                entry = self._entry(row)
                if ctx is not None and ctx._terminal_reason is not None:
                    return False
                if not self.admitted:
                    self._business_uncommitted(entry)
                else:
                    current = self.orch._db.get_task(self.task.id)
                    if (current is None or current.cancelled_at is not None
                            or current.current_session_id != self.session_id or current.assigned_agent != self.agent
                            or current.status != TaskStatus.IN_PROGRESS or current.block_kind is not None
                            or current.orchestration_step_count != entry["fingerprint"]["count"] + 1):
                        raise PauseControlError("pause_control_unavailable")
                    if self.owner == "v2":
                        _authenticate_v2_retry(self.store, entry)
                    if self.orch._db.get_latest_task_result(self.task.id, self.agent, self.session_id) is not None:
                        raise DeferredRootPause("callback already landed")
                entry["phase"] = "possible_launch"
                entry["captured_generation"] = None
                self.store.save_journal_uncommitted(row)
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
        import time
        if time.monotonic() >= self.recovery_deadline:
            raise RuntimeError("completion recovery live budget expired")
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
        import time
        with self.store.writer(self.task.id) as (_, row):
            entry = self._entry(row)
            proof = entry["context"].get("recovery_return")
            if proof is None:
                proof = dict(origin_session_id=result.session_id, provider_session_id=result.agent_session_id,
                             duration_seconds=result.duration_seconds, recovery_session_id=self.orch._build_session_id(),
                             deadline_at=(datetime.now(timezone.utc) + timedelta(seconds=120)).isoformat())
                entry["context"]["recovery_return"] = proof
            current = self.orch._db.get_task(self.task.id)
            entry["phase"], entry["owner"] = "recovery_deferred", "recovery"
            entry["retry"] = {}
            entry["fingerprint"] = fingerprint(current)
            entry["session_id"] = proof["recovery_session_id"]
            entry["started_at"] = entry["captured_generation"] = None
            self.store.save_journal_uncommitted(row)
        self.task, self.owner, self.session_id = current, "recovery", proof["recovery_session_id"]
        self.admitted = self.attempt_committed = self.unsettled = False
        self.recovery_deadline = time.monotonic() + max(0.0, (datetime.fromisoformat(proof["deadline_at"]) - datetime.now(timezone.utc)).total_seconds())
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
                timeout_seconds_override=max(0, int(self.recovery_deadline - time.monotonic())),
                recovery_deadline_monotonic=self.recovery_deadline, recovery=True)
            return self.admitted, recovery_result, recovery_report
        except DeferredRootPause:
            raise
        except Exception as exc:
            result.success = False
            result.error = f"completion recovery launch failed: {exc}"
            return self.admitted, result, None

    def retry_attempt(self, *, boundary: str, ordinal: int, budget: int,
                      backoff: float = 0.0, schedule: tuple[float, ...] | None = None,
                      enqueued_at: str | None = None) -> dict:
        """Retain the original retry budget; every actual retry arbitrates again."""
        if boundary == "host" and ordinal and self.unsettled:
            raise PauseControlError("pause_control_unavailable")
        with self.store.writer(self.task.id) as (_, row):
            entry = self._entry(row)
            prior = entry["retry"]
            if prior and prior["boundary"] != boundary:
                raise PauseControlError("pause_control_unavailable")
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
        if not self.attempt_committed:
            return
        receipt = None if outcome is None else outcome.receipt
        if (receipt is not None and receipt.backend != "passthrough" and receipt.quiescent is True
                and receipt.cleanup_status.value != "incomplete" and not receipt.survivors):
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
            self.phase("running", started_at=now())

    def close(self) -> None:
        with self.store.db._lock:
            row = self.store.row_uncommitted(self.store.root_uncommitted(self.task.id)["id"])
            unknown = self._entry(row)["context"].get("execution_unknown", False)
        if self.unsettled or unknown:
            with self.store.writer(self.task.id) as (_, row):
                entry = self._entry(row)
                entry["phase"] = "unknown"
                entry["started_at"] = entry["captured_generation"] = None
                task = self.orch._db.get_task(self.task.id)
                # A finished direct producer may have genuinely settled its
                # callback/decision. Its unknown descendant tree remains a
                # drain blocker, but does not impersonate a live step owner.
                if (not self.unsettled and unknown and task is not None
                        and (task.status != TaskStatus.IN_PROGRESS or task.block_kind is not None
                             or task.current_session_id != self.session_id)):
                    entry["context"]["producer_settled"] = True
                self.store.save_journal_uncommitted(row)
            return
        with self.store.writer(self.task.id) as (_, row):
            if row is not None:
                row["journal"]["entries"] = [e for e in row["journal"]["entries"] if e["id"] != self.identity]
                self.store.save_journal_uncommitted(row)


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


def deferred_invocation(orch: Any, task: TaskRecord, metadata: dict | None = None) -> TaskInvocation | None:
    """Adopt only an exact durable deferred owner, once per live worker."""
    store = orch._db._task_pause_store
    with store.writer(task.id) as (_, row):
        if row is not None:
            _observe_settled_producers(store, row)
            store.save_journal_uncommitted(row)
        entries = [] if row is None else [e for e in row["journal"]["entries"]
                                          if e["task_id"] == task.id and e["owner"] != "job"
                                          and not e["context"].get("producer_settled")]
        if not entries:
            store.held_uncommitted(task.id)
            return None
        if len(entries) != 1 or entries[0]["phase"] not in {"retry_deferred", "recovery_deferred"}:
            raise DeferredRootPause("invocation already owned")
        entry = entries[0]
        consumer_sid = (entry["context"]["recovery_return"]["origin_session_id"]
                        if entry["phase"] == "recovery_deferred" else entry["session_id"])
        landed = store.db.get_latest_task_result(task.id, entry["agent"], consumer_sid)
        consumer_only = landed is not None and type(landed["id"]) is int
        if not consumer_only:
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
                    or not entry["retry"] or task.status != TaskStatus.IN_PROGRESS):
                raise PauseControlError("pause_control_unavailable")
            if not consumer_only and datetime.fromisoformat(entry["retry"]["not_before"]) > datetime.now(timezone.utc):
                raise DeferredRootPause("retry backoff pending")
            f = entry["fingerprint"]
            original = task.model_copy(update={"status": TaskStatus(f["status"]),
                "block_kind": BlockKind(f["block_kind"]) if f["block_kind"] else None,
                "orchestration_step_count": f["count"], "current_session_id": f["session_id"],
                "assigned_agent": f["agent"], "active_fanout": f["fanout"], "blocked_on_job_ids": f["jobs"]})
            admitted = True
        else:
            proof = entry["context"]["recovery_return"]
            if task.current_session_id != proof["origin_session_id"] or task.assigned_agent != entry["agent"]:
                raise PauseControlError("pause_control_unavailable")
            original, admitted = task, False
        metadata = dict(entry["owner_identity"])
        invocation = TaskInvocation(orch, original, entry["agent"], entry["session_id"],
                                    owner=entry["owner"], metadata=metadata, identity=entry["id"], admitted=admitted)
        invocation.deferred_recovery = entry["phase"] == "recovery_deferred"
        invocation.consumer_only = consumer_only
        invocation.consumer_session_id = consumer_sid
        entry["phase"] = "result_processing" if consumer_only else "prepared"
        store.save_journal_uncommitted(row)
    with store.db._lock:
        store._live[invocation.identity] = invocation
    return invocation


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


def release_live(invocation: TaskInvocation) -> None:
    with invocation.store.db._lock:
        invocation.store._live.pop(invocation.identity, None)


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
        root = store.root_uncommitted(task_id)
        row = store.row_uncommitted(root["id"])
        return bool(row and any(e["task_id"] == task_id and e["owner"] != "job"
            and not e["context"].get("producer_settled") and e["phase"] in {"prepared", "retry_deferred", "recovery_deferred", "action_started", "possible_launch", "unknown"}
            for e in row["journal"]["entries"]))


def restore_pause_preparations(orch: Any) -> None:
    """Cold boot closes only affirmative precommit reservations.

    Possible launch/ACTION and missing closure remain unknown. A deferred
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
                if (task is not None and (task.cancelled_at is not None or task.status.value in {"completed", "failed", "cancelled", "superseded"})
                        and entry["phase"] in {"prepared", "retry_deferred", "recovery_deferred"}
                        and not entry["context"].get("execution_unknown")):
                    continue
                if entry["phase"] == "prepared" and entry["owner"] != "job":
                    if "recovery_return" in entry["context"]:
                        entry["phase"] = "recovery_deferred"
                    elif entry["retry"] and entry["retry"]["ordinal"]:
                        entry["phase"] = "retry_deferred"
                    else:
                        continue
                elif entry["phase"] in {"possible_launch", "running", "action_started", "result_processing"}:
                    entry["phase"] = "unknown"
                    entry["started_at"] = entry["captured_generation"] = None
                retained.append(entry)
            row["journal"]["entries"] = retained
            store.save_journal_uncommitted(row)


async def discover_pause_work(org: Any, queue: Any) -> None:
    """Bounded durable rediscovery through the existing owner publishers."""
    store = getattr(org.db, "_task_pause_store", None)
    if store is None or queue is None:
        return
    from runtime.workflows.recovery import classify_task
    from runtime.orchestrator.authority import enqueue_task_generation_aware
    from runtime.daemon.routes.jobs import _run_job_core
    # One bounded page per sweep, retaining cursor only as a traversal aid.
    # Release intent is acknowledged only for a fully inspected actual root.
    cursor = getattr(store, "_discovery_cursor", "")
    with org.db._lock:
        roots = org.db._conn.execute("SELECT root_task_id,generation FROM task_pause_controls "
                                    "WHERE root_task_id>? ORDER BY root_task_id LIMIT 32", (cursor,)).fetchall()
    if not roots:
        store._discovery_cursor = ""
        return
    for root_id, generation in roots:
        with org.db.coherent_read_view() as conn:
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
        with store.writer(root_id) as (_, current):
            _observe_settled_producers(store, current)
            retained = []
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
            current["journal"]["entries"] = retained
            store.save_journal_uncommitted(current)
            entries = list(retained)
        if not complete:
            continue
        # Exact landed callbacks settle through the existing consumer even
        # while held. Delivery itself grants no provider-launch permission.
        deferred_callbacks = {e["task_id"]: e for e in entries if e["owner"] != "job"
            and not e["context"].get("producer_settled")
            and e["phase"] in {"retry_deferred", "recovery_deferred"}}
        if held:
            for entry in deferred_callbacks.values():
                sid = (entry["context"]["recovery_return"]["origin_session_id"]
                       if entry["phase"] == "recovery_deferred" else entry["session_id"])
                result = org.db.get_latest_task_result(entry["task_id"], entry["agent"], sid)
                if result is not None and type(result["id"]) is int:
                    if "authority_v2_generation" in entry["owner_identity"]:
                        try:
                            with org.db.coherent_read_view():
                                _authenticate_deferred_v2(store, entry)
                        except DeferredRootPause:
                            continue
                    queue.enqueue_if_absent(org.slug, entry["task_id"], metadata=dict(entry["owner_identity"]))
        else:
            deferred = {e["task_id"]: e for e in entries if e["owner"] != "job"
                        and not e["context"].get("producer_settled")
                        and e["phase"] in {"retry_deferred", "recovery_deferred"}}
            for (task_id,) in task_rows:
                task = org.db.get_task(task_id)
                if task_id in deferred:
                    entry = deferred[task_id]
                    if "authority_v2_generation" in entry["owner_identity"]:
                        try:
                            with org.db.coherent_read_view():
                                _authenticate_deferred_v2(store, entry)
                        except DeferredRootPause:
                            continue
                        # This is delivery for the already admitted SAME
                        # owner, not publication/admission of a pending G.
                        queue.enqueue_if_absent(org.slug, task_id, metadata=dict(entry["owner_identity"]))
                    else:
                        queue.enqueue_if_absent(org.slug, task_id,
                            publisher=lambda task_id=task_id, entry=entry: enqueue_task_generation_aware(
                                org.orchestrator, queue, org.slug, task_id, metadata=dict(entry["owner_identity"])))
                elif task is not None and eligible(org.orchestrator, task):
                    ownership = classify_task(org.db, task_id, org_slug=org.slug)
                    if ownership.kind == "draft":
                        org.workflow_drafts.notify_queued(task_id, queue)
                    elif ownership.kind == "legacy":
                        enqueue_task_generation_aware(org.orchestrator, queue, org.slug, task_id)
            for entry in entries:
                if entry["owner"] == "job" and entry["phase"] == "pending_job":
                    job_id = entry["owner_identity"]["job_id"]
                    job = org.db.get_job(job_id)
                    if job is not None and job.status.value == "pending" and not job.review_required:
                        try:
                            await _run_job_core(org, job_id=job_id, cwd_override=None, timeout_override=None,
                                trigger="agent", trigger_actor=entry["agent"], submit_session_id=entry["session_id"])
                        except Exception:
                            import logging
                            logging.getLogger(__name__).exception("pause rediscovery job remains pending: %s", job_id)
            with store.writer(root_id) as (_, current):
                if current["generation"] == generation and not current["held"]:
                    org.db._conn.execute("UPDATE task_pause_controls SET release_pending_generation=NULL WHERE root_task_id=?", (root_id,))
        store._discovery_cursor = root_id


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
                elif ownership.kind == "draft" and ownership.state == "queued" and capture is not None:
                    try:
                        # This is a snapshot observation, never an admission
                        # validator/lease. The final writer still uses the sole
                        # authoritative captured-value validator.
                        if org.workflow_authority._pointer(conn, org.workflow_authority.namespace) != capture.pointer:
                            raise PauseControlError("pause_control_unavailable")
                        intent = org.workflow_drafts._intent(conn, task_id)
                        org.workflow_drafts._eligible(conn, intent, capture, sessions)
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
