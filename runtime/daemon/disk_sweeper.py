"""THR307 nightly disk sweep: 03:00 MACHINE-local, no startup catchup.

A single 1800s monotonic window includes discovery, ALL sequential orgs, both
phases, checks, Git and removal. No cursor/count cap or fairness guarantee.
All blocking work runs in an owned worker process group: cancellation of a
Python thread would not stop deletion. Public manual entry is DRY RUN ONLY:
    python -m runtime.daemon.disk_sweeper --dry-run --runtime-root /absolute/runtime
Scheduled deletion applies to every admitted org (THR307 seq134); obsolete org
disk_sweep input is ignored. Explicit manual dry run remains nonmutating. No
legacy cleanup code/flags are invoked. Output is never a candidate. See the
maintained THR307 spec for filesystem observation scope and nonpreemptible kernel-I/O limitations.
"""
from __future__ import annotations

import argparse
import asyncio
import errno
import hashlib
import importlib.util
import json
import logging
import marshal
import os
import re
import signal
import sqlite3
import stat
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import ExitStack, closing, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from types import CodeType
from typing import Any

from runtime.daemon.cleanup_helpers import helper_argv

from runtime.daemon._disk_sweep_fs import (
    Expired, Retain, allocated, check, directory, identity, mutate, names,
    signature, snapshot, trees_equal,
    _observer, measurement_clock, measurement_elapsed, observe, observed, observing,
)

LOG = logging.getLogger(__name__)
BUDGET_SECONDS = 1800
PROCESS_SCOPE = 'not checked under THR307 seq117; ended task and structural root required; no process absence or authentication claim'
# Worker-only exit after its final report was published with a non-complete
# outcome. Distinct from 1 (uncaught exception) and 2 (argument error), so the
# supervisor can tell an orderly incomplete sweep from a crash.
WORKER_INCOMPLETE_EXIT = 3
# Candidate states whose filesystem effect is not known.
_OPEN_MUTATION_STATES = ('inspecting', 'mutation_in_flight', 'partial_or_unknown')
TERMINAL = {'completed', 'failed', 'cancelled', 'superseded'}
TASK = re.compile(r'TASK-[0-9]+\Z')
_history = ContextVar('disk_sweep_kept_history', default=None)


# Review revision 3 section 5.3: the sole scope/coverage classification table.
# Context rows require the full existing mapping proof, never just a reason label.
# Unknown findings keep their item and veto completeness; they grant no clearance.
_CLASSIFICATION = {
    'mapped_metadata': ('item', True),
    'arrival': ('item', True),
    'unknown_location_residue': ('entry', False),
    'missing_folder': ('item', True),
    'unregistered_task': ('item', False),
    'non_clone': ('none', True),
    'registration_unknown': ('clone', False),
    'candidate_lineage_unknown': ('item', False),
    'job_lineage_unknown': ('org', False),
    'org_unavailable': ('org', False),
    'run_error': ('run', False),
    'preservation': ('item', True),
    'item_unknown': ('item', False),
}
_PRESERVATION_REASONS = {
    'task_or_root_not_terminal', 'active_job', 'dirty_or_untracked', 'unique_head_commit',
    'changed_within_hour', 'unknown_external_hardlinks', 'filesystem_boundary',
    'possible_nested_worktree', 'linked_descendant', 'git_unregistered_locked_or_prunable',
}
_ORG_REASONS = {
    'unknown_job_status', 'team_move_cleanup_owner_unavailable', 'team_move_native_held',
}


class _LineageUnknown(Retain):
    """Keep the ancestry observation's candidate/job context with its reason."""
    def __init__(self, reason: str, finding: str) -> None:
        super().__init__(reason)
        self.finding = finding


def _classification(reason: str, finding: str | None = None) -> dict:
    code = reason.split(':', 1)[0]
    if finding is None:
        finding = ('job_lineage_unknown' if code in _ORG_REASONS else
                   'missing_folder' if code == 'missing_worktree_folder' else
                   'unregistered_task' if code == 'unknown_unregistered_task_worktree' else
                   'preservation' if code in _PRESERVATION_REASONS else 'item_unknown')
    hold, located = _CLASSIFICATION[finding]
    return dict(finding=finding, hold=hold, located_inspected=located)


def _scope(inventory: dict, path: str, reason: str, finding: str, count: int | None = None) -> None:
    if not any(scope['path'] == path for scope in inventory.setdefault('discovery_scopes', [])):
        inventory['discovery_scopes'].append(dict(path=path, reason=reason, candidate_count=count))
    inventory.setdefault('scope_classifications', {})[path] = _classification(reason, finding)


def _coverage(result: dict) -> dict:
    reasons = []
    scopes = result.get('discovery_scopes', [])
    classified = result.get('scope_classifications', {})
    unknown = result.get('discovery', 'unknown') == 'unknown' or any(
        not classified.get(scope['path'], {}).get('located_inspected', False) for scope in scopes)
    if unknown: reasons.append('unknown_discovery')
    for scope in scopes:
        if not classified.get(scope['path'], {}).get('located_inspected', False):
            reasons.append(scope['reason'])
    for c in result.get('candidates', []):
        if c.get('state') == 'removed' or c.get('state') == 'eligible' and result.get('mode') == 'dry_run':
            continue
        if c.get('state') != 'retained':
            reasons.append(c.get('state', 'unknown_candidate_state') + ':' + c['path'])
        elif not classified.get(c['path'], _classification(c.get('reason', 'unknown'))).get('located_inspected'):
            reasons.append(c.get('reason', 'unknown_keep') + ':' + c['path'])
    if result.get('error'): reasons.append(result['error'])
    if result.get('org_hold'): reasons.append('org_hold:' + result['org_hold'])
    candidates = result.get('candidates', [])
    return dict(complete=not reasons, reasons=list(dict.fromkeys(reasons)), unknown_discovery=unknown,
                candidate_count=None if unknown else len(candidates),
                unreached=None if unknown else sum(c.get('state') == 'unreached' for c in candidates))


def _finalize_night(report: dict) -> None:
    """Coverage record, never deletion/recovery authority; legacy reports stay unproven."""
    reasons = []
    if report.get('scheduled_admission', {}).get('state') == 'suspended': reasons.append('scheduled_suspension')
    if report.get('outcome') != 'complete': reasons.append('outcome:' + str(report.get('outcome', 'unknown')))
    if not report.get('scheduled_date'): reasons.append('not_scheduled')
    if not report.get('source_revision'): reasons.append('source_revision_missing')
    if report.get('policy') != 'thr307-ended-root-one-hour-all-orgs': reasons.append('policy_missing_or_unknown')
    if not report.get('timezone') or not report.get('timezone_offset'): reasons.append('timezone_missing')
    roots = report.get('selected_org_roots')
    if not isinstance(roots, list):
        reasons.append('admitted_org_inventory_missing')
    elif sorted(Path(root).name for root in roots) != sorted(org.get('org', '') for org in report.get('orgs', [])):
        reasons.append('admitted_org_coverage_mismatch')
    if not report.get('started_at') or not report.get('finished_at'): reasons.append('inventory_window_unfinished')
    if report.get('unreached_orgs') != []: reasons.append('unreached_orgs')
    if report.get('runtime_discovery') == 'unknown': reasons.append('runtime_discovery_unknown')
    for key in ('error', 'interruption', 'partial_mutation', 'move_deferred_orgs', 'unloaded_orgs_unknown'):
        if report.get(key): reasons.append(key + ':' + str(report[key]))
    for org in report.get('orgs', []):
        if org.get('mode') != 'on': reasons.append('not_deletion_mode:' + str(org.get('org')))
        if not org.get('inventory_window', {}).get('finished_at'): reasons.append('inventory_unfinished:' + str(org.get('org')))
        coverage = _coverage(org)
        if not coverage['complete']: reasons.extend(str(org.get('org')) + ':' + reason for reason in coverage['reasons'])
    elapsed = report.get('elapsed_seconds')
    if not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or not 0 <= elapsed <= BUDGET_SECONDS:
        reasons.append('budget_closure_unproven')
    if reasons and report.get('scheduled_date') and report.get('outcome') == 'complete' and all(o.get('mode') == 'on' for o in report.get('orgs', [])):
        report['outcome'] = 'incomplete'
    # Worker outcome describes completed coverage, allowing its normal exit.
    # Only the parent's post-stop/reap publication can complete the night.
    if (not isinstance(report.get('worker_group_stop'), str) or not report['worker_group_stop']
            or type(report.get('worker_returncode')) is not int or report['worker_returncode'] != 0):
        reasons.append('supervisor_closure_unproven')
    report['night'] = dict(definition='thr307-review3-coverage', complete=not reasons, reasons=list(dict.fromkeys(reasons)))


class _Measurements:
    """Bounded aggregates of existing calls; never consulted by policy code."""
    _counter_keys = dict(topology='topology_reads', census='censuses', ownership='ownership_reads',
                         entry='entries_walked', report_write='report_writes')

    def __init__(self, result: dict) -> None:
        self.result = result
        self.errors = dict(count=0, types=[])
        self.data = result['measurements'] = dict(
            counters=self.counters(), clones={}, errors=self.errors,
            history=(_history.get() or {}).get('status', 'not_available'),
        )
        self.candidate = None
        self.started = None
        self.clone = None
        self.clone_stack = []
        self.org_started = measurement_clock()
        self.data['started_monotonic'] = self.org_started

    @staticmethod
    def counters() -> dict:
        return dict(topology_reads=0, censuses=0, ownership_reads=0,
                    entries_walked=0, report_writes=0, report_writes_confirmed=0,
                    git={}, durations_seconds={}, gitdir_gone_count='unknown')

    def targets(self) -> list[dict]:
        values = [self.data['counters']]
        if self.clone is not None:
            target = self.data['clones'].get(self.clone)
            if target is None:
                target = self.data['clones'][self.clone] = self.counters()
            values.append(target)
        return values

    def item(self, candidate: dict) -> dict:
        item = candidate.get('measurement')
        if item is None:
            item = candidate['measurement'] = dict(
                stage_reached='discovered', duration_seconds=0.0,
                final_reason='unreached', attempted_effect={}, confirmed_effect={},
                kept_since=None, kept_allocated_bytes='unknown',
            )
        return item

    def refresh(self, candidate: dict) -> None:
        item = self.item(candidate)
        state = candidate['state']
        reason = item.get('interruption', candidate.get('reason', state))
        item['final_reason'] = reason[:1024]
        item['final_reason_truncated'] = len(reason) > 1024
        if self.candidate is candidate and self.started is not None:
            ended = measurement_clock()
            if ended is not None:
                item['duration_seconds'] = round(max(0, ended - self.started), 6)
        if state == 'retained':
            history = _history.get() or {}
            item['kept_since'] = history.get('paths', {}).get(candidate['path'], history.get('date') or datetime.now().astimezone().date().isoformat())
        else:
            item['kept_since'] = None
        if state == 'removed':
            item['stage_reached'] = 'removal_confirmed'
            item['confirmed_effect']['remove'] = 1

    def record(self, event: str, phase: str, **values: Any) -> None:
        if event == 'org_checkpoint':
            self.data['duration_seconds'] = measurement_elapsed(self.org_started)
            return
        if event == 'measurement_error':
            self.errors['count'] += 1
            if len(self.errors['types']) < 8:
                self.errors['types'].append(values['error'][:64])
            return
        if event == 'item':
            candidate = values['candidate']
            if phase == 'interrupted':
                self.item(candidate)['interruption'] = values['reason'][:1024]
            if phase == 'start':
                self.candidate, self.clone = candidate, candidate.get('repo')
                self.started = measurement_clock()
                self.item(candidate)['stage_reached'] = 'inspection'
                self.item(candidate)['started_monotonic'] = self.started
            self.refresh(candidate)
            if phase == 'finish':
                self.candidate, self.clone, self.started = None, None, None
            return
        args, kwargs = values.get('args', ()), values.get('kwargs', {})
        if event in ('topology', 'census', 'git') and phase == 'start':
            self.clone_stack.append(self.clone)
            if event != 'git' or self.clone is None and str(args[0]) in self.data['clones']:
                self.clone = str(args[0])
        if self.candidate is not None and phase == 'start' and event != 'report_write':
            self.item(self.candidate)['stage_reached'] = event + (':' + ' '.join(args[2:4] if args[2] == 'worktree' else args[2:3]) if event == 'git' else '')
        key = self._counter_keys.get(event)
        for target in self.targets():
            if key and (phase == 'start' or event == 'entry'):
                target[key] += 1
            if phase == 'finish':
                duration = values.get('duration')
                if duration is not None:
                    durations = target['durations_seconds']
                    durations[event] = round(durations.get(event, 0) + duration, 6)
                if event == 'report_write' and values['error'] is None:
                    target['report_writes_confirmed'] += 1
            if event == 'git':
                # Fixed subcommands only: no argv/path/error strings or call log.
                subcommand = args[2]
                if subcommand == 'worktree':
                    subcommand += ' ' + (args[3] if args[3] in ('list', 'remove', 'prune') else 'other')
                elif subcommand not in ('rev-parse', 'rev-list', 'status'):
                    subcommand = 'other'
                command = target['git'].setdefault(subcommand, dict(calls=0, duration_seconds=0.0, timeouts=0))
                if phase == 'start':
                    command['calls'] += 1
                elif phase == 'finish':
                    if values.get('duration') is not None:
                        command['duration_seconds'] = round(command['duration_seconds'] + values['duration'], 6)
                    if isinstance(values['error'], Expired) and 'git subprocess deadline' in str(values['error']):
                        command['timeouts'] += 1
        if self.candidate is not None:
            item = self.item(self.candidate)
            if phase == 'start' and (event == 'mutation' or event == 'git' and args[2:4] == ('worktree', 'remove')):
                effect = 'repair' if event == 'mutation' and not kwargs['remove'] else 'remove'
                item['attempted_effect'][effect] = item['attempted_effect'].get(effect, 0) + 1
            if phase == 'finish' and values.get('error') is None:
                if event == 'snapshot' and str(args[0]) == self.candidate['path']:
                    item['kept_allocated_bytes'] = allocated(values['value'], set())[0]
                if event == 'mutation' and not kwargs['remove']:
                    item['confirmed_effect']['repair'] = len(values['value'])
        if event == 'census' and phase == 'finish' and values['value'] is not None:
            rows = values['value']
            # A census already distinguishes unknown-location admin residue
            # from unregistered, located TASK folders. Report the latest count,
            # not a sum that multiplies residue by repeated topology proofs.
            count = len(rows.residues.keys() - rows.unregistered)
            self.data['clones'][str(args[0])]['gitdir_gone_count'] = count
            counts = [c['gitdir_gone_count'] for c in self.data['clones'].values()]
            self.data['counters']['gitdir_gone_count'] = sum(counts) if all(isinstance(c, int) for c in counts) else 'unknown'
        if event in ('topology', 'census', 'git') and phase == 'finish':
            self.clone = self.clone_stack.pop()
        if event == 'report_write' and phase == 'start':
            # Publish a value snapshot. Its own completed write/duration can
            # only be included at a later existing checkpoint; never add a
            # measurement-only write or mutate the published value afterward.
            self.result['measurements'] = deepcopy(self.data)


