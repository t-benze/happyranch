#!/usr/bin/env python3
"""Observe a packaged cleanup batch and preserve native failure/publication evidence."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import time
from typing import Any

# Packaged source is read-only evidence, including during sibling imports.
sys.dont_write_bytecode = True

from prepare_cleanup import (CAP, aware_instant, beneath, binding, current_owner,
                             digest, history_input, read_json, retention_eligible, successful_receipts,
                             validate_inventory, write_json)

SCRIPTS = Path(__file__).resolve().parent
COMMAND_CAP = 1 << 20
REQUEST_KEYS = {'task_id', 'session_id', 'title', 'script', 'interpreter', 'rationale',
                'review_required', 'persistent', 'max_runtime_seconds', 'cwd_hint'}


def capture_command(argv: list[str], cwd: Path, timeout: float = 10,
                    cap: int = COMMAND_CAP) -> dict[str, Any]:
    receipt: dict[str, Any] = {'argv': argv, 'cwd': str(cwd), 'started_at': dt.datetime.now(dt.timezone.utc).isoformat(),
                              'exit_code': None, 'stdout': '', 'stderr': '', 'error': None}
    streams = {'stdout': bytearray(), 'stderr': bytearray()}
    process = None
    try:
        process = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, start_new_session=True)
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 'stdout')
            selector.register(process.stderr, selectors.EVENT_READ, 'stderr')
            deadline = time.monotonic() + timeout
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise TimeoutError('command_timeout')
                for key, _ in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    value = streams[key.data]
                    value.extend(chunk[:max(0, cap - len(value))])
                    if len(value) >= cap:
                        raise ValueError('command_output_cap:' + key.data)
            process.wait(timeout=max(.01, deadline - time.monotonic()))
        receipt['exit_code'] = process.returncode
    except (OSError, ValueError, TimeoutError, subprocess.TimeoutExpired) as exc:
        receipt['error'] = str(exc)
        if process is not None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            receipt['exit_code'] = process.returncode
    finally:
        if process is not None:
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    receipt.update({key: bytes(value).decode('utf-8', errors='replace') for key, value in streams.items()})
    receipt['ended_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    return receipt


def _read(path: Path, cap: int = COMMAND_CAP) -> dict[str, Any]:
    receipt: dict[str, Any] = {'path': str(path), 'value': None, 'error': None}
    try:
        with path.open('rb') as handle:
            value = handle.read(cap + 1)
        if len(value) > cap:
            raise ValueError('read_cap')
        receipt['value'] = value.decode('utf-8', errors='strict')
    except (OSError, ValueError) as exc:
        receipt['error'] = str(exc)
    return receipt


def _link(path: Path) -> dict[str, Any]:
    try:
        return {'path': str(path), 'value': os.readlink(path), 'error': None}
    except OSError as exc:
        return {'path': str(path), 'value': None, 'error': str(exc)}


def _decode_mount(value: str) -> str:
    return re.sub(r'\\([0-7]{3})', lambda match: chr(int(match[1], 8)), value)


def _mount_table(text: str) -> list[dict[str, Any]]:
    result = []
    for line in text.splitlines():
        left, right = line.split(' - ', 1)
        fields, tail = left.split(), right.split()
        if len(fields) < 6 or len(tail) < 3 or not fields[0].isdigit() or not fields[1].isdigit():
            raise ValueError('malformed_mountinfo')
        target = _decode_mount(fields[4])
        if not target.startswith('/'):
            raise ValueError('relative_mount_target')
        result.append({'id': fields[0], 'parent': fields[1], 'device': fields[2],
                       'root': _decode_mount(fields[3]), 'target': target,
                       'options': fields[5], 'fstype': tail[0], 'source': _decode_mount(tail[1]),
                       'super_options': tail[2]})
    if not result:
        raise ValueError('empty_mountinfo')
    return result


def capture_context() -> dict[str, Any]:
    pid = os.getpid()
    self_root = Path('/proc') / str(pid)
    reads = {'mountinfo': _read(self_root / 'mountinfo'), 'pid1_comm': _read(Path('/proc/1/comm'), 64),
             'self_pid_ns': _link(self_root / 'ns/pid'), 'self_mnt_ns': _link(self_root / 'ns/mnt'),
             'root_pid_ns': _link(Path('/proc/1/ns/pid')), 'root_mnt_ns': _link(Path('/proc/1/ns/mnt'))}
    errors = [key + ':' + value['error'] for key, value in reads.items()
              if value['error'] and not key.startswith('root_')]
    mounts = []
    if not errors:
        try:
            mounts = _mount_table(reads['mountinfo']['value'])
            proc_mounts = [item for item in mounts if item['fstype'] == 'proc']
            if (reads['pid1_comm']['value'].strip() not in ('init', 'systemd')
                    or len(proc_mounts) != 1 or proc_mounts[0]['target'] != '/proc'
                    or proc_mounts[0]['root'] != '/'):
                errors.append('host_init_or_unstacked_proc_unestablished')
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
    return {'uid': os.getuid(), 'gid': os.getgid(), 'pid': pid, 'reads': reads,
            'mounts': mounts, 'errors': errors, 'complete': not errors}


def _identity(path: Path) -> list[int]:
    value = path.lstat()
    return [value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid]


def _metadata(path: Path, mounts: list[dict[str, Any]]) -> dict[str, Any]:
    # Metadata only: credential/configuration file contents are never opened.
    result: dict[str, Any] = {'path': str(path)}
    try:
        result['identity'] = _identity(path)
        resolved = path.resolve(strict=True)
        result['resolved'] = str(resolved)
        result['target_identity'] = _identity(resolved)
        result['link'] = os.readlink(path) if path.is_symlink() else None
        covering = [item for item in mounts if beneath(resolved, Path(item['target']))]
        if not covering:
            raise ValueError('mount_coverage_absent')
        longest = max(len(item['target']) for item in covering)
        covering = [item for item in covering if len(item['target']) == longest]
        if len(covering) != 1:
            raise ValueError('mount_coverage_ambiguous')
        result['mount'] = covering[0]
        if resolved.is_dir():
            with os.scandir(resolved) as entries:
                for number, _ in enumerate(entries):
                    if number >= 4096:
                        raise ValueError('protected_directory_enumeration_cap')
        result['state'] = 'present'
    except FileNotFoundError as exc:
        try:
            # A dangling link is an error. Absence needs a readable, present
            # ancestor and an exact negative directory entry observation.
            if path.is_symlink():
                raise ValueError('dangling_protected_link') from exc
            ancestor = path.parent
            while not ancestor.exists():
                if ancestor.is_symlink() or ancestor == ancestor.parent:
                    raise ValueError('unknown_absent_parent') from exc
                ancestor = ancestor.parent
            names = os.listdir(ancestor)
            if len(names) > 4096:
                raise ValueError('parent_enumeration_cap')
            missing = path.relative_to(ancestor).parts[0]
            if missing in names:
                raise ValueError('changed_or_unreadable_target') from exc
            result.update(state='absent', parent=str(ancestor), parent_identity=_identity(ancestor))
        except (OSError, ValueError) as missing_exc:
            result.update(state='error', error=str(missing_exc))
    except (OSError, ValueError) as exc:
        result.update(state='error', error=str(exc))
    return result


def _cache_source(containing: Path) -> dict[str, Any]:
    receipts = [capture_command(['git', '-C', str(containing), 'ls-files', '-z'], containing),
                capture_command(['git', '-C', str(containing), 'status', '--porcelain=v1', '-z'], containing)]
    if not successful_receipts(receipts):
        raise ValueError('cache_git_evidence_incomplete:' + json.dumps(receipts))
    import hashlib
    value = hashlib.sha256()
    total = 0
    for name in receipts[0]['stdout'].split('\0'):
        if not name:
            continue
        path = containing / name
        if not beneath(path.resolve(), containing) or path.is_symlink():
            raise ValueError('cache_source_boundary')
        total += path.stat().st_size
        if total > 64 * COMMAND_CAP:
            raise ValueError('cache_source_cap')
        value.update(name.encode() + b'\0' + path.read_bytes())
    return {'receipts': receipts, 'source_sha256': value.hexdigest(), 'status': receipts[1]['stdout']}


def capture_protected(bound: dict[str, str], rows: list[dict[str, Any]], history_path: Path,
                      context: dict[str, Any]) -> dict[str, Any]:
    workspace, primary = Path(bound['WORKSPACE']), Path(bound['PRIMARY'])
    paths = {workspace, workspace / 'repos', primary, primary / '.git',
             Path.home() / '.codex', Path.home() / '.aws', SCRIPTS.parent,
             workspace / '.agents/skills', workspace / '.claude/skills'}
    paths.update(workspace / name for name in ('output', 'memory', 'learnings', 'artifacts', 'logs',
                                              'config', '.happyranch', '.agents', '.claude', '.codex', '.aws',
                                              'task_history.md'))
    for row in rows:
        if row.get('kind') == 'cache':
            paths.update({Path(row['containing']), Path(row['containing']) / '.git'})
    receipts = [capture_command(['findmnt', '--json', '--list', '--output', 'TARGET,SOURCE,FSTYPE,OPTIONS'], workspace),
                capture_command(['git', '-C', str(primary), 'rev-parse', '--git-common-dir'], workspace)]
    errors = []
    table = None
    try:
        if not successful_receipts(receipts):
            raise ValueError('required_command_failed')
        table = json.loads(receipts[0]['stdout'])
        if (not isinstance(table, dict) or not isinstance(table.get('filesystems'), list)
                or not table['filesystems'] or any(not isinstance(item, dict)
                or not isinstance(item.get('target'), str) or not item['target'].startswith('/')
                or not all(isinstance(item.get(key), str) for key in ('source', 'fstype', 'options'))
                for item in table['filesystems'])):
            raise ValueError('findmnt_malformed')
        git_root = Path(receipts[1]['stdout'].strip())
        paths.add(git_root if git_root.is_absolute() else primary / git_root)
    except (ValueError, TypeError) as exc:
        errors.append(str(exc))
    metadata = [_metadata(path, context['mounts']) for path in sorted(paths)]
    errors.extend(item['path'] + ':' + item['error'] for item in metadata if item['state'] == 'error')
    history = None
    packages: dict[str, str] = {}
    caches: dict[str, Any] = {}
    concurrent: dict[str, Any] = {}
    filesystem = None
    try:
        # Include every primary root in this positively enumerated repository
        # directory without descending into registered candidate worktrees.
        repositories = list((workspace / 'repos').iterdir())
        if len(repositories) > 4096:
            raise ValueError('repository_metadata_cap')
        extra = []
        for repo in repositories:
            extra.extend([_metadata(repo, context['mounts']), _metadata(repo / '.git', context['mounts'])])
        metadata.extend(extra)
        errors.extend(item['path'] + ':' + item['error'] for item in extra if item['state'] == 'error')
        history, _ = history_input(history_path)
        history = {'input_sha256': digest(history_path), 'sources': history['sources']}
        packages['../SKILL.md'] = digest(SCRIPTS.parent / 'SKILL.md')
        for path in sorted(SCRIPTS.iterdir()):
            if path.is_file() and path.suffix in ('.py', '.sh'):
                packages[path.name] = digest(path)
        for row in rows:
            if row.get('kind') == 'cache':
                containing = Path(row['containing'])
                caches[str(containing)] = _cache_source(containing)
        for name in ('output', 'memory', 'artifacts', 'logs'):
            path = workspace / name
            if not path.exists():
                continue
            children = list(path.iterdir())
            if len(children) > 4096:
                raise ValueError('concurrent_metadata_cap')
            concurrent[name] = {str(child): [*_identity(child), child.lstat().st_size,
                                             child.lstat().st_mtime_ns, child.lstat().st_ctime_ns]
                                for child in children}
        fs = os.statvfs(workspace)
        filesystem = {'available_bytes': fs.f_bavail * fs.f_frsize, 'free_inodes': fs.f_favail}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errors.append(str(exc))
    return {'metadata': metadata, 'commands': receipts, 'findmnt': table, 'history': history, 'package_sha256': packages,
            'cache_source': caches, 'concurrent_metadata': concurrent, 'statvfs': filesystem,
            'errors': errors, 'complete': not errors}


def _observe(manifest: Path, history: Path, phase: str) -> dict[str, Any]:
    context = capture_context()
    observation: dict[str, Any] = {'phase': phase, 'context': context, 'errors': list(context['errors'])}
    try:
        bound = binding()
        if not bound['PRIMARY']:
            raise ValueError('observation_requires_assigned_repository')
        rows = read_json(manifest)
        if not isinstance(rows, list):
            raise ValueError('manifest_not_array')
        observation['binding'] = bound
        observation['owner_receipt'] = current_owner(bound, Path(bound['WORKSPACE']))
        protected = capture_protected(bound, rows, history, context)
        observation['protected'] = protected
        observation['errors'].extend(protected['errors'])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        observation['errors'].append(str(exc))
    observation['complete'] = not observation['errors']
    return observation


def compare_observations(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    errors = []
    concurrent = False
    if before.get('complete') is not True or after.get('complete') is not True:
        errors.append('observation_incomplete')
    else:
        if before['binding'] != after['binding']:
            errors.append('binding_changed')
        for key in ('uid', 'gid', 'pid', 'mounts'):
            if before['context'][key] != after['context'][key]:
                errors.append('context_changed:' + key)
        for key in ('self_pid_ns', 'self_mnt_ns'):
            if before['context']['reads'][key] != after['context']['reads'][key]:
                errors.append('namespace_changed:' + key)
        for key in ('metadata', 'findmnt', 'history', 'package_sha256'):
            if before['protected'][key] != after['protected'][key]:
                errors.append('protected_changed:' + key)
        # Native command timestamps change on purpose; compare only source/status.
        def caches(value: dict[str, Any]) -> dict[str, Any]:
            return {key: {name: row[name] for name in ('source_sha256', 'status')}
                    for key, row in value['protected']['cache_source'].items()}
        if caches(before) != caches(after):
            errors.append('cache_source_or_status_changed')
        concurrent = before['protected']['concurrent_metadata'] != after['protected']['concurrent_metadata']
    return {'complete': not errors, 'errors': errors, 'concurrent_output_changed': concurrent,
            'unique_reclaimed_bytes': None}


def run_observed_batch(manifest: Path, journal: Path, history: Path, evidence_dir: Path,
                       max_candidates: int, deadline_seconds: int,
                       candidate_timeout_seconds: int) -> int:
    if not 1 <= max_candidates <= CAP or not 1 <= deadline_seconds <= 1200 or not 1 <= candidate_timeout_seconds <= 180:
        raise ValueError('unsupported_batch_bounds')
    evidence_dir.mkdir(parents=True, exist_ok=True)
    sequence: dict[str, Any] = {'batch_started': False, 'batch': None, 'postcheck': None,
                                'complete': False, 'write_errors': [], 'persisted_artifacts': []}

    def retain(name: str, value: Any) -> bool:
        try:
            write_json(evidence_dir / name, value)
        except (OSError, ValueError, TypeError) as exc:
            sequence['write_errors'].append({'artifact': name, 'error': str(exc)})
            return False
        sequence['persisted_artifacts'].append(name)
        return True

    before = _observe(manifest, history, 'before')
    sequence['binding'] = before.get('binding')
    sequence['source_sha256'] = {'observe_cleanup_batch.py': digest(Path(__file__).resolve())}
    sequence['source_sha256'].update(before.get('protected', {}).get('package_sha256', {}))
    sequence['manifest_sha256'] = digest(manifest)
    sequence['history_sha256'] = digest(history)
    baseline_written = retain('before.json', before)
    sequence['baseline'] = {'complete': before['complete'] and baseline_written, 'errors': before['errors']}
    if sequence['baseline']['complete']:
        sequence['batch_started'] = True
        command = [sys.executable, str(SCRIPTS / 'run_cleanup_batch.py'), '--manifest', str(manifest),
                   '--journal', str(journal), '--max-candidates', str(max_candidates),
                   '--deadline-seconds', str(deadline_seconds), '--candidate-timeout-seconds', str(candidate_timeout_seconds)]
        sequence['environment'] = {**before['binding'], 'TMPDIR': os.environ.get('TMPDIR')}
        sequence['batch'] = capture_command(command, Path(before['binding']['WORKSPACE']),
                                             timeout=deadline_seconds + candidate_timeout_seconds + 10)
        # Retain native argv/cwd/exit/raw streams before post-observation can fail.
        retain('batch.json', sequence['batch'])
        try:
            after = _observe(manifest, history, 'after')
            post = compare_observations(before, after)
            post['native_errors'] = after['errors']
            if not retain('after.json', after):
                post['complete'] = False
                post['errors'].append('after_receipt_write_failed')
        except (OSError, ValueError, TypeError, KeyError) as exc:
            post = {'complete': False, 'errors': ['post_observation_failed'], 'native_errors': [str(exc)]}
        sequence['postcheck'] = post
        batch = sequence['batch']
        try:
            native = json.loads(batch['stdout'])
            if (not isinstance(native, dict) or native.get('remaining') != 0
                    or native.get('stop_reason') != 'complete'):
                raise ValueError('batch_incomplete')
        except (ValueError, TypeError) as exc:
            sequence['batch_summary_error'] = str(exc)
        sequence['complete'] = (batch['exit_code'] == 0 and batch['error'] is None
                                and 'batch_summary_error' not in sequence and post['complete']
                                and not sequence['write_errors'])
    sequence['halt_reason'] = ('complete' if sequence['complete'] else 'baseline_incomplete'
                                if not sequence['batch_started'] else 'batch_or_postcheck_incomplete')
    sequence['unknown_postcondition'] = not sequence['complete']
    try:
        write_json(evidence_dir / 'sequence.json', sequence)
        sequence_persisted = True
    except (OSError, ValueError, TypeError) as exc:
        sequence['write_errors'].append({'artifact': 'sequence.json', 'error': str(exc)})
        sequence.update(complete=False, unknown_postcondition=True, halt_reason='sequence_write_failed')
        sequence_persisted = False
    if sequence['write_errors']:
        # Each native stream is bounded by capture_command's COMMAND_CAP. This
        # single structured process-output receipt is the fallback, never a
        # claim that an unavailable file was persisted or a candidate journal.
        print(json.dumps({'kind': 'cleanup_sequence_fallback', 'persisted': sequence_persisted,
                          'evidence_dir': str(evidence_dir), 'sequence': sequence}, sort_keys=True))
    return 0 if sequence['complete'] else 2


def validate_job_request(request: dict[str, Any], bound: dict[str, str], workspace: Path) -> None:
    required = REQUEST_KEYS - {'cwd_hint'}
    if not isinstance(request, dict) or not required <= request.keys() or request.keys() - REQUEST_KEYS:
        raise ValueError('unsupported_request_shape')
    if request['task_id'] != bound['ACTING_TASK'] or request['session_id'] != bound['SESSION_ID']:
        raise ValueError('binding_mismatch')
    if (request['interpreter'] != 'bash' or request['review_required'] is not False
            or request['persistent'] is not True or type(request['max_runtime_seconds']) is not int
            or not 1 <= request['max_runtime_seconds'] <= 3600
            or any(not isinstance(request[key], str) or not request[key]
                   for key in ('task_id', 'session_id', 'title', 'rationale', 'script'))
            or len(request['title']) > 200 or len(request['script'].encode()) > 65536):
        raise ValueError('unsupported_request_value')
    hint = request.get('cwd_hint')
    if hint is not None and (not isinstance(hint, str) or Path(hint).is_absolute()
                            or '..' in hint.split('/') or '\0' in hint
                            or not beneath((workspace / hint).resolve(), workspace)
                            or not (workspace / hint).is_dir()):
        raise ValueError('invalid_cwd_hint')


def verified_preparation(preparation_path: Path, inventory: dict[str, Any],
                         bound: dict[str, str]) -> dict[str, Any]:
    preparation = read_json(preparation_path)
    if not isinstance(preparation, dict) or preparation.get('binding') != bound:
        raise ValueError('preparation_binding_mismatch')
    validate_inventory(inventory, bound)
    snapshot = preparation_path.parent / 'inventory.json'
    original = Path(preparation['inventory_path'])
    manifest = Path(preparation['manifest_path'])
    history = Path(preparation['history_path'])
    if (not all(path.is_absolute() for path in (original, manifest, history))
            or read_json(snapshot) != inventory or read_json(original) != inventory
            or digest(snapshot) != preparation['inventory_snapshot_sha256']
            or digest(original) != preparation['inventory_sha256']
            or read_json(manifest) != preparation['manifest']
            or digest(manifest) != preparation['manifest_sha256']
            or digest(history) != preparation['history_sha256']):
        raise ValueError('preparation_artifact_changed')
    history_value, exclusions = history_input(history)
    if preparation.get('history_sources') != history_value['sources']:
        raise ValueError('preparation_history_inconsistent')
    if preparation.get('state') == 'no_candidates_complete':
        if (preparation.get('manifest') != [] or preparation.get('frozen_selection') != []
                or preparation.get('errors') != [] or preparation.get('no_refill') is not True
                or not successful_receipts(preparation.get('receipts'))):
            raise ValueError('empty_preparation_incomplete')
        owner = preparation['receipts'][0]
        record = json.loads(owner['stdout'])
        if (owner['argv'] != ['happyranch', 'recall', '--org', bound['ORG'], bound['ACTING_TASK']]
                or owner['cwd'] != bound['WORKSPACE'] or record.get('task_id') != bound['ACTING_TASK']
                or record.get('assigned_agent') != bound['AGENT'] or record.get('status') != 'in_progress'
                or not isinstance(record.get('brief'), str)
                or record['brief'].splitlines()[0] != bound['CLEANUP_MARKER']):
            raise ValueError('empty_preparation_owner_inconsistent')
        observed_at = aware_instant(preparation['observed_at'])
        expected = []
        seen: set[str] = set()
        for row in inventory['candidates']:
            candidate, containing = Path(row['candidate']), Path(row['containing'])
            listed = row['listing']
            primary = Path(bound['PRIMARY'])
            if (not candidate.is_absolute() or candidate.resolve() != candidate
                    or containing.resolve() != containing or not containing.name.startswith('TASK-')
                    or containing != primary / '.claude/worktrees' / containing.name
                    or str(candidate) in seen or row['kind'] not in ('worktree', 'cache')
                    or (row['kind'] == 'worktree' and candidate != containing)
                    or (row['kind'] == 'cache' and (candidate.parent != containing
                        or candidate.name not in ('.venv', 'node_modules')))):
                raise ValueError('empty_preparation_candidate_scope_unknown')
            seen.add(str(candidate))
            if any(beneath(candidate, old) or beneath(old, candidate)
                   or beneath(containing, old) or beneath(old, containing) for old in exclusions):
                reason = 'historical_overlap'
            elif isinstance(listed.get('assigned_agent'), str) and listed['assigned_agent'] != bound['AGENT']:
                reason = 'foreign_or_unknown_owner'
            elif (listed.get('assigned_agent') == bound['AGENT'] and listed.get('task_id') == containing.name
                  and not retention_eligible(listed, 604800 if row['kind'] == 'worktree' else 86400, observed_at)):
                reason = 'young_or_nonterminal'
            else:
                raise ValueError('empty_preparation_candidate_not_known_excluded')
            expected.append({'candidate': str(candidate), 'listing': listed, 'reason': reason})
        if preparation.get('rows') != expected:
            raise ValueError('empty_preparation_rows_inconsistent')
    elif preparation.get('state') != 'prepared_nonempty' or not preparation.get('manifest'):
        raise ValueError('preparation_incomplete')
    return preparation


def render_job_request(preparation_path: Path, evidence_dir: Path, deadline_seconds: int,
                       candidate_timeout_seconds: int, max_runtime_seconds: int,
                       cwd_hint: str | None = None) -> dict[str, Any] | None:
    bound = binding()
    inventory = read_json(preparation_path.parent / 'inventory.json')
    preparation = verified_preparation(preparation_path, inventory, bound)
    if preparation['state'] == 'no_candidates_complete':
        return None
    manifest = Path(preparation['manifest_path'])
    history = Path(preparation['history_path'])
    workspace = Path(bound['WORKSPACE'])
    evidence_dir.mkdir(parents=True, exist_ok=True)
    if (evidence_dir.resolve() != evidence_dir
            or not beneath(evidence_dir, workspace / 'output' / bound['ACTING_TASK'])
            or any(evidence_dir.iterdir()) or not os.access(evidence_dir, os.W_OK)):
        raise ValueError('evidence_boundary_or_not_empty')
    if (type(deadline_seconds) is not int or not 1 <= deadline_seconds <= 1200
            or type(candidate_timeout_seconds) is not int or not 1 <= candidate_timeout_seconds <= 180
            or type(max_runtime_seconds) is not int
            or max_runtime_seconds < deadline_seconds + candidate_timeout_seconds + 60):
        raise ValueError('insufficient_job_observation_budget')
    remaining = aware_instant(os.environ.get('CLEANUP_SESSION_DEADLINE')) - dt.datetime.now(dt.timezone.utc)
    if remaining <= dt.timedelta(seconds=max_runtime_seconds + 30):
        raise ValueError('insufficient_remaining_session_budget')
    current_owner(bound, workspace)
    for name in ('observe_cleanup_batch.py', 'prepare_cleanup.py', 'run_cleanup_batch.py', 'run_cleanup_candidate.sh'):
        if not (SCRIPTS / name).is_file():
            raise ValueError('packaged_dependency_missing')
    command = [sys.executable, str(SCRIPTS / 'observe_cleanup_batch.py'), 'run', '--manifest', str(manifest),
               '--journal', str(evidence_dir / 'final-ledger.jsonl'), '--history-exclusions', str(history),
               '--evidence-dir', str(evidence_dir), '--max-candidates', '40',
               '--deadline-seconds', str(deadline_seconds), '--candidate-timeout-seconds', str(candidate_timeout_seconds)]
    exports = dict(bound)
    exports['CLEANUP_SESSION_DEADLINE'] = os.environ['CLEANUP_SESSION_DEADLINE']
    if 'TMPDIR' in os.environ:
        exports['TMPDIR'] = os.environ['TMPDIR']
    script = 'set -euo pipefail\n' + ''.join(f'export {key}={shlex.quote(value)}\n' for key, value in exports.items()) + shlex.join(command) + '\n'
    request = {'task_id': bound['ACTING_TASK'], 'session_id': bound['SESSION_ID'],
               'title': 'Observed cleanup ' + bound['ACTING_TASK'], 'script': script, 'interpreter': 'bash',
               'rationale': 'One frozen observed batch; current session ' + bound['SESSION_ID'],
               'review_required': False, 'persistent': True, 'max_runtime_seconds': max_runtime_seconds}
    if cwd_hint is not None:
        request['cwd_hint'] = cwd_hint
    validate_job_request(request, bound, workspace)
    return request


def summarize_publication(inventory: dict[str, Any], sequence: dict[str, Any],
                          task: dict[str, Any], result: dict[str, Any],
                          audit: dict[str, Any], preparation: dict[str, Any] | None = None,
                          evidence_errors: list[str] | None = None) -> dict[str, Any]:
    status = task.get('status')
    native_inventory = (inventory.get('complete') is True and isinstance(inventory.get('candidates'), list)
                        and all(successful_receipts(inventory.get(key)) for key in
                            ('repository_receipts', 'assigned_task_receipts', 'trigger_history_receipts')))
    sequence_complete = (sequence.get('complete') is True and sequence.get('batch_started') is True
                         and sequence.get('baseline', {}).get('complete') is True
                         and successful_receipts([sequence.get('batch')])
                         and sequence.get('postcheck', {}).get('complete') is True
                         and not sequence.get('write_errors') and not sequence.get('unknown_postcondition')
                         and 'batch_summary_error' not in sequence)
    empty = (native_inventory and inventory.get('candidates') == []
             and inventory.get('repositories_state') in ('present', 'absent')
             and all(successful_receipts(inventory.get(key)) for key in
                     ('repository_receipts', 'assigned_task_receipts', 'trigger_history_receipts')))
    no_action = not sequence and (empty or (native_inventory and preparation is not None
                                           and preparation.get('state') == 'no_candidates_complete'))
    evidence_complete = (native_inventory and not evidence_errors and (no_action or sequence_complete))
    pending = status == 'in_progress' and result.get('result_id') is not None
    canonical = task.get('output_dir')
    published = (status == 'completed' and isinstance(canonical, str) and bool(canonical)
                 and audit.get('completion_report') is True)
    if status in ('failed', 'cancelled', 'superseded') or task.get('executor_exit_code') not in (None, 0):
        state = 'failed_unpublished' if not published else 'failed'
    elif pending and evidence_complete:
        state = 'result_admitted_terminal_pending'
    elif published and evidence_complete:
        state = 'published_empty' if no_action else 'published'
    elif status == 'in_progress' and evidence_complete:
        state = 'local_complete_unpublished'
    else:
        state = 'incomplete_unpublished'
    return {'state': state, 'task_status': status, 'canonical_output_dir': canonical,
            'local_result_id': result.get('result_id'), 'result_admitted_terminal_pending': pending,
            'native_empty_inventory': empty, 'no_action_complete': no_action and evidence_complete,
            'evidence_complete': evidence_complete, 'evidence_errors': evidence_errors or [],
            'preparation': preparation,
            'inventory': inventory, 'sequence': sequence, 'task_projection': task,
            'result_projection': result, 'audit_projection': audit, 'unique_reclaimed_bytes': None}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    request = commands.add_parser('job-request')
    request.add_argument('--preparation', type=Path, required=True)
    request.add_argument('--output', type=Path, required=True)
    request.add_argument('--evidence-dir', type=Path, required=True)
    request.add_argument('--deadline-seconds', type=int, default=1200)
    request.add_argument('--candidate-timeout-seconds', type=int, default=180)
    request.add_argument('--max-runtime-seconds', type=int, default=2100)
    request.add_argument('--cwd-hint')
    for name in ('observe', 'run'):
        sub = commands.add_parser(name)
        sub.add_argument('--manifest', type=Path, required=True)
        sub.add_argument('--history-exclusions', type=Path, required=True)
        sub.add_argument('--evidence-dir', type=Path, required=True)
        if name == 'observe':
            sub.add_argument('--phase', choices=('before', 'after'), required=True)
        else:
            sub.add_argument('--journal', type=Path, required=True)
            sub.add_argument('--max-candidates', type=int, default=40)
            sub.add_argument('--deadline-seconds', type=int, default=1200)
            sub.add_argument('--candidate-timeout-seconds', type=int, default=180)
    report = commands.add_parser('report')
    for name in ('inventory', 'sequence', 'task-projection', 'result-projection', 'audit-projection', 'output-dir'):
        report.add_argument('--' + name, type=Path, required=True)
    report.add_argument('--preparation', type=Path)
    args = parser.parse_args(argv)
    try:
        if any(isinstance(value, Path) and not value.is_absolute() for value in vars(args).values()):
            raise ValueError('input_and_output_paths_must_be_absolute')
        if args.command == 'job-request':
            value = render_job_request(args.preparation, args.evidence_dir, args.deadline_seconds,
                                       args.candidate_timeout_seconds, args.max_runtime_seconds, args.cwd_hint)
            if value is not None:
                write_json(args.output, value)
            print(json.dumps({'request_emitted': value is not None}))
            return 0
        if args.command == 'run':
            return run_observed_batch(args.manifest, args.journal, args.history_exclusions, args.evidence_dir,
                                      args.max_candidates, args.deadline_seconds, args.candidate_timeout_seconds)
        if args.command == 'observe':
            args.evidence_dir.mkdir(parents=True, exist_ok=True)
            value = _observe(args.manifest, args.history_exclusions, args.phase)
            write_json(args.evidence_dir / (args.phase + '.json'), value)
            return 0 if value['complete'] else 2
        bound = binding()
        inventory = read_json(args.inventory)
        preparation = None
        evidence_errors = []
        try:
            validate_inventory(inventory, bound)
            if args.preparation is not None:
                preparation = verified_preparation(args.preparation, inventory, bound)
        except (OSError, ValueError, TypeError, KeyError, IndexError) as exc:
            evidence_errors.append(str(exc))
        sequence = {}
        try:
            if args.sequence.name == '.sequence.json.pending':
                raise ValueError('sequence_staging_not_published')
            sequence = read_json(args.sequence)
            if not isinstance(sequence, dict):
                raise ValueError('sequence_not_object')
            if sequence.get('kind') == 'cleanup_sequence_fallback':
                fallback = sequence
                sequence = fallback.get('sequence')
                if (type(fallback.get('persisted')) is not bool or not isinstance(sequence, dict)
                        or sequence.get('binding') != bound):
                    raise ValueError('sequence_fallback_invalid')
                # A fallback is failure evidence, even if a readable staged or
                # historical success-valued file disagrees with captured output.
                evidence_errors.append('observer_write_failure_fallback')
        except (OSError, ValueError, TypeError) as exc:
            sequence = {}
            evidence_errors.append('sequence_unavailable:' + str(exc))
        value = summarize_publication(inventory, sequence, read_json(args.task_projection),
                                      read_json(args.result_projection), read_json(args.audit_projection),
                                      preparation, evidence_errors)
        args.output_dir.mkdir(parents=True, exist_ok=True)
        write_json(args.output_dir / 'publication.json', value)
        bound = binding()
        completion_status = ('blocked' if value['state'] in ('failed', 'failed_unpublished', 'incomplete_unpublished') else 'completed')
        write_json(args.output_dir / 'completion-intent.json', {
            'task_id': bound['ACTING_TASK'], 'session_id': bound['SESSION_ID'], 'agent': bound['AGENT'],
            'status': completion_status, 'confidence': 0 if completion_status == 'blocked' else 85,
            'summary': 'Cleanup evidence state: ' + value['state'] + '; canonical lifecycle remains authoritative.',
            'output_dir': 'output/' + bound['ACTING_TASK'],
            'risks': ['Unique physical reclaimed bytes unknown; callback has not been sent by this helper.'],
        })
        print(json.dumps({'state': value['state']}))
        return 0 if value['state'] in ('published', 'published_empty', 'result_admitted_terminal_pending', 'local_complete_unpublished') else 2
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
