"""C3 native-owner evidence in existing audit_log; no schema or task journal."""
from __future__ import annotations

import json
from typing import Annotated, Any, Literal

from pydantic import Field, model_validator
from runtime.infrastructure.thread_admission import Closed, ID, ThreadAttempt


class ProducerLifecycle(Closed):
    version: Literal[1] = 1
    org_slug: ID
    kind: Literal['dream', 'wake', 'schedule', 'job', 'writer', 'cleanup']
    source_id: ID
    occurrence_id: ID
    agent_id: ID
    task_id: ID | None = None
    session_id: ID
    preparation_id: ID
    enqueued_at: ID
    boundary: Literal['host', 'provider'] = 'host'
    # None reads old missing facts conservatively: its budget is frozen.
    retry_initialized: bool | None = None
    ordinal: Annotated[int, Field(ge=0, le=64)] = 0
    schedule: Annotated[tuple[Annotated[float, Field(ge=0, le=86400)], ...], Field(max_length=64)] = ()
    attempts: Annotated[tuple[ThreadAttempt, ...], Field(max_length=64)] = ()
    deferred: bool = False
    producer_settled: bool = False
    failed_cleanup: bool = False

    @model_validator(mode='after')
    def valid(self):
        from datetime import datetime
        if datetime.fromisoformat(self.enqueued_at).tzinfo is None:
            raise ValueError('producer enqueue timezone')
        if type(self.version) is not int or self.ordinal > len(self.schedule):
            raise ValueError('producer version/retry bound')
        if self.retry_initialized is False and (self.schedule or self.ordinal or self.attempts):
            raise ValueError('uninitialized producer retry evidence')
        if self.retry_initialized is not None and any(a.boundary != self.boundary or a.ordinal > self.ordinal for a in self.attempts):
            raise ValueError('producer attempt budget owner')
        if len({a.attempt_id for a in self.attempts}) != len(self.attempts):
            raise ValueError('duplicate producer attempt')
        if any(a.phase != 'initial' or (a.launched and a.no_launch_deferred)
               or (a.quiescent and not a.settled) or (a.launched and not a.possible_launch)
               or (a.no_launch_deferred and not a.settled) for a in self.attempts):
            raise ValueError('invalid producer attempt')
        return self

    @property
    def unknown(self):
        return self.failed_cleanup or (self.retry_initialized is None and self.deferred) or any(a.possible_launch and not (a.settled and a.quiescent) for a in self.attempts)


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate producer field')
        out[key] = value
    return out


class ProducerAdmissionStore:
    def __init__(self, db: Any, org_slug: str):
        self.db, self.org_slug = db, org_slug

    @staticmethod
    def append(prior, current, *, legacy_history=False):
        for field in ('version', 'org_slug', 'kind', 'source_id', 'occurrence_id', 'agent_id', 'task_id', 'session_id', 'preparation_id', 'enqueued_at'):
            if getattr(prior, field) != getattr(current, field):
                raise ValueError('producer identity changed')
        if prior.failed_cleanup and not current.failed_cleanup:
            raise ValueError('producer uncertainty erased')
        if prior.retry_initialized is not False and prior.retry_initialized != current.retry_initialized:
            raise ValueError('producer retry initialization changed')
        if current.retry_initialized is False and prior.boundary != current.boundary:
            raise ValueError('uninitialized producer boundary changed')
        if prior.retry_initialized is False and current.retry_initialized is None:
            raise ValueError('producer retry initialization erased')
        legacy_uninitialized = legacy_history and prior.retry_initialized is None and current.retry_initialized is None and not prior.schedule
        if prior.retry_initialized is not False and not legacy_uninitialized and (prior.schedule != current.schedule or prior.boundary != current.boundary):
            raise ValueError('producer budget changed')
        if not prior.ordinal <= current.ordinal <= prior.ordinal + 1:
            raise ValueError('producer ordinal reset/skipped')
        prior_unknown = prior.unknown
        if legacy_history and prior.retry_initialized is None and current.retry_initialized is None:
            # Historical safe reentry keeps its original interpretation. Missing
            # initialization blocks new execution, not reading committed bytes.
            prior_unknown = prior.failed_cleanup or any(a.possible_launch and not (a.settled and a.quiescent) for a in prior.attempts)
        if prior.producer_settled and not current.producer_settled and not (
                prior.deferred and not prior_unknown and not current.deferred):
            raise ValueError('producer ownership reopened')
        if len(current.attempts) < len(prior.attempts):
            raise ValueError('producer attempt erased')
        if len(current.attempts) > len(prior.attempts) + 1 or any(not a.settled for a in prior.attempts[:-1]):
            raise ValueError('producer attempt sequence')
        if len(current.attempts) > len(prior.attempts) and prior.attempts and not prior.attempts[-1].settled:
            raise ValueError('producer open attempt replaced')
        for old, new in zip(prior.attempts, current.attempts):
            if old.settled and old != new:
                raise ValueError('producer settlement changed')
            if (old.attempt_id, old.phase, old.boundary, old.ordinal, old.executor, old.model) != (
                    new.attempt_id, new.phase, new.boundary, new.ordinal, new.executor, new.model):
                raise ValueError('producer attempt changed')
            if old.launched and not new.launched or old.possible_launch and not new.possible_launch or old.no_launch_deferred and not new.no_launch_deferred:
                raise ValueError('producer launch erased')

    def history_uncommitted(self):
        states = {}
        # Exhaustive, including terminal source rows and older uncertain attempts.
        for row in self.db._conn.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='producer_move_lifecycle' ORDER BY id"):
            raw = row['payload']
            if not isinstance(raw, str) or len(raw.encode('utf8')) > 16384:
                raise ValueError('producer audit bound')
            json.loads(raw, object_pairs_hook=_unique)
            state = ProducerLifecycle.model_validate_json(raw, strict=True)
            if state.org_slug != self.org_slug or row['task_id'] != state.source_id or row['agent'] != state.agent_id:
                raise ValueError('producer audit owner mismatch')
            key = (state.kind, state.source_id, state.occurrence_id)
            prior = states.get(key)
            if prior is not None:
                # Preserve the old writer's readable history, without admitting
                # a missing initialization fact for new retry/reentry writes.
                self.append(prior, state, legacy_history=True)
            elif state.retry_initialized is True or state.attempts or state.schedule or state.ordinal or state.deferred or state.producer_settled or state.failed_cleanup:
                raise ValueError('producer initial evidence missing')
            states[key] = state
        return states

    def save_uncommitted(self, state):
        raw = state.model_dump_json()
        state = ProducerLifecycle.model_validate_json(raw, strict=True)
        payload = json.loads(raw)
        durable = json.dumps(payload)  # actual AuditMixin serializer, not compact JSON
        if len(durable.encode('utf8')) > 16384 or ProducerLifecycle.model_validate_json(durable, strict=True) != state:
            raise ValueError('producer audit representation bound')
        prior = self.history_uncommitted().get((state.kind, state.source_id, state.occurrence_id))
        if prior is not None:
            self.append(prior, state)
            if prior == state:
                return
        elif state.retry_initialized is True or state.schedule or state.ordinal or state.attempts or state.producer_settled or state.deferred or state.failed_cleanup:
            raise ValueError('producer initial evidence missing')
        self.db.insert_audit_log_uncommitted(state.source_id, state.agent_id, 'producer_move_lifecycle', payload)


