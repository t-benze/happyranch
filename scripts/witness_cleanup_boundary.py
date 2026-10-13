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

CASES = ('T1', 'T2', 'T3', 'O1', 'O2', 'O3', 'J1', 'P1', 'R1', 'R2', 'R3')
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
 if item.get('block_file'): pathlib.Path(item['block_file']).mkdir()
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
        fallback = json.loads(receipt['stdout']) if receipt['stdout'].strip() else None
        sequence = (fallback['sequence'] if fallback and fallback.get('kind') == 'cleanup_sequence_fallback'
                    else json.loads(sequence_path.read_text()) if sequence_path.is_file() else {})
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
    empty_preparation = w.root / 'empty/preparation.json'
    receipt = w.run([sys.executable, str(w.scripts / 'observe_cleanup_batch.py'), 'job-request', '--preparation', str(empty_preparation), '--output', str(w.root / 'empty-request.json'), '--evidence-dir', str(w.workspace / 'output/TASK-actor/empty-request')])
    w.check(receipt['exit_code'] == 0 and not (w.root / 'empty-request.json').exists(), 'honest_empty_no_request')
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
    w.mutate(scratch, 'observe_cleanup_batch.py', "    if sequence['baseline']['complete']:", '    if True:')
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
    w.check(receipt['exit_code'] != 0 and not (missing_output / 'after.json').exists() and seq.get('unknown_postcondition') is True and seq.get('batch', {}).get('exit_code') == 0
            and seq.get('write_errors') and (missing_output / 'journal.jsonl').is_file(),
            'native_post_receipt_write_failure_retains_batch_and_sequence')
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


def report_case(w: Witness, inventory: Path, sequence: dict[str, Any] | Path, name: str,
                *, preparation: Path | None = None, scripts: Path | None = None,
                task: dict[str, Any] | None = None, result_id: int | None = 71) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    argv = [sys.executable, str((scripts or w.scripts) / 'observe_cleanup_batch.py'), 'report', '--inventory', str(inventory)]
    for key, value in (('sequence', sequence), ('task-projection', task or {'status': 'in_progress', 'output_dir': None}),
                       ('result-projection', {'result_id': result_id}), ('audit-projection', {'completion_report': False})):
        argv += ['--' + key, str(value if isinstance(value, Path) else w.write(w.root / (name + '-' + key + '.json'), value))]
    if preparation is not None:
        argv += ['--preparation', str(preparation)]
    output = w.root / name
    argv += ['--output-dir', str(output)]
    receipt = w.run(argv)
    return (receipt, json.loads((output / 'publication.json').read_text()),
            json.loads((output / 'completion-intent.json').read_text()))


def r1(w: Witness) -> None:
    # P1/R1: admitted result with incomplete inventory, baseline or postcheck.
    row = w.candidate('TASK-evidence')
    inventory_path = w.inventory([row])
    inventory = json.loads(inventory_path.read_text())
    native = inventory['repository_receipts'][0]
    complete = {'complete': True, 'batch_started': True, 'baseline': {'complete': True},
                'batch': native, 'postcheck': {'complete': True}}
    cases = [('missing-inventory', dict(inventory, repository_receipts=[]), {}),
             ('failed-baseline', inventory, {'complete': False, 'batch_started': False, 'baseline': {'complete': False}}),
             ('missing-postcheck', inventory, dict(complete, postcheck={})),
             ('failed-postcheck', inventory, dict(complete, complete=False, postcheck={'complete': False})),
             ('incomplete-inventory', dict(inventory, complete=False), complete)]
    for name, census, sequence in cases:
        w.write(inventory_path, census)
        receipt, value, intent = report_case(w, inventory_path, sequence, name)
        w.check(receipt['exit_code'] != 0 and value['state'] == 'incomplete_unpublished'
                and intent['status'] == 'blocked', 'R1_incomplete_evidence_blocks:' + name)
        w.check(value['local_result_id'] == 71 and value['result_admitted_terminal_pending'] is True,
                'R1_pending_fact_retained:' + name)
    w.write(inventory_path, inventory)
    receipt, value, intent = report_case(w, inventory_path, complete, 'complete-pending')
    w.check(receipt['exit_code'] == 0 and value['state'] == 'result_admitted_terminal_pending'
            and intent['status'] == 'completed', 'R1_complete_pending_keeper')
    receipt, value, intent = report_case(w, inventory_path, complete, 'failed-lifecycle',
                                        task={'status': 'failed', 'executor_exit_code': 1, 'output_dir': None})
    w.check(receipt['exit_code'] != 0 and value['state'] == 'failed_unpublished'
            and intent['status'] == 'blocked', 'R1_failed_lifecycle_keeper')
    w.missing.append('P1/R1 projections do not prove sent callback, terminal execution or canonical publication.')


