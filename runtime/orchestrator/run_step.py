"""Implementation of Orchestrator.run_step — the single primitive that advances
a task one subprocess call at a time. Separate from orchestrator.py so the
algorithm has its own test surface.

Entry contract (Path B): task MUST be one of:
  (a) status=pending, or
  (b) status=in_progress AND block_kind=DELEGATED AND all children are terminal, or
  (c) status=in_progress AND block_kind=BLOCKED_ON_JOB AND all blocking jobs are terminal.
Any other state = stale enqueue, silent no-op — in particular
in_progress + block_kind IS NULL is a LIVE subprocess and must NOT be admitted
(it would double-spawn). Phase 3: no legacy blocked shapes are accepted;
the boot-time migration flips them before request handling.

Exit contract: task ends in exactly one of {in_progress-then-crashed,
completed, failed, in_progress(DELEGATED), in_progress(BLOCKED_ON_JOB),
escalated}.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
import importlib.util
import json
import logging
import os
import stat
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Callable, NamedTuple

from runtime.infrastructure.database import (
    AttachmentCorrectionStopped, Committed, InvalidLineage, LostClaim, PendingRetry, RetryClaim, SpawnOutcome,
)
from runtime.infrastructure.db.authority_v2_attempts import _V2StartupConnectionUnsafe
from runtime.models import BlockKind, TaskStatus
from runtime.orchestrator.org_config import load_org_config
from runtime.orchestrator.executors import _meaningful_stderr
from runtime.orchestrator.task_prompt_headers import (
    _list_candidate_agents,
    _REVISIT_DISCIPLINE_LINES,
    _revisit_header_if_applicable,
    _auto_revisit_header,
    _resolved_escalation_header_if_applicable,
    _build_prior_steps_from_db,
    _summarize_recent_chain,
    _fanout_join_header_if_applicable,
)

if TYPE_CHECKING:
    from runtime.models import TaskRecord
    from runtime.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

TERMINAL_STATES = frozenset({
    TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SUPERSEDED,
    TaskStatus.CANCELLED,  # Path B: founder-initiated terminal stop.
})

# Path B: parked-but-in_progress carriers. A task with block_kind set
# runs no subprocess — it is waiting on children/jobs it manages.
# block_kind IS NULL (a live subprocess) is deliberately NOT a parked carrier.
# Phase 3 (THR-037): the deprecated BLOCKED status was retired.
_PARKED_CARRIER_STATUSES = frozenset({TaskStatus.IN_PROGRESS})

_TERMINAL_WORKTREE_STATUSES = frozenset({
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
})
_TERMINAL_WORKTREE_TIMEOUT_SECONDS = 5.0
_TERMINAL_WORKTREE_OUTPUT_LIMIT = 1_000_000
_TERMINAL_WORKTREE_SCANNER_PATH = (
    Path(__file__).resolve().parents[1]
    / "skills/bundled/workspace-cleanup/scripts/check_path_use.py"
)
_TERMINAL_WORKTREE_SCANNER_MODULE_NAME = (
    "_happyranch_terminal_worktree_check_path_use"
)
_TERMINAL_WORKTREE_SCANNER_MODULE: ModuleType | None = None
_TERMINAL_WORKTREE_SCANNER_LOCK = threading.Lock()


class TerminalWorktreeReclaimOutcome(NamedTuple):
    """Bounded, non-raising disposition for one terminal hook attempt."""

    kind: str
    reason: str


def _run_terminal_worktree_command(
    args: list[str], *, cwd: Path, timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run one literal bounded probe/action without a shell."""
    return subprocess.run(
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=max(timeout, 0.001),
        check=False,
    )


def _terminal_worktree_remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("terminal worktree reclamation deadline expired")
    return remaining


