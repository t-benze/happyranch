"""Original-tail and initial-startup preparation proof; never reconstruct a live owner."""
from __future__ import annotations

from contextlib import ExitStack
from copy import deepcopy
import json
import logging
import os
import sqlite3
from pathlib import Path
import stat
import time
from typing import Any

from runtime.daemon.lifetime_lock import LockProof
from runtime.infrastructure.db.tasks import _repository_require
from runtime.orchestrator.task_pause import fingerprint
from runtime.orchestrator.workspace_adapters import _repo_directory, _repo_refresh_lease, _repo_refresh_repository

logger = logging.getLogger('happyranch.daemon')
STARTUP_ACTION = 'repository_preparation_startup_closed'


def refused_task_idle(live: Any, entry: dict, cleanup: Any) -> bool:
    """Positive original-tail proof only; never reconstruct a historical owner.

    Called under the Pause writer AFTER durable unknown and producer settlement.
    Repository closure receipts prove the preparation processes, while the
    original invocation proves that executor admission was never committed.
    Sticky native cleanup history and hold/admission policy are unchanged.
    """
    from runtime.infrastructure.db.tasks import RepositoryPreparationError, repository_preparation_closed
    from runtime.models import TaskStatus
    if (live.owner not in {'ordinary', 'v2', 'recovery'} or live.unsettled
            or live.launch_observed or live.attempt_committed or live.admitted
            or live._repository_worker_active or getattr(live, '_repository_completion_active', False)
            or not entry['context'].get('producer_settled') or entry['started_at'] is not None
            or live.identity in live.store._live
            or cleanup is not None and (cleanup.processes or cleanup.kind not in {'safe', 'unknown'}
                or cleanup.kind == 'unknown' and cleanup.reason != 'transition_capture_or_teardown_unavailable')):
        return False
    db = live.orch._db
    task = db.get_task(live.task.id)
    reservation = live.move_reservation
    evidence = live._repository_preparations
    if (task is None or task.status not in {TaskStatus.COMPLETED, TaskStatus.FAILED,
            TaskStatus.CANCELLED, TaskStatus.SUPERSEDED} or task.current_session_id is not None
            or reservation is None or reservation.possible_launch
            or not reservation.owner_live
            or live.orch._team_moves._reservations.get(reservation.owner_id) is not reservation
            or not evidence or set(evidence) != set(reservation.repository_obligations)
            or not all(reservation.repository_obligations.values())
            or any(e.owner is not live or not e.closed or not e.worker_returned or e.pending or e.uncertain
                   for e in evidence.values())
            or any(not v['closed'] or v['failed'] for v in live._repository_effect_intervals)
            or any(other.task.id == task.id for other in live.store._live.values())
            or db._conn.execute("SELECT 1 FROM jobs WHERE task_id=? AND status='running' LIMIT 1", (task.id,)).fetchone()):
        return False
    try:
        history = db.repository_preparation_history_uncommitted(task.id)
        streams: dict[str, list[dict]] = {}
        for row in history:
            event = row['event']
            if event['owner']['invocation_id'] == live.identity:
                if (event['obligation'] not in evidence or event['owner'] != evidence[event['obligation']].binding
                        or event['owner']['attempt_id'] != live.refusal_attempt_id):
                    return False
                streams.setdefault(event['obligation'], []).append(event)
        if set(streams) != set(evidence) or any(not repository_preparation_closed(events)
                or events[-1]['phase'] != 'closed'
                or next(e['inventory']['intervals'] for e in events if e['phase'] == 'outer_closed')
                    != live._repository_effect_intervals for events in streams.values()):
            return False
        rows = db._conn.execute("SELECT CASE WHEN typeof(payload)='text' AND length(CAST(payload AS BLOB))<=8192 "
            "THEN payload END FROM audit_log WHERE task_id=? AND action='task_native_launch_boundary' "
            "ORDER BY id DESC LIMIT 3", (task.id,)).fetchall()
        native = [json.loads(row[0]) for row in rows]
        return bool(native and native[0]['kind'] == 'refused'
            and native[0]['invocation_id'] == live.identity
            and native[0]['attempt_id'] == live.refusal_attempt_id
            and native[0]['session_id'] == live.session_id
            and native[0]['disposition'] == 'unclosed_preparation')
    except (RepositoryPreparationError, sqlite3.Error, ValueError, TypeError, KeyError, StopIteration):
        return False