def native_owner_uncommitted(db):
    """No installation/identity inference. Unowned installed layouts refuse."""
    move = getattr(db, '_team_move_coordinator', None)
    if move is not None:
        return move.producers
    if db is not None and db._conn.execute(
            "SELECT 1 FROM sqlite_schema WHERE type='table' AND name='team_move_operations'").fetchone():
        from runtime.orchestrator.team_move import DeferredTeamMove
        raise DeferredTeamMove('team_move_native_owner_unavailable')
    return None


class NativeWriterLifecycle(Closed):
    version: Literal[1] = 1
    org_slug: ID
    writer_id: ID
    publisher: ID
    agents: tuple[ID, ...] = ()
    teams: tuple[ID, ...] = ()
    state: Literal['active', 'settled', 'unknown'] = 'active'

    @model_validator(mode='after')
    def valid(self):
        if type(self.version) is not int or len(set(self.agents)) != len(self.agents) or len(set(self.teams)) != len(self.teams):
            raise ValueError('native writer version/identity scope')
        return self


class NativeWriterStore:
    def __init__(self, db, org_slug):
        self.db, self.org_slug = db, org_slug

    @staticmethod
    def append(prior, current):
        if prior.model_copy(update={'state': current.state}) != current:
            raise ValueError('native writer identity changed')
        if prior.state != 'active' and prior != current:
            raise ValueError('native writer terminal evidence changed')

    def history_uncommitted(self):
        states = {}
        for row in self.db._conn.execute("SELECT task_id,agent,payload FROM audit_log WHERE action='team_move_native_writer' ORDER BY id"):
            raw = row['payload']
            if not isinstance(raw, str) or len(raw.encode('utf8')) > 16384:
                raise ValueError('native writer audit bound')
            json.loads(raw, object_pairs_hook=_unique)
            state = NativeWriterLifecycle.model_validate_json(raw, strict=True)
            if state.org_slug != self.org_slug or row['task_id'] != 'config:team-move-writer:' + state.writer_id or row['agent'] != 'native_writer':
                raise ValueError('native writer audit owner')
            prior = states.get(state.writer_id)
            if prior is None and state.state != 'active':
                raise ValueError('native writer initial evidence missing')
            if prior is not None:
                self.append(prior, state)
            states[state.writer_id] = state
        return states

    def save_uncommitted(self, state):
        raw = json.dumps(json.loads(state.model_dump_json()))
        if len(raw.encode('utf8')) > 16384 or NativeWriterLifecycle.model_validate_json(raw, strict=True) != state:
            raise ValueError('native writer representation bound')
        prior = self.history_uncommitted().get(state.writer_id)
        if prior is not None:
            self.append(prior, state)
        elif state.state != 'active':
            raise ValueError('native writer initial evidence missing')
        self.db.insert_audit_log_uncommitted('config:team-move-writer:' + state.writer_id,
            'native_writer', 'team_move_native_writer', json.loads(raw))