def _terminal_worktree_path_has_symlink(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        if stat.S_ISLNK(current.lstat().st_mode):
            return True
    return False


def _load_terminal_worktree_scanner() -> ModuleType:
    """Load the bundled scanner by path as the single process-rule source."""
    global _TERMINAL_WORKTREE_SCANNER_MODULE
    if _TERMINAL_WORKTREE_SCANNER_MODULE is not None:
        return _TERMINAL_WORKTREE_SCANNER_MODULE
    with _TERMINAL_WORKTREE_SCANNER_LOCK:
        if _TERMINAL_WORKTREE_SCANNER_MODULE is not None:
            return _TERMINAL_WORKTREE_SCANNER_MODULE
        spec = importlib.util.spec_from_file_location(
            _TERMINAL_WORKTREE_SCANNER_MODULE_NAME,
            _TERMINAL_WORKTREE_SCANNER_PATH,
        )
        if spec is None or spec.loader is None:
            raise ImportError("bundled workspace-cleanup scanner is unavailable")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            if sys.modules.get(spec.name) is module:
                sys.modules.pop(spec.name, None)
            raise
        _TERMINAL_WORKTREE_SCANNER_MODULE = module
        return module


def _terminal_worktree_process_reference(
    candidate: Path, deadline: float,
) -> str | None:
    """Map the shared same-user scanner outcome to terminal-hook reasons."""
    try:
        scanner = _load_terminal_worktree_scanner()
        bounds = scanner.Bounds(
            deadline_seconds=_terminal_worktree_remaining(deadline),
        )
        result = scanner.scan(candidate, bounds=bounds)
        _terminal_worktree_remaining(deadline)
    except Exception:
        logger.warning(
            "terminal worktree shared process scanner failed closed",
            exc_info=True,
        )
        return "process-probe-uncertain"
    if result.state == "clear_observation":
        return None
    if result.state == "blocked":
        return "live-process-reference"
    return "process-probe-uncertain"


_ROOT_WORKTREE_OWNER_EVENTS: ContextVar = ContextVar('root_worktree_owner_events', default=None)


class _StartupSweepUnavailable(RuntimeError):
    """An observed mandatory outcome was negative or remains unsettled."""


class _StartupSweepOutcome(list[str]):
    """List-compatible tokens with the positively consumed native outcome.

    Existing token callers retain their public shape. The recovery coordinator
    can consume disposition/counters directly, without reconstructing success
    from tokens or running another DB scan. Failed sweeps rethrow their cause.
    """

    def __init__(self, tokens, disposition: str, counters: dict[str, int]):
        super().__init__(tokens)
        self.tokens = tuple(self)
        self.disposition = disposition
        self.counters = dict(counters)
        self.first_cause = None


class _StartupSweepOwner:
    """One cold body, retained native tails and ONE final cleanup decision.

    Admission tokens serialize with failure/stop; their short mutex never
    spans DB work, callbacks or native waits. Already admitted effects retain
    their actual history. Only the body thread drains and closes this owner.
    """

    def __init__(self, orch, transitions):
        self.orch = orch
        self.delivery = dict(orch=orch, events=transitions, active=True,
                             teardown_incomplete=False, startup=self)
        self._lock = threading.RLock()
        self._body_thread = threading.get_ident()
        self._body_open = True
        self._sealed = False
        self._boundary_complete = False
        self._batch_attempted = False
        self.batch_result = None
        self._tails: dict[threading.Thread, Future] = {}
        self._humans: dict[int, Any] = {}
        self._first: BaseException | None = None
        self._first_chain: BaseException | None = None
        self._secondary: list[BaseException] = []
        self.counts = dict(tails_started=0, tails_settled=0, effects_started=0,
                           effects_settled=0, human_started=0, human_done=0,
                           negative=0, local_hold=0, secondary_overflow=0)

    def fail(self, cause: BaseException) -> None:
        unsafe = cause if isinstance(cause, _V2StartupConnectionUnsafe) else None
        if unsafe is not None:
            cause = next((original for original in unsafe.causes
                          if not isinstance(original, Exception)), cause)
        with self._lock:
            self.delivery['teardown_incomplete'] = True
            if self._first is None:
                self._first = cause
                self._first_chain = unsafe if unsafe is not cause else None
            elif self._first is not cause:
                if len(self._secondary) < 8:
                    self._secondary.append(cause)
                else:
                    self.counts['secondary_overflow'] += 1

    def negative(self, code: str) -> None:
        with self._lock:
            self.counts['negative'] += 1
        self.fail(_StartupSweepUnavailable(code))

    def observe_gate(self) -> None:
        gate = getattr(self.orch, '_startup_recovery', None)
        if gate is not None:
            snapshot = gate.snapshot()
            if snapshot['stopping'] or snapshot['state'] != 'recovering':
                self.negative('startup_sweep_admission_closed')

    def check(self) -> None:
        with self._lock:
            self.observe_gate()
            if self._first is not None:
                raise _StartupSweepUnavailable('startup_sweep_unsettled') from self._first
            if self._sealed:
                self.negative('startup_sweep_sealed')
                raise _StartupSweepUnavailable('startup_sweep_sealed')

    def admission_open(self) -> bool:
        with self._lock:
            try:
                self.observe_gate()
            except BaseException as exc:
                self.fail(exc)
            return self._first is None and not self._sealed

    @contextmanager
    def effect(self):
        with self._lock:
            self.check()
            if not self._body_open and threading.current_thread() not in self._tails:
                self.negative('startup_sweep_effect_closed')
                raise _StartupSweepUnavailable('startup_sweep_effect_closed')
            self.counts['effects_started'] += 1
        try:
            yield
        except BaseException as exc:
            self.fail(exc)
            raise
        finally:
            with self._lock:
                self.counts['effects_settled'] += 1

    def local_hold(self) -> None:
        with self._lock:
            self.counts['local_hold'] += 1

    def human_finished(self, disposition: str) -> None:
        # The Future's positive result is consumed separately, after the cold
        # caller and its original bookkeeping have actually returned.
        if disposition != 'done':
            self.negative('startup_human_negative_outcome')

    def _consume_humans(self) -> None:
        for key, operation in tuple(self._humans.items()):
            if not operation.completion.done():
                continue
            try:
                result = operation.completion.result()
                if (result != 'done' or operation.disposition != 'done'
                        or operation.phase != 'caller_bookkeeping'
                        or operation.bookkeeping or operation.drain is not None):
                    self.negative('startup_human_unsettled')
                else:
                    self.counts['human_done'] += 1
            except BaseException as exc:
                self.fail(exc)
            self._humans.pop(key)

    def _retire_ended(self) -> None:
        # Called under the mutex: Future.done alone never frees a native slot.
        for ended, completed in tuple(self._tails.items()):
            if ended.ident is not None and not ended.is_alive():
                self._tails.pop(ended)
                self._consume(completed)

    def register_human(self, operation) -> None:
        with self._lock:
            self.check()
            self._retire_ended()
            self._consume_humans()
            if self._first is not None:
                raise _StartupSweepUnavailable('startup_human_prior_failure') from self._first
            if (threading.get_ident() != self._body_thread or not self._body_open
                    or len(self._humans) + len(self._tails) >= 16):
                self.negative('startup_human_registration_closed')
                raise _StartupSweepUnavailable('startup_human_registration_closed')
            self._humans[id(operation)] = operation
            self.counts['human_started'] += 1

    def merge(self, transitions, *, teardown_incomplete=False):
        with self._lock:
            if self._sealed or not self.delivery['active']:
                self.negative('startup_transition_after_seal')
                raise _StartupSweepUnavailable('startup_transition_after_seal')
            if transitions is not self.delivery['events']:
                self.delivery['events'].extend(transitions)
            if teardown_incomplete:
                self.negative('startup_tail_teardown_incomplete')
            self._check_capture()
        return _RootWorktreeBatchDisposition('deferred', 'outer_owner_retained')

    def _check_capture(self) -> None:
        transitions = self.delivery['events']
        if (getattr(transitions, 'unexamined', ()) or getattr(transitions, 'overflow_count', 0)
                or getattr(transitions, 'capture_seconds', 0.0) > 0.5):
            self.negative('startup_transition_capture_incomplete')

    def start_tail(self, operation: Callable[[], Any]) -> threading.Thread:
        """Reserve BEFORE Thread.start; Future.done is not Thread exit."""
        future = Future()

        def run() -> None:
            token = None
            cause = None
            try:
                token = _ROOT_WORKTREE_OWNER_EVENTS.set(self.delivery)
                operation()
            except BaseException as exc:
                cause = exc
                self.fail(exc)
            finally:
                if token is not None:
                    try:
                        _ROOT_WORKTREE_OWNER_EVENTS.reset(token)
                    except BaseException as exc:
                        self.fail(exc)
                        if cause is None:
                            cause = exc
                if cause is None:
                    future.set_result(None)
                else:
                    future.set_exception(cause)

        thread = threading.Thread(target=run, name='startup-cleanup-tail', daemon=True)
        with self._lock:
            self.check()
            retained_caller = threading.current_thread() in self._tails
            if not self._body_open and not retained_caller:
                self.negative('startup_tail_registration_closed')
                raise _StartupSweepUnavailable('startup_tail_registration_closed')
            self._retire_ended()
            if threading.get_ident() == self._body_thread:
                self._consume_humans()
            self.check()
            if len(self._tails) + len(self._humans) >= 16:
                self.negative('startup_tail_capacity')
                raise _StartupSweepUnavailable('startup_tail_capacity')
            self._tails[thread] = future
            self.counts['tails_started'] += 1
            try:
                thread.start()
            except BaseException as exc:
                # Even an interrupted start before ident publication can have
                # native effects. Retain the reservation; join must prove exit.
                self.fail(exc)
                raise
        return thread

    def _consume(self, future: Future) -> None:
        if not future.done():
            self.negative('startup_tail_outcome_missing')
            return
        try:
            if future.result() is not None:
                self.negative('startup_tail_outcome_unknown')
        except BaseException as exc:
            self.fail(exc)
        self.counts['tails_settled'] += 1

    def drain(self) -> None:
        """Close body admissions, then join outside all DB/publisher locks."""
        with self._lock:
            self._body_open = False
        if threading.get_ident() != self._body_thread:
            raise RuntimeError('startup_sweep_wrong_drain_owner')
        db = getattr(self.orch, '_db', None)
        if db is not None and db._lock._is_owned():
            raise RuntimeError('startup_sweep_drain_under_database_lock')
        org = getattr(getattr(self.orch, '_workflow_drafts', None), 'org', None)
        if org is not None and org.workflow_authority._publisher_lock._is_owned():
            raise RuntimeError('startup_sweep_drain_under_publisher_lock')
        failed_joins = set()
        while True:
            with self._lock:
                tails = tuple((thread, future) for thread, future in self._tails.items()
                              if thread not in failed_joins)
                if not tails:
                    self._consume_humans()
                    if (not self._tails and not self._humans
                            and self.counts['effects_started'] == self.counts['effects_settled']):
                        self._sealed = True
                    else:
                        self.negative('startup_native_owners_incomplete')
                    return
            for thread, future in tails:
                try:
                    thread.join()
                    if thread.is_alive():
                        raise RuntimeError('startup_tail_join_incomplete')
                except BaseException as exc:
                    self.fail(exc)
                    failed_joins.add(thread)
                    continue
                with self._lock:
                    if self._tails.pop(thread, None) is not None:
                        self._consume(future)

    def finalize(self, token) -> None:
        """Retain this owner through drain, reset and the real batch result."""
        try:
            self.observe_gate()
        except BaseException as exc:
            self.fail(exc)
        try:
            self.drain()
            with self._lock:
                self._check_capture()
                if self.delivery['teardown_incomplete'] and self._first is None:
                    self.negative('startup_sweep_teardown_incomplete')
            self.observe_gate()
        except BaseException as exc:
            self.fail(exc)
        # Unjoined tails keep their explicitly installed context active. They
        # may still merge captures, but sticky failure denies publications.
        if self._sealed:
            self.delivery['active'] = False
        reset = False
        try:
            _ROOT_WORKTREE_OWNER_EVENTS.reset(token)
            reset = True
        except BaseException as exc:
            self.fail(exc)
        # No batch function at all on suppression: even its report/DB path
        # would be an effect. Seal and reset failures also retain the owner.
        with self._lock:
            attempt = self._first is None and self._sealed and reset
            if attempt:
                if self._batch_attempted:
                    self.negative('startup_final_batch_duplicate')
                    attempt = False
        if attempt:
            try:
                # Same short admission rule as tail publications. Stop may
                # close NEW effects; an already admitted batch remains history.
                with self._lock:
                    self.observe_gate()
                    admitted = self._first is None
                    if admitted:
                        self._batch_attempted = True
                if admitted:
                    self.batch_result = _root_worktree_batch(self.orch, self.delivery['events'])
                    if getattr(self.batch_result, 'kind', None) != 'safe':
                        self.negative('startup_final_batch_unsettled')
            except BaseException as exc:
                self.fail(exc)
        with self._lock:
            self._boundary_complete = (self._sealed and reset
                and (not self._batch_attempted
                     or getattr(self.batch_result, 'kind', None) == 'safe'))

    def outcome(self, tokens) -> _StartupSweepOutcome:
        self.raise_if_failed()
        with self._lock:
            if (not self._boundary_complete or not self._batch_attempted
                    or getattr(self.batch_result, 'kind', None) != 'safe'):
                raise _StartupSweepUnavailable('startup_sweep_outcome_incomplete')
            disposition = 'local_hold' if self.counts['local_hold'] else 'settled'
            return _StartupSweepOutcome(tokens, disposition, self.counts)

    def raise_if_failed(self) -> None:
        with self._lock:
            cause = self._first
            secondary = tuple(self._secondary)
        if cause is not None:
            for failure in secondary:
                try:
                    cause.add_note('Secondary startup failure: ' + type(failure).__name__)
                except BaseException:
                    pass
            if self._first_chain is not None:
                raise cause from self._first_chain
            raise cause


def _startup_sweep_owner(orch):
    delivery = _ROOT_WORKTREE_OWNER_EVENTS.get()
    if delivery is not None and delivery['orch'] is orch and delivery['active']:
        return delivery.get('startup')
    return None


@contextmanager
def _startup_sweep_effect(orch):
    owner = _startup_sweep_owner(orch)
    if owner is None:
        yield
    else:
        with owner.effect():
            yield


class _RootWorktreeTransitions(list):
    """Bounded owner manifest; overflow is an explicit audit-window frontier."""
    def __init__(self):
        super().__init__()
        self.capture_seconds = 0.0
        self.unexamined = []
        self.overflow_count = 0
        self.audit_low = None
        self.audit_high = None

    def __bool__(self):
        return len(self) > 0 or bool(self.unexamined) or self.overflow_count > 0

    def append(self, event):
        if len(self) < 256:
            super().append(event)
        else:
            self.overflow_count += 1

    def extend(self, events):
        self.capture_seconds += getattr(events, 'capture_seconds', 0.0)
        for event in events:
            self.append(event)
        missing = getattr(events, 'unexamined', [])
        room = max(0, 256-len(self.unexamined))
        self.unexamined.extend(missing[:room])
        self.overflow_count += max(0, len(missing)-room)
        self.overflow_count += getattr(events, 'overflow_count', 0)
        low, high = getattr(events, 'audit_low', None), getattr(events, 'audit_high', None)
        if low is not None:
            self.audit_low = low if self.audit_low is None else min(low, self.audit_low)
        if high is not None:
            self.audit_high = high if self.audit_high is None else max(high, self.audit_high)


@contextmanager
def _defer_root_worktree_batches(orch, transitions):
    delivery = dict(orch=orch, events=transitions, active=True, teardown_incomplete=False)
    token = _ROOT_WORKTREE_OWNER_EVENTS.set(delivery)
    try:
        yield delivery
    finally:
        delivery['active'] = False
        _ROOT_WORKTREE_OWNER_EVENTS.reset(token)


@dataclass
class _RootWorktreeTransition:
    before: dict
    after: dict
    writer: str
    attempted: bool = False

    def provenance(self) -> dict:
        return dict(before=self.before, after=self.after, writer=self.writer)


@contextmanager
def _capture_root_worktree_transition(orch, task_id, writer, transitions):
    """Capture only the ORIGINAL committed owner, never a later notice.

    The terminal writer still runs if capture admission fails. Capturing adds
    no lifecycle semantics. Callers place their root tail outside all writer
    locks, after their existing terminal/history/teardown effects.
    """
    if orch is None:
        yield
        return
    db = orch._db
    startup = _startup_sweep_owner(orch)
    capture_lock = startup._lock if startup is not None else nullcontext()
    with capture_lock:
        allowance = max(0.0, min(0.05, 0.5-getattr(transitions, 'capture_seconds', 0.0)))
    capture_started = time.monotonic()
    acquired = allowance > 0 and db._lock.acquire(timeout=allowance)
    before = None
    capture_unavailable = not acquired
    before_seconds = 0.0
    try:
        if acquired:
            try:
                before = db.terminal_worktree_task_projection(task_id)
                capture_unavailable |= before is None
                if isinstance(transitions, _RootWorktreeTransitions):
                    anchor = db.terminal_worktree_audit_high_watermark()
                    if type(anchor) is not int:
                        raise ValueError('capture_audit_anchor_unavailable')
                    with capture_lock:
                        if transitions.audit_low is None:
                            transitions.audit_low = anchor
                        transitions.audit_high = anchor
            except Exception:
                capture_unavailable = True
                if isinstance(transitions, _RootWorktreeTransitions):
                    with capture_lock:
                        transitions.audit_high = None
                logger.exception('root worktree %s before projection unavailable', task_id)
        before_seconds = time.monotonic()-capture_started
        yield
    finally:
        after_started = time.monotonic()
        try:
            if acquired and before is not None and before['parent_task_id'] is None:
                after = db.terminal_worktree_task_projection(task_id)
                capture_unavailable |= after is None
                if (after is not None and before['status'] not in {s.value for s in TERMINAL_STATES}
                        and after['status'] in {s.value for s in TERMINAL_STATES}):
                    with capture_lock:
                        transitions.append(_RootWorktreeTransition(before, after, writer))
                if isinstance(transitions, _RootWorktreeTransitions):
                    anchor = db.terminal_worktree_audit_high_watermark()
                    if type(anchor) is not int:
                        raise ValueError('capture_audit_anchor_unavailable')
                    with capture_lock:
                        transitions.audit_high = anchor
        except Exception:
            capture_unavailable = True
            if isinstance(transitions, _RootWorktreeTransitions):
                with capture_lock:
                    transitions.audit_high = None
            logger.exception('root worktree %s transition capture unavailable', task_id)
        finally:
            with capture_lock:
                if isinstance(transitions, _RootWorktreeTransitions):
                    transitions.capture_seconds += before_seconds+time.monotonic()-after_started
                    if capture_unavailable:
                        if len(transitions.unexamined) < 256:
                            transitions.unexamined.append(dict(task_id=task_id, writer=writer, reason='transition_capture_unavailable',
                                audit_low=transitions.audit_low, audit_high=transitions.audit_high))
                        else:
                            transitions.overflow_count += 1
            if acquired:
                db._lock.release()
            if startup is not None:
                with startup._lock:
                    if (capture_unavailable or getattr(transitions, 'overflow_count', 0)
                            or getattr(transitions, 'capture_seconds', 0.0) > 0.5):
                        startup.negative('startup_transition_capture_incomplete')
            if not acquired:
                logger.warning('root worktree %s transition capture lock unavailable; no new admission', task_id)


@dataclass
class _RootWorktreeBudget:
    started: float = field(default_factory=time.monotonic)
    db_seconds: float = 0.0
    report_seconds: float = 0.0
    reap_seconds: float = 0.0
    nodes: int = 0
    attempts: int = 0
    admissions: int = 0
    halted: bool = False
    processes: list = field(default_factory=list)

    @property
    def deadline(self):
        return self.started + 10.0

    def remaining(self):
        if self.halted:
            raise RuntimeError('owned_probe_or_action_unsettled')
        return _terminal_worktree_remaining(self.deadline)

    def database(self, db, operation, *, report=False):
        allowance = (1.0-self.report_seconds if report else
                     min(0.5-self.db_seconds, self.remaining()))
        if allowance <= 0:
            raise TimeoutError('report_budget' if report else 'db_budget')
        started = time.monotonic()
        acquired = db._lock.acquire(timeout=min(0.05, allowance))
        try:
            if not acquired:
                raise TimeoutError('db_lock_admission')
            result = operation()
        finally:
            if acquired:
                db._lock.release()
            elapsed = time.monotonic()-started
            if report:
                self.report_seconds += elapsed
            else:
                self.db_seconds += elapsed
        # Synchronous SQLite/fsync is nonpreemptible. A late return admits no
        # further work. A report commit may exist despite a reported overrun.
        if elapsed > allowance:
            self.halted = True
            raise TimeoutError('budget_overrun')
        return result

    def command(self, args, cwd, deadline):
        """Owned group, capped streaming capture, cumulative one-second reap."""
        import selectors
        import signal
        self.remaining()
        if self.reap_seconds >= 1.0:
            self.halted = True
            raise TimeoutError('reap_reserve_exhausted')
        process = subprocess.Popen(args, cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        facts = dict(pid=process.pid, argv=args, action_started=(args[:3] == ['git', 'worktree', 'remove']), reaped=False)
        self.processes.append(facts)
        selector = selectors.DefaultSelector()
        output = [bytearray(), bytearray()]
        for index, stream in enumerate((process.stdout, process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, index)
        try:
            while selector.get_map():
                _terminal_worktree_remaining(min(deadline, self.deadline))
                for key, _ in selector.select(min(0.05, max(0, min(deadline, self.deadline)-time.monotonic()))):
                    chunk = os.read(key.fd, 65536)
                    if chunk:
                        output[key.data].extend(chunk)
                        if sum(map(len, output)) > 1048576:
                            raise ValueError('subprocess_output_cap')
                    else:
                        selector.unregister(key.fileobj)
            code = process.wait(timeout=max(0.001, min(deadline, self.deadline)-time.monotonic()))
            facts['returncode'] = code
            return subprocess.CompletedProcess(args, code, output[0].decode(), output[1].decode())
        finally:
            selector.close()
            started = time.monotonic()
            # Kill the owned group even when its leader exited but descendants
            # retained a pipe. No next mutation until group exit is observed.
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                group_live = False
            else:
                group_live = True
            if group_live:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    if process.poll() is None:
                        process.wait(timeout=min(0.25, max(0.001, 1.0-self.reap_seconds)))
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            try:
                process.wait(timeout=min(0.5, max(0.001, 1.0-self.reap_seconds-(time.monotonic()-started))))
                facts['reaped'] = True
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    facts['group_exited'] = True
                else:
                    facts['group_exited'] = False
                    self.halted = True
            except subprocess.TimeoutExpired:
                self.halted = True
            self.reap_seconds += time.monotonic()-started
            if self.reap_seconds > 1.0:
                self.halted = True
            process.stdout.close()
            process.stderr.close()
            # Retain action/uncertainty facts; settled probes do not grow the
            # aggregate report without bound. Group exit was observed above.
            if not facts['action_started'] and facts['reaped'] and facts.get('group_exited'):
                self.processes.remove(facts)


def _root_worktree_membership(orch, root, budget):
    """FIFO parent-only closure; retain the last accepted cursor on refusal."""
    members = {root['id']: root}
    if budget.nodes >= 256:
        return {}, [dict(parent=root['id'], cursor=None, depth=0)], 1, False, 'membership_node_cap'
    budget.nodes += 1
    frontier = [dict(parent=root['id'], cursor=None, depth=0)]
    lower = 1
    try:
        while frontier:
            budget.remaining()
            edge = frontier[0]
            rows = budget.database(orch._db, lambda: orch._db.terminal_worktree_children_page(
                edge['parent'], None if edge['cursor'] is None else tuple(edge['cursor'])))
            page = rows[:64]
            # Validate the entire admitted page before advancing the cursor.
            seen = set(members)
            for row in page:
                if (row['parent_task_id'] != edge['parent'] or row['id'] in seen
                        or not isinstance(row['created_at'], str)):
                    raise ValueError('membership_parent_or_cycle')
                seen.add(row['id'])
            if page and edge['depth'] >= 64:
                edge['overflow_child_id'] = page[0]['id']; lower = len(members)+1
                raise ValueError('membership_depth_cap')
            if budget.nodes+len(page) > 256:
                room = max(0, 256-budget.nodes)
                lower = len(members)+len(page)
                edge['overflow_child_id'] = page[room]['id']
                edge['partially_admitted_page_ids'] = [row['id'] for row in page[:room]]
                for row in page[:room]:
                    members[row['id']] = row
                    frontier.append(dict(parent=row['id'], cursor=None, depth=edge['depth']+1))
                budget.nodes += room
                raise ValueError('membership_node_cap')
            for row in page:
                members[row['id']] = row
                frontier.append(dict(parent=row['id'], cursor=None, depth=edge['depth']+1))
            budget.nodes += len(page)
            lower = len(members)
            if len(rows) == 65:
                edge['cursor'] = [page[-1]['created_at'], page[-1]['id']]
            else:
                frontier.pop(0)
        # Rewalk with the same paged SELECT owner, without increasing the
        # admission count. A new edge or changed owner invalidates closure.
        observed = {}
        for parent in members:
            cursor = None
            while True:
                rows = budget.database(orch._db, lambda: orch._db.terminal_worktree_children_page(parent, cursor))
                for row in rows[:64]:
                    if row['id'] not in members or row != members[row['id']] or row['id'] in observed:
                        raise ValueError('membership_changed')
                    observed[row['id']] = row
                if len(rows) < 65:
                    break
                cursor = (rows[63]['created_at'], rows[63]['id'])
        if set(observed) != set(members)-{root['id']}:
            raise ValueError('membership_changed')
        return members, [], lower, True, None
    except Exception as exc:
        return members, frontier, lower, False, str(exc)[:160]


def _root_worktree_revalidate_membership(orch, members, budget):
    observed = set()
    for parent in members:
        cursor = None
        while True:
            rows = budget.database(orch._db, lambda: orch._db.terminal_worktree_children_page(parent, cursor))
            for row in rows[:64]:
                if row['id'] not in members or row != members[row['id']] or row['id'] in observed:
                    raise ValueError('action_membership_changed')
                observed.add(row['id'])
            if len(rows) < 65:
                break
            cursor = (rows[63]['created_at'], rows[63]['id'])
    if observed != set(members)-{next(iter(members))}:
        raise ValueError('action_membership_changed')


def _root_worktree_protection(orch, members, budget):
    projections = {}
    reasons = {}
    sessions = getattr(orch, '_sessions', None)
    if sessions is None:
        raise ValueError('session_tracker_unavailable')
    for tid, task in members.items():
        current, projection = budget.database(orch._db, lambda: (
            orch._db.terminal_worktree_task_projection(tid), orch._db.terminal_worktree_protection(tid)))
        if current != task:
            raise ValueError('task_projection_changed')
        projections[tid] = projection
        if task['status'] not in {s.value for s in TERMINAL_STATES}:
            reasons[tid] = 'owner_nonterminal'
        if (any(t == tid for t, _a, _s in sessions.iter_active())
                or sessions.iter_task_cancel_controls(tid) or sessions.iter_task_pids(tid)):
            reasons[tid] = 'live_session_control_or_pid'
        for job in projection['jobs']:
            if job['status'] in ('pending', 'running'):
                reasons[tid] = 'pending_or_running_job'
            elif job['status'] not in ('completed', 'failed', 'cancelled', 'rejected'):
                raise ValueError('job_state_unknown')
            elif job['status'] != 'rejected' and job['finished_at'] is None:
                raise ValueError('job_terminal_evidence_missing')
        for recovery in projection['recoveries']:
            if recovery['state'] in ('claimed', 'callback_accepted') or recovery['settled_at'] is None:
                raise ValueError('original_recovery_unsettled')
        for intent in projection['intents']:
            if intent['state'] not in ('completed', 'failed', 'cancelled'):
                raise ValueError('workflow_host_ownership_unsettled')
            if intent['host_launch_started']:
                # A status label cannot replace authenticated native closure.
                # The bounded projection has no complete historical host proof.
                raise ValueError('workflow_native_closure_unavailable')
        # Unknown predecessor-path reuse conservatively protects its entire
        # parent tree. Related rows are never added to candidate membership.
        if projection['successors']:
            for identity in members:
                reasons[identity] = 'successor_use_unresolved'
    store = getattr(orch._db, '_task_pause_store', None)
    if store is not None:
        root_id = next(iter(members))
        row = budget.database(orch._db, lambda: store.row_uncommitted(root_id))
        if row:
            if row['held']:
                reasons.update({tid: 'stored_hold' for tid in members})
            for entry in row['journal']['entries']:
                if entry['task_id'] not in members or entry['context'].get('execution_unknown') or entry['phase'] == 'unknown':
                    raise ValueError('host_tree_ownership_unavailable')
                if entry['phase'] not in ('prepared', 'pending_job', 'retry_deferred', 'recovery_deferred'):
                    reasons[entry['task_id']] = 'host_or_result_owner_unsettled'
        # Session/control records and the durable journal together retain
        # native ownership; clearing a tracker alone never closes the journal.
    return projections, reasons


def _root_worktree_registration(run, primary, candidate, task_id):
    listed = run(['git', 'worktree', 'list', '--porcelain'], primary)
    if listed.returncode:
        raise ValueError('registration_unavailable')
    records = []
    for raw in listed.stdout.strip().split('\n\n'):
        fields = {}
        for line in raw.splitlines():
            key, _, value = line.partition(' ')
            if key in fields:
                raise ValueError('registration_ambiguous')
            fields[key] = value
        if fields.get('worktree') == str(candidate):
            records.append(fields)
    if not records:
        return None
    if len(records) != 1 or records[0].get('branch') != 'refs/heads/task/'+task_id or 'locked' in records[0] or 'prunable' in records[0]:
        raise ValueError('registration_identity')
    return records[0]


def _root_worktree_reconcile(orch, transition, event_id, budget, *, teardown_incomplete=False, receipt_allowance=262144):
    root = transition.after
    receipt = dict(event_id=event_id, root_created_at=root['created_at'],
                   membership_complete=False, protection_complete=False, complete=False,
                   task_ids=[], candidate_ids=[], outcomes=[], frontier=[],
                   admitted_unexamined_ids=[], remaining_candidate_ids=[], unknown_total=None)
    members, frontier, lower, complete, error = _root_worktree_membership(orch, root, budget)
    receipt.update(task_ids=list(members), frontier=frontier, known_lower_bound=lower,
                   membership_complete=complete, reason=error)
    candidates = []
    noops = []
    # Discover every registered candidate before action, or refuse closure.
    # Filesystem shape and Git registration are also repeated at action time.
    run = lambda args, cwd: budget.command(args, cwd, budget.deadline)
    try:
        if not complete:
            raise ValueError(error or 'membership_incomplete')
        protections, retained = _root_worktree_protection(orch, members, budget)
        receipt['protection_complete'] = True
        if teardown_incomplete:
            raise ValueError('terminal_teardown_incomplete')
        for tid, task in members.items():
            budget.remaining()
            agent = task['assigned_agent']
            if not agent or agent not in orch.teams.all_agents():
                raise ValueError('assigned_owner_unregistered')
            workspace = orch._paths.workspaces_dir / agent
            primary = workspace / 'repos/happyranch'
            candidate = primary / '.claude/worktrees' / tid
            if (not primary.is_absolute() or primary.resolve() != primary or candidate.resolve() != candidate
                    or candidate.parent != primary / '.claude/worktrees' or candidate.name != tid):
                raise ValueError('path_noncanonical')
            if not primary.exists():
                noops.append(dict(task_id=tid, kind='noop', reason='primary_absent')); continue
            registration = _root_worktree_registration(run, primary, candidate, tid)
            if registration is None:
                noops.append(dict(task_id=tid, kind='noop', reason='unregistered' if candidate.exists() else 'absent')); continue
            candidates.append(dict(task_id=tid, path=str(candidate), primary=str(primary), workspace=str(workspace)))
        receipt['candidate_ids'] = [item['task_id'] for item in candidates]
        receipt['candidates'] = candidates
        receipt['noops'] = noops
        receipt['remaining_candidate_ids'] = list(receipt['candidate_ids'])
        # Capacity is checked with all candidate and outcome space reserved.
        worst = dict(receipt, outcomes=[dict(item, kind='action_anomaly', reason='x'*200, action_started=True,
                     measurement_error='x'*200, apparent_bytes_after=None, allocated_bytes_after=None) for item in candidates],
                     processes=budget.processes + [dict(pid=999999999999999999,
                         argv=['git', 'worktree', 'remove', item['path']],
                         action_started=True, reaped=True, group_exited=True, returncode=-999)
                         for item in candidates[:max(0, 16-budget.attempts)]])
        if len(json.dumps(worst).encode()) + 8192 > receipt_allowance:
            raise ValueError('receipt_capacity')
        if receipt['protection_complete'] is not True:
            raise ValueError('protection_incomplete')
        import tempfile
        with tempfile.TemporaryDirectory(prefix='root-worktree-') as scratch:
            for item in candidates:
                if budget.attempts >= 16 or budget.halted or time.monotonic() >= budget.deadline:
                    break
                budget.attempts += 1
                tid = item['task_id']; candidate = Path(item['path']); primary = Path(item['primary'])
                candidate_deadline = min(budget.deadline, time.monotonic()+5.0)
                run = lambda args, cwd: budget.command(args, cwd, candidate_deadline)
                action_started = False
                snapshot = str(Path(scratch) / (tid+'.json'))
                outcome = dict(task_id=tid, path=str(candidate), kind='retained', action_started=False)
                try:
                    if tid in retained:
                        outcome['reason'] = retained[tid]
                        continue
                    _root_worktree_revalidate_membership(orch, members, budget)
                    before_protection, before_retained = _root_worktree_protection(orch, members, budget)
                    if before_protection != protections or before_retained != retained:
                        raise ValueError('protection_changed')
                    head = None
                    for boundary in range(2):
                        budget.remaining()
                        if _terminal_worktree_path_has_symlink(candidate) or _terminal_worktree_path_has_symlink(primary):
                            raise ValueError('symlink_component')
                        if primary.stat().st_uid != os.getuid() or candidate.stat().st_uid != os.getuid() or primary.stat().st_dev != candidate.stat().st_dev:
                            raise ValueError('ownership_or_device')
                        top = run(['git', 'rev-parse', '--show-toplevel'], primary)
                        if top.returncode or top.stdout.strip() != str(primary):
                            raise ValueError('primary_identity')
                        registration = _root_worktree_registration(run, primary, candidate, tid)
                        if registration is None:
                            raise ValueError('registration_changed')
                        common = run(['git', 'rev-parse', '--git-common-dir'], candidate)
                        common_primary = run(['git', 'rev-parse', '--git-common-dir'], primary)
                        if common.returncode or common_primary.returncode:
                            raise ValueError('common_dir_unavailable')
                        def resolved(value, base):
                            return (base / value.strip()).resolve()
                        if resolved(common.stdout, candidate) != resolved(common_primary.stdout, primary):
                            raise ValueError('common_dir_mismatch')
                        clean = run(['git', 'status', '--porcelain=v1', '--untracked-files=all'], candidate)
                        if clean.returncode or clean.stdout:
                            raise ValueError('dirty_or_status_unknown')
                        preserved = run([sys.executable, str(_TERMINAL_WORKTREE_SCANNER_PATH.with_name('worktree_safety.py')),
                                         '--timeout', str(max(0.001, candidate_deadline-time.monotonic())),
                                         'preserved', str(candidate), str(primary), tid], primary)
                        if preserved.returncode:
                            raise ValueError('preservation_refused:'+preserved.stdout[:160])
                        value = json.loads(preserved.stdout)
                        if head is not None and value['head'] != head:
                            raise ValueError('head_changed')
                        head = value['head']; outcome['head'] = head
                        scanned = run([sys.executable, str(_TERMINAL_WORKTREE_SCANNER_PATH), '--target', str(candidate),
                                       '--containing-worktree', str(candidate), '--json', '--deadline-seconds',
                                       str(max(0.001, candidate_deadline-time.monotonic()))], primary)
                        if scanned.returncode or json.loads(scanned.stdout).get('state') != 'clear_observation':
                            raise ValueError('process_use_blocked_or_unknown')
                        safe = run([sys.executable, str(_TERMINAL_WORKTREE_SCANNER_PATH.with_name('worktree_safety.py')),
                                    '--timeout', str(max(0.001, candidate_deadline-time.monotonic())), 'snapshot',
                                    str(candidate), str(candidate), str(primary), item['workspace'], snapshot], primary)
                        if safe.returncode:
                            raise ValueError('recursive_boundary_refused')
                        if boundary == 0:
                            _root_worktree_revalidate_membership(orch, members, budget)
                    latest, latest_retained = _root_worktree_protection(orch, members, budget)
                    if latest != protections or latest_retained != retained:
                        raise ValueError('action_protection_changed')
                    # Action starts only after all reservations and evidence.
                    budget.remaining(); _terminal_worktree_remaining(candidate_deadline)
                    action_started = True; outcome['action_started'] = True
                    removed = run(['git', 'worktree', 'remove', str(candidate)], primary)
                    if removed.returncode:
                        raise ValueError('remove_nonzero')
                    post = run([sys.executable, str(_TERMINAL_WORKTREE_SCANNER_PATH.with_name('worktree_safety.py')),
                                'post-action', str(candidate), snapshot, '', ''], primary)
                    branch = run(['git', 'rev-parse', '--verify', 'refs/heads/task/'+tid], primary)
                    if (post.returncode or _root_worktree_registration(run, primary, candidate, tid) is not None
                            or branch.returncode or branch.stdout.strip() != head):
                        raise ValueError('post_action_identity_registration_or_branch')
                    outcome.update(kind='removed', reason='verified_absent_unregistered',
                                   apparent_bytes_after=0, allocated_bytes_after=0)
                except Exception as exc:
                    outcome.update(kind='action_anomaly' if action_started else 'retained', reason=str(exc)[:200])
                    if action_started or budget.halted:
                        budget.halted = True
                        try:
                            absent = not os.path.lexists(candidate)
                            outcome.update(apparent_bytes_after=0 if absent else None,
                                allocated_bytes_after=0 if absent else None,
                                measurement_error=None if absent else 'recursive_residual_measurement_unavailable')
                        except OSError as measurement_error:
                            outcome.update(apparent_bytes_after=None, allocated_bytes_after=None,
                                measurement_error=type(measurement_error).__name__)
                finally:
                    receipt['outcomes'].append(outcome)
                    receipt['remaining_candidate_ids'].remove(tid)
                if budget.halted:
                    break
    except Exception as exc:
        receipt['reason'] = str(exc)[:200]
        receipt['protection_complete'] = False
        receipt['admitted_unexamined_ids'] = list(members)
    receipt['noops'] = noops
    receipt['candidate_ids'] = [item['task_id'] for item in candidates]
    accounted = {outcome['task_id'] for outcome in receipt['outcomes']}
    receipt['remaining_candidate_ids'] = [item['task_id'] for item in candidates if item['task_id'] not in accounted]
    receipt['candidate_total'] = len(candidates) if receipt['membership_complete'] and receipt['protection_complete'] else None
    receipt['remaining_total'] = len(receipt['remaining_candidate_ids']) if receipt['candidate_total'] is not None else None
    receipt['counts'] = {kind: sum(row['kind'] == kind for row in receipt['outcomes']) for kind in ('removed', 'retained', 'action_anomaly')}
    receipt['counts']['unexamined'] = len(receipt['remaining_candidate_ids'])
    receipt['complete'] = (receipt['membership_complete'] and receipt['protection_complete'] and not receipt['frontier']
                           and not receipt['remaining_candidate_ids'] and not receipt['admitted_unexamined_ids']
                           and not receipt['counts']['action_anomaly'] and not budget.halted)
    receipt['unknown_total'] = len(members) if receipt['membership_complete'] else None
    return receipt


@dataclass(frozen=True)
class _RootWorktreeBatchDisposition:
    """Actual cleanup-owner outcome; preservation is not teardown uncertainty."""
    kind: str
    reason: str
    receipts: tuple[dict, ...] = ()
    processes: tuple[dict, ...] = ()


def _root_worktree_batch_impl(orch, transitions, *, teardown_incomplete=False):
    """One finite, synchronous owner batch; no continuation or replay retry."""
    delivery = _ROOT_WORKTREE_OWNER_EVENTS.get()
    if delivery is not None and delivery['orch'] is orch and delivery['active']:
        startup = delivery.get('startup')
        if startup is not None:
            return startup.merge(transitions, teardown_incomplete=teardown_incomplete)
        else:
            delivery['events'].extend(transitions)
            delivery['teardown_incomplete'] |= teardown_incomplete
        return _RootWorktreeBatchDisposition('deferred', 'outer_owner_retained')
    if orch is None:
        return _RootWorktreeBatchDisposition('unknown', 'cleanup_owner_unavailable')
    capture_missing = getattr(transitions, 'unexamined', [])
    if capture_missing:
        logger.warning('root worktree capture scope unexamined=%s raw_audit_window=(%s,%s] cursor=%s; zero actions; complete=false',
            capture_missing, getattr(transitions, 'audit_low', None),
            getattr(transitions, 'audit_high', None), getattr(transitions, 'audit_low', None))
    if getattr(transitions, 'overflow_count', 0):
        # Close the raw interval after the last original writer's terminal
        # audit tail. An unavailable upper bound remains explicitly unknown.
        try:
            capture_budget = _RootWorktreeBudget(db_seconds=transitions.capture_seconds)
            transitions.audit_high = capture_budget.database(orch._db,
                orch._db.terminal_worktree_audit_high_watermark, report=True)
        except Exception:
            transitions.audit_high = None
        logger.warning('root worktree capture overflow=%s known_roots=%s raw_audit_window=(%s,%s] cursor=%s; zero actions; complete=false',
            transitions.overflow_count, [event.after['id'] for event in transitions],
            transitions.audit_low, transitions.audit_high, transitions.audit_low)
        return _RootWorktreeBatchDisposition('unknown', 'transition_capture_overflow')
    if not transitions:
        return _RootWorktreeBatchDisposition('unknown' if capture_missing or teardown_incomplete else 'safe',
            'transition_capture_or_teardown_unavailable' if capture_missing or teardown_incomplete else 'no_action')
    capture_seconds = getattr(transitions, 'capture_seconds', 0.0)
    budget = _RootWorktreeBudget(started=time.monotonic()-capture_seconds, db_seconds=capture_seconds)
    # Missing protection/capture closure forbids the whole owner batch.
    teardown_incomplete |= bool(capture_missing) or budget.db_seconds > 0.5
    teardown_incomplete |= orch._db._lock._is_owned()
    receipts = []
    unexamined = [event.after['id'] for event in transitions if not event.attempted]
    size = len(json.dumps([event.provenance() for event in transitions]).encode())
    if size > 262144:
        logger.error('root worktree owner transition manifest exceeds receipt cap; all unexamined; '
                     'roots=%s raw_audit_window=(%s,%s] cursor=%s; complete=false; no retry',
                     len(transitions), getattr(transitions, 'audit_low', None),
                     getattr(transitions, 'audit_high', None), getattr(transitions, 'audit_low', None))
        return _RootWorktreeBatchDisposition('unknown', 'transition_manifest_capacity')
    for event in transitions:
        if event.attempted:
            continue
        # Mark before DB admission, including failure/uncertain commit.
        event.attempted = True
        if budget.admissions >= 16 or budget.halted or time.monotonic() >= budget.deadline:
            continue
        budget.admissions += 1
        root = event.after
        event_id = None
        receipt = None
        try:
            disposition, event_id = budget.database(orch._db, lambda: orch._db.admit_terminal_worktree_root(event.provenance()))
            if disposition != 'admitted':
                receipts.append(dict(root_id=root['id'], event_id=event_id, disposition=disposition, complete=False))
                unexamined.remove(root['id'])
                continue
            receipt = _root_worktree_reconcile(orch, event, event_id, budget,
                teardown_incomplete=teardown_incomplete, receipt_allowance=262144-size)
            receipt['root_id'] = root['id']
            receipt['budget'] = dict(work_seconds=time.monotonic()-budget.started, db_seconds=budget.db_seconds,
                                     reap_seconds=budget.reap_seconds, attempts=budget.attempts, nodes=budget.nodes)
            size += len(json.dumps(receipt).encode())
            if size > 262144:
                raise ValueError('batch_receipt_capacity')
            def finish():
                if orch._db._conn.in_transaction:
                    raise ValueError('finish_transaction_owned')
                return orch._db.append_terminal_worktree_audit(root['id'], root['assigned_agent'] or 'orchestrator',
                    'root_worktree_reconciliation_finished', receipt)
            budget.database(orch._db, finish, report=True)
            receipts.append(receipt)
            unexamined.remove(root['id'])
        except Exception:
            budget.halted = True
            logger.exception('root worktree %s cleanup missing/uncertain; event=%s; no retry; halt', root['id'], event_id)
            logger.error('root worktree missing finish facts=%s owned_processes=%s', receipt, budget.processes)
            break
    logger.info('root worktree batch receipts=%s unexamined_root_ids=%s elapsed=%s report=%s reap=%s',
                receipts, unexamined, time.monotonic()-budget.started, budget.report_seconds, budget.reap_seconds)
    unknown = (teardown_incomplete or budget.halted
        or any(not p['reaped'] or not p.get('group_exited') for p in budget.processes)
        or any(r.get('counts', {}).get('action_anomaly') for r in receipts))
    # A bounded refusal/preserved tree admits no further cleanup; it can be
    # safe even with complete=false. Swallowed report/action/reap failures and
    # halted budgets above are distinct, attributable unknown teardown.
    return _RootWorktreeBatchDisposition('unknown' if unknown else 'safe',
        'cleanup_or_report_unsettled' if unknown else 'settled_or_preserved',
        tuple(receipts), tuple(dict(p) for p in budget.processes))


def _root_worktree_batch(orch, transitions, *, teardown_incomplete=False):
    try:
        return _root_worktree_batch_impl(orch, transitions, teardown_incomplete=teardown_incomplete)
    except Exception:
        logger.exception('root worktree owner tail unavailable; terminal state/report preserved; no retry')
        return _RootWorktreeBatchDisposition('unknown', 'cleanup_owner_tail_unavailable')


async def _await_root_worktree_batch(orch, transitions, *, teardown_incomplete=False):
    """Merge cold tails inline; normal callers retain finite off-loop cleanup."""
    startup = _startup_sweep_owner(orch)
    if startup is not None:
        return startup.merge(transitions, teardown_incomplete=teardown_incomplete)
    return await _await_worktree_cleanup(_root_worktree_batch, orch, transitions,
                                        teardown_incomplete=teardown_incomplete)


async def _await_terminal_task_worktree(orch: "Orchestrator", task_id: str) -> TerminalWorktreeReclaimOutcome:
    """Await the existing child hook outside the event loop and owner locks."""
    return await _await_worktree_cleanup(_reclaim_terminal_task_worktree, orch, task_id)


async def _await_worktree_cleanup(operation: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    worker = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(worker)
            break
        except asyncio.CancelledError:
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


def _reclaim_terminal_task_worktree(
    orch: "Orchestrator", task_id: str, *, transition: _RootWorktreeTransition | None = None,
    original_task: TaskRecord | None = None,
    current_owner: Callable[[], bool] | None = None,
) -> TerminalWorktreeReclaimOutcome:
    """Remove one exact clean, durable, quiescent task worktree fail-closed.

    This is a forward-only, one-shot terminal hook.  It never scans for other
    worktrees, retries, deletes a branch, changes task state, signals a process,
    or raises into the terminal transition that called it.
    """
    if current_owner is not None and not current_owner():
        return TerminalWorktreeReclaimOutcome("preserved", "attachment_stop_owner_lost")
    # Original owners already captured this root (including unavailable
    # capture). Its one shared batch owns all cleanup observations; a notice
    # lookup here would reset the DB/work allowance before that batch runs.
    if original_task is not None and original_task.id == task_id and original_task.parent_task_id is None:
        return TerminalWorktreeReclaimOutcome('preserved', 'original_root_owner_batch')
    try:
        task = orch._db.get_task(task_id)
    except Exception:
        logger.warning('terminal worktree %s task unavailable', task_id, exc_info=True)
        return TerminalWorktreeReclaimOutcome('preserved', 'task_probe_unavailable')
    if task is not None and task.parent_task_id is None:
        if transition is not None:
            _root_worktree_batch(orch, [transition])
            return TerminalWorktreeReclaimOutcome("root_receipt", "owner_batch")
        # Terminal notices/read paths have no original winner. They cannot
        # manufacture a new start, even when the prior finish is missing.
        budget = _RootWorktreeBudget()
        try:
            rows = budget.database(orch._db, lambda: orch._db.terminal_worktree_raw_audit(task_id))
            starts = [row for row in rows if row['action'] == 'root_worktree_reconciliation_started']
            if len(starts) == 1:
                start = starts[0]
                payload = json.loads(start['payload'])
                if payload['root_created_at'] == task.created_at.isoformat():
                    finishes = [row for row in rows if row['action'] == 'root_worktree_reconciliation_finished'
                                and json.loads(row['payload']).get('event_id') == start['id']]
                    return TerminalWorktreeReclaimOutcome('preserved',
                        ('replayed_finished:' if len(finishes) == 1 else 'interrupted_unknown:')+str(start['id']))
        except Exception:
            logger.warning('root worktree %s prior receipt lookup unavailable', task_id, exc_info=True)
        return TerminalWorktreeReclaimOutcome("preserved", "original_root_transition_unavailable")
    deadline = time.monotonic() + _TERMINAL_WORKTREE_TIMEOUT_SECONDS

    def preserve(reason: str) -> TerminalWorktreeReclaimOutcome:
        logger.info("terminal worktree %s preserved: %s", task_id, reason)
        return TerminalWorktreeReclaimOutcome("preserved", reason)

    def run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        if current_owner is not None and not current_owner():
            raise ValueError("attachment_stop_owner_lost")
        result = _run_terminal_worktree_command(
            args,
            cwd=cwd,
            timeout=_terminal_worktree_remaining(deadline),
        )
        _terminal_worktree_remaining(deadline)
        if len(result.stdout) + len(result.stderr) > _TERMINAL_WORKTREE_OUTPUT_LIMIT:
            raise ValueError("terminal worktree probe output exceeded limit")
        return result

    try:
        store = getattr(orch._db, "_task_pause_store", None)
        if store is not None:
            projection = store.projection(task_id)
            if projection["effective_hold"] or projection["blockers"] or not projection["evidence_complete"]:
                return preserve("task-pause-ownership-unsettled")
        _terminal_worktree_remaining(deadline)
        task = orch._db.get_task(task_id)
        if task is None:
            return preserve("task-missing")
        if task.status not in _TERMINAL_WORKTREE_STATUSES:
            return preserve("status-ineligible")
        agent = task.assigned_agent
        if not agent or agent not in orch.teams.all_agents():
            return preserve("agent-unregistered")

        primary = orch._paths.workspaces_dir / agent / "repos" / "happyranch"
        candidate = primary / ".claude" / "worktrees" / task_id
        if not primary.is_absolute() or not candidate.is_absolute():
            return preserve("path-noncanonical")
        if candidate.parent != primary / ".claude" / "worktrees":
            return preserve("path-noncanonical")
        if not os.path.lexists(primary):
            return preserve("primary-missing")
        if _terminal_worktree_path_has_symlink(primary):
            return preserve("primary-symlinked")
        if primary.resolve(strict=True) != primary:
            return preserve("primary-noncanonical")
        if not os.path.lexists(candidate):
            return preserve("worktree-absent")
        if _terminal_worktree_path_has_symlink(candidate):
            return preserve("worktree-symlinked")
        if candidate.resolve(strict=True) != candidate:
            return preserve("worktree-noncanonical")
        if primary.stat().st_dev != candidate.stat().st_dev:
            return preserve("cross-device")

        bare = run(["git", "rev-parse", "--is-bare-repository"], primary)
        if bare.returncode != 0 or bare.stdout.strip() != "false":
            return preserve("primary-not-nonbare")
        top = run(["git", "rev-parse", "--show-toplevel"], primary)
        if top.returncode != 0 or Path(top.stdout.strip()) != primary:
            return preserve("primary-root-mismatch")

        listed = run(["git", "worktree", "list", "--porcelain"], primary)
        if listed.returncode != 0:
            return preserve("worktree-registration-unknown")
        record: dict[str, str] | None = None
        for raw_record in listed.stdout.strip().split("\n\n"):
            fields: dict[str, str] = {}
            for line in raw_record.splitlines():
                key, _, value = line.partition(" ")
                fields[key] = value
            if fields.get("worktree") == str(candidate):
                record = fields
                break
        if record is None:
            return preserve("worktree-unregistered")
        expected_branch = f"refs/heads/task/{task_id}"
        if candidate.name != task_id or record.get("branch") != expected_branch:
            return preserve("worktree-identity-mismatch")

        status = run(
            ["git", "status", "--porcelain", "--untracked-files=all"], candidate,
        )
        if status.returncode != 0:
            return preserve("worktree-status-unknown")
        if status.stdout:
            return preserve("worktree-dirty")

        durable = run([
            "git", "for-each-ref", "--contains", "HEAD",
            "--format=%(refname)", "refs/remotes",
        ], candidate)
        if durable.returncode != 0:
            return preserve("remote-ref-unknown")
        if not [line for line in durable.stdout.splitlines() if line.startswith("refs/remotes/")]:
            return preserve("commit-not-durable")

        branch = f"task/{task_id}"
        pull_requests = run([
            "gh", "pr", "list", "--state", "all", "--head", branch,
            "--json", "number,state", "--limit", "100",
        ], primary)
        if pull_requests.returncode != 0:
            return preserve("pull-request-probe-unknown")
        try:
            pr_rows = json.loads(pull_requests.stdout)
        except (json.JSONDecodeError, TypeError):
            return preserve("pull-request-probe-unknown")
        if (
            not isinstance(pr_rows, list)
            or len(pr_rows) >= 100
            or any(
                not isinstance(row, dict)
                or not isinstance(row.get("number"), int)
                or row.get("state") not in {"OPEN", "CLOSED", "MERGED"}
                for row in pr_rows
            )
        ):
            return preserve("pull-request-probe-unknown")
        if any(row["state"] != "MERGED" for row in pr_rows):
            return preserve("unmerged-pull-request")

        sessions = getattr(orch, "_sessions", None)
        if sessions is None:
            return preserve("session-tracker-unavailable")
        try:
            if any(tid == task_id for tid, _agent, _sid in sessions.iter_active()):
                return preserve("live-session")
            if sessions.iter_task_cancel_controls(task_id):
                return preserve("live-control")
            if sessions.iter_task_pids(task_id):
                return preserve("live-pid")
        except Exception:
            return preserve("session-probe-unknown")

        process_reason = _terminal_worktree_process_reference(candidate, deadline)
        if process_reason is not None:
            return preserve(process_reason)

        _terminal_worktree_remaining(deadline)
        results = orch._db.get_task_results(task_id)
        applicable = [row for row in results if row.get("agent") == agent]
        if applicable:
            risks = applicable[-1].get("risks_flagged")
            if risks is not None and not isinstance(risks, list):
                return preserve("deferral-evidence-unknown")
            if any(
                isinstance(risk, str) and risk.startswith("worktree-deferred:")
                for risk in (risks or [])
            ):
                return preserve("recorded-deferral")

        removed = run(
            ["git", "worktree", "remove", str(candidate)],
            primary,
        )
        if removed.returncode != 0:
            return preserve("remove-failed")
        logger.info("terminal worktree %s removed", task_id)
        return TerminalWorktreeReclaimOutcome("removed", "eligible")
    except subprocess.TimeoutExpired:
        return preserve("probe-timeout")
    except TimeoutError:
        return preserve("deadline-expired")
    except Exception:
        logger.exception("terminal worktree %s probe failed closed", task_id)
        return preserve("probe-error")


def is_root(task: "TaskRecord") -> bool:
    """Canonical structural root test (THR-033 Change A): a task with no parent.

    Per the subtask/composite-task model this is equivalent to
    ``task_type == "task"``, but ``parent_task_id`` is the direct fact the
    escalation/walk code keys on. Only root tasks escalate to the founder; a
    non-root task that would escalate instead fails and hands back to its parent.
    """
    return task.parent_task_id is None


def _enqueue_task_generation_aware(
    orch: "Orchestrator", task_id: str, *, metadata: dict | None = None,
    legacy: str = "put_nowait",
) -> None:
    """Route a producer's enqueue through the common DB-aware boundary.

    THR-229 checkpoint C3d4a: every direct run_step producer uses this instead
    of a bare ``queue.put_nowait``.  The boundary resolves the TARGET root's own
    durable v2 generation at production time (never request metadata, never a
    parent's token): a ``pending(G)`` target is published through the
    authenticated notification publisher, ``admitted``/malformed/unreadable
    targets refuse, and ``absent``/``retired`` targets keep the unchanged
    ordinary enqueue with trigger metadata preserved.

    ``legacy`` preserves each producer's EXACT original ordinary call shape:
    ``put_nowait(slug, task_id)`` (the direct producers, no metadata kwarg) or
    ``enqueue(slug, task_id, metadata=...)`` (the blocked-job resume producer).
    The boundary's publish/refuse decisions are identical either way.
    """
    queue = getattr(orch, "_queue", None)
    if queue is None:
        return
    from runtime.orchestrator.authority import enqueue_task_generation_aware
    if legacy == "enqueue":
        if metadata is None:
            raw = lambda: queue.enqueue(orch._slug, task_id)
        else:
            raw = lambda: queue.enqueue(orch._slug, task_id, metadata=metadata)
    else:
        raw = lambda: queue.put_nowait(orch._slug, task_id)
    enqueue_task_generation_aware(
        orch, queue, orch._slug, task_id, metadata=metadata,
        ordinary_enqueue=raw,
    )


def _request_pending_v2_publication(orch: "Orchestrator", task_id: str) -> None:
    """THR-229 R4 liveness: request INDEPENDENT discovery/publication.

    An untagged queue item whose ordinary claim was refused because the target
    root has a live ``pending(G)`` v2 dispatch pointer must never adopt G and
    must never fall back to an ordinary launch.  Accepted R4 instead requires
    this dequeue to REQUEST the EXISTING authenticated publisher for the target
    (which independently resolves durable state, claims, raw-puts the tagged G
    and acknowledges).  The tagged ``try_claim_v2_continuation_generation``
    commit remains the ONLY admission; this request admits/launches nothing.

    A genuine ordinary CAS loser (no pending pointer), and any
    malformed/unreadable/absent/admitted/retired state, requests NOTHING and
    can therefore never become a duplicate ordinary enqueue.  Only a genuine
    durable ``Database`` drives the classification; a mock/duck-typed
    orchestrator is not permission to consult durable v2 state.
    """
    from runtime.infrastructure.database import Database

    db = getattr(orch, "_db", None)
    if not isinstance(db, Database):
        return
    queue = getattr(orch, "_queue", None)
    if queue is None:
        return
    try:
        classification = db.classify_authority_policy_v2_root_dispatch_for_enqueue(
            task_id
        )
    except Exception:
        logger.exception(
            "run_step %s: v2 dispatch classification failed", task_id,
        )
        return
    if getattr(classification, "kind", None) != "pending":
        return
    try:
        from runtime.orchestrator.authority import (
            publish_authority_policy_v2_notifications,
        )
        publish_authority_policy_v2_notifications(
            orch, queue, root_task_id=task_id,
        )
    except Exception:
        # Liveness is best-effort: a publication-request failure must never
        # turn a correct fail-closed refusal into an admission or a crash.
        logger.exception(
            "run_step %s: pending v2 publication request failed", task_id,
        )


def run_step_impl(orch: "Orchestrator", task_id: str, metadata: dict | None = None) -> None:
    # Workflow dispatch returns before the ordinary invocation owner exists.
    # This worker scope also carries its terminal event past tracker teardown,
    # workflow invocation close/release and the dispatcher's live-claim tail.
    transitions = _RootWorktreeTransitions()
    from runtime.orchestrator.team_move import _task_tail
    tail = []
    tail_token = _task_tail.set(tail)
    tail_failed = False
    disposition = None
    try:
        with _defer_root_worktree_batches(orch, transitions) as delivery:
            _run_step_owned(orch, task_id, metadata)
    except BaseException:
        tail_failed = True
        raise
    finally:
        tail_failed |= delivery['teardown_incomplete']
        try:
            disposition = _root_worktree_batch(orch, transitions, teardown_incomplete=tail_failed)
            tail_failed |= disposition.kind != 'safe'
            if tail_failed:
                logger.warning('task %s outer cleanup retained: %s processes=%s',
                    task_id, disposition.reason, disposition.processes)
        except BaseException:
            tail_failed = True
            raise
        finally:
            _task_tail.reset(tail_token)
            from runtime.orchestrator.task_pause import settle_live
            settlement_errors = []
            for invocation, failed in tail:
                try:
                    settle_live(invocation, failed=failed or tail_failed, cleanup=disposition)
                except BaseException as exc:
                    settlement_errors.append(exc)
            if settlement_errors:
                raise BaseExceptionGroup("task Pause tail settlement failed", settlement_errors)


def _run_step_owned(orch: "Orchestrator", task_id: str, metadata: dict | None = None) -> None:
    from runtime.orchestrator.task_pause import (
        TaskInvocation, DeferredRootPause, _invocation, eligible, deferred_invocation, adopt_deferred_invocation,
    )
    from runtime.infrastructure.task_pause_controls import PauseControlError
    from runtime.orchestrator.team_move import DeferredTeamMove
    store = getattr(orch._db, "_task_pause_store", None)
    if store is None:
        from runtime.infrastructure.team_move_store import TeamMoveError, validate_team_move_schema
        try:
            with orch._db._lock:
                installed = validate_team_move_schema(orch._db._conn, org_slug=orch._slug)
        except TeamMoveError as exc:
            logger.warning('task %s move admission unavailable: %s', task_id, exc.code)
            orch._team_moves.defer_task(task_id, metadata)
            return
        if installed:
            orch._team_moves.defer_task(task_id, metadata)
            return
        return _run_step_body(orch, task_id, metadata)
    task = orch._db.get_task(task_id)
    if task is None or task.cancelled_at is not None or task.status in TERMINAL_STATES:
        return
    from runtime.workflows.recovery import classify_task, route_owned_task
    if classify_task(orch._db, task_id, org_slug=orch._slug).kind != "legacy":
        route_owned_task(orch, task_id)
        return
    invocation = TaskInvocation(orch, task, task.assigned_agent or _default_agent_for_root(orch, task),
        orch._build_session_id(),
        owner="v2" if isinstance(metadata, dict) and "authority_v2_generation" in metadata else "ordinary",
        metadata=metadata or {})
    try:
        deferred = deferred_invocation(orch, task, metadata, adopt=False)
        if deferred is not None:
            invocation = deferred
            adopt_deferred_invocation(invocation)
        else:
            if not eligible(orch, task):
                return
            invocation.prepare()
    except DeferredTeamMove as exc:
        logger.warning('task %s deferred by move admission: %s operation=%s', task_id, exc.code, exc.operation_id)
        orch._team_moves.defer_task(task_id, metadata)
        return
    except DeferredRootPause:
        return
    except Exception as exc:
        if isinstance(exc, PauseControlError) and exc.detail["code"] == "root_paused":
            return
        if invocation is not None:
            from runtime.orchestrator.launch_refusals import settle_refusal
            if settle_refusal(invocation, exc, prepared=False):
                return
        raise
    token = _invocation.set(invocation)
    root_delivery = dict(orch=orch, events=_RootWorktreeTransitions(), active=True, teardown_incomplete=False)
    root_token = _ROOT_WORKTREE_OWNER_EVENTS.set(root_delivery)
    try:
        consumer_only = getattr(invocation, "consumer_only", False)
        landed = (orch._db.get_latest_task_result(task_id, invocation.agent,
                    invocation.consumer_session_id if consumer_only else invocation.session_id)
                  if invocation.admitted or consumer_only else None)
        if landed is not None:
            # The existing consumer authenticates the actual INTEGER row and
            # current owner. A late callback ends this retry; no provider rerun
            # and no synthetic ExecutorResult or consumed-result replay.
            from runtime.orchestrator.orchestrator import completion_report_from_result_row
            report = completion_report_from_result_row(task_id, landed, fallback_agent=invocation.agent)
            orch._audit.log_completion_report(report=report)
            _consume_completion_report(orch, task_id, report, result_row_id=landed["id"])
        elif consumer_only:
            raise DeferredRootPause("exact callback no longer available")
        elif getattr(invocation, "deferred_recovery", False):
            from runtime.orchestrator.executors import ExecutorResult
            with store.db._lock:
                row = store.row_uncommitted(store.root_uncommitted(task_id)["id"])
                proof = invocation._entry(row)["context"]["recovery_return"]
            result = ExecutorResult(success=True, session_id=proof["origin_session_id"],
                                    agent_session_id=proof["provider_session_id"], duration_seconds=proof["duration_seconds"])
            _finish_step_return(orch, task, invocation.agent, result,
                                orch._read_completion_from_db(task_id, invocation.agent, result.session_id),
                                usage_already_recorded=True)
        else:
            _run_step_body(orch, task_id, invocation.metadata)
    except (DeferredRootPause, DeferredTeamMove) as exc:
        # A same-owner cleanup refusal is not an intentional move hold. Keep
        # every other native move/hold disposition and its admission policy.
        if (isinstance(exc, DeferredTeamMove) and exc.code == "team_move_preparation_unsettled"
                and invocation.move_reservation is not None and invocation.move_reservation.cleanup_failed
                and not invocation.launch_observed):
            root_delivery['teardown_incomplete'] = True
            from runtime.orchestrator.launch_refusals import settle_refusal
            if settle_refusal(invocation, exc, prepared=True):
                return
        if isinstance(exc, DeferredTeamMove):
            logger.warning('task %s deferred by move admission: %s operation=%s', task_id, exc.code, exc.operation_id)
            orch._team_moves.defer_task(task_id, metadata)
        with store.db._lock:
            row = store.row_uncommitted(store.root_uncommitted(task_id)["id"])
            entry = invocation._entry(row)
            recovery_deferred = "recovery_return" in entry["context"] and not invocation.admitted
        if recovery_deferred:
            invocation.phase("recovery_deferred")
        elif invocation.admitted:
            invocation.phase("retry_deferred", started_at=None, captured_generation=None)
    except BaseException as exc:
        root_delivery['teardown_incomplete'] = True
        if isinstance(exc, Exception) and not invocation.launch_observed:
            from runtime.orchestrator.launch_refusals import settle_refusal
            if settle_refusal(invocation, exc, prepared=True):
                return
        if invocation.admitted:
            invocation.phase("unknown")
        raise
    finally:
        root_delivery['teardown_incomplete'] |= sys.exc_info()[0] is not None
        from runtime.orchestrator.task_pause import release_live
        try:
            release_live(invocation, failed=root_delivery['teardown_incomplete'])
        except BaseException:
            root_delivery['teardown_incomplete'] = True
            raise
        finally:
            _invocation.reset(token)
            root_delivery['active'] = False
            _ROOT_WORKTREE_OWNER_EVENTS.reset(root_token)
            _root_worktree_batch(orch, root_delivery['events'],
                teardown_incomplete=root_delivery['teardown_incomplete'])


def _run_step_body(orch: "Orchestrator", task_id: str, metadata: dict | None = None) -> None:
    # metadata: optional resume context (trigger, triggering_job_id); read by the CAS-win audit hook in Task 11.
    db = orch._db
    from runtime.orchestrator.team_move import DeferredTeamMove
    from runtime.infrastructure.task_pause_controls import PauseControlError
    task = db.get_task(task_id)
    if task is None:
        return

    from runtime.orchestrator.task_pause import current_invocation
    retained_invocation = current_invocation()
    if retained_invocation is not None and retained_invocation.admitted:
        task = retained_invocation.task
    from runtime.workflows.recovery import route_owned_task
    if route_owned_task(orch, task_id):
        return

    # Cancellation short-circuit. Once /cancel marks a task FAILED + sets
    # cancelled_at, any late queue event (e.g., the parent auto-resume after
    # the SIGTERM'd child's audit arrives) must be a no-op. Checking status
    # alone isn't enough — in_progress(delegated) parents get cancelled too, and
    # the terminal-state test runs in step 1 below. Re-check before returning
    # so we don't enter the in_progress transition.
    if task.cancelled_at is not None:
        logger.debug("run_step %s: cancelled, skipping", task_id)
        return

    # ---- 1. Verify entry state ----
    # Path B: parked carriers are in_progress(delegated|blocked_on_job).
    # A live subprocess is in_progress + block_kind IS NULL — it falls to the
    # `else: skip` and is never re-admitted (admitting it would double-spawn).
    if task.status == TaskStatus.PENDING:
        pass  # eligible
    elif task.status in _PARKED_CARRIER_STATUSES and task.block_kind == BlockKind.DELEGATED:
        children = [db.get_task(cid) for cid in db.get_children(task_id)]
        # THR-211: a child whose current session has durably landed a
        # structured terminal report counts as terminal for parent admission
        # even while its row reads in_progress (the status transition is
        # deferred to session finalization). A genuinely-running child with no
        # landed result still blocks the parent — never suppress live work.
        if any(
            c is None or (
                c.status not in TERMINAL_STATES
                and not _child_has_landed_terminal_result(orch, c)
            )
            for c in children
        ):
            logger.debug("run_step %s: child still running, skipping", task_id)
            return
    elif task.status in _PARKED_CARRIER_STATUSES and task.block_kind == BlockKind.BLOCKED_ON_JOB:
        # Blocked-on-job task: re-check live job table to see whether all
        # blocking jobs have reached a terminal state. Spec §5.1.
        import json as _json
        try:
            job_ids = _json.loads(task.blocked_on_job_ids or "[]")
        except _json.JSONDecodeError:
            logger.debug("run_step %s: blocked_on_job_ids unparseable", task_id)
            return
        if not job_ids:
            logger.debug("run_step %s: blocked_on_job_ids empty", task_id)
            return
        _TERMINAL_JOB_STATES = {"completed", "failed", "rejected"}
        for jid in job_ids:
            jstatus = db.get_job_status(jid)
            if jstatus not in _TERMINAL_JOB_STATES:
                logger.debug(
                    "run_step %s: blocking job %s still in-flight (status=%s)",
                    task_id, jid, jstatus,
                )
                return
        # All jobs terminal — fall through to step 2 + step 3.
    else:
        logger.debug(
            "run_step %s: not eligible (status=%s, block_kind=%s)",
            task_id, task.status, task.block_kind,
        )
        return

    from runtime.orchestrator.task_pause import current_invocation, DeferredRootPause
    pause_invocation = current_invocation()
    if pause_invocation is not None:
        # Candidate identity is pure preparation; arbitration owns all writes.
        next_count = task.orchestration_step_count + 1
        reserved_session_id = pause_invocation.session_id
    else:
        # ---- 2. Atomic claim (persisted count is monotonic telemetry) ----
        next_count = task.orchestration_step_count + 1
        # ---- 3. Atomic claim: unblock + increment + mark in_progress ----
        # Conditional CAS on (expected_status, expected_block_kind) — if another
        # worker has already claimed this task_id (duplicate enqueue from a
        # multi-child fan-in race, or parent auto-resume colliding with a late
        # callback), the UPDATE matches zero rows and we return silently.
        #
        # THR-229 C3d3b mandatory fence: a TAGGED queue item carrying a v2
        # continuation generation token is admitted ONLY by the atomic generation
        # claim, which authenticates the exact token G.  A malformed/present-null,
        # empty, stale or mismatched token refuses and NEVER falls back to the
        # ordinary claim.  An UNTAGGED item still uses the ordinary claim, which
        # itself refuses a pending v2 pointer (so a missed producer cannot dispatch
        # a root awaiting a v2 continuation).
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        tagged_admission = "authority_v2_generation" in metadata_dict
        reserved_session_id: str | None = None
        if tagged_admission:
            admission_generation = metadata_dict.get("authority_v2_generation")
            notification = None
            if isinstance(admission_generation, str) and admission_generation:
                notification = db.get_authority_policy_v2_recovery_notification(
                    admission_generation
                )
            if notification is None or notification.root_task_id != task_id:
                logger.debug(
                    "run_step %s: tagged continuation generation absent/unknown — refuse",
                    task_id,
                )
                return
            reserved_session_id = orch._build_session_id()
            reservation = db.try_claim_v2_continuation_generation(
                root_task_id=task_id,
                manager_agent=notification.manager_agent,
                manager_session_id=notification.manager_session_id,
                result_id=notification.result_id,
                generation_id=admission_generation,
                next_session_id=reserved_session_id,
            )
            if reservation.status != "claimed":
                logger.debug(
                    "run_step %s: generation admission refused (%s) — no ordinary claim",
                    task_id, reservation.reason,
                )
                return
            next_count = reservation.orchestration_step_count or next_count
            # The reserved session is now durable on the task; settle the admission
            # bookkeeping BEFORE any external launch.  A settlement failure holds
            # dispatch and permits only exact settlement retry — never a launch and
            # never a repeat generation admission.
            settlement = db.settle_v2_continuation_generation_admission(
                root_task_id=task_id,
                manager_agent=notification.manager_agent,
                manager_session_id=notification.manager_session_id,
                result_id=notification.result_id,
                generation_id=admission_generation,
                next_session_id=reserved_session_id,
            )
            if settlement.status not in ("settled", "already_settled_exact"):
                logger.warning(
                    "run_step %s: admission settlement held (%s) — no launch",
                    task_id, settlement.reason,
                )
                return
            claimed = True
        else:
            claimed = db.try_claim_for_step(
                task_id,
                expected_status=task.status,
                expected_block_kind=task.block_kind,
                new_count=next_count,
            )
        if not claimed:
            logger.debug(
                "run_step %s: lost claim race (another worker is advancing it)",
                task_id,
            )
            # THR-229 R4: the ordinary claim also refuses a root whose durable
            # pointer is ``pending(G)``.  That specific refusal is NOT a lost race:
            # request independent discovery/publication for the target so the live
            # generation can be tagged and admitted through its own fence.  This
            # dequeue admits/launches nothing and never rewrites the stale item.
            _request_pending_v2_publication(orch, task_id)
            return

        # Spec §5.2: write task_resumed_from_jobs audit row immediately after the
        # CAS wins on an in_progress(blocked_on_job) → in_progress(NULL) transition. The
        # prompt-build at step 4 reads this row to inject BLOCKED-JOBS-RESULTS.
        if (task.status in _PARKED_CARRIER_STATUSES
                and task.block_kind == BlockKind.BLOCKED_ON_JOB):
            import json as _json
            try:
                job_ids = _json.loads(task.blocked_on_job_ids or "[]")
            except _json.JSONDecodeError:
                job_ids = []
            job_outcomes = {jid: (db.get_job_status(jid) or "unknown")
                            for jid in job_ids}
            md = metadata or {}
            orch._audit.log_task_resumed_from_jobs(
                task_id=task_id,
                blocking_job_ids=job_ids,
                trigger=md.get("trigger", "unknown"),
                triggering_job_id=md.get("triggering_job_id"),
                job_outcomes=job_outcomes,
            )

        # Fan-out dispatch: active_fanout can only be "spawned"
        # (all children terminal — inject join context).
        # pending_review removed — founder ruling THR-012 msg 129/131
        # removed the fan-out review gate entirely.
        if task.active_fanout is not None:
            from runtime.orchestrator.fanout import FanoutState as _FanoutState
            fanout_state = _FanoutState.deserialize(task.active_fanout)
            if fanout_state.status == "spawned":
                # status == "spawned" → all children terminal; inject join context.
                _inject_fanout_join_context(orch, task_id, task.active_fanout)
                db.update_task_active_fanout(task_id, None)

    # ---- 4. Run the agent subprocess ----
    agent = task.assigned_agent or _default_agent_for_root(orch, task)
    if task.assigned_agent is None and pause_invocation is None:
        db.update_task(task_id, assigned_agent=agent)

    prompt = _build_agent_prompt(orch, task, agent)
    if pause_invocation is None:
        prompt += _prepare_workspace_cleanup_reclamation_context(
            orch, task, agent, stale_orchestration_step_count=task.orchestration_step_count,
            claimed_next_step_count=next_count,
        )
    try:
        if reserved_session_id is None:
            result, report = orch._run_agent(task_id, agent, prompt)
        else:
            result, report = orch._run_agent(
                task_id, agent, prompt, runtime_session_id=reserved_session_id,
            )
    except (DeferredRootPause, DeferredTeamMove, PauseControlError):
        # Holds remain deferrals. Actual native prelaunch errors reach the
        # owner boundary for bounded refusal accounting and normal failure.
        raise
    except Exception as exc:
        if pause_invocation is not None and not pause_invocation.launch_observed:
            # The native owner records and settles before its release tail.
            raise
        # Metadata only: never format the traceback/exception (which includes
        # messages and source lines), or inspect frame locals. Diagnostic
        # failure cannot replace the existing failure/wake/followup sequence.
        try:
            source_root = Path(__file__).resolve().parents[2]
            locations = []
            trace = exc.__traceback__
            for _ in range(64):
                if trace is None:
                    break
                filename = Path(trace.tb_frame.f_code.co_filename)
                location = "external"
                if filename.is_absolute() and filename.is_relative_to(source_root):
                    relative = filename.relative_to(source_root).as_posix()
                    if (len(relative) <= 200 and relative.split("/")[0] in {"runtime", "cli"}
                            and ".." not in filename.parts and all(c.isalnum() or c in "_./-" for c in relative)):
                        location = relative
                locations.append(f"{location}:{trace.tb_lineno}")
                trace = trace.tb_next
            exception_class = type(exc).__name__
            if len(exception_class) > 80 or not exception_class.isidentifier():
                exception_class = "unavailable"
            logger.warning("agent invocation exception task=%s agent=%s runtime_session=%s class=%s frames=%s truncated=%s",
                           task_id, agent, reserved_session_id or "unavailable", exception_class,
                           locations[-8:], trace is not None or len(locations) > 8)
        except Exception:
            pass
        note = f"agent invocation failed: {exc}"
        _fail(orch, task_id, note=note)
        _enqueue_parent_if_waiting(orch, task_id, root_auto_revisit_spawned=False)
        _maybe_post_thread_followup(
            orch, task_id,
            status=TaskStatus.FAILED, auto_revisit_spawned=False,
        )
        return

    _finish_step_return(orch, task, agent, result, report)


def _finish_step_return(orch: "Orchestrator", task: TaskRecord, agent: str, result, report,
                        *, usage_already_recorded: bool = False) -> None:
    db, task_id = orch._db, task.id
    from runtime.orchestrator.task_pause import current_invocation, DeferredRootPause
    from runtime.orchestrator.team_move import DeferredTeamMove
    from runtime.infrastructure.task_pause_controls import PauseControlError
    pause_invocation = current_invocation()
    # Persist token usage for this session, regardless of session outcome.
    # Spec 4.3: skip when None; otherwise write — including the parse-failure
    # case where token columns are NULL but ``usage_raw_json`` carries the
    # raw payload. Done before outcome classification so timeouts / blocked
    # sessions still land their usage row.
    if not usage_already_recorded and result.token_usage is not None:
        db.insert_session_token_usage(
            task_id=task_id,
            agent=agent,
            session_id=result.session_id,
            executor=orch._resolve_executor_name(agent),
            token_usage=result.token_usage,
            scope_type="task",
            scope_id=task_id,
            thread_id=task.dispatched_from_thread_id,
        )

    # THR-247: a clean Codex provider return without the exact callback gets
    # one durably-spent, provider-resumed recovery before ordinary failure.
    # No timeout/error/cancel/recovery invocation is eligible, and a late
    # origin callback loses to the transactional claim rather than being
    # inferred from prose or a prior job/session outcome.
    accepted_recovery_callback = False
    recovery_session_id: str | None = None
    recovery_claimed = False
    # Resolution normally happened inside ``_run_agent``.  Preserve the
    # historic fail-closed no-callback outcome for legacy rows/tests whose
    # executor definition is no longer available: they are simply ineligible
    # for the Codex-only recovery, not a new invocation error.
    from runtime.orchestrator.orchestrator import AgentTerminatedError, AgentUnavailableError
    try:
        recovery_executor = orch._resolve_executor_name(agent)
    except (AgentTerminatedError, AgentUnavailableError):
        recovery_executor = None
    if (
        result.success and report is None
        and recovery_executor == "codex"
        and bool(result.agent_session_id)
    ):
        if pause_invocation is not None:
            claimed, recovery_result, recovery_report = pause_invocation.recover_return(result)
            recovery_session_id = pause_invocation.session_id
            recovery_claimed = claimed
            origin_session_id = result.session_id
            if claimed:
                if (recovery_result.session_id == recovery_session_id
                        and recovery_result.token_usage is not None):
                    db.insert_session_token_usage(
                        task_id=task_id, agent=agent,
                        session_id=recovery_result.session_id,
                        executor=orch._resolve_executor_name(agent),
                        token_usage=recovery_result.token_usage,
                        scope_type="task", scope_id=task_id,
                        thread_id=task.dispatched_from_thread_id,
                    )
                result, report = recovery_result, recovery_report
                accepted_row = db.get_accepted_task_completion_recovery_result(task_id=task_id, agent=agent)
                if accepted_row is not None:
                    from runtime.orchestrator.orchestrator import completion_report_from_result_row
                    report = completion_report_from_result_row(task_id, accepted_row, fallback_agent=agent)
                    accepted_recovery_callback = True
            else:
                report = orch._read_completion_from_db(task_id, agent, origin_session_id)
        else:
            origin_session_id = result.session_id
            recovery_session_id = orch._build_session_id()
            # Establish the live budget before the durable claim.  Claim/SQLite
            # contention is part of this one recovery attempt; it must never get
            # a fresh 120 seconds after returning.
            recovery_deadline_monotonic = time.monotonic() + 120.0
            claimed_at = datetime.now(timezone.utc)
            expires_at = claimed_at + timedelta(seconds=120)
            if db.claim_task_completion_recovery(
                task_id=task_id, agent=agent, origin_session_id=origin_session_id,
                recovery_session_id=recovery_session_id,
                provider_session_id=result.agent_session_id,
                claimed_at=claimed_at.isoformat(), expires_at=expires_at.isoformat(),
            ):
                recovery_claimed = True
                # Claim-before-launch deliberately spends the one attempt even if
                # preparation/admission/launch refuses.  The short prompt carries
                # the current binding and never replays the implementation brief.
                recovery_prompt = (
                    "Your previous turn ended without a HappyRanch completion callback. "
                    "Submit the required callback now using the current task/session "
                    f"binding task={task_id} session={recovery_session_id} and the "
                    "work already performed. If waiting on a job, report blocked with "
                    "its actual job ID. Do not claim unverified success. Do not continue "
                    "implementation or start new work."
                )
                # Wall expiry is durable; this monotonic deadline fences the live
                # invocation across preparation and admission waits.
                remaining = max(0, int(recovery_deadline_monotonic - time.monotonic()))
                if remaining > 0:
                    try:
                        recovery_result, recovery_report = orch._run_agent(
                            task_id, agent, recovery_prompt,
                            runtime_session_id=recovery_session_id,
                            resume_session_id=result.agent_session_id,
                            origin_runtime_session_id=origin_session_id,
                            timeout_seconds_override=remaining,
                            recovery_deadline_monotonic=recovery_deadline_monotonic,
                            recovery=True,
                        )
                        if recovery_result.token_usage is not None:
                            db.insert_session_token_usage(
                                task_id=task_id, agent=agent,
                                session_id=recovery_result.session_id,
                                executor=orch._resolve_executor_name(agent),
                                token_usage=recovery_result.token_usage,
                                scope_type="task", scope_id=task_id,
                                thread_id=task.dispatched_from_thread_id,
                            )
                        result, report = recovery_result, recovery_report
                    except (DeferredRootPause, DeferredTeamMove, PauseControlError):
                        raise
                    except Exception as exc:
                        result.error = f"completion recovery launch failed: {exc}"
                else:
                    # The durable claim has already spent the sole recovery.  Do
                    # not leave a clean origin omission in progress merely
                    # because claim contention consumed the entire live budget.
                    result.success = False
                    result.error = "completion recovery live budget expired before launch"
                accepted_row = db.get_accepted_task_completion_recovery_result(
                    task_id=task_id, agent=agent,
                )
                if accepted_row is not None:
                    from runtime.orchestrator.orchestrator import completion_report_from_result_row
                    report = completion_report_from_result_row(
                        task_id, accepted_row, fallback_agent=agent,
                    )
                    # The accepted callback is the durable terminal authority.
                    # Keep the provider result untouched for honest diagnostics and
                    # usage accounting, but do not let a late provider error erase
                    # an already-admitted recovery result.
                    accepted_recovery_callback = True
            else:
                # An exact origin callback may have committed between the initial
                # read and claim. Decode only that persisted invocation result.
                accepted = orch._read_completion_from_db(task_id, agent, origin_session_id)
                if accepted is not None:
                    report = accepted

    # A newer ordinary generation can replace the claimed recovery while its
    # preparation/admission path is waiting.  Its launch validator refuses to
    # spawn, and its old unwind must not fail, clear, consume, or otherwise
    # settle the newer owner.  The current durable binding is the authority;
    # this is intentionally before ordinary result classification.
    refetch = db.get_task(task_id)
    if (
        # A candidate recovery id is allocated before the transaction.  A
        # losing claim can mean the exact origin callback won the race, in
        # which case the task correctly remains owned by that origin session;
        # comparing it to an unused candidate would strand the accepted row.
        recovery_claimed
        and refetch is not None
        # An exhausted/pre-publication recovery legitimately leaves the
        # durable origin binding in place.  Only a third generation is a
        # replacement whose state this old invocation must preserve.
        and refetch.current_session_id not in {recovery_session_id, origin_session_id}
        and refetch.cancelled_at is None
    ):
        logger.debug(
            "run_step %s: recovery ownership replaced before launch/settlement",
            task_id,
        )
        return

    # Cancel-race Guard B: /cancel can land between try_claim_for_step and
    # subprocess exit. The l.41 entry guard only catches NEW enqueues. If we
    # observe cancelled_at != NULL here, the report (if any) is from a
    # cancelled tree and must not feed the decision pipeline — otherwise the
    # `delegate` branch (no idempotence guard) will resurrect the parent and
    # spawn a child task on a tree the founder explicitly killed.
    # Token usage stays persisted above (provider really charged for it).
    # See docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.2.
    refetch = db.get_task(task_id)
    if refetch is None or refetch.cancelled_at is not None:
        logger.debug(
            "run_step %s: cancelled during session, dropping report", task_id,
        )
        # IN_PROGRESS cancellation: the cancel route sent SIGTERM and set
        # cancelled_at BEFORE run_step reached Site B, so Site B never runs.
        # Fire the followup here instead.  Disjoint with the cancel route's
        # Phase 1b (which fires for PENDING/BLOCKED only — tasks that had no
        # live subprocess at cancel time), so no double-fire risk.
        # THR-181 Track A (founder lifecycle envelope): cancellation during the
        # continued turn spends the single-use envelope fail-closed so the
        # continuation is never re-used.
        try:
            db.spend_authority_continue_envelope_if_active(
                task_id,
                audit_agent=agent,
                error="cancelled during the continued turn",
            )
        except Exception:  # pragma: no cover - fail-closed defensive
            pass
        if refetch is not None:
            _maybe_post_thread_followup(
                orch, task_id,
                status=TaskStatus.FAILED, auto_revisit_spawned=False,
            )
        return

    # ---- 5. Classify outcome ----
    if (not result.success or report is None) and not accepted_recovery_callback:
        note = _session_failed_note(result, report)
        _fail(orch, task_id, note=note)
        _enqueue_parent_if_waiting(orch, task_id, root_auto_revisit_spawned=False)
        _maybe_post_thread_followup(
            orch, task_id,
            status=TaskStatus.FAILED, auto_revisit_spawned=False,
        )
        return

    # THR-181 Track A: resolve the IMMUTABLE task-result row id whose
    # CompletionReport produced this decision. The authority hook derives its
    # candidate claim from it, so restart/recovery re-entry of the SAME row
    # maps to the SAME candidate (never a second evaluation).  It is resolved
    # before the completion audit so a v2 manager result can be attributed to
    # that exact causal result/session at the real completion producer seam.
    result_row_id = None
    if accepted_recovery_callback:
        accepted_row = db.get_accepted_task_completion_recovery_result(
            task_id=task_id, agent=agent,
        )
        if accepted_row is None:
            current = db.get_task(task_id)
            if (
                current is None
                or current.cancelled_at is not None
                or current.current_session_id != recovery_session_id
            ):
                # Cancellation or a newer owner won after the earlier lookup.
                # This old runner has no authority to settle that generation.
                return
            # The ledger/result/current-owner binding changed after the
            # callback was observed.  Never fall back to an unrelated latest
            # result row; terminally fail closed instead.
            _fail(
                orch, task_id,
                note="accepted completion recovery result no longer matches durable binding",
                reclaim_terminal_worktree=False,
            )
            _enqueue_parent_if_waiting(orch, task_id, root_auto_revisit_spawned=False)
            _maybe_post_thread_followup(
                orch, task_id, status=TaskStatus.FAILED, auto_revisit_spawned=False,
            )
            return
        result_row_id = accepted_row["id"]
    if (not accepted_recovery_callback) and report is not None and isinstance(
        getattr(result, "session_id", None), str
    ) and result.session_id:
        _result_row = db.get_latest_task_result(task_id, agent, result.session_id)
        if _result_row is not None:
            result_row_id = _result_row["id"]
    orch._log_step_result(task_id, result, report, result_row_id=result_row_id)
    if accepted_recovery_callback:
        _consume_accepted_completion_recovery(
            orch, task_id, report, agent=agent,
            session_id=recovery_session_id or "", result_row_id=result_row_id,
        )
    else:
        _consume_completion_report(orch, task_id, report, result_row_id=result_row_id)


@dataclass
class _HumanFailedRecoveryOperation:
    task_id: str
    agent: str
    session_id: str
    result_id: int
    phase: str = "evidence"
    key: tuple | None = None
    accepted_payload: tuple[dict, dict, dict] | None = None
    relation: tuple[str, str] | None = None
    completion: Future = field(default_factory=Future)
    disposition: str = "continuation_pending"
    timer: object | None = None
    drain: object | None = None
    job_ids: tuple[str, ...] = ()
    bookkeeping: list[Callable[[], None]] = field(default_factory=list)
    parent_effect: Callable[[], None] | None = None
    startup_owner: _StartupSweepOwner | None = None

    def matches_context(self, context: dict) -> bool:
        task, episode = context["task"], context["episode"]
        return bool(self.accepted_payload is not None
            and self.key == (task.id, self.agent, episode["origin_session_id"], self.session_id, self.result_id)
            and self.relation == (task.parent_task_id, task.team)
            and self.accepted_payload[1] == context["report"].model_dump()
            and self.accepted_payload[2] == context["selected_row"]
            and all(self.accepted_payload[0][name] == episode[name]
                    for name in ("origin_session_id", "provider_session_id", "claimed_at",
                                 "expires_at", "accepted_result_session_id")))

    def finish(self, disposition: str) -> None:
        if self.completion.done():
            return
        self.disposition = disposition
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        if not self.completion.done():
            if self.startup_owner is not None:
                self.startup_owner.human_finished(disposition)
            self.completion.set_result(disposition)


def _is_human_failed_recovery(orch: "Orchestrator", task, report) -> bool:
    if (task is None or task.task_type != "subtask" or task.parent_task_id is None
            or report.status != "blocked" or report.waiting_on_job_ids):
        return False
    try:
        return orch.teams.manager_for_team(task.team).kind == "human"
    except KeyError:
        return False


def _drive_human_failed_recovery(orch: "Orchestrator", operation: _HumanFailedRecoveryOperation) -> None:
    """One synchronous gate attempt on the writer loop, or before serving."""
    if operation.completion.done():
        return
    if operation.startup_owner is not None and not operation.startup_owner.admission_open():
        operation.finish('recovery_required')
        return
    org = getattr(getattr(orch, "_workflow_drafts", None), "org", None)
    queue = getattr(orch, "_queue", None)
    supervisor = getattr(orch, "_host_supervisor", None)
    if (org is None or (queue is not None and getattr(queue, "_stopping", False))
            or (supervisor is not None and supervisor._admission.is_shutdown())):
        operation.finish("recovery_required")
        return
    authority, db = org.workflow_authority, orch._db
    operation.disposition = "writer_busy"
    if authority._async_writer_lock.locked() or not authority._publisher_lock.acquire(blocking=False):
        _retry_human_failed_recovery(orch, operation)
        return
    acquired_db = False
    try:
        if authority._async_writer_lock.locked() or not db._lock.acquire(blocking=False):
            return
        acquired_db = True
        with authority.writer_interval(publisher="human-failed-leaf-recovery", settlement=True):
            params = dict(task_id=operation.task_id, agent=operation.agent,
                          session_id=operation.session_id, result_row_id=operation.result_id)
            while operation.phase != "drain_jobs":
                if operation.phase not in ('evidence', 'chain', 'fanout', 'fail', 'review',
                                           'history', 'marker', 'settle_jobs',
                                           'parent_handoff', 'caller_bookkeeping'):
                    operation.finish('recovery_required')
                    return
                with _startup_sweep_effect(orch):
                    disposition, context = db.human_failed_recovery_context(
                        **params, allow_unbound_live=operation.phase == "evidence")
                    if disposition != "eligible":
                        operation.finish("lost_owner" if disposition == "not_applicable" else disposition)
                        return
                    task, episode = context["task"], context["episode"]
                    key = (operation.task_id, operation.agent, episode["origin_session_id"], operation.session_id, operation.result_id)
                    if operation.key is None:
                        operation.key = key
                        operation.accepted_payload = episode, context["report"].model_dump(), context["selected_row"]
                        operation.relation = (task.parent_task_id, task.team)
                        if operation.parent_effect is None:
                            operation.parent_effect = lambda: _enqueue_parent_if_waiting(
                                orch, operation.task_id, root_auto_revisit_spawned=False)
                        full = getattr(orch, "_human_failed_recovery_by_fingerprint", None)
                        if full is None:
                            full = orch._human_failed_recovery_by_fingerprint = {}
                        full[key] = operation
                    elif not operation.matches_context(context):
                        operation.finish("lost_owner")
                        return
                    if episode["state"] == "callback_consumed" and operation.phase in ("evidence", "chain", "fanout", "fail", "review", "history", "marker"):
                        operation.phase = "settle_jobs"
                    if operation.phase in ("evidence", "chain", "fanout", "fail", "review", "marker"):
                        disposition = db.apply_human_failed_recovery_effect(**params, effect=operation.phase)
                        if disposition != "progressed":
                            operation.finish(disposition)
                            return
                        order = ("evidence", "chain", "fanout", "fail", "review", "history", "marker", "settle_jobs")
                        operation.phase = order[order.index(operation.phase) + 1]
                    elif operation.phase == "history":
                        # Preserve the native file boundary outside DB/SQL while
                        # retaining the publisher; fresh reads precede the marker.
                        db._lock.release()
                        acquired_db = False
                        orch._update_task_history(operation.task_id)
                        if not db._lock.acquire(blocking=False):
                            return
                        acquired_db = True
                        operation.phase = "marker"
                    elif operation.phase == "settle_jobs":
                        ids = db.settle_consumed_task_completion_recovery_jobs(
                            task_id=operation.task_id, agent=operation.agent,
                            recovery_session_id=operation.session_id, result_row_id=operation.result_id,
                            terminal_status=TaskStatus.FAILED.value,
                            finished_at=datetime.now(timezone.utc).isoformat())
                        if ids is None:
                            operation.finish("lost_owner")
                            return
                        operation.job_ids = ids
                        operation.phase = "drain_jobs" if ids else "parent_handoff"
                    elif operation.phase == "parent_handoff":
                        if not db.handoff_consumed_task_completion_recovery_parent_effect(
                                task_id=operation.task_id, agent=operation.agent,
                                recovery_session_id=operation.session_id, result_row_id=operation.result_id,
                                terminal_status=TaskStatus.FAILED.value, effect=operation.parent_effect):
                            operation.finish("lost_owner")
                            return
                        operation.phase = "caller_bookkeeping"
                    elif operation.phase == "caller_bookkeeping":
                        for effect in operation.bookkeeping:
                            with _startup_sweep_effect(orch):
                                effect()
                        operation.bookkeeping.clear()
                        operation.finish("done")
                        return
    except Exception as exc:
        if operation.startup_owner is not None:
            operation.startup_owner.fail(exc)
        logger.exception("human failed recovery requires next-start reconciliation task=%s phase=%s", operation.task_id, operation.phase)
        operation.finish("recovery_required")
    finally:
        if acquired_db:
            db._lock.release()
        authority._publisher_lock.release()
        if not operation.completion.done():
            if operation.phase == "drain_jobs":
                _drain_human_failed_recovery_jobs(orch, operation)
            else:
                _retry_human_failed_recovery(orch, operation)


def _retry_human_failed_recovery(orch: "Orchestrator", operation: _HumanFailedRecoveryOperation) -> None:
    if operation.completion.done() or operation.timer is not None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # Genuine pre-serving startup only; no inherited live controls.
        org = getattr(getattr(orch, "_workflow_drafts", None), "org", None)
        if getattr(orch, "_main_loop", None) is not None or org is None or org.workflow_authority._async_writer_lock.locked():
            operation.finish("recovery_required")
        return
    if loop is not getattr(orch, "_main_loop", loop) and getattr(orch, "_main_loop", None) is not None:
        operation.finish("recovery_required")
        return
    def retry() -> None:
        operation.timer = None
        _drive_human_failed_recovery(orch, operation)
    try:
        operation.timer = loop.call_later(0.05, retry)
    except RuntimeError:
        operation.finish("recovery_required")


def _drain_human_failed_recovery_jobs(orch: "Orchestrator", operation: _HumanFailedRecoveryOperation) -> None:
    if operation.drain is not None:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # Cold startup has no inherited opaque controls; DB settlement is the
        # authentic boundary and persisted PIDs are never signalled.
        operation.phase = "parent_handoff"
        return
    operation.disposition = "continuation_pending"
    try:
        operation.drain = _kill_jobs_for_terminating_task(
            orch, operation.task_id,
            recovery_owner=(operation.agent, operation.session_id, operation.result_id, TaskStatus.FAILED.value),
            recovery_job_ids=operation.job_ids,
            human_recovery_operation=operation,
        )
    except RuntimeError:
        operation.finish("recovery_required")


def _submit_human_failed_recovery(orch: "Orchestrator", task_id: str, agent: str,
                                 session_id: str, result_id: int, *,
                                 bookkeeping: Callable[[], None] | None = None,
                                 parent_effect: Callable[[], None] | None = None) -> _HumanFailedRecoveryOperation:
    """Retain selected identity before transferring a worker's finite tail."""
    startup_owner = _startup_sweep_owner(orch)
    if startup_owner is not None:
        startup_owner.check()
    operation = _HumanFailedRecoveryOperation(task_id, agent, session_id, result_id,
        parent_effect=parent_effect, startup_owner=startup_owner)
    if startup_owner is not None:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if getattr(orch, '_main_loop', None) is not None:
                startup_owner.negative('startup_human_loop_already_bound')
                startup_owner.raise_if_failed()
        else:
            startup_owner.negative('startup_human_requires_cold_caller')
            startup_owner.raise_if_failed()
        startup_owner.register_human(operation)
    def install() -> None:
        operations = getattr(orch, "_human_failed_recovery_operations", None)
        if operations is None:
            operations = orch._human_failed_recovery_operations = {}
        transport_key = (task_id, agent, session_id, result_id)
        existing = operations.get(transport_key)
        if existing is not None and existing.disposition == "recovery_required":
            # Settlement may already have marked the captured jobs FAILED
            # before their native drain failed or was cancelled. Retain that
            # exceptional owner/phase/controls for next-start discovery;
            # replaying settlement could mistake those rows for a zero-job
            # success. Refused callers must not add success-only bookkeeping.
            operation.finish("recovery_required")
            return
        if existing is not None and not existing.completion.done():
            if startup_owner is not None:
                # Do not adopt an unknown timer/drain or transfer its loop.
                operation.finish('recovery_required')
                return
            if bookkeeping is not None:
                existing.bookkeeping.append(bookkeeping)
            # Preserve the original phase/callback rather than redraining.
            def joined(done: Future) -> None:
                operation.finish(done.result())
            existing.completion.add_done_callback(joined)
            return
        if existing is not None and existing.disposition == "done":
            operation.phase = "caller_bookkeeping" if bookkeeping is not None else "settle_jobs"
        if bookkeeping is not None:
            operation.bookkeeping.append(bookkeeping)
        operations[transport_key] = operation
        _drive_human_failed_recovery(orch, operation)
    try:
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        loop = getattr(orch, "_main_loop", None)
        if running is not None:
            if loop is not None and running is not loop:
                operation.finish("recovery_required")
            else:
                install()
        elif loop is not None:
            if loop.is_closed() or not loop.is_running():
                operation.finish("recovery_required")
            else:
                try:
                    loop.call_soon_threadsafe(install)
                except RuntimeError:
                    operation.finish("recovery_required")
        else:
            install()
            while not operation.completion.done():
                if startup_owner is not None:
                    startup_owner.check()
                time.sleep(0.05)
                _drive_human_failed_recovery(orch, operation)
    except BaseException as exc:
        if startup_owner is not None:
            startup_owner.fail(exc)
            operation.finish('recovery_required')
        raise
    if startup_owner is not None:
        # Completion.result, phase, bookkeeping and drain are consumed; a
        # successful install/Future cannot hide a normal negative outcome.
        with startup_owner._lock:
            startup_owner._consume_humans()
        startup_owner.raise_if_failed()

    return operation


def _consume_accepted_completion_recovery(
    orch: "Orchestrator", task_id: str, report, *, agent: str, session_id: str,
    result_row_id: int,
) -> _HumanFailedRecoveryOperation | None:
    """Reconcile one ledger-selected recovery callback at the effect boundary.

    Terminal effects retain their established reconciliation.  The blocked
    branch uses a dedicated transaction because its completion audit, parked
    carrier, blocked audit, and callback-consumed ledger receipt are one
    effect.  A crash before its commit replays the immutable accepted result;
    a crash after commit leaves ordinary startup to reconstruct queue delivery.
    """
    db = orch._db
    current = db.get_task(task_id)
    if current is None:
        return
    # THR-229 C3d3c2: an accepted-recovery special branch (blocked/jobs, leaf
    # subtask, continued-same-root, non-root escalation) must NOT bypass the v2
    # decision-dispatch guard.  Classify against the REAL persisted lineage
    # BEFORE any special effect: an active/unspent reserved R2, a spent receipt,
    # a causal replay or any malformed/foreign identity on a live lineage all
    # route through the common guarded entry (which spends/claims/refuses and
    # never runs a special branch).  A classification read failure is fail-closed
    # too -- never a silent special-branch fallthrough.
    _resolved_recovery_row = _resolve_completion_result_row_id(
        db, task_id, report, result_row_id,
    )
    try:
        _recovery_context = db.authority_policy_v2_completion_dispatch_context(
            root_task_id=task_id, result_row_id=_resolved_recovery_row,
        )
        _recovery_kind = getattr(_recovery_context, "kind", None)
    except Exception:
        logger.exception(
            "run_step %s: v2 recovery classification failed", task_id,
        )
        _recovery_kind = "foreign"
    if _recovery_kind == "causal":
        # THR-229 C3d4b: the causal result R of a FINALIZED v2 continuation is
        # post-final bookkeeping only -- never the ordinary decision body.  Settle
        # the exact recovery receipt through the EXISTING public settlement writer
        # and publish the pending generation through the EXISTING authenticated
        # publisher, deriving every immutable identity from durable rows.  A
        # missing/conflicting proof refuses with the prior residue and no normal
        # effect; the causal R never spends, remints, evaluates or re-enters the
        # ordinary effect path.
        from runtime.orchestrator.authority import (
            reconcile_authority_policy_v2_post_final,
        )
        reconcile_authority_policy_v2_post_final(orch, root_task_id=task_id)
        return
    if _recovery_kind != "no_v2":
        _consume_completion_report(
            orch, task_id, report, result_row_id=_resolved_recovery_row,
            reclaim_terminal_worktree=False,
        )
        return
    if _is_human_failed_recovery(orch, current, report):
        return _submit_human_failed_recovery(orch, task_id, agent, session_id, result_row_id)
    effects_applied = (
        current.status in TERMINAL_STATES
    )
    if report.status == "blocked" and report.waiting_on_job_ids:
        import json as _json
        deduped = sorted(set(report.waiting_on_job_ids))
        # Preserve the ordinary blocked-report validation before the special
        # recovery transaction.  A missing job remains an ordinary fail-closed
        # outcome; it is not a recoverable partial receipt.
        if any(db.get_job_status(jid) is None for jid in deduped):
            _consume_completion_report(
                orch, task_id, report, result_row_id=result_row_id,
                reclaim_terminal_worktree=False,
            )
            return
        transitioned = db.consume_accepted_blocked_task_completion_recovery(
            task_id=task_id, agent=agent, session_id=session_id,
            result_row_id=result_row_id, blocked_on_job_ids=deduped,
            note=report.output_summary, completion_payload=report.model_dump(),
            settled_at=datetime.now(timezone.utc).isoformat(),
        )
        if transitioned:
            # This is deliberately post-commit.  A crash here leaves a fully
            # durable parked receipt and startup reconstructs the one wake.
            _maybe_resume_blocked_task(
                orch, task_id, trigger="block_submit", triggering_job_id=None,
            )
        return
    # A leaf completed callback has no manager decision side effects.  Make
    # its durable audit, terminal row, and accepted-result receipt one unit;
    # otherwise a crash after the independently committed audit replays it.
    if report.status == "completed" and current.task_type == "subtask":
        reviewer = None
        if current.parent_task_id is not None and not orch.teams.is_team_manager(agent):
            reviewer = _delegated_reviewer(orch, current)
        completion_payload = report.model_dump()
        try:
            human_team = orch.teams.manager_for_team(current.team).kind == "human"
        except KeyError:
            human_team = False
        if human_team:
            completion_payload.update(
                _recovery_session_id=session_id, _result_row_id=result_row_id,
            )
        root_transitions = _RootWorktreeTransitions()
        with _capture_root_worktree_transition(orch, task_id, 'consume_accepted_completed_task_completion_recovery', root_transitions):
            transitioned = db.consume_accepted_completed_task_completion_recovery(
                task_id=task_id, agent=agent, session_id=session_id,
                result_row_id=result_row_id, note=report.output_summary,
                output_dir=report.output_dir, completion_payload=completion_payload,
                settled_at=datetime.now(timezone.utc).isoformat(),
                reviewer=reviewer,
                verdict=report.verdict if report.verdict is not None else "approved",
            )
        if transitioned:
            orch._update_task_history(task_id)
            _handoff_consumed_recovery_terminal_effects(
                orch, task_id, agent, session_id, result_row_id,
                TaskStatus.COMPLETED.value,
                after_recovery_cleanup=lambda: _enqueue_parent_if_waiting(orch, task_id),
                root_transitions=root_transitions,
            )
        return
    _recovery_decision = getattr(report, "decision", None)
    _recovery_action = (
        _recovery_decision.get("action") if isinstance(_recovery_decision, dict)
        else getattr(_recovery_decision, "action", None)
    )
    # A successful authority CONTINUE_SAME_ROOT changes the root to PENDING
    # before this recovery receipt is settled.  Re-entry after a crash must
    # recognize only the committed exact causal continuation, rather than
    # rerunning the authority evaluator or guessing from status alone.
    if (
        report.status == "completed" and current.task_type == "task"
        and current.parent_task_id is None and _recovery_action == "escalate"
        and current.status is TaskStatus.PENDING
    ):
        db.reconcile_accepted_recovery_continued_same_root(
            task_id=task_id, agent=agent, session_id=session_id,
            result_row_id=result_row_id,
            completion_payload={**report.model_dump(), "_recovery_session_id": session_id,
                                "_result_row_id": result_row_id},
            settled_at=datetime.now(timezone.utc).isoformat(),
        )
        return
    # The existing defensive non-root manager branch routes escalation to its
    # parent.  Its recovery receipt must be durable with that FAILED effect,
    # rather than suppressed by the root-escalation receipt special case.
    if (
        report.status == "completed" and current.task_type == "task"
        and current.parent_task_id is not None and _recovery_action == "escalate"
    ):
        reason = getattr(_recovery_decision, "reason", None) or "Escalated"
        transitioned = db.consume_accepted_nonroot_escalation_recovery(
            task_id=task_id, agent=agent, session_id=session_id,
            result_row_id=result_row_id,
            note=f"non-root escalation requested ({reason}); routed to parent",
            completion_payload={**report.model_dump(), "_recovery_session_id": session_id,
                                "_result_row_id": result_row_id},
            settled_at=datetime.now(timezone.utc).isoformat(),
        )
        if transitioned:
            orch._update_task_history(task_id)
            _handoff_consumed_recovery_terminal_effects(
                orch, task_id, agent, session_id, result_row_id,
                TaskStatus.FAILED.value,
                after_recovery_cleanup=lambda: _enqueue_parent_if_waiting(orch, task_id),
            )
        return
    if not effects_applied:
        # An interrupted ordinary terminal transition may have committed its
        # audit before _complete.  The immutable accepted session identifies
        # that receipt; never emit a second audit while re-entering it.
        # Root recovery escalation owns its completion receipt in the same
        # final transaction as the escalation and consumed marker.  Writing it
        # here would survive a later transaction rollback and duplicate it on
        # the restart replay.
        recovery_root_escalation = (
            current.task_type == "task" and _recovery_action == "escalate"
        )
        if not recovery_root_escalation and not db.has_task_completion_report_audit(
            task_id=task_id, agent=agent, session_id=session_id,
            result_row_id=result_row_id,
        ):
            db.insert_audit_log(
                task_id=task_id, agent=agent, action="completion_report",
                payload={**report.model_dump(), "_recovery_session_id": session_id,
                         "_result_row_id": result_row_id},
            )
        _consume_completion_report(
            orch, task_id, report, result_row_id=result_row_id,
            recovery_reentry=(current.task_type == "task"),
            recovery_owner=(agent, session_id) if current.task_type == "task" else None,
            recovery_result_id=result_row_id if current.task_type == "task" else None,
            reclaim_terminal_worktree=False,
        )
        # ``run_authority_hook`` may have returned the already-authorized
        # same root to pending.  Couple its exact recovery receipt now; a
        # later startup will take the branch above if this process dies first.
        continued = db.get_task(task_id)
        if (
            current.task_type == "task" and current.parent_task_id is None
            and _recovery_action == "escalate" and continued is not None
            and continued.status is TaskStatus.PENDING
        ):
            db.reconcile_accepted_recovery_continued_same_root(
                task_id=task_id, agent=agent, session_id=session_id,
                result_row_id=result_row_id,
                completion_payload={**report.model_dump(), "_recovery_session_id": session_id,
                                    "_result_row_id": result_row_id},
                settled_at=datetime.now(timezone.utc).isoformat(),
            )
            return
        # Manager decisions keep their ordinary authority-hook and delivery
        # semantics.  A terminal decision may have committed before a crash;
        # cleanup is therefore reconstructed from the durable terminal row.
        settled = db.get_task(task_id)
        # A losing final completion CAS may observe a cancellation installed
        # immediately before it.  That cancellation owns its own cleanup and
        # parent delivery; recovery must not replay those effects.  A durable
        # completed manager-DONE effect, including crash re-entry, remains
        # reconstructible here.
        accepted = db.get_accepted_task_completion_recovery_result(
            task_id=task_id, agent=agent,
        )
        if (
            current.task_type == "task" and settled is not None
            and settled.status is TaskStatus.COMPLETED
            # A terminal row alone is not this recovery's authority.  A
            # competing owner may have completed after the final CAS lost.
            and settled.assigned_agent == agent
            and settled.current_session_id == session_id
            and accepted is not None and accepted["id"] == result_row_id
        ):
            if db.mark_task_completion_recovery_callback_consumed(
                task_id=task_id, agent=agent, session_id=session_id,
                result_row_id=result_row_id,
                settled_at=datetime.now(timezone.utc).isoformat(),
            ):
                _handoff_consumed_recovery_terminal_effects(
                    orch, task_id, agent, session_id, result_row_id,
                    TaskStatus.COMPLETED.value,
                    after_recovery_cleanup=lambda: _enqueue_parent_if_waiting(orch, task_id),
                )
    settled = db.get_task(task_id)
    if (settled is not None and settled.status is TaskStatus.COMPLETED
            and settled.assigned_agent == agent and settled.current_session_id == session_id):
        db.mark_task_completion_recovery_callback_consumed(
            task_id=task_id, agent=agent, session_id=session_id,
            result_row_id=result_row_id,
            settled_at=datetime.now(timezone.utc).isoformat(),
        )


def _is_continued_turn_decision(active_envelope, result_row_id: int | None) -> bool:
    """THR-181 Track A (founder lifecycle envelope): is this consumed report the
    continued turn's NEW decision (gate by the envelope), as opposed to a
    replay of the original escalate's immutable result row (ordinary path)?

    The envelope is bound to the original escalate's causal result row
    (``causal_event_id == "result:<row_id>"``). A decision whose result row
    is THAT row is a duplicate delivery of the already-granted escalation
    and must ride the ordinary fail-closed path (hook eligibility/claim
    CAS); a decision from ANY other row (or a row-less report arriving
    inside the continuation window) is the continued turn.
    """
    if active_envelope is None:
        return False
    causal = active_envelope["causal_event_id"] or ""
    if causal.startswith("result:"):
        try:
            causal_row = int(causal.split(":", 1)[1])
        except (ValueError, IndexError):
            causal_row = None
        if causal_row is not None and result_row_id == causal_row:
            return False
    return True


def _spend_envelope_as_violation(
    orch: "Orchestrator", db, task_id: str, agent: str, envelope,
    *, decision_family: str, error: str,
) -> None:
    """THR-181 Track A (founder lifecycle envelope): spend the single-use continuation
    envelope as ``violated`` (exactly-once CAS + atomic audit row). The
    attempted decision family is recorded bounded (never raw prose); the
    attempt is never silently discarded."""
    try:
        db.consume_authority_continue_envelope(
            envelope_id=envelope["id"],
            root_task_id=task_id,
            decision_family=decision_family,
            expected_manager_agent=agent,
            expected_session_id=envelope["manager_session_id"],
            expected_causal_event_id=envelope["causal_event_id"],
            expected_causal_event_digest=envelope["causal_event_digest"],
            expected_policy_id=envelope["policy_id"],
            expected_policy_version=envelope["policy_version"],
            expected_policy_digest=envelope["policy_digest"],
            expected_clause_id=envelope["clause_id"],
            expected_action=envelope["action"],
            audit_agent=agent,
            error=error,
            violation=True,
        )
    except Exception as exc:  # pragma: no cover - fail-closed defensive
        # A consumption write defect can never permit continuation: the
        # escalation path below still runs fail-closed.
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "envelope spend failed for %s: %s", task_id, exc,
        )


def _spend_envelope_as_consumed(
    orch: "Orchestrator", db, task_id: str, agent: str, envelope,
    *, decision_family: str,
) -> None:
    """THR-181 Track A (founder lifecycle envelope): consume the single-use
    continuation envelope as ``consumed`` (a normally validated manager decision).
    Exactly-once; a concurrent consumer that already spent it is a no-op."""
    try:
        db.consume_authority_continue_envelope(
            envelope_id=envelope["id"],
            root_task_id=task_id,
            decision_family=decision_family,
            expected_manager_agent=agent,
            expected_session_id=envelope["manager_session_id"],
            expected_causal_event_id=envelope["causal_event_id"],
            expected_causal_event_digest=envelope["causal_event_digest"],
            expected_policy_id=envelope["policy_id"],
            expected_policy_version=envelope["policy_version"],
            expected_policy_digest=envelope["policy_digest"],
            expected_clause_id=envelope["clause_id"],
            expected_action=envelope["action"],
            audit_agent=agent,
        )
    except Exception as exc:  # pragma: no cover - fail-closed defensive
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "envelope consume failed for %s: %s", task_id, exc,
        )


def _escalate_continued_turn_violation(
    orch: "Orchestrator", task_id: str, agent: str, *, attempted: str,
    last_summary: str = "",
) -> None:
    """THR-181 Track A: fail closed when a continued turn attempts to replace
    its same root, using the EXISTING ordinary
    founder-escalation path (``try_escalate`` CAS -> escalation audit row ->
    notification -> thread projection), with a server-derived reason. The
    authority hook is deliberately not used to authorize replacement."""
    db = orch._db
    reason = (
        f"authority-continuation envelope violation: continued turn "
        f"produced {attempted!r} outside the single-use permitted action; "
        f"escalated to founder"
    )
    if not db.try_escalate_runtime(
        task_id,
        reason=reason,
        agent=agent,
        reason_code="runtime_authority_envelope_violation",
    ):
        # Cancellation/terminal won the race — the founder's state wins.
        return
    orch.notify_escalated(
        task_id=task_id, agent=agent, reason=reason, last_summary=last_summary,
    )
    _maybe_post_thread_escalation(orch, task_id, reason=reason)


def _resolve_completion_result_row_id(db, task_id: str, report, result_row_id):
    """Resolve the immutable ``task_results`` row this report was built from."""
    if result_row_id is None and report is not None:
        row = db.execute(
            "SELECT id FROM task_results WHERE task_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row is not None:
            result_row_id = row["id"]
    return result_row_id


# THR-229 checkpoint C3d3c2: the guarded common-consumer decision dispatch.
# The consumed continuation envelope carrying a ``spending_result_id`` IS the
# result-keyed decision receipt (R2).  Its closed ``decision_state`` is the
# durable single-use token: ONLY the winning ``ready -> claimed`` claim (made by
# this guard, through the public storage writer) authorizes exactly ONE entry
# into the EXISTING ordinary decision body below.  A duplicate/restarted
# ``claimed`` receipt, an ``applied``/``refused`` receipt, a corrupt/conflicting
# identity or a malformed/foreign report NEVER reaches the ordinary body, and a
# missing/mistyped identity on a root with a retired v2 generation is never
# ordinary-path permission.
_V2_DECISION_DISPATCH_ORDINARY = "ordinary"
_V2_DECISION_DISPATCH_ADMITTED = "admitted"
_V2_DECISION_DISPATCH_SKIP = "skip"


class _V2DecisionDispatchOutcome(NamedTuple):
    """The winning caller's exact identity for acknowledgement/refusal.

    ``manager_agent``/``causal_result_id`` are the CAUSAL receipt identity the
    claim was made with -- never the reserved spending result id and never
    whichever task/owner happens to be current after the consumer effect.
    """
    kind: str
    manager_agent: str | None = None
    causal_result_id: int | None = None


def _v2_report_binds(db, task_id, spending_result_id, report) -> bool:
    if spending_result_id is None:
        return False
    try:
        return db.authority_policy_v2_decision_result_report_binds(
            root_task_id=task_id, spending_result_id=spending_result_id,
            report=report,
        )
    except Exception:
        return False


def _v2_decision_dispatch_gate(
    orch: "Orchestrator", task_id: str, report, result_row_id, agent: str,
) -> _V2DecisionDispatchOutcome:
    """Classify the report against the REAL persisted v2 lineage.

    Returns an outcome whose ``kind`` is ``ordinary`` for a provably no-v2/v1
    path or for a GENUINE later result on a fully terminal v2 lineage (whose
    supplied report must match that result's persisted material identity),
    ``admitted`` only for the uninterrupted winning claim (one consumer
    entry), and ``skip`` for every other outcome -- causal continuation replay,
    a duplicate/restarted/refused receipt, a foreign or malformed identity, a
    drifted later report or a lookup failure -- after performing the
    interruption-refusal bookkeeping where required.  This is the production
    authority rule: reader absence or a mock's ``None`` is never ordinary
    permission.
    """
    db = orch._db
    skip = _V2DecisionDispatchOutcome(_V2_DECISION_DISPATCH_SKIP)
    try:
        context = db.authority_policy_v2_completion_dispatch_context(
            root_task_id=task_id, result_row_id=result_row_id,
        )
    except Exception:
        logger.exception("run_step %s: v2 completion classification failed", task_id)
        return skip
    kind = getattr(context, "kind", None)
    if kind == "no_v2":
        return _V2DecisionDispatchOutcome(_V2_DECISION_DISPATCH_ORDINARY)
    if kind == "later":
        # A genuine later result on a FULLY TERMINAL v2 lineage is ordinary ONLY
        # when the supplied report IS that exact result's persisted material
        # body.  The classifier already proved the session/owner provenance and
        # that the row is genuinely later than the terminal evidence; an
        # unrelated/older row or a drifted report is never ordinary permission.
        if not _v2_report_binds(db, task_id, result_row_id, report):
            logger.warning("run_step %s: v2 later report identity conflict", task_id)
            return skip
        return _V2DecisionDispatchOutcome(_V2_DECISION_DISPATCH_ORDINARY)
    if kind == "causal":
        # The causal result R of a v2 attempt is continuation bookkeeping only:
        # it never spends, remints, evaluates or re-enters the normal effect.
        logger.info("run_step %s: v2 causal continuation replay", task_id)
        return skip
    manager_agent = getattr(context, "manager_agent", None) or agent
    causal_result_id = getattr(context, "causal_result_id", None)
    if kind == "reserved":
        # The exact active reserved next result R2: atomically spend through the
        # EXISTING writer, then claim exactly once.  A failed spend/claim yields
        # ZERO consumer entry.
        spending_result_id = getattr(context, "spending_result_id", None)
        if not _v2_report_binds(db, task_id, spending_result_id, report):
            logger.warning("run_step %s: v2 reserved report identity conflict", task_id)
            return skip
        spend = db.spend_authority_policy_v2_continue_envelope(
            root_task_id=task_id, manager_agent=manager_agent,
            manager_session_id=getattr(context, "manager_session_id", None),
            result_id=causal_result_id,
            generation_id=getattr(context, "generation_id", None),
            next_session_id=getattr(context, "next_session_id", None),
            spending_result_id=spending_result_id,
        )
        if getattr(spend, "status", None) not in ("spent", "already_spent_exact"):
            logger.warning(
                "run_step %s: v2 reserved spend refused (%s)",
                task_id, getattr(spend, "status", None),
            )
            return skip
        claim = db.claim_authority_policy_v2_decision_dispatch(
            root_task_id=task_id, manager_agent=manager_agent,
            result_id=causal_result_id,
        )
        if getattr(claim, "status", None) == "claimed":
            return _V2DecisionDispatchOutcome(
                _V2_DECISION_DISPATCH_ADMITTED, manager_agent, causal_result_id,
            )
        return skip
    if kind == "receipt":
        if not isinstance(causal_result_id, int) or isinstance(causal_result_id, bool):
            return skip
        state = getattr(context, "decision_state", None)
        if state == "ready":
            if not _v2_report_binds(
                db, task_id, getattr(context, "spending_result_id", None), report,
            ):
                logger.warning(
                    "run_step %s: v2 decision report identity conflict", task_id,
                )
                return skip
            claim = db.claim_authority_policy_v2_decision_dispatch(
                root_task_id=task_id, manager_agent=manager_agent,
                result_id=causal_result_id,
            )
            if getattr(claim, "status", None) == "claimed":
                return _V2DecisionDispatchOutcome(
                    _V2_DECISION_DISPATCH_ADMITTED, manager_agent, causal_result_id,
                )
            return skip
        if state == "claimed":
            # Restart after a committed claim (before the effect, after an
            # effect or after a failed acknowledgement): audited refusal
            # bookkeeping with the EXACT causal identity, never a second
            # consumer.
            try:
                refusal = db.refuse_authority_policy_v2_decision_dispatch(
                    root_task_id=task_id, manager_agent=manager_agent,
                    result_id=causal_result_id,
                )
                if getattr(refusal, "status", None) != "refused":
                    logger.warning(
                        "run_step %s: v2 decision refusal pending (%s)",
                        task_id, getattr(refusal, "reason", None),
                    )
            except Exception:
                logger.exception("run_step %s: v2 decision refusal failed", task_id)
            return skip
        # ``applied``/``refused`` are terminal: read-only, never a second consumer.
        return skip
    # ``foreign``/unknown: an unmatched identity on a live lineage is never
    # ordinary permission.
    return skip


def _consume_completion_report(
    orch: "Orchestrator", task_id: str, report,
    *, result_row_id: int | None = None, recovery_reentry: bool = False,
    recovery_owner: tuple[str, str] | None = None,
    recovery_result_id: int | None = None,
    reclaim_terminal_worktree: bool = True,
    after_human_recovery: Callable[[], None] | None = None,
) -> _HumanFailedRecoveryOperation | str | None:
    """Guard the common consumer with the v2 decision-dispatch receipt.

    This is the single common entry every ordinary completion, accepted
    recovery, startup sweep and zombie-reaper caller uses.  Before ANY normal
    task mutation/orchestration audit/decision/delegate/enqueue effect it
    classifies the exact result-keyed receipt: an uninterrupted winning claim
    runs the EXISTING normal decision body exactly once and then acknowledges
    ``claimed -> applied``; every other v2 outcome skips the body (performing
    audited interruption refusal for a restarted ``claimed`` receipt).  A
    provably ordinary/v1 result is unchanged.
    """
    db = orch._db
    task = db.get_task(task_id)
    if task is None:
        return
    from runtime.workflows.recovery import classify_task
    if classify_task(db, task_id, org_slug=getattr(orch, "_slug", None)).kind != "legacy":
        # A result is transport evidence only. The draft owner joins exact
        # accepted result and actual host quiescence; no manager decision tail.
        return
    agent = task.assigned_agent or "unknown"
    reclaim_kwargs = (
        {} if reclaim_terminal_worktree
        else {"reclaim_terminal_worktree": False}
    )
    resolved_row_id = _resolve_completion_result_row_id(db, task_id, report, result_row_id)
    # K3a is selected before any existing v2 result-action spending. The
    # production owner refuses missing downstream readiness with zero mutation.
    decision = orch._parse_next_step(report) if task.task_type == "task" else None
    if decision is not None and decision.action == "workflow_start":
        from runtime.workflows.coding_start import CodingStartError
        coding = getattr(orch, "_workflow_coding_start", None)
        if coding is None:
            logger.warning("coding start unavailable for %s", task_id)
            return
        try:
            coding.consume(RetryClaim.from_task(task, result_row_id=resolved_row_id), decision, report)
        except CodingStartError as exc:
            logger.warning("coding start held for %s: %s", task_id, exc.code)
        return
    outcome = _v2_decision_dispatch_gate(orch, task_id, report, resolved_row_id, agent)
    if outcome.kind == _V2_DECISION_DISPATCH_SKIP:
        return
    if outcome.kind == _V2_DECISION_DISPATCH_ORDINARY:
        if _is_human_failed_recovery(orch, task, report):
            accepted = db.selected_human_leaf_recovery_result(task_id=task_id, agent=agent,
                session_id=task.current_session_id or "")
            if accepted is not None:
                if accepted["id"] != resolved_row_id:
                    return "lost_owner"
                return _submit_human_failed_recovery(orch, task_id, agent, task.current_session_id or "", resolved_row_id,
                    bookkeeping=after_human_recovery)
        _consume_completion_report_body(
            orch, task_id, report, result_row_id=resolved_row_id,
            recovery_reentry=recovery_reentry, recovery_owner=recovery_owner,
            recovery_result_id=recovery_result_id,
            **reclaim_kwargs,
        )
        return
    # ADMITTED: the winning claim authorizes exactly one consumer entry.  The
    # SAME caller runs the EXISTING body outside any DB transaction once, then
    # acknowledges the EXACT causal receipt it claimed.
    manager_agent = outcome.manager_agent or agent
    causal_result_id = outcome.causal_result_id
    try:
        _consume_completion_report_body(
            orch, task_id, report, result_row_id=resolved_row_id,
            recovery_reentry=recovery_reentry, recovery_owner=recovery_owner,
            recovery_result_id=recovery_result_id,
            **reclaim_kwargs,
        )
    except BaseException:
        # On exception, attempt the prescribed refusal with that SAME causal
        # identity; already committed effects are preserved.
        try:
            refusal = db.refuse_authority_policy_v2_decision_dispatch(
                root_task_id=task_id, manager_agent=manager_agent,
                result_id=causal_result_id,
            )
            if getattr(refusal, "status", None) != "refused":
                logger.warning(
                    "run_step %s: v2 decision refusal not settled (%s)",
                    task_id, getattr(refusal, "reason", None),
                )
        except Exception:  # pragma: no cover - fail-closed defensive
            logger.exception("run_step %s: v2 decision refusal failed", task_id)
        raise
    # A bounded ack/audit failure leaves the receipt ``claimed``; a later restart
    # performs refusal housekeeping and never re-runs the consumer.
    try:
        ack = db.acknowledge_authority_policy_v2_decision_dispatch(
            root_task_id=task_id, manager_agent=manager_agent,
            result_id=causal_result_id,
        )
        if getattr(ack, "status", None) not in ("applied", "already_applied_exact"):
            logger.warning(
                "run_step %s: v2 decision acknowledgement not settled (%s)",
                task_id, getattr(ack, "reason", None),
            )
    except Exception:  # pragma: no cover - fail-closed defensive
        logger.exception("run_step %s: v2 decision acknowledgement failed", task_id)


def _consume_completion_report_body(
    orch: "Orchestrator", task_id: str, report,
    *, result_row_id: int | None = None, recovery_reentry: bool = False,
    recovery_owner: tuple[str, str] | None = None,
    recovery_result_id: int | None = None,
    reclaim_terminal_worktree: bool = True,
) -> None:
    """Consume a persisted CompletionReport and apply its transition.

    Used both inline in ``run_step_impl`` (after ``_log_step_result``) and
    from the boot sweep when an orphaned (unconsumed) ``task_result`` row is
    discovered for a task whose executor process died mid-turn.

    ``result_row_id`` is the immutable ``task_results`` row id that produced
    ``report`` — the authority hook binds its candidate identity to it so a
    restart replay cannot mint a second candidate/evaluation. When omitted it
    is resolved deterministically to the latest task_results row for the
    task (the row every shipping call site built the report from).

    Re-fetches the task from the DB so it works from any call site (the
    inline ``run_step_impl`` call site already has the task in scope, but
    the sweep does not). Semantic zero-delta extract — the body of this
    function was formerly the tail of ``run_step_impl``.
    """
    db = orch._db
    task = db.get_task(task_id)
    if task is None:
        return
    agent = task.assigned_agent or "unknown"
    reclaim_kwargs = (
        {} if reclaim_terminal_worktree
        else {"reclaim_terminal_worktree": False}
    )
    # The retry claim binds the result supplied for this report. The authority
    # hook's legacy latest-row lookup below may find earlier synthetic feedback
    # when a scripted executor returns a report without persisting a result.
    expected_claim = RetryClaim.from_task(task, result_row_id=result_row_id)
    # THR-181 Track A (founder lifecycle envelope): the single-use continuation
    # envelope, when ACTIVE, marks this consumption as the continued turn's
    # decision — PROVIDED the consumed report is NOT a replay of the original
    # escalate's immutable result row (the envelope's causal event). A replay
    # of the same row is a duplicate delivery of the already-granted
    # escalate and must ride the ordinary fail-closed path (the hook's
    # eligibility/claim CAS), never the envelope gate.
    active_envelope = db.get_active_authority_continue_envelope(task_id)
    if result_row_id is None and report is not None:
        row = db.execute(
            "SELECT id FROM task_results WHERE task_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        if row is not None:
            result_row_id = row["id"]
    # ``orchestration_step_count`` was already incremented by
    # ``try_claim_for_step`` at the top of ``run_step_impl``; use its
    # current value (not +1) for audit-log keying.
    next_count = task.orchestration_step_count

    if report.status == "blocked":
        if _is_continued_turn_decision(active_envelope, result_row_id):
            _spend_envelope_as_consumed(
                orch, db, task_id, agent, active_envelope,
                decision_family="blocked",
            )
        if report.waiting_on_job_ids:
            # Spec §5.3: block-on-jobs branch. In-place transition, NOT _fail.
            import json as _json
            deduped = sorted(set(report.waiting_on_job_ids))
            # Defensive re-validation: a job could have been deleted between the
            # route POST and run_step_impl consuming the report (extremely
            # unlikely; jobs are write-once + terminal-frozen). Degrade gracefully.
            for jid in deduped:
                if db.get_job_status(jid) is None:
                    note = f"self-blocked but job {jid} not found"
                    _fail(
                        orch, task_id, note=note,
                        **reclaim_kwargs,
                    )
                    _enqueue_parent_if_waiting(orch, task_id)
                    _maybe_post_thread_followup(
                        orch, task_id,
                        status=TaskStatus.FAILED, auto_revisit_spawned=False,
                    )
                    return
            # Path B: a task waiting on jobs it submitted is in_progress, with
            # the waiting reason kept in block_kind.
            db.update_task(
                task_id,
                status=TaskStatus.IN_PROGRESS,
                block_kind=BlockKind.BLOCKED_ON_JOB,
                blocked_on_job_ids=_json.dumps(deduped),
                note=report.output_summary,
            )
            orch._audit.log_task_blocked_on_jobs(
                task_id=task_id, agent=agent,
                blocking_job_ids=deduped,
                output_summary_excerpt=(report.output_summary or "")[:200],
            )
            # Immediate predicate check (caller B). Spec §5.6: runs HERE, after
            # the agent session has already been cleared by submit_completion.
            # No session race.
            _maybe_resume_blocked_task(
                orch, task_id,
                trigger="block_submit", triggering_job_id=None,
            )
            return
        # Existing escalated path (waiting_on_job_ids empty).
        note = f"self-blocked: {report.output_summary}"
        _fail(
            orch, task_id, note=note,
            **reclaim_kwargs,
        )
        _enqueue_parent_if_waiting(orch, task_id)
        _maybe_post_thread_followup(
            orch, task_id,
            status=TaskStatus.FAILED, auto_revisit_spawned=False,
        )
        return

    # ---- 6. Parse next step ----
    # Orchestration is driven by task TYPE, not manager role. A type=task
    # owner (any agent) speaks the NextStep protocol; a type=subtask is
    # leaf-only. `task` is the early-fetched record; task_type is immutable
    # provenance, safe to read post-claim.
    if task.task_type == "task":
        decision = orch._parse_next_step(report)
        # A persisted orchestration step number is the durable idempotency key
        # for every consumer entry, not only callers that happened to label
        # themselves as recovery.  Startup and zombie recovery rebuild the
        # same report row but historically did not both propagate
        # ``recovery_reentry``; never append a second decision audit for the
        # already-recorded step.
        if db.has_orchestration_step_audit(
            task_id=task_id, step_number=next_count,
        ):
            _step_audit_id = None
        else:
            _step_audit_id = orch._audit.log_orchestration_step(
                task_id, next_count, decision.model_dump(exclude_none=True),
            )
    else:
        from runtime.models import NextStep
        decision = NextStep(action="done", summary=report.output_summary)
        _step_audit_id = None

    # The envelope is a single-use lifecycle/causality receipt. It does not
    # whitelist an exact manager action: after normal parsing, the continued
    # turn follows the ordinary manager-decision validation and dispatch path.
    # Independent same-root identity, cancellation, CAS, budget, and protected-
    # boundary fences remain daemon-owned and non-overridable.
    if _is_continued_turn_decision(active_envelope, result_row_id):
        if decision.action == "supersede":
            _spend_envelope_as_violation(
                orch, db, task_id, agent, active_envelope,
                decision_family=decision.action,
                error="continued turn attempted to replace the same root",
            )
            _escalate_continued_turn_violation(
                orch, task_id, agent, attempted=decision.action,
                last_summary=report.output_summary,
            )
            return
        _spend_envelope_as_consumed(
            orch, db, task_id, agent, active_envelope,
            decision_family=decision.action,
        )

    # ---- 7. Dispatch on action ----
    if decision.action == "done":
        if recovery_owner is None:
            completed = _complete(
                orch, task_id,
                note=decision.summary or report.output_summary,
                output_dir=report.output_dir,
                **reclaim_kwargs,
            )
        else:
            completed = _complete(orch, task_id, note=decision.summary or report.output_summary,
                                  output_dir=report.output_dir,
                                  recovery_owner=(*recovery_owner, recovery_result_id),
                                  after_recovery_cleanup=lambda: _enqueue_parent_if_waiting(
                                      orch, task_id),
                                  after_recovery_parent_effect=lambda: _maybe_post_thread_followup(
                                      orch, task_id,
                                      status=TaskStatus.COMPLETED,
                                      auto_revisit_spawned=False,
                                  ),
                                  **reclaim_kwargs)
        if not completed:
            return
        if recovery_owner is None:
            _enqueue_parent_if_waiting(orch, task_id)
            _maybe_post_thread_followup(
                orch, task_id,
                status=TaskStatus.COMPLETED, auto_revisit_spawned=False,
            )
        return

    if decision.action == "escalate":
        reason = decision.reason or "Escalated"
        if not is_root(task):
            # THR-033 Change A defensive guard: a non-root task never escalates
            # directly to the founder — it fails and hands back to its parent
            # (bounded failure-recovery carries it up). Manager-owned fan-out
            # and chained children make this a live shipping guard.
            _fail(
                orch, task_id,
                note=f"non-root escalation requested ({reason}); routed to parent",
                **reclaim_kwargs,
            )
            _enqueue_parent_if_waiting(orch, task_id)
            _maybe_post_thread_followup(
                orch, task_id,
                status=TaskStatus.FAILED, auto_revisit_spawned=False,
            )
            return
        # Root: park in escalated for the founder (try_escalate* now writes the
        # top-level ESCALATED status; behavior otherwise unchanged).
        # THR-181 Track A: BEFORE the manager root's proposed escalation is
        # committed, run exactly one audited LLM authority evaluation of the
        # proposed reason against the release-controlled policy for this
        # team (Engineering v1). The hook returns "continue_same_root" only
        # when the policy's narrow continue clause matched and the named
        # same-root permitted action was executed + audited atomically;
        # EVERY other outcome — ambiguity, malformed/missing/unknown output,
        # timeout/provider error, policy/team/digest mismatch, audit failure,
        # cancellation, exhausted limits, stale/CAS conflict, restart-
        # incomplete state, ineligibility, or any successor/supersede/
        # revisit/fresh-root signal — fails closed to "escalate", which
        # proceeds through the exact existing escalation path below.
        from runtime.orchestrator.authority import run_authority_hook
        hook_outcome = run_authority_hook(
            orch, task, agent, reason, result_row_id,
            manager_self_evaluation=report.manager_self_evaluation,
        )
        if hook_outcome == "continue_same_root":
            # Same-root continuation already executed (audited): the root
            # returned to pending for its next manager decision step and was
            # re-enqueued. The escalation is NOT committed.
            return
        if hook_outcome == "v2_continued":
            # The v2 final continuation committed (post-final settlement or
            # publication may still be pending); no ordinary escalation runs.
            return
        if hook_outcome == "v2_refused":
            refusal_status = getattr(hook_outcome, "status", None)
            task_disposition = getattr(hook_outcome, "task_disposition", None)
            refused_task = orch._db.get_task(task_id)
            expected_prefix = "authority_v2_refusal:"
            if (
                refusal_status == "refused"
                and task_disposition == "failed"
                and refused_task is not None
                and refused_task.parent_task_id is not None
                and refused_task.status == TaskStatus.FAILED
                and refused_task.completed_at is not None
                and (refused_task.note or "").startswith(expected_prefix)
            ):
                expected_note = refused_task.note or ""
                if recovery_owner is None:
                    _fail_terminal_tail(
                        orch,
                        task_id,
                        expected_note=expected_note,
                        reclaim_terminal_worktree=reclaim_kwargs.get(
                            "reclaim_terminal_worktree", True,
                        ),
                    )
                    if _task_matches_authority_v2_refusal_failure(
                        orch, task_id, expected_note,
                    ):
                        _enqueue_parent_if_waiting(orch, task_id)
                    if _task_matches_authority_v2_refusal_failure(
                        orch, task_id, expected_note,
                    ):
                        _maybe_post_thread_followup(
                            orch,
                            task_id,
                            status=TaskStatus.FAILED,
                            auto_revisit_spawned=False,
                        )
                else:
                    _handoff_consumed_recovery_terminal_effects(
                        orch,
                        task_id,
                        recovery_owner[0],
                        recovery_owner[1],
                        recovery_result_id,
                        TaskStatus.FAILED.value,
                        after_recovery_cleanup=lambda: (
                            _enqueue_parent_if_waiting(orch, task_id)
                            if _task_matches_authority_v2_refusal_failure(
                                orch, task_id, expected_note,
                            )
                            else None
                        ),
                    )
                return
            if (
                refusal_status == "refused"
                and task_disposition == "escalated"
                and refused_task is not None
                and refused_task.parent_task_id is None
                and refused_task.status == TaskStatus.ESCALATED
                and refused_task.completed_at is None
                and (refused_task.note or "").startswith(expected_prefix)
            ):
                # Root refusal retains the pre-existing escalation projection.
                orch.notify_escalated(
                    task_id=task_id, agent=agent, reason=reason,
                    last_summary=getattr(report, "output_summary", "") or "",
                )
                _maybe_post_thread_escalation(orch, task_id, reason=reason)
            return
        # A bounded v2_pending outcome means refusal housekeeping did not
        # commit.  Failing closed must still terminate and surface the root, so
        # continue through the ordinary exactly-once escalation CAS below.  A
        # later refusal-housekeeping discovery may close the retained attempt;
        # it cannot re-run this consumer once the task is already escalated.
        # Atomic CAS: transition to ESCALATED only if not cancelled
        # or terminal. Closes the post-_is_already_terminal race (Codex P2 on
        # PR #34) by serializing against /cancel via the Database RLock.
        # If False: founder cancellation landed between Guard B's re-fetch and
        # here. Drop the escalate silently — the founder's terminal state wins.
        recovery_escalation = recovery_owner is not None
        # Keep the ordinary Database contract (task_id + reason) intact.
        # Recovery metadata is a separate, server-authorized transaction
        # extension and must never leak into ordinary callers or their
        # wrappers.
        escalate_kwargs: dict = {"reason": reason}
        if recovery_escalation:
            escalate_kwargs.update(
                recovery_owner=recovery_owner,
                recovery_result_id=result_row_id,
                recovery_completion_payload={
                    **report.model_dump(), "_recovery_session_id": recovery_owner[1],
                    "_result_row_id": result_row_id,
                },
                recovery_settled_at=datetime.now(timezone.utc).isoformat(),
            )
        if not db.try_escalate(task_id, **escalate_kwargs):
            logger.debug(
                "run_step %s: cancelled between re-check and escalate, dropping",
                task_id,
            )
            return
        # The recovery-aware CAS durably included this audit with its exact
        # accepted-result receipt.  Ordinary consumers retain their existing
        # independently committed audit path.
        if not recovery_escalation:
            orch._audit.log_escalation(task_id, agent, reason)
        orch.notify_escalated(
            task_id=task_id, agent=agent, reason=reason,
            last_summary=getattr(report, "output_summary", "") or "",
        )
        _maybe_post_thread_escalation(orch, task_id, reason=reason)
        # parent stays in_progress(delegated) until this task reaches a terminal.
        return

    if decision.action == "supersede":
        # The decision model rejects every caller-supplied identity/target field;
        # the database method re-checks this current claimed root under its lock.
        try:
            expected_manager = orch.teams.manager_for_team(task.team).name
        except (KeyError, ValueError):
            expected_manager = None
        if expected_manager != agent or task.current_session_id is None:
            _fail(
                orch, task_id,
                note="manager supersession claim is not current",
                **reclaim_kwargs,
            )
            _enqueue_parent_if_waiting(orch, task_id)
            _maybe_post_thread_followup(
                orch, task_id, status=TaskStatus.FAILED, auto_revisit_spawned=False,
            )
            return
        if task.parent_task_id is not None:
            # THR-277 PR3: non-root is its own refusal reason.  Do not call
            # try_manager_supersede and reinterpret its generic None result;
            # that result also covers unrelated root races/ineligibility.
            # The atomic writer rechecks this exact current claim.  A stale,
            # cancelled, blocked, terminal, or replaced owner therefore gets
            # no failure and no parent wake.
            note = f"manager supersede refused: non-root task {task_id}"
            decision_session_id = task.current_session_id
            if recovery_owner is not None:
                decision_session_id = recovery_owner[1]
            elif result_row_id is not None:
                result_owner = db.execute(
                    "SELECT agent, session_id FROM task_results "
                    "WHERE id = ? AND task_id = ?",
                    (result_row_id, task_id),
                ).fetchone()
                if result_owner is not None and result_owner["agent"] == agent:
                    # Bind the terminal decision to the session that produced
                    # its immutable result, never to a replacement owner from
                    # the fresh task read above.
                    decision_session_id = result_owner["session_id"]
            if not isinstance(decision_session_id, str) or not decision_session_id:
                return
            if db.try_fail_nonroot_manager_supersede(
                task_id,
                actor_agent=agent,
                actor_session_id=decision_session_id,
                expected_team=task.team,
                note=note,
            ):
                if recovery_owner is None:
                    _fail_terminal_tail(
                        orch,
                        task_id,
                        expected_note=note,
                        reclaim_terminal_worktree=reclaim_kwargs.get(
                            "reclaim_terminal_worktree", True,
                        ),
                    )
                    if _task_matches_authority_v2_refusal_failure(
                        orch, task_id, note,
                    ):
                        _enqueue_parent_if_waiting(orch, task_id)
                    if _task_matches_authority_v2_refusal_failure(
                        orch, task_id, note,
                    ):
                        _maybe_post_thread_followup(
                            orch,
                            task_id,
                            status=TaskStatus.FAILED,
                            auto_revisit_spawned=False,
                        )
                else:
                    _handoff_consumed_recovery_terminal_effects(
                        orch,
                        task_id,
                        recovery_owner[0],
                        recovery_owner[1],
                        recovery_result_id,
                        TaskStatus.FAILED.value,
                        after_recovery_cleanup=lambda: (
                            _enqueue_parent_if_waiting(orch, task_id)
                            if _task_matches_authority_v2_refusal_failure(
                                orch, task_id, note,
                            )
                            else None
                        ),
                    )
            return
        if task.dispatched_from_thread_id:
            reason = (
                "manager supersession rejected: thread-origin roots are not "
                "eligible for supersession; founder action required"
            )
            if db.try_reject_thread_origin_manager_supersede(
                task_id,
                actor_agent=agent,
                actor_session_id=task.current_session_id,
                expected_team=task.team,
                reason=reason,
                reason_code=(
                    "runtime_manager_supersession_thread_origin_ineligible"
                ),
            ):
                orch.notify_escalated(
                    task_id=task_id,
                    agent=agent,
                    reason=reason,
                    last_summary=report.output_summary,
                )
                _maybe_post_thread_escalation(orch, task_id, reason=reason)
            # A cancellation or another terminal consumer may have won the
            # CAS. In either case, never reinterpret that race as permission
            # to create a successor.
            return
        root_transitions = _RootWorktreeTransitions()
        with _capture_root_worktree_transition(orch, task_id, 'try_manager_supersede', root_transitions):
            successor_id = db.try_manager_supersede(
                task_id,
                actor_agent=agent,
                actor_session_id=task.current_session_id,
                expected_team=task.team,
                successor_brief=decision.successor_brief or "",
                rationale=decision.rationale or "",
                attestation=decision.attestation.model_dump() if decision.attestation else {},
            )
        # Root-only policy is unchanged: a generic None is a silent refusal;
        # the writer's stale-claim RuntimeError rolls back and propagates.  It
        # is never reclassified as the non-root fail-and-wake case above.
        if successor_id is None:
            # The claim may have been superseded by cancellation or a competing
            # consumer.  Never cancel/alter live work just to make it eligible.
            return
        try:
            _enqueue_task_generation_aware(orch, successor_id)
        except Exception:
            # The committed successor remains pending. Startup recovery
            # idempotently re-enqueues pending tasks; never reopen predecessor.
            logger.exception("manager supersession %s enqueue failed", successor_id)
        finally:
            _root_worktree_batch(orch, root_transitions)
        return

    if decision.action == "fanout":
        # Phase 1: read-only native fan-out core.
        # Validate structural correctness (width, cap ack, no per-child then/verdict).
        from runtime.orchestrator.fanout import (
            MAX_FANOUT_WIDTH,
            FanoutState,
            fanout_child_targets,
            validate_fanout_decision,
        )
        err = validate_fanout_decision(decision)
        if err is not None:
            note = f"invalid fanout: {err}"
            _fail(
                orch, task_id, note=note,
                **reclaim_kwargs,
            )
            _enqueue_parent_if_waiting(orch, task_id)
            _maybe_post_thread_followup(
                orch, task_id,
                status=TaskStatus.FAILED, auto_revisit_spawned=False,
            )
            return

        # Validate each child's workspace exists (reuse _validate_one_leg).
        reviewer_agents = _reviewer_agents_for(orch)
        for i, child in enumerate(decision.children):
            child_err = _validate_one_leg(
                orch, agent=child.agent, where=f"fanout child {i + 1}",
            )
            if child_err is not None:
                note = f"invalid fanout: {child_err}"
                _fail(
                    orch, task_id, note=note,
                    **reclaim_kwargs,
                )
                _enqueue_parent_if_waiting(orch, task_id)
                _maybe_post_thread_followup(
                    orch, task_id,
                    status=TaskStatus.FAILED, auto_revisit_spawned=False,
                )
                return
            # THR-175: a pipeline-carrier first leg that is a configured
            # reviewer must declare expect_verdict=APPROVE (only when the child
            # actually carries a downstream ``then`` chain). Omission is a
            # HARD REJECT — a recoverable authoring error, fed back to the
            # owner for correction (never a root failure).
            if child.then and child.agent in reviewer_agents and child.expect_verdict is None:
                _reject_reviewer_omission(
                    orch, task_id, agent, next_count,
                    _reviewer_omitted_expectation_error(child.agent),
                    action_label="fan-out",
                )
                return
            # Validate carrier chain legs for pipeline children (Phase 2).
            for j, leg in enumerate(child.then):
                leg_err = _validate_one_leg(
                    orch, agent=leg.agent,
                    where=f"fanout child {i + 1} then leg {j + 1}",
                )
                if leg_err is not None:
                    note = f"invalid fanout: {leg_err}"
                    _fail(
                        orch, task_id, note=note,
                        **reclaim_kwargs,
                    )
                    _enqueue_parent_if_waiting(orch, task_id)
                    _maybe_post_thread_followup(
                        orch, task_id,
                        status=TaskStatus.FAILED, auto_revisit_spawned=False,
                    )
                    return
                if leg.agent in reviewer_agents and leg.expect_verdict is None:
                    _reject_reviewer_omission(
                        orch, task_id, agent, next_count,
                        _reviewer_omitted_expectation_error(leg.agent),
                        action_label="fan-out",
                    )
                    return

        # Scope validation: manager own-team/self only; non-manager self-only.
        # Reuse _legs_out_of_scope with the fan-out child targets.
        targets = fanout_child_targets(decision)
        out_of_scope: list[tuple[str, str]] = []
        if orch.teams.is_team_manager(agent):
            caller_team = orch.teams.team_for_manager(agent)
            for a in targets:
                if not a or a == agent:
                    continue
                t = orch.teams.team_for_agent(a)
                if caller_team is None or t != caller_team:
                    out_of_scope.append((a, f"on team {t!r}" if t else "not on a team"))
        else:
            for a in targets:
                if not a or a == agent:
                    continue
                out_of_scope.append(
                    (a, "non-manager owners may only delegate to themselves"),
                )
        if out_of_scope:
            parts = [f"{name!r} ({reason})" for name, reason in out_of_scope]
            if orch.teams.is_team_manager(agent):
                caller_team = orch.teams.team_for_manager(agent)
                feedback = (
                    f"Invalid fan-out: you are on team {caller_team!r}, but "
                    f"{'; '.join(parts)}. Pick agents on your own team or "
                    "yourself, or escalate."
                )
            else:
                feedback = (
                    f"Invalid fan-out: {'; '.join(parts)}. You may only "
                    f"fan-out sub-tasks to yourself ({agent!r}), or escalate."
                )
            db.insert_task_result(
                task_id=task_id,
                agent=agent,
                session_id="",
                status="completed",
                confidence_score=0,
                output_summary=feedback,
                risks_flagged=[],
            )
            orch._audit.log_orchestration_step(
                task_id, next_count, {"action": "feedback", "reason": feedback},
            )
            db.update_task(task_id, status=TaskStatus.PENDING, block_kind=None)
            _enqueue_task_generation_aware(orch, task_id)
            return

        for i, child in enumerate(decision.children):
            retry_link_err = _check_retry_link_required(
                orch,
                task_id,
                target_agent=child.agent,
                revisit_of_task_id=child.revisit_of_task_id,
            )
            if retry_link_err is not None:
                _reject_retry_link_decision(
                    orch,
                    task_id,
                    agent,
                    next_count,
                    f"fanout child {i + 1}: {retry_link_err}",
                    expected_claim=expected_claim,
                )
                return

        # Spawn children immediately — no review gate (founder ruling THR-012 msg 129/131).
        width = len(decision.children)
        children_payload = []
        for c in decision.children:
            cd = {"agent": c.agent, "prompt": c.prompt}
            if c.revisit_of_task_id is not None:
                cd["revisit_of_task_id"] = c.revisit_of_task_id
            if c.expect_verdict is not None:
                cd["expect_verdict"] = c.expect_verdict
            if c.attachments:
                cd["attachments"] = [ref.model_dump(exclude_none=True) for ref in c.attachments]
            if c.then:
                cd["then"] = [
                    {"agent": l.agent, "prompt": l.prompt,
                     "expect_verdict": l.expect_verdict,
                     **({"attachments": [ref.model_dump(exclude_none=True) for ref in l.attachments]}
                        if l.attachments else {})}
                    for l in c.then
                ]
            children_payload.append(cd)
        try:
            outcome = _spawn_fanout_children(
                orch, task, task_id, next_count,
                children=children_payload,
                width=width,
                manager_agent=agent,
                join_summary=decision.join_summary,
                step_audit_id=_step_audit_id,
                expected_claim=expected_claim,
            )
        except _PreChildDecisionAttachmentError as exc:
            _attachment_feedback_and_reenqueue(
                orch, expected_claim, exc.detail, location=exc.location,
                reclaim_terminal_worktree=reclaim_terminal_worktree,
            )
            return
        if isinstance(outcome, InvalidLineage):
            _reject_retry_link_decision(orch, task_id, agent, next_count, outcome.reason,
                                        expected_claim=expected_claim)
        return

    if decision.action == "delegate":
        # Classify delegate validation errors. A configured reviewer leg that
        # omits expect_verdict (THR-175 HARD REJECT) is a recoverable authoring
        # error — feedback task result + feedback orchestration step, root back
        # to PENDING, one self re-enqueue so the owner corrects the decision
        # (never a root failure). Missing agent name / missing workspace are
        # unrecoverable for this step and keep hard terminal failure.
        err = _validate_delegate(orch, decision)
        if err is not None:
            if _is_reviewer_omission_error(err):
                _reject_reviewer_omission(
                    orch, task_id, agent, next_count, err,
                )
            else:
                note = f"invalid delegate: {err}"
                _fail(
                    orch, task_id, note=note,
                    **reclaim_kwargs,
                )
                _enqueue_parent_if_waiting(orch, task_id)
                _maybe_post_thread_followup(
                    orch, task_id,
                    status=TaskStatus.FAILED, auto_revisit_spawned=False,
                )
            return
        # Target-scope guard. Managers: own-team agents or self. Non-manager
        # owners: self only. Violations feed a feedback step back (not a hard
        # fail) so the owner can correct its decision next step.
        out_of_scope = _legs_out_of_scope(orch, owner=agent, decision=decision)
        if out_of_scope:
            parts = [f"{name!r} ({reason})" for name, reason in out_of_scope]
            if orch.teams.is_team_manager(agent):
                caller_team = orch.teams.team_for_manager(agent)
                feedback = (
                    f"Invalid delegation: you are on team {caller_team!r}, but "
                    f"{'; '.join(parts)}. Pick agents on your own team or "
                    "yourself, or escalate."
                )
            else:
                feedback = (
                    f"Invalid delegation: {'; '.join(parts)}. You may only "
                    f"delegate sub-tasks to yourself ({agent!r}), or escalate."
                )
            db.insert_task_result(
                task_id=task_id,
                agent=agent,
                session_id="",
                status="completed",
                confidence_score=0,
                output_summary=feedback,
                risks_flagged=[],
            )
            orch._audit.log_orchestration_step(
                task_id, next_count, {"action": "feedback", "reason": feedback},
            )
            db.update_task(task_id, status=TaskStatus.PENDING, block_kind=None)
            _enqueue_task_generation_aware(orch, task_id)
            return

        # THR-078 seq15: MANDATORY retry-link.  When this parent has FAILED
        # children and the owner is re-delegating to the same agent as a
        # failed child, revisit_of_task_id is REQUIRED — even the first retry
        # of a failed slice is DISALLOWED without the field.  Omission is
        # hard-rejected (feedback + re-enqueue), never silently treated as
        # an unlinked fresh dispatch that resets the ceiling.
        retry_link_err = _check_retry_link_required(
            orch,
            task_id,
            target_agent=decision.agent,
            revisit_of_task_id=decision.revisit_of_task_id,
        )
        if retry_link_err is not None:
            _reject_retry_link_decision(
                orch, task_id, agent, next_count, retry_link_err,
                expected_claim=expected_claim,
            )
            return

        from runtime.models import TaskRecord
        # Revision tracking: bump the delegating task's revision_count only
        # when the manager re-delegates to the *worker-of-record* — i.e. the
        # earliest-completed child. By convention, the first delegated child
        # is the worker for this task (true for both Content Team and
        # Engineering Team flows); subsequent same-agent delegations are
        # genuine revise cycles. Re-delegating to QA/reviewer is *not* a
        # revision and must not bump the count (spec
        # historical team design: "manager escalates after 2 rounds").
        revision_delta = 0
        cap = load_org_config(orch._paths).max_revise_rounds
        existing_children = db.get_children(task_id)
        completed_children = []
        for cid in existing_children:
            c = db.get_task(cid)
            if c is not None and c.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                completed_children.append(c)
        if completed_children:
            # Earliest by created_at; tie-break on id for determinism.
            completed_children.sort(key=lambda c: (c.created_at, c.id))
            worker_of_record = completed_children[0].assigned_agent
            # Self-targeted delegation is a sequence step (self-decomposition),
            # NOT a revise cycle — only bump when re-delegating to a DIFFERENT
            # worker-of-record. `agent` is this task's owner.
            if worker_of_record == decision.agent and decision.agent != agent:
                if cap > 0 and task.revision_count >= cap:
                    # THR-026 seq33: revise-round budget exhausted.
                    # DELIBERATE stop-with-best — do NOT increment, do NOT
                    # delegate, do NOT auto-revisit. Non-root tasks fail and
                    # wake their parent; root tasks escalate under this revise limit.
                    reason = f"iteration_budget_exhausted: revise budget ({cap} rounds) exhausted"
                    if not is_root(task):
                        _fail(
                            orch, task_id, note=reason,
                            **reclaim_kwargs,
                        )
                        _enqueue_parent_if_waiting(orch, task_id)
                        _maybe_post_thread_followup(
                            orch, task_id,
                            status=TaskStatus.FAILED, auto_revisit_spawned=False,
                        )
                        return
                    # Root: park in escalated for the founder (CAS-free —
                    # we are past the atomic claim in section 3, so there is
                    # no duplicate-delivery race).
                    if not db.try_escalate_runtime(
                        task_id,
                        reason=reason,
                        agent="orchestrator",
                        reason_code="runtime_revise_budget_exhausted",
                    ):
                        logger.debug(
                            "run_step %s: cancelled between re-check and "
                            "revise-budget escalate, dropping", task_id,
                        )
                        return
                    orch.notify_escalated(
                        task_id=task_id, agent="orchestrator", reason=reason,
                    )
                    _maybe_post_thread_escalation(orch, task_id, reason=reason)
                    return
                revision_delta = 1
        child_id = db.next_task_id()
        child = TaskRecord(
            id=child_id,
            team=task.team,
            brief=decision.prompt or "",
            assigned_agent=decision.agent,
            parent_task_id=task_id,
            status=TaskStatus.PENDING,
            session_timeout_seconds=task.session_timeout_seconds,
            task_type="subtask",
            # THR-078: carry revisit_of_task_id from the owner's decision
            # so the fan-out barrier can derive per-slice retry count
            # from existing DB lineage (no schema migration).
            revisit_of_task_id=decision.revisit_of_task_id,
        )

        # THR-109: validate and prepare decision attachments.
        # Run validation when ANY leg (direct or chain) declares attachments.
        delegate_attachment_params: list[dict] | None = None
        has_any_decision_att = bool(decision.attachments) or any(
            getattr(leg, 'attachments', None) for leg in (decision.then or [])
        )
        if has_any_decision_att:
            try:
                prevalidated = _validate_decision_attachments(orch, decision)
                if prevalidated:
                    delegate_attachment_params = _prepare_attachment_params(
                        orch, prevalidated,
                    )
            except ValueError as e:
                detail = _classify_attachment_error(e)
                if detail is not None:
                    _attachment_feedback_and_reenqueue(
                        orch, expected_claim, detail,
                        reclaim_terminal_worktree=reclaim_terminal_worktree,
                    )
                    return
                note = f"invalid decision attachments: {e}"
                _fail(
                    orch, task_id, note=note,
                    **reclaim_kwargs,
                )
                _enqueue_parent_if_waiting(orch, task_id)
                _maybe_post_thread_followup(
                    orch, task_id,
                    status=TaskStatus.FAILED, auto_revisit_spawned=False,
                )
                return

        # Atomic CAS: insert child + transition parent to IN_PROGRESS(DELEGATED)
        # under the same RLock acquisition. Serializes against /cancel via
        # Database RLock — closes the spawn-new-work race (Codex P1 on PR #34).
        # If False: cancel landed between Guard B's re-fetch and here. No child
        # was inserted, no parent overwrite, no enqueue. The founder's terminal
        # state wins. Drop silently — the founder cancelled deliberately.
        #
        # THR-109: active_chain is written inside try_delegate in the SAME
        # transaction as child insert + parent update + attachment links/audit.
        # A crash or write failure rolls back everything atomically — no
        # orphan chain state, no orphan child, no broken parent state.
        chain_json: str | None = None
        if decision.then or decision.expect_verdict is not None:
            from runtime.orchestrator.chain import ChainState
            chain = ChainState(
                step_index=0,
                first_leg_expect_verdict=decision.expect_verdict,
                legs=list(decision.then),
                step_audit_id=_step_audit_id,
            )
            chain_json = chain.serialize()
        outcome = db.try_delegate(
            task_id, child,
            parent_note=f"Delegated to {decision.agent} (child={child_id})",
            attachments=delegate_attachment_params,
            active_chain_json=chain_json,
            uploaded_by=agent,
            expected_claim=expected_claim,
            revision_delta=revision_delta, revision_cap=cap,
        )
        if isinstance(outcome, InvalidLineage):
            _reject_retry_link_decision(orch, task_id, agent, next_count, outcome.reason,
                                        expected_claim=expected_claim)
            return
        if isinstance(outcome, LostClaim):
            logger.debug(
                "run_step %s: cancelled between re-check and delegate, dropping",
                task_id,
            )
            return
        if not isinstance(outcome, Committed):
            raise TypeError(f"Unexpected spawn outcome: {type(outcome).__name__}")
        logger.debug("run_step %s: try_delegate SUCCEEDED, child=%s", task_id, child_id)
        _enqueue_task_generation_aware(orch, child_id)
        return

    # ---- 8. Unknown action ----
    note = f"unknown action: {decision.action}"
    _fail(
        orch, task_id, note=note,
        **reclaim_kwargs,
    )
    _enqueue_parent_if_waiting(orch, task_id)
    _maybe_post_thread_followup(
        orch, task_id,
        status=TaskStatus.FAILED, auto_revisit_spawned=False,
    )


def _reviewer_agents_for(orch: "Orchestrator") -> frozenset[str]:
    """The org's configured reviewer identities as a frozenset (THR-175).

    Resolved from the DB-backed ``reviewer_agents`` org setting (code default
    ``code_reviewer``) at the real Database seam, validated against the live
    active-agent roster — an unknown persisted name resolves fail-closed to
    the code default so ``code_reviewer`` is never silently demoted.
    """
    from runtime.orchestrator.org_config import (
        resolve_known_agent_names,
        resolve_org_setting_reviewer_agents,
    )
    return frozenset(resolve_org_setting_reviewer_agents(
        orch._db, known_agents=resolve_known_agent_names(orch._paths),
    ))


def _reviewer_omitted_expectation_error(agent: str) -> str:
    """HARD-REJECT message for a configured reviewer leg that omits
    ``expect_verdict``.  Remediation: set ``expect_verdict: "APPROVE"`` on
    every configured reviewer leg."""
    return (
        f"reviewer leg {agent!r} omits expect_verdict — HARD REJECT. "
        f"A configured reviewer leg ({agent!r}) must declare "
        f'expect_verdict: "APPROVE" so downstream QA/work only auto-advances '
        f"on an explicit approval."
    )


def _validate_one_leg(orch: "Orchestrator", *, agent: str | None, where: str) -> str | None:
    """Validate a single delegation leg (agent present + workspace exists).
    Returns None on success, a human-readable error string on failure.
    ``where`` is used only for chain-leg messages; the first-leg messages
    preserve the original wording for backward compatibility.
    """
    if not agent:
        return "missing agent name"
    workspace = orch._paths.workspaces_dir / agent
    if not workspace.exists():
        if where == "first leg":
            return f"no workspace for agent {agent!r}"
        return f"chain leg {where}: no workspace for agent {agent!r}"
    return None


_ATTACHMENT_REMEDIATION = {
    "too_many_attachments": "Reduce each task/leg list to at most five attachments.",
    "duplicate_attachment": "Use distinct fresh authorized uploads for each claiming task/leg; inherited access is reading, not a second claim.",
    "task_attachment_not_found": "Correct the reference or upload the permitted source anew before delegating.",
    "attachment_already_claimed": "A key belongs to one task, even in the same tree. Use output_dir/recall or a fresh authorized task upload for the handoff.",
    "invalid_attachment_display_name": "Use a safe supported filename without path separators or control characters (maximum 200 characters).",
    "unsupported_attachment_type": "Use the allowlisted extension matching the authorized file; do not relabel incompatible bytes or bypass MIME checks.",
}


def _classify_attachment_error(exc: ValueError) -> dict | None:
    """Only the six exact built-in validator/preparation emissions are recoverable."""
    if type(exc) is not ValueError or len(exc.args) != 1 or type(exc.args[0]) is not str:
        return None

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise ValueError("nonstandard JSON constant")

    try:
        detail = json.loads(exc.args[0], object_pairs_hook=pairs, parse_constant=constant)
        if type(detail) is not dict or type(detail.get("code")) is not str:
            return None
        code = detail["code"]
        if code not in _ATTACHMENT_REMEDIATION:
            return None
        fields = {"code"}
        if code == "too_many_attachments":
            fields.add("max")
            if type(detail.get("max")) is not int or detail["max"] != 5:
                return None
        if code in {"duplicate_attachment", "task_attachment_not_found", "attachment_already_claimed"}:
            fields.add("storage_key")
            if type(detail.get("storage_key")) is not str or not detail["storage_key"]:
                return None
        if code == "attachment_already_claimed":
            fields.add("task_id")
            if type(detail.get("task_id")) is not str or not detail["task_id"]:
                return None
        if set(detail) != fields:
            return None
        # Reject lone surrogates even though the JSON decoder accepts them.
        for value in detail.values():
            if type(value) is str:
                value.encode("utf-8")
        return detail
    except (ValueError, UnicodeError, RecursionError):
        return None


class _PreChildDecisionAttachmentError(Exception):
    """Private transport from recognized fanout pre-child seams only."""
    def __init__(self, detail: dict, location: str) -> None:
        self.detail, self.location = detail, location
        super().__init__(detail["code"])


def _attachment_feedback_text(detail: dict, location: str | None) -> str:
    text = "Invalid initial decision attachments: " + detail["code"]
    if location:
        text += " at " + location
    if "storage_key" in detail:
        text += " storage_key=" + json.dumps(detail["storage_key"][:200], ensure_ascii=True)
    if "task_id" in detail:
        text += " claimed_by=" + json.dumps(detail["task_id"][:200], ensure_ascii=True)
    return (text + ". " + _ATTACHMENT_REMEDIATION[detail["code"]]
            + " Correct in a genuinely new authorized session/result; never replay the consumed result. "
            "At most two committed attachment-error feedback opportunities are shared by this task across sessions and initial delegate/fanout decisions. "
            "Committed feedback spends a chance even when enqueue is lost; current authority and revision limits still govern. "
            "Keep task-only evidence in output_dir/recall, not Shared Artifacts.")


def _attachment_stop_owner_current(orch: "Orchestrator", stopped: AttachmentCorrectionStopped) -> bool:
    from runtime.infrastructure.db.tasks import _ATTACHMENT_STOP_FIELDS, _attachment_material_digest
    db = orch._db
    row = db._conn.execute("SELECT * FROM tasks WHERE id=?", (stopped.claim.task_id,)).fetchone()
    if row is None or tuple(row[key] for key in _ATTACHMENT_STOP_FIELDS) != stopped.task_projection:
        return False
    result = db._conn.execute("SELECT * FROM task_results WHERE id=?", (stopped.claim.result_row_id,)).fetchone()
    if result is None or _attachment_material_digest(result) != stopped.accepted_result_digest:
        return False
    for raw in stopped.audit_pair:
        audit = json.loads(raw)
        current = db._conn.execute("SELECT * FROM audit_log WHERE id=?", (audit["id"],)).fetchone()
        if current is None or json.dumps(dict(current), sort_keys=True) != raw:
            return False
    return True


def _attachment_owned_effect(
    orch: "Orchestrator", current_owner: Callable[[], bool],
    effect: Callable[[], object], *, history: bool = False,
) -> object | None:
    """Same-daemon fence; history additionally reserves independent SQLite writers."""
    with orch._db._lock:
        try:
            if history:
                orch._db._conn.execute("BEGIN IMMEDIATE")
            if current_owner():
                return effect()
        finally:
            if history:
                orch._db._conn.rollback()


def _attachment_feedback_and_reenqueue(
    orch: "Orchestrator", expected_claim: RetryClaim, detail: dict, *,
    location: str | None = None, reclaim_terminal_worktree: bool = True,
) -> None:
    feedback = _attachment_feedback_text(detail, location)
    outcome = orch._db.try_attachment_retry_feedback(expected_claim, feedback, detail["code"])
    if isinstance(outcome, PendingRetry):
        enqueue = None if orch._queue is None else lambda: orch._queue.put_nowait(orch._slug, expected_claim.task_id)
        orch._db.admit_retry_feedback(outcome, enqueue)
        return
    if not isinstance(outcome, AttachmentCorrectionStopped):
        return
    task_id = expected_claim.task_id
    current = lambda: _attachment_stop_owner_current(orch, outcome)
    # Normal continued-turn handling normally already spent this envelope.
    # Never spend a replacement selected after the winning stop.
    def abort_original_envelope():
        envelope = orch._db.get_active_authority_continue_envelope(task_id)
        if (outcome.active_envelope is not None and envelope is not None
                and json.dumps(dict(envelope), sort_keys=True) == outcome.active_envelope):
            orch._db.spend_authority_continue_envelope_if_active(
                task_id, audit_agent=expected_claim.assigned_agent or "orchestrator",
                error="task stopped after attachment correction opportunities were exhausted",
            )
    _attachment_owned_effect(orch, current, abort_original_envelope)
    root_transitions = _RootWorktreeTransitions()
    if outcome.worktree_before and outcome.worktree_after:
        before, after = json.loads(outcome.worktree_before), json.loads(outcome.worktree_after)
        if (before["parent_task_id"] is None and before["status"] not in {s.value for s in TERMINAL_STATES}
                and after["status"] in {s.value for s in TERMINAL_STATES}):
            root_transitions.append(_RootWorktreeTransition(before, after, "attachment_correction_stop"))
    is_root_stop = bool(outcome.audit_pair)
    if is_root_stop:
        _attachment_owned_effect(orch, current, lambda: orch.notify_escalated(
            task_id=task_id, agent="orchestrator", reason=outcome.task_projection[8],
        ))
        _attachment_owned_effect(orch, current, lambda: _maybe_post_thread_escalation(
            orch, task_id, reason=outcome.task_projection[8], current_owner=current,
        ))
    else:
        _fail_terminal_tail(orch, task_id, reclaim_terminal_worktree=reclaim_terminal_worktree,
                            current_owner=current, root_transitions=root_transitions)
        _attachment_owned_effect(orch, current, lambda: _enqueue_parent_if_waiting(
            orch, task_id, current_owner=current,
        ))
        _attachment_owned_effect(orch, current, lambda: _maybe_post_thread_followup(
            orch, task_id, status=TaskStatus.FAILED, auto_revisit_spawned=False,
            current_owner=current,
        ))


def _prepare_attachment_params(
    orch: "Orchestrator",
    prevalidated: list[dict],
) -> list[dict]:
    """Build attachment param dicts from prevalidated refs for DB insertion.

    Re-checks file existence and reads size_bytes. Caller must already have
    validated the refs via ``validate_task_attachment_refs``.

    Returns a list of dicts with keys: ordinal, storage_key, display_name,
    size_bytes, content_type.
    """
    import json as _json
    from runtime.infrastructure.task_attachment_store import TaskAttachmentStore
    store = TaskAttachmentStore(orch._paths.task_attachments_dir)
    params: list[dict] = []
    for idx, pva in enumerate(prevalidated):
        path = store.path_for(pva["storage_key"])
        if not path.exists():
            raise ValueError(_json.dumps({
                "code": "task_attachment_not_found",
                "storage_key": pva["storage_key"],
            }))
        size_bytes = path.stat().st_size
        params.append({
            "ordinal": idx,
            "storage_key": pva["storage_key"],
            "display_name": pva["display_name"],
            "size_bytes": size_bytes,
            "content_type": pva["content_type"],
        })
    return params


def _validate_decision_attachments(
    orch: "Orchestrator",
    decision,
) -> list[dict] | None:
    """Validate all attachment refs across a delegate decision (direct +
    chain legs). Returns prevalidated attachment params for the direct
    delegate leg, or None if no attachments in any leg.

    Chain leg attachments are validated for early detection but their
    params are not returned here — they're re-validated when the leg spawns.
    The direct leg's prevalidated params ARE returned for immediate use.

    Always validates all legs — even when the direct leg has zero attachments
    but a later chain leg declares refs. This ensures whole-decision prevalidation:
    an invalid later-leg ref rejects the decision before any child is spawned,
    any parent parks, any active_chain is written, or any queue entry is created.

    Duplicate detection is DECISION-WIDE: a storage_key used in both
    ``decision.attachments`` and ``decision.then[i].attachments``, or in two
    different chain legs, is rejected before any child/spawn/park/chain state.
    One shared key ledger covers every declared ref in the decision.

    Raises ValueError (JSON-encoded error) on validation failure. Initial
    dispatch recognizes only the six closed attachment-error shapes for owned
    feedback; later chain advancement retains its existing failure semantics.
    """
    from runtime.infrastructure.task_attachment_store import (
        TaskAttachmentStore,
        validate_task_attachment_refs,
    )
    store = TaskAttachmentStore(orch._paths.task_attachments_dir)
    db = orch._db

    has_direct = bool(decision.attachments)
    has_chain = any(
        getattr(leg, 'attachments', None) for leg in (decision.then or [])
    )
    if not has_direct and not has_chain:
        return None

    # One decision-wide key ledger for duplicate detection across all legs.
    seen_keys: set[str] = set()

    # Validate direct delegate attachments.
    direct_refs = list(decision.attachments) if decision.attachments else []
    prevalidated = None
    if direct_refs:
        prevalidated = validate_task_attachment_refs(
            store=store, db=db, refs=direct_refs,
            external_seen_keys=seen_keys,
        )
        for pva in prevalidated:
            seen_keys.add(pva["storage_key"])

    # Validate chain leg attachments (early detection — always run, even
    # when direct attachments are absent). Each leg's keys are checked
    # against the accumulating decision-wide seen_keys for duplicates.
    if decision.then:
        for i, leg in enumerate(decision.then):
            leg_refs = list(leg.attachments) if leg.attachments else []
            if leg_refs:
                leg_pv = validate_task_attachment_refs(
                    store=store, db=db, refs=leg_refs,
                    external_seen_keys=seen_keys,
                )
                for pva in leg_pv:
                    seen_keys.add(pva["storage_key"])

    return prevalidated


def _validate_delegate(orch: "Orchestrator", decision) -> str | None:
    """Return a human-readable error string if the delegate decision is
    unusable, or None if it's good to spawn. Validates the first leg and
    every entry in ``decision.then`` (chain legs), returning on the first
    failure encountered.

    THR-175: any configured reviewer leg (first leg or a ``then`` leg) that
    omits ``expect_verdict`` is a HARD REJECT before any child is spawned."""
    reviewer_agents = _reviewer_agents_for(orch)
    err = _validate_one_leg(orch, agent=decision.agent, where="first leg")
    if err is not None:
        return err
    # A reviewer FIRST leg only matters when it gates a downstream chain
    # (``then`` non-empty). A bare single-leg reviewer delegate (no ``then``,
    # no ``expect_verdict``) is not a chain and is not rejected.
    if decision.then and decision.agent in reviewer_agents and decision.expect_verdict is None:
        return _reviewer_omitted_expectation_error(decision.agent)
    for i, leg in enumerate(decision.then or []):
        err = _validate_one_leg(orch, agent=leg.agent, where=str(i + 2))
        if err is not None:
            return err
        if leg.agent in reviewer_agents and leg.expect_verdict is None:
            return _reviewer_omitted_expectation_error(leg.agent)
    return None


def _check_retry_link_required(
    orch: "Orchestrator",
    task_id: str,
    *,
    target_agent: str | None,
    revisit_of_task_id: str | None,
) -> str | None:
    """Validate a retry link for a delegate or one fan-out child.

    THR-078 seq15: when this parent has FAILED children and the delegate
    re-targets the agent of any FAILED child, ``revisit_of_task_id`` is
    MANDATORY — even the first retry is disallowed without the field. A
    supplied link must identify a same-agent FAILED child under this parent
    or a predecessor root connected by verified recorded supersessions. Returns None only for a valid retry or a fresh
    dispatch without a link."""
    if target_agent is None:
        return None
    outcome = orch._db.verify_retry_link(task_id, target_agent, revisit_of_task_id)
    if isinstance(outcome, InvalidLineage):
        return f"FAILED child retry for {target_agent!r}: {outcome.reason} (revisit_of_task_id required and must identify a valid failed predecessor)"
    return None


def _feedback_and_reenqueue(
    orch: "Orchestrator",
    task_id: str,
    agent: str,
    next_count: int,
    feedback: str,
) -> None:
    """Record a feedback task result + feedback orchestration audit step and
    re-enqueue the SAME task for a corrected decision.

    The owner's next session must re-run and fix the decision; the root is
    never failed, no child is spawned, and no active_chain/fanout/park
    metadata is written. This is the established authoring-error feedback
    shape (out-of-scope and retry-link rejection reuse it): one
    ``task_results`` row (status=completed, confidence=0), one
    ``orchestration_step`` audit row keyed to the claimed step with
    ``{"action": "feedback", "reason": feedback}``, task back to PENDING
    with block_kind cleared, and exactly one self re-enqueue."""
    db = orch._db
    db.insert_task_result(
        task_id=task_id,
        agent=agent,
        session_id="",
        status="completed",
        confidence_score=0,
        output_summary=feedback,
        risks_flagged=[],
    )
    orch._audit.log_orchestration_step(
        task_id, next_count, {"action": "feedback", "reason": feedback},
    )
    db.update_task(task_id, status=TaskStatus.PENDING, block_kind=None)
    _enqueue_task_generation_aware(orch, task_id)


def _is_reviewer_omission_error(err: str) -> bool:
    """True when a delegate/fanout validation error is the recoverable THR-175
    reviewer ``expect_verdict`` omission (HARD REJECT with remediation) rather
    than an unrecoverable structural error (missing agent name / missing
    workspace). Only ``_reviewer_omitted_expectation_error`` emits this text,
    so no structural error can be misclassified as recoverable."""
    return "omits expect_verdict" in err


def _reject_reviewer_omission(
    orch: "Orchestrator",
    task_id: str,
    agent: str,
    next_count: int,
    err: str,
    *,
    action_label: str = "delegation",
) -> None:
    """HARD REJECT a configured reviewer leg that omits ``expect_verdict`` as a
    recoverable authoring error: feedback task result + feedback orchestration
    audit step, root back to PENDING, one self re-enqueue — never a root
    failure (TASK-5921). ``err`` already carries the HARD REJECT message and
    the expect_verdict: "APPROVE" remediation."""
    feedback = (
        f"Invalid {action_label}: {err} "
        f"Correct the decision and re-submit with "
        f'expect_verdict: "APPROVE" on the reviewer leg.'
    )
    _feedback_and_reenqueue(orch, task_id, agent, next_count, feedback)


def _reject_retry_link_decision(
    orch: "Orchestrator",
    task_id: str,
    agent: str,
    next_count: int,
    retry_link_err: str,
    *, expected_claim: RetryClaim,
) -> None:
    """Fail closed with feedback when a delegate or fan-out retry is invalid."""
    feedback = (
        f"Invalid revisit_of_task_id: {retry_link_err}. "
        f"When re-delegating to an agent with a FAILED child under this parent, "
        f"you MUST set revisit_of_task_id to that failed predecessor's task id."
    )
    pending = orch._db.try_retry_feedback(expected_claim, feedback)
    if isinstance(pending, PendingRetry):
        enqueue = None if orch._queue is None else lambda: orch._queue.put_nowait(orch._slug, task_id)
        orch._db.admit_retry_feedback(pending, enqueue)


def _legs_out_of_scope(orch: "Orchestrator", owner: str, decision) -> list[tuple[str, str]]:
    """Return [(agent_name, reason)] for delegation legs `owner` may not target.

    - Manager owner: may target agents on its own team, or itself.
    - Non-manager owner: may target ONLY itself (self-decomposition).

    Empty list = all legs in scope.
    """
    targets = [decision.agent] + [leg.agent for leg in (decision.then or [])]
    out: list[tuple[str, str]] = []
    if orch.teams.is_team_manager(owner):
        caller_team = orch.teams.team_for_manager(owner)
        for a in targets:
            if not a or a == owner:        # self always allowed
                continue
            t = orch.teams.team_for_agent(a)
            if caller_team is None or t != caller_team:
                out.append((a, f"on team {t!r}" if t else "not on a team"))
    else:
        for a in targets:
            if not a or a == owner:
                continue
            out.append((a, "non-manager owners may only delegate to themselves"))
    return out


def _default_agent_for_root(orch: "Orchestrator", task) -> str:
    """Root tasks default to the manager for their team."""
    owner = orch.teams.executable_manager_for_team(task.team)
    if owner is None:
        raise ValueError("owner_required_for_human_team")
    return owner


def _workspace_cleanup_remainder(after) -> dict | None:
    """Serialize the consumer's actual post-action accounting as remainder.

    ``ReclamationResult`` exposes ``before``/``after`` ``Accounting`` values and
    has no ``remainder`` attribute.  The only real remainder evidence is the
    returned ``after`` accounting; it stays ``None`` when ``after`` is ``None``
    (an unknown or unrecoverable post-action state is never invented).
    """
    if after is None:
        return None
    return {
        "allocated_bytes": after.allocated_bytes,
        "apparent_bytes": after.apparent_bytes,
        "inodes": after.inodes,
    }


def _workspace_cleanup_remainder_fact(remainder: dict | None) -> str:
    """Render the serialized ``after`` accounting for the prompt fact.

    A known remainder is emitted literally so a partial physical effect survives
    even when the owner audit publication fails; ``None`` renders the explicit
    ``null`` marker for unknown after-accounting and is never inferred from the
    claimed byte/inode counts.
    """
    import json as _json

    if remainder is None:
        return "null"
    return _json.dumps(remainder, sort_keys=True)


def _prepare_workspace_cleanup_reclamation_context(
    orch: "Orchestrator", task: "TaskRecord", agent: str, *,
    stale_orchestration_step_count: int, claimed_next_step_count: int,
) -> str:
    """Best-effort, bounded reclamation immediately before an agent launch.

    The selector owns its read-only provenance and finite-shortlist checks.
    This hook deliberately owns only the action-phase admissions: one initial
    config read, then one fresh config/owner pair before each consumer call.
    It never turns a refusal into a permit or manufactures a consumer result.
    """
    # The default is disabled.  Preserve the shared loader's strict parsing:
    # malformed configuration escapes exactly as it does at other consumers.
    if not load_org_config(orch._paths).workspace_cleanup_reclamation_actions_enabled:
        return ""

    # The selector's steady-state contract relies on its caller to establish a
    # registered owner identity; its canonical/authoritative workspace arguments
    # are not that check.  Read the existing in-memory TeamsRegistry (no new DB
    # or config read, so the bounded action-phase budget is unchanged) and refuse
    # an unregistered agent before selection/action: it reaches no consumer call
    # and writes no owner action audit.
    if orch.teams.team_for_agent(agent) is None:
        return ""

    deadline = time.monotonic() + 1.0
    reads = 1  # Initial enabled-config load is outside the timed phase.

    def admit_observation(_name: str) -> bool:
        nonlocal reads
        if reads >= 23 or time.monotonic() > deadline:
            return False
        reads += 1
        return True

    workspace = orch._paths.workspaces_dir / agent
    selection = orch._db.select_workspace_cleanup_reclamation_candidates(
        owner_task_id=task.id,
        agent=agent,
        stale_orchestration_step_count=stale_orchestration_step_count,
        claimed_next_step_count=claimed_next_step_count,
        canonical_workspace=workspace,
        authoritative_workspace=workspace,
        admit_observation=admit_observation,
    )
    if selection is None:
        return ""

    # Importing here keeps the formerly dormant engine unreachable except at
    # this explicitly bounded hook.  Its startup clock remains authoritative.
    from runtime.daemon.task_scratch_reclamation import (
        collect_revalidate_seal_consume_disposable,
    )
    from runtime.orchestrator.task_scratch_report import _STARTED_MONOTONIC

    facts: list[str] = []
    for candidate in selection.candidates:
        # The post-selection checks are fresh by design.  They are counted as
        # actual reads/loads; a fifth call is therefore allowed at read 23.
        if not admit_observation("config"):
            break
        # Only the initial shared-config load keeps the ordinary escaping
        # behavior.  A per-target fresh load or owner read that fails is one
        # refusal: stop later admissions, keep already-known facts.
        try:
            enabled = load_org_config(
                orch._paths
            ).workspace_cleanup_reclamation_actions_enabled
        except Exception:
            break
        if not enabled:
            break
        if not admit_observation("owner"):
            break
        try:
            owner = orch._db.get_task(task.id)
        except Exception:
            break
        if (
            owner is None or owner.id != task.id or owner.assigned_agent != agent
            or owner.status is not TaskStatus.IN_PROGRESS or owner.block_kind is not None
            or owner.cancelled_at is not None or owner.orchestration_step_count != 1
        ):
            break
        # Consumer admission consumes no hook read budget, but it must still
        # begin before the shared one-second deadline expires.
        if time.monotonic() > deadline or orch._sessions is None:
            break
        try:
            result = collect_revalidate_seal_consume_disposable(
                db=orch._db, sessions=orch._sessions, workspace=workspace,
                task_id=candidate.task_id, agent_name=agent,
                monotonic_now=time.monotonic(),
                daemon_started_monotonic=_STARTED_MONOTONIC,
            )
        except Exception:
            # An escaped ordinary consumer failure is unknown: do not publish a
            # synthetic None outcome and do not start another target.
            # BaseException-class interruption (cancellation/KeyboardInterrupt)
            # is deliberately not caught here and propagates unchanged.
            break

        if result is None:
            # Two indistinguishable refusal causes (invoked None / returned
            # unknown) publish the literal refusal prose plus the target id.
            payload = {
                "target_task_id": candidate.task_id, "outcome": "none",
                "claimed_bytes": 0, "claimed_inodes": 0, "remainder": None,
                "reason": None, "publication": "attempted",
            }
            fact = f"target={candidate.task_id} refused_or_unavailable"
        else:
            remainder = _workspace_cleanup_remainder(result.after)
            payload = {
                "target_task_id": candidate.task_id, "outcome": result.outcome,
                "claimed_bytes": result.reclaimed_bytes,
                "claimed_inodes": result.reclaimed_inodes,
                "remainder": remainder, "reason": result.reason,
                "publication": "attempted",
            }
            fact = (
                f"target={candidate.task_id} outcome={result.outcome} "
                f"reason={result.reason} "
                f"claimed_bytes={result.reclaimed_bytes} "
                f"claimed_inodes={result.reclaimed_inodes} "
                f"remainder={_workspace_cleanup_remainder_fact(remainder)}"
            )
        try:
            orch._db.insert_audit_log(
                task.id, agent, "workspace_cleanup_reclamation_attempt", payload,
            )
        except Exception:
            # Publication loss leaves the known result intact but bars later
            # actions; the prompt exposes only that limited known fact.
            facts.append(f"{fact} publication_unavailable")
            break
        facts.append(fact)

    if not facts:
        return ""
    return "\n\nWorkspace cleanup reclamation:\n" + "\n".join(
        f"- {fact}" for fact in facts
    )


def _build_agent_prompt(orch: "Orchestrator", task, agent: str) -> str:
    """Build the per-task `role_guidance` body — i.e., what gets indented under
    `role_guidance: |` in the outer wrapper built by
    ``Orchestrator._build_agent_prompt``.

    Subtask agents get their per-task instruction as the bare brief,
    which the outer wrapper already renders as ``Parameters.brief``. Echoing
    it here would duplicate the brief in every subtask agent spawn (the wrapper
    drops the ``role_guidance:`` line when this returns empty).

    Task owners return the capabilities prompt (decision schema, agent roster,
    prior steps). For revisited roots, a one-shot context header is prepended
    on the very first orchestration step (detected via audit log).
    """
    from runtime.orchestrator.capabilities import build_capabilities_prompt
    if task.task_type != "task":
        # Leaf subtask instruction is the brief, except a resumed job-block
        # needs its outcome pointer to avoid resubmitting the same job (THR-161).
        return _blocked_jobs_resume_header_if_applicable(orch, task.id) or ""
    from runtime.orchestrator import prompt_loader
    is_mgr = orch.teams.is_team_manager(agent)
    agents_for_prompt: list[dict] = []
    if is_mgr:
        for name in _list_candidate_agents(orch, agent):
            candidate = prompt_loader.load_agent(orch._paths, name)
            desc = (candidate.description if candidate is not None else None) or name
            agents_for_prompt.append({"name": name, "description": desc})
        # Self-targeting (spec §3): a manager may delegate a sub-task to itself
        # to break its own work into a fresh bounded session. _list_candidate_agents
        # only returns team workers (the manager is never in teams.yaml `workers`),
        # so advertise self explicitly in the roster.
        agents_for_prompt.append({
            "name": agent,
            "description": "yourself — delegate a sub-task to yourself to "
                           "decompose your own work into a fresh bounded session",
        })
    prior_steps = _build_prior_steps_from_db(orch, task.id)
    base = build_capabilities_prompt(
        agents=agents_for_prompt,
        step_number=task.orchestration_step_count + 1,  # 1-indexed for manager display
        prior_steps=prior_steps,
        manager_name=agent,
        self_only=not is_mgr,
        reviewer_agents=sorted(_reviewer_agents_for(orch)),
        name_metadata=orch.read_name_metadata(),
    )
    headers: list[str] = []
    revisit = _revisit_header_if_applicable(orch, task.id)
    if revisit is not None:
        headers.append(revisit)
    resume_header = _blocked_jobs_resume_header_if_applicable(orch, task.id)
    if resume_header is not None:
        headers.append(resume_header)
    resolved = _resolved_escalation_header_if_applicable(orch, task.id)
    if resolved is not None:
        headers.append(resolved)
    # Fan-out join header
    fanout_join = _fanout_join_header_if_applicable(orch, task.id)
    if fanout_join is not None:
        headers.append(fanout_join)
    if headers:
        return "".join(headers) + base
    return base


def _blocked_jobs_resume_header_if_applicable(
    orch: "Orchestrator", task_id: str,
) -> str | None:
    """Return a BLOCKED-JOBS-RESULTS header on the first agent step after a
    task resumes from a job-block, otherwise None.

    Trigger: the most recent `task_resumed_from_jobs` audit entry for this task
    has a higher row id than the most recent `orchestration_step` entry —
    i.e. the jobs are terminal AND the agent hasn't run yet. Audit `id` is
    autoincrement, so id-ordering is equivalent to chronological ordering.
    Once the agent produces its first decision after resume,
    `log_orchestration_step` writes a row with a higher id and this helper
    returns None on every subsequent call.

    Spec: §6.4.
    """
    import json as _json  # noqa: PLC0415

    logs = orch._db.get_audit_logs(task_id)
    last_resumed = None
    last_step = None
    for entry in logs:
        action = entry["action"]
        if action == "task_resumed_from_jobs":
            last_resumed = entry
        elif action == "orchestration_step":
            last_step = entry
    if last_resumed is None:
        return None
    if last_step is not None and last_step["id"] > last_resumed["id"]:
        return None

    payload = last_resumed["payload"] or {}
    if isinstance(payload, str):
        try:
            payload = _json.loads(payload)
        except Exception:
            payload = {}
    job_ids: list[str] = payload.get("blocking_job_ids", [])
    outcomes: dict[str, str] = payload.get("job_outcomes", {})

    lines: list[str] = [
        "=== BLOCKED-JOBS-RESULTS (system) ===",
        f"You self-blocked on {', '.join(job_ids)}. They are now terminal:",
        "",
    ]
    for jid in job_ids:
        status = outcomes.get(jid, "unknown")
        try:
            detail = orch._db.get_job(jid)
        except Exception:
            # Legacy/test-crafted rows can predate required JobRecord fields.
            # The status string in the audit payload remains the durable
            # compatibility floor; optional presentation detail must never
            # make a resumed task fail before its provider launch.
            logger.warning(
                "resume header could not load optional job detail for %s/%s",
                task_id, jid,
            )
            detail = None
        detail_parts: list[str] = []
        if detail is not None and detail.reason is not None:
            detail_parts.append(detail.reason)
        if detail is not None and detail.exit_code is not None:
            detail_parts.append(f"exit {detail.exit_code}")
        detail_suffix = f" ({', '.join(detail_parts)})" if detail_parts else ""
        lines.append(f"  {jid}  {status}{detail_suffix}")
        lines.append(f"          → happyranch jobs show {jid}")
        lines.append(f"          → happyranch jobs output {jid}")
    lines.append("")
    lines.append("Re-read your task brief; decide whether to proceed, retry, or escalate.")
    lines.append("======================================")
    return "\n".join(lines) + "\n\n"


def _is_already_terminal(orch: "Orchestrator", task_id: str) -> bool:
    """Shared idempotence predicate for the four decision branches.

    Returns True when the row is gone, already terminal (COMPLETED / FAILED),
    or cancelled — any state where a subsequent decision must not overwrite
    the task's status, note, or spawn children.

    Includes `cancelled_at` explicitly as defense in depth: today `/cancel`
    always flips status to FAILED alongside stamping `cancelled_at`, so the
    `status in TERMINAL_STATES` check covers the cancelled case. But if a
    future code path ever stamps `cancelled_at` without touching status, this
    predicate still does the right thing.

    Closes the cancel-race documented in
    docs/superpowers/specs/2026-05-26-cancel-race-design.md §5.3.
    """
    existing = orch._db.get_task(task_id)
    return (
        existing is None
        or existing.status in TERMINAL_STATES
        or existing.cancelled_at is not None
    )


def _complete(orch: "Orchestrator", task_id: str, *, note: str, output_dir: str | None = None,
              recovery_owner: tuple[str, str, int] | None = None,
              after_recovery_cleanup: Callable[[], None] | None = None,
              after_recovery_parent_effect: Callable[[], None] | None = None,
              reclaim_terminal_worktree: bool = True) -> bool:
    root_transitions = _RootWorktreeTransitions()
    root_handed_off = False
    tail_failed = False
    try:
        from datetime import datetime, timezone
        # Idempotence guard: /cancel may have already taken this task to FAILED
        # between Popen return and here. Don't resurrect a cancelled task back to
        # COMPLETED just because the subprocess happened to finish cleanly before
        # SIGTERM arrived.
        if _is_already_terminal(orch, task_id):
            return False
        if recovery_owner is not None:
            with _capture_root_worktree_transition(orch, task_id, '_complete.recovery', root_transitions):
                committed = orch._db.complete_task_if_current_recovery_owner(
                    task_id=task_id, agent=recovery_owner[0], session_id=recovery_owner[1],
                    note=note, output_dir=output_dir, completed_at=datetime.now(timezone.utc).isoformat(),
                    result_row_id=recovery_owner[2],
                )
            if not committed:
                return False
        else:
            with _capture_root_worktree_transition(orch, task_id, '_complete', root_transitions):
                orch._db.update_task(task_id, status=TaskStatus.COMPLETED, block_kind=None,
                                     note=note, final_output_dir=output_dir,
                                     completed_at=datetime.now(timezone.utc).isoformat())
        _log_verdict_if_delegated(orch, task_id, success=True)
        orch._update_task_history(task_id)
        if recovery_owner is None:
            _kill_jobs_for_terminating_task(orch, task_id)
            if reclaim_terminal_worktree and not root_transitions:
                _reclaim_terminal_task_worktree(orch, task_id)
        elif orch._db.mark_task_completion_recovery_callback_consumed(
            task_id=task_id, agent=recovery_owner[0], session_id=recovery_owner[1],
            result_row_id=recovery_owner[2], settled_at=datetime.now(timezone.utc).isoformat(),
        ):
            _handoff_consumed_recovery_terminal_effects(
                orch, task_id, recovery_owner[0], recovery_owner[1], recovery_owner[2],
                TaskStatus.COMPLETED.value, after_recovery_cleanup=after_recovery_cleanup,
                after_recovery_parent_effect=after_recovery_parent_effect,
                root_transitions=root_transitions,
            )
            root_handed_off = True
        return True
    except BaseException:
        tail_failed = True
        raise
    finally:
        if root_transitions and not root_handed_off:
            _root_worktree_batch(orch, root_transitions, teardown_incomplete=tail_failed or recovery_owner is not None)



def _fail(
    orch: "Orchestrator", task_id: str, *, note: str,
    reclaim_terminal_worktree: bool = True,
    current_owner: Callable[[], bool] | None = None,
) -> None:
    root_transitions = _RootWorktreeTransitions()
    from datetime import datetime, timezone
    # Idempotence guard — same rationale as _complete. When /cancel SIGTERMs
    # the subprocess, run_step re-enters via the post-execution classifier and
    # tries to write a "session failed (rc=-15; ...)" note. That must NOT
    # overwrite the cancel route's "cancelled by <actor>: ..." note.
    if _is_already_terminal(orch, task_id):
        return
    with orch._db._lock:
        current = orch._db.get_task(task_id)
        if current is not None:
            try:
                human = orch.teams.manager_for_team(current.team).kind == "human"
            except KeyError:
                human = False
            if human and current.task_type == "subtask" and current.assigned_agent:
                accepted = orch._db.get_accepted_task_completion_recovery_result(task_id=task_id, agent=current.assigned_agent)
                if accepted is not None:
                    # Only a caller presenting actual R may consume it. A late
                    # provider failure cannot clear or overwrite its winner.
                    return
        if _is_already_terminal(orch, task_id):
            return
        if current_owner is not None and not current_owner():
            return
        # THR-181 Track A (founder lifecycle envelope): a failure closes the single-use
        # continuation window fail-closed — the continuation was never exercised
        # for its permitted action and must never be re-used.
        try:
            _t = orch._db.get_task(task_id)
            orch._db.spend_authority_continue_envelope_if_active(
                task_id,
                audit_agent=(
                    _t.assigned_agent if _t is not None else "orchestrator"
                ) or "orchestrator",
                error="task failed without the permitted continued-turn decision",
            )
        except Exception:  # pragma: no cover - fail-closed defensive
            pass
        # Clear any in-flight chain so the CLI/Web UI doesn't show a chain strip
        # on a FAILED task. The chain can't re-activate (the task is terminal),
        # but the dangling state is cosmetically misleading. Always-clear is
        # cheap and works for cascade-fail, self-blocked, invalid-delegate, and
        # session-failure failure modes.
        orch._db.update_task_active_chain(task_id, None)
        orch._db.update_task_active_fanout(task_id, None)
        with _capture_root_worktree_transition(orch, task_id, '_fail', root_transitions):
            orch._db.update_task(
                task_id,
                status=TaskStatus.FAILED,
                block_kind=None,
                note=note,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
    _fail_terminal_tail(
        orch,
        task_id,
        reclaim_terminal_worktree=reclaim_terminal_worktree,
        root_transitions=root_transitions,
        expected_note=note if current_owner is not None else None,
    )


def _task_matches_authority_v2_refusal_failure(
    orch: "Orchestrator", task_id: str, expected_note: str,
) -> bool:
    """Fence each refusal tail effect to the exact committed FAILED owner."""
    task = orch._db.get_task(task_id)
    return bool(
        task is not None
        and task.status == TaskStatus.FAILED
        and task.note == expected_note
        and task.completed_at is not None
    )


def _fail_terminal_tail(
    orch: "Orchestrator",
    task_id: str,
    *,
    reclaim_terminal_worktree: bool = True,
    expected_note: str | None = None,
    root_transitions: list | None = None,
    current_owner: Callable[[], bool] | None = None,
) -> None:
    """Run `_fail`'s post-transition effects, optionally owner-fenced."""
    def current() -> bool:
        if current_owner is not None and not current_owner():
            return False
        task = orch._db.get_task(task_id)
        if task is not None and task.task_type == "subtask" and task.assigned_agent:
            try:
                human = orch.teams.manager_for_team(task.team).kind == "human"
            except KeyError:
                human = False
            if human and orch._db.selected_human_leaf_recovery_result(task_id=task_id, agent=task.assigned_agent,
                    session_id=task.current_session_id or "") is not None:
                return False
        return (
            expected_note is None
            or _task_matches_authority_v2_refusal_failure(
                orch, task_id, expected_note,
            )
        )

    tail_failed = False
    try:
        if current():
            if current_owner is None:
                _log_verdict_if_delegated(orch, task_id, success=False)
            else:
                _attachment_owned_effect(orch, current, lambda: _log_verdict_if_delegated(orch, task_id, success=False))
        if current():
            if current_owner is None:
                orch._update_task_history(task_id)
            else:
                _attachment_owned_effect(orch, current, lambda: orch._update_task_history(task_id), history=True)
        if current():
            if current_owner is None:
                _kill_jobs_for_terminating_task(orch, task_id)
            else:
                _attachment_owned_effect(orch, current, lambda: _kill_jobs_for_terminating_task(
                    orch, task_id, current_owner=current,
                ))
        if reclaim_terminal_worktree and not root_transitions and current():
            _reclaim_terminal_task_worktree(orch, task_id, current_owner=current_owner)
    except BaseException:
        tail_failed = True
        raise
    finally:
        if root_transitions:
            try:
                owner_current = current()
            except Exception:
                owner_current = False
                logger.exception('root worktree failure-tail owner unavailable')
            _root_worktree_batch(orch, root_transitions, teardown_incomplete=tail_failed or not owner_current)



def _handoff_consumed_recovery_terminal_effects(
    orch: "Orchestrator", task_id: str, agent: str, session_id: str,
    result_row_id: int, terminal_status: str, *,
    after_recovery_cleanup: Callable[[], None] | None = None,
    root_transitions: list | None = None,
    after_recovery_parent_effect: Callable[[], None] | None = None,
) -> None:
    """Capture a consumed recovery's exact jobs before asynchronous effects.

    The settlement transaction owns the current-receipt recheck and durable
    ``task_ended`` write.  It releases SQLite before the runner's process wait;
    the async cleanup receives only the captured IDs and fences parent delivery
    with the same consumed owner predicate.
    """
    task = orch._db.get_task(task_id)
    if terminal_status == TaskStatus.FAILED.value and task is not None and task.task_type == "subtask":
        try:
            human = orch.teams.manager_for_team(task.team).kind == "human"
        except KeyError:
            human = False
        if human:
            return _submit_human_failed_recovery(orch, task_id, agent, session_id, result_row_id,
                parent_effect=after_recovery_cleanup,
                bookkeeping=after_recovery_parent_effect)
    recovery_job_ids = orch._db.settle_consumed_task_completion_recovery_jobs(
        task_id=task_id, agent=agent, recovery_session_id=session_id,
        result_row_id=result_row_id, terminal_status=terminal_status,
        finished_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )
    if recovery_job_ids is None:
        if root_transitions:
            _root_worktree_batch(orch, root_transitions, teardown_incomplete=True)
        return
    # A valid zero-job receipt has no asynchronous termination boundary.  Its
    # parent handoff must not be lost merely because there is nothing for the
    # runner to drain.
    if not recovery_job_ids:
        handed_off = False
        if after_recovery_cleanup is not None:
            with _startup_sweep_effect(orch):
                handed_off = orch._db.handoff_consumed_task_completion_recovery_parent_effect(
                    task_id=task_id, agent=agent, recovery_session_id=session_id,
                    result_row_id=result_row_id, terminal_status=terminal_status,
                    effect=after_recovery_cleanup,
                )
        if handed_off and after_recovery_parent_effect is not None:
            with _startup_sweep_effect(orch):
                after_recovery_parent_effect()
        if root_transitions:
            current_owner = orch._db.consumed_task_completion_recovery_owner_is_current(
                task_id=task_id, agent=agent, recovery_session_id=session_id,
                result_row_id=result_row_id, terminal_status=terminal_status)
            _root_worktree_batch(orch, root_transitions, teardown_incomplete=not current_owner)
        return
    _kill_jobs_for_terminating_task(
        orch, task_id,
        recovery_owner=(agent, session_id, result_row_id, terminal_status),
        recovery_job_ids=recovery_job_ids,
        root_transitions=root_transitions,
        after_recovery_cleanup=after_recovery_cleanup,
        after_recovery_parent_effect=after_recovery_parent_effect,
    )


def _kill_jobs_for_terminating_task(
    orch: "Orchestrator", task_id: str, *,
    recovery_owner: tuple[str, str, int, str] | None = None,
    recovery_job_ids: tuple[str, ...] | None = None,
    after_recovery_cleanup: Callable[[], None] | None = None,
    root_transitions: list | None = None,
    after_recovery_parent_effect: Callable[[], None] | None = None,
    human_recovery_operation: _HumanFailedRecoveryOperation | None = None,
    current_owner: Callable[[], bool] | None = None,
) -> asyncio.Task | threading.Thread | None:
    """Fire-and-forget: kill all in-flight persistent jobs owned by ``task_id``.

    Called from ``_complete`` and ``_fail`` whenever a task transitions to a
    terminal state. The kill runs out-of-band so task-row progression never
    blocks on job cleanup (5s SIGTERM grace + SIGKILL).

    The DB row update for the killed jobs is a backstop in case the runner's
    own bookkeeping (via ``_KILL_REASON_OVERRIDE``) doesn't get to commit —
    e.g., the run_step thread exits before the runner coroutine finishes its
    final UPDATE. With persistent jobs the runner is its own background task
    and should complete normally; this path matters mostly during shutdown
    races. We use the same loop-detection / daemon-thread fallback as
    ``Orchestrator.notify_failed`` because ``run_step`` runs on a thread-pool
    worker with no event loop of its own.
    """
    db = orch._db
    if current_owner is not None and not current_owner():
        startup = _startup_sweep_owner(orch)
        if startup is not None:
            startup.local_hold()
        return
    inflight_map = (
        {job_id: task_id for job_id in recovery_job_ids}
        if recovery_job_ids is not None else db.get_running_job_task_ids()
    )
    if recovery_job_ids is None and not any(v == task_id for v in inflight_map.values()):
        return

    from datetime import datetime, timezone

    import asyncio
    import threading

    from runtime.daemon.jobs_runner import terminate_jobs_for_task

    async def _kill_and_backstop() -> None:
        if human_recovery_operation is not None:
            operation = human_recovery_operation
            org = getattr(getattr(orch, "_workflow_drafts", None), "org", None)
            queue = getattr(orch, "_queue", None)
            supervisor = getattr(orch, "_host_supervisor", None)
            if (org is None or (queue is not None and getattr(queue, "_stopping", False))
                    or (supervisor is not None and supervisor._admission.is_shutdown())):
                operation.finish("recovery_required")
                return
            authority = org.workflow_authority
            # This task may start after a writer/cancellation won. Admit the
            # actual opaque-control action on its owning loop immediately
            # before entering the native drain, without awaiting under locks.
            if authority._async_writer_lock.locked() or not authority._publisher_lock.acquire(blocking=False):
                operation.drain = None
                _retry_human_failed_recovery(orch, operation)
                return
            acquired_db = False
            try:
                if authority._async_writer_lock.locked() or not db._lock.acquire(blocking=False):
                    operation.drain = None
                    _retry_human_failed_recovery(orch, operation)
                    return
                acquired_db = True
                disposition, context = db.human_failed_recovery_context(
                    task_id=task_id, agent=operation.agent, session_id=operation.session_id,
                    result_row_id=operation.result_id,
                )
                if (disposition != "eligible" or context["episode"]["state"] != "callback_consumed"
                        or not operation.matches_context(context)):
                    operation.finish(disposition if disposition != "eligible" else "lost_owner")
                    return
            except Exception:
                logger.exception("human failed recovery native drain admission failed")
                operation.finish("recovery_required")
                return
            finally:
                if acquired_db:
                    db._lock.release()
                authority._publisher_lock.release()
            try:
                await terminate_jobs_for_task(task_id, db=orch._db, org_slug=orch._slug, inflight_to_task=inflight_map)
            except asyncio.CancelledError:
                operation.finish("recovery_required")
                return
            except Exception:
                logger.exception("human failed recovery job drain failed")
                operation.finish("recovery_required")
                return
            # Keep the original K and callback. Busy here retries only this
            # handoff; it never settles/drains the already finished jobs again.
            operation.phase = "parent_handoff"
            _drive_human_failed_recovery(orch, operation)
            return
        with db._lock:
            if current_owner is not None and not current_owner():
                startup = _startup_sweep_owner(orch)
                if startup is not None:
                    startup.local_hold()
                return
        drain_failed = False
        try:
            await terminate_jobs_for_task(task_id, db=orch._db, org_slug=orch._slug, inflight_to_task=inflight_map)
            # Backstop DB update — guarded by status='running' so we don't trample
            # the runner's own terminal write if it got there first.  The database
            # helper owns only this short transaction; never hold its lock across
            # termination, signals, or the runner's grace-period wait above.
            now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            if recovery_owner is None:
                with _startup_sweep_effect(orch):
                    if current_owner is None:
                        db.backstop_terminated_task_jobs(task_id, finished_at=now)
                    else:
                        _attachment_owned_effect(orch, current_owner, lambda: db.backstop_terminated_task_jobs(task_id, finished_at=now))
            else:
                agent, session_id, result_row_id, terminal_status = recovery_owner
                handed_off = False
                if after_recovery_cleanup is not None:
                    with _startup_sweep_effect(orch):
                        handed_off = db.handoff_consumed_task_completion_recovery_parent_effect(
                            task_id=task_id, agent=agent,
                            recovery_session_id=session_id, result_row_id=result_row_id,
                            terminal_status=terminal_status,
                            effect=after_recovery_cleanup,
                        )
                if after_recovery_cleanup is not None:
                    startup = _startup_sweep_owner(orch)
                    if startup is not None:
                        if handed_off is False:
                            startup.local_hold()
                        elif handed_off is not True:
                            startup.negative('startup_parent_handoff_unknown')
                if handed_off and after_recovery_parent_effect is not None:
                    with _startup_sweep_effect(orch):
                        after_recovery_parent_effect()
        except BaseException as exc:
            drain_failed = True
            startup = _startup_sweep_owner(orch)
            if startup is not None:
                startup.fail(exc)
            raise
        finally:
            if root_transitions:
                startup = _startup_sweep_owner(orch)
                try:
                    # Do not shadow the closure's current_owner predicate.
                    # A suppressed cold tail only merges its capture: no DB
                    # ownership reread, report or cleanup executor escape.
                    batch_owner_current = False
                    if startup is None or startup.admission_open():
                        if recovery_owner is not None:
                            agent, session_id, result_row_id, terminal_status = recovery_owner
                            batch_owner_current = db.consumed_task_completion_recovery_owner_is_current(
                                task_id=task_id, agent=agent, recovery_session_id=session_id,
                                result_row_id=result_row_id, terminal_status=terminal_status)
                        else:
                            with db._lock:
                                batch_owner_current = current_owner is None or current_owner()
                    await _await_root_worktree_batch(orch, root_transitions,
                        teardown_incomplete=drain_failed or not batch_owner_current)
                except BaseException as exc:
                    if startup is None:
                        raise
                    startup.fail(exc)
                    if not drain_failed:
                        raise

    startup = _startup_sweep_owner(orch)
    if startup is not None:
        return startup.start_tail(lambda: asyncio.run(_kill_and_backstop()))
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop — run_step thread-pool worker. Spawn a daemon thread
        # that owns its own loop.
        threading.Thread(
            target=lambda: asyncio.run(_kill_and_backstop()),
            daemon=True,
        ).start()
    else:
        return loop.create_task(_kill_and_backstop())




def _delegated_reviewer(orch: "Orchestrator", task) -> str:
    """Human teams attribute agent outcomes to the executable parent owner."""
    db = orch._db
    parent = db.get_task(task.parent_task_id) if task.parent_task_id else None
    try:
        child_team = orch.teams.manager_for_team(task.team)
        if child_team.kind != "human":
            return orch.teams.manager_for_team(parent.team if parent else task.team).name or "unknown_manager"
        from runtime.orchestrator import prompt_loader
        from runtime.orchestrator._paths import OrgPaths
        owner = None if parent is None else parent.assigned_agent
        org = getattr(getattr(orch, "_workflow_drafts", None), "org", None)
        if (org is None or not owner or parent.team != task.team or parent.id == task.id
                or parent.parent_task_id == task.id or parent.task_type not in ("task", "subtask")):
            return "unknown_manager"
        seen = {task.id}
        ancestor = parent
        while ancestor is not None:
            if ancestor.id in seen:
                return "unknown_manager"
            seen.add(ancestor.id)
            if ancestor.parent_task_id is None:
                break
            ancestor = db.get_task(ancestor.parent_task_id)
            if ancestor is None:
                return "unknown_manager"
        active = {definition.name: definition for definition in prompt_loader.list_agents(OrgPaths(root=org.root))}
        definition = active.get(owner)
        child = active.get(task.assigned_agent)
        if (definition is None or child is None or definition.team != task.team
                or child.team != task.team or child.role != "worker"
                or definition.role != "worker" or owner not in child_team.workers
                or task.assigned_agent not in child_team.workers):
            return "unknown_manager"
        return owner
    except (KeyError, ValueError):
        return "unknown_manager"


def _log_verdict_if_delegated(
    orch: "Orchestrator", task_id: str, *, success: bool,
) -> None:
    """Emit the review_verdict audit row for a delegated subtask.

    The parent task owner is the implicit reviewer of every delegated subtask.
    The audit row's verdict is a DISTINCT fact from the subtask's completion
    status: when the subtask reported an explicit structured
    ``CompletionReport.verdict`` (APPROVE / PASS / REQUEST_CHANGES / ...), that
    reported workflow verdict is preserved verbatim. Only when no structured
    verdict is present do we fall back to the legacy implicit mapping
    (COMPLETED -> "approved", FAILED -> "rejected").

    Audit rows are how the founder reviews which agents need attention; they
    are the canonical record of delegation outcomes.
    """
    task = orch._db.get_task(task_id)
    if task is None or task.parent_task_id is None:
        return
    agent = task.assigned_agent
    if not agent or orch.teams.is_team_manager(agent) or agent in ("orchestrator", "unknown"):
        return
    if not success:
        with orch._db._lock:
            current = orch._db.get_task(task_id)
            try:
                human = current is not None and orch.teams.manager_for_team(current.team).kind == "human"
            except KeyError:
                human = False
            if human:
                if current.assigned_agent != agent or (current.task_type == "subtask" and orch._db.selected_human_leaf_recovery_result(
                        task_id=task_id, agent=agent, session_id=current.current_session_id or "") is not None):
                    # The selected effect owner alone inserts or reuses this
                    # row; an ordinary late tail cannot append a verdict.
                    return
                orch._audit.log_review_verdict(
                    task_id=task_id, reviewer=_delegated_reviewer(orch, current),
                    verdict=_verdict_for_delegated(orch, task_id, success=success),
                    feedback=current.note, reviewed_agent=agent,
                )
                return
    reviewer = _delegated_reviewer(orch, task)
    orch._audit.log_review_verdict(
        task_id=task_id,
        reviewer=reviewer,
        verdict=_verdict_for_delegated(orch, task_id, success=success),
        feedback=task.note,
        reviewed_agent=agent,
    )


from runtime.orchestrator.task_terminal_readers import _verdict_for_delegated


def _advance_chain_for_completed_child(
    *,
    orch: "Orchestrator",
    parent_task_id: str,
    child_task_id: str,
    report=None,
) -> str:
    """Inspect the parent's active_chain against the just-completed subtask's
    report. Either spawn the next leg ("advance") or clear the chain so the
    caller falls through to the normal parent-wake path ("wake").

    Returns "advance" or "wake". When "advance" is returned, the parent is
    NOT re-enqueued and orchestration_step_count is NOT bumped.

    Only called when child.status == COMPLETED — FAILED subtasks are handled
    by the bounded-wake logic in _enqueue_parent_if_waiting (TASK-573).

    THR-211: ``report`` carries the exact authenticated CompletionReport for
    the child's current session (``_child_landed_terminal_report``). When it
    is provided it is authoritative — a newer unrelated row can never advance
    or clear the chain. When it is ``None`` (a genuinely legacy child with no
    agent/session fingerprint, or a direct caller that does not scope), the
    most-recent-row read is kept for compatibility. When it is the
    ``_NO_AUTHENTICATED_REPORT`` sentinel (a modern child whose exact report
    is missing or unacceptable — exact-row miss, wrong agent/session,
    malformed/unknown/blocked/nonterminal) the chain FAILS CLOSED: it is
    cleared and the parent wakes, and task-wide evidence is never consulted.
    """
    from runtime.models import TaskRecord
    from runtime.orchestrator.chain import (
        ChainState,
        build_prior_leg_context,
        compute_advance_action,
    )

    parent = orch._db.get_task(parent_task_id)
    if parent is None or parent.active_chain is None:
        return "wake"

    chain = ChainState.deserialize(parent.active_chain)
    if report is _NO_AUTHENTICATED_REPORT:
        # TASK-5818: a modern fingerprint exists but the exact authenticated
        # report is missing/unacceptable — fail closed.  Never consult the
        # task-wide newest-row read: a wrong-agent/wrong-session (or any
        # unrelated) row must not gate chain advancement.
        orch._db.update_task_active_chain(parent_task_id, None)
        return "wake"
    if report is None:
        report = orch._db.get_latest_completion_report(child_task_id)
    if report is None:
        orch._db.update_task_active_chain(parent_task_id, None)
        return "wake"

    completed_child = orch._db.get_task(child_task_id)
    completed_agent = completed_child.assigned_agent if completed_child is not None else None
    action = compute_advance_action(
        chain=chain,
        report=report,
        completed_agent=completed_agent,
        reviewer_agents=_reviewer_agents_for(orch),
    )
    if action.kind == "wake":
        orch._db.update_task_active_chain(parent_task_id, None)
        return "wake"

    # THR-109: validate attachments FIRST, before any state change.
    # If validation fails the chain is cleared and parent wakes — no
    # child, no link/audit, no queue entry, no advanced active_chain.
    prior_context = build_prior_leg_context(
        child_task_id=child_task_id, report=report,
    )
    full_brief = action.next_leg.prompt + prior_context

    chain_att_params: list[dict] | None = None
    if action.next_leg.attachments:
        from runtime.infrastructure.task_attachment_store import (
            TaskAttachmentStore,
            validate_task_attachment_refs,
        )
        store = TaskAttachmentStore(orch._paths.task_attachments_dir)
        try:
            prevalidated = validate_task_attachment_refs(
                store=store, db=orch._db, refs=list(action.next_leg.attachments),
            )
            if prevalidated:
                chain_att_params = _prepare_attachment_params(orch, prevalidated)
        except ValueError:
            # Attachment validation failed — do NOT spawn the leg.
            # Clear the chain so the parent can wake and handle the failure.
            orch._db.update_task_active_chain(parent_task_id, None)
            return "wake"

    # Atomic chain advance: update parent active_chain + insert next child +
    # attachment links/audit in a single transaction. A crash or write failure
    # rolls back everything — no orphan chain state, no orphan child, no
    # orphan link/audit/claim, no queue side effect.
    next_child_id = orch._db.next_task_id()
    chain.step_index = action.next_step_index
    chain_json = chain.serialize()

    next_child = TaskRecord(
        id=next_child_id,
        team=parent.team,
        brief=full_brief,
        parent_task_id=parent_task_id,
        assigned_agent=action.next_leg.agent,
        status=TaskStatus.PENDING,
        session_timeout_seconds=parent.session_timeout_seconds,
        task_type="subtask",
    )
    if not orch._db.try_advance_chain(
        parent_id=parent_task_id,
        active_chain_json=chain_json,
        next_child=next_child,
        attachments=chain_att_params,
        uploaded_by="orchestrator",
    ):
        # Chain advance + child insert failed — transaction rolled back.
        # No partial state remains; parent wakes normally.
        return "wake"

    orch._audit.log_chain_auto_advance(
        parent_task_id=parent_task_id,
        leg_index=action.next_step_index,
        spawned_child_id=next_child_id,
        triggering_child_id=child_task_id,
        triggering_verdict=report.verdict,
        chain_origin_step_audit_id=chain.step_audit_id,
    )
    _enqueue_task_generation_aware(orch, next_child_id)
    return "advance"


from runtime.orchestrator.task_terminal_readers import _is_carrier


def _carrier_fail_on_verdict_mismatch(
    orch: "Orchestrator", parent: "TaskRecord",
    child_task_id: str, chain_snapshot: str | None, report=None,
) -> bool:
    """After a carrier's chain leg completed but the chain did not advance
    (outcome == "wake"), check if this was a verdict mismatch.  If so, fail
    the carrier immediately (fail-closed at carrier).  Returns True if the
    carrier was failed, False otherwise (including non-carriers).

    THR-211: ``report`` carries the exact authenticated CompletionReport for
    the child's current session when one exists; it is authoritative over the
    most-recent-row read, which remains the fallback when no fingerprint
    exists (compatibility with pre-THR-211 behavior).  The
    ``_NO_AUTHENTICATED_REPORT`` sentinel (modern fingerprint, no acceptable
    exact report) fails the carrier closed — an unverifiable leg outcome must
    never complete or advance a carrier on task-wide evidence.
    """
    if chain_snapshot is None:
        return False
    if not _is_carrier(orch, parent):
        return False
    from runtime.orchestrator.chain import ChainState, compute_advance_action
    chain = ChainState.deserialize(chain_snapshot)
    if report is _NO_AUTHENTICATED_REPORT:
        # TASK-5818: the child has a modern fingerprint but no acceptable
        # exact authenticated report — the leg's outcome cannot be verified.
        # Fail the carrier closed rather than complete or advance it on
        # task-wide evidence.
        expected = chain.current_expect_verdict()
        note = (
            f"carrier leg {child_task_id} outcome unverifiable: "
            "no exact authenticated report"
        )
        if expected is not None:
            note += f" (expected {expected!r})"
        _fail(orch, parent.id, note=note)
        # Feed carrier failure into the fan-out parent's barrier.
        _enqueue_parent_if_waiting(orch, parent.id)
        return True
    if report is None:
        report = orch._db.get_latest_completion_report(child_task_id)
    if report is None:
        # No report for the completed child — fail-closed only when a verdict
        # expectation exists (pre-existing semantics).
        expected = chain.current_expect_verdict()
        if expected is None:
            return False
        _fail(
            orch, parent.id,
            note=f"carrier verdict mismatch: expected {expected!r}, got None",
        )
        # Feed carrier failure into the fan-out parent's barrier.  Returning
        # True without these side effects strands both carrier and parent.
        _enqueue_parent_if_waiting(orch, parent.id)
        return True
    completed_child = orch._db.get_task(child_task_id)
    completed_agent = completed_child.assigned_agent if completed_child is not None else None
    action = compute_advance_action(
        chain=chain,
        report=report,
        completed_agent=completed_agent,
        reviewer_agents=_reviewer_agents_for(orch),
    )
    # A verdict mismatch OR a THR-175 reviewer non-approve (omitted-expectation
    # reviewer leg) must fail the whole carrier — never chain-complete it as a
    # false success.
    if action.kind == "wake" and action.reason in ("verdict_mismatch", "reviewer_non_approve"):
        _fail(orch, parent.id,
               note=f"carrier verdict mismatch: expected {action.expected!r}, got {action.actual!r}")
        # Feed carrier failure into the fan-out parent's barrier.
        _enqueue_parent_if_waiting(orch, parent.id)
        return True
    return False


def _carrier_fail_immediate(
    orch: "Orchestrator", parent: "TaskRecord",
    child_task_id: str,
) -> bool:
    """A carrier's chain leg failed: fail the whole carrier immediately
    (fail-closed at carrier).  Returns True if the carrier was failed,
    False for non-carriers."""
    if not _is_carrier(orch, parent):
        return False
    child = orch._db.get_task(child_task_id)
    report = orch._db.get_latest_completion_report(child_task_id)
    verdict = report.verdict if report is not None and report.verdict else "(none)"
    revisit = child.revisit_of_task_id if child is not None else None
    reason = child.note if child is not None and child.note else "(no summary)"
    # The carrier retains its own terminal outcome, while its existing note
    # truthfully carries the leaf that caused it.  Fan-out join context reads
    # direct child rows, so this preserves causal provenance without a new
    # stored field or accidentally selecting an unrelated sibling.
    _fail(
        orch,
        parent.id,
        note=(
            f"carrier chain leg {child_task_id} failed; "
            f"causal_leaf_id={child_task_id}; causal_status="
            f"{child.status.value if child is not None else '(unknown)'}; "
            f"causal_verdict={verdict}; "
            f"causal_revisit_of_task_id={revisit or '(none)'}; "
            f"causal_reason={reason}"
        ),
    )
    # Feed carrier failure into the fan-out parent's barrier.
    _enqueue_parent_if_waiting(orch, parent.id)
    return True


def _carrier_complete_on_chain_complete(
    orch: "Orchestrator", parent: "TaskRecord",
) -> bool:
    """After a carrier's chain completed successfully (all legs done,
    final leg verdict matched), complete the carrier DIRECTLY — NO
    orch._run_agent session, NO manager-wake.  Then feed the carrier's
    completion into the fan-out parent's barrier EXACTLY ONCE.

    Returns True if the carrier was completed, False for non-carriers.
    """
    if not _is_carrier(orch, parent):
        return False
    # Complete the carrier directly — it has no session of its own.
    _complete(orch, parent.id,
              note="carrier chain complete")
    # Feed carrier completion into the fan-out parent's barrier.
    _enqueue_parent_if_waiting(orch, parent.id)
    return True


# Historical THR-078 lineage utility retained for old records and diagnostic
# readers. Current failure routing does not call it: a valid retry link is
# mechanical provenance and every unresolved failed leaf returns to its owner.
_FAILURE_ROUND_BOUND = 2  # historical failure-recovery design reference
_SLICE_RETRY_CEILING = 1  # historical diagnostic label; not a routing guard


def _is_slice_retry_exhausted(
    orch: "Orchestrator", child: "TaskRecord", parent: "TaskRecord",
) -> bool:
    """Classify legacy retry lineage for diagnostic readers only.

    Current failure routing does not call this helper. A valid
    ``revisit_of_task_id`` is mechanical provenance; an unresolved failed
    leaf returns to its owner regardless of this historical classification.

    Derivation: follow the child's ``revisit_of_task_id`` chain.  A FAILED
    ancestor under the same parent counts toward the ceiling, but a COMPLETED
    or SUPERSEDED ancestor retires earlier failures in that lineage (THR-183),
    so the scan stops at the nearest COMPLETED/SUPERSEDED ancestor.  This uses
    ONLY the existing ``revisit_of_task_id`` column on TaskRecord (no schema
    migration).
    """
    if child.revisit_of_task_id is None:
        return False
    db = orch._db
    # Walk the revisit chain from this child.
    # walk_revisit_chain returns [child, predecessor, ..., original].
    from runtime.infrastructure.database import LineageTooDeep
    try:
        chain = db.walk_revisit_chain(child.id, max_hops=200, truncate=True)
    except LineageTooDeep:
        chain = []
    # Skip the first entry (the child itself); check each ancestor for
    # same-parent membership.  A FAILED predecessor counts toward the ceiling;
    # a COMPLETED/SUPERSEDED predecessor resets the lineage (retires earlier
    # failures); anything else is ignored.
    for ancestor in chain[1:]:
        if ancestor.parent_task_id != parent.id:
            continue
        if ancestor.status in (TaskStatus.COMPLETED, TaskStatus.SUPERSEDED):
            return False
        if ancestor.status == TaskStatus.FAILED:
            return True
    return False


def _current_unresolved_failed_leaves(
    orch: "Orchestrator",
    failed_siblings: list["TaskRecord"],
    parent: "TaskRecord",
) -> list["TaskRecord"]:
    """Return the current unresolved FAILED leaf of each logical retry lineage.

    A FAILED child that has a later COMPLETED or SUPERSEDED descendant in the
    same ``revisit_of_task_id`` lineage is retired and must not contribute to
    ceiling evaluation.  A FAILED child with a later FAILED descendant is not
    the leaf — the descendant is.  Only terminal FAILED leaves of non-retired
    lineages are returned.
    """
    # Map every sibling by id so we can follow revisit links forward.
    all_siblings = [
        orch._db.get_task(cid) for cid in orch._db.get_children(parent.id)
    ]
    sibling_by_id = {s.id: s for s in all_siblings if s is not None}

    predecessor_to_successors: dict[str, list["TaskRecord"]] = {}
    for s in sibling_by_id.values():
        pred = s.revisit_of_task_id
        if pred is not None and pred in sibling_by_id:
            predecessor_to_successors.setdefault(pred, []).append(s)

    leaves: list["TaskRecord"] = []
    seen: set[str] = set()

    def _is_retired(task_id: str, visited: set[str]) -> bool:
        """True if any descendant in the same lineage is COMPLETED/SUPERSEDED."""
        for succ in predecessor_to_successors.get(task_id, []):
            if succ.id in visited:
                continue
            visited.add(succ.id)
            if succ.status in (TaskStatus.COMPLETED, TaskStatus.SUPERSEDED):
                return True
            if _is_retired(succ.id, visited):
                return True
        return False

    def _collect_leaf(task_id: str, visited: set[str]) -> "TaskRecord" | None:
        """Return the terminal FAILED leaf reachable forward, if any."""
        successors = predecessor_to_successors.get(task_id, [])
        if not successors:
            task = sibling_by_id.get(task_id)
            if task is not None and task.status == TaskStatus.FAILED:
                return task
            return None
        for succ in successors:
            if succ.id in visited:
                continue
            visited.add(succ.id)
            leaf = _collect_leaf(succ.id, visited)
            if leaf is not None:
                return leaf
        return None

    for failed in failed_siblings:
        if failed.id in seen:
            continue
        if _is_retired(failed.id, {failed.id}):
            continue
        leaf = _collect_leaf(failed.id, {failed.id})
        if leaf is not None and leaf.id not in seen:
            seen.add(leaf.id)
            leaves.append(leaf)
    return leaves


def _format_slice_retry_exhausted_reason(
    orch: "Orchestrator", leaf: "TaskRecord",
) -> str:
    """Build an escalation reason that names the causal terminal event."""
    results = orch._db.get_task_results(leaf.id)
    latest = results[-1] if results else {}
    verdict = latest.get("verdict") or "n/a"
    return (
        f"per-slice retry ceiling ({_SLICE_RETRY_CEILING}) exhausted: "
        f"causal terminal event {leaf.id} status={leaf.status.value} "
        f"verdict={verdict}: {leaf.note or '(no note)'}"
    )


# THR-211 (TASK-5818 fix-forward): ``_child_landed_terminal_report`` returns
# None for BOTH a genuinely legacy child (no agent/session fingerprint) and a
# modern child whose exact authenticated report is missing or unacceptable
# (exact-row miss, or an exact row whose status is not ``completed`` — blocked,
# unknown, nonterminal).  Chain advancement may fall back to the task-wide
# newest-row read ONLY for the former; for the latter it must fail closed and
# never consult task-wide evidence (a wrong-agent/wrong-session row must never
# gate the chain).  This sentinel marks the modern-but-unverifiable case.
class _NoAuthenticatedReport:
    """Sentinel: the child carries a modern ``(assigned_agent,
    current_session_id)`` fingerprint but has no acceptable exact authenticated
    completion report.  Chain advancement MUST fail closed on this value — it
    must never fall back to the task-wide newest-row read."""

    __slots__ = ()

    def __repr__(self) -> str:  # debugging aid
        return "_NO_AUTHENTICATED_REPORT"


_NO_AUTHENTICATED_REPORT = _NoAuthenticatedReport()


from runtime.orchestrator.task_terminal_readers import _child_has_modern_fingerprint


from runtime.orchestrator.task_terminal_readers import _child_landed_terminal_report


def _child_has_landed_terminal_result(orch: "Orchestrator", child: "TaskRecord") -> bool:
    """THR-211: True when the child's CURRENT session has durably landed a
    structured terminal completion report even though the task row still reads
    ``in_progress`` (the status transition is deferred to executor/session
    finalization by ``_consume_completion_report``).

    See ``_child_landed_terminal_report`` for the authority predicate.
    """
    return _child_landed_terminal_report(orch, child) is not None


def _enqueue_parent_if_waiting(
    orch: "Orchestrator", task_id: str, *,
    root_auto_revisit_spawned: bool = False,
    current_owner: Callable[[], bool] | None = None,
) -> None:
    with _startup_sweep_effect(orch):
        return _enqueue_parent_if_waiting_owned(
            orch, task_id, root_auto_revisit_spawned=root_auto_revisit_spawned,
            current_owner=current_owner,
        )


def _enqueue_parent_if_waiting_owned(
    orch: "Orchestrator",
    task_id: str,
    *,
    root_auto_revisit_spawned: bool = False,
    current_owner: Callable[[], bool] | None = None,
) -> None:
    """Idempotent: advance the parent only if it's actually waiting on THIS
    lineage (in_progress+delegated) AND all its children are now terminal.

    Per-slice revisit-lineage rule (THR-078, TASK-573 bounded failure-recovery):
      - every subtask COMPLETED → enqueue parent for its next manager
        decision step (unchanged happy path).
      - a subtask FAILED → retain the current unresolved FAILED leaf, clear
        any active chain, and enqueue the owning manager for a bounded
        decision step. The parent receives the failed subtask's reason so it
        can decide whether to re-dispatch unchanged work or issue a revised
        assignment. A linked historical retry failure is causal context only;
        it does not cause a runtime escalation or upward failure cascade.

    ``root_auto_revisit_spawned`` is a retained compatibility/bookkeeping
    input. All current production callers (opaque-failure branches and
    startup sweep) pass ``False`` — no daemon auto-successor exists
    (TASK-3604). The boolean preserves call-site symmetry for the bounded
    parent wake / per-slice ownership contract; it does not signal that a
    root has been auto-revisited. See the retired spec
    2026-05-25-session-timeout-auto-route-design.md §6 for historical context.

    Chain handling: a FAILED chain leg clears the active chain and falls
    through to the bounded-wake logic below. A COMPLETED chain leg tries
    to auto-advance as before; on mismatch it clears the chain and wakes
    the parent.
    """
    if current_owner is not None and not current_owner():
        return
    task = orch._db.get_task(task_id)
    if task is None or task.parent_task_id is None:
        return
    from runtime.workflows.recovery import classify_task
    ownership = classify_task(orch._db, task_id, org_slug=getattr(orch, "_slug", None))
    if ownership.kind != 'legacy':
        if ownership.kind != 'coding_source_waiting':
            return
        # The original source may notify its ordinary caller only after the
        # independent complete reader admits the committed structural return.
        from runtime.workflows.coding_start import queued_storage
        with orch._db._lock:
            entry = queued_storage(orch._db._conn, orch._slug).runs[ownership.intent_id]
            if not entry['native'].get('returned') or entry['source']['task_id'] != task_id:
                return
    if classify_task(orch._db, task.parent_task_id, org_slug=getattr(orch, "_slug", None)).kind != 'legacy':
        return
    parent = orch._db.get_task(task.parent_task_id)
    # Path B: a delegating parent is in_progress(delegated). The non-cascading
    # failure-recovery contract (TASK-573/THR-028) below is otherwise unchanged.
    if parent is None or parent.status not in _PARKED_CARRIER_STATUSES:
        return
    if parent.block_kind != BlockKind.DELEGATED:
        return

    def enqueue_parent_once() -> None:
        """Keep a restart-reconstructed wake from duplicating a live tail."""
        if current_owner is not None and not current_owner():
            return
        queue = getattr(orch, "_queue", None)
        if queue is None:
            return
        enqueue_if_absent = getattr(queue, "enqueue_if_absent", None)
        if callable(enqueue_if_absent):
            enqueue_if_absent(
                orch._slug,
                parent.id,
                publisher=lambda: _enqueue_task_generation_aware(orch, parent.id),
            )
            return
        # Lightweight test adapters predate TaskQueue's atomic boundary. The
        # production daemon always wires TaskQueue; retain adapter compatibility
        # without reaching into either queue implementation's private storage.
        _enqueue_task_generation_aware(orch, parent.id)

    # Chain-advance branch: if the parent has an active chain and the just-
    # terminated subtask completed cleanly, try to auto-advance to the next
    # leg instead of waking the parent. FAILED subtasks clear the chain and
    # fall through to the bounded-wake logic below.
    #
    # Phase 2 carrier fail-closed: when the parent is a carrier (its own
    # parent has active_fanout), a verdict-mismatch or a failed leg fails
    # the whole carrier immediately — no partial-chain completion.
    child = orch._db.get_task(task_id)
    if child is not None and parent.active_chain is not None:
        # THR-211: a child whose current session has durably landed a
        # structured terminal report is dispatch-terminal even while its row
        # reads in_progress (status transition deferred to session
        # finalization).  The newest-child guard keeps the recognition
        # at-most-once: after the chain advances, the next leg child is the
        # parent's newest child, so a late re-trigger from the already-
        # recognized child (e.g. its delayed session-finalization
        # consumption) can neither re-enter the advance path nor prematurely
        # clear the chain.
        is_chain_trigger = (
            child.status == TaskStatus.COMPLETED
            or (
                child.status not in TERMINAL_STATES
                and _child_has_landed_terminal_result(orch, child)
            )
        )
        if is_chain_trigger:
            children_ids = orch._db.get_children(parent.id)
            if children_ids and child.id == children_ids[-1]:
                # THR-211: carry the EXACT authenticated report (the
                # (task_id, assigned_agent, current_session_id) fingerprint
                # scoped row) through the chain gate so a newer unrelated
                # row can never advance or clear the chain.
                authenticated_report = _child_landed_terminal_report(orch, child)
                # TASK-5818: None is ambiguous.  A GENUINELY legacy child
                # (no agent/session fingerprint) may still use the unscoped
                # newest-row fallback inside _advance_chain_for_completed_child;
                # a MODERN child whose exact report is missing or unacceptable
                # (exact-row miss, wrong agent/session, malformed/unknown/
                # blocked/nonterminal) must fail closed and must NEVER consult
                # task-wide evidence for chain advancement.
                if (
                    authenticated_report is None
                    and _child_has_modern_fingerprint(child)
                ):
                    authenticated_report = _NO_AUTHENTICATED_REPORT
                # Snapshot the chain BEFORE advance (which may clear it).
                chain_snapshot = parent.active_chain
                outcome = _advance_chain_for_completed_child(
                    orch=orch, parent_task_id=parent.id, child_task_id=task_id,
                    report=authenticated_report,
                )
                if outcome == "advance":
                    return  # next leg spawned; parent stays in_progress(delegated)
                # outcome == "wake" → chain cleared; carrier fail-closed?
                if _carrier_fail_on_verdict_mismatch(
                    orch, parent, task_id, chain_snapshot,
                    report=authenticated_report,
                ):
                    return  # carrier failed; outer _enqueue_parent_if_waiting skipped
                # Chain complete + verdict matched → carrier auto-completes
                # directly (no _run_agent session, no manager-wake).
                if _carrier_complete_on_chain_complete(orch, parent):
                    return  # carrier completed; outer _enqueue_parent_if_waiting skipped
            # Stale re-trigger for a leg the chain already advanced past, or a
            # non-newest child: fall through to sibling-check + parent-wake.
        else:
            # FAILED chain leg: do NOT clear active_chain here. A recovery or
            # startup-style caller may invoke this on a historical FAILED
            # ancestor whose retry lineage was later retired by a COMPLETED or
            # SUPERSEDED descendant. Clearing the chain before the retired-
            # lineage check would mutate parent state for no reason. The chain
            # is cleared only after the sibling evaluation below proves there
            # is a genuine unresolved failure (or a carrier fail-closed case).
            # Carrier fail-closed is applied at that point, not here.
            pass

    siblings = [orch._db.get_task(cid) for cid in orch._db.get_children(parent.id)]
    if any(
        s is None or (
            s.status not in TERMINAL_STATES
            and not _child_has_landed_terminal_result(orch, s)
        )
        for s in siblings
    ):
        return

    failed = [s for s in siblings if s.status == TaskStatus.FAILED]
    if failed:
        # THR-183: use the current unresolved FAILED leaf of each logical
        # retry lineage, not every historical FAILED sibling. A later
        # COMPLETED/SUPERSEDED descendant retires earlier failures in that
        # lineage, so a normal parent wake cannot select a stale reason.

        unresolved_leaves = _current_unresolved_failed_leaves(orch, failed, parent)
        if unresolved_leaves:
            # Genuine unresolved failure: clear active_chain now. For a carrier
            # (its own parent has active_fanout), fail the whole carrier
            # immediately — no partial-chain completion.
            if parent.active_chain is not None:
                if current_owner is not None and not current_owner():
                    return
                orch._db.update_task_active_chain(parent.id, None)
            if _is_carrier(orch, parent):
                causal_leaf = next(
                    (leaf for leaf in unresolved_leaves if leaf.id == task_id),
                    unresolved_leaves[-1],
                )
                if current_owner is not None and not current_owner():
                    return
                _carrier_fail_immediate(orch, parent, causal_leaf.id)
                return  # carrier failure feeds the fan-out parent's barrier

            # A linked historical retry remains truthful causal context in the
            # child's durable row/lineage. It is not a daemon escalation or
            # upward failure cascade: the owning manager decides whether to
            # re-dispatch, revise work, or propose escalation through THR-181.
            # Enqueue the parent for that fresh decision step. Do NOT
            # cascade-fail.
            # NOTE: active_fanout is NOT cleared here — the CAS-winner needs
            # it to inject structured join context (child verdict, confidence,
            # output_dir, failure note) via _inject_fanout_join_context.  The
            # CAS-winner clears active_fanout after injecting join context.
            enqueue_parent_once()
            return

        # All failures are retired (lineage has a later COMPLETED/SUPERSEDED
        # descendant). Treat this as a normal bounded parent wake: queue the
        # parent once, leaving active_chain, active_fanout, status, block_kind,
        # and note completely untouched. No escalation, no audit side effect.
        enqueue_parent_once()
        return

    enqueue_parent_once()


# ---------------------------------------------------------------------------
# Legacy auto-revisit audit readers (TASK-3604: auto-revisit creation removed;
# these readers are preserved for historical compatibility — existing
# auto_revisit_of audit rows must remain readable).
# ---------------------------------------------------------------------------
def _maybe_resume_blocked_task(
    orch: "Orchestrator",
    task_id: str,
    *,
    trigger: str,
    triggering_job_id: str | None,
) -> bool:
    """Check predicate (all blocking jobs terminal) and enqueue if satisfied.

    READ-ONLY: does NOT mutate task state. The state transition happens at
    run_step_impl step 3's CAS when the worker picks up the enqueued task.

    Returns True if it enqueued; False otherwise. Idempotent — extra enqueues
    are harmless (run_step_impl's CAS admits exactly one).

    Spec: docs/superpowers/specs/2026-05-28-task-blocked-by-job-design.md §5.4
    """
    import json as _json

    db = orch._db
    audit = orch._audit
    task = db.get_task(task_id)
    if task is None:
        return False
    # Path B: a task parked on jobs is in_progress(blocked_on_job).
    if task.status not in _PARKED_CARRIER_STATUSES or task.block_kind != BlockKind.BLOCKED_ON_JOB:
        return False  # silent — steady state

    try:
        job_ids = _json.loads(task.blocked_on_job_ids or "[]")
    except _json.JSONDecodeError:
        audit.log_task_resume_skipped(
            task_id=task_id, reason="empty_job_list",
            blocked_on_job_ids_raw=task.blocked_on_job_ids,
        )
        return False
    if not job_ids:
        audit.log_task_resume_skipped(
            task_id=task_id, reason="empty_job_list",
            blocked_on_job_ids_raw=task.blocked_on_job_ids,
        )
        return False

    _TERMINAL = {"completed", "failed", "rejected"}
    for jid in job_ids:
        if db.get_job_status(jid) not in _TERMINAL:
            return False  # silent — common steady state

    # All terminal — enqueue (trigger metadata preserved through the boundary).
    # This producer's legacy shape is ``queue.enqueue(..., metadata=...)``.
    _enqueue_task_generation_aware(
        orch, task_id,
        metadata={"trigger": trigger, "triggering_job_id": triggering_job_id},
        legacy="enqueue",
    )
    return True


from runtime.orchestrator.task_thread_posting import _append_followup_system_and_reinvoke


def _maybe_post_thread_escalation(
    orch: "Orchestrator",
    task_id: str,
    *,
    reason: str,
    current_owner: Callable[[], bool] | None = None,
) -> None:
    """Post a `task_escalated` SYSTEM message + re-invoke the dispatcher when a
    thread-dispatched task escalates to the founder.

    Unlike `_maybe_post_thread_followup` (terminal-only, root-only because
    terminals cascade up to the parent), escalations do NOT cascade
    (run_step escalate branch: "parent stays in_progress(delegated)") and a team
    manager can escalate at any depth. So we walk ancestors to the chain root,
    then the revisit chain, to find the originating thread.

    Spec: docs/superpowers/specs/2026-06-06-thread-escalation-surfacing-design.md
    """
    if current_owner is not None and not current_owner():
        return
    db = orch._db
    audit = orch._audit

    task = db.get_task(task_id)
    if task is None:
        return
    # Re-read persisted state: the founder may have resolved/cancelled the
    # escalation in the window between try_escalate and this call.
    if task.status != TaskStatus.ESCALATED:
        return

    # Resolve the originating thread. Escalation can fire on a child, so walk
    # ancestors to the chain root first, then the revisit chain (only the
    # dispatched root carries dispatched_from_thread_id).
    from runtime.infrastructure.database import LineageTooDeep  # local: avoid cycle
    try:
        ancestors = db.walk_ancestors(task_id, max_hops=200)
    except LineageTooDeep:
        audit.log_thread_followup_skipped(
            "(unresolved)", original_task_id=task_id, terminal_task_id=task_id,
            reason="chain_too_deep",
        )
        return
    root = ancestors[-1] if ancestors else task
    chain = db.walk_revisit_chain(root.id, max_hops=200, truncate=True)
    original = chain[-1] if chain else root
    thread_id = original.dispatched_from_thread_id
    if thread_id is None:
        # Not a thread-dispatched chain; silent no-op.
        return

    # Thread-state guard.
    thread = db.get_thread(thread_id)
    from runtime.models import ThreadStatus as _ThreadStatus
    if thread is None or thread.status is not _ThreadStatus.OPEN:
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="thread_not_open",
            thread_status=(thread.status.value if thread else "missing"),
            task_status="escalated",
        )
        return

    # Dispatcher identity from the thread_dispatch audit row on the original.
    dispatch_rows = [
        r for r in db.get_audit_logs(thread_id)
        if r["action"] == "thread_dispatch"
        and _payload_dict(r).get("task_id") == original.id
    ]
    if not dispatch_rows:
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="dispatcher_unresolved",
        )
        return
    dispatcher = _payload_dict(dispatch_rows[0])["dispatcher"]

    # `original_task_id` is the revisit-chain origin (thread-dispatch keying);
    # `root_task_id` is the ancestor root of the escalating task. These are
    # equal for a root escalation but differ when a child escalates inside a
    # revisited chain — both are emitted for the dispatcher's downstream use.
    # The result was persisted by the ordinary completion callback before this
    # escalation path ran. Snapshot that exact durable row into the causal
    # message; the continuation route re-reads and compares it, so the caller
    # cannot substitute later repair descendants or prose as authority.
    results = db.get_task_results(task_id)
    latest_result = results[-1] if results else None
    causal_terminal_result = None
    if latest_result is not None:
        causal_terminal_result = {
            "task_id": task_id,
            "result_id": latest_result["id"],
            "terminal_status": latest_result.get("status"),
            "verdict": latest_result.get("verdict"),
            "output_summary": latest_result.get("output_summary"),
            "created_at": latest_result.get("created_at"),
        }
    escalation_rows = [
        row for row in db.get_audit_logs(task_id) if row["action"] == "escalation"
    ]
    causal_escalation_audit_id = escalation_rows[-1]["id"] if escalation_rows else None
    system_payload = {
        "kind_tag": "task_escalated",
        "task_id": task_id,
        "original_task_id": original.id,
        "root_task_id": root.id,
        "status": "escalated",
        "reason": reason,
        "revisit_chain_length": len(chain) if chain else 1,
    }
    if causal_terminal_result is not None:
        system_payload["causal_terminal_result"] = causal_terminal_result
    if causal_escalation_audit_id is not None:
        system_payload["causal_escalation_audit_id"] = causal_escalation_audit_id
    if current_owner is not None and not current_owner():
        return
    _append_followup_system_and_reinvoke(
        orch,
        thread_id=thread_id,
        dispatcher=dispatcher,
        original_id=original.id,
        source_task_id=task_id,
        system_payload=system_payload,
    )


def _maybe_post_thread_followup(
    orch: "Orchestrator",
    task_id: str,
    *,
    status: TaskStatus,
    auto_revisit_spawned: bool,
    current_owner: Callable[[], bool] | None = None,
    revisit_task_id: str | None = None,
) -> None:
    """Post a task-followup system message + mint a re-invocation for the dispatcher.

    Fire predicate (spec §4, §4.1):
      - status == COMPLETED                                → always fire
      - status == SUPERSEDED                      → always fire (terminal,
                                                             completion-class — a
                                                             thread-originated task
                                                             auto-resolved by a
                                                             continuation; THR-018 §3a)
      - status == FAILED                                   → true terminal, fire
                                                             (TASK-3604: daemon
                                                             auto-revisit removed;
                                                             all current production
                                                             callers pass
                                                             ``auto_revisit_spawned=False``)

    Only root tasks fire. Non-root child terminals wake the parent via
    bounded recovery (``_enqueue_parent_if_waiting``) — the parent may
    produce a followup at its own terminal, not via cascade. The originating
    thread is found by walking ``walk_revisit_chain`` backward to the
    earliest predecessor and reading ``dispatched_from_thread_id`` off that
    row.

    ``auto_revisit_spawned`` is a retained compatibility parameter.
    Since TASK-3604 removed daemon auto-revisit creation, all current
    production callers pass ``False``. The parameter and its conditional
    ``reinvoke`` logic are preserved for compatibility with the
    ``_append_followup_system_and_reinvoke`` contract; the
    ``revisit_task_id`` path is historical and never populated by current
    code paths.

    Spec: docs/superpowers/specs/2026-05-28-thread-task-followup-design.md §4-§6
    """
    # Predicate gate — first pass using caller's claim (cheap early-out).
    # CANCELLED is a terminal followup status (Path B, THR-037): a founder cancel
    # writes the stored terminal CANCELLED and replays in the task_failed class.
    # TASK-3604: daemon auto-revisit removed — all current production
    # callers pass auto_revisit_spawned=False, so the system-message-only
    # path with revisit_task_id is historical. The parameter and conditional
    # are retained for compatibility with _append_followup_system_and_reinvoke.
    if status not in (
        TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SUPERSEDED,
        TaskStatus.CANCELLED,
    ):
        return

    if current_owner is not None and not current_owner():
        return
    db = orch._db
    audit = orch._audit
    terminal_task = db.get_task(task_id)
    if terminal_task is None:
        return

    # Re-read the persisted status. Site D's caller passes COMPLETED, but
    # /cancel may have raced past Guard B and flipped the row to a terminal
    # state; _complete() short-circuits in that race. Under Path B (THR-037) a
    # founder cancel now writes the stored terminal CANCELLED (was
    # failed+cancelled_at), and both cancel callers still pass the legacy FAILED
    # hint — so CANCELLED must be honored here or a cancelled root is silently
    # dropped (no task_failed system message, no TASK_FOLLOWUP). Trust the DB,
    # not the caller's claim.
    actual_status = terminal_task.status
    if actual_status not in (
        TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.SUPERSEDED,
        TaskStatus.CANCELLED,
    ):
        # Row isn't terminal yet — caller raced ahead of the DB write.
        # Bail; the eventual real terminal will re-enter this helper.
        return

    # Only root tasks fire. Children are handled at their parent's terminal site.
    if terminal_task.parent_task_id is not None:
        return

    # Find the original dispatched root via the revisit chain.
    # walk_revisit_chain returns [task, predecessor, ..., original].
    # Use a hop bound large enough to cover any realistic revisit chain
    # (200 hops, matching the historical retry-lineage diagnostic bound), and
    # handle LineageTooDeep defensively rather than crashing and silently
    # discarding the followup without any audit trail.
    from runtime.infrastructure.database import LineageTooDeep  # local: avoid cycle
    try:
        chain = db.walk_revisit_chain(task_id, max_hops=200)
    except LineageTooDeep:
        audit.log_thread_followup_skipped(
            "(unresolved)", original_task_id=task_id, terminal_task_id=task_id,
            reason="chain_too_deep",
        )
        return
    original = chain[-1] if chain else terminal_task
    thread_id = original.dispatched_from_thread_id
    if thread_id is None:
        # Not a thread-dispatched chain; silent no-op (no audit).
        return

    # Thread-state guard.
    thread = db.get_thread(thread_id)
    if thread is None:
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="thread_not_open", thread_status="missing",
            task_status=status.value,
        )
        return
    from runtime.models import ThreadStatus as _ThreadStatus
    if thread.status is not _ThreadStatus.OPEN:
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="thread_not_open",
            thread_status=thread.status.value,
            task_status=status.value,
        )
        return

    # Dispatcher identity: read from the thread_dispatch audit row on the
    # ORIGINAL task (revisit roots don't have their own dispatch row).
    dispatch_rows = [
        r for r in db.get_audit_logs(thread_id)
        if r["action"] == "thread_dispatch"
        and _payload_dict(r).get("task_id") == original.id
    ]
    if not dispatch_rows:
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="dispatcher_unresolved",
        )
        return
    dispatcher = _payload_dict(dispatch_rows[0])["dispatcher"]

    # Build system payload using DB-actual status (not the caller's claim) so
    # a cancel race at Site D doesn't emit task_completed for a FAILED row.
    # Completion-class terminals (COMPLETED, SUPERSEDED) → task_completed;
    # failure-class terminals (FAILED, and the Path B stored CANCELLED) → task_failed.
    # The payload's status field carries the precise label (status='cancelled' +
    # cancelled=true for a CANCELLED row), mirroring org_state._TERMINAL_STATUS_TO_EVENT.
    kind_tag = (
        "task_failed"
        if actual_status in (TaskStatus.FAILED, TaskStatus.CANCELLED)
        else "task_completed"
    )
    system_payload = {
        "kind_tag": kind_tag,
        "task_id": task_id,
        "original_task_id": original.id,
        "root_task_id": original.id,
        "status": actual_status.value,
        "final_output_summary": terminal_task.note or "",
        "final_output_dir": terminal_task.final_output_dir,
        "cancelled": terminal_task.cancelled_at is not None,
        "revisit_chain_length": len(chain) if chain else 1,
        "revisit_task_id": revisit_task_id,
    }

    if current_owner is not None and not current_owner():
        return
    _append_followup_system_and_reinvoke(
        orch,
        thread_id=thread_id,
        dispatcher=dispatcher,
        original_id=original.id,
        source_task_id=task_id,
        system_payload=system_payload,
        reinvoke=not auto_revisit_spawned,
    )