def settle_preparation(current: dict, live: Any, native_binding: dict | None) -> str | None:
    """The human decision, kept separate from the strictly owner-less proof below.

    Both require affirmative closure of every selected obligation. Human callers
    still require the actual original owner and its complete native receipts;
    startup never supplies a replacement owner to this branch.
    """
    missing = current['missing_predicates']
    if 'original_owner' in missing:
        return 'original_owner_unavailable'
    if 'held' in missing:
        return 'held'
    if 'active_worker' in missing or 'active_preparation' in missing:
        return 'active_owner'
    if missing:
        return 'unresolved_preparation'
    if native_binding != current['owner']:
        return 'stale_state'
    from runtime.orchestrator.launch_refusals import _current
    store = live.store
    if (store._live.get(live.identity) is not live or not _current(live)
            or live.move_reservation.cleanup_failed):
        return 'unresolved_preparation'
    store.held_uncommitted(live.task.id)
    live.orch._team_moves.revalidate_uncommitted(live.move_reservation)
    selected = list(live._repository_preparations.values())
    if (not selected or any(not e.closed for e in selected)
            or not all(live.settle_repository_preparation(e) for e in selected)):
        return 'unresolved_preparation'
    return None


class _Retained(ValueError):
    def __init__(self, reason: str, path: Path | None = None) -> None:
        self.reason, self.path = reason, path
        super().__init__(reason)


