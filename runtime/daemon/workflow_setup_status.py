"""Independent, read-only setup evidence. Never workflow admission authority."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from runtime.daemon._disk_sweep_fs import Expired, check, directory, signature
from runtime.daemon.org_integrity_monitor import latest_report, runtime_key
from runtime.daemon.job_fence_observation import JobFenceObservation, observe_job_fences

SetupState = Literal['not_installed', 'installed_unverified', 'installed_verified', 'unknown']
SetupReason = Literal['evidence_unavailable', 'registered', 'not_installed', 'install_incomplete',
                      'commit_uncertain', 'installation_unverified', 'default_load_budget_exceeded',
                      'default_load_qualification_unavailable', 'writer_exclusion_unproven',
                      'startup_upgrade_budget_unavailable', 'history_check_failed', 'integrity_check_failed',
                      'backup_verification_failed', 'registration_failed']


class Closed(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class IntegrityReporting(Closed):
    external: Literal['persisted', 'unavailable', 'pending']
    audit: Literal['persisted', 'unavailable', 'pending']


class IntegrityReport(Closed):
    outcome: Literal['ok', 'failed', 'unavailable']
    reason: Literal['full_integrity_ok', 'full_integrity_not_ok', 'database_corrupt', 'busy', 'deadline', 'sql_budget',
                    'open_failed', 'io', 'identity_changed', 'unattached_unreadable', 'interrupted',
                    'in_flight_reader', 'report_unavailable']
    checked_at: str | None
    scheduled_date: str | None
    reporting: IntegrityReporting


class WorkflowSetupStatus(Closed):
    format: Literal['workflow-setup-status@1'] = 'workflow-setup-status@1'
    org_slug: str
    state: SetupState = 'unknown'
    reason: SetupReason = 'evidence_unavailable'
    attempt_time: str | None = None
    backup_path: str | None = None
    integrity_report: IntegrityReport | None = None
    writer_blockers: JobFenceObservation | None = None


def _pairs(items: list[tuple]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('duplicate_evidence')
        result[key] = value
    return result


def _record(parent: int, name: str, deadline: float) -> tuple[dict, str, int]:
    check(deadline)
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) != 0o600 or before.st_size > 65536):
            raise ValueError('unsafe_evidence')
        raw = stream.read(65537)
        if (len(raw) > 65536 or signature(before) != signature(os.fstat(stream.fileno()))
                or signature(before) != signature(os.stat(name, dir_fd=parent, follow_symlinks=False))):
            raise ValueError('changed_evidence')
    value = json.loads(raw, object_pairs_hook=_pairs)
    if type(value) is not dict:
        raise ValueError('invalid_evidence')
    check(deadline)
    return value, hashlib.sha256(raw).hexdigest(), len(raw)


def _utc(value: object) -> str:
    if type(value) is not str or len(value) > 40:
        raise ValueError('invalid_time')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise ValueError('invalid_time')
    return value


def _names(parent: int, limit: int, deadline: float) -> list[str]:
    result = []
    with os.scandir(parent) as entries:
        for entry in entries:
            check(deadline)
            if len(result) >= limit:
                raise ValueError('evidence_namespace_exceeded')
            result.append(entry.name)
    return sorted(result)


def _attempt_record(home: Path, runtime_root: Path, key: str, slug: str, deadline: float,
                    *, directory_reader: Callable | None = None) -> dict:
    """Bounded complete namespace/chain read; missing evidence proves no state.

    A4 owns publication. Unrecognized or unfinished evidence stays unknown.
    No backup or org database is opened, and no selected prefix is accepted.
    Startup supplies its original-custody directory reader; advisory status
    retains its independent traversal.
    """
    open_directory = directory if directory_reader is None else directory_reader
    root = home / 'backups' / 'workflow-sequential' / key / slug
    total = 0
    outcomes = []
    hold = None
    with open_directory(root, deadline) as namespace:
        names = _names(namespace, 64, deadline)
        if 'hold.json' in names:
            value, _, size = _record(namespace, 'hold.json', deadline)
            total += size
            old_hold = value.get('format') == 'workflow-setup-hold@1'
            if (set(value) != {'format', 'runtime_key', 'org_slug', 'reason', 'state', 'attempt_time'} | (set() if old_hold else {'predecessor'})
                    or value['format'] not in ('workflow-setup-hold@1', 'workflow-setup-hold@2')
                    or (value['runtime_key'], value['org_slug']) != (key, slug)
                    or value['state'] not in (('unknown', 'not_installed') if old_hold else ('unknown', 'installed_unverified'))
                    or value['reason'] not in ('writer_exclusion_unproven', 'startup_upgrade_budget_unavailable',
                        'history_check_failed', 'integrity_check_failed', 'backup_verification_failed',
                        'registration_failed', 'install_incomplete', 'default_load_budget_exceeded', 'commit_uncertain')
                    or _names(namespace, 64, deadline) != names):
                raise ValueError('attempt_hold_invalid')
            hold = dict(value, phase='held', attempt_time=_utc(value['attempt_time']), backup_path=None)
            if old_hold:
                if names != ['hold.json']:
                    raise ValueError('attempt_hold_invalid')
                return hold
            if names == ['hold.json'] and hold['predecessor'] is None:
                return hold
        if not 0 < len(names) <= 64:
            raise ValueError('attempt_namespace_unavailable')
        for name in sorted(set(names) - {'hold.json'}):
            check(deadline)
            if not re.fullmatch(r'[a-zA-Z0-9-]{1,128}', name):
                raise ValueError('attempt_identity_invalid')
            with open_directory(root / name, deadline) as parent:
                entries = _names(parent, 17, deadline)
                records = sorted(item for item in entries if re.fullmatch(r'[0-9]{2}\.json', item))
                if set(entries) - set(records) - {'before.db'} or not 0 < len(records) <= 16:
                    raise ValueError('attempt_records_unavailable')
                previous = None
                last = None
                for sequence, filename in enumerate(records):
                    if filename != f'{sequence:02}.json':
                        raise ValueError('attempt_sequence_invalid')
                    value, digest, size = _record(parent, filename, deadline)
                    total += size
                    if total > 4 * 1024 * 1024:
                        raise ValueError('attempt_bytes_exceeded')
                    old_event = value.get('format') == 'workflow-setup-event@1'
                    if (set(value) != {'format', 'runtime_key', 'org_slug', 'attempt_id', 'sequence',
                            'previous_digest', 'phase', 'state', 'reason', 'attempt_time', 'backup_path',
                            'db_identity', 'source_inventory_digest', 'backup_sha256', 'target_version'} | (set() if old_event else {'predecessor'})
                            or value['format'] not in ('workflow-setup-event@1', 'workflow-setup-event@2')
                            or (value['runtime_key'], value['org_slug'], value['attempt_id']) != (key, slug, name)
                            or type(value['sequence']) is not int or value['sequence'] != sequence
                            or value['previous_digest'] != previous):
                        raise ValueError('attempt_chain_invalid')
                    db_identity = value['db_identity']
                    if (type(db_identity) is not dict or set(db_identity) != {'path', 'device', 'inode'}
                            or db_identity['path'] != str(runtime_root / 'orgs' / slug / 'happyranch.db')
                            or any(type(db_identity[field]) is not int or db_identity[field] < 0 for field in ('device', 'inode'))
                            or type(value['target_version']) is not int or value['target_version'] != (1 if old_event else 2)
                            or type(value['source_inventory_digest']) is not str
                            or not re.fullmatch(r'[0-9a-f]{64}', value['source_inventory_digest'])
                            or (value['backup_sha256'] is not None and (type(value['backup_sha256']) is not str
                                or not re.fullmatch(r'[0-9a-f]{64}', value['backup_sha256'])))):
                        raise ValueError('attempt_source_invalid')
                    _utc(value['attempt_time'])
                    expected_backup = str(root / name / 'before.db')
                    if value['backup_path'] not in (None, expected_backup):
                        raise ValueError('attempt_backup_invalid')
                    phase_state = {'intent': ('unknown', 'install_incomplete'),
                        'backup_verified': ('unknown', 'install_incomplete'),
                        'pre_commit': ('unknown', 'commit_uncertain'),
                        'committed': ('installed_unverified', 'installation_unverified'),
                        'verified': ('installed_unverified', 'installation_unverified'),
                        'registered': ('installed_verified', 'registered'),
                        'not_installed': ('not_installed', 'not_installed'),
                        'not_upgraded': ('installed_unverified', 'installation_unverified')}
                    if phase_state.get(value['phase']) != (value['state'], value['reason']):
                        raise ValueError('attempt_state_invalid')
                    phases = ('intent', 'backup_verified', 'pre_commit', 'committed', 'verified', 'registered')
                    if old_event and value['phase'] == 'not_upgraded':
                        raise ValueError('attempt_phase_invalid')
                    if value['phase'] not in ('not_installed', 'not_upgraded') and (sequence >= len(phases) or value['phase'] != phases[sequence]):
                        raise ValueError('attempt_phase_invalid')
                    if value['phase'] not in ('intent', 'not_installed', 'not_upgraded') and (value['backup_path'] is None or value['backup_sha256'] is None):
                        raise ValueError('attempt_backup_unproven')
                    if value['phase'] in ('not_installed', 'not_upgraded') and (last is None or last['phase'] not in ('intent', 'backup_verified', 'pre_commit')):
                        raise ValueError('attempt_absence_unproven')
                    if last is not None and (_utc(value['attempt_time']) != last['attempt_time']
                            or last['phase'] in ('registered', 'not_installed', 'not_upgraded')
                            or any(value[field] != last[field] for field in
                                ('format', 'db_identity', 'source_inventory_digest', 'target_version', 'backup_path'))
                            or value.get('predecessor') != last.get('predecessor')
                            or last['backup_sha256'] is not None and value['backup_sha256'] != last['backup_sha256']):
                        raise ValueError('attempt_terminal_invalid')
                    previous, last = digest, value
                # A status path is only disclosed for a safe literal backup.
                if last['backup_path'] is not None:
                    info = os.stat('before.db', dir_fd=parent, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                        raise ValueError('attempt_backup_unsafe')
                outcomes.append(dict(last, record_digest=previous))
                if _names(parent, 17, deadline) != entries:
                    raise ValueError('attempt_changed')
        if _names(namespace, 64, deadline) != names:
            raise ValueError('attempt_namespace_changed')
    check(deadline)
    # Only one explicitly linked 1 -> 2 transition is supported. Never select
    # attempts by time/name, discard an unfinished attempt, or admit a retry.
    if not 1 <= len(outcomes) <= 2:
        raise ValueError('attempt_selection_unavailable')
    outcomes.sort(key=lambda value: value['target_version'])
    first, current = outcomes[0], outcomes[-1]
    predecessor = None
    if len(outcomes) == 2:
        if (first['target_version'] != 1 or first['phase'] != 'registered'
                or current['target_version'] != 2 or first['db_identity'] != current['db_identity']):
            raise ValueError('attempt_selection_unavailable')
        predecessor = dict(attempt_id=first['attempt_id'], digest=first['record_digest'])
    if current.get('predecessor') != predecessor:
        raise ValueError('attempt_predecessor_invalid')
    if hold is not None:
        if (len(outcomes) != 1 or current['target_version'] != 1 or current['phase'] != 'registered'
                or hold['predecessor'] != dict(attempt_id=current['attempt_id'], digest=current['record_digest'])):
            raise ValueError('attempt_hold_invalid')
        return hold
    return current


def _attempt(home: Path, runtime_root: Path, key: str, slug: str, deadline: float) -> dict:
    value = _attempt_record(home, runtime_root, key, slug, deadline)
    projected = {field: value[field] for field in ('state', 'reason', 'attempt_time', 'backup_path')}
    if value.get('target_version') == 1 and value['phase'] == 'registered':
        projected.update(state='installed_unverified', reason='installation_unverified')
    return projected


def read_setup_status(home: Path, runtime_root: Path, slug: str,
                      *, job_ids: list[str] | None = None) -> WorkflowSetupStatus:
    deadline = time.monotonic() + 1
    key = runtime_key(runtime_root)
    try:
        attempt = _attempt(home, runtime_root, key, slug, deadline)
    except (OSError, ValueError, TimeoutError, Expired, TypeError, KeyError, RecursionError):
        attempt = {}
    # Independent acquisition: bad/slow attempt evidence cannot hide daily data.
    daily = latest_report(home, runtime_root, slug)
    blockers = (observe_job_fences(home, runtime_root / 'orgs' / slug, job_ids)
                if attempt.get('reason') == 'writer_exclusion_unproven' else None)
    return WorkflowSetupStatus(org_slug=slug, **attempt, integrity_report=daily, writer_blockers=blockers)
