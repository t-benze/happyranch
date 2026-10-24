"""Private C2 lifecycle evidence in the EXISTING thread audit scope.

No schema install, invocation credential, token mutation or task-Pause journal.
The full bounded snapshots are strict, append-only evidence, not process proof.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
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
    # Old missing facts remain readable, never a new retry initialization grant.
    retry_initialized: bool | None = None
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
        if self.retry_initialized is False and (self.schedule or self.ordinal
                or any(a.phase == self.phase for a in self.attempts)):
            raise ValueError("uninitialized thread retry evidence")
        if self.retry_initialized is not None and any(a.phase == self.phase
                and (a.boundary != self.boundary or a.ordinal > self.ordinal) for a in self.attempts):
            raise ValueError("thread attempt budget owner")
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
        try:
            for _, state in self._replay_states(inv, rows, check=lambda: None):
                prior = state
        except (ValueError, TypeError, ValidationError) as exc:
            from runtime.orchestrator.team_move import DeferredTeamMove
            raise DeferredTeamMove("team_move_thread_audit_unavailable") from exc
        return prior

    def _replay_bounded(
        self, inv: Any, rows: Sequence[Mapping[str, Any]], *, check: Callable[[], None],
    ) -> tuple[tuple[int, ThreadLifecycle], ...]:
        """Replay a COMPLETE selected thread scope, already charged by Database.

        The caller supplies every lifecycle row from the true beginning, never
        a last-row or truncated prefix. Missing initial history refuses. This
        private entry has no SQL or daemon dependency; its budget check is pure.
        """
        check()
        if len(rows) > 1024:
            raise ValueError("thread history bound")
        states = tuple((row["id"], state) for row, state in self._replay_states(inv, rows, check=check))
        if not states:
            raise ValueError("thread history missing")
        check()
        return states

    def _replay_states(
        self, inv: Any, rows: Iterable[Mapping[str, Any]], *, check: Callable[[], None],
    ) -> Iterable[tuple[Mapping[str, Any], ThreadLifecycle]]:
        return self._replay_selected(inv,
            self._decode_states(inv.thread_id, rows, check=check), check=check)

    def _decode_states(
        self, thread_id: str, rows: Iterable[Mapping[str, Any]], *, check: Callable[[], None],
    ) -> Iterable[tuple[Mapping[str, Any], ThreadLifecycle]]:
        """Common family validation, including siblings of the selected owner."""
        for row in rows:
            check()
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
            check()
            state = ThreadLifecycle.model_validate_json(raw, strict=True)
            if (state.org_slug != self.org_slug or state.thread_id != thread_id
                    or state.agent_id != row["agent"]):
                raise ValueError("audit owner")
            yield row, state

    def _replay_selected(
        self, inv: Any, decoded: Iterable[tuple[Mapping[str, Any], ThreadLifecycle]], *,
        check: Callable[[], None],
    ) -> Iterable[tuple[Mapping[str, Any], ThreadLifecycle]]:
        prior = None
        for row, state in decoded:
            if state.invocation_id != inv.id:
                continue
            if state.agent_id != inv.agent_name or (inv.session_id and state.session_id != inv.session_id):
                raise ValueError("invocation owner")
            if prior is not None:
                self._validate_append(prior, state)
            elif (state.retry_initialized is True or state.attempts or state.phase != "initial" or state.ordinal or state.schedule
                    or state.deferred or state.producer_settled or state.failed_cleanup):
                raise ValueError("thread initial preparation evidence missing")
            prior = state
            check()
            yield row, state

    def _read_segment(self, thread_id: str) -> _ThreadLifecycleSegment:
        return _ThreadLifecycleSegment(self, thread_id)

    @staticmethod
    def _validate_append(prior: ThreadLifecycle, current: ThreadLifecycle) -> None:
        if (prior.retry_initialized is None) != (current.retry_initialized is None):
            raise ValueError("thread historical retry fact replaced")
        if current.phase != prior.phase:
            if prior.phase != "initial" or current.phase not in {"fallback", "nudge"} or current.ordinal != 0:
                raise ValueError("thread phase changed outside finite fallback/nudge")
            if current.retry_initialized is not None and (current.retry_initialized is not False or current.schedule):
                raise ValueError("thread new phase initialization missing")
        elif prior.retry_initialized is not None or current.retry_initialized is not None:
            if (prior.retry_initialized is None
                    or (prior.retry_initialized is not False and prior.retry_initialized != current.retry_initialized)
                    or current.retry_initialized is None
                    or (current.retry_initialized is False and prior.boundary != current.boundary)
                    or (prior.retry_initialized is False and current.retry_initialized is True
                        and (current.ordinal != 0 or current.attempts != prior.attempts))
                    or (prior.retry_initialized is True and (prior.schedule != current.schedule or prior.boundary != current.boundary))
                    or not prior.ordinal <= current.ordinal <= prior.ordinal + 1):
                raise ValueError("thread retry initialization/budget changed")
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
        elif (state.retry_initialized is not False or state.attempts or state.phase != "initial"
                or state.ordinal or state.schedule or state.deferred or state.producer_settled or state.failed_cleanup):
            raise ValueError("thread initial preparation evidence missing")
        payload = json.loads(raw)
        # AuditMixin writes json.dumps(payload), including ASCII escaping and
        # spaces. Bound and validate those ACTUAL durable bytes before INSERT,
        # not just Pydantic's compact in-memory serialization.
        audit_raw = json.dumps(payload)
        if len(audit_raw.encode("utf8")) > 16384 or ThreadLifecycle.model_validate_json(audit_raw, strict=True) != state:
            raise ValueError("thread audit writer representation bound")
        self.db.insert_audit_log_uncommitted(task_id=inv.thread_id, agent=inv.agent_name,
            action="thread_move_lifecycle", payload=payload)


class _ThreadLifecycleSegment:
    """Private lazy conclusions for one cutover's mutation-free interval."""

    def __init__(self, store: ThreadAdmissionStore, thread_id: str) -> None:
        self.store, self.thread_id = store, thread_id
        self.decoded: dict[int, list[tuple[Mapping[str, Any], ThreadLifecycle]]] | None = None
        self.selected: dict[tuple[int, str, str | None], ThreadLifecycle | None] = {}

    def read(self, inv: Any) -> ThreadLifecycle | None:
        try:
            if inv.thread_id != self.thread_id:
                raise ValueError("thread segment owner")
            if self.decoded is None:
                rows = self.store.db._conn.execute(
                    "SELECT agent,payload FROM audit_log WHERE task_id=? "
                    "AND action='thread_move_lifecycle' ORDER BY id", (self.thread_id,),
                )
                decoded: dict[int, list[tuple[Mapping[str, Any], ThreadLifecycle]]] = {}
                # All common siblings validate even when selected history is
                # absent. Selected append/initial/session errors stay lazy.
                for row, state in self.store._decode_states(self.thread_id, rows, check=lambda: None):
                    decoded.setdefault(state.invocation_id, []).append((row, state))
                self.decoded = decoded
            key = (inv.id, inv.agent_name, inv.session_id)
            if key not in self.selected:
                prior = None
                for _, state in self.store._replay_selected(
                        inv, self.decoded.get(inv.id, ()), check=lambda: None):
                    prior = state
                self.selected[key] = prior
            return self.selected[key]
        except (ValueError, TypeError, ValidationError) as exc:
            from runtime.orchestrator.team_move import DeferredTeamMove
            raise DeferredTeamMove("team_move_thread_audit_unavailable") from exc