class _RepositoryCheck:
    """Literal bounded observations under the SAME cooperative common-dir lease."""
    def __init__(self, deadline: float) -> None:
        self.deadline, self.entries = deadline, 0

    def check(self, path: Path) -> None:
        self.entries += 1
        if self.entries > 4096 or time.monotonic() >= self.deadline:
            raise _Retained('repository_check_exhausted', path)

    def directory(self, path: Path) -> None:
        self.check(path)
        try:
            fd = _repo_directory(path)
            os.close(fd)
        except OSError:
            raise _Retained('repository_metadata_unavailable', path) from None

    def file(self, path: Path) -> bytes:
        try:
            return self._file(path)
        except OSError:
            raise _Retained('repository_metadata_unavailable', path) from None

    def _file(self, path: Path) -> bytes:
        self.check(path)
        parent = _repo_directory(path.parent)
        try:
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                before = os.fstat(fd)
                if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                        or before.st_uid != os.getuid()):
                    raise _Retained('unsafe_metadata', path)
                raw = os.read(fd, 4097)
                keys = ('st_dev', 'st_ino', 'st_mode', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_nlink')
                after = os.fstat(fd)
                entry = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                if (len(raw) > 4096 or os.read(fd, 1)
                        or any(getattr(before, k) != getattr(after, k) or getattr(before, k) != getattr(entry, k) for k in keys)):
                    raise _Retained('metadata_unavailable', path)
                return raw
            finally:
                os.close(fd)
        finally:
            os.close(parent)

    def locks(self, directory: Path, *, refs: bool = False, cooperative: bool = False) -> None:
        self.directory(directory)
        with os.scandir(directory) as entries:
            for entry in entries:
                path = directory / entry.name
                self.check(path)
                # The cooperative lease is a permanent inode, not a Git lock.
                if cooperative and entry.name == 'happyranch-repository-refresh.lock' and not refs:
                    continue
                if entry.name.endswith('.lock') or entry.name == 'locked':
                    raise _Retained('git_lock', path)
                mode = entry.stat(follow_symlinks=False).st_mode
                if refs:
                    if stat.S_ISDIR(mode):
                        self.locks(path, refs=True)
                    elif not stat.S_ISREG(mode):
                        raise _Retained('unsafe_refs', path)
        if not refs:
            refdir = directory / 'refs'
            try:
                refdir.lstat()
            except FileNotFoundError:
                pass
            else:
                self.locks(refdir, refs=True)

    def registration(self, worktree: Path, admin: Path, common: Path) -> None:
        self.directory(worktree)
        self.directory(admin)
        raw = self.file(worktree / '.git')
        if not raw.startswith(b'gitdir: '):
            raise _Retained('broken_worktree', worktree / '.git')
        target = Path(os.path.abspath(worktree / raw[8:].decode('utf8').rstrip('\n')))
        if target != admin or self.file(admin / 'gitdir').decode('utf8').rstrip('\n') != str(worktree / '.git'):
            raise _Retained('broken_worktree', worktree / '.git')
        common_pointer = self.file(admin / 'commondir').decode('utf8').rstrip('\n')
        if Path(os.path.abspath(admin / common_pointer)) != common:
            raise _Retained('broken_worktree', admin / 'commondir')
        head = self.file(admin / 'HEAD').strip()
        import re
        if not (re.fullmatch(rb'[a-f0-9]{40}', head) or re.fullmatch(rb'ref: refs/[^\x00-\x20]+', head)):
            raise _Retained('broken_worktree', admin / 'HEAD')
        self.locks(admin)

    def repository(self, common: Path, task_id: str, repo_dir: Path) -> dict:
        # Resolve the real assigned workspace's git dir, including a registered
        # linked checkout. A common dir alone does not identify that git dir.
        if not task_id or '/' in task_id or task_id in {'.', '..'}:
            raise _Retained('repository_layout_unavailable', repo_dir)
        self.locks(common, cooperative=True)
        gitpath = repo_dir / '.git'
        if stat.S_ISDIR(gitpath.lstat().st_mode):
            if gitpath != common:
                raise _Retained('repository_layout_unavailable', gitpath)
        else:
            raw = self.file(gitpath)
            if not raw.startswith(b'gitdir: '):
                raise _Retained('broken_worktree', gitpath)
            gitdir = Path(os.path.abspath(repo_dir / raw[8:].decode('utf8').rstrip('\n')))
            if gitdir.parent != common / 'worktrees':
                raise _Retained('repository_layout_unavailable', gitdir)
            self.registration(repo_dir, gitdir, common)
        worktree = repo_dir / '.claude' / 'worktrees' / task_id
        container = common / 'worktrees'
        registered = []
        try:
            container.lstat()
        except FileNotFoundError:
            pass
        else:
            self.directory(container)
            with os.scandir(container) as entries:
                for entry in entries:
                    admin = container / entry.name
                    self.check(admin)
                    self.directory(admin)
                    pointer = self.file(admin / 'gitdir').decode('utf8').rstrip('\n')
                    if pointer == str(worktree / '.git'):
                        registered.append(admin)
        try:
            worktree.lstat()
        except FileNotFoundError:
            # Parent symlinks/dangling pointers are ambiguity, not absence.
            for parent in (repo_dir / '.claude', worktree.parent):
                try:
                    parent.lstat()
                except FileNotFoundError:
                    break
                self.directory(parent)
            if registered:
                raise _Retained('broken_worktree', registered[0])
            return {'repository': str(common), 'worktree': str(worktree), 'state': 'absent'}
        self.directory(worktree)
        if len(registered) != 1:
            raise _Retained('broken_worktree', worktree)
        admin = registered[0]
        self.registration(worktree, admin, common)
        return {'repository': str(common), 'worktree': str(worktree), 'state': 'registered'}


def _ownerless_obligations(orch: Any, original: dict, history: list[dict]) -> tuple[list[dict], list[dict]]:
    """Positive prepared phase proves no executor; child absence is never inferred."""
    _repository_require(original['phase'] == 'prepared' and original['owner'] != 'job'
        and not original['context'].get('execution_unknown') and original['started_at'] is None
        and original['captured_generation'] is None, 'not_prepared_only')
    selected = [row['event'] for row in history
                if row['event']['owner']['invocation_id'] == original['id']]
    _repository_require(bool(selected), 'original_repository_unavailable')
    bindings = [v['owner'] for v in selected]
    binding = bindings[0]
    observer = getattr(getattr(orch._db, '_memory_collection_context', None), 'memory_collection', None)
    _repository_require(observer is not None and all(b == binding for b in bindings)
        and binding['boot_id'] != observer.boot_id and binding['kind'] == 'task'
        and binding['org'] == original['org'] and binding['scope'] == original['task_id']
        and binding['agent'] == original['agent'] and binding['session_id'] == original['session_id']
        and '/' not in original['agent'] and original['agent'] not in {'.', '..'},
        'original_repository_unavailable')
    repositories, children = {}, []
    streams = {}
    for event in selected:
        streams.setdefault(event['obligation'], []).append(event)
        repository = event['repository']
        key = repository['path']
        _repository_require(key not in repositories or repositories[key] == repository)
        repositories[key] = repository
    _repository_require(len({(r['device'], r['inode']) for r in repositories.values()}) == len(repositories),
        'repository_identity_ambiguous')
    # Positive phase evidence must also agree with any separately recorded
    # executor/session boundary. Scoped exhaustion retains only this candidate.
    rows = orch._db._conn.execute("SELECT action,CASE WHEN typeof(payload)='text' AND "
        "length(CAST(payload AS BLOB))<=8192 THEN payload END FROM audit_log WHERE task_id=? AND action IN "
        "('task_native_launch_boundary','session_start') ORDER BY id LIMIT 129", (original['task_id'],)).fetchall()
    _repository_require(len(rows) <= 128, 'evidence_unavailable', 503)
    def unique(pairs: list[tuple[str, Any]]) -> dict:
        result = {}
        for key, value in pairs:
            _repository_require(key not in result)
            result[key] = value
        return result
    from runtime.orchestrator.launch_refusals import _LIMIT, validate_preparation_cause
    for action, raw in rows:
        _repository_require(type(raw) is str and len(raw.encode()) <= 8192, 'evidence_unavailable', 503)
        boundary = json.loads(raw, object_pairs_hook=unique)
        _repository_require(type(boundary) is dict and type(boundary.get('session_id')) is str
            and 0 < len(boundary['session_id']) <= 256)
        if action == 'task_native_launch_boundary':
            _repository_require(boundary.get('kind') in {'started', 'refused'}
                and type(boundary.get('count')) is int and 0 <= boundary['count'] <= _LIMIT
                and all(type(boundary.get(k)) is str and 0 < len(boundary[k]) <= 256
                    for k in ('logical_owner', 'attempt_id', 'invocation_id')))
            _repository_require(boundary['count'] == 0 if boundary['kind'] == 'started' else boundary['count'] > 0)
            if boundary['kind'] == 'refused':
                validate_preparation_cause(boundary.get('first_cause'))
        _repository_require(not (boundary.get('invocation_id') == original['id']
            or boundary['session_id'] == original['session_id']), 'conflicting_launch_boundary')
    for events in streams.values():
        for event in events:
            if event['phase'] == 'attempted':
                suffix = [v for v in events if v['request_ref'] == event['request_ref'] and v['sequence'] > event['sequence']]
                # Popen may have occurred after attempted but before started.
                _repository_require(any(v['phase'] == 'started' for v in suffix), 'child_creation_unknown')
            if event['phase'] != 'started':
                continue
            _repository_require(type(event['pid']) is int and event['pid'] > 0
                and event['pid'] <= 2**31-1 and event['start_pin'] is not None, 'child_identity_unavailable')
            suffix = [v for v in events if v['request_ref'] == event['request_ref'] and v['sequence'] > event['sequence']]
            consumed = any(v['phase'] == 'consumed' and v['consumed'] for v in suffix)
            finished = any(v['phase'] == 'finished' and v['complete'] for v in suffix)
            if consumed and finished:
                children.append({'pid': event['pid'], 'start_pin': event['start_pin'], 'proof': 'original_finished'})
                continue
            try:
                os.kill(event['pid'], 0)
            except ProcessLookupError:
                children.append({'pid': event['pid'], 'start_pin': event['start_pin'], 'proof': 'kill0_esrch'})
            else:
                raise _Retained('child_not_proven_dead')
    return list(repositories.values()), children


class StartupPreparations:
    """Construct only for the initial org population, before restore/admission."""
    def __init__(self, orch: Any, proof: LockProof | None) -> None:
        self.orch, self.proof, self.used = orch, proof, False
        self.records: tuple[dict, ...] = ()
        if type(proof) is not LockProof or proof.proven is not True:
            return
        store = getattr(orch._db, '_task_pause_store', None)
        if store is None:
            return
        records = []
        with orch._db._lock:
            roots = [r[0] for r in orch._db._conn.execute('SELECT root_task_id FROM task_pause_controls')]
            for root in roots:
                try:
                    row = store.row_uncommitted(root)
                    records.extend(deepcopy(e) for e in row['journal']['entries']
                        if e['phase'] == 'prepared' and e['owner'] != 'job'
                        and e['task_id'] not in {'TASK-12738', 'TASK-12724'})
                except Exception:
                    logger.info('preparation_startup org=%s root=%s retained=capture_unavailable', orch._slug, root)
        self.records = tuple(records)

    def run(self) -> None:
        if self.used:
            return
        self.used = True
        for original in self.records:
            try:
                self._close(original)
                logger.info('preparation_startup org=%s task=%s closed', self.orch._slug, original['task_id'])
            except Exception as exc:
                # Candidate-local exhaustion/errors are retention, never fatal
                # global startup dependencies or evidence of empty history.
                reason = exc.reason if isinstance(exc, _Retained) else getattr(exc, 'code', 'evidence_unavailable')
                path = exc.path if isinstance(exc, _Retained) else getattr(exc, 'filename', None)
                logger.info('preparation_startup org=%s task=%s retained=%s path=%r',
                    self.orch._slug, original['task_id'], reason, str(path) if path else None)

    def _close(self, original: dict) -> None:
        orch, db = self.orch, self.orch._db
        store = db._task_pause_store
        _repository_require(type(self.proof) is LockProof and self.proof.proven is True)
        # Every read stays candidate-local and complete on the existing bounds.
        with db._memory_collection_read_budget(), db.coherent_read_view():
            history = db.repository_preparation_history_uncommitted(original['task_id'])
            repositories, children = _ownerless_obligations(orch, original, history)
        deadline = time.monotonic() + .5
        check = _RepositoryCheck(deadline)
        # Receipt identity must map to exactly one CURRENT assigned-workspace
        # checkout. Never substitute common.parent for a linked repo's git dir.
        repo_root = orch._paths.workspaces_dir / original['agent'] / 'repos'
        check.directory(repo_root)
        paths: dict[str, list[Path]] = {r['path']: [] for r in repositories}
        with os.scandir(repo_root) as entries:
            for entry in entries:
                path = repo_root / entry.name
                check.check(path)
                try:
                    identity = _repo_refresh_repository(path)
                except (OSError, ValueError):
                    continue
                if identity in repositories:
                    paths[identity['path']].append(path)
        for repository in repositories:
            if len(paths[repository['path']]) != 1:
                raise _Retained('repository_location_ambiguous', repo_root)
        with ExitStack() as stack:
            validations, repository_checks = [], []
            for repository in sorted(repositories, key=lambda r: (r['device'], r['inode'], r['path'])):
                common = Path(repository['path'])
                repo_dir = paths[repository['path']][0]
                try:
                    lease = stack.enter_context(_repo_refresh_lease(repo_dir, deadline=deadline,
                        cancel_requested=None, expected_repository=repository, blocking=False))
                except (OSError, TimeoutError, ValueError):
                    raise _Retained('repository_lease_unavailable', common) from None
                validations.append(lease[1])
                repository_checks.append(check.repository(common, original['task_id'], repo_dir))
            # No producers/HTTP exist yet. Still use the normal root writer and
            # revalidate all business/journal/evidence before the single COMMIT.
            with store.writer(original['task_id']) as (_, row):
                current = next((e for e in row['journal']['entries'] if e['id'] == original['id']), None)
                restored = deepcopy(original)
                restored.update(phase='unknown', started_at=None, captured_generation=None)
                restored['context']['execution_unknown'] = True
                task = db.get_task(original['task_id'])
                _repository_require(sum(e['task_id'] == original['task_id'] and e['owner'] != 'job'
                    for e in row['journal']['entries']) == 1
                    and current == restored and original['id'] not in store._live
                    and original['id'] not in orch._team_moves._reservations
                    and task is not None and fingerprint(task) == original['fingerprint'], 'stale_state')
                with db._memory_collection_read_budget():
                    current_history = db.repository_preparation_history_uncommitted(original['task_id'])
                    _repository_require(current_history == history, 'stale_state')
                    # Repeat child proof inside the effect boundary; a reused or
                    # ambiguous PID can only retain, never authorize a signal.
                    _, children = _ownerless_obligations(orch, original, current_history)
                for validate in validations:
                    validate()
                for repository in repositories:
                    check.repository(Path(repository['path']), original['task_id'], paths[repository['path']][0])
                # This is a new closing receipt, NOT a fabricated native closed
                # event. Preserve both original prepared and restored unknown
                # facts verbatim, then use normal closure's journal removal.
                db.insert_audit_log_uncommitted(original['task_id'], 'daemon', STARTUP_ACTION,
                    {'version': 1, 'original': original, 'restored': current,
                     'lock_reason': self.proof.reason, 'children': children,
                     'repositories': repository_checks, 'history_ids': [r['id'] for r in history]})
                row['journal']['entries'] = [e for e in row['journal']['entries'] if e['id'] != original['id']]
                store.save_journal_uncommitted(row)