from runtime.orchestrator.task_thread_posting import _payload_dict


def _session_failed_note(result, report) -> str:
    """Build an enriched `agent session failed` note.

    The pre-TASK-045 version wrote a bare constant string, so when the
    Claude subprocess finished without calling `happyranch report-completion`
    there was no trace of WHY — rc, stderr, and stdout were all dropped
    on the floor. Now we surface rc and the tail of stderr (or stdout,
    if stderr is empty) so the next class-of-TASK-045 failure is
    self-diagnosing from the audit trail alone.
    """
    bits: list[str] = []
    rc = getattr(result, "returncode", None)
    bits.append(f"rc={rc}" if rc is not None else "rc=?")
    human_error = str(getattr(result, "human_error", "") or "")
    err = human_error or (
        "" if getattr(result, "human_error_inspected", False) else _meaningful_stderr(
            getattr(result, "stderr_tail", "") or ""
        )
    )
    out = (getattr(result, "stdout_tail", "") or "").strip()
    if err:
        preview = err.replace("\n", " ")[:300]
        bits.append(f"stderr: {preview}")
    terminal_error = str(getattr(result, "terminal_error", "") or "").strip()
    # Raw stderr remains the human cause.  A structured classification is
    # supplementary evidence, never a reason to conceal that cause.
    if terminal_error:
        bits.append(f"terminal_error: {terminal_error[:120]}")
    notice = str(getattr(result, "terminal_error_notice", "") or "").strip()
    if notice:
        bits.append(f"notice: {notice[:300]}")
    # A selected human cause or recognized terminal notice must lead any raw
    # stdout diagnostic fragment.  Complete stderr inspection can validly
    # select no cause, while stdout still contains the JSON envelope.
    if not err and out:
        bits.append(f"stdout: {out.replace(chr(10), ' ')[:300]}")
    # ``error`` is built from full provider output, so it must never be copied
    # wholesale into durable task state.  Tails above already preserve it.
    if not err and not out:
        error_str = str(getattr(result, "error", "") or "").replace("\n", " ").strip()
        if error_str:
            bits.append(error_str[:300])
    if report is None and getattr(result, "success", False):
        bits.append("no completion callback")
    return f"agent session failed ({'; '.join(bits)})"


