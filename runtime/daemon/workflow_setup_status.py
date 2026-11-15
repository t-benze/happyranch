"""Independent, read-only setup evidence. Never workflow admission authority."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from runtime.daemon._disk_sweep_fs import Expired, check, directory, signature
from runtime.daemon.org_integrity_monitor import latest_report, runtime_key

SetupState = Literal['not_installed', 'installed_unverified', 'installed_verified', 'unknown']
SetupReason = Literal['evidence_unavailable', 'registered', 'not_installed', 'install_incomplete',
                      'commit_uncertain', 'installation_unverified']


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


def _attempt(home: Path, runtime_root: Path, key: str, slug: str, deadline: float) -> dict:
    """Bounded complete namespace/chain read; missing evidence proves no state.

    A4 owns publication. Unrecognized or unfinished evidence stays unknown.
    No backup or org database is opened, and no selected prefix is accepted.
    """
    root = home / 'backups' / 'workflow-sequential' / key / slug
    total = 0
    outcomes = []
    with directory(root, deadline) as namespace:
        names = _names(namespace, 64, deadline)
        if not 0 < len(names) <= 64:
            raise ValueError('attempt_namespace_unavailable')
        for name in sorted(names):
            check(deadline)
            if not re.fullmatch(r'[a-zA-Z0-9-]{1,128}', name):
                raise ValueError('attempt_identity_invalid')
            with directory(root / name, deadline) as parent:
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
                    if (set(value) != {'format', 'runtime_key', 'org_slug', 'attempt_id', 'sequence',
                            'previous_digest', 'phase', 'state', 'reason', 'attempt_time', 'backup_path',
                            'db_identity', 'source_inventory_digest', 'backup_sha256', 'target_version'}
                            or value['format'] != 'workflow-setup-event@1'
                            or (value['runtime_key'], value['org_slug'], value['attempt_id']) != (key, slug, name)
                            or type(value['sequence']) is not int or value['sequence'] != sequence
                            or value['previous_digest'] != previous):
                        raise ValueError('attempt_chain_invalid')
                    db_identity = value['db_identity']
                    if (type(db_identity) is not dict or set(db_identity) != {'path', 'device', 'inode'}
                            or db_identity['path'] != str(runtime_root / 'orgs' / slug / 'happyranch.db')
                            or any(type(db_identity[field]) is not int or db_identity[field] < 0 for field in ('device', 'inode'))
                            or type(value['target_version']) is not int or value['target_version'] != 1
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
                        'not_installed': ('not_installed', 'not_installed')}
                    if phase_state.get(value['phase']) != (value['state'], value['reason']):
                        raise ValueError('attempt_state_invalid')
                    phases = ('intent', 'backup_verified', 'pre_commit', 'committed', 'verified', 'registered')
                    if value['phase'] != 'not_installed' and (sequence >= len(phases) or value['phase'] != phases[sequence]):
                        raise ValueError('attempt_phase_invalid')
                    if value['phase'] not in ('intent', 'not_installed') and (value['backup_path'] is None or value['backup_sha256'] is None):
                        raise ValueError('attempt_backup_unproven')
                    if value['phase'] == 'not_installed' and (last is None or last['phase'] not in ('intent', 'backup_verified', 'pre_commit')):
                        raise ValueError('attempt_absence_unproven')
                    if last is not None and (_utc(value['attempt_time']) != last['attempt_time']
                            or last['phase'] in ('registered', 'not_installed')
                            or any(value[field] != last[field] for field in
                                ('db_identity', 'source_inventory_digest', 'target_version', 'backup_path'))
                            or last['backup_sha256'] is not None and value['backup_sha256'] != last['backup_sha256']):
                        raise ValueError('attempt_terminal_invalid')
                    previous, last = digest, value
                # A status path is only disclosed for a safe literal backup.
                if last['backup_path'] is not None:
                    info = os.stat('before.db', dir_fd=parent, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600:
                        raise ValueError('attempt_backup_unsafe')
                outcomes.append(last)
                if _names(parent, 17, deadline) != entries:
                    raise ValueError('attempt_changed')
        if _names(namespace, 64, deadline) != names:
            raise ValueError('attempt_namespace_changed')
    check(deadline)
    # Multiple attempts require the future bootstrap owner's reconciliation;
    # timestamps alone do not select an authoritative attempt.
    if len(outcomes) != 1:
        raise ValueError('attempt_selection_unavailable')
    return {key: outcomes[0][key] for key in ('state', 'reason', 'attempt_time', 'backup_path')}


def read_setup_status(home: Path, runtime_root: Path, slug: str) -> WorkflowSetupStatus:
    deadline = time.monotonic() + 1
    key = runtime_key(runtime_root)
    try:
        attempt = _attempt(home, runtime_root, key, slug, deadline)
    except (OSError, ValueError, TimeoutError, Expired, TypeError, KeyError, RecursionError):
        attempt = {}
    # Independent acquisition: bad/slow attempt evidence cannot hide daily data.
    daily = latest_report(home, runtime_root, slug)
    return WorkflowSetupStatus(org_slug=slug, **attempt, integrity_report=daily)
