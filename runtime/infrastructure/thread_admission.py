"""Private C2 lifecycle evidence in the EXISTING thread audit scope.

No schema install, invocation credential, token mutation or task-Pause journal.
The full bounded snapshots are strict, append-only evidence, not process proof.
"""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


ID = Annotated[str, Field(min_length=1, max_length=256)]


class ThreadAttempt(Closed):
    attempt_id: ID
    phase: Literal["initial", "fallback", "nudge"]
    boundary: Literal["host", "provider"]
    ordinal: Annotated[int, Field(ge=0, le=64)]
    executor: ID | None = None
    model: Annotated[str, Field(min_length=1, max_length=512)] | None = None
    possible_launch: bool = False
    launched: bool = False
    no_launch_deferred: bool = False
    settled: bool = False
    quiescent: bool = False


class ThreadLifecycle(Closed):
    version: Literal[1] = 1
    org_slug: ID
    invocation_id: Annotated[int, Field(gt=0)]
    thread_id: ID
    agent_id: ID
    session_id: ID
    preparation_id: ID
    phase: Literal["initial", "fallback", "nudge"] = "initial"
    boundary: Literal["host", "provider"] = "host"
    ordinal: Annotated[int, Field(ge=0, le=64)] = 0
    schedule: Annotated[tuple[Annotated[float, Field(ge=0, le=86400)], ...], Field(max_length=64)] = ()
    attempts: Annotated[tuple[ThreadAttempt, ...], Field(max_length=64)] = ()
    deferred: bool = False
    producer_settled: bool = False
    failed_cleanup: bool = False

    @model_validator(mode="after")
    def validate_state(self) -> ThreadLifecycle:
        if type(self.version) is not int or self.ordinal > len(self.schedule):
            raise ValueError("thread lifecycle retry/version bound")
        if len({a.attempt_id for a in self.attempts}) != len(self.attempts):
            raise ValueError("duplicate thread attempt")
        if any(a.launched and a.no_launch_deferred for a in self.attempts):
            raise ValueError("launched attempt cannot be a no-launch deferral")
        if any(a.quiescent and not a.settled for a in self.attempts):
            raise ValueError("thread attempt quiescence without settlement")
        return self

    @property
    def unknown(self) -> bool:
        return self.failed_cleanup or any(a.possible_launch and not (a.settled and a.quiescent) for a in self.attempts)


class ThreadAdmissionStore:
    def __init__(self, db: Any, org_slug: str) -> None:
        self.db, self.org_slug = db, org_slug

    def read_uncommitted(self, inv: Any) -> ThreadLifecycle | None:
        # Scope remains the real thread ID, not an overloaded task identity.
        rows = self.db._conn.execute(
            "SELECT agent,payload FROM audit_log WHERE task_id=? "
            "AND action='thread_move_lifecycle' ORDER BY id", (inv.thread_id,),
        )
        prior = None
        for row in rows:
            try:
                raw = row["payload"]
                if type(raw) is not str or len(raw.encode("utf8")) > 16384:
                    raise ValueError("audit bound")
                def unique(pairs: list[tuple[str, Any]]) -> dict:
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise ValueError("duplicate thread evidence field")
                        result[key] = value
                    return result
                json.loads(raw, object_pairs_hook=unique)
                state = ThreadLifecycle.model_validate_json(raw, strict=True)
                if (state.org_slug != self.org_slug or state.thread_id != inv.thread_id
                        or state.agent_id != row["agent"]):
                    raise ValueError("audit owner")
                if state.invocation_id != inv.id:
                    continue
                if state.agent_id != inv.agent_name or (inv.session_id and state.session_id != inv.session_id):
                    raise ValueError("invocation owner")
                if prior is not None:
                    self._validate_append(prior, state)
                elif (state.attempts or state.phase != "initial" or state.ordinal or state.schedule
                        or state.deferred or state.producer_settled or state.failed_cleanup):
                    raise ValueError("thread initial preparation evidence missing")
                prior = state
            except (ValueError, TypeError, ValidationError) as exc:
                from runtime.orchestrator.team_move import DeferredTeamMove
                raise DeferredTeamMove("team_move_thread_audit_unavailable") from exc
        return prior

    @staticmethod
    def _validate_append(prior: ThreadLifecycle, current: ThreadLifecycle) -> None:
        if current.phase != prior.phase:
            if prior.phase != "initial" or current.phase not in {"fallback", "nudge"} or current.ordinal != 0:
                raise ValueError("thread phase changed outside finite fallback/nudge")
        elif prior.schedule and (current.schedule != prior.schedule or current.boundary != prior.boundary
                or current.ordinal < prior.ordinal or current.ordinal > prior.ordinal + 1):
            raise ValueError("thread retry budget/ordinal changed")
        if prior.producer_settled and not current.producer_settled:
            if not (prior.deferred and not prior.unknown and not current.deferred):
                raise ValueError("unsettled thread producer adopted")
        for field in ("version", "org_slug", "invocation_id", "thread_id", "agent_id", "session_id", "preparation_id"):
            if getattr(prior, field) != getattr(current, field):
                raise ValueError("thread lifecycle identity changed")
        if prior.failed_cleanup and not current.failed_cleanup:
            raise ValueError("thread cleanup uncertainty erased")
        if len(current.attempts) < len(prior.attempts):
            raise ValueError("thread attempt removed")
        if len({a.attempt_id for a in current.attempts}) != len(current.attempts):
            raise ValueError("thread attempt identity duplicated")
        for old, new in zip(prior.attempts, current.attempts):
            if old.settled and old != new:
                raise ValueError("thread attempt settlement changed")
            if (old.attempt_id, old.phase, old.boundary, old.ordinal, old.executor, old.model) != (new.attempt_id, new.phase, new.boundary, new.ordinal, new.executor, new.model):
                raise ValueError("thread attempt identity changed")
            if old.launched and not new.launched:
                raise ValueError("thread process launch erased")
            if old.no_launch_deferred and not new.no_launch_deferred:
                raise ValueError("thread no-launch deferral erased")
            if old.possible_launch and not new.possible_launch:
                raise ValueError("thread launch erased")
            if new.quiescent and not new.settled:
                raise ValueError("thread cleanup not settled")

    def save_uncommitted(self, inv: Any, state: ThreadLifecycle) -> None:
        # The strict serializer/reader and owner check run BEFORE audit insertion.
        raw = state.model_dump_json()
        checked = ThreadLifecycle.model_validate_json(raw, strict=True)
        if checked != state or len(raw.encode("utf8")) > 16384:
            raise ValueError("thread lifecycle serialization bound")
        if (state.org_slug, state.thread_id, state.invocation_id, state.agent_id) != (
                self.org_slug, inv.thread_id, inv.id, inv.agent_name):
            raise ValueError("thread lifecycle owner mismatch")
        prior = self.read_uncommitted(inv)
        if prior is not None:
            self._validate_append(prior, state)
            if prior == state:
                return
        self.db.insert_audit_log_uncommitted(task_id=inv.thread_id, agent=inv.agent_name,
            action="thread_move_lifecycle", payload=json.loads(raw))