# --- Fan-out spawn + join context helpers ---


def _spawn_fanout_children(
    orch: "Orchestrator",
    parent: "TaskRecord",
    task_id: str,
    next_count: int,
    *,
    expected_claim: RetryClaim,
    children: list[dict],
    width: int,
    manager_agent: str,
    join_summary: str | None = None,
    step_audit_id: int | None = None,
) -> SpawnOutcome:
    """Allocate child IDs, build TaskRecords, atomically insert all N children
    and park the parent in in_progress(delegated) with active_fanout set.
    Shared by the fresh dispatch path and the review-gate re-entry path.

    Phase 2 pipeline: a child with non-empty ``then`` or ``expect_verdict`` is a
    *carrier* — its inline chain is materialized (active_chain set) and its first
    leg is spawned as a subtask of the carrier.  The carrier itself parks as
    delegated; it does NOT run an agent session.  Plain children (empty ``then``,
    no ``expect_verdict``) are dispatched as bare PENDING subtasks, unchanged.

    Only Committed writes fanout_spawned and enqueues child/first-leg tasks.
    InvalidLineage is returned to the consumer for owned retry feedback;
    LostClaim drops stale input. Transaction exceptions propagate.
    """
    from runtime.models import TaskRecord
    from runtime.orchestrator.fanout import FanoutState

    db = orch._db

    # Allocate sequential child IDs.
    child_records: list[TaskRecord] = []
    children_ids: list[str] = []
    base_id = db.next_task_id()
    base_num = int(base_id.split("-")[-1])
    for i, child_info in enumerate(children):
        cid = f"TASK-{base_num + i:03d}"
        children_ids.append(cid)
        has_pipeline = bool(child_info.get("then")) or child_info.get("expect_verdict") is not None
        # THR-056 option 3: mutating fan-out — children targeted at a team
        # manager are task_type='task' so their delegate-chain decisions are
        # parsed and can spawn implementation children. Pipeline carriers
        # (has_pipeline=True) never run agent sessions themselves, so their
        # task_type does not affect decision parsing; keep them as subtask.
        is_manager = (
            not has_pipeline
            and orch.teams is not None
            and bool(orch.teams.is_team_manager(child_info["agent"]))
        )
        child_task_type = "task" if is_manager else "subtask"
        child_records.append(TaskRecord(
            id=cid,
            team=parent.team,
            brief=child_info["prompt"] or "",
            assigned_agent=child_info["agent"],
            parent_task_id=task_id,
            status=TaskStatus.IN_PROGRESS if has_pipeline else TaskStatus.PENDING,
            block_kind=BlockKind.DELEGATED if has_pipeline else None,
            session_timeout_seconds=parent.session_timeout_seconds,
            task_type=child_task_type,
            revisit_of_task_id=child_info.get("revisit_of_task_id"),
        ))

    # THR-109: validate attachment refs per-child (count limit, per-child
    # duplicates) and globally (cross-sibling duplicate keys, existence,
    # claim status). Pipeline carriers own their declared refs; their first
    # legs inherit.
    #
    # Decision-wide validation:
    # 1. Per-child/per-leg: validate each child's top-level refs AND each
    #    pipeline child's nested then[].attachments (count ≤ 5 per leg,
    #    intra-leg duplicates, file exists, not claimed).
    # 2. Cross-sibling global: one shared key ledger (all_keys) catches
    #    duplicates across top-level-to-top-level, carrier-to-nested,
    #    nested-to-nested, and cross-sibling keys. A duplicate, missing,
    #    or already-claimed key anywhere rejects the entire fanout before
    #    child, parent park, active_fanout, link/audit, claim, or queue.
    children_att_params: list[list[dict] | None] = []
    has_any_attachments = any(
        child_info.get("attachments") or any(
            leg.get("attachments") for leg in (child_info.get("then") or [])
        )
        for child_info in children
    )
    if has_any_attachments:
        import json
        from runtime.infrastructure.task_attachment_store import (
            TaskAttachmentStore,
            validate_task_attachment_refs,
        )
        from runtime.models import TaskAttachmentRef
        store = TaskAttachmentStore(orch._paths.task_attachments_dir)

        # One fanout-wide key ledger for all declared refs.
        per_child_prevalidated: list[list[dict]] = []
        all_keys: set[str] = set()
        try:
            for child_index, child_info in enumerate(children):
                location = f"children[{child_index}].attachments"
                child_refs_raw = child_info.get("attachments") or []
                refs = [TaskAttachmentRef(
                    storage_key=r["storage_key"],
                    display_name=r.get("display_name"),
                ) for r in child_refs_raw]
                # Per-child count limit + intra-child duplicates + existence
                # + claim status, AND global duplicate vs all_keys.
                child_pv = validate_task_attachment_refs(
                    store=store, db=db, refs=refs,
                    external_seen_keys=all_keys,
                )
                for pva in child_pv:
                    all_keys.add(pva["storage_key"])

                # Validate pipeline nested attachments (each then[].attachments
                # leg individually, with global duplicate detection).
                pipeline_nested_refs: list[list] = []
                for leg_index, leg in enumerate(child_info.get("then") or []):
                    location = f"children[{child_index}].then[{leg_index}].attachments"
                    leg_refs_raw = leg.get("attachments") or []
                    if not leg_refs_raw:
                        continue
                    leg_refs = [TaskAttachmentRef(
                        storage_key=r["storage_key"],
                        display_name=r.get("display_name"),
                    ) for r in leg_refs_raw]
                    leg_pv = validate_task_attachment_refs(
                        store=store, db=db, refs=leg_refs,
                        external_seen_keys=all_keys,
                    )
                    for pva in leg_pv:
                        all_keys.add(pva["storage_key"])

                per_child_prevalidated.append(child_pv)
        except ValueError as e:
            detail = _classify_attachment_error(e)
            if detail is not None:
                raise _PreChildDecisionAttachmentError(detail, location) from e
            import json as _json
            error_detail = _json.loads(str(e))
            note = (
                f"fanout attachment validation failed: "
                f"{error_detail.get('code', 'unknown')} "
                f"({error_detail.get('storage_key', '')})"
            )
            _fail(orch, task_id, note=note)
            _enqueue_parent_if_waiting(orch, task_id)
            _maybe_post_thread_followup(
                orch, task_id,
                status=TaskStatus.FAILED, auto_revisit_spawned=False,
            )
            return LostClaim()

        # Build per-child params from the per-child prevalidated lists.
        for child_index, child_pv in enumerate(per_child_prevalidated):
            if not child_pv:
                children_att_params.append(None)
            else:
                try:
                    child_params = _prepare_attachment_params(orch, child_pv)
                except ValueError as exc:
                    detail = _classify_attachment_error(exc)
                    if detail is None:
                        raise
                    raise _PreChildDecisionAttachmentError(
                        detail, f"children[{child_index}].attachments",
                    ) from exc
                children_att_params.append(child_params)
    else:
        children_att_params = [None] * len(children)

    # Build FanoutState BEFORE the atomic insert so it's ready to persist.
    fanout_state = FanoutState(
        children_ids=children_ids,
        children_details=children,
        width=width,
        manager_agent=manager_agent,
        join_summary=join_summary,
        status="spawned",
    )
    parent_note = (
        f"Fan-out to {width} children: "
        + ", ".join(
            f"{c['agent']}={cid}"
            for c, cid in zip(children, children_ids)
        )
    )

    # Build pipeline carrier chain data BEFORE the atomic transaction.
    # Carrier chains are materialized inside try_delegate_many so active_chain
    # + first leg are atomic with the fanout spawn — no partial carrier state.
    carrier_chains_data: list[dict] | None = None
    pipeline_indices: set[int] = set()
    # First leg IDs start after the last child ID. Use base_num + len(children)
    # (not another next_task_id() call — children aren't inserted yet so MAX
    # hasn't moved).
    carrier_leg_offset = 0
    for i, child_info in enumerate(children):
        has_pipeline = bool(child_info.get("then")) or child_info.get("expect_verdict") is not None
        if not has_pipeline:
            continue
        pipeline_indices.add(i)
        # Build the carrier chain and first leg data.
        from runtime.orchestrator.chain import ChainState
        from runtime.models import ChainLeg, TaskAttachmentRef
        then_legs = [
            ChainLeg(
                agent=leg["agent"], prompt=leg["prompt"],
                expect_verdict=leg.get("expect_verdict"),
                attachments=[TaskAttachmentRef(
                    storage_key=a["storage_key"],
                    display_name=a.get("display_name"),
                ) for a in leg.get("attachments", []) or []],
            )
            for leg in child_info.get("then", []) or []
        ]
        carrier_chain = ChainState(
            step_index=0,
            first_leg_expect_verdict=child_info.get("expect_verdict"),
            legs=then_legs,
            step_audit_id=step_audit_id or 0,
        )
        first_leg_id = f"TASK-{base_num + len(children) + carrier_leg_offset:03d}"
        carrier_leg_offset += 1
        first_leg_data = {
            "id": first_leg_id,
            "team": parent.team,
            "brief": child_info["prompt"] or "",
            "assigned_agent": child_info["agent"],
            "status": TaskStatus.PENDING,
            "session_timeout_seconds": parent.session_timeout_seconds,
            "task_type": "subtask",
            "revision_count": 0,
            "orchestration_step_count": 0,
        }
        if carrier_chains_data is None:
            carrier_chains_data = []
        carrier_chains_data.append({
            "child_index": i,
            "active_chain_json": carrier_chain.serialize(),
            "first_leg": first_leg_data,
            "first_leg_id": first_leg_id,
        })

    # Atomic insert-N + parent transition under a single explicit SQL
    # transaction. active_fanout, carrier active_chain, and first-leg inserts
    # are all written in the same transaction as child inserts + parent park +
    # attachment links/audit — no crash gap between spawn and metadata
    # persistence. Any failure rolls back everything.
    outcome = db.try_delegate_many(
        task_id, child_records, parent_note=parent_note,
        active_fanout_json=fanout_state.serialize(),
        children_attachments=children_att_params,
        carrier_chains=carrier_chains_data,
        uploaded_by=manager_agent,
        expected_claim=expected_claim,
    )
    if not isinstance(outcome, Committed):
        logger.debug(
            "run_step %s: cancelled between re-check and fanout spawn, dropping",
            task_id,
        )
        return outcome

    orch._audit.log_fanout_spawned(
        task_id=task_id,
        agent=manager_agent,
        width=width,
        children_ids=children_ids,
    )

    # Enqueue: plain children go directly to the queue; pipeline carriers'
    # first legs are enqueued instead (carriers themselves never run sessions).
    for i, child_info in enumerate(children):
        cid = children_ids[i]
        has_pipeline = i in pipeline_indices
        if not has_pipeline:
            _enqueue_task_generation_aware(orch, cid)
            continue
        # Find the carrier's first leg id from the pre-allocated data.
        for cc in (carrier_chains_data or []):
            if cc["child_index"] == i:
                _enqueue_task_generation_aware(orch, cc["first_leg_id"])

    return outcome