def r2(w: Witness) -> None:
    # T3/P1/R2: native nonempty inventory, verified known exclusions, zero action.
    rows = []
    young = w.candidate('TASK-young', dt.datetime.now(dt.timezone.utc).isoformat()); rows.append(young)
    active = w.candidate('TASK-active'); active['listing']['status'] = 'in_progress'; rows.append(active)
    foreign = w.candidate('TASK-foreign'); foreign['listing']['assigned_agent'] = 'another'; rows.append(foreign)
    historical = w.candidate('TASK-historical'); rows.append(historical)
    history = json.loads(w.history.read_text()); history['excluded_paths'] = [historical['candidate']]; w.write(w.history, history)
    inventory = w.inventory(rows)
    receipt, prep = w.prepare(inventory, 'excluded-preparation')
    w.check(receipt['exit_code'] == 0 and prep['state'] == 'no_candidates_complete' and prep['manifest'] == [], 'R2_known_excluded_prepared')
    preparation = w.root / 'excluded-preparation/preparation.json'
    originals = {path: path.read_bytes() for path in
                 (inventory, preparation, Path(prep['manifest_path']), w.history)}
    request = w.root / 'excluded-request.json'
    receipt = w.run([sys.executable, str(w.scripts / 'observe_cleanup_batch.py'), 'job-request',
                     '--preparation', str(preparation), '--output', str(request),
                     '--evidence-dir', str(w.workspace / 'output/TASK-actor/no-action')])
    w.check(receipt['exit_code'] == 0 and json.loads(receipt['stdout'])['request_emitted'] is False
            and not request.exists(), 'R2_verified_exclusions_zero_request')
    receipt, value, intent = report_case(w, inventory, {}, 'excluded-report', preparation=preparation, result_id=None)
    w.check(receipt['exit_code'] == 0 and value['state'] == 'local_complete_unpublished'
            and value['no_action_complete'] is True and intent['status'] == 'completed', 'R2_verified_exclusions_complete')
    w.check(value['inventory']['candidates'] == rows and value['preparation']['rows'] == prep['rows']
            and value['sequence'] == {} and not w.sink.exists(), 'R2_original_receipts_zero_batch_request')
    for name, change in (('binding', {'binding': dict(prep['binding'], SESSION_ID='wrong')}),
                         ('state-only', {'rows': []}), ('errors', {'errors': ['unknown']}),
                         ('manifest', {'manifest': [{'unknown': True}]}), ('frozen', {'frozen_selection': ['unknown']}),
                         ('missing-owner', {'receipts': []}), ('time', {'observed_at': '2027-01-01T00:00:00Z'})):
        bad = w.write(w.root / (name + '-preparation.json'), dict(prep, **change))
        # The verified inventory snapshot resides beside the original receipt.
        bad = w.write(preparation, json.loads(bad.read_text()))
        receipt, value, intent = report_case(w, inventory, {}, 'bad-' + name, preparation=bad)
        w.check(receipt['exit_code'] != 0 and intent['status'] == 'blocked', 'R2_invalid_preparation_refused:' + name)
        w.write(preparation, prep)
    def keeper(name: str, scripts: Path | None = None) -> None:
        for path, original in originals.items():
            w.check(path.read_bytes() == original, 'R2_original_bytes_valid:' + name + ':' + path.name)
        snapshot = preparation.parent / 'inventory.json'
        for path, key in ((inventory, 'inventory_sha256'), (snapshot, 'inventory_snapshot_sha256'),
                          (Path(prep['manifest_path']), 'manifest_sha256'), (w.history, 'history_sha256')):
            w.check(hashlib.sha256(path.read_bytes()).hexdigest() == prep[key],
                    'R2_digest_valid:' + name + ':' + key)
        receipt, value, intent = report_case(w, inventory, {}, name, preparation=preparation, scripts=scripts)
        w.check(receipt['exit_code'] == 0 and value['no_action_complete'] is True
                and value['evidence_errors'] == [] and intent['status'] == 'completed',
                'R2_isolated_valid_keeper:' + name)

    changed_inventory = json.loads(originals[inventory])
    changed_inventory['candidates'][0]['listing']['status'] = 'failed'
    faults = (('inventory', inventory, changed_inventory),
              ('manifest', Path(prep['manifest_path']), [{'bad': True}]),
              ('history', w.history, originals[w.history] + b' \n'))
    for name, path, changed in faults:
        keeper('before-' + name)
        try:
            if isinstance(changed, bytes):
                path.write_bytes(changed)
            else:
                w.write(path, changed)
            receipt, value, intent = report_case(w, inventory, {}, 'changed-' + name, preparation=preparation)
            cause = 'preparation_artifact_changed'
            w.check(receipt['exit_code'] != 0 and intent['status'] == 'blocked'
                    and value['evidence_errors'] == [cause], 'R2_changed_' + name + '_refused')
        finally:
            path.write_bytes(originals[path])
        keeper('restored-' + name)

    # Preserve the substantive changed-exclusion negative as well as the byte
    # digest fault. The causal history mutant below changes only whitespace,
    # keeping semantic exclusions and every independent guard valid.
    keeper('before-history-content')
    try:
        w.write(w.history, dict(history, excluded_paths=[]))
        receipt, value, intent = report_case(w, inventory, {}, 'changed-history-content', preparation=preparation)
        w.check(receipt['exit_code'] != 0 and intent['status'] == 'blocked'
                and value['evidence_errors'] == ['preparation_artifact_changed'], 'R2_changed_history_content_refused')
    finally:
        w.history.write_bytes(originals[w.history])
    keeper('restored-history-content')

    # Independently remove ONLY each selected validation in a disposable copy.
    # Every other input is valid; the SAME named refusal assertion must now fail.
    for name, path, changed in faults[1:]:
        scratch = w.copy_scripts('R2-' + name + '-validation-control')
        before = (scratch / 'observe_cleanup_batch.py').read_bytes()
        if name == 'manifest':
            w.mutate(scratch, 'observe_cleanup_batch.py',
                     "            or read_json(manifest) != preparation['manifest']\n            or digest(manifest) != preparation['manifest_sha256']\n", '')
        else:
            w.mutate(scratch, 'observe_cleanup_batch.py',
                     "            or digest(history) != preparation['history_sha256']", '')
        try:
            keeper('control-before-' + name, scratch)
            if isinstance(changed, bytes):
                path.write_bytes(changed)
            else:
                w.write(path, changed)
            receipt, value, intent = report_case(w, inventory, {}, 'control-changed-' + name,
                                                preparation=preparation, scripts=scratch)
            named_assertion = ('R2_changed_' + name + '_refused')
            detected = (receipt['exit_code'] != 0 and intent['status'] == 'blocked'
                        and value['evidence_errors'] == ['preparation_artifact_changed'])
            w.write(w.root / ('R2-' + name + '-control-result.json'),
                    {'named_assertion': named_assertion, 'assertion_met': detected,
                     'receipt': receipt, 'publication': value, 'intent': intent,
                     'verification_level': 'shipping report subprocess over disposable single-validation mutant'})
            w.check(not detected and receipt['exit_code'] == 0 and value['no_action_complete'] is True,
                    'R2_named_assertion_fails_without_' + name + '_validation')
        finally:
            path.write_bytes(originals[path])
            (scratch / 'observe_cleanup_batch.py').write_bytes(before)
        keeper('control-restored-' + name, scratch)
    # Unknown/exhausted receipt never becomes known empty.
    unknown = w.candidate('TASK-unknown', 'naive-or-unknown')
    inventory = w.inventory([unknown]); receipt, prep = w.prepare(inventory, 'unknown-preparation')
    receipt, value, intent = report_case(w, inventory, {}, 'unknown-report', preparation=w.root / 'unknown-preparation/preparation.json')
    w.check(receipt['exit_code'] != 0 and intent['status'] == 'blocked', 'R2_unknown_exhausted_refused')
    inventory = w.inventory([])
    receipt, value, intent = report_case(w, inventory, {}, 'literal-empty')
    w.check(receipt['exit_code'] == 0 and value['native_empty_inventory'], 'R2_literal_empty_keeper')
    useful = w.candidate('TASK-useful'); inventory = w.inventory([useful])
    receipt, prep = w.prepare(inventory, 'useful-preparation')
    w.check(receipt['exit_code'] == 0 and prep['state'] == 'prepared_nonempty' and len(prep['manifest']) == 1, 'R2_useful_preparation_keeper')


