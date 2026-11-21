"""Linux-safe real-child evidence checks; never invokes Swift or Jenkins."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SUITE_DIR = Path(__file__).resolve().parents[3] / 'scripts/mac_native'
sys.path.insert(0, str(SUITE_DIR))
import suite_settings_scheme as suite

CHILD = r'''
import hashlib, json, os, signal, sys, time
from pathlib import Path
kind, target = sys.argv[1:]
def event(name):
    with open(target, 'ab', buffering=0) as f:
        f.write((json.dumps(dict(name=name, detail=kind))+'\n').encode())
        os.fsync(f.fileno())
def term(*_):
    os.write(2, b'term received\n')
    event('term_received')
if kind == 'ignore':
    signal.signal(signal.SIGTERM, term)
    pid = os.fork()
    if pid == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True: time.sleep(.1)
    Path(target+'.pid').write_text(str(pid))
event('process_start')
os.write(1, b'partial stdout\n')
os.write(2, b'partial stderr\n')
event('check_started')
if kind == 'large':
    os.write(1, b'x'*100000)
    os.write(2, b'y'*120000)
    for i in range(1200): event('bounded_'+str(i)+'_'*80)
if kind == 'watchdog':
    event('watchdog_fired')
    sys.exit(124)
if kind in ('normal', 'large'):
    event('orderly_exit')
    sys.exit(0)
while True: time.sleep(.1)
'''


def check_hash(stream: dict[str, Any], expected: bytes) -> None:
    assert stream['bytes'] == len(expected), stream
    assert stream['sha256'] == hashlib.sha256(expected).hexdigest(), stream


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before-source', type=Path, help='Exercise the original nested command unchanged; expected to fail')
    args = parser.parse_args()
    tmp = Path(os.environ['TMPDIR'])
    assert tmp.is_absolute() and not tmp.is_relative_to('/tmp')
    with tempfile.TemporaryDirectory(prefix='timeout-proof-', dir=tmp) as folder:
        root = Path(folder)
        child = root / 'child.py'
        child.write_text(CHILD)
        if args.before_source:
            # Compile the actual old command function; no rewritten timeout implementation.
            tree = ast.parse(args.before_source.read_text())
            command = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == 'command')
            module = ast.fix_missing_locations(ast.Module(body=[command], type_ignores=[]))
            from suite_supervisor import _command
            section: dict[str, Any] = dict(commands=[])
            space = dict(Path=Path, Any=Any, ROOT=root, env=dict(os.environ), section=section,
                         deadline=time.monotonic()+5, _command=_command, time=time, hashlib=hashlib,
                         subprocess=subprocess)
            exec(compile(module, str(args.before_source), 'exec'), space)
            code, raw = space['command']('old_timeout', [sys.executable, str(child), 'hang', str(root/'old.jsonl')], .5)
            emitted = (root/'old.jsonl').read_bytes()
            assert json.loads(emitted.splitlines()[-1])['name'] == 'check_started'
            print(json.dumps(dict(before=True, code=code, retained_stdout=raw.decode(),
                                  emitted_events=emitted.decode(), command=section['commands'][0])))
            assert b'partial stdout\n' in raw, 'REGRESSION: original timeout discarded emitted stdout'
            return
        results = []
        for kind in ('hang', 'ignore', 'watchdog', 'normal', 'large'):
            event_file = root / (kind+'.jsonl')
            row = suite.observe([sys.executable, str(child), kind, str(event_file)], dict(os.environ), root,
                                time.monotonic() + (5 if kind == 'large' else .5), event_file)
            data = event_file.read_bytes()
            check_hash(row['event_file'], data)
            assert row['event_count'] == len(data.splitlines())
            assert row['last_event']['name'] == json.loads(data.splitlines()[-1])['name']
            if kind in ('hang', 'ignore'):
                assert row['status'] == 'timeout_partial' and row['timed_out'], row
                assert row['events'][0]['name'] == 'process_start'
                assert row['signal'] == (signal.SIGKILL if kind == 'ignore' else signal.SIGTERM), row
                assert row['elapsed_seconds'] < 4, row
                check_hash(row['stdout'], b'partial stdout\n')
                if kind == 'ignore':
                    assert row['signals_sent'] == ['SIGTERM', 'SIGKILL'], row
                    check_hash(row['stderr'], b'partial stderr\nterm received\n')
                    pid = int(Path(str(event_file)+'.pid').read_text())
                    # Linux can retain a killed orphan as a zombie until its init reaps it.
                    status = Path(f'/proc/{pid}/stat')
                    assert not status.exists() or status.read_text().split()[2] == 'Z'
                else:
                    check_hash(row['stderr'], b'partial stderr\n')
            elif kind == 'watchdog':
                assert row['status'] == 'timeout_partial' and not row['timed_out'] and row['exit_code'] == 124
            else:
                assert row['status'] == 'completed' and row['exit_code'] == 0
                assert row['last_event']['name'] == 'orderly_exit'
            if kind == 'large':
                check_hash(row['stdout'], b'partial stdout\n'+b'x'*100000)
                check_hash(row['stderr'], b'partial stderr\n'+b'y'*120000)
                assert row['stdout']['truncated'] and row['stderr']['truncated'] and row['events_truncated']
                assert len(json.dumps(row).encode()) < 110000
            results.append(dict(case=kind, status=row['status'], signal=row['signal'],
                                elapsed_seconds=row['elapsed_seconds'], event_count=row['event_count'],
                                last_event=row['last_event'], stdout=row['stdout'], stderr=row['stderr'],
                                event_file_bytes=row['event_file']['bytes'], event_file_sha256=row['event_file']['sha256']))
        sample = [dict(index=i, question=q, check=c, mode=m, status='completed', exit_code=0, events=[], downloads=[])
                  for i, (q, c, m) in enumerate(suite.CASES)]
        sample[6]['status'] = 'timeout_partial'  # M2 must not relabel the other questions.
        assessed = suite.assess(sample)
        assert assessed['M2']['status'] == 'timeout_partial'
        assert all(assessed[q]['status'] == 'failed' for q in ('M1', 'M3', 'M4'))
        # Bound an entire section, including all invocation events and preparation excerpts.
        bounded = dict(commands=[{k: row[k] for k in ('stdout', 'stderr')} for _ in range(13)],
                       runs=[dict(r, **row) for r in sample], questions=assessed)
        section_bytes = len(json.dumps(bounded, indent=2).encode())
        assert section_bytes < 768 * 1024, section_bytes
        print(json.dumps(dict(passed=True, cases=results, independent_statuses=True,
                              bounded_section_bytes=section_bytes), indent=2))


if __name__ == '__main__':
    main()