def _prepare_fanout_join_payload(
    orch: "Orchestrator", task_id: str, active_fanout_json: str,
) -> dict | None:
    """Pure prospective join, reused by final atomic admission."""
    from runtime.orchestrator.fanout import (
        FanoutState,
        build_fanout_join_context,
        collect_child_join_info,
    )
    fanout = FanoutState.deserialize(active_fanout_json)
    if fanout.status != "spawned":
        raise ValueError("fanout is not ready for final admission")

    db = orch._db
    # Collect child results for all children in the fan-out.
    children = []
    child_reports: dict[str, dict | None] = {}
    for cid in fanout.children_ids:
        child = db.get_task(cid)
        if child is not None:
            children.append(child)
        # Fetch the latest completion report for verdict/confidence.
        report = db.get_latest_completion_report(cid)
        if report is not None:
            child_reports[cid] = {
                "verdict": report.verdict,
                "confidence_score": report.confidence,
            }
        else:
            child_reports[cid] = None

    join_infos = collect_child_join_info(children, child_reports=child_reports)
    join_context = build_fanout_join_context(
        parent_task_id=task_id,
        fanout=fanout,
        child_results=join_infos,
    )

    return {"width": fanout.width, "children_ids": fanout.children_ids,
            "context_markdown": join_context}


