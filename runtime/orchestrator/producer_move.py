"""Shared native producer/writer admission; the module name is historical.

The short gate never spans await, filesystem work or provider execution.
Registered ownership does span them, and survives as strict audit evidence.
"""
from __future__ import annotations

import uuid
import logging
import hashlib
import json
import time
from datetime import datetime, timezone
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from typing import Any

from runtime.infrastructure.producer_admission import ProducerAdmissionStore, ProducerLifecycle, native_owner_uncommitted, NativeWriterLifecycle, NativeWriterStore
from runtime.infrastructure.thread_admission import ThreadAttempt
from runtime.infrastructure.team_move_store import MoveRequest, _decode
from runtime.orchestrator.team_move import DeferredTeamMove, PermanentAgentIdentity

logger = logging.getLogger(__name__)

_current: ContextVar[Any] = ContextVar('native_move_preparation', default=None)
_TABLES = {'dream': 'dreams', 'wake': 'work_hours', 'schedule': 'schedules', 'job': 'jobs'}


def current_producer_preparation():
    return _current.get()


def owner_for(db):
    if db is None:
        return None
    with db._lock:
        return native_owner_uncommitted(db)


class NativeWriterDisposition:
    """Only the native caller can establish a validation-only refusal.

    Exact exception identity matters: failed rollback/cleanup must not inherit
    the original validation's no-effect evidence. No HTTP status is classified.
    """
    def __init__(self):
        self.writer_id = None
        self.no_effect_error = None
        self.uncertain = False

    @contextmanager
    def validation(self):
        try:
            yield
        except Exception as exc:
            self.no_effect_error = exc
            raise


@contextmanager
def producer_preparation_effects(owner):
    """An explicit synchronous native preparation/validation interval.

    Active durable ownership already covers interruption. A caught error or
    an internally reported partial effect retains sticky cleanup uncertainty.
    Successful completion is evidence for this interval only, not a process
    receipt or evidence that an earlier failed interval settled.
    """
    if owner is None:
        yield
        return
    token = _current.set(owner)
    from runtime.orchestrator.workspace_adapters import _repo_effect_interval
    try:
        with _repo_effect_interval(owner):
            yield
    except BaseException as exc:
        try:
            owner.failed_cleanup()
        except BaseException as evidence:
            exc.add_note(f'native preparation evidence failed: {evidence}')
        raise
    finally:
        _current.reset(token)


@contextmanager
def native_writer(db, *, publisher, agents=(), teams=(), durable=True, settlement=False):
    """Reservation only, never a gate/DB lock across the writer body.

    The caller retains its existing profile/workflow/roster/store lock order.
    Unscoped canonical changes invalidate org-wide snapshot inputs and reserve
    that input. This does not authorize D to borrow an existing journal.
    """
    disposition = NativeWriterDisposition()
    admission = owner_for(db)
    if admission is None:
        yield disposition
        return
    with db._lock:
        registry = admission.move._teams_reader() if admission.move._teams_reader else None
        member_teams = set(teams)
        unknown_membership = False
        for agent in agents:
            team = None if registry is None else (registry.team_for_agent(agent) or registry.team_for_manager(agent))
            if team is not None:
                member_teams.add(team)
            elif not teams:
                unknown_membership = True
        teams = tuple(sorted(member_teams))
    key = uuid.uuid4().hex
    state = NativeWriterLifecycle(org_slug=admission.move.store.org_slug,
        writer_id=key, publisher=publisher, agents=tuple(agents), teams=tuple(teams))
    # Native terminal recovery already owns publisher/DB; its leaf check is
    # serialized against the operation's SAME DB COMMIT without reverse gate.
    gate = nullcontext() if db._lock._is_owned() else admission.move.gate
    with gate, db._lock:
        if not settlement and publisher == 'disk_sweep':
            admission.check_uncommitted(agents=agents, teams=teams, global_write=unknown_membership or not (agents or teams))
        if durable:
            admission.write_writer(state)
            disposition.writer_id = key
        admission.writer_epoch += 1
        admission.writers[key] = dict(owner_id=key, publisher=publisher, agents=tuple(agents), teams=tuple(teams))
    primary = None
    try:
        yield disposition
    except BaseException as exc:
        primary = exc
        raise
    finally:
        with db._lock:
            # Do not acquire move gate from native terminal/compensation owners.
            # A persistence failure keeps the live reservation and active audit.
            try:
                if durable:
                    unknown = disposition.uncertain or (primary is not None and primary is not disposition.no_effect_error)
                    admission.write_writer(state.model_copy(update={'state': 'unknown' if unknown else 'settled'}))
            except BaseException as settlement:
                if primary is None:
                    raise
                primary.add_note(f'native writer evidence failed: {settlement}')
                logger.exception('Retaining native writer ownership after evidence failure')
            else:
                admission.writers.pop(key, None)
                admission.writer_epoch += 1


