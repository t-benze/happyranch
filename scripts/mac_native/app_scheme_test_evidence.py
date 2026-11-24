"""Full-package test census and closed per-invocation evidence for app-scheme."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

TEST_OUTPUT_CAP = 16 * 1024 * 1024
EVENT_ARCHIVE_CAP = 4 * 1024 * 1024


def archive_events(events: bytes) -> dict[str, Any]:
    """Keep byte-exact gzip in collected probe.json, without a Jenkins change."""
    if not events or len(events) > TEST_OUTPUT_CAP:
        raise ValueError('missing or oversized Swift Testing archive input')
    compressed = gzip.compress(events, mtime=0)
    if len(compressed) > EVENT_ARCHIVE_CAP:
        raise ValueError('Swift Testing archive exceeds compressed bound')
    return {'encoding': 'gzip+base64', 'bytes': len(events), 'sha256': hashlib.sha256(events).hexdigest(),
            'gzip_bytes': len(compressed), 'gzip_sha256': hashlib.sha256(compressed).hexdigest(),
            'data': base64.b64encode(compressed).decode('ascii')}


def source_inventory(tests: Path) -> dict[str, Any]:
    """Independent source expectations; unsupported declaration shapes refuse."""
    xctest, swift = [], []
    for path in sorted(tests.glob('*Tests/*.swift')):
        source = path.read_text()
        owner = None
        for match in re.finditer(r'\bclass\s+(\w+)\s*:\s*XCTestCase\b|\bfunc\s+(test\w+)\s*\(', source):
            if match[1]:
                owner = match[1]
            elif owner:
                xctest.append(f'{path.parent.name}.{owner}/{match[2]}')
        functions = re.findall(r'@Test\b[\s\S]*?\bfunc\s+(\w+)\s*\(', source)
        if len(functions) != len(re.findall(r'@Test\b', source)):
            raise RuntimeError('unsupported Swift Testing source declaration')
        swift.extend(f'{path.parent.name}/{path.name}/{name}' for name in functions)
    if not xctest or not swift or len(set(xctest)) != len(xctest):
        raise RuntimeError('empty or duplicate native source inventory')
    return {'xctest': xctest, 'swift_testing': swift}


def terminal_results(output: str) -> dict[str, Any]:
    # The command's hard output cap refuses overflow; never trim test results.
    lines = [line for line in output.splitlines()
             if re.search(r'\b(?:Test|Suite) .+\b(?:passed|failed|skipped)\b', line)]
    return {'lines': lines, 'truncated': False,
            'json_bytes': sum(len(json.dumps(line).encode()) + 2 for line in lines)}


def assess_native(raw: bytes, events: bytes, inventory: dict[str, Any], code: int) -> dict[str, Any]:
    """Require every source test and every started invocation to finish cleanly.

    Swift Testing ABI v0 has no stable parameter-case IDs. --no-parallel lets
    us assign an ordinal within each function without retaining argument values.
    Unknown/malformed/missing evidence fails, never falls back to aggregate text.
    """
    result: dict[str, Any] = {'status': 'FAIL', 'cases': [], 'errors': [], 'truncated': False}
    errors, cases = result['errors'], result['cases']
    starts: Counter[str] = Counter()
    ends: dict[str, list[str]] = {}
    for owner, method, state in re.findall(
        r"Test Case '-\[([\w.]+) (\w+)\]' (started|passed|failed|skipped)", raw.decode(errors='replace')
    ):
        key = f'{owner}/{method}'
        if state == 'started':
            starts[key] += 1
        else:
            ends.setdefault(key, []).append(state)
    for key in sorted(set(inventory['xctest']) | set(starts) | set(ends)):
        passed = starts[key] == 1 and ends.get(key) == ['passed']
        cases.append({'library': 'XCTest', 'id': key, 'status': 'PASS' if passed else 'FAIL',
                      'starts': starts[key], 'outcomes': ends.get(key, [])})
    functions: dict[str, dict[str, Any]] = {}
    suites: set[str] = set()
    records = []
    try:
        if not events or not events.endswith(b'\n') or len(events) > TEST_OUTPUT_CAP:
            raise ValueError('missing, partial or oversized Swift Testing events')
        for line in events.splitlines():
            record = json.loads(line)
            if record['version'] != 0 or not isinstance(record['payload'], dict):
                raise ValueError('unsupported Swift Testing schema')
            payload = record['payload']
            if record['kind'] == 'test':
                key = payload['id']
                if payload['kind'] == 'suite':
                    suites.add(key)
                elif payload['kind'] == 'function':
                    if key in functions:
                        raise ValueError('duplicate Swift Testing function')
                    if type(payload['isParameterized']) is not bool:
                        raise ValueError('missing Swift Testing parameterization')
                    functions[key] = {'source': payload['sourceLocation']['fileID'] + '/' + payload['name'].split('(')[0],
                                      'parameterized': payload['isParameterized'],
                                      'starts': 0, 'ends': 0, 'cases': [], 'active': None, 'failed': False}
                else:
                    raise ValueError('unsupported Swift Testing test kind')
            elif record['kind'] == 'event':
                records.append(payload)
            else:
                raise ValueError('unsupported Swift Testing record kind')
        if Counter(row['source'] for row in functions.values()) != Counter(inventory['swift_testing']):
            errors.append('Swift Testing source inventory mismatch')
            missing = Counter(inventory['swift_testing']) - Counter(row['source'] for row in functions.values())
            for source in sorted(missing.elements()):
                cases.append({'library': 'Swift Testing', 'id': source, 'status': 'FAIL', 'reason': 'missing function'})
        run = Counter()
        for event in records:
            kind, key = event['kind'], event.get('testID')
            if kind in ('runStarted', 'runEnded'):
                run[kind] += 1
            elif run != Counter(runStarted=1):
                errors.append('Swift Testing event outside run boundary')
            row = functions.get(key)
            if kind in ('issueRecorded', 'testSkipped', 'testCancelled', 'testCaseCancelled'):
                errors.append(f'Swift Testing {kind}: {key}')
                if row:
                    row['failed'] = True
                    if row['active'] is not None:
                        row['active']['status'] = 'FAIL'
                    elif not row['parameterized']:
                        for item in row['cases']:
                            item['status'] = 'FAIL'
            if row is None:
                if key is not None and key not in suites:
                    errors.append('unknown Swift Testing event owner')
                continue
            if kind == 'testStarted':
                row['starts'] += 1
                if row['starts'] != 1 or row['ends']:
                    errors.append('duplicate or out-of-order Swift Testing function start')
                if not row['parameterized']:
                    item = {'library': 'Swift Testing', 'id': key, 'lifecycle': 'function',
                            'status': 'FAIL' if row['failed'] else 'INCOMPLETE', 'starts': row['starts'], 'ends': 0}
                    row['cases'].append(item)
                    cases.append(item)
                    row['active'] = item
            elif kind == 'testEnded':
                row['ends'] += 1
                if row['starts'] != 1 or row['ends'] != 1:
                    errors.append('duplicate or out-of-order Swift Testing function end')
                if not row['parameterized'] and row['active'] is not None:
                    row['active']['ends'] = row['ends']
                    if row['active']['status'] == 'INCOMPLETE':
                        row['active']['status'] = 'PASS'
                    row['active'] = None
                elif row['active'] is not None:
                    errors.append('Swift Testing function ended before case')
            elif kind == 'testCaseStarted':
                if not row['parameterized'] or row['starts'] != 1 or row['ends']:
                    errors.append('unexpected or out-of-order Swift Testing case start')
                    continue
                if row['active'] is not None:
                    errors.append('overlapping Swift Testing cases')
                item = {'library': 'Swift Testing', 'id': key, 'case': len(row['cases']) + 1,
                        'lifecycle': 'parameter', 'status': 'INCOMPLETE'}
                row['cases'].append(item)
                cases.append(item)
                row['active'] = item
            elif kind == 'testCaseEnded':
                if not row['parameterized'] or row['starts'] != 1 or row['ends']:
                    errors.append('unexpected or out-of-order Swift Testing case end')
                    continue
                if row['active'] is None:
                    errors.append('Swift Testing case ended without start')
                else:
                    if row['active']['status'] == 'INCOMPLETE':
                        row['active']['status'] = 'PASS'
                    row['active'] = None
        if run != Counter(runStarted=1, runEnded=1):
            errors.append('missing or duplicate Swift Testing run boundary')
        for key, row in functions.items():
            if row['starts'] != 1 or row['ends'] != 1 or not row['cases'] or row['active'] is not None or row['failed']:
                errors.append(f'incomplete or failed Swift Testing function: {key}')
                if not row['cases']:
                    cases.append({'library': 'Swift Testing', 'id': key, 'status': 'FAIL', 'reason': 'no invocation'})
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        errors.append('invalid Swift Testing capture: ' + type(error).__name__)
    if code:
        errors.append(f'swift test exit {code}')
    if not errors and cases and all(row['status'] == 'PASS' for row in cases):
        result['status'] = 'PASS'
    result['counts'] = dict(Counter(row['library'] for row in cases))
    return result
