#!/usr/bin/env python3
"""Explicit finite disposable cleanup boundary witness; never an automated gate.

Creates only a new owned mode0700 /tmp repository. Shipping subprocesses own
eligibility/observation. Declarative command fixtures never clear the scanner.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any

CASES = ('T1', 'T2', 'T3', 'O1', 'O2', 'O3', 'J1', 'P1')
MARKER = 'HAPPYRANCH SYSTEM WORKSPACE CLEANUP RUN (manual-dispatch)'


class Witness:
    def __init__(self, source: Path, root: Path, evidence: Path) -> None:
        self.source, self.root, self.evidence = source, root, evidence
        self.commands: list[dict[str, Any]] = []
        self.assertions: list[str] = []
        self.missing: list[str] = []
        self.scripts = source / 'runtime/skills/bundled/workspace-cleanup/scripts'
        self.workspace = root / 'workspace'
        self.primary = self.workspace / 'repos/fixture'
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', WORKSPACE=str(self.workspace),
                        PRIMARY=str(self.primary), AGENT='witness', ORG='fixture', ACTING_TASK='TASK-actor',
                        SESSION_ID='disposable-record-not-session-authority', CLEANUP_MARKER=MARKER,
                        CLEANUP_SESSION_DEADLINE=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat())
        self.records: dict[str, Any] = {'TASK-actor': {'task_id': 'TASK-actor', 'assigned_agent': 'witness',
                                                     'status': 'in_progress', 'brief': MARKER}}
        self.setup()

    def write(self, path: Path, value: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + '\n')
        return path

    def run(self, argv: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None,
            stdin: str | None = None) -> dict[str, Any]:
        where = cwd or self.workspace
        result = subprocess.run(argv, cwd=where, env=env or self.env, input=stdin,
                                capture_output=True, text=True, timeout=40)
        receipt = {'argv': argv, 'cwd': str(where), 'exit_code': result.returncode,
                   'stdout': result.stdout, 'stderr': result.stderr, 'error': None}
        self.commands.append(receipt)
        return receipt

    def check(self, condition: bool, assertion: str) -> None:
        if not condition:
            raise AssertionError(assertion)
        self.assertions.append(assertion)

    def setup(self) -> None:
        self.primary.mkdir(parents=True)
        for name in ('output', 'memory', 'artifacts', 'logs', '.codex', '.aws', '.agents', '.claude', 'config'):
            (self.workspace / name).mkdir()
        (self.workspace / 'task_history.md').write_text('Disposable history; no production records.\n')
        for argv in (['git', 'init', str(self.primary)], ['git', '-C', str(self.primary), 'config', 'user.email', 'witness@example.invalid'],
                     ['git', '-C', str(self.primary), 'config', 'user.name', 'Disposable witness']):
            self.check(self.run(argv)['exit_code'] == 0, 'disposable_git_setup')
        (self.primary / 'keeper').write_text('preserve\n')
        self.run(['git', '-C', str(self.primary), 'add', 'keeper'])
        self.check(self.run(['git', '-C', str(self.primary), 'commit', '-m', 'Disposable keeper'])['exit_code'] == 0, 'disposable_commit')
        self.fixtures = self.root / 'commands'
        self.fixtures.mkdir()
        self.reply = self.root / 'command-replies.json'
        self.log = self.root / 'fixture-commands.jsonl'
        self.counter = self.root / 'findmnt-count'
        self.sink = self.root / 'submit-count.jsonl'
        self.env.update(PATH=str(self.fixtures) + os.pathsep + os.environ['PATH'],
                        WITNESS_REPLIES=str(self.reply), WITNESS_LOG=str(self.log), WITNESS_COUNT=str(self.counter),
                        WITNESS_SINK=str(self.sink))
        fixture = '''#!{python}
import json, os, pathlib, subprocess, sys
name=pathlib.Path(sys.argv[0]).name
args=sys.argv[1:]
with open(os.environ['WITNESS_LOG'],'a') as f: f.write(json.dumps([name,*args])+'\\n')
replies=json.loads(pathlib.Path(os.environ['WITNESS_REPLIES']).read_text())
if name=='happyranch':
 if args[:2]==['jobs','submit']:
  with open(os.environ['WITNESS_SINK'],'a') as f: f.write(pathlib.Path(args[args.index('--from-file')+1]).read_text().replace('\\n','')+'\\n')
  print('controlled rejection',file=sys.stderr); sys.exit(17)
 if args[:1]!=['recall'] or args[-1] not in replies['tasks']: sys.exit(91)
 print(json.dumps(replies['tasks'][args[-1]])); sys.exit(0)
if name=='findmnt':
 p=pathlib.Path(os.environ['WITNESS_COUNT']); count=int(p.read_text())+1 if p.exists() else 1; p.write_text(str(count))
 item=replies['findmnt'].get(str(count),replies['findmnt']['default'])
 if item.get('chmod'): os.chmod(item['chmod'],0o700)
 if item.get('drift'): os.chmod(item['drift'],0o711)
 if item.get('deny'): os.chmod(item['deny'],0o000)
 print(item['stdout'],end=''); print(item.get('stderr',''),end='',file=sys.stderr); sys.exit(item['exit_code'])
sys.exit(92)
'''.format(python=sys.executable)
        for name in ('happyranch', 'findmnt'):
            path = self.fixtures / name
            path.write_text(fixture)
            path.chmod(0o700)
        native = self.run(['findmnt', '--json', '--list', '--output', 'TARGET,SOURCE,FSTYPE,OPTIONS'],
                          env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.check(native['exit_code'] == 0, 'native_findmnt_prerequisite')
        self.native_mount = {'exit_code': 0, 'stdout': native['stdout'], 'stderr': ''}
        self.replies: dict[str, Any] = {'tasks': self.records, 'findmnt': {'default': self.native_mount}}
        self.sync()
        self.history = self.write(self.root / 'history.json', {'complete': True,
                       'sources': [{'path': str(self.workspace / 'task_history.md'),
                                    'sha256': hashlib.sha256((self.workspace / 'task_history.md').read_bytes()).hexdigest()}],
                       'excluded_paths': []})

    def sync(self) -> None:
        self.write(self.reply, self.replies)

    def candidate(self, task_id: str, completed: str | None = None) -> dict[str, Any]:
        path = self.primary / '.claude/worktrees' / task_id
        self.check(self.run(['git', '-C', str(self.primary), 'worktree', 'add', str(path), '-b', 'task/' + task_id])['exit_code'] == 0,
                   'registered_disposable_worktree')
        record = {'task_id': task_id, 'assigned_agent': 'witness', 'status': 'completed',
                  'completed_at': completed or (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)).isoformat()}
        self.records[task_id] = record
        self.sync()
        measure = self.run(['du', '-s', '--block-size=1', str(path)])
        self.check(measure['exit_code'] == 0, 'native_size_receipt')
        return {'candidate': str(path), 'containing': str(path), 'kind': 'worktree',
                'allocated_bytes': int(measure['stdout'].split()[0]), 'listing': dict(record), 'measurement_receipts': [measure]}

    def inventory(self, rows: list[dict[str, Any]]) -> Path:
        git = self.run(['git', '-C', str(self.primary), 'worktree', 'list', '--porcelain'])
        # The assigned and filtered trigger receipts are declarative command
        # fixtures, clearly labelled; no live task/history mutation occurs.
        actor = self.run(['happyranch', 'recall', '--org', 'fixture', 'TASK-actor'])
        return self.write(self.root / 'inventory.json', {'complete': True,
                          'binding': {key: self.env[key] for key in ('WORKSPACE', 'PRIMARY', 'AGENT', 'ORG', 'ACTING_TASK', 'SESSION_ID', 'CLEANUP_MARKER')},
                          'repositories_state': 'present', 'repository_receipts': [git],
                          'assigned_task_receipts': [actor], 'trigger_history_receipts': [actor], 'candidates': rows})

    def prepare(self, inventory: Path, name: str, scripts: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        output = self.root / name
        receipt = self.run([sys.executable, str((scripts or self.scripts) / 'prepare_cleanup.py'), 'prepare',
                            '--inventory', str(inventory), '--history-exclusions', str(self.history), '--output-dir', str(output), '--max-candidates', '40'])
        return receipt, json.loads((output / 'preparation.json').read_text())

    def copy_scripts(self, name: str) -> Path:
        path = self.root / name / 'scripts'
        shutil.copytree(self.scripts, path)
        shutil.copyfile(self.scripts.parent / 'SKILL.md', path.parent / 'SKILL.md')
        return path

    def mutate(self, scripts: Path, filename: str, old: str, new: str) -> None:
        path = scripts / filename
        text = path.read_text()
        self.check(old in text, 'scratch_mutation_target_present:' + filename)
        path.write_text(text.replace(old, new, 1))

    def observed(self, manifest: Path, name: str, scripts: Path | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        if self.counter.exists():
            self.counter.unlink()
        self.sync()
        output = self.root / name
        receipt = self.run([sys.executable, str((scripts or self.scripts) / 'observe_cleanup_batch.py'), 'run',
                            '--manifest', str(manifest), '--journal', str(output / 'journal.jsonl'),
                            '--history-exclusions', str(self.history), '--evidence-dir', str(output),
                            '--max-candidates', '40', '--deadline-seconds', '1', '--candidate-timeout-seconds', '5'])
        sequence_path = output / 'sequence.json'
        sequence = json.loads(sequence_path.read_text()) if sequence_path.exists() else {}
        return receipt, sequence


def t1(w: Witness) -> None:
    row = w.candidate('TASK-equivalent', '2026-01-01T00:00:00Z')
    positive = []
    for number, instant in enumerate(('2026-01-01T00:00:00Z', '2026-01-01T00:00:00+00:00', '2026-01-01T08:00:00+08:00')):
        w.records['TASK-equivalent']['completed_at'] = instant
        w.sync()
        receipt, prep = w.prepare(w.inventory([row]), 'equivalent-' + str(number))
        w.check(receipt['exit_code'] == 0 and prep['state'] == 'prepared_nonempty', 'equivalent_aware_positive:' + instant)
        positive.append(prep['manifest'])
    w.check(all(item == positive[0] for item in positive), 'same_literal_eligible_set')
    scratch = w.copy_scripts('lexical-regression')
    w.mutate(scratch, 'prepare_cleanup.py', "and aware_instant(listed.get('completed_at'))\n            == aware_instant(fresh.get('completed_at'))", "and listed.get('completed_at') == fresh.get('completed_at')")
    receipt, prep = w.prepare(w.inventory([row]), 'red-lexical', scratch)
    w.check(receipt['exit_code'] != 0 and not prep['manifest'], 'lexical_regression_detected')


def t2(w: Witness) -> None:
    code = '''import datetime as d, json, sys
sys.path.insert(0,sys.argv[1]); from prepare_cleanup import retention_eligible
now=d.datetime(2026,1,10,tzinfo=d.timezone.utc)
print(json.dumps([[retention_eligible({'status':'completed','completed_at':(now-d.timedelta(seconds=f,microseconds=x)).isoformat()},f,now) for x in (-1,0,1)] for f in (86400,604800)]))'''
    receipt = w.run([sys.executable, '-c', code, str(w.scripts)])
    w.check(receipt['exit_code'] == 0 and json.loads(receipt['stdout']) == [[False, True, True]] * 2, 'exact_inclusive_floors_callable_only')
    for number, value in enumerate((None, '', 'broken', '2026-01-01T00:00:00',
                                    (dt.datetime.now(dt.timezone.utc)-dt.timedelta(seconds=30)).isoformat(), '2026-01-01T00:00:00Z')):
        path = w.write(w.root / 'record.json', {'status': 'completed', 'completed_at': value})
        for floor in (86400, 604800):
            receipt = w.run([sys.executable, str(w.scripts / 'prepare_cleanup.py'), 'retention', '--task-json', str(path), '--floor-seconds', str(floor)])
            w.check((receipt['exit_code'] == 0) == (number == 5), 'retention_cli_strict:' + str(number) + ':' + str(floor))
    row = w.candidate('TASK-strict', '2026-01-01T00:00:00')
    receipt = w.run(['bash', str(w.scripts / 'run_cleanup_candidate.sh'), row['candidate'], row['containing']])
    w.check(receipt['exit_code'] == 2 and json.loads(receipt['stdout'])['reason'] == 'gate:retention-age', 'full_candidate_naive_retention_refusal')
    w.records['TASK-strict']['completed_at'] = '2026-01-01T00:00:00Z'
    w.sync()
    receipt = w.run(['bash', str(w.scripts / 'run_cleanup_candidate.sh'), row['candidate'], row['containing']])
    w.check(receipt['exit_code'] == 2 and json.loads(receipt['stdout'])['reason'] != 'gate:retention-age', 'aware_old_advances_to_blocked_join_no_scanner')
    row['listing']['completed_at'] = '2026-01-01T00:00:00Z'
    for number, change in enumerate(({'completed_at': '2026-01-01T00:00:01Z'}, {'status': 'failed'}, {'assigned_agent': 'other'})):
        w.records['TASK-strict'] = dict(row['listing'], **change)
        w.sync()
        receipt, prep = w.prepare(w.inventory([row]), 'changed-' + str(number))
        w.check(receipt['exit_code'] != 0 and not prep['manifest'], 'changed_snapshot_refused:' + str(number))
    scratch = w.copy_scripts('naive-regression')
    path = scratch / 'prepare_cleanup.py'
    text = path.read_text()
    at = text.index('\ndef retention_eligible')
    text = text[:at] + '''\n_strict_aware = aware_instant
def aware_instant(value):
    if isinstance(value,str) and len(value)==19:
        return dt.datetime.fromisoformat(value).replace(tzinfo=dt.timezone.utc)
    return _strict_aware(value)
''' + text[at:]
    path.write_text(text)
    w.records['TASK-strict'] = dict(row['listing'], completed_at='2026-01-01T00:00:00')
    w.sync()
    receipt = w.run(['bash', str(scratch / 'run_cleanup_candidate.sh'), row['candidate'], row['containing']])
    w.check(json.loads(receipt['stdout'])['reason'] != 'gate:retention-age', 'naive_coercion_regression_detected_by_full_candidate_oracle')
    w.missing.append('Exact preparation wall-clock equality is not scheduled; callable arithmetic equality only. Second action-time pass source inspected; witness never reaches scanner.')


def t3(w: Witness) -> None:
    rows = [w.candidate('TASK-cap-' + str(number)) for number in range(42)]
    w.records['TASK-cap-0']['completed_at'] = '2025-01-01T00:00:00Z'
    rows[0]['listing'] = dict(w.records['TASK-cap-0'])
    w.sync()
    inventory = w.inventory(rows)
    w.records['TASK-cap-0']['completed_at'] = '2025-01-01T00:00:01Z'
    w.sync()
    receipt, prep = w.prepare(inventory, 'cap')
    w.check(receipt['exit_code'] == 0 and len(prep['frozen_selection']) == 40 and len(prep['manifest']) == 39, 'frozen_cap_no_refill_after_changed_row')
    scratch = w.copy_scripts('cap-regression')
    w.mutate(scratch, 'prepare_cleanup.py', '[:max_candidates]', '[:max_candidates+1]')
    _, red = w.prepare(inventory, 'red-cap', scratch)
    w.check(len(red['frozen_selection']) == 41, 'cap_regression_detected')
    history = json.loads(w.history.read_text())
    history['excluded_paths'] = [rows[1]['containing']]
    w.write(w.history, history)
    young = dict(rows[2], listing=dict(rows[2]['listing'], completed_at=dt.datetime.now(dt.timezone.utc).isoformat()))
    unknown = dict(rows[3], listing=dict(rows[3]['listing'], completed_at=None))
    _, mixed = w.prepare(w.inventory([rows[1], young, unknown, rows[4]]), 'mixed')
    w.check(len(mixed['manifest']) == 1 and mixed['manifest'][0]['candidate'] == rows[4]['candidate'], 'mixed_useful_with_young_unknown_history_exclusions')
    _, empty = w.prepare(w.inventory([]), 'empty')
    w.check(empty['state'] == 'no_candidates_complete', 'honest_complete_empty')
    data = json.loads((w.root / 'inventory.json').read_text()); data['repository_receipts'][0]['exit_code'] = 17
    path = w.write(w.root / 'bad-inventory.json', data)
    receipt, bad = w.prepare(path, 'bad-inventory')
    w.check(receipt['exit_code'] != 0 and bad['state'] == 'incomplete' and not bad['manifest'], 'failed_inventory_not_empty')
    history['complete'] = False; w.write(w.history, history)
    receipt, bad = w.prepare(w.inventory([]), 'bad-history')
    w.check(receipt['exit_code'] != 0 and bad['state'] == 'incomplete', 'incomplete_history_not_empty')
    history['complete'] = True; w.write(w.history, history)
    cache = Path(rows[1]['containing']) / 'node_modules'; cache.mkdir()
    cache_row = dict(rows[1], candidate=str(cache), kind='cache')
    _, overlap = w.prepare(w.inventory([cache_row, rows[4]]), 'cache-overlap')
    w.check(len(overlap['manifest']) == 1 and overlap['rows'][0]['reason'] == 'historical_overlap', 'historical_containing_cache_overlap')
    empty_preparation = w.root / 'empty/preparation.json'
    receipt = w.run([sys.executable, str(w.scripts / 'observe_cleanup_batch.py'), 'job-request', '--preparation', str(empty_preparation), '--output', str(w.root / 'empty-request.json'), '--evidence-dir', str(w.workspace / 'output/TASK-actor/empty-request')])
    w.check(receipt['exit_code'] == 0 and not (w.root / 'empty-request.json').exists(), 'honest_empty_no_request')
    receipt = w.run([sys.executable, str(w.scripts / 'observe_cleanup_batch.py'), 'job-request', '--preparation', str(w.root / 'bad-inventory/preparation.json'), '--output', str(w.root / 'bad-request.json'), '--evidence-dir', str(w.workspace / 'output/TASK-actor/bad-request')])
    w.check(receipt['exit_code'] != 0 and not (w.root / 'bad-request.json').exists(), 'incomplete_inventory_no_request')
    scratch_empty = w.copy_scripts('empty-fallthrough-regression')
    w.mutate(scratch_empty, 'prepare_cleanup.py', "report['state'] = 'incomplete'", "report['state'] = 'no_candidates_complete'")
    receipt, red_empty = w.prepare(path, 'red-empty-fallthrough', scratch_empty)
    w.check(receipt['exit_code'] == 0 and red_empty['state'] == 'no_candidates_complete', 'empty_fallthrough_regression_detected')
    scratch_refill = w.copy_scripts('refill-regression')
    w.mutate(scratch_refill, 'prepare_cleanup.py', 'for _, candidate, row in selected:', 'for _, candidate, row in selected + [item for item in candidates if item not in selected][:1]:')
    inventory = w.inventory(rows)
    w.records['TASK-cap-0']['completed_at'] = '2025-01-01T00:00:01Z'; w.sync()
    _, refilled = w.prepare(inventory, 'red-refill', scratch_refill)
    w.check(any(item['candidate'] not in refilled['frozen_selection'] for item in refilled['manifest']), 'refill_regression_detected_literal_candidate_outside_frozen_set')


def o1(w: Witness) -> None:
    manifest = w.write(w.root / 'manifest.json', [])
    (w.fixtures / 'findmnt').rename(w.fixtures / 'findmnt-disabled')
    receipt = w.run([sys.executable, str(w.scripts / 'observe_cleanup_batch.py'), 'observe', '--manifest', str(manifest),
                     '--history-exclusions', str(w.history), '--evidence-dir', str(w.root / 'observe-native'), '--phase', 'before'])
    observation = json.loads((w.root / 'observe-native/before.json').read_text())
    if observation['context']['complete'] is not True:
        w.missing.append('Supported host context unavailable: ' + json.dumps(observation['context']['errors']))
        return
    w.check(receipt['exit_code'] == 0, 'actual_supported_host_native_mount_observation')
    paths = {row['path'] for row in observation['protected']['metadata']}
    w.check(str(w.workspace / '.codex') in paths and str(w.workspace / '.aws') in paths, 'workspace_codex_aws_metadata_included')
    reads = observation['context']['reads']
    if reads['root_pid_ns']['error'] or reads['root_mnt_ns']['error']:
        w.check(observation['complete'], 'natural_root_namespace_denial_is_diagnostic')
    else:
        w.missing.append('Natural root namespace denial venue absent; no elevation or namespace change attempted.')
    if reads['root_pid_ns']['error'] or reads['root_mnt_ns']['error']:
        scratch = w.copy_scripts('root-namespace-veto-regression')
        w.mutate(scratch, 'observe_cleanup_batch.py', "    return {'uid': os.getuid()", "    if reads['root_pid_ns']['error'] or reads['root_mnt_ns']['error']:\n        errors.append('regressed_root_namespace_veto')\n    return {'uid': os.getuid()")
        receipt = w.run([sys.executable, str(scratch / 'observe_cleanup_batch.py'), 'observe', '--manifest', str(manifest), '--history-exclusions', str(w.history), '--evidence-dir', str(w.root / 'red-root-veto'), '--phase', 'before'])
        w.check(receipt['exit_code'] != 0, 'root_namespace_veto_regression_detected_in_actual_host')
    w.missing.append('Unavailable-self-context denial-specific supported venue remains missing; no namespace/privilege change authorized.')


def o2(w: Witness) -> None:
    row = w.candidate('TASK-baseline', dt.datetime.now(dt.timezone.utc).isoformat())
    manifest = w.write(w.root / 'manifest.json', [{key: row[key] for key in ('candidate', 'containing', 'kind', 'allocated_bytes')}])
    for name, fault in (('findmnt-failure', {'exit_code': 17, 'stdout': '', 'stderr': 'controlled mount failure'}),
                        ('malformed-mount', {'exit_code': 0, 'stdout': '{}', 'stderr': ''})):
        w.replies['findmnt'] = {'default': fault}
        receipt, seq = w.observed(manifest, name)
        w.check(receipt['exit_code'] != 0 and seq.get('batch_started') is False
                and not (w.root / name / 'journal.jsonl').exists(), 'baseline_zero_batch_journal:' + name)
    blocked = w.root / 'blocked'
    blocked.mkdir(); (blocked / 'secret').mkdir(); blocked.chmod(0)
    (w.workspace / '.aws').rmdir(); (w.workspace / '.aws').symlink_to(blocked / 'secret')
    w.replies['findmnt'] = {'default': w.native_mount}
    try:
        receipt, seq = w.observed(manifest, 'denied-parent')
        w.check(receipt['exit_code'] != 0 and seq.get('batch_started') is False, 'unreadable_protected_parent_zero_batch')
    finally:
        blocked.chmod(0o700)
    (w.workspace / '.aws').unlink(); (w.workspace / '.aws').mkdir()
    scratch = w.copy_scripts('baseline-admission-regression')
    w.mutate(scratch, 'observe_cleanup_batch.py', "    if before['complete']:", '    if True:')
    w.replies['findmnt'] = {'default': {'exit_code': 17, 'stdout': '', 'stderr': 'controlled baseline failure'}}
    receipt, seq = w.observed(manifest, 'red-baseline-admission', scratch)
    w.check(seq['batch_started'] is True and (w.root / 'red-baseline-admission/journal.jsonl').is_file(), 'baseline_admission_regression_detected_by_forbidden_native_journal')


def o3(w: Witness) -> None:
    row = w.candidate('TASK-refusal', dt.datetime.now(dt.timezone.utc).isoformat())
    manifest = w.write(w.root / 'manifest.json', [{key: row[key] for key in ('candidate', 'containing', 'kind', 'allocated_bytes')}])
    native_sequences = []
    variants = [('clean', manifest, {'default': w.native_mount}),
                ('post-failure', manifest, {'default': w.native_mount, '2': {'exit_code': 17, 'stdout': '', 'stderr': 'controlled after failure'}}),
                ('batch-failure', w.write(w.root / 'bad-manifest.json', [{'bad': 'schema'}]), {'default': w.native_mount}),
                ('both-failures', w.root / 'bad-manifest.json', {'default': w.native_mount, '2': {'exit_code': 17, 'stdout': '', 'stderr': 'controlled after failure'}}),
                ('protected-drift', manifest, {'default': w.native_mount, '2': dict(w.native_mount, drift=str(w.workspace / '.codex'))})]
    for name, path, replies in variants:
        w.replies['findmnt'] = replies
        receipt, seq = w.observed(path, name)
        if seq.get('batch_started') is not True:
            w.missing.append('O3 requires supported host context; baseline refused: ' + json.dumps(seq.get('baseline')))
            return
        w.check((receipt['exit_code'] == 0) == (name == 'clean'), 'sequence_exit:' + name)
        if name in ('clean', 'post-failure', 'protected-drift'):
            journal = [json.loads(line) for line in (w.root / name / 'journal.jsonl').read_text().splitlines()]
            w.check(len(journal) == 1 and journal[0]['receipt']['reason'] == 'gate:retention-age', 'actual_native_refusal_journal:' + name)
            w.check(seq['batch']['exit_code'] == 0, 'batch_success_preserved:' + name)
        if name == 'both-failures':
            w.check(seq['batch']['exit_code'] != 0 and not seq['postcheck']['complete'], 'both_native_causes_preserved')
        native_sequences.append(seq)
    scratch = w.copy_scripts('exit-masking-regression')
    w.mutate(scratch, 'observe_cleanup_batch.py', 'return 0 if sequence[\'complete\'] else 2', "return sequence['batch']['exit_code'] if sequence['batch_started'] else 2")
    w.replies['findmnt'] = {'default': w.native_mount, '2': {'exit_code': 17, 'stdout': '', 'stderr': 'controlled after failure'}}
    receipt, seq = w.observed(manifest, 'red-exit-masking', scratch)
    w.check(receipt['exit_code'] == 0 and seq['complete'] is False, 'post_failure_masking_regression_detected')
    mounts = json.loads(w.native_mount['stdout'])
    mounts['filesystems'][0]['source'] += '-controlled-drift'
    w.replies['findmnt'] = {'default': w.native_mount, '2': dict(w.native_mount, stdout=json.dumps(mounts))}
    receipt, seq = w.observed(manifest, 'mount-drift')
    w.check(receipt['exit_code'] != 0 and 'protected_changed:findmnt' in seq['postcheck']['errors'], 'controlled_mount_drift_nonzero')
    missing_output = w.root / 'missing-post'
    w.replies['findmnt'] = {'default': w.native_mount, '2': dict(w.native_mount, deny=str(missing_output))}
    try:
        receipt, seq = w.observed(manifest, 'missing-post')
    finally:
        missing_output.chmod(0o700)
    w.check(receipt['exit_code'] != 0 and not (missing_output / 'after.json').exists() and not seq
            and (missing_output / 'journal.jsonl').is_file(), 'native_post_receipt_write_failure_nonzero_and_missing_retained')
    w.missing.append('O3 proves sequencing with native refusals, never deletion or scanner authority.')


def j1(w: Witness) -> None:
    row = w.candidate('TASK-request')
    _, prep = w.prepare(w.inventory([row]), 'prepared')
    prep_path = w.root / 'prepared/preparation.json'
    def request(name: str, hint: str | None = None, budget: str = '300') -> dict[str, Any]:
        argv = [sys.executable, str(w.scripts / 'observe_cleanup_batch.py'), 'job-request', '--preparation', str(prep_path),
                '--output', str(w.root / (name + '.json')), '--evidence-dir', str(w.workspace / 'output/TASK-actor' / name),
                '--deadline-seconds', '60', '--candidate-timeout-seconds', '60', '--max-runtime-seconds', budget]
        if hint is not None:
            argv += ['--cwd-hint', hint]
        return w.run(argv)
    for name, hint in (('absolute', str(w.workspace)), ('traversal', '../'), ('embedded', 'repos/../repos')):
        receipt = request(name, hint)
        w.check(receipt['exit_code'] != 0 and not (w.root / (name + '.json')).exists() and not w.sink.exists(), 'invalid_request_zero_submit:' + name)
    w.check(request('budget', budget='120')['exit_code'] != 0, 'insufficient_budget_refused')
    for name, hint in (('omitted', None), ('relative', 'repos/fixture')):
        receipt = request(name, hint)
        w.check(receipt['exit_code'] == 0, 'supported_render:' + name)
        value = json.loads((w.root / (name + '.json')).read_text())
        code = "from runtime.daemon.routes.jobs import SubmitBody,_validate_cwd_hint; import json,sys; body=SubmitBody.model_validate(json.load(sys.stdin)); _validate_cwd_hint(body.cwd_hint); print('shipping validators accepted')"
        receipt = w.run(['uv', 'run', '--frozen', 'python', '-c', code], cwd=w.source,
                        stdin=json.dumps(value))
        w.check(receipt['exit_code'] == 0, 'actual_shipping_SubmitBody_cwd_validator:' + name)
    receipt = w.run(['happyranch', 'jobs', 'submit', '--org', 'fixture', '--from-file', str(w.root / 'omitted.json')])
    w.check(receipt['exit_code'] == 17 and len(w.sink.read_text().splitlines()) == 1, 'one_rejected_submit_no_retry')
    valid = json.loads((w.root / 'omitted.json').read_text())
    bound = {key: w.env[key] for key in ('WORKSPACE', 'PRIMARY', 'AGENT', 'ORG', 'ACTING_TASK', 'SESSION_ID', 'CLEANUP_MARKER')}
    for name, change in (('nonstring', {'cwd_hint': 1}), ('unknown', {'max_output_bytes': 1}),
                         ('interpreter', {'interpreter': 'python3'}), ('sid', {'session_id': 'wrong'}),
                         ('runner', {'runner': '/alternate'}), ('boolean', {'persistent': 1})):
        value = dict(valid, **change)
        code = "import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from observe_cleanup_batch import validate_job_request; data=json.load(sys.stdin); validate_job_request(data['request'],data['binding'],Path(data['workspace']))"
        receipt = w.run([sys.executable, '-c', code, str(w.scripts)], stdin=json.dumps({'request': value, 'binding': bound, 'workspace': str(w.workspace)}))
        w.check(receipt['exit_code'] != 0, 'closed_helper_request_negative:' + name)
    for hint in ('/absolute', '../', 'repos/../repos'):
        value = dict(valid, cwd_hint=hint)
        code = "from runtime.daemon.routes.jobs import SubmitBody,_validate_cwd_hint; import json,sys; body=SubmitBody.model_validate(json.load(sys.stdin)); _validate_cwd_hint(body.cwd_hint)"
        receipt = w.run(['uv', 'run', '--frozen', 'python', '-c', code], cwd=w.source, stdin=json.dumps(value))
        w.check(receipt['exit_code'] != 0, 'actual_shipping_cwd_validator_negative:' + hint)
    scratch = w.copy_scripts('cwd-validation-regression')
    w.mutate(scratch, 'observe_cleanup_batch.py', '    if hint is not None and (', '    if False and (')
    receipt = w.run([sys.executable, str(scratch / 'observe_cleanup_batch.py'), 'job-request', '--preparation', str(prep_path), '--output', str(w.root / 'red-cwd.json'), '--evidence-dir', str(w.workspace / 'output/TASK-actor/red-cwd'), '--deadline-seconds', '60', '--candidate-timeout-seconds', '60', '--max-runtime-seconds', '300', '--cwd-hint', '/absolute'])
    w.check(receipt['exit_code'] == 0 and (w.root / 'red-cwd.json').exists() and len(w.sink.read_text().splitlines()) == 1, 'cwd_validation_regression_detected_no_extra_submission')
    w.missing.append('No real server session authorization/callback behavior follows from isolated request fixtures.')


def p1(w: Witness) -> None:
    inventory_path = w.inventory([])
    for name, task, expected in (
        ('failed', {'status': 'failed', 'executor_exit_code': 1, 'output_dir': None}, 'failed_unpublished'),
        ('pending', {'status': 'in_progress', 'output_dir': None}, 'result_admitted_terminal_pending'),
        ('active-empty', {'status': 'in_progress', 'output_dir': None}, 'local_complete_unpublished'),
        ('empty', {'status': 'completed', 'executor_exit_code': 0, 'output_dir': 'output/TASK-actor'}, 'published_empty')):
        args = [sys.executable, str(w.scripts / 'observe_cleanup_batch.py'), 'report', '--inventory', str(inventory_path)]
        for key, value in (('sequence', {}), ('task-projection', task), ('result-projection', {'result_id': None if name == 'active-empty' else 1, 'payload': {'status': 'completed'}}),
                           ('audit-projection', {'completion_report': name == 'empty'})):
            args += ['--' + key, str(w.write(w.root / (key + '.json'), value))]
        args += ['--output-dir', str(w.root / name)]
        receipt = w.run(args)
        value = json.loads((w.root / name / 'publication.json').read_text())
        w.check(value['state'] == expected, 'authoritative_publication:' + name)
        w.check((receipt['exit_code'] == 0) == (name != 'failed'), 'publication_cli_exit:' + name)
    scratch = w.copy_scripts('prefer-local-regression')
    w.mutate(scratch, 'observe_cleanup_batch.py', "state = 'failed_unpublished' if not published else 'failed'", "state = 'published_empty'")
    args[1] = str(scratch / 'observe_cleanup_batch.py')
    task = w.write(w.root / 'task-projection.json', {'status': 'failed', 'executor_exit_code': 1, 'output_dir': None})
    args[-1] = str(w.root / 'red-local-success')
    receipt = w.run(args)
    w.check(json.loads((w.root / 'red-local-success/publication.json').read_text())['state'] == 'published_empty', 'prefer_local_success_regression_detected')
    inventory = json.loads(inventory_path.read_text())
    inventory['repositories_state'] = 'absent'
    # A genuinely empty configured-repository view is witnessed separately in
    # a new disposable directory, never inferred from a failed Git listing.
    no_repo_workspace = w.root / 'no-repo-workspace'; no_repo_workspace.mkdir()
    w.env.update(WORKSPACE=str(no_repo_workspace), PRIMARY='')
    inventory['binding'] = {key: w.env[key] for key in inventory['binding']}
    inspection = w.run([sys.executable, '-c', "import os,sys; assert os.listdir(sys.argv[1])==[]; assert not os.path.lexists(sys.argv[1]+'/repos'); print('repository directory positively absent under inspected parent')", str(no_repo_workspace)])
    inventory['repository_receipts'] = [inspection]
    w.write(inventory_path, inventory)
    w.write(w.root / 'task-projection.json', {'status': 'completed', 'executor_exit_code': 0, 'output_dir': 'output/TASK-actor'})
    args[1] = str(w.scripts / 'observe_cleanup_batch.py'); args[-1] = str(w.root / 'no-repo')
    receipt = w.run(args)
    value = json.loads((w.root / 'no-repo/publication.json').read_text())
    w.check(receipt['exit_code'] == 0 and value['state'] == 'published_empty' and not value['sequence'], 'native_empty_repository_receipt_no_host_baseline')
    inventory['repository_receipts'] = []; w.write(inventory_path, inventory); args[-1] = str(w.root / 'missing-native')
    receipt = w.run(args)
    w.check(receipt['exit_code'] != 0 and json.loads((w.root / 'missing-native/publication.json').read_text())['state'] == 'incomplete_unpublished', 'missing_native_provenance_not_empty_success')
    w.missing.append('Synthetic task/result/audit projections never prove original callback/runtime behavior; Product18963 original callback command/HTTP/exit/error remain missing.')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--case', choices=CASES, required=True)
    parser.add_argument('--source-root', type=Path, required=True)
    parser.add_argument('--disposable-root', type=Path)
    parser.add_argument('--evidence-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    source = args.source_root.resolve(strict=True)
    args.evidence_dir.mkdir(parents=True, exist_ok=True)
    if any(args.evidence_dir.iterdir()):
        parser.error('evidence directory must be empty')
    if args.disposable_root:
        root = args.disposable_root
        if not root.is_absolute() or root.parent != Path('/tmp') or root.exists():
            parser.error('disposable root must be a new immediate /tmp child')
        root.mkdir(mode=0o700)
    else:
        root = Path(tempfile.mkdtemp(prefix='happyranch-cleanup-witness-', dir='/tmp'))
    value = root.lstat()
    if value.st_uid != os.getuid() or stat.S_IMODE(value.st_mode) != 0o700:
        parser.error('scratch ownership/mode mismatch')
    identity = (value.st_dev, value.st_ino)
    evidence: dict[str, Any] = {'case': args.case, 'scratch': str(root), 'identity': identity,
                               'python': sys.executable, 'version': sys.version, 'status': 'failed',
                               'four_answers': ['Observable named CLI/manifest/journal/publication assertions.',
                                                'Credible lexical/coercion/cap/admission/exit/publication regression or explicitly missing control.',
                                                'Existing scanner/batch tests cover different boundaries; retired units unused.',
                                                'Shipping subprocesses and external declarative commands; no production test seam.']}
    witness = None
    try:
        witness = Witness(source, root, args.evidence_dir)
        globals()[args.case.lower()](witness)
        evidence['status'] = 'executed_with_gaps' if witness.missing else 'executed'
    except (OSError, ValueError, AssertionError, subprocess.TimeoutExpired) as exc:
        evidence['error'] = str(exc)
    finally:
        if witness is not None:
            evidence.update(commands=witness.commands, assertions=witness.assertions, missing=witness.missing,
                            helper_sha256={path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                                           for path in witness.scripts.iterdir() if path.is_file()})
        # Preserve source copies, setup inputs and raw observation/journal artifacts
        # in the evidence directory; remove only this fresh recorded root.
        shutil.copytree(root, args.evidence_dir / 'disposable-artifacts', symlinks=True)
        value = root.lstat()
        if (value.st_dev, value.st_ino) == identity and value.st_uid == os.getuid():
            shutil.rmtree(root)
            evidence['scratch_removed'] = True
        else:
            evidence['scratch_removed'] = False
        (args.evidence_dir / 'evidence.json').write_text(json.dumps(evidence, indent=2) + '\n')
    print(json.dumps({key: evidence[key] for key in ('case', 'status', 'scratch_removed')}))
    return 1 if evidence['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