class ProducerPreparation:
    def __init__(self, admission, state):
        self.admission, self.state = admission, state
        self.identity = PermanentAgentIdentity(state.org_slug, state.agent_id)
        self.owner_live = True
        self.last_spec = None
        self.executor = self.model = None
        self.attempt_open = False
        self.safe_defer = False

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
    def move_reservation(self):
        return self

    @property
    def session_id(self):
        return self.state.session_id

    def save(self, **changes):
        candidate = self.state.model_copy(update=changes)
        db = self.admission.db
        if db._conn.in_transaction:
            self.state = self.state.model_copy(update={'failed_cleanup': True})
            raise DeferredTeamMove('team_move_producer_sql_owner_unavailable')
        began = False
        try:
            db._conn.execute('BEGIN IMMEDIATE')
            began = True
            self.admission.store.save_uncommitted(candidate)
            db._conn.commit()
        except BaseException as exc:
            self.state = self.state.model_copy(update={'failed_cleanup': True})
            if began:
                try:
                    db._conn.rollback()
                except BaseException as rollback:
                    exc.add_note(f'native producer rollback failed: {rollback}')
            raise
        self.state = candidate

    def before_fifo(self):
        with self.admission.move.gate, self.admission.db._lock:
            self.admission.revalidate_uncommitted(self)

    @property
    def enqueued_at(self):
        age = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(self.state.enqueued_at)).total_seconds())
        return time.monotonic() - age

    def retry_schedule(self, boundary, schedule):
        with self.admission.move.gate, self.admission.db._lock:
            self.admission.revalidate_uncommitted(self)
            if self.state.retry_initialized is None:
                raise DeferredTeamMove('team_move_producer_retry_initialization_unknown')
            if self.state.retry_initialized is False:
                self.save(retry_initialized=True, boundary=boundary, schedule=tuple(float(x) for x in schedule))
            elif self.state.boundary != boundary:
                raise DeferredTeamMove('team_move_producer_retry_owner')
            return self.state.ordinal, self.state.schedule

    def next_attempt(self, ordinal):
        with self.admission.db._lock:
            if not self.state.ordinal < ordinal <= len(self.state.schedule):
                raise DeferredTeamMove('team_move_producer_retry_budget')
            self.save(ordinal=ordinal)

    def commit(self, ctx=None):
        with self.admission.move.gate, self.admission.db._lock:
            self.admission.revalidate_uncommitted(self)
            with ctx._lock if ctx is not None else nullcontext():
                if ctx is not None and ctx._terminal_reason is not None:
                    return False
                if self.state.retry_initialized is None:
                    raise DeferredTeamMove('team_move_producer_retry_initialization_unknown')
                if self.state.retry_initialized is False:
                    # Direct job/no-retry launch has an affirmative zero budget.
                    self.save(retry_initialized=True)
                if self.attempt_open:
                    raise DeferredTeamMove('team_move_producer_attempt_unsettled')
                self.save(attempts=self.state.attempts + (ThreadAttempt(
                    attempt_id=uuid.uuid4().hex, phase='initial', boundary=self.state.boundary,
                    ordinal=self.state.ordinal, executor=self.executor, model=self.model, possible_launch=True),), deferred=False)
                self.attempt_open = True
                self.safe_defer = False
        return True

    def _producer_move_commit(self, ctx):
        return self.last_spec if self.commit(ctx) else None

    def __call__(self):
        pass  # final private commitment protocol; no pre-prepare launch grant

    def before_selflaunch(self):
        try:
            self.before_fifo()
        except DeferredTeamMove:
            self.safe_defer = self.attempt_open
            raise
        if not self.attempt_open:
            self.commit()

    def on_started(self):
        with self.admission.db._lock:
            if not self.attempt_open:
                raise DeferredTeamMove('team_move_producer_launch_owner_missing')
            last = self.state.attempts[-1].model_copy(update={'launched': True})
            try:
                self.save(attempts=self.state.attempts[:-1] + (last,))
            except Exception:
                # Already launched: keep unknown local ownership and let native
                # containment finish. Never interrupt cleanup to report evidence.
                self.state = self.state.model_copy(update={'failed_cleanup': True,
                    'attempts': self.state.attempts[:-1] + (last,)})

    def terminal(self, outcome):
        with self.admission.db._lock:
            if not self.attempt_open:
                return
            receipt = None if outcome is None else outcome.receipt
            clean = bool(receipt is not None and receipt.backend != 'passthrough'
                and receipt.quiescent is True and receipt.cleanup_status.value != 'incomplete'
                and not receipt.survivors and outcome.cleanup_error is None)
            no_launch = self.safe_defer and not self.state.attempts[-1].launched
            clean |= bool(no_launch and (outcome is None or outcome.cleanup_error is None))
            last = self.state.attempts[-1].model_copy(update={'settled': True, 'quiescent': clean,
                'no_launch_deferred': bool(no_launch)})
            self.save(attempts=self.state.attempts[:-1] + (last,))
            self.attempt_open = False

    def direct_terminal(self):
        self.terminal(None)  # local parent return is not process-tree proof

    def failed_cleanup(self, *, repository_deadline: float | None = None):
        if repository_deadline is not None:
            # An expired/busy persistence gate cannot delay the local latch.
            self.state = self.state.model_copy(update={"failed_cleanup": True})
            from runtime.orchestrator.workspace_adapters import _repo_preparation_cleanup
            with _repo_preparation_cleanup(self, repository_deadline):
                if not self.admission.db._conn.in_transaction:
                    self.save(failed_cleanup=True)
            return
        with self.admission.db._lock:
            if self.admission.db._conn.in_transaction:
                # A caught package/event error may still own native SQL work.
                # Retain active durable ownership and local sticky uncertainty;
                # finish persists it only after that real transaction settles.
                self.state = self.state.model_copy(update={'failed_cleanup': True})
                return
            self.save(failed_cleanup=True)

    def invoke(self, action):
        token = _current.set(self)
        try:
            return action()
        finally:
            _current.reset(token)

    def finish(self, *, deferred=False, failed=False):
        failed = failed or any(not v.closed for v in self._repository_preparations.values())
        with self.admission.db._lock:
            if self.attempt_open:
                self.direct_terminal()
            self.save(producer_settled=True, deferred=deferred,
                failed_cleanup=self.state.failed_cleanup or failed)
            self.owner_live = False
            if all(v.closed for v in self._repository_preparations.values()):
                self.admission.live.pop((self.state.kind, self.state.source_id), None)