def _measure_org(function: Callable) -> Callable:
    @wraps(function)
    def call(*args: Any, **kwargs: Any) -> dict:
        result = kwargs.get('result')
        if result is None:
            result = kwargs['result'] = {}
        token = None
        collector = None
        try:
            collector = _Measurements(result)
            token = _observer.set(collector)
        except Exception as exc:
            result['measurement_error'] = type(exc).__name__
        try:
            return function(*args, **kwargs)
        finally:
            if token is not None:
                _observer.reset(token)
    return call


def _kept_history(summary: Path, deadline: float, date: str) -> dict:
    """One bounded, no-follow pass of reports; no candidate-state I/O."""
    result = dict(date=date, paths={}, interrupted_removals=[], status='complete', reports=0, bytes_read=0)
    try:
        # Bound enumeration, total bytes and path count, including legacy files.
        with directory(summary.parent, deadline) as parent:
            with os.scandir(parent) as entries:
                filenames = []
                for index, entry in enumerate(entries):
                    if index >= 512:
                        raise Retain('history_directory_limit')
                    if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.json', entry.name) and entry.name < date + '.json':
                        filenames.append(entry.name)
            remaining = 32 * 1024 * 1024
            selected = sorted(filenames, reverse=True)[:32]
            if len(filenames) > len(selected):
                result['status'] = 'bounded_window'
            for name in selected:
                check(deadline)
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    before = os.fstat(fd)
                    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 8 * 1024 * 1024:
                        raise Retain('history_file_limit_or_type')
                    # Ordinary aggregate exhaustion is a successful window,
                    # not a corrupt report. Keep dates from validated files.
                    if before.st_size > remaining:
                        result['status'] = 'bounded_window'
                        break
                    raw = os.read(fd, before.st_size + 1)
                    remaining -= len(raw)
                    result['bytes_read'] += len(raw)
                    if len(raw) != before.st_size or signature(os.fstat(fd)) != signature(before) or signature(os.stat(name, dir_fd=parent, follow_symlinks=False)) != signature(before):
                        raise Retain('history_changed')
                    with directory(summary.parent, deadline) as current:
                        if identity(os.fstat(current)) != identity(os.fstat(parent)):
                            raise Retain('history_parent_changed')
                    report = json.loads(raw)
                    for org in report.get('orgs', []):
                        for candidate in org.get('candidates', []):
                            if candidate.get('state') in ('mutation_in_flight', 'partial_or_unknown'):
                                if len(result['interrupted_removals']) >= 256:
                                    raise Retain('history_interrupted_removal_limit')
                                result['interrupted_removals'].append(dict(
                                    path=candidate['path'], phase=candidate.get('phase'), effect='unknown',
                                    summary_path=str(summary.parent / name), source_revision=report.get('source_revision'),
                                    scheduled_date=report.get('scheduled_date'), automatic_recovery=False))
                            if candidate.get('state') == 'retained':
                                path = candidate['path']
                                since = candidate.get('measurement', {}).get('kept_since') or name[:-5]
                                if not isinstance(since, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', since) or since > name[:-5]:
                                    raise Retain('history_date_invalid')
                                result['paths'][path] = min(since, result['paths'].get(path, since))
                                if len(result['paths']) > 8192:
                                    raise Retain('history_path_limit')
                    result['reports'] += 1
                finally:
                    os.close(fd)
    except Exception as exc:
        result.update(status='unavailable:' + type(exc).__name__, paths={}, interrupted_removals=[])
    return result


def _measure_run(function: Callable) -> Callable:
    @wraps(function)
    def call(*args: Any, **kwargs: Any) -> dict:
        token = None
        try:
            summary = args[2] if len(args) > 2 else kwargs['summary']
            deadline = args[1] if len(args) > 1 else kwargs['deadline']
            history = _kept_history(summary, deadline, kwargs.get('scheduled_date') or datetime.now().astimezone().date().isoformat())
            token = _history.set(history)
        except Exception:
            pass
        try:
            return function(*args, **kwargs)
        finally:
            if token is not None:
                _history.reset(token)
    return call


def _stop_cause(error: BaseException) -> str:
    if 'git subprocess deadline' in str(error):
        return 'git_timeout'
    return 'budget' if str(error) == 'total sweep deadline' else 'error'


def _interrupted_measurements(report: dict, cause: str) -> None:
    """Complete timing from the saved start; never infer unreported effects."""
    try:
        for org in report.get('orgs', []):
            data = org.get('measurements')
            if data is not None and org.get('stop_cause') is None:
                duration = measurement_elapsed(data.get('started_monotonic'))
                if duration is not None:
                    data['duration_seconds'] = round(duration, 6)
                org['stop_cause'] = cause
            for candidate in org.get('candidates', []):
                if candidate.get('state') not in ('inspecting', 'mutation_in_flight'):
                    continue
                item = candidate.get('measurement')
                if item is not None:
                    duration = measurement_elapsed(item.get('started_monotonic'))
                    if duration is not None:
                        item['duration_seconds'] = round(duration, 6)
                    item.update(final_reason=cause, effects_after_checkpoint='unknown')
    except Exception as exc:
        report['measurement_error'] = type(exc).__name__


class _RegistrationUnknown(Retain):
    """The entire repository registration scope is uncertain."""


class _MetadataFileError(Retain):
    """Failure at the leaf after opening every literal parent successfully."""


def _signature_change(comparison: str, before: tuple | None, after: tuple | None) -> str:
    """Describe only the already observed unequal signature fields."""
    fields = ('device', 'inode', 'type', 'mode', 'size', 'mtime_ns', 'ctime_ns', 'nlink')
    changes = {}
    for index, field in enumerate(fields):
        old = before[index] if before is not None else None
        new = after[index] if after is not None else None
        if old != new:
            changes[field] = [old, new]
    return ';signature_change=' + json.dumps(
        {'comparison': comparison, 'changes': changes}, separators=(',', ':'),
    )


def git(repo: Path, deadline: float, *args: str) -> str:
    check(deadline)
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_OPTIONAL_LOCKS='0', GIT_TERMINAL_PROMPT='0', LC_ALL='C')
    # Child remains in worker's process group, killed/reaped by supervisor.
    with observing('git', repo, deadline, *args):
        try:
            result = subprocess.run(
                ['git', '-c', 'core.fsmonitor=false', '-C', str(repo), *args], env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=max(.001, min(15, deadline - time.monotonic())),
            )
        except subprocess.TimeoutExpired as exc:
            # Abort the whole worker, so its supervisor kills any Git descendants.
            raise Expired("git subprocess deadline; descendant state unknown") from exc
    check(deadline)
    if result.returncode:
        raise Retain('git_failed:' + result.stderr.decode(errors='replace')[:1000])
    return result.stdout.decode('utf-8', errors='strict')


def _metadata_file(path: Path, deadline: float, *, text: bool = False) -> tuple:
    """Read metadata only through literal, no-follow parents and regular files."""
    check(deadline)
    leaf_error = None
    try:
        with directory(path.parent, deadline) as parent:
            try:
                fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
                try:
                    observed = os.fstat(fd)
                    if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                        raise Retain('unknown_metadata_type_or_hardlink')
                    raw = os.read(fd, 65537 if text else 1)
                    if text and (len(raw) > 65536 or len(raw) != observed.st_size or b'\0' in raw):
                        raise Retain('unknown_metadata_text')
                    if signature(os.fstat(fd)) != signature(observed):
                        raise Retain('metadata_changed_during_read')
                    check(deadline)
                    return signature(observed), raw.decode('utf-8', errors='strict') if text else None
                finally:
                    os.close(fd)
            except Exception as exc:
                leaf_error = exc
                raise
    except Expired:
        raise
    except Exception as exc:
        error_type = _MetadataFileError if leaf_error is exc else Retain
        raise error_type(f'unknown_git_metadata:{path}: {exc}') from exc


class _Registrations(dict):
    """Actual Git rows plus independently proven paths and protected local errors."""
    def __init__(self):
        super().__init__()
        self.linked = {}
        self.topology = {}
        self.metadata = {}
        self.errors = {}
        self.residues = {}
        self.unregistered = set()
        self.missing_heads = set()
        self.missing_folders = {}
        self.arrivals = set()
        self.findings = {}

    def owner(self, path: str) -> tuple:
        if path in self.residues:
            return path, self.topology[path], self.topology[path] if path in self.unregistered else None
        entry = self.linked[path]
        return str(entry), self.topology[str(entry)], self.topology[path]

    def require(self, path: Path, deadline: float) -> None:
        _retain_linked_descendants(path, self, deadline)
        if str(path) in self.missing_folders:
            raise Retain(self.missing_folders[str(path)])
        if str(path) in self.errors or str(path) in self.residues:
            raise Retain((self.errors | self.residues)[str(path)])
        row = self.get(str(path))
        if row is None or 'locked' in row or 'prunable' in row:
            raise Retain('git_unregistered_locked_or_prunable')
        if row.get('branch') not in (None, f'refs/heads/task/{path.name}'):
            raise Retain('unknown_worktree_branch_owner')