def _inject_fanout_join_context(
    orch: "Orchestrator", task_id: str, active_fanout_json: str,
) -> None:
    """On CAS win for a fan-out parent: collect child results, build join
    context, and write a fanout_join audit row so the manager's prompt can
    include it. Also clears active_fanout on the parent (caller must do this
    separately or here; we just write the audit row).

    The audit row written here is read by _fanout_join_header_if_applicable
    in the prompt-build step.
    """
    from runtime.orchestrator.fanout import (
        FanoutState,
        build_fanout_join_context,
        collect_child_join_info,
    )
    try:
        fanout = FanoutState.deserialize(active_fanout_json)
    except Exception:
        logger.debug("run_step %s: active_fanout unparseable, skipping join", task_id)
        return

    db = orch._db
    # Collect child results for all children in the fan-out.
    children = []
    child_reports: dict[str, dict | None] = {}
    for cid in fanout.children_ids:
        child = db.get_task(cid)
        if child is not None:
            children.append(child)
        # Fetch the latest completion report for verdict/confidence.
        report = db.get_latest_completion_report(cid)
        if report is not None:
            child_reports[cid] = {
                "verdict": report.verdict,
                "confidence_score": report.confidence,
            }
        else:
            child_reports[cid] = None

    join_infos = collect_child_join_info(children, child_reports=child_reports)
    join_context = build_fanout_join_context(
        parent_task_id=task_id,
        fanout=fanout,
        child_results=join_infos,
    )

    # Write a fanout_join audit row that the prompt builder reads.
    orch._audit.log_fanout_join(
        task_id=task_id,
        width=fanout.width,
        children_ids=fanout.children_ids,
        context_markdown=join_context,
    )
