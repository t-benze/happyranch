"""Authenticate the ordinary naming export before owned storage removal.

This is an operator evidence reader, not a test or product behavior substitute.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('source_sha')
    parser.add_argument('workload_exit', type=int)
    parser.add_argument('work_state', type=Path)
    parser.add_argument('work_name')
    args = parser.parse_args()
    root = args.destination
    selection = json.loads(Path(__file__).with_name('ordinary-selection.json').read_text())
    manifest = json.loads((root / 'export-manifest.json').read_text())
    if manifest['source_sha'] != args.source_sha:
        raise ValueError('export source mismatch')
    entries = list(root.rglob('*'))
    if len(entries) > 14000 or sum(p.stat().st_size for p in entries if p.is_file()) > 256 * 1024**2:
        raise ValueError('complete export byte/inode cap exceeded')
    if any(p.is_symlink() for p in entries):
        raise ValueError('symlink in host export')
    if json.loads((root / 'ordinary-selection.json').read_text()) != selection:
        raise ValueError('selected composition mismatch')
    names = set()
    for row in manifest['files']:
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts or row['path'] in names:
            raise ValueError('unsafe/duplicate export path')
        names.add(row['path'])
        raw = (root / relative).read_bytes()
        if len(raw) != row['bytes'] or hashlib.sha256(raw).hexdigest() != row['sha256']:
            raise ValueError('incomplete/changed export: ' + row['path'])
        if len(raw) > 8 * 1024**2:
            raise ValueError('per-file export cap exceeded')
        if args.workload_exit == 0 and relative.suffix in {'.log', '.txt'}:
            if b'[nightly log truncated; showing final output bytes]' in raw:
                raise ValueError('incomplete retained-tail evidence')
    actual_fixtures = {str(p.relative_to(root)) for p in (root / 'fixture-artifacts').rglob('*') if p.is_file()}
    if actual_fixtures != {name for name in names if name.startswith('fixture-artifacts/')}:
        raise ValueError('fixture inventory mismatch')
    outcomes = []
    if (root / 'naming.xml').exists():
        for case in ET.parse(root / 'naming.xml').iter('testcase'):
            nodeid = case.attrib['classname'].replace('.', '/') + '.py::' + case.attrib['name']
            outcome = next((kind for kind in ('failure', 'error', 'skipped') if case.find(kind) is not None), 'passed')
            outcomes.append({'nodeid': nodeid, 'outcome': outcome})
    expected = [case['nodeid'] for case in selection['cases']]
    report = {'source_sha': args.source_sha, 'workload_exit': args.workload_exit,
              'actual_outcomes': outcomes, 'missing': sorted(set(expected) - {row['nodeid'] for row in outcomes}),
              'F1_scope': 'isolated negative owner validation; no full H/I transaction compatibility claim',
              'outgoing_payload': 'UNOBSERVED; no omission/null transport observation inferred'}
    (root / 'case-outcomes.json').write_text(json.dumps(report, indent=2) + '\n')
    if args.workload_exit == 0:
        required = {'naming.xml', 'naming.log', 'work-status.json', 'boundary-completed.json',
                    'source-sentinel', 'evidence-sentinel', 'ordinary-selection.json', 'fixture-export.json'}
        if not required <= names:
            raise ValueError('missing required authenticated evidence')
        status = json.loads((root / 'work-status.json').read_text())
        if status != {'workload_exit': 0, 'evidence_check_exit': 0}:
            raise ValueError('workload evidence status mismatch')
        if Counter(row['nodeid'] for row in outcomes) != Counter(expected) or any(row['outcome'] != 'passed' for row in outcomes):
            raise ValueError('missing/duplicate/foreign/failed/skipped selected case')
        work = json.loads(args.work_state.read_text())
        if (len(work) != 1 or work[0]['Name'] != '/' + args.work_name
                or work[0]['State']['Running'] is not False or work[0]['State']['Pid'] != 0
                or work[0]['State']['ExitCode'] != 0
                or work[0]['Config']['Cmd'] != ['bash', '/work.sh', args.source_sha]):
            raise ValueError('outer workload container closure unproven')
        config = work[0]['HostConfig']
        if (config['NetworkMode'] != 'none' or config['Memory'] != 4026531840
                or config['MemorySwap'] != 4026531840 or config['PidsLimit'] != 496
                or config['NanoCpus'] != 1950000000 or config['ReadonlyRootfs'] is not True):
            raise ValueError('outer workload containment mismatch')
        boundary = json.loads((root / 'boundary-completed.json').read_text())
        if boundary['sha'] != args.source_sha or boundary['tmpfs_quotas_verified'] is not True:
            raise ValueError('completed resource boundary missing')
        (root / 'nested-session-closure.json').write_text(json.dumps({
            'source_sha': args.source_sha, 'container_id': work[0]['Id'],
            'running': False, 'pid': 0,
            'case_ids': [case['nodeid'] for case in selection['cases'] if 'cli_reasoning_composition[' in case['nodeid']],
            'basis': 'exact offline resource-bounded Docker workload container stopped; all nested sessions contained',
        }, indent=2) + '\n')
    (root / 'export-authenticated.json').write_text(json.dumps({
        'source_sha': args.source_sha, 'files': len(names),
        'manifest_sha256': hashlib.sha256((root / 'export-manifest.json').read_bytes()).hexdigest(),
        'workload_exit': args.workload_exit,
    }) + '\n')


if __name__ == '__main__':
    main()