class _DeferredJob:
    """Original job-only callback/owner association, never reconstructed rows."""
    def __init__(self, owner, callback):
        self.owner, self.callback = owner, callback


def _stage_job_terminal_uncommitted(owner, *, observed: bool, failed: bool = False, deferred: bool = False):
    """Job-only candidate in the original owner's row/audit/Pause transaction.

    A direct child observation settles only that attempt, never descendants.
    Failed or unfinished ownership remains active/unknown, with every older
    attempt and sticky failed_cleanup byte retained by the strict writer.
    """
    if owner is None:
        return None
    a, s = owner.admission, owner.state
    if s.kind != 'job' or a.live.get(('job', s.source_id)) is not owner or not owner.owner_live:
        raise DeferredTeamMove('job_registration_unavailable')
    if a.history_uncommitted().get(('job', s.source_id, s.occurrence_id)) != s:
        raise DeferredTeamMove('team_move_producer_evidence_changed')
    attempts = s.attempts
    if observed:
        if not owner.attempt_open or not attempts or not attempts[-1].launched:
            raise DeferredTeamMove('team_move_producer_attempt_unsettled')
        attempts = attempts[:-1] + (attempts[-1].model_copy(update={'settled': True, 'quiescent': False}),)
    safe_defer = deferred and not attempts and not s.failed_cleanup and not failed
    finished_tail = (observed or not attempts) and not failed and not s.failed_cleanup
    candidate = s.model_copy(update=dict(attempts=attempts,
        producer_settled=finished_tail, deferred=safe_defer,
        failed_cleanup=s.failed_cleanup or failed or (not observed and bool(attempts))))
    a.store.save_uncommitted(candidate)
    return candidate


