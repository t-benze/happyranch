#!/usr/bin/env python3
"""Prepare one frozen cleanup manifest from native receipts; never act or submit."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

# Packaged source is read-only evidence, including during sibling imports.
sys.dont_write_bytecode = True

TERMINAL = {'completed', 'failed', 'cancelled', 'superseded'}
MARKERS = {
    'HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)',
    'HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (daemon-triggered)',
}
CAP = 40


def aware_instant(value: Any) -> dt.datetime:
    if (not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-]\d\d:\d\d)', value)):
        raise ValueError('missing_malformed_or_naive_time')
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('naive_time')
    return parsed.astimezone(dt.timezone.utc)


def retention_eligible(record: dict[str, Any], floor_seconds: int,
                       observed_at: dt.datetime) -> bool:
    if type(floor_seconds) is not int or floor_seconds not in (86400, 604800):
        raise ValueError('unsupported_retention_floor')
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError('naive_observation')
    completed = aware_instant(record.get('completed_at'))
    return (record.get('status') in TERMINAL
            and observed_at - completed >= dt.timedelta(seconds=floor_seconds))


def same_task_snapshot(listed: dict[str, Any], fresh: dict[str, Any]) -> bool:
    return (all(isinstance(listed.get(key), str) and listed[key] == fresh.get(key)
                for key in ('task_id', 'assigned_agent', 'status'))
            and aware_instant(listed.get('completed_at'))
            == aware_instant(fresh.get('completed_at')))


def read_json(path: Path) -> Any:
    data = path.read_bytes()
    if len(data) > 16 * 1024 * 1024:
        raise ValueError('input_cap')
    return json.loads(data)


def write_json(path: Path, value: Any) -> None:
    # Only the canonical name is consumable. Keep the one-attempt staging
    # sibling on failure (and success), so correctness never depends on unlink.
    # Check before staging; link below also refuses a concurrently occupied name.
    if path.exists() or path.is_symlink():
        raise FileExistsError(str(path))
    pending = path.with_name('.' + path.name + '.pending')
    with pending.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    # Close must succeed before no-clobber publication. Nothing fallible follows.
    os.link(pending, path)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def beneath(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def successful_receipts(value: Any) -> bool:
    return (isinstance(value, list) and bool(value) and all(
        isinstance(row, dict) and isinstance(row.get('argv'), list)
        and bool(row['argv']) and all(isinstance(arg, str) for arg in row['argv'])
        and isinstance(row.get('cwd'), str) and row['cwd'].startswith('/')
        and type(row.get('exit_code')) is int and row['exit_code'] == 0
        and isinstance(row.get('stdout'), str) and isinstance(row.get('stderr'), str)
        and row.get('error') is None for row in value))


def binding() -> dict[str, str]:
    keys = ('WORKSPACE', 'PRIMARY', 'AGENT', 'ORG', 'ACTING_TASK', 'SESSION_ID', 'CLEANUP_MARKER')
    result = {key: os.environ.get(key, '') for key in keys}
    if not all(result[key] for key in keys if key != 'PRIMARY') or result['CLEANUP_MARKER'] not in MARKERS:
        raise ValueError('scope_or_marker_unset')
    workspace, primary = Path(result['WORKSPACE']), Path(result['PRIMARY'])
    if (not workspace.is_absolute() or workspace.resolve() != workspace
            or not workspace.is_dir()
            or (result['PRIMARY'] and (primary.resolve() != primary
                or primary.parent != workspace / 'repos' or not primary.is_dir()))):
        raise ValueError('workspace_primary_boundary')
    return result


def history_input(path: Path) -> tuple[dict[str, Any], list[Path]]:
    value = read_json(path)
    if (not isinstance(value, dict) or set(value) != {'complete', 'sources', 'excluded_paths'}
            or value['complete'] is not True or not isinstance(value['sources'], list)
            or not value['sources'] or not isinstance(value['excluded_paths'], list)):
        raise ValueError('history_incomplete')
    for source in value['sources']:
        if (not isinstance(source, dict) or set(source) != {'path', 'sha256'}
                or not Path(source['path']).is_absolute()
                or digest(Path(source['path'])) != source['sha256']):
            raise ValueError('history_source_changed')
    paths = [Path(item) for item in value['excluded_paths']]
    if any(not item.is_absolute() or item.resolve() != item for item in paths):
        raise ValueError('history_path_unknown')
    return value, paths


def validate_inventory(value: Any, bound: dict[str, str]) -> None:
    if (not isinstance(value, dict) or value.get('complete') is not True
            or value.get('binding') != bound or not isinstance(value.get('candidates'), list)
            or value.get('repositories_state') not in ('present', 'absent')):
        raise ValueError('inventory_scope_incomplete')
    for group in ('repository_receipts', 'assigned_task_receipts', 'trigger_history_receipts'):
        if not successful_receipts(value.get(group)):
            raise ValueError(group + '_incomplete')
    if value['repositories_state'] == 'present' and not bound['PRIMARY']:
        raise ValueError('present_repository_without_primary')
    if value['repositories_state'] == 'absent' and value['candidates']:
        raise ValueError('absent_repositories_with_candidates')


def fresh_task(task_id: str, bound: dict[str, str], cwd: Path) -> tuple[Any, dict[str, Any]]:
    from observe_cleanup_batch import capture_command
    receipt = capture_command(['happyranch', 'recall', '--org', bound['ORG'], task_id], cwd)
    if not successful_receipts([receipt]):
        raise ValueError('fresh_recall_failed:' + json.dumps(receipt, sort_keys=True))
    return json.loads(receipt['stdout']), receipt


def current_owner(bound: dict[str, str], cwd: Path) -> dict[str, Any]:
    record, receipt = fresh_task(bound['ACTING_TASK'], bound, cwd)
    if (record.get('task_id') != bound['ACTING_TASK'] or record.get('assigned_agent') != bound['AGENT']
            or record.get('status') != 'in_progress'
            or not isinstance(record.get('brief'), str)
            or record['brief'].splitlines()[0] != bound['CLEANUP_MARKER']):
        raise ValueError('current_owner_or_marker_changed')
    # Session authority is still authenticated by the supported jobs service;
    # recall alone does not prove an ACTIVE session.
    return receipt


def prepare(inventory_path: Path, history_path: Path, output_dir: Path,
            max_candidates: int = CAP) -> dict[str, Any]:
    if type(max_candidates) is not int or not 1 <= max_candidates <= CAP:
        raise ValueError('cap_outside_1_40')
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise ValueError('evidence_directory_not_empty')
    report: dict[str, Any] = {'state': 'incomplete', 'errors': [], 'rows': [], 'receipts': [],
                              'manifest': [], 'frozen_selection': [], 'no_refill': True}
    try:
        bound = binding()
        inventory = read_json(inventory_path)
        write_json(output_dir / 'inventory.json', inventory)
        validate_inventory(inventory, bound)
        history, exclusions = history_input(history_path)
        report.update(binding=bound, inventory_path=str(inventory_path), inventory_sha256=digest(inventory_path),
                      inventory_snapshot_sha256=digest(output_dir / 'inventory.json'),
                      history_path=str(history_path), history_sha256=digest(history_path),
                      history_sources=history['sources'], observed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        report['receipts'].append(current_owner(bound, Path(bound['WORKSPACE'])))
        observed_at = aware_instant(report['observed_at'])
        candidates = []
        seen: set[str] = set()
        uncertain = False
        for row in inventory['candidates']:
            reason = None
            if not isinstance(row, dict):
                raise ValueError('candidate_schema_unknown')
            candidate, containing = Path(row['candidate']), Path(row['containing'])
            listed = row.get('listing', {})
            task_id = containing.name
            primary = containing.parent.parent.parent
            if (not candidate.is_absolute() or candidate.resolve() != candidate
                    or containing.resolve() != containing or not task_id.startswith('TASK-')
                    or primary.parent != Path(bound['WORKSPACE']) / 'repos'
                    or containing != primary / '.claude/worktrees' / task_id
                    or primary != Path(bound['PRIMARY'])
                    or row.get('kind') not in ('worktree', 'cache')
                    or (row['kind'] == 'worktree' and candidate != containing)
                    or (row['kind'] == 'cache' and (candidate.parent != containing
                        or candidate.name not in ('.venv', 'node_modules')))
                    or str(candidate) in seen):
                raise ValueError('candidate_scope_unknown')
            seen.add(str(candidate))
            if any(beneath(candidate, old) or beneath(old, candidate)
                   or beneath(containing, old) or beneath(old, containing) for old in exclusions):
                reason = 'historical_overlap'
            elif listed.get('assigned_agent') != bound['AGENT']:
                reason = 'foreign_or_unknown_owner'
                uncertain |= not isinstance(listed.get('assigned_agent'), str)
            else:
                try:
                    if listed.get('task_id') != task_id:
                        raise ValueError('owner_task_mapping_unknown')
                    floor = 604800 if row['kind'] == 'worktree' else 86400
                    if not retention_eligible(listed, floor, observed_at):
                        reason = 'young_or_nonterminal'
                    else:
                        candidates.append((aware_instant(listed['completed_at']), str(candidate), row))
                except (ValueError, TypeError) as exc:
                    uncertain = True
                    reason = str(exc)
            report['rows'].append({'candidate': str(candidate), 'listing': listed, 'reason': reason})
        selected = sorted(candidates, key=lambda item: (item[2]['kind'] != 'worktree', item[0], item[1]))[:max_candidates]
        report['frozen_selection'] = [item[1] for item in selected]
        for _, candidate, row in selected:
            entry = next(item for item in report['rows'] if item['candidate'] == candidate)
            try:
                fresh, receipt = fresh_task(row['listing']['task_id'], bound, Path(bound['WORKSPACE']))
                report['receipts'].append(receipt)
                entry['fresh'] = fresh
                if not same_task_snapshot(row['listing'], fresh):
                    raise ValueError('task_snapshot_changed')
                if not retention_eligible(fresh, 604800 if row['kind'] == 'worktree' else 86400, observed_at):
                    raise ValueError('fresh_retention_refused')
                if (type(row.get('allocated_bytes')) is not int or row['allocated_bytes'] < 0
                        or not successful_receipts(row.get('measurement_receipts'))):
                    raise ValueError('measurement_incomplete')
                report['manifest'].append({key: row[key] for key in ('candidate', 'containing', 'kind', 'allocated_bytes')})
                entry['reason'] = 'prepared'
            except (OSError, ValueError, TypeError, KeyError) as exc:
                entry['reason'] = str(exc)
                uncertain = True
        report['state'] = ('prepared_nonempty' if report['manifest'] else
                           'incomplete' if uncertain else 'no_candidates_complete')
        if uncertain:
            report['errors'].append('candidate_uncertainty_retained_no_refill')
    except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
        report['state'] = 'incomplete'
        report['manifest'] = []
        report['errors'].append(str(exc))
    write_json(output_dir / 'manifest.json', report['manifest'])
    report['manifest_path'] = str((output_dir / 'manifest.json').resolve())
    report['manifest_sha256'] = digest(output_dir / 'manifest.json')
    write_json(output_dir / 'preparation.json', report)
    return report


# Local task-output evidence only. These records grant no cleanup authority.
EVIDENCE_CAP = 16 * 1024 * 1024
PACKAGE_NAMES = ('../SKILL.md', 'prepare_cleanup.py', 'run_cleanup_candidate.sh',
                 'run_cleanup_batch.py', 'observe_cleanup_batch.py',
                 'check_path_use.py', 'worktree_safety.py')


def package_identity() -> dict[str, str]:
    scripts = Path(__file__).resolve().parent
    return {name: digest(scripts / name) for name in PACKAGE_NAMES}


def same_evidence(first: Any, second: Any) -> bool:
    # Python equality equates bool/int and int/float; closed JSON evidence must not.
    return json.dumps(first, sort_keys=True, separators=(',', ':')) == json.dumps(
        second, sort_keys=True, separators=(',', ':'))


def canonical_record(path: Path) -> Any:
    if path.is_symlink() or not path.is_file():
        raise ValueError('canonical_record_missing:' + str(path))
    value = read_json(path)
    pending = path.with_name('.' + path.name + '.pending')
    if pending.exists() or pending.is_symlink():
        if pending.is_symlink() or pending.read_bytes() != path.read_bytes():
            raise ValueError('conflicting_pending:' + str(path))
    return value


def evidence_context(manifest: Path, history: Path, journal: Path,
                     root: Path) -> dict[str, Any]:
    bound = binding()
    for path in (manifest, history, journal, root):
        if not path.is_absolute() or path.resolve() != path:
            raise ValueError('evidence_path_not_literal')
    if not beneath(root, Path(bound['WORKSPACE']) / 'output' / bound['ACTING_TASK']):
        raise ValueError('evidence_sink_outside_current_output')
    history_value, _ = history_input(history)
    return {'binding': bound, 'manifest_path': str(manifest),
            'manifest_sha256': digest(manifest), 'history_path': str(history),
            'history_sha256': digest(history), 'history_sources': history_value['sources'],
            'journal_path': str(journal), 'evidence_root': str(root),
            'package_sha256': package_identity()}


def create_attempt(context: dict[str, Any], item: dict[str, Any], ordinal: int,
                   argv: list[str]) -> Path:
    root = Path(context['evidence_root']) / 'attempts'
    root.mkdir(exist_ok=True)
    path = root / str(ordinal)
    path.mkdir()  # An existing namespace, including pending alone, never retries.
    write_json(path / 'attempt.json', {'version': 1, 'context': context,
               'ordinal': ordinal, 'item': item, 'argv': argv})
    canonical_record(path / 'attempt.json')
    return path


def validate_attempt(path: Path, context: dict[str, Any] | None = None) -> dict[str, Any]:
    value = canonical_record(path / 'attempt.json')
    if not isinstance(value, dict) or set(value) != {'version', 'context', 'ordinal', 'item', 'argv'}:
        raise ValueError('attempt_schema')
    if type(value['version']) is not int or value['version'] != 1 or type(value['ordinal']) is not int or value['ordinal'] < 1:
        raise ValueError('attempt_ordinal')
    held = value['context']
    fresh = evidence_context(Path(held['manifest_path']), Path(held['history_path']),
                             Path(held['journal_path']), Path(held['evidence_root']))
    if not same_evidence(held, fresh) or (context is not None and not same_evidence(held, context)):
        raise ValueError('attempt_binding_drift')
    expected = Path(held['evidence_root']) / 'attempts' / str(value['ordinal'])
    if path != expected or path.resolve() != path:
        raise ValueError('attempt_path_drift')
    if value['argv'] != ['bash', str(Path(__file__).with_name('run_cleanup_candidate.sh').resolve()),
                          value['item']['candidate'], value['item']['containing']]:
        raise ValueError('attempt_argv')
    return value


def cache_index(containing: Path) -> dict[str, Any]:
    import subprocess
    import stat
    import base64
    result = subprocess.run(['git', '-C', str(containing), 'rev-parse', '--git-path', 'index'],
                            capture_output=True, text=True, env={**os.environ, 'GIT_OPTIONAL_LOCKS': '0'})
    if result.returncode or result.stderr or len(result.stdout.splitlines()) != 1:
        raise ValueError('cache_index_path_unavailable')
    index = Path(result.stdout.strip())
    if not index.is_absolute():
        index = containing / index
    index = index.absolute()
    paths = [index, *sorted(index.parent.glob('sharedindex.*'))]
    if len(paths) > 64:
        raise ValueError('cache_shared_index_cap')
    rows = []
    total = 0
    for path in paths:
        st = path.lstat()
        if not stat.S_ISREG(st.st_mode) or path.is_symlink():
            raise ValueError('cache_index_type')
        total += st.st_size
        if total > EVIDENCE_CAP:
            raise ValueError('cache_index_cap')
        data = path.read_bytes()
        after = path.lstat()
        identity = lambda x: [x.st_dev, x.st_ino, x.st_mode, x.st_uid, x.st_gid, x.st_size, x.st_mtime_ns]
        if identity(st) != identity(after) or len(data) != st.st_size:
            raise ValueError('cache_index_changed_during_read')
        # Git may touch a split shared-index mtime even with optional locks off.
        # Keep it in the individual read bracket, not stable inode/byte identity.
        rows.append({'path': str(path), 'identity': identity(st)[:-1],
                     'sha256': hashlib.sha256(data).hexdigest(),
                     'bytes_base64': base64.b64encode(data).decode('ascii')})
    return {'path_receipt': {'argv': result.args, 'exit_code': result.returncode,
                            'stdout': result.stdout, 'stderr': result.stderr}, 'files': rows}


def native_inputs(scratch: Path) -> list[dict[str, str]]:
    import base64
    entries = []
    total = 0
    # Keep the complete native files; their encoded bytes are evidence, not references
    # replacing an unavailable receipt. Each gate pass has its own archive.
    paths = sorted(scratch.rglob('*'))
    if len(paths) > 4096:
        raise ValueError('gate_evidence_entry_cap')
    for path in paths:
        if path.is_symlink():
            raise ValueError('gate_evidence_symlink')
        if path.is_dir():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError('gate_evidence_type')
        data = path.read_bytes()
        total += len(data)
        if total > EVIDENCE_CAP // 2:
            raise ValueError('gate_evidence_cap')
        entries.append({'name': str(path.relative_to(scratch)), 'sha256': hashlib.sha256(data).hexdigest(),
                        'bytes_base64': base64.b64encode(data).decode('ascii')})
    return entries


def capture_failure(value: Any) -> str | None:
    required = {'argv', 'cwd', 'started_at', 'ended_at', 'exit_code', 'stdout', 'stderr', 'error'}
    if (not isinstance(value, dict) or not required <= set(value)
            or not isinstance(value['argv'], list) or not value['argv']
            or any(not isinstance(arg, str) for arg in value['argv'])
            or any(not isinstance(value[key], str) for key in ('cwd', 'started_at', 'ended_at', 'stdout', 'stderr'))):
        return 'capture_receipt_incomplete'
    if value['error'] is not None:
        return 'capture_error:' + str(value['error'])
    if type(value['exit_code']) is not int or value['exit_code'] < 0:
        return 'capture_exit_unestablished:' + str(value['exit_code'])
    try:
        if aware_instant(value['ended_at']) < aware_instant(value['started_at']):
            return 'capture_timestamp_order'
    except (ValueError, TypeError):
        return 'capture_timestamp_unestablished'
    return None


def native_capture_failure(entries: list[dict[str, str]]) -> str | None:
    import base64
    archived = {row['name']: base64.b64decode(row['bytes_base64'], validate=True) for row in entries}
    for row in entries:
        data = archived[row['name']]
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('native_input_digest')
        name = row['name']
        pending = Path(name).name.startswith('.') and name.endswith('.pending')
        canonical_name = str(Path(name).with_name(Path(name).name[1:-len('.pending')])) if pending else name
        # These are all current capture sinks, including action/restoration sinks.
        capture_sink = (canonical_name.endswith('.native.json') or canonical_name.startswith('preserved-')
                        or canonical_name in {'restore-mv.json', 'restore-rmdir.json', 'isolation-mktemp.json',
                                    'isolation-directory-identity.json', 'isolation-mv.json', 'isolation-rmdir.json'})
        if capture_sink and pending and archived.get(canonical_name) != data:
            return name + ':capture_publication_unestablished'
        try:
            value = json.loads(data)
        except (ValueError, UnicodeError):
            if capture_sink:
                return name + ':capture_receipt_unreadable'
            continue
        if capture_sink or (isinstance(value, dict) and {'argv', 'error', 'stdout', 'stderr'} <= set(value)):
            failure = capture_failure(value)
            if failure is not None:
                return name + ':' + failure
    return None


def publish_eligibility(attempt: Path, scratch: Path, measurement: str) -> str:
    import base64
    value = validate_attempt(attempt)
    if value['item']['candidate'] != os.environ['CANDIDATE'] or value['item']['containing'] != os.environ['CONTAINING']:
        raise ValueError('runner_literal_identity_drift')
    entries = native_inputs(scratch)
    failure = native_capture_failure(entries)
    if failure is not None or (attempt / 'evidence-failed').exists():
        raise ValueError('native_capture_uncertain:' + str(failure))
    names = {row['name'] for row in entries}
    if not {'task.json', 'tree-boundary.json', 'peer-tasks.json', 'trigger-audit.json'} <= names:
        raise ValueError('gate_evidence_missing')
    write_json(attempt / 'eligible.json', {'version': 1, 'attempt_sha256': digest(attempt / 'attempt.json'),
               'item': value['item'], 'action': 'isolate_cache' if value['item']['kind'] == 'cache' else 'git_worktree_remove',
               'measurement': json.loads(measurement), 'native_inputs': entries})
    canonical_record(attempt / 'eligible.json')
    return digest(attempt / 'eligible.json')


def validate_eligibility(attempt: Path, expected: str | None = None) -> str | None:
    import base64
    path = attempt / 'eligible.json'
    if not path.exists():
        if expected is not None or path.with_name('.eligible.json.pending').exists():
            raise ValueError('eligibility_unpublished')
        return None
    value = canonical_record(path)
    owner = validate_attempt(attempt)
    if not isinstance(value, dict) or set(value) != {'version', 'attempt_sha256', 'item', 'action', 'measurement', 'native_inputs'}:
        raise ValueError('eligibility_schema')
    if (value['version'] != 1 or value['attempt_sha256'] != digest(attempt / 'attempt.json')
            or not same_evidence(value['item'], owner['item']) or value['action'] != (
                'isolate_cache' if owner['item']['kind'] == 'cache' else 'git_worktree_remove')):
        raise ValueError('eligibility_binding')
    seen = set()
    for row in value['native_inputs']:
        if not isinstance(row, dict) or set(row) != {'name', 'sha256', 'bytes_base64'} or row['name'] in seen:
            raise ValueError('native_input_schema')
        seen.add(row['name'])
        data = base64.b64decode(row['bytes_base64'], validate=True)
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('native_input_digest')
    failure = native_capture_failure(value['native_inputs'])
    if failure is not None:
        raise ValueError('native_capture_uncertain:' + failure)
    actual = digest(path)
    if expected is not None and actual != expected:
        raise ValueError('eligibility_digest_drift')
    return actual


def acknowledgement(attempt: Path, journal: Path, start: int, end: int) -> dict[str, Any]:
    owner = validate_attempt(attempt)
    data = journal.read_bytes()
    if len(data) > EVIDENCE_CAP or len(data) != end or not 0 <= start < end or not data.endswith(b'\n'):
        raise ValueError('journal_range_or_cap')
    return {'version': 1, 'context': owner['context'], 'ordinal': owner['ordinal'],
            'attempt_sha256': digest(attempt / 'attempt.json'), 'eligible_sha256': validate_eligibility(attempt),
            'journal_path': str(journal), 'prefix_bytes': end,
            'prefix_sha256': hashlib.sha256(data).hexdigest(), 'row_start': start, 'row_end': end,
            'row_sha256': hashlib.sha256(data[start:end]).hexdigest()}


def validate_coverage(context: dict[str, Any], manifest: list[dict[str, Any]],
                      *, recovery: bool = False) -> list[dict[str, Any]]:
    root = Path(context['evidence_root'])
    # Explicit enclosing failure wins even before a candidate envelope exists.
    # A genuinely fresh observer has only its successful before record; failed
    # or pending postwrites must never masquerade as untouched input.
    for name in ('before.json', 'batch.json', 'after.json', 'sequence.json'):
        path = root / name
        if path.exists() or path.is_symlink() or path.with_name('.' + name + '.pending').exists():
            enclosing = canonical_record(path)
            if name == 'batch.json':
                if not successful_receipts([enclosing]):
                    raise ValueError('native_batch_failure')
            elif (enclosing.get('complete') is not True
                  or (name in ('before.json', 'after.json')
                      and enclosing.get('binding') != context['binding'])
                  or (name == 'sequence.json' and (
                      enclosing.get('write_errors') or enclosing.get('unknown_postcondition')
                      or enclosing.get('coverage_context') != context))):
                raise ValueError('enclosing_observation_failure:' + name)
    namespace = root / 'attempts'
    journal = Path(context['journal_path'])
    if not namespace.is_dir() or namespace.is_symlink():
        raise ValueError('attempt_namespace_missing')
    paths = sorted(namespace.iterdir(), key=lambda p: p.name)
    if len(paths) > CAP or any(not p.is_dir() or p.is_symlink() for p in paths):
        raise ValueError('attempt_namespace_unknown_or_cap')
    data = journal.read_bytes() if journal.exists() else b''
    if len(data) > EVIDENCE_CAP or (data and not data.endswith(b'\n')):
        raise ValueError('journal_truncated_or_cap')
    lines = data.splitlines(keepends=True)
    if len(lines) != len(paths):
        raise ValueError('attempt_terminal_coverage_missing')
    by_candidate = {item['candidate']: (n, item) for n, item in enumerate(manifest, 1)}
    rows = []
    seen = set()
    offset = 0
    for number, line in enumerate(lines, 1):
        row = json.loads(line)
        ordinal = row.get('ordinal')
        if type(ordinal) is not int or ordinal < 1 or ordinal > len(manifest):
            raise ValueError('terminal_ordinal')
        path = namespace / str(ordinal)
        owner = validate_attempt(path, context)
        candidate = row.get('candidate')
        if candidate in seen or candidate not in by_candidate:
            raise ValueError('terminal_duplicate_or_unknown')
        seen.add(candidate)
        expected_ordinal, item = by_candidate[candidate]
        if ordinal != expected_ordinal or not same_evidence(owner['item'], item) or owner['argv'] != row.get('argv'):
            raise ValueError('terminal_literal_binding')
        allowed = {'attempt.json', '.attempt.json.pending', 'eligible.json', '.eligible.json.pending',
                   'ack.json', '.ack.json.pending', 'terminal-native.json', '.terminal-native.json.pending'}
        children = list(path.iterdir())
        scratch = [p for p in children if p.name.startswith('gates.') and p.is_dir() and not p.is_symlink()]
        if len(scratch) == 1:
            failure = native_capture_failure(native_inputs(scratch[0]))
            if failure is not None:
                raise ValueError('native_capture_uncertain:' + failure)
        if len(scratch) != 1 or any(p.is_symlink() or (p.name not in allowed and p not in scratch) for p in children):
            raise ValueError('attempt_namespace_extra_or_missing')
        ack = canonical_record(path / 'ack.json')
        end = offset + len(line)
        expected = {'version': 1, 'context': context, 'ordinal': ordinal,
                    'attempt_sha256': digest(path / 'attempt.json'), 'eligible_sha256': validate_eligibility(path),
                    'journal_path': str(journal), 'prefix_bytes': end,
                    'prefix_sha256': hashlib.sha256(data[:end]).hexdigest(),
                    'row_start': offset, 'row_end': end, 'row_sha256': hashlib.sha256(line).hexdigest()}
        if not same_evidence(ack, expected) or row.get('attempt_sha256') != expected['attempt_sha256'] or row.get('eligible_sha256') != expected['eligible_sha256']:
            raise ValueError('terminal_acknowledgement_mismatch')
        native = canonical_record(path / 'terminal-native.json')
        expected_native = {key: row[key] for key in ('exit_code', 'receipt', 'stdout', 'stderr', 'error')}
        expected_native['native_inputs'] = native_inputs(scratch[0])
        if not same_evidence(native, expected_native):
            raise ValueError('native_terminal_mismatch')
        failure = native_capture_failure(expected_native['native_inputs'])
        if failure is not None:
            raise ValueError('native_capture_uncertain:' + failure)
        if row.get('exit_code') not in (0, 2) or row.get('error') is not None:
            raise ValueError('native_terminal_failure')
        if row['exit_code'] == 0 and expected['eligible_sha256'] is None:
            raise ValueError('removal_without_eligibility')
        rows.append(row)
        offset = end
    if recovery:
        # Even an empty prior namespace can follow failed envelope publication.
        # Later readers need the enclosing native outcome, never an inferred fresh run.
        sequence = canonical_record(root / 'sequence.json')
        batch = canonical_record(root / 'batch.json')
        after = canonical_record(root / 'after.json')
        before = canonical_record(root / 'before.json')
        if (sequence.get('binding') != context['binding'] or sequence.get('batch') != batch
                or not successful_receipts([batch]) or not sequence.get('complete')
                or sequence.get('write_errors') or sequence.get('unknown_postcondition')
                or sequence.get('coverage_context') != context or after.get('complete') is not True
                or before.get('complete') is not True):
            raise ValueError('enclosing_observation_incomplete')
        for pending in root.glob('.*.pending'):
            canonical_record(root / pending.name[1:-len('.pending')])
    return rows


def evidence_cli(args: argparse.Namespace) -> int:
    if args.command == 'evidence-capture':
        from observe_cleanup_batch import capture_command
        argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
        # Inherit the candidate's cancellation group. Only outer observers own
        # a separate group; native action children must not escape that lifetime.
        value = capture_command(argv, Path.cwd(), timeout=45, own_group=False)
        value['interpreter'] = sys.executable
        value['python_version'] = sys.version
        write_json(args.output, value)
        sys.stdout.write(value['stdout'])
        sys.stderr.write(value['stderr'])
        return value['exit_code'] if value['error'] is None and value['exit_code'] is not None else 3
    if args.command == 'capture-check':
        failure = capture_failure(canonical_record(args.output))
        if failure is not None:
            raise ValueError(failure)
        return 0
    if args.command == 'cache-index':
        print(json.dumps(cache_index(args.containing), sort_keys=True))
        return 0
    if args.command == 'eligible':
        print(publish_eligibility(args.attempt, args.scratch, args.measurement))
        return 0
    if args.command == 'validate-eligible':
        validate_attempt(args.attempt)
        validate_eligibility(args.attempt, args.sha256)
        return 0
    validate_attempt(args.attempt)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare')
    prep.add_argument('--inventory', type=Path, required=True)
    prep.add_argument('--history-exclusions', type=Path, required=True)
    prep.add_argument('--output-dir', type=Path, required=True)
    prep.add_argument('--max-candidates', type=int, default=CAP)
    retention = commands.add_parser('retention')
    retention.add_argument('--task-json', type=Path, required=True)
    retention.add_argument('--floor-seconds', type=int, required=True)
    for name in ('attempt-check', 'eligible', 'validate-eligible'):
        sub = commands.add_parser(name)
        sub.add_argument('--attempt', type=Path, required=True)
        if name == 'eligible':
            sub.add_argument('--scratch', type=Path, required=True)
            sub.add_argument('--measurement', required=True)
        if name == 'validate-eligible':
            sub.add_argument('--sha256', required=True)
    index = commands.add_parser('cache-index')
    index.add_argument('--containing', type=Path, required=True)
    capture = commands.add_parser('evidence-capture')
    capture.add_argument('--output', type=Path, required=True)
    capture.add_argument('argv', nargs=argparse.REMAINDER)
    check = commands.add_parser('capture-check')
    check.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if any(isinstance(value, Path) and not value.is_absolute() for value in vars(args).values()):
            raise ValueError('input_and_output_paths_must_be_absolute')
        if args.command not in ('retention', 'prepare'):
            return evidence_cli(args)
        if args.command == 'retention':
            return 0 if retention_eligible(read_json(args.task_json), args.floor_seconds,
                                           dt.datetime.now(dt.timezone.utc)) else 2
        result = prepare(args.inventory, args.history_exclusions, args.output_dir, args.max_candidates)
        print(json.dumps({'state': result['state'], 'selected': len(result['manifest'])}))
        return 2 if result['state'] == 'incomplete' else 0
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