def r3(w: Witness) -> None:
    # O3/R3 actual default native batch, postwrite and final-sequence faults.
    row = w.candidate('TASK-write-refusal', dt.datetime.now(dt.timezone.utc).isoformat())
    manifest = w.write(w.root / 'manifest.json', [{key: row[key] for key in ('candidate', 'containing', 'kind', 'allocated_bytes')}])
    invalid = w.write(w.root / 'invalid-manifest.json', [{'bad': 'schema'}])
    for name, path, fault in (('postwrite-refusal', manifest, 'deny'), ('postwrite-batch-failure', invalid, 'deny'),
                              ('after-file-failure', manifest, 'after.json'), ('sequence-file-failure', manifest, 'sequence.json')):
        output = w.root / name
        change = {'deny': str(output)} if fault == 'deny' else {'block_file': str(output / fault)}
        w.replies['findmnt'] = {'default': w.native_mount, '2': dict(w.native_mount, **change)}
        try:
            receipt, sequence = w.observed(path, name)
        finally:
            if output.exists(): output.chmod(0o700)
        before = json.loads((output / 'before.json').read_text())
        if before.get('complete') is not True:
            w.missing.append('O3/R3 supported host baseline unavailable: ' + json.dumps(before.get('errors'))); return
        w.check(receipt['exit_code'] != 0 and sequence.get('complete') is False
                and sequence.get('unknown_postcondition') is True and sequence.get('write_errors'), 'R3_postwrite_nonzero_unknown:' + name)
        batch = sequence['batch']
        w.check(isinstance(batch['argv'], list) and batch['argv'][1].endswith('/run_cleanup_batch.py')
                and batch['cwd'] == str(w.workspace) and isinstance(batch['stdout'], str)
                and isinstance(batch['stderr'], str) and type(batch['exit_code']) is int, 'R3_native_batch_receipt_retained:' + name)
        w.check(sequence['binding']['SESSION_ID'] == w.env['SESSION_ID']
                and sequence['source_sha256']['observe_cleanup_batch.py']
                and sequence['manifest_sha256'] and sequence['history_sha256'], 'R3_source_binding_retained:' + name)
        if path == manifest:
            journal = [json.loads(line) for line in (output / 'journal.jsonl').read_text().splitlines()]
            w.check(batch['exit_code'] == 0 and len(journal) == 1 and journal[0]['receipt']['reason'] == 'gate:retention-age', 'R3_actual_native_refusal:' + name)
        else:
            w.check(batch['exit_code'] != 0 and not sequence['postcheck']['complete'], 'R3_batch_and_postwrite_causes_retained')
        if fault in ('deny', 'sequence.json'):
            fallback = json.loads(receipt['stdout'])
            w.check(fallback['kind'] == 'cleanup_sequence_fallback' and fallback['persisted'] is False
                    and fallback['sequence'] == sequence, 'R3_explicit_captured_fallback:' + name)
        else:
            w.check((output / 'sequence.json').is_file(), 'R3_durable_sequence_keeper')
        w.check(json.loads((output / 'batch.json').read_text()) == batch, 'R3_batch_persisted_before_postwrite:' + name)
    # R3.1: actual observer -> writer -> shipping report. Late faults occur
    # AFTER full success bytes exist, in disposable source only. A useful valid
    # preparation is retained; the fresh task then becomes too young so the
    # default native batch refuses before scanner/removal.
    useful = w.candidate('TASK-late-write')
    inventory = w.inventory([useful])
    receipt, prep = w.prepare(inventory, 'late-preparation')
    w.check(receipt['exit_code'] == 0 and prep['state'] == 'prepared_nonempty', 'R3_late_useful_valid_preparation')
    w.records['TASK-late-write']['completed_at'] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest = Path(prep['manifest_path'])
    w.replies['findmnt'] = {'default': w.native_mount}
    receipt, clean = w.observed(manifest, 'late-clean')
    if clean.get('baseline', {}).get('complete') is not True:
        w.missing.append('R3.1 full-observer host baseline unavailable'); return
    w.check(receipt['exit_code'] == 0 and clean['complete'] is True, 'R3_late_clean_observer_keeper')
    result, value, intent = report_case(w, inventory, w.root / 'late-clean/sequence.json', 'late-clean-report')
    w.check(result['exit_code'] == 0 and intent['status'] == 'completed', 'R3_late_clean_report_keeper')
    for fault in ('flush', 'fsync', 'close', 'link'):
        scratch = w.copy_scripts('late-' + fault + '-source')
        writer = scratch / 'prepare_cleanup.py'
        original = writer.read_bytes()
        # Fault implementation deliberately lives only in the scratch copy.
        injection = r"""
# DISPOSABLE fault injection, never part of the shipping writer.
_original_open = Path.open
_original_fsync = os.fsync
_original_link = os.link
_fault = FAULT_LITERAL
class _LateFile:
    def __init__(self, handle): self.handle = handle
    def __enter__(self): self.handle.__enter__(); return self
    def __exit__(self, *args):
        answer = self.handle.__exit__(*args)
        if _fault == 'close': raise OSError('controlled late close failure after success bytes')
        return answer
    def __getattr__(self, name): return getattr(self.handle, name)
    def flush(self):
        self.handle.flush()
        if _fault == 'flush': raise OSError('controlled late flush failure after success bytes')
def _late_open(path, *args, **kwargs):
    handle = _original_open(path, *args, **kwargs)
    return _LateFile(handle) if path.name == '.sequence.json.pending' and args[:1] == ('x',) else handle
def _late_fsync(fd):
    if _fault == 'fsync' and Path(os.readlink('/proc/self/fd/' + str(fd))).name == '.sequence.json.pending':
        raise OSError('controlled late fsync failure after success bytes')
    return _original_fsync(fd)
def _late_link(source, destination, *args, **kwargs):
    if _fault == 'link' and Path(destination).name == 'sequence.json':
        raise OSError('controlled publication link failure after success bytes')
    return _original_link(source, destination, *args, **kwargs)
Path.open = _late_open
os.fsync = _late_fsync
os.link = _late_link
""".replace('FAULT_LITERAL', repr(fault))
        writer.write_text(original.decode() + injection)
        name = 'late-' + fault
        try:
            receipt, sequence = w.observed(manifest, name, scratch)
            output = w.root / name
            fallback = json.loads(receipt['stdout'])
            w.check(receipt['exit_code'] != 0 and sequence['complete'] is False
                    and sequence['unknown_postcondition'] is True and fallback['persisted'] is False,
                    'R3_late_observer_blocks:' + fault)
            pending = output / '.sequence.json.pending'
            w.check(json.loads(pending.read_text())['complete'] is True and not (output / 'sequence.json').exists(),
                    'R3_late_success_bytes_never_canonical:' + fault)
            w.check(sequence['write_errors'][-1]['artifact'] == 'sequence.json'
                    and ('controlled late ' + fault if fault != 'link' else 'controlled publication link')
                    in sequence['write_errors'][-1]['error'], 'R3_late_native_write_failure_retained:' + fault)
            batch = sequence['batch']
            w.check(batch == json.loads((output / 'batch.json').read_text())
                    and batch['argv'][1] == str(scratch / 'run_cleanup_batch.py')
                    and batch['cwd'] == str(w.workspace) and batch['exit_code'] == 0
                    and batch['error'] is None and isinstance(batch['stdout'], str) and isinstance(batch['stderr'], str),
                    'R3_late_native_batch_receipt_retained:' + fault)
            journal = [json.loads(line) for line in (output / 'journal.jsonl').read_text().splitlines()]
            w.check(len(journal) == 1 and journal[0]['receipt']['reason'] == 'gate:retention-age',
                    'R3_late_default_batch_refuses_before_scanner:' + fault)
            captured = output / 'captured-fallback.json'
            captured.write_text(receipt['stdout'])
            result, value, intent = report_case(w, inventory, captured, name + '-report')
            w.check(result['exit_code'] != 0 and value['state'] == 'incomplete_unpublished'
                    and intent['status'] == 'blocked' and value['sequence'] == sequence
                    and value['evidence_errors'] == ['observer_write_failure_fallback'],
                    'R3_late_shipping_report_blocks_with_native_evidence:' + fault)
            w.check(value['local_result_id'] == 71 and value['result_admitted_terminal_pending'] is True
                    and value['sequence']['binding'] == prep['binding']
                    and value['sequence']['source_sha256']['observe_cleanup_batch.py']
                    and value['sequence']['batch'] == batch and value['sequence']['write_errors'],
                    'R3_late_report_preserves_binding_source_pending_and_both_causes:' + fault)
            result, value, intent = report_case(w, inventory, pending, name + '-pending-report')
            w.check(result['exit_code'] != 0 and intent['status'] == 'blocked'
                    and value['evidence_errors'] == ['sequence_unavailable:sequence_staging_not_published'],
                    'R3_late_pending_bytes_refused_by_report:' + fault)
            result, value, intent = report_case(w, inventory, output / 'sequence.json', name + '-missing-report')
            w.check(result['exit_code'] != 0 and intent['status'] == 'blocked'
                    and value['result_admitted_terminal_pending'] is True
                    and value['evidence_errors'][0].startswith('sequence_unavailable:'),
                    'R3_late_missing_canonical_blocks_independently:' + fault)
        finally:
            writer.write_bytes(original)
        w.check(writer.read_bytes() == original, 'R3_late_scratch_source_restored:' + fault)
    # An earlier readable success record stays byte-preserved on exclusive
    # publication refusal. Authentic captured failure still controls reporting.
    output = w.root / 'existing-success'
    output.mkdir()
    canonical = w.write(output / 'sequence.json', clean)
    original = canonical.read_bytes()
    receipt, sequence = w.observed(manifest, 'existing-success')
    captured = output / 'captured-fallback.json'; captured.write_text(receipt['stdout'])
    result, value, intent = report_case(w, inventory, captured, 'existing-success-report')
    w.check(receipt['exit_code'] != 0 and canonical.read_bytes() == original
            and json.loads(original)['complete'] is True and sequence['complete'] is False
            and result['exit_code'] != 0 and intent['status'] == 'blocked'
            and value['sequence']['batch'] == sequence['batch'] and value['sequence']['write_errors'],
            'R3_captured_failure_overrides_preserved_success_record')

    # Exclusive publication refuses pre-existing evidence and partial attempts.
    script = "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); from prepare_cleanup import write_json; write_json(Path(sys.argv[2]), {'complete':True})"
    for name, occupied in (('canonical', 'receipt.json'), ('pending', '.receipt.json.pending')):
        output = w.root / ('no-clobber-' + name); output.mkdir()
        existing = output / occupied; existing.write_bytes(b'original preserved bytes\n')
        result = w.run([sys.executable, '-c', script, str(w.scripts), str(output / 'receipt.json')])
        w.check(result['exit_code'] != 0 and existing.read_bytes() == b'original preserved bytes\n',
                'R3_writer_no_clobber_one_attempt:' + name)
    w.missing.append('O3/R3 refusal journals prove sequence only, never removals or scanner clearance.')


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
    if args.case == 'R2':
        evidence['requirements'] = [
            {'finding': 'R2.1', 'scenario': 'Exact original inventory/preparation/manifest/history bytes, valid original/copy hashes and no-action keeper before/after each isolated fault',
             'input': 'Changed inventory listing, manifest content, history bytes and substantive exclusions',
             'expected': 'Only preparation_artifact_changed refusal, blocked intent/nonzero; exact restoration returns valid keeper',
             'level': 'shipping prepare/report CLI subprocesses in disposable Git repo'},
            {'finding': 'R2.1', 'scenario': 'Valid keeper in each single-validation disposable mutant',
             'input': 'Remove manifest content+digest checks OR only history digest check, with every independent input valid',
             'expected': 'Named refusal assertion false, mutant report exit0; restore artifact AND source and prove valid keeper',
             'level': 'focused causal report CLI controls; raw R2-*-control-result.json and command receipts'}]
        evidence['four_answers'] = [
            'Observable no-action/blocked intent, exact bytes/hashes, specific artifact refusal and before/after valid keeper at shipping report seam.',
            'Separate manifest and history digest mutants falsify named assertions with otherwise valid inputs; substantive history negative retained separately.',
            'Preparation/report digest coupling caused a masked control; existing inventory rejection and retired units cannot prove the selected validator.',
            'New disposable Git repo, exact original-byte restoration, shipping subprocesses and single-check scratch source copies; no production seam.']
    elif args.case == 'R3':
        evidence['requirements'] = [
            {'finding': 'R3.1', 'scenario': 'Useful valid preparation then fresh age refusal before scanner; clean observer/report keeper',
             'input': 'Scratch-only late flush/fsync/close/publication-link faults after complete success bytes are produced',
             'expected': 'Success bytes remain pending only; canonical absent; observer nonzero/unknown; shipping report blocks while retaining batch argv/cwd/exit/raw, failure/source/binding and pending result',
             'level': 'actual shipping host observer/default batch and report subprocesses, synthetic task/result projections; no callback'},
            {'finding': 'R3.1', 'scenario': 'Existing open/after-write/native-batch failure controls plus exclusive writer keepers',
             'input': 'Unreadable evidence directory, occupied canonical sequence path, after receipt path, invalid batch and occupied canonical/pending writer names',
             'expected': 'Original evidence retained, unknown/nonzero, both causes retained or captured; no clobber or retry',
             'level': 'actual observer/default native refusal batch plus exact writer subprocess'}]
        evidence['four_answers'] = [
            'Final shipping report after failed late persistence must produce blocked intent/nonzero with native receipt and admitted-result pending fact.',
            'Authenticated reviewer rejected-head fsync receipt supplies red proof; full repaired observer covers flush/fsync/close/link and clean/no-clobber keepers.',
            'Open-only faults missed complete bytes before fsync/close; existing native batch controls do not prove publication boundary; retired units unused.',
            'Actual default shipping batch refuses before scanner/removal in owned scratch; only scratch copies inject faults, report uses shipping source and captured raw fallback.']
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