def _release_job_terminal(owner, candidate, *, callback=None):
    """Only after COMMIT; never pop a replacement owner or callback."""
    if owner is None:
        return
    a, s = owner.admission, owner.state
    if a.live.get(('job', s.source_id)) is not owner:
        raise DeferredTeamMove('job_registration_unavailable')
    owner.state = candidate
    if candidate.attempts and candidate.attempts[-1].settled:
        owner.attempt_open = False
    if candidate.producer_settled:
        owner.owner_live = False
        del a.live[('job', s.source_id)]
        retained = a.deferred_jobs.get(s.source_id)
        if retained is not None and retained.owner is owner and retained.callback is callback:
            del a.deferred_jobs[s.source_id]



class ProducerMoveAdmission:
    """Historical name for shared native producer, retry and writer ownership."""
    def __init__(self, move):
        self.move, self.db = move, move.store.db
        self.store = ProducerAdmissionStore(self.db, move.store.org_slug)
        self.writer_store = NativeWriterStore(self.db, move.store.org_slug)
        self.live = {}
        self.writers = {}
        self.startup_deferred = set()
        self.deferred_jobs = {}
        self.root = None
        self.sessions = None
        self.writer_epoch = 0

    def write_writer(self, state):
        if self.db._conn.in_transaction:
            raise DeferredTeamMove('team_move_writer_sql_owner_unavailable')
        try:
            self.db._conn.execute('BEGIN IMMEDIATE')
            self.writer_store.save_uncommitted(state)
            self.db._conn.commit()
        except BaseException:
            self.db._conn.rollback()
            raise

    def check_uncommitted(self, *, agents=(), teams=(), global_write=False):
        row = self.move.active_uncommitted()
        if row is None:
            return
        request = _decode(MoveRequest, row['request_bytes'])
        if global_write or set(agents).intersection(request.affected_agents) or set(teams).intersection((request.source_team, request.target_team)):
            raise DeferredTeamMove('team_move_native_held', row['operation_id'])

    def check_job_uncommitted(self, record):
        task = self.db.get_task(record.task_id)
        if task is None:
            raise DeferredTeamMove('team_move_job_task_owner_unavailable')

    def history_uncommitted(self):
        try:
            return self.store.history_uncommitted()
        except (ValueError, TypeError) as exc:
            raise DeferredTeamMove('team_move_producer_evidence_unavailable') from exc

    @staticmethod
    def occurrence(kind, row):
        if kind != 'schedule':
            return row['id']
        # Native row epoch, not a newly minted occurrence or shifted deadline.
        return hashlib.sha256(json.dumps([row['id'], row['fire_count'], row['fire_at']],
            separators=(',', ':')).encode('utf8')).hexdigest()

    def state_for_uncommitted(self, kind, row, states=None):
        states = self.history_uncommitted() if states is None else states
        return states.get((kind, row['id'], self.occurrence(kind, row)))

    def preserve_uncommitted(self, kind, source_id, agent_id):
        """Early native reapers preserve lifecycle-owned uncertainty."""
        return any(state.kind == kind and state.source_id == source_id and (not state.producer_settled or state.deferred or state.unknown)
            for state in self.history_uncommitted().values())

    def reserve(self, kind, source_id):
        with self.move.gate, self.db._lock:
            row = self.db._conn.execute(f'SELECT * FROM {_TABLES[kind]} WHERE id=?', (source_id,)).fetchone()
            if row is None:
                return None
            teams = self.move._teams_reader() if self.move._teams_reader else None
            if teams is None or row['agent_name'] not in teams.all_agents():
                raise DeferredTeamMove('team_move_producer_identity_unavailable')
            if kind == 'job':
                self.check_job_uncommitted(self.db.get_job(source_id))
            key = (kind, source_id)
            if key in self.live:
                raise DeferredTeamMove('team_move_producer_owner_live')
            expected = 'firing' if kind == 'schedule' else 'pending'
            if row['status'] not in (expected, 'running'):
                return None
            prior = self.state_for_uncommitted(kind, row)
            if prior is not None and (prior.agent_id != row['agent_name'] or (kind == 'job' and prior.task_id != row['task_id'])):
                raise DeferredTeamMove('team_move_producer_owner_changed')
            if prior is not None and (prior.unknown or not prior.producer_settled or not prior.deferred):
                raise DeferredTeamMove('team_move_producer_owner_unsettled')
            # Safe deferred RUNNING rows retain native ownership, not a new
            # occurrence or new SID. A row without proof is never adopted.
            if row['status'] != expected and not (prior and prior.deferred and row['status'] == 'running'):
                return None
            job_sid = None
            if kind == 'job':
                task = self.db.get_task(row['task_id'])
                pause = getattr(self.db, '_task_pause_store', None)
                if pause is not None:
                    control = pause.row_uncommitted(pause.root_uncommitted(task.id)['id'])
                    entries = [] if control is None else control['journal']['entries']
                    matches = [e for e in entries if e['owner'] == 'job' and e['owner_identity'].get('job_id') == source_id]
                    if len(matches) == 1:
                        job_sid = matches[0]['session_id']
                job_sid = job_sid or task.current_session_id
                if not job_sid:
                    raise DeferredTeamMove('team_move_job_session_owner_unavailable')
            state = prior or ProducerLifecycle(org_slug=self.move.store.org_slug,
                retry_initialized=False, kind=kind, source_id=source_id, occurrence_id=self.occurrence(kind, row), agent_id=row['agent_name'],
                task_id=row['task_id'] if kind == 'job' else None, enqueued_at=datetime.now(timezone.utc).isoformat(),
                session_id=job_sid or f'sess-{uuid.uuid4().hex}', preparation_id=uuid.uuid4().hex)
            owner = ProducerPreparation(self, state)
            owner.save(deferred=False, producer_settled=False)
            self.live[key] = owner
            return owner

    def revalidate_uncommitted(self, owner):
        state = owner.state
        if self.live.get((state.kind, state.source_id)) is not owner or not owner.owner_live:
            raise DeferredTeamMove('team_move_producer_owner_missing')
        row = self.db._conn.execute(f'SELECT * FROM {_TABLES[state.kind]} WHERE id=?', (state.source_id,)).fetchone()
        if row is None or row['agent_name'] != state.agent_id:
            raise DeferredTeamMove('team_move_producer_owner_changed')
        if state.kind == 'job':
            if row['task_id'] != state.task_id:
                raise DeferredTeamMove('team_move_job_task_owner_changed')
            self.check_job_uncommitted(self.db.get_job(state.source_id))
        if row['status'] not in ('pending', 'running', 'firing'):
            raise DeferredTeamMove('team_move_producer_source_terminal')
        if self.state_for_uncommitted(state.kind, row) != state:
            raise DeferredTeamMove('team_move_producer_evidence_changed')

    def _released_sources(self) -> tuple:
        """Native notification selection only; never new launch permission."""
        selected = []
        with self.move.gate, self.db._lock:
            states = self.history_uncommitted()
            for kind in ('dream', 'wake', 'schedule'):
                status = 'firing' if kind == 'schedule' else 'pending'
                for row in self.db._conn.execute(f'SELECT * FROM {_TABLES[kind]}'):
                    state = self.state_for_uncommitted(kind, row, states)
                    safe = state is None or (state.deferred and state.producer_settled and not state.unknown)
                    if safe and (row['status'] == status or state and state.deferred and row['status'] == 'running'):
                        selected.append((kind, row['id']))
        return tuple(selected)

    @staticmethod
    def _publish_released_sources(org: Any, selected: tuple) -> None:
        # Loop-owned queue/dedup mutation. Native reserve/claim still revalidates
        # current release/history; a selected notification confers no admission.
        from runtime.daemon.dream_queue import DreamJob
        from runtime.daemon.wake_queue import WakeJob
        from runtime.daemon.schedule_queue import ScheduleJob
        queues = {'dream': (org.dream_queue, DreamJob, 'dream_id'),
            'wake': (org.wake_queue, WakeJob, 'work_hour_id'),
            'schedule': (org.schedule_queue, ScheduleJob, 'schedule_id')}
        for kind, source_id in selected:
            queue, cls, field = queues[kind]
            queue.put_nowait(cls(org_slug=org.slug, **{field: source_id}))

    def redeliver_after_release(self, org):
        """Existing synchronous scheduler entry; queues retain dedup semantics."""
        self._publish_released_sources(org, self._released_sources())