@observed('census')
def _registration_metadata(repo: Path, deadline: float, *, metadata_path: Path | None = None, metadata_inventory: _Registrations | None = None) -> _Registrations:
    """Prove the entire literal mapping and safe file census BEFORE Git.

    Ordinary I/O/change errors after that proof can be registration-local.
    Only a final missing gitdir in an otherwise safe entry can be an unmapped
    residue. Literal unregistered TASK directories are protected local paths;
    unsafe objects, conflicting pointers and unknown locations stay shared.
    A candidate-scoped read still stats every object and reads every pointer
    and HEAD. Only siblings' ordinary auxiliary file reads are redundant;
    their fresh stat signatures still detect changes between censuses.
    """
    common = repo / '.git'
    admin = common / 'worktrees'
    result = _Registrations()
    topology = result.topology
    by_entry = {}
    mapped_inodes = set()
    try:
        with directory(repo, deadline) as fd:
            topology[str(repo)] = identity(os.fstat(fd))
            mapped_inodes.add(topology[str(repo)])
        absent = False
        with directory(common, deadline) as fd:
            topology[str(common)] = identity(os.fstat(fd))
            for name in ('commondir', 'gitdir'):
                if os.path.lexists(common / name):
                    raise Retain(f'unexpected_primary_pointer:{common / name}')
            try:
                os.stat('worktrees', dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                absent = True
        if absent:
            topology[str(admin)] = None
            tree, entries = {}, []
        else:
            with directory(admin, deadline) as fd:
                topology[str(admin)] = identity(os.fstat(fd))
                entries = names(fd, deadline)
            # The complete stat census rejects unsafe objects even in a record
            # whose ordinary file is unreadable. It never follows metadata links.
            tree = snapshot(admin, deadline)
            if sorted(relative for relative in tree if relative and '/' not in relative) != entries:
                raise Retain(f'changed_registration_entries:{admin}')
            for relative, observed in tree.items():
                check(deadline)
                name, slash, leaf = relative.partition('/')
                if slash:
                    by_entry.setdefault(name, {})[leaf] = observed
                if not (stat.S_ISDIR(observed.st_mode) or stat.S_ISREG(observed.st_mode)) or (
                        stat.S_ISREG(observed.st_mode) and observed.st_nlink != 1):
                    raise Retain(f'aliased_or_special_metadata:{admin / relative}')
        topology['entries'] = tuple(entries)
        for name in entries:
            check(deadline)
            entry = admin / name
            with directory(entry, deadline) as fd:
                if identity(os.fstat(fd)) != identity(tree[name]):
                    raise Retain(f'changed_registration_entry:{entry}')
                topology[str(entry)] = identity(os.fstat(fd))
            try:
                seen, gitdir = _metadata_file(entry / 'gitdir', deadline, text=True)
            except _MetadataFileError as exc:
                if not isinstance(exc.__cause__, OSError) or exc.__cause__.errno != errno.ENOENT:
                    raise
                # No location can be attributed to this entry. Validate every
                # remaining object, including commondir for any nonempty entry.
                # Never turn its name, HEAD or Git's omission into a worktree.
                topology[str(entry / 'gitdir')] = None
                local_tree = by_entry.get(name, {})
                if local_tree:
                    seen, commondir = _metadata_file(entry / 'commondir', deadline, text=True)
                    if commondir not in ('../..\n', str(common) + '\n'):
                        raise Retain(f'redirected_commondir:{entry / "commondir"}')
                    for relative, observed in local_tree.items():
                        target = entry / relative
                        if stat.S_ISDIR(observed.st_mode):
                            if relative in ('HEAD', 'index'):
                                raise Retain(f'nonregular_registration_metadata:{target}')
                            current = signature(observed)
                        else:
                            current, head = _metadata_file(target, deadline, text=relative == 'HEAD')
                            if current != signature(observed):
                                raise Retain(f'metadata_changed:{target}')
                            if relative == 'HEAD' and not (
                                    re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\n', head)
                                    or re.fullmatch(r'ref: refs/[^\s]+\n', head)):
                                raise Retain(f'malformed_head:{target}')
                        topology[str(target)] = current
                    if topology[str(entry / 'commondir')] != seen:
                        raise Retain(f'metadata_changed:{entry / "commondir"}')
                result.residues[str(entry)] = str(exc)
                continue
            topology[str(entry / 'gitdir')] = seen
            seen, commondir = _metadata_file(entry / 'commondir', deadline, text=True)
            topology[str(entry / 'commondir')] = seen
            gitdir = gitdir.removesuffix('\n')
            if not gitdir or gitdir.startswith('//') or gitdir != os.path.abspath(gitdir) or Path(gitdir).name != '.git':
                raise Retain(f'malformed_gitdir:{entry / "gitdir"}')
            path = Path(gitdir).parent
            if str(path) in result.linked or path == repo:
                raise Retain(f'conflicting_gitdir:{entry / "gitdir"}')
            if commondir not in ('../..\n', str(common) + '\n'):
                raise Retain(f'redirected_commondir:{entry / "commondir"}')
            # Only final-folder ENOENT through a proven literal parent qualifies.
            # Every ancestor, entry/pointer, name and unique mapping is still
            # reconciled by all three existing censuses and Git's captured list.
            with directory(path.parent, deadline) as parent:
                topology[str(path.parent)] = identity(os.fstat(parent))
                try:
                    os.stat(path.name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    topology[str(path)] = None
                    result.linked[str(path)] = entry
                    result.missing_folders[str(path)] = f'missing_worktree_folder:{path}'
                    continue
            with directory(path, deadline) as fd:
                topology[str(path)] = identity(os.fstat(fd))
                if topology[str(path)] in mapped_inodes:
                    raise Retain(f'aliased_worktree_target:{path}')
                mapped_inodes.add(topology[str(path)])
            seen, backlink = _metadata_file(Path(gitdir), deadline, text=True)
            if backlink != 'gitdir: ' + str(entry) + '\n':
                raise Retain(f'conflicting_worktree_backlink:{gitdir}')
            topology[gitdir] = seen
            result.linked[str(path)] = entry
        prefix = repo / '.claude/worktrees'
        try:
            with directory(repo / '.claude', deadline) as fd:
                try:
                    os.stat('worktrees', dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    roots = None
                else:
                    with directory(prefix, deadline) as pfd:
                        topology[str(prefix)] = identity(os.fstat(pfd))
                        roots = names(pfd, deadline)
        except FileNotFoundError:
            roots = None
        topology['task_roots'] = None if roots is None else tuple(roots)
        for name in roots or ():
            check(deadline)
            path = prefix / name
            if TASK.fullmatch(name) and str(path) not in result.linked:
                # A literal directory without a pointer has a known location,
                # even though it supplies no registration or deletion authority.
                with directory(prefix, deadline) as pfd:
                    observed = os.stat(name, dir_fd=pfd, follow_symlinks=False)
                    if not stat.S_ISDIR(observed.st_mode):
                        raise Retain(f'unknown_unregistered_task_worktree:{path}')
                with directory(path, deadline) as fd:
                    topology[str(path)] = identity(os.fstat(fd))
                    if topology[str(path)] in mapped_inodes:
                        raise Retain(f'aliased_worktree_target:{path}')
                    mapped_inodes.add(topology[str(path)])
                    try:
                        os.stat('.git', dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise Retain(f'unknown_unregistered_task_worktree:{path}:unreconciled_backlink')
                result.residues[str(path)] = f'unknown_unregistered_task_worktree:{path}'
                result.unregistered.add(str(path))
        # All paths/containment and initial object types are now accounted for.
        # Keep pointers in shared topology; only auxiliary/HEAD reads may fail
        # locally, and only with a still ordinary or genuinely missing leaf.
        for path, entry in result.linked.items():
            observed_files = result.metadata[path] = {}
            local_tree = by_entry.get(entry.name, {})
            local_tree.setdefault('HEAD', None)
            for relative, observed in local_tree.items():
                check(deadline)
                target = entry / relative
                if observed is not None and stat.S_ISDIR(observed.st_mode):
                    if relative in ('HEAD', 'index'):
                        raise Retain(f'nonregular_registration_metadata:{target}')
                    observed_files[str(target)] = signature(observed)
                    continue
                if (metadata_path is not None and metadata_inventory is not None and path != str(metadata_path)
                        and metadata_path not in Path(path).parents and Path(path) not in metadata_path.parents
                        and relative not in ('HEAD', 'gitdir', 'commondir')
                        and metadata_inventory.metadata.get(path, {}).get(str(target)) == signature(observed)):
                    observed_files[str(target)] = signature(observed)
                    continue
                try:
                    seen, head = _metadata_file(target, deadline, text=relative == 'HEAD')
                except Retain as exc:
                    # A changed/vanished ordinary leaf is local; a newly unsafe
                    # leaf, pointer or ancestor must refuse the shared census.
                    if relative in ('gitdir', 'commondir'):
                        raise
                    with directory(target.parent, deadline) as fd:
                        try: current = os.stat(target.name, dir_fd=fd, follow_symlinks=False)
                        except FileNotFoundError: current = None
                    if current is not None and (not stat.S_ISREG(current.st_mode) or current.st_nlink != 1):
                        raise
                    cause = exc.__cause__
                    if not isinstance(exc, _MetadataFileError) or not (isinstance(cause, OSError) and cause.errno in (
                            errno.EACCES, errno.EPERM, errno.ENOENT, errno.EIO, errno.ESTALE)
                            or str(cause) == 'metadata_changed_during_read'):
                        raise
                    result.errors.setdefault(path, str(exc))
                    if relative == 'HEAD' and isinstance(cause, FileNotFoundError):
                        result.missing_heads.add(path)
                    continue
                observed_files[str(target)] = seen
                if observed is None or seen != signature(observed):
                    if relative in ('gitdir', 'commondir'):
                        raise Retain(f'metadata_changed:{target}')
                    result.errors.setdefault(path, f'metadata_changed:{target}' + _signature_change(
                        'snapshot_to_read', signature(observed) if observed is not None else None, seen,
                    ))
                if relative == 'HEAD' and not (
                        re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\n', head)
                        or re.fullmatch(r'ref: refs/[^\s]+\n', head)):
                    raise Retain(f'malformed_head:{target}')
        return result
    except Expired:
        raise
    except Exception as exc:
        raise _RegistrationUnknown(f'unknown_git_registrations:{admin}: {exc}') from exc


def _registration_additions(before: _Registrations, after: _Registrations, repo: Path, deadline: float) -> set[str]:
    """Allow only additive, fully attributed conventional TASK roots/pairs.

    Existing identities/pointers/residues must remain exact. A new unmapped
    admin entry, non-TASK name or unknown descendant cannot be attributed here.
    """
    prefix, admin = repo / '.claude/worktrees', repo / '.git/worktrees'
    failure = f'git_mapping_changed_during_inventory:{repo}'
    old_roots = set(before.topology['task_roots'] or ())
    new_roots = set(after.topology['task_roots'] or ())
    old_entries, new_entries = set(before.topology['entries']), set(after.topology['entries'])
    if not old_roots <= new_roots or not old_entries <= new_entries:
        raise _RegistrationUnknown(failure)
    additions = set()
    allowed_keys = set()
    entries = set()
    for name in new_roots - old_roots:
        check(deadline)
        path = str(prefix / name)
        if not TASK.fullmatch(name):
            raise _RegistrationUnknown(failure)
        additions.add(path)
        allowed_keys.add(path)
        entry = after.linked.get(path)
        if entry is not None:
            if not TASK.fullmatch(entry.name) or entry.name in old_entries:
                raise _RegistrationUnknown(failure)
            entries.add(entry.name)
            allowed_keys.update((str(entry), str(entry / 'gitdir'), str(entry / 'commondir'), str(Path(path) / '.git')))
        elif path not in after.residues:
            raise _RegistrationUnknown(failure)
    if entries != new_entries - old_entries:
        raise _RegistrationUnknown(failure)
    if {p: e for p, e in after.linked.items() if p not in additions} != before.linked:
        raise _RegistrationUnknown(failure)
    if set(after.residues) - additions != set(before.residues):
        raise _RegistrationUnknown(failure)
    if after.unregistered - additions != before.unregistered:
        raise _RegistrationUnknown(failure)
    # Parent identity creation is safe only for the first attributed addition;
    # replacement of an existing parent remains a shared refusal.
    for parent in (prefix, admin):
        key = str(parent)
        if additions and before.topology.get(key) is None and after.topology.get(key) is not None:
            allowed_keys.add(key)
    for key in before.topology.keys() | after.topology.keys():
        check(deadline)
        if key in ('entries', 'task_roots'):
            if not additions and before.topology[key] != after.topology[key]:
                raise _RegistrationUnknown(failure)
        elif key in allowed_keys:
            if key in before.topology and before.topology[key] is not None:
                raise _RegistrationUnknown(failure)
        elif before.topology.get(key) != after.topology.get(key):
            raise _RegistrationUnknown(failure)
    return additions


@observed('topology')
def registrations(repo: Path, deadline: float, *, retained: dict | None = None, metadata_path: Path | None = None, metadata_inventory: _Registrations | None = None) -> _Registrations:
    before = _registration_metadata(repo, deadline, metadata_path=metadata_path, metadata_inventory=metadata_inventory)
    # A second literal census before Git catches mapping/type changes during
    # ordinary reads. Local churn protects its owner without poisoning peers.
    after = _registration_metadata(repo, deadline, metadata_path=metadata_path, metadata_inventory=metadata_inventory)
    additions = _registration_additions(before, after, repo, deadline)
    errors = dict(before.errors)
    for path, error in after.errors.items():
        errors.setdefault(path, error)
    for path in before.linked:
        left, right = before.metadata[path], after.metadata[path]
        for target in sorted(left.keys() | right.keys()):
            check(deadline)
            if left.get(target) != right.get(target):
                errors.setdefault(path, f'metadata_changed:{target}' + _signature_change(
                    'pre_git_censuses', left.get(target), right.get(target),
                ))
    if retained is not None:
        latched = retained.setdefault(str(repo), {})
        for path, (_, owner, _) in latched.items():
            check(deadline)
            if (path not in before.linked and path not in before.residues) or before.owner(path) != owner:
                raise _RegistrationUnknown(f'local_registration_attribution_lost:{repo}:{path}')
    try:
        raw = git(repo, deadline, 'worktree', 'list', '--porcelain', '-z')
    except (Retain, UnicodeError) as exc:
        raise _RegistrationUnknown(f'unknown_git_registration_list:{repo}: {exc}') from exc
    for block in raw.strip('\0').split('\0\0'):
        row = {}
        for field in block.split('\0'):
            key, _, value = field.partition(' ')
            if key in row:
                raise _RegistrationUnknown(f'duplicate_git_registration_field:{repo}')
            row[key] = value
        path = row.get('worktree')
        if not path or path in after:
            raise _RegistrationUnknown(f'unknown_git_registration:{repo}')
        after[path] = row
    final = _registration_metadata(repo, deadline, metadata_path=metadata_path, metadata_inventory=metadata_inventory)
    additions |= _registration_additions(after, final, repo, deadline)
    # Git's list can precede or follow an additive TASK arrival. Only those
    # newly proven paths may be absent from the captured list; every extra row
    # still needs a complete final reciprocal mapping.
    expected = {str(repo), *final.linked}
    if set(after) - expected or expected - set(after) - additions:
        raise _RegistrationUnknown(f'unknown_git_registration_inventory:{repo / ".git/worktrees"}')
    for path in final.linked:
        if path not in after:
            continue
        head = after[path].get('HEAD', '')
        if not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', head) or set(head) == {'0'}:
            if not (path in after.missing_heads | final.missing_heads and set(head) == {'0'} and 'detached' in after[path]):
                raise _RegistrationUnknown(f'unknown_git_registration_head:{final.linked[path]}')
    for path in after.linked:
        if path in final.errors:
            errors.setdefault(path, final.errors[path])
        for target in sorted(after.metadata[path].keys() | final.metadata[path].keys()):
            check(deadline)
            if after.metadata[path].get(target) != final.metadata[path].get(target):
                errors.setdefault(path, f'metadata_changed:{target}' + _signature_change(
                    'post_git_census', after.metadata[path].get(target), final.metadata[path].get(target),
                ))
    for path in additions:
        if path in final.linked:
            errors.setdefault(path, f'unknown_unregistered_task_worktree:{path}')
    errors.update({p: e for p, e in final.errors.items() if p not in errors})
    errors.update(final.missing_folders)
    if retained is not None:
        for path, error in (errors | before.residues | after.residues | final.residues).items():
            finding = ('missing_folder' if path in final.missing_folders else
                       'unknown_location_residue' if path in final.residues and path not in final.unregistered else
                       'arrival' if path in additions and path in final.linked else
                       'unregistered_task' if path in final.unregistered else 'mapped_metadata')
            latched.setdefault(path, (error, final.owner(path), finding))
        final.findings = {path: value[2] for path, value in latched.items()}
        errors = {path: value[0] for path, value in latched.items() if path in final.linked}
        final.residues = {path: latched[path][0] for path in final.residues}
    final.update(after)
    final.arrivals = additions
    final.errors = errors | final.missing_folders
    return final


def read_json(path: Path, deadline: float, limit: int = 65536):
    check(deadline)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        import stat
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Retain('unknown_evidence_type')
        raw = os.read(fd, limit + 1)
        if len(raw) > limit:
            raise Retain('unknown_evidence_size')
        return json.loads(raw)
    finally:
        os.close(fd)


class Evidence:
    """Read-only native task/job/audit records. Never opens Database/schema."""
    def __init__(self, org: Path, deadline: float, *, writer_id: str | None = None, writing: bool = False):
        self.org, self.deadline = org, deadline
        self.writer_id, self.writing = writer_id, writing

    def _move_writer(self, conn: sqlite3.Connection) -> None:
        if not self.writing:
            return
        from types import SimpleNamespace
        from runtime.infrastructure.team_move_store import validate_team_move_schema
        from runtime.infrastructure.producer_admission import NativeWriterStore
        # No Database initializer or schema installation in this child. An
        # installed layout requires the actual parent's still-active global
        # reservation, not a flag or process-local coordinator in this worker.
        if not validate_team_move_schema(conn, org_slug=self.org.name):
            return
        writer = NativeWriterStore(SimpleNamespace(_conn=conn), self.org.name).history_uncommitted().get(self.writer_id)
        if (writer is None or writer.publisher != 'disk_sweep' or writer.state != 'active'
                or writer.agents or writer.teams):
            raise Retain('team_move_cleanup_owner_unavailable')
        if conn.execute('SELECT 1 FROM team_move_operations WHERE active_slot=1').fetchone():
            raise Retain('team_move_native_held')

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """One consistent read; caller never carries it across filesystem work."""
        check(self.deadline)
        # Opening mode=ro cannot create/upgrade a production database.
        conn = sqlite3.connect((self.org / 'happyranch.db').as_uri() + '?mode=ro', uri=True, timeout=.1)
        conn.row_factory = sqlite3.Row
        conn.set_progress_handler(lambda: int(time.monotonic() >= self.deadline), 1000)
        try:
            conn.execute('BEGIN')
            self._move_writer(conn)
            # The existing primary-key index makes each ancestry lookup local.
            # SQLite permits multiple NULLs in a TEXT PRIMARY KEY: preserve the
            # old full-table reader's duplicate-key refusal for that case too.
            if len(conn.execute('SELECT id FROM tasks WHERE id IS NULL LIMIT 2').fetchall()) > 1:
                raise _LineageUnknown('unknown_conflicting_lineage', 'org_unavailable')
            yield conn
        finally:
            conn.close()

    def _ownership(self, conn: sqlite3.Connection, workspace: Path, task_id: str, records: dict) -> None:
        """Same evaluator for refusal inventory and fresh, independent admission."""
        tasks = records.setdefault('tasks', {})

        def ancestry(owner, finding):
            seen = set()
            while True:
                check(self.deadline)
                if not isinstance(owner, str) or not owner:
                    raise _LineageUnknown('unknown_malformed_job_or_task_lineage', finding)
                if owner in seen:
                    raise _LineageUnknown('unknown_missing_or_cyclic_lineage', finding)
                if owner not in tasks:
                    row = conn.execute('SELECT id,parent_task_id,status,assigned_agent FROM tasks WHERE id=?', (owner,)).fetchone()
                    if row is None:
                        raise _LineageUnknown('unknown_missing_or_cyclic_lineage', finding)
                    tasks[owner] = dict(row)
                seen.add(owner)
                row = tasks[owner]
                parent = row['parent_task_id']
                if parent is None:
                    return owner
                if not isinstance(parent, str) or not parent:
                    raise _LineageUnknown('unknown_conflicting_lineage', finding)
                owner = parent

        root = ancestry(task_id, 'candidate_lineage_unknown')
        owner = tasks[task_id]
        if owner['assigned_agent'] != workspace.name:
            raise Retain('unknown_owner_workspace')
        for row in (owner, tasks[root]):
            if row['status'] not in TERMINAL:
                raise Retain('task_or_root_not_terminal:' + str(row['status']))
        # Actual recorded workspace evidence, not TASK-like basename alone.
        observed = []
        for row in conn.execute("SELECT payload FROM audit_log WHERE task_id=? AND action='session_start'", (task_id,)):
            check(self.deadline)
            payload = json.loads(row[0])
            if not isinstance(payload, dict) or not isinstance(payload.get('workspace'), str):
                raise Retain('unknown_workspace_evidence')
            observed.append(payload['workspace'])
        if not observed or set(observed) != {str(workspace)}:
            raise Retain('unknown_missing_or_conflicting_workspace_evidence')
        # Enumerate ALL statuses, including unknown/NULL values, through
        # the existing status index. Only unfinished rows need owner reads.
        # Preserve the old table traversal's rowid order: an earlier active
        # job refusal must not be replaced by a later unknown-job refusal.
        if 'jobs' not in records:
            jobs = []
            for status, in conn.execute('SELECT DISTINCT status FROM jobs'):
                check(self.deadline)
                if status not in ('completed', 'failed', 'rejected'):
                    for row in conn.execute('SELECT rowid,task_id,status FROM jobs WHERE status IS ?', (status,)):
                        check(self.deadline)
                        jobs.append(row)
            jobs.sort(key=lambda row: row['rowid'])
            records['jobs'] = jobs
        jobs = records['jobs']
        # Task rows above are memoized within this transaction only. Every
        # ownership call opens a new current read, including after scans,
        # before removal, and on the existing repair/retry admissions.
        for row in jobs:
            check(self.deadline)
            if row['status'] not in ('pending', 'queued', 'running'):
                raise Retain('unknown_job_status')
            job_root = ancestry(row['task_id'], 'job_lineage_unknown')
            if job_root == root:
                raise Retain('active_job:' + row['status'])

    @observed('ownership')
    def ownership(self, path: Path, workspace: Path, task_id: str) -> None:
        with self._connection() as conn:
            self._ownership(conn, workspace, task_id, {})

    @observed('ownership')
    def inventory(self, candidates: list[dict]) -> dict[str, Exception]:
        """Read task/job inventory once. Return failures only, never clearance."""
        refused = {}
        if not candidates:
            return refused
        try:
            with self._connection() as conn:
                tasks = {}
                for row in conn.execute('SELECT id,parent_task_id,status,assigned_agent FROM tasks'):
                    check(self.deadline)
                    tasks[row['id']] = dict(row)
                records = dict(tasks=tasks)
                for candidate in candidates:
                    check(self.deadline)
                    try:
                        self._ownership(conn, Path(candidate['workspace']), Path(candidate['path']).name, records)
                    except Expired:
                        raise
                    except Exception as exc:
                        refused[candidate['path']] = exc.with_traceback(None)
        except Expired:
            raise
        except Exception as exc:
            # An unavailable inventory is uncertainty, never an empty success.
            return {candidate['path']: exc.with_traceback(None) for candidate in candidates}
        return refused




def _retain_linked_descendants(path: Path, rows: dict[str, dict], deadline: float) -> None:
    """A complete reciprocal census protects every different linked descendant."""
    for registered in rows.linked.keys() | rows.residues.keys():
        check(deadline)
        descendant = Path(registered)
        if descendant != path and path in descendant.parents:
            raise Retain(f'linked_descendant:{descendant}')
    check(deadline)


def _worktree_guard(candidate: dict, deadline: float) -> None:
    path = Path(candidate["path"])
    task_id = path.name
    guard = read_json(path / '.worktree-guard.json', deadline, 4 * 1024 * 1024)
    if (not isinstance(guard, dict) or guard.get('task_id') != task_id
            or guard.get('worktree_root') != str(path)
            or guard.get('primary_root') != candidate['repo']):
        raise Retain('unknown_worktree_owner')


def _worktree_location(path: Path, repo: Path, rows: _Registrations, deadline: float) -> None:
    """Never send Git through an own mapping changed since discovery."""
    try:
        entry = rows.linked[str(path)]
        for parent in (repo, repo / '.git', repo / '.git/worktrees', entry, path.parent, path):
            with directory(parent, deadline) as fd:
                if identity(os.fstat(fd)) != rows.topology[str(parent)]:
                    raise Retain(f'changed_registration_entry:{parent}')
        for pointer in (entry / 'gitdir', entry / 'commondir', path / '.git'):
            seen, _ = _metadata_file(pointer, deadline)
            if seen != rows.topology[str(pointer)]:
                raise Retain(f'metadata_changed:{pointer}')
    except Expired:
        raise
    except Exception as exc:
        raise _RegistrationUnknown(f'unknown_git_registrations:{repo / ".git/worktrees"}: {exc}') from exc


def worktree_inventory(candidate: dict, topology: dict, deadline: float) -> Exception | None:
    """Inspect cheap keeps in candidate order; success never admits deletion."""
    try:
        path = Path(candidate['path'])
        _worktree_guard(candidate, deadline)
        rows = topology[candidate['repo']]
        rows.require(path, deadline)
        _worktree_location(path, Path(candidate['repo']), rows, deadline)
        dirty = git(path, deadline, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
        if dirty:
            candidate['dirty_paths'] = dirty.split('\0')
            raise Retain('dirty_or_untracked')
    except Expired:
        raise
    except Exception as exc:
        return exc.with_traceback(None)
    return None


def _worktree_gate(candidate: dict, deadline: float, retained: dict | None = None, inventory: _Registrations | None = None) -> tuple:
    path = Path(candidate['path'])
    task_id = path.name
    _worktree_guard(candidate, deadline)
    repo = Path(candidate['repo'])
    rows = registrations(repo, deadline, retained=retained, metadata_path=path, metadata_inventory=inventory)
    rows.require(path, deadline)
    row = rows.get(str(path))
    if row is None or 'locked' in row or 'prunable' in row:
        raise Retain('git_unregistered_locked_or_prunable')
    branch = row.get('branch')
    if branch is not None and branch != f'refs/heads/task/{task_id}':
        raise Retain('unknown_worktree_branch_owner')
    common = git(path, deadline, 'rev-parse', '--path-format=absolute', '--git-common-dir').strip()
    # The complete literal census above already proved this primary .git and
    # rejected redirected primary pointers. Resolve the candidate afresh, but
    # do not ask Git to rediscover the same primary common directory each gate.
    if common != str(repo / '.git') or git(path, deadline, 'rev-parse', '--show-toplevel').strip() != str(path):
        raise Retain('unknown_git_ownership')
    with directory(Path(common), deadline):
        pass
    dirty = git(path, deadline, 'status', '--porcelain=v1', '-z', '--untracked-files=all')
    if dirty:
        candidate['dirty_paths'] = dirty.split('\0')
        raise Retain('dirty_or_untracked')
    if git(path, deadline, 'rev-list', '-n1', 'HEAD', '--not', '--branches', '--remotes').strip():
        raise Retain('unique_head_commit')
    return row.get("HEAD"), row.get("branch"), common, rows.topology, rows.metadata[str(path)]

def admit(candidate: dict, evidence: Evidence, deadline: float, *, now: float | None = None, repairs: dict | None = None, retained: dict | None = None, topology: dict | None = None) -> dict:
    """Fresh common gates before each candidate mutation/retry admission."""
    path, workspace = Path(candidate['path']), Path(candidate['workspace'])
    task_id = path.name
    if not TASK.fullmatch(task_id):
        raise Retain('unknown_task_identity')
    # Ancestor no-follow check also covers evidence sidecars' parents.
    with directory(workspace, deadline):
        pass
    evidence.ownership(path, workspace, task_id)
    if candidate['phase'] == 'scratch':
        from runtime.orchestrator.task_scratch import validate_task_scratch_manifest
        manifest = workspace / '.happyranch/task-scratch-manifests' / (task_id + '.json')
        with directory(manifest.parent, deadline):
            pass
        validate_task_scratch_manifest(read_json(manifest, deadline), expected_task_id=task_id, expected_root=path)
    else:
        if topology is not None:
            _worktree_location(path, Path(candidate['repo']), topology[candidate['repo']], deadline)
        inventory = topology[candidate['repo']] if topology is not None else None
        git_identity = _worktree_gate(candidate, deadline, retained, inventory)
        if topology is not None:
            inventoried = topology[candidate['repo']][str(path)]
            if git_identity[:2] != (inventoried.get('HEAD'), inventoried.get('branch')):
                raise Retain('git_identity_changed_since_inventory')
    tree = snapshot(path, deadline, scratch=candidate['phase'] == 'scratch')
    if candidate['phase'] == 'worktree':
        for relative in sorted(tree):
            check(deadline)
            if relative != '.git' and Path(relative).name == '.git':
                raise Retain(f'possible_nested_worktree:{path / relative}')
    import stat
    links = Counter(identity(s) for s in tree.values())
    if any(not stat.S_ISDIR(s.st_mode) and s.st_nlink > links[identity(s)] for s in tree.values()):
        raise Retain('unknown_external_hardlinks')
    cutoff = (time.time() if now is None else now) - 3600
    for relative, observed in tree.items():
        age_stat = observed
        if repairs and relative in repairs:
            repaired_signature, previous = repairs[relative]
            if signature(observed) != repaired_signature:
                raise Retain('tree_changed_after_owned_repair')
            # Only the precisely observed own chmod's ctime/mode is excluded;
            # file writes, additions and unknown directory changes still refuse.
            age_stat = previous
        if max(age_stat.st_mtime, age_stat.st_ctime) > cutoff:
            raise Retain('changed_within_hour')
    # Re-read durable state after slow filesystem/Git observations.
    evidence.ownership(path, workspace, task_id)
    if candidate['phase'] == 'worktree' and _worktree_gate(candidate, deadline, retained, inventory) != git_identity:
        raise Retain('git_identity_changed_during_admission')
    latest = snapshot(path, deadline, scratch=candidate['phase'] == 'scratch')
    if candidate['phase'] == 'worktree':
        for relative in sorted(latest):
            check(deadline)
            if relative != '.git' and Path(relative).name == '.git':
                raise Retain(f'possible_nested_worktree:{path / relative}')
    if not trees_equal(latest, tree, deadline):
        raise Retain('tree_changed_during_admission')
    check(deadline)
    # Re-open without following ancestors; bind complete observed root identity.
    with directory(path, deadline) as fd:
        if signature(os.fstat(fd)) != signature(tree['']):
            raise Retain('identity_changed')
    if candidate['phase'] == 'worktree' and _worktree_gate(candidate, deadline, retained, inventory) != git_identity:
        raise Retain('git_identity_changed_before_action')
    return tree


@contextmanager
def _discovery_scope(path: Path, inventory: dict, deadline: float, checkpoint: Callable[[], None], *, missing_ok: bool = False) -> Iterator[None]:
    """Retain an uncertain literal scope; expiry always unwinds the batch."""
    try:
        check(deadline)
        yield
    except Expired as exc:
        # Nested scopes unwind; retain the narrowest interrupted path once.
        if not inventory['discovery_scopes'] or not inventory['discovery_scopes'][-1].get('interrupted'):
            _scope(inventory, str(path), str(exc), 'run_error')
            next(scope for scope in inventory['discovery_scopes'] if scope['path'] == str(path))['interrupted'] = True
        raise
    except FileNotFoundError as exc:
        if not missing_ok:
            _scope(inventory, str(path), str(exc), 'item_unknown')
    except Exception as exc:
        _scope(inventory, str(path), str(exc) or type(exc).__name__,
               'registration_unknown' if isinstance(exc, _RegistrationUnknown) else 'item_unknown')
    finally:
        try:
            checkpoint()
        except Exception as exc:
            raise Expired('discovery checkpoint failed; global stop: ' + str(exc)) from exc


def discover(org: Path, deadline: float, *, inventory: dict | None = None, checkpoint: Callable[[], None] = lambda: None, retained: dict | None = None, topology: dict | None = None) -> list[dict]:
    """Stable ordering of known candidates; uncertain scopes are never empty."""
    inventory = inventory if inventory is not None else {}
    inventory.update(candidates=[], discovery_scopes=[], scope_classifications={}, exclusions=[], deferred_arrivals=[], discovery='unknown', discovery_root=str(org / 'workspaces'),
                     inventory_window=dict(started_at=datetime.now().astimezone().isoformat()))
    candidates = inventory['candidates']
    workspaces = org / 'workspaces'
    with _discovery_scope(workspaces, inventory, deadline, checkpoint):
        with directory(workspaces, deadline) as wfd:
            owners = names(wfd, deadline)
        for owner in owners:
            workspace = workspaces / owner
            with _discovery_scope(workspace, inventory, deadline, checkpoint):
                with directory(workspace, deadline):
                    pass
                tmp = workspace / '.happyranch/task-tmp'
                with _discovery_scope(tmp, inventory, deadline, checkpoint, missing_ok=True):
                    with directory(tmp, deadline) as fd:
                        for name in names(fd, deadline):
                            if TASK.fullmatch(name):
                                with _discovery_scope(tmp / name, inventory, deadline, checkpoint):
                                    s = os.stat(name, dir_fd=fd, follow_symlinks=False)
                                    candidates.append(dict(phase='scratch', path=str(tmp / name), workspace=str(workspace), oldest=s.st_mtime_ns, state='unreached'))
                repos = workspace / 'repos'
                with _discovery_scope(repos, inventory, deadline, checkpoint, missing_ok=True):
                    with directory(repos, deadline) as fd:
                        repositories = names(fd, deadline)
                    for name in repositories:
                        repo = repos / name
                        with _discovery_scope(repo, inventory, deadline, checkpoint):
                            with directory(repo, deadline):
                                pass
                            # Never walk arbitrary children or let Git find an
                            # ancestor repository. Only literal primary .git.
                            with directory(repo, deadline) as rfd:
                                try:
                                    os.stat('.git', dir_fd=rfd, follow_symlinks=False)
                                except FileNotFoundError:
                                    # Absence from an existing configured clone is
                                    # not an exclusion. Missing/malformed agent
                                    # definitions fail conservatively in this scope.
                                    from runtime.orchestrator.agent_def import parse_agent_text
                                    _, text = _metadata_file(org / 'org/agents' / (owner + '.md'), deadline, text=True)
                                    agent = parse_agent_text(text, expected_name=owner)
                                    if name in agent.repos:
                                        raise Retain('configured_clone_git_missing:' + str(repo))
                                    try:
                                        os.stat('.claude', dir_fd=rfd, follow_symlinks=False)
                                    except FileNotFoundError:
                                        pass
                                    else:
                                        raise Retain('possible_former_clone:' + str(repo))
                                    classification = _classification('ordinary_non_clone', 'non_clone')
                                    if classification['hold'] == 'none':
                                        inventory['exclusions'].append(dict(path=str(repo), reason='ordinary_non_clone'))
                                    continue
                            with directory(repo / '.git', deadline):
                                pass
                            rows = registrations(repo, deadline, retained=retained)
                            for path, error in rows.residues.items():
                                _scope(inventory, path, error, rows.findings.get(path, 'unregistered_task' if path in rows.unregistered else 'unknown_location_residue'), None if path in rows.unregistered else 0)
                            for path, error in rows.errors.items():
                                finding = rows.findings.get(path, 'missing_folder' if path in rows.missing_folders else 'arrival' if path in rows.arrivals else 'mapped_metadata')
                                _scope(inventory, path, error, finding)
                                if finding == 'arrival': inventory['deferred_arrivals'].append(path)
                            if str(repo) not in rows:
                                raise Retain('unknown_git_primary_registration')
                            if topology is not None:
                                topology[str(repo)] = rows
                            prefix = repo / '.claude/worktrees'
                            for literal in rows.linked.keys() | rows.residues.keys():
                                check(deadline)
                                path = Path(literal)
                                if path.parent != prefix or not TASK.fullmatch(path.name):
                                    continue
                                with _discovery_scope(path, inventory, deadline, checkpoint):
                                    with directory(path.parent, deadline) as fd:
                                        try:
                                            s = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
                                        except FileNotFoundError:
                                            s = None
                                    candidates.append(dict(phase='worktree', path=str(path), workspace=str(workspace), repo=str(repo), oldest=s.st_mtime_ns if s else 0, state='unreached'))
    check(deadline)
    candidates.sort(key=lambda c: (c['phase'] != 'scratch', c['oldest'], c['path']))
    inventory['discovery'] = 'partial' if inventory['discovery_scopes'] else 'complete'
    inventory['inventory_window']['finished_at'] = datetime.now().astimezone().isoformat()
    return candidates


def _read_json(path: Path) -> dict:
    """Read reports/markers through literal parents and one regular inode."""
    with directory(path.parent, float('inf')) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd) as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise Retain('ambiguous_disk_sweep_metadata')
            value = json.load(stream)
            if (signature(os.fstat(stream.fileno())) != signature(before)
                    or signature(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != signature(before)):
                raise Retain('disk_sweep_metadata_changed')
            with directory(path.parent, float('inf')) as current:
                if identity(os.fstat(current)) != identity(os.fstat(parent)):
                    raise Retain('disk_sweep_parent_changed')
            return value


@observed('report_write')
def atomic_json(path: Path, value: dict) -> None:
    # Every caller, including the schedule marker, uses literal parents. Never
    # create a missing scheduled control parent or truncate an unowned pending
    # inode (which could be a hardlink into a candidate). Ambiguity refuses.
    with directory(path.parent, float('inf')) as parent:
        def target_signature():
            try:
                observed = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                return None
            if not stat.S_ISREG(observed.st_mode) or observed.st_nlink != 1:
                raise Retain('ambiguous_disk_sweep_publication_target')
            return signature(observed)
        before = target_signature()
        pending = path.name + '.pending'
        fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        # Retain a pending file on failure as uncertainty; never retry or remove
        # someone else's pending evidence. Successful replace consumes ours.
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.flush()
            with directory(path.parent, float('inf')) as current:
                if identity(os.fstat(current)) != identity(os.fstat(parent)):
                    raise Retain('disk_sweep_publication_parent_changed')
            if (target_signature() != before
                    or signature(os.stat(pending, dir_fd=parent, follow_symlinks=False)) != signature(os.fstat(stream.fileno()))
                    or os.fstat(stream.fileno()).st_nlink != 1):
                raise Retain('disk_sweep_publication_changed')
            os.replace(pending, path.name, src_dir_fd=parent, dst_dir_fd=parent)


def _summarize_org(result: dict) -> None:
    candidates = result.get('candidates', [])
    result['coverage'] = _coverage(result)
    # Existing pre-mutation checkpoints persist an intent, not proof of any
    # effect. Interrupted intents remain reporting-only, including next night.
    result['interrupted_removals'] = [dict(path=c['path'], phase=c['phase'],
        state=c['state'], effect='unknown', automatic_recovery=False,
        reason=c.get('reason', 'interrupted_or_unconfirmed_intent'))
        for c in candidates if c.get('state') in ('mutation_in_flight', 'partial_or_unknown')]
    result['measurement_scope'] = 'eligible scanned inodes only; retained/unreached allocation is unknown; removed allocation is not physical freed space'
    result['physical_freed_bytes'] = None
    result['process_scope'] = PROCESS_SCOPE
    # Compatibility field: unused since seq117 removed process inspection.
    result['process_exemptions'] = []
    result['retained_allocation_unknown_count'] = sum(c.get('allocated_bytes') is None for c in candidates)
    result['retained_count'] = sum(c.get('state') == 'retained' for c in candidates)
    result['partial_unknown_count'] = sum(c.get('state') in ('mutation_in_flight', 'partial_or_unknown') for c in candidates)
    result['retained_reasons'] = dict(Counter(c.get('reason', 'unknown').split(':', 1)[0] for c in candidates if c.get('state') in ('retained', 'partial_or_unknown')))
    result['known_eligible_leftovers'] = [c['path'] for c in candidates if c.get('state') == 'eligible']
    for phase, values in result.get('phases', {}).items():
        values['attribution_unknown'] = any(c.get('state') in ('mutation_in_flight', 'partial_or_unknown') for c in candidates if c['phase'] == phase)
    result['known_candidate_count'] = len(candidates)
    result['known_unreached_count'] = sum(c.get('state') == 'unreached' for c in candidates)
    # A known subset count must never stand in for an uncertain total.
    result['unreached'] = result['known_unreached_count'] if result.get('discovery') == 'complete' else None
    result['unknown_scope'] = {
        'discovery_scopes': result.get('discovery_scopes', []),
        'unfinished_discovery_root': result.get('discovery_root') if result.get('discovery') == 'unknown' else None,
        'discovery': result.get('discovery', 'unknown'), 'unreached': result.get('unreached'),
        'inspecting': [c['path'] for c in candidates if c.get('state') == 'inspecting'],
        'partial_mutation': [c['path'] for c in candidates if c.get('state') in ('mutation_in_flight', 'partial_or_unknown')],
    }


@_measure_org
def sweep_org(org: Path, deadline: float, *, mode: str, checkpoint=lambda: None, now: float | None = None, result: dict | None = None, measurement_seen: set | None = None, removal_seen: set | None = None, writer_id: str | None = None) -> dict:
    result = result if result is not None else {}
    result.update(org=org.name, mode=mode, outcome='incomplete', discovery='unknown', candidates=[], unreached=None,
                  phases={p: dict(measured_allocated_bytes=0, removed_attributable_bytes=0, attribution_unknown=False) for p in ('scratch', 'worktree')})
    retained = {}
    def report_checkpoint() -> None:
        observe('org_checkpoint')
        for errors in retained.values():
            for path, (error, owner, finding) in errors.items():
                if not any(scope['path'] == path for scope in result.get('discovery_scopes', [])):
                    _scope(result, path, error, finding, 0 if owner[2] is None and finding == 'unknown_location_residue' else None)
                    if finding == 'arrival': result.setdefault('deferred_arrivals', []).append(path)
                    if result.get('discovery') == 'complete':
                        result['discovery'] = 'partial'
        for candidate in result.get('candidates', []):
            local = retained.get(candidate.get('repo'), {}).get(candidate['path'])
            if local and candidate.get('state') == 'eligible':
                candidate.update(state='retained', reason=local[0])
            observe('item', 'checkpoint', candidate=candidate)
        _summarize_org(result)
        checkpoint()

    evidence = Evidence(org, deadline, writer_id=writer_id, writing=mode == 'on')
    measured_inodes = measurement_seen if measurement_seen is not None else set()
    removed_inodes = removal_seen if removal_seen is not None else set()
    uncertain_repos = set()

    try:
        topology = {}
        candidates = discover(org, deadline, inventory=result, checkpoint=report_checkpoint, retained=retained, topology=topology)
        refusals = evidence.inventory(candidates)
        report_checkpoint()
        for candidate in candidates:
            check(deadline)
            observe('item', 'start', candidate=candidate)
            candidate['state'] = 'inspecting'
            report_checkpoint()
            phase = result['phases'][candidate['phase']]
            mutation_started = False
            try:
                if result.get('org_hold'):
                    raise Retain(result['org_hold'])
                if candidate.get('repo') in uncertain_repos:
                    raise _RegistrationUnknown('unknown_repository_registration_scope')
                local = retained.get(candidate.get('repo'), {}).get(candidate['path'])
                if local:
                    raise Retain(local[0])
                if candidate['path'] in refusals:
                    raise refusals[candidate['path']]
                if candidate['phase'] == 'worktree':
                    refusal = worktree_inventory(candidate, topology, deadline)
                    if refusal is not None: raise refusal
                tree = admit(candidate, evidence, deadline, now=now, retained=retained, topology=topology)
                amount, _ = allocated(tree, measured_inodes)
                phase['measured_allocated_bytes'] += amount
                candidate['allocated_bytes'] = amount
                candidate['state'] = 'eligible'
                report_checkpoint()
                if mode != 'on':
                    continue
                # The entire admission is repeated immediately before mutation;
                # the first pass is inventory only, not reusable authority.
                # Checkpoint uncertainty BEFORE the final full fresh gates, so
                # writing a large summary cannot stale mutation admission.
                candidate['state'] = 'mutation_in_flight'
                phase['attribution_unknown'] = True
                report_checkpoint()
                fresh = admit(candidate, evidence, deadline, now=now, retained=retained, topology=topology)
                if not trees_equal(tree, fresh, deadline):
                    raise Retain('tree_changed_before_mutation')
                if candidate['phase'] == 'scratch':
                    mutation_started = True
                    mutate(Path(candidate['path']), fresh, deadline, remove=True, scratch=True)
                else:
                    repo, path = Path(candidate['repo']), Path(candidate['path'])
                    # Last local metadata check after fresh tree comparisons;
                    # Git must never consume a known redirected registration.
                    rows = registrations(repo, deadline, retained=retained, metadata_path=path, metadata_inventory=topology[str(repo)])
                    rows.require(path, deadline)
                    admitted_branch = rows[str(path)].get('branch')
                    mutation_started = True
                    try:
                        git(repo, deadline, 'worktree', 'remove', str(path))
                    except Retain as exc:
                        candidate['first_remove_error'] = str(exc)
                        # Only one Git retry, with full fresh gates before
                        # contained repair AND again before Git retry. Own
                        # permission/partial changes count as recent and may
                        # conservatively refuse that retry.
                        retry_tree = admit(candidate, evidence, deadline, now=now, retained=retained, topology=topology)
                        registrations(repo, deadline, retained=retained, metadata_path=path, metadata_inventory=topology[str(repo)]).require(path, deadline)
                        repairs = mutate(path, retry_tree, deadline, remove=False)
                        admit(candidate, evidence, deadline, now=now, repairs=repairs, retained=retained, topology=topology)
                        registrations(repo, deadline, retained=retained, metadata_path=path, metadata_inventory=topology[str(repo)]).require(path, deadline)
                        candidate['retry_count'] = 1
                        git(repo, deadline, 'worktree', 'remove', str(path))
                    if str(path) in registrations(repo, deadline, retained=retained, metadata_path=path, metadata_inventory=topology[str(repo)]):
                        raise Retain('registration_remains_after_remove')
                try:
                    os.lstat(candidate['path'])
                except FileNotFoundError:
                    pass
                else:
                    raise Retain('root_remains_after_remove')
                if candidate['phase'] == 'worktree' and admitted_branch is not None:
                    # Removing a worktree must preserve its named branch. A
                    # missing/refused resolution is an unconfirmed effect,
                    # even when the folder and registration are already gone.
                    git(repo, deadline, 'rev-parse', '--verify', admitted_branch + '^{commit}')
                _, attributable = allocated(fresh, removed_inodes)
                phase['removed_attributable_bytes'] += attributable
                candidate.update(state='removed', removed_attributable_bytes=attributable)
                # Earlier partial operations remain unknown.
                phase['attribution_unknown'] = any(c.get('state') in ('partial_or_unknown', 'mutation_in_flight') for c in result['candidates'] if c['phase'] == candidate['phase'])
            except Expired as exc:
                observe('item', 'interrupted', candidate=candidate, reason=str(exc))
                raise
            except Exception as exc:
                classification = _classification(str(exc), 'registration_unknown' if isinstance(exc, _RegistrationUnknown) else
                                                 exc.finding if isinstance(exc, _LineageUnknown) else
                                                 'org_unavailable' if isinstance(exc, sqlite3.Error) else None)
                if isinstance(exc, _LineageUnknown):
                    result.setdefault('scope_classifications', {})[candidate['path']] = classification
                if classification['hold'] == 'org': result['org_hold'] = str(exc)
                if classification['hold'] == 'clone':
                    repo = candidate['repo']
                    if repo not in uncertain_repos:
                        _scope(result, repo, str(exc), 'registration_unknown')
                        uncertain_repos.add(repo)
                    result['discovery'] = 'partial'
                if mutation_started:
                    candidate['state'] = 'partial_or_unknown'
                else:
                    candidate['state'] = 'retained'
                candidate['reason'] = str(exc) or type(exc).__name__
            finally:
                observe('item', 'finish', candidate=candidate)
            report_checkpoint()
        result['outcome'] = ('error' if any(c['state'] == 'partial_or_unknown' for c in candidates)
                             else 'complete' if _coverage(result)['complete'] else 'incomplete')
        # Review decision 2 removes empty-scope repository-wide prune entirely.
        # No registration repair or metadata housekeeping replaces it.

    except Expired as exc:
        result['stop_cause'] = _stop_cause(exc)
        result.update(outcome='incomplete', error=str(exc))
        result['candidates'].sort(key=lambda c: (c['phase'] != 'scratch', c['oldest'], c['path']))
        report_checkpoint()
        raise
    except Exception as exc:
        result['stop_cause'] = 'error'
        result.update(outcome='incomplete', error=str(exc) or type(exc).__name__)
    result.setdefault('stop_cause', 'error' if result.get('error') or result.get('outcome') == 'error' else 'finished')
    observe('org_checkpoint')
    _summarize_org(result)
    checkpoint()
    return result


@_measure_run
def sweep(roots: list[str], deadline: float, summary: Path, *, dry_run: bool, scheduled_date: str | None, revision: str, started_monotonic: float | None = None, runtime_root: Path | None = None, native_writers: dict[str, str] | None = None) -> dict:
    from runtime.orchestrator.org_config import load_org_config
    from runtime.orchestrator._paths import OrgPaths
    report = dict(format='disk-sweep@1', revision=revision, source_revision=revision, scheduled_date=scheduled_date,
                  timezone=datetime.now().astimezone().tzname(), timezone_offset=datetime.now().astimezone().strftime('%z'),
                  started_at=datetime.now().astimezone().isoformat(), deadline_monotonic=deadline,
                  budget_seconds=deadline - started_monotonic if started_monotonic is not None else BUDGET_SECONDS,
                  process_scope=PROCESS_SCOPE,
                  outcome='incomplete', orgs=[], unreached_orgs=list(sorted(roots)),
                  stop_cause='in_progress',
                  policy='thr307-ended-root-one-hour-all-orgs',
                  previous_interrupted_removals=(_history.get() or {}).get('interrupted_removals', []),
                  measurement_history={k: v for k, v in (_history.get() or {}).items() if k not in ('paths', 'interrupted_removals')})
    pending_checkpoints = 0
    initialized_org = None
    def checkpoint(*, force: bool = False) -> None:
        nonlocal pending_checkpoints, initialized_org
        pending_checkpoints += 1
        current = report['orgs'][-1] if report['orgs'] else {}
        # Nonmutating progress may lag by a bounded batch. The last published
        # run stays incomplete; it cannot claim unpublished keeps or effects.
        # Always persist mutation intent BEFORE the final fresh admission, and
        # flush each org exit (including expiry/error) under its observer.
        if (not force and pending_checkpoints < 32 and 'stop_cause' not in current
                and not ('phases' in current and current is not initialized_org)
                and not any(c.get('state') == 'mutation_in_flight' for c in current.get('candidates', []))):
            return
        atomic_json(summary, report)
        pending_checkpoints = 0
        if 'phases' in current:
            initialized_org = current
    checkpoint(force=True)
    measurement_seen, removal_seen = set(), set()
    if runtime_root is not None:
        report['runtime_discovery'] = 'unknown'
        checkpoint(force=True)
        try:
            with directory(runtime_root / 'orgs', deadline) as fd:
                roots = [str(runtime_root / 'orgs' / name) for name in names(fd, deadline) if name not in ('_pending', '_archive')]
            report.update(runtime_discovery='complete', unreached_orgs=roots.copy())
        except Exception as exc:
            report.update(error=str(exc), unreached_orgs=None, stop_cause=_stop_cause(exc))
            checkpoint(force=True)
            return report
    report['selected_org_roots'] = roots.copy()
    checkpoint(force=True)
    try:
        for root in sorted(roots):
            check(deadline)
            item = dict(org=Path(root).name, outcome='incomplete')
            report['orgs'].append(item)
            report['unreached_orgs'].remove(root)
            checkpoint(force=True)
            try:
                config_path = Path(root) / 'org/config.yaml'
                load_org_config(OrgPaths(Path(root)))
                item['config_sha256'] = hashlib.sha256(config_path.read_bytes() if config_path.exists() else b'').hexdigest()
                # Policy is independent of org config; loading above preserves
                # unrelated validation. Only explicit preview suppresses writes.
                mode = 'dry_run' if dry_run else 'on'
                sweep_org(Path(root), deadline, mode=mode, checkpoint=checkpoint, result=item, measurement_seen=measurement_seen, removal_seen=removal_seen,
                          writer_id=(native_writers or {}).get(root))
            except Expired:
                raise
            except Exception as exc:
                item.update(outcome='error', error=str(exc) or type(exc).__name__, stop_cause='error')
            checkpoint(force=True)
        report['outcome'] = 'complete' if all(o['outcome'] == 'complete' for o in report['orgs']) else 'incomplete'
        report['stop_cause'] = 'error' if any(o.get('error') or o.get('outcome') == 'error' for o in report['orgs']) else 'finished'
    except Expired as exc:
        report['stop_cause'] = _stop_cause(exc)
        report['error'] = str(exc)
    report['finished_at'] = datetime.now().astimezone().isoformat()
    report['elapsed_seconds'] = max(0, time.monotonic() - (started_monotonic if started_monotonic is not None else deadline - BUDGET_SECONDS))
    try:
        space = os.statvfs(summary.parent)
        report['disk_free_percent_context_only'] = 100 * space.f_bavail / space.f_blocks if space.f_blocks else None
        report['low_disk_below_20_percent'] = report['disk_free_percent_context_only'] is not None and report['disk_free_percent_context_only'] < 20
    except OSError:
        report['disk_free_percent_context_only'] = None
    _finalize_night(report)
    checkpoint(force=True)
    return report


async def run_worker(roots: list[str], summary: Path, *, dry_run: bool, scheduled_date: str | None = None, budget: float = BUDGET_SECONDS, runtime_root: Path | None = None, native_writers: dict[str, str] | None = None) -> dict:
    """Supervise entire process group, including Git, until stopped/reaped.

    SIGKILL at the shared deadline (no deletion grace/window extension).
    A task in uninterruptible kernel I/O can delay reaping; this OS limitation
    is not presented as a hard real-time guarantee. No detached worker survives
    ordinary cancellation and the daemon waits for group termination.
    """
    started_monotonic = time.monotonic()
    deadline = started_monotonic + budget
    revision = await asyncio.to_thread(_revision)
    argv = [*helper_argv('disk-sweep'), '--worker', '--summary', str(summary), '--deadline', str(deadline), '--started-monotonic', str(started_monotonic), '--revision', revision]
    for root in roots:
        argv += ['--org-root', root]
    for root, writer_id in sorted((native_writers or {}).items()):
        argv += ['--native-writer', root, writer_id]
    if runtime_root is not None:
        argv += ['--runtime-root', str(runtime_root)]
    if dry_run:
        argv.append('--dry-run')
    if scheduled_date:
        argv += ['--scheduled-date', scheduled_date]
    initial = dict(format='disk-sweep@1', revision=revision, source_revision=revision, orgs=[], unreached_orgs=roots,
                   runtime_discovery='unknown' if runtime_root else 'selected_daemon_orgs',
                   outcome='incomplete', scheduled_date=scheduled_date, budget_seconds=budget,
                   started_at=datetime.now().astimezone().isoformat(),
                   timezone=datetime.now().astimezone().tzname(),
                   timezone_offset=datetime.now().astimezone().strftime('%z'))
    def preview_parents():
        # Public preview remains usable with no control directory. Creation is
        # manual-only and descriptor-relative; aliases still refuse before write.
        for component in reversed((summary.parent, *summary.parent.parents)):
            if component == component.parent:
                continue
            with directory(component.parent, deadline) as parent:
                try:
                    os.mkdir(component.name, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                with directory(component, deadline):
                    pass
    if dry_run:
        await asyncio.to_thread(preview_parents)
    await asyncio.to_thread(atomic_json, summary, initial)
    spawn = asyncio.create_task(asyncio.create_subprocess_exec(
        *argv, start_new_session=True, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    cause = None
    cancelled = False
    try:
        process = await asyncio.shield(spawn)
    except asyncio.CancelledError:
        # A cancellation during spawn still obtains and stops the real child;
        # never lose its handle while it might begin deletion.
        process = await spawn
        cause, cancelled = 'shutdown', True
    except Exception as exc:
        initial.update(error='worker_spawn:' + type(exc).__name__, stop_cause='error')
        await asyncio.to_thread(atomic_json, summary, initial)
        return initial
    try:
        if not cancelled:
            await asyncio.wait_for(process.wait(), max(.001, deadline - time.monotonic()))
        if process.returncode and process.returncode != WORKER_INCOMPLETE_EXIT:
            cause = f'worker_exit:{process.returncode}'
    except asyncio.TimeoutError:
        cause = 'deadline'
    except asyncio.CancelledError:
        cause, cancelled = 'shutdown', True
    finally:
        # Reconcile the entire group even if its leader exited first.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
    def finish():
        try:
            report = _read_json(summary)
        except Exception:
            report = dict(format='disk-sweep@1', orgs=[], unreached_orgs=roots, revision=revision, source_revision=revision)
        for item in report.get('orgs', []):
            _summarize_org(item)
        report.update(worker_pid=process.pid, worker_returncode=process.returncode, worker_group_stop='owned group signalled SIGKILL when present; leader reaped; uninterruptible kernel I/O remains an OS limitation')
        if cause:
            report['stop_cause'] = 'budget' if cause == 'deadline' else 'shutdown_signal' if cause == 'shutdown' else 'error'
            _interrupted_measurements(report, report['stop_cause'])
            report.update(outcome='incomplete', interruption=cause, partial_mutation='unknown', finished_at=datetime.now().astimezone().isoformat())
        report['elapsed_seconds'] = max(0, time.monotonic() - started_monotonic)
        _finalize_night(report)
        atomic_json(summary, report)
        return report
    report = await asyncio.to_thread(finish)
    if cancelled:
        error = asyncio.CancelledError()
        error.disk_sweep_report = report
        raise error
    return report


def _scheduled_admission(deadline: float) -> dict:
    """Observe the daemon-wide stop once, before any scheduled writer work.

    Only a missing literal file or the exact Boolean false allows admission.
    This is an ordinary bounded filesystem observation, not an in-flight fence.
    """
    from runtime.runtime import daemon_home
    path = daemon_home() / 'disk-sweep/stop.json'
    admission = dict(scope='daemon', path=str(path), state='suspended', reason='invalid_control')
    try:
        with directory(path.parent, deadline) as parent:
            parent_identity = identity(os.fstat(parent))
            try:
                before = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                # ENOENT on this final literal name only; parent failure is not
                # absence. Reopen parents to reject aliases/replacement.
                with directory(path.parent, deadline) as current:
                    if identity(os.fstat(current)) != parent_identity:
                        raise Retain('stop_parent_changed')
                check(deadline)
                return dict(admission, state='allowed', reason='absent')
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise Retain('stop_not_regular_or_hardlinked')
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                if signature(os.fstat(fd)) != signature(before):
                    raise Retain('stop_changed_before_read')
                raw = os.read(fd, 65537)
                if len(raw) > 65536 or len(raw) != before.st_size:
                    raise Retain('stop_oversized_or_short_read')
                if signature(os.fstat(fd)) != signature(before):
                    raise Retain('stop_changed_during_read')
                if signature(os.stat(path.name, dir_fd=parent, follow_symlinks=False)) != signature(before):
                    raise Retain('stop_replaced_during_read')
                with directory(path.parent, deadline) as current:
                    if identity(os.fstat(current)) != parent_identity:
                        raise Retain('stop_parent_changed')
                check(deadline)
            finally:
                os.close(fd)
        # Retain duplicate keys as pairs, so even duplicate identical false
        # values cannot silently pass the closed one-key object contract.
        pairs = json.loads(raw.decode('utf-8', errors='strict'), object_pairs_hook=lambda value: value)
        if (not isinstance(pairs, list) or len(pairs) != 1
                or not isinstance(pairs[0], tuple) or pairs[0][0] != 'stop'
                or type(pairs[0][1]) is not bool):
            raise Retain('stop_requires_only_boolean_stop')
        check(deadline)
        return dict(admission, state='suspended' if pairs[0][1] else 'allowed',
                    reason='stop_true' if pairs[0][1] else 'stop_false')
    except Exception as exc:
        return dict(admission, error=f'{type(exc).__name__}: {exc}')


async def _run_scheduled_worker(state, summary: Path, date: str) -> dict:
    from runtime.orchestrator.producer_move import native_writer
    from runtime.orchestrator.team_move import DeferredTeamMove
    started = time.monotonic()
    started_at = datetime.now().astimezone().isoformat()
    deadline = started + BUDGET_SECONDS
    admission = await asyncio.to_thread(_scheduled_admission, deadline)
    report = dict(format='disk-sweep@1', revision=_revision(), scheduled_date=date,
                  timezone=datetime.now().astimezone().tzname(), timezone_offset=datetime.now().astimezone().strftime('%z'),
                  started_at=started_at, deadline_monotonic=deadline, budget_seconds=BUDGET_SECONDS,
                  process_scope=PROCESS_SCOPE, outcome='incomplete', orgs=[],
                  unreached_orgs=[str(org.root) for _, org in sorted(state.orgs_snapshot().items())],
                  scheduled_admission=admission)
    report['source_revision'] = report['revision']
    if admission['state'] == 'suspended':
        report.update(outcome='suspended', stop_cause='stop_control', finished_at=datetime.now().astimezone().isoformat())
        LOG.warning('Disk sweep globally suspended: %s', admission)
    # Publish the actual initial summary before ANY durable native reservation.
    # An unsafe target/pending inode must refuse while all orgs are unstarted.
    # Worker checkpoints still publish independently; a later failure retains
    # genuine reservation uncertainty rather than claiming this check fences it.
    await asyncio.to_thread(atomic_json, summary, report)
    if admission['state'] == 'suspended':
        _finalize_night(report)
        await asyncio.to_thread(atomic_json, summary, report)
        return report
    # Reservations span filesystem/process work; their short gate/transaction
    # does not. Retain active evidence on process loss; D reads the same history.
    with ExitStack() as stack:
        roots, writers, dispositions, deferred = [], {}, {}, {}
        for slug, org in sorted(state.orgs_snapshot().items()):
            check(deadline)
            try:
                disposition = stack.enter_context(native_writer(org.db, publisher='disk_sweep'))
            except DeferredTeamMove as exc:
                deferred[slug] = exc.code
                continue
            root = str(org.root)
            roots.append(root)
            dispositions[slug] = disposition
            if disposition.writer_id is not None:
                writers[root] = disposition.writer_id
        # Any unexpected unwind, missing report or interrupted worker remains
        # unknown. Only affirmative complete native closure below may settle.
        for disposition in dispositions.values():
            disposition.uncertain = True
        check(deadline)
        report = await run_worker(roots, summary, dry_run=False, scheduled_date=date,
                                  native_writers=writers, budget=deadline - time.monotonic())
        report['scheduled_admission'] = admission
        if deferred:
            report.update(outcome='incomplete', move_deferred_orgs=deferred)
        report['elapsed_seconds'] = max(0, time.monotonic() - started)
        _finalize_night(report)
        await asyncio.to_thread(atomic_json, summary, report)
        for slug, disposition in dispositions.items():
            if _org_effects_known(report, slug):
                disposition.uncertain = False
        return report


def _org_effects_known(report: dict, slug: str) -> bool:
    """Whether this org's own sweep returned with every filesystem effect known.

    Orgs are swept sequentially in one worker, so a later org entry proves this
    org's sweep returned; otherwise only an uninterrupted worker exit does. One
    org's incomplete, failed or interrupted sweep never leaves another org's
    finished sweep unknown. Partial discovery and retained candidates are known
    effects; an unexpected error or any in-flight/partial mutation is not.
    """
    items = report.get('orgs', [])
    matches = [index for index, item in enumerate(items) if item.get('org') == slug]
    if len(matches) != 1 or not report.get('worker_group_stop'):
        return False
    index = matches[0]
    item = items[index]
    returned = index < len(items) - 1 or not report.get('interruption')
    return (returned
            and item.get('mode') == 'on'
            and item.get('outcome') in ('complete', 'incomplete')
            and 'error' not in item
            and not any(c.get('state') in _OPEN_MUTATION_STATES for c in item.get('candidates', [])))


def _revision() -> str:
    # Both SourceFileLoader and PyInstaller's PYZ loader expose executing code.
    # No loose sources, Git checkout, version placeholder or fallback identity.
    # Canonical filenames avoid install/build-directory-dependent revisions.
    def canonical(code: CodeType) -> CodeType:
        return code.replace(co_filename='<module>', co_consts=tuple(
            canonical(value) if isinstance(value, CodeType) else value for value in code.co_consts))

    digest = hashlib.sha256()
    for name in ('runtime.daemon.disk_sweeper', 'runtime.daemon._disk_sweep_fs',
                 'runtime.daemon.app', 'runtime.orchestrator.org_config'):
        spec = importlib.util.find_spec(name)
        loader = spec.loader if spec is not None else None
        get_code = getattr(loader, 'get_code', None)
        code = get_code(name) if get_code is not None else None
        if not isinstance(code, CodeType):
            raise RuntimeError('disk sweep revision unavailable')
        digest.update(name.encode('ascii') + b'\0')
        digest.update(marshal.dumps(canonical(code)))
    return 'code-sha256:' + digest.hexdigest()


def next_night(now: datetime) -> datetime:
    """Next future local 03:00; never startup catchup. OS local TZ, not org TZ."""
    local = now.astimezone()
    date = local.date() + timedelta(days=local.hour >= 3)
    # Naive local timestamp lets the OS resolve the date's DST offset.
    return datetime.combine(date, datetime.min.time()).replace(hour=3).astimezone()


def _record_previous_night(state, date: str) -> None:
    """Record only. Founder delivery awaits THR307's open surface choice.

    Single daemon scheduler, existing org audit envelope; committed rows are
    the restart/repeat deduplication authority. No marker reset or catch-up.
    A daemon that stays down cannot record anything.
    """
    from runtime.runtime import daemon_home
    summary = daemon_home() / 'disk-sweep' / (date + '.json')
    try:
        report = _read_json(summary)
        _finalize_night(report)
    except FileNotFoundError:
        # A missing parent is not evidence that just this date file is absent.
        try:
            with directory(summary.parent, float('inf')):
                pass
        except Exception as exc:
            payload = dict(scheduled_date=date, status='incomplete', reasons=['summary_parent_unavailable:' + str(exc)], summary_path=None)
        else:
            payload = dict(scheduled_date=date, status='missing', reasons=['date_summary_missing'], summary_path=None)
    except Exception as exc:
        payload = dict(scheduled_date=date, status='incomplete', reasons=['summary_unreadable:' + str(exc)], summary_path=str(summary))
    else:
        if report.get('scheduled_date') != date:
            report['night']['reasons'].append('scheduled_date_mismatch')
            report['night']['complete'] = False
        if report['night']['complete']:
            # A parent JSON publication can itself precede an interrupted audit
            # loop. Complete records require the matching existing org audits.
            audit_reasons = []
            for slug, org in sorted(state.orgs_snapshot().items()):
                item = next((o for o in report.get('orgs', []) if o.get('org') == slug), None)
                try:
                    expected = _audit_payload(report, item, summary)
                    rows = org.db.get_audit_logs_by_action('disk_sweep_summary')
                    if not any(row['task_id'] == '' and row['agent'] == 'daemon' and row['payload'] == expected for row in rows):
                        audit_reasons.append('summary_audit_missing_or_mismatched:' + slug)
                except Exception as exc:
                    audit_reasons.append('summary_audit_unavailable:' + slug + ':' + type(exc).__name__)
            if not audit_reasons:
                return
            report['night']['reasons'].extend(audit_reasons)
            report['night']['complete'] = False
        payload = dict(scheduled_date=date, status='incomplete', reasons=report['night']['reasons'], summary_path=str(summary))
    payload['delivery'] = 'record_only_founder_delivery_pending'
    for slug, org in sorted(state.orgs_snapshot().items()):
        try:
            rows = org.db.get_audit_logs_by_action('disk_sweep_night_notice')
            if any(row['task_id'] == '' and row['agent'] == 'daemon' and row['payload'].get('scheduled_date') == date for row in rows):
                continue
            org.db.insert_audit_log('', 'daemon', 'disk_sweep_night_notice', payload)
            LOG.warning('Disk sweep night record (delivery pending) org=%s: %s', slug, payload)
        except Exception:
            LOG.exception('Disk sweep night record unavailable org=%s date=%s', slug, date)


async def disk_sweeper_loop(state) -> None:
    from runtime.runtime import daemon_home
    now = datetime.now().astimezone()
    previous = now.date() - timedelta(days=now.hour < 3)
    try:
        await asyncio.to_thread(_record_previous_night, state, previous.isoformat())
    except Exception:
        LOG.exception('Disk sweep previous-night record unavailable at startup')
    while True:
        due = next_night(datetime.now().astimezone())
        await asyncio.sleep(max(0, due.timestamp() - time.time()))
        if state.runtime is None:
            continue
        try:
            await asyncio.to_thread(_record_previous_night, state, (due.date() - timedelta(days=1)).isoformat())
        except Exception:
            LOG.exception('Disk sweep previous-night record unavailable before scheduled attempt')
        data = daemon_home() / 'disk-sweep'
        marker = data / 'last-scheduled.json'
        date = due.date().isoformat()
        def reserve():
            # Mark BEFORE launching. An interrupted night's partial run is not
            # retried after restart. Corrupt metadata refuses, never duplicates.
            # Validate literal parents BEFORE interpreting final-file ENOENT.
            # No marker/control directory creation on the scheduled path.
            with directory(data, float('inf')):
                try:
                    old = _read_json(marker)
                except FileNotFoundError:
                    old = None
                else:
                    if not isinstance(old, dict):
                        raise ValueError('invalid disk sweep date metadata')
            if old is not None:
                if not isinstance(old.get('scheduled_date'), str):
                    raise ValueError('invalid disk sweep date metadata')
                if old['scheduled_date'] >= date:
                    return False
            atomic_json(marker, {'scheduled_date': date})
            return True
        try:
            if not await asyncio.to_thread(reserve):
                continue
            summary = data / f'{date}.json'
            cancelled = None
            try:
                report = await _run_scheduled_worker(state, summary, date)
            except asyncio.CancelledError as exc:
                report = getattr(exc, 'disk_sweep_report', None)
                if report is None:
                    raise
                cancelled = exc
            if state.broken_orgs:
                report.update(unloaded_orgs_unknown=sorted(state.broken_orgs))
                if report.get('outcome') != 'suspended':
                    report['outcome'] = 'incomplete'
                _finalize_night(report)
                await asyncio.to_thread(atomic_json, summary, report)
            # Existing audit interface and row shape, one summary per swept org.
            # No notification interface suitable for a disk alert exists here;
            # below-20% context is carried in this audit/JSON, as approved.
            for slug, org in sorted(state.orgs_snapshot().items()):
                item = next((r for r in report.get('orgs', []) if r['org'] == slug), None)
                try:
                    await asyncio.to_thread(org.db.insert_audit_log, '', 'daemon', 'disk_sweep_summary', _audit_payload(report, item, summary))
                except Exception as exc:
                    report.update(outcome='incomplete', error='summary_audit_failed:' + type(exc).__name__)
                    _finalize_night(report)
                    await asyncio.to_thread(atomic_json, summary, report)
                    raise
            if cancelled is not None:
                raise cancelled
        except asyncio.CancelledError:
            raise
        except Exception:
            LOG.exception('Disk sweep incomplete; no same-night automatic retry')


def _audit_payload(report: dict, item: dict | None, summary: Path) -> dict:
    return {
        'summary_path': str(summary), 'revision': report.get('revision'),
        'night': report.get('night'), 'policy': report.get('policy'), 'elapsed_seconds': report.get('elapsed_seconds'),
        'worker_group_stop': report.get('worker_group_stop'), 'worker_returncode': report.get('worker_returncode'),
        'previous_interrupted_removals': report.get('previous_interrupted_removals', []),
        'unloaded_orgs_unknown': report.get('unloaded_orgs_unknown'),
        'source_revision': report.get('source_revision'),
        'stop_cause': report.get('stop_cause'), 'measurement_history': report.get('measurement_history'),
        'scheduled_date': report.get('scheduled_date'),
        'timezone': report.get('timezone'), 'timezone_offset': report.get('timezone_offset'),
        'started_at': report.get('started_at'), 'finished_at': report.get('finished_at'),
        'budget_seconds': report.get('budget_seconds'), 'outcome': report.get('outcome'),
        'scheduled_admission': report.get('scheduled_admission'),
        'org': item, 'unreached_orgs': report.get('unreached_orgs'),
        'interruption': report.get('interruption'),
        'move_deferred_orgs': report.get('move_deferred_orgs'),
        'low_disk_below_20_percent': report.get('low_disk_below_20_percent'),
    }


def _manual_audit(report: dict, roots: list[str], summary: Path) -> None:
    """Reuse the existing writer without initializing/upgrading any schema."""
    import threading
    # Import defines the facade required by the unchanged writer decorator;
    # no Database instance, initializer, OrgState or schema installer is called.
    import runtime.infrastructure.database  # noqa: F401
    from runtime.infrastructure.db.audit import AuditMixin

    class SummaryWriter(AuditMixin):
        def __init__(self, conn):
            self._conn = conn
            self._lock = threading.RLock()

    for root in roots:
        item = next((o for o in report.get('orgs', []) if o['org'] == Path(root).name), None)
        conn = sqlite3.connect((Path(root) / 'happyranch.db').as_uri() + '?mode=rw', uri=True, timeout=.1)
        try:
            SummaryWriter(conn).insert_audit_log('', 'daemon', 'disk_sweep_summary', _audit_payload(report, item, summary))
        finally:
            conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--runtime-root', type=Path)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--org-root', action='append', default=[], help=argparse.SUPPRESS)
    parser.add_argument('--summary', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--deadline', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--revision', help=argparse.SUPPRESS)
    parser.add_argument('--started-monotonic', type=float, help=argparse.SUPPRESS)
    parser.add_argument('--scheduled-date', help=argparse.SUPPRESS)
    parser.add_argument('--native-writer', nargs=2, action='append', default=[], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        if args.summary is None or args.deadline is None:
            parser.error('worker requires deadline and summary')
        report = sweep(args.org_root, args.deadline, args.summary, dry_run=args.dry_run, scheduled_date=args.scheduled_date, revision=args.revision, started_monotonic=args.started_monotonic, runtime_root=args.runtime_root,
                       native_writers=dict(args.native_writer))
        return 0 if report.get('outcome') == 'complete' else WORKER_INCOMPLETE_EXIT
    else:
        if not args.dry_run or args.runtime_root is None:
            parser.error('manual use requires --dry-run --runtime-root ABSOLUTE_RUNTIME')
        from runtime.runtime import RuntimeDir, daemon_home
        runtime = RuntimeDir.load(args.runtime_root)
        summary = daemon_home() / 'disk-sweep' / f'manual-{time.time_ns()}.json'
        # Candidate/org enumeration itself belongs inside the shared budget.
        report = asyncio.run(run_worker([], summary, dry_run=True, runtime_root=runtime.root))
        _manual_audit(report, report.get('selected_org_roots', []), summary)
        print(json.dumps({'summary_path': str(summary), 'report': report}, indent=2))
    return 0 if report.get('outcome') == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