async def run_native_producer(org, kind, source_id, action):
    admission = owner_for(org.db)
    if admission is None:
        return await action(None)
    owner = None
    deferred = failed = False
    primary = None
    try:
        owner = admission.reserve(kind, source_id)
        if owner is None:
            return
        return await action(owner)
    except DeferredTeamMove:
        deferred = True  # owner rows remain unchanged; no failure/callback
    except BaseException as exc:
        primary = exc
        failed = True
        raise
    finally:
        if owner is not None:
            try:
                owner.finish(deferred=deferred, failed=failed)
            except BaseException as settlement:
                if primary is None:
                    raise
                primary.add_note(f'native producer evidence failed: {settlement}')
                logger.exception('Retaining native producer ownership after evidence failure')


class _BoundProducerExecutor:
    """Carry actual C3 ownership into executor-pool threads and native retries."""
    def __init__(self, executor, owner):
        self.executor, self.owner = executor, owner

    def __getattr__(self, name):
        value = getattr(self.executor, name)
        if name != 'build_launch_spec':
            return value
        def build(**kwargs):
            spec = value(**kwargs)
            self.owner.last_spec = spec
            return spec
        return build

    def run(self, **kwargs):
        previous = kwargs.get('on_started')
        def started(pid):
            self.owner.on_started()
            if previous is not None:
                previous(pid)
        running = kwargs.get('running')
        if running is None:
            kwargs['on_started'] = started
        def execute():
            try:
                result = self.executor.run(**kwargs)
            except DeferredTeamMove:
                # Preserve the native final-before-Popen no-launch evidence;
                # completed preparation is not an unresolved partial effect.
                raise
            except BaseException as exc:
                try:
                    self.owner.failed_cleanup()
                except BaseException as evidence:
                    exc.add_note(f'native executor preparation evidence failed: {evidence}')
                raise
            if not getattr(result, 'success', False) and not self.owner.state.attempts:
                # A caught pre-launch failure carries no native no-effect proof.
                self.owner.failed_cleanup()
            return result
        return self.owner.invoke(execute)


def bind_producer_executor(executor, owner):
    return executor if owner is None else _BoundProducerExecutor(executor, owner)


@contextmanager
def native_claim(db, agent_id):
    admission = owner_for(db)
    if admission is None:
        yield
        return
    with admission.move.gate, db._lock:
        yield


async def redeliver_native_jobs(org):
    import asyncio
    from runtime.daemon.jobs_runner import register_runner_task
    admission = owner_for(org.db)
    if admission is None:
        return
    for job_id, retained in tuple(admission.deferred_jobs.items()):
        try:
            owner = admission.reserve('job', job_id)
        except DeferredTeamMove:
            continue
        if owner is not None:
            register_runner_task(job_id, asyncio.create_task(retained.callback(owner)),
                db=org.db, org_slug=org.slug, move_owner=owner)
            if admission.deferred_jobs.get(job_id) is retained:
                del admission.deferred_jobs[job_id]
