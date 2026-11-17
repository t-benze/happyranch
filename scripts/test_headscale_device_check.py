"""Synthetic subprocess CLI checks using tagged v0.25.1 encoding/json shapes.

No server, root operation, external service or retired test suite is used.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'deploy/remote-access/test-server/headscale_device_check.py'
SECRET = 'SYNTHETIC-KEY-NEVER-PRINT-8b9410'


def stamp(value: datetime) -> dict:
    # Go encoding/json on timestamppb.Timestamp (seconds/nanos), not protojson.
    return {'seconds': int(value.timestamp())}


@pytest.fixture
def inventory() -> tuple[list, list, list]:
    now = datetime.now(timezone.utc)
    approved = [{
        'node_id': 1, 'node_name': 'synthetic-dev', 'user': 'synthetic-user',
        'owner': 'synthetic-owner', 'purpose': 'synthetic-check',
        'invited_home_machine': 'synthetic-home',
        'added_date': (now.date() - timedelta(days=1)).isoformat(),
        'review_by': (now.date() + timedelta(days=1)).isoformat(),
    }]
    nodes = [{'id': 1, 'name': 'synthetic-dev', 'user': {'id': 2, 'name': 'synthetic-user'}}]
    # False bools omitted by tagged generated Go omitempty tags. Used=true
    # single-use key is no longer an outstanding invitation.
    keys = [{'id': '7', 'user': 'synthetic-user', 'key': SECRET, 'used': True,
             'created_at': stamp(now - timedelta(minutes=5)),
             'expiration': stamp(now + timedelta(minutes=5))}]
    return approved, nodes, keys


def run_check(tmp_path: Path, inventory: tuple, *extra: str) -> subprocess.CompletedProcess:
    paths = []
    for name, rows in zip(('approved', 'nodes', 'keys'), inventory):
        path = tmp_path / f'{name}.json'
        path.write_text(json.dumps(rows))
        paths.append(str(path))
    return subprocess.run(
        [sys.executable, str(SCRIPT), '--approved', paths[0], '--nodes-json', paths[1],
         '--keys-json', paths[2], *extra], capture_output=True, text=True, timeout=10,
    )


def test_clean(tmp_path: Path, inventory: tuple) -> None:
    result = run_check(tmp_path, inventory)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'headscale-device-check: 0 findings\n'
    assert result.stderr == ''


@pytest.mark.parametrize('case,expected', [
    ('unknown', 'unknown node-id=3'),
    ('missing', 'missing approved-entry=1 node-id=1'),
    ('overdue', 'overdue approved-entry=1'),
    ('reusable', 'reusable key-id=7'),
    ('long-lived', 'long-lived key-id=7'),
    ('unused', 'unused-unexpired key-id=7'),
    ('custom-max', 'long-lived key-id=7'),
    ('identity-change', 'unknown node-id=1'),
    ('nanosecond-over-max', 'long-lived key-id=7'),
])
def test_findings(tmp_path: Path, inventory: tuple, case: str, expected: str) -> None:
    approved, nodes, keys = inventory
    extra = ()
    if case == 'unknown':
        nodes.append({'id': 3, 'name': 'synthetic-other', 'user': {'name': 'synthetic-user'}})
    elif case == 'missing':
        nodes.clear()
    elif case == 'overdue':
        approved[0]['review_by'] = approved[0]['added_date']
    elif case == 'reusable':
        keys[0]['reusable'] = True
    elif case == 'long-lived':
        keys[0]['expiration']['seconds'] += 7200
    elif case == 'unused':
        keys[0].pop('used')
    elif case == 'custom-max':
        extra = ('--max-key-lifetime-seconds', '60')
    elif case == 'identity-change':
        nodes[0]['name'] = 'synthetic-renamed'
    elif case == 'nanosecond-over-max':
        keys[0]['expiration'] = {'seconds': keys[0]['created_at']['seconds'] + 3600, 'nanos': 1}
    result = run_check(tmp_path, inventory, *extra)
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert f'FINDING {expected}\n' in result.stdout
    assert result.stderr == ''


@pytest.mark.parametrize('case', [
    'missing-node-id', 'missing-node-user', 'missing-key-expiry', 'missing-key-created',
    'missing-key-id', 'missing-key-material', 'wrong-bool', 'null-bool', 'wrong-id',
    'string-timestamp', 'bad-nanos', 'duplicate-node', 'duplicate-key',
    'bad-date', 'missing-owner', 'wrong-envelope', 'broken-json', 'duplicate-json-field',
    'invalid-option', 'unreadable',
])
def test_malformed(tmp_path: Path, inventory: tuple, case: str) -> None:
    approved, nodes, keys = inventory
    extra = ()
    if case == 'missing-node-id':
        del nodes[0]['id']
    elif case == 'missing-node-user':
        del nodes[0]['user']
    elif case.startswith('missing-key-'):
        field = {'expiry': 'expiration', 'created': 'created_at', 'id': 'id', 'material': 'key'}[case[12:]]
        del keys[0][field]
    elif case == 'wrong-bool':
        keys[0]['reusable'] = 'false'
    elif case == 'null-bool':
        keys[0]['used'] = None
    elif case == 'wrong-id':
        nodes[0]['id'] = True
    elif case == 'string-timestamp':
        keys[0]['expiration'] = '2026-11-17T00:00:00Z'
    elif case == 'bad-nanos':
        keys[0]['expiration']['nanos'] = -1
    elif case == 'duplicate-node':
        nodes.append(deepcopy(nodes[0]))
    elif case == 'duplicate-key':
        keys.append(deepcopy(keys[0]))
    elif case == 'bad-date':
        approved[0]['review_by'] = 'invalid'
    elif case == 'missing-owner':
        del approved[0]['owner']
    elif case == 'wrong-envelope':
        inventory = (approved, {'nodes': nodes}, keys)
    elif case == 'invalid-option':
        extra = ('--max-key-lifetime-seconds', SECRET)
    elif case == 'unreadable':
        extra = ('--nodes-json', str(tmp_path / 'absent'))
    if case in ('broken-json', 'duplicate-json-field'):
        path = tmp_path / 'bad.json'
        path.write_text('{"key":' if case == 'broken-json' else '[{"id":1,"id":2}]')
        extra = ('--nodes-json', str(path))
    result = run_check(tmp_path, inventory, *extra)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert result.stdout == ''
    assert result.stderr == 'ERROR input/parse failure; no audit result\n'


@pytest.mark.parametrize('malformed', [False, True])
def test_key_material_never_printed(tmp_path: Path, inventory: tuple, malformed: bool) -> None:
    approved, nodes, keys = inventory
    # Put a secret in every echoed-looking string, not just the key field.
    approved[0]['node_name'] = SECRET
    nodes[0]['name'] = SECRET
    keys[0]['reusable'] = True
    if malformed:
        keys[0]['id'] = SECRET
    result = run_check(tmp_path, inventory)
    assert result.returncode == (2 if malformed else 1)
    assert SECRET not in result.stdout + result.stderr
    assert 'Traceback' not in result.stderr


def test_multiple_user_files_and_nil_slices(tmp_path: Path, inventory: tuple) -> None:
    empty = tmp_path / 'empty.json'
    empty.write_text('null')
    # Every --keys-json file must be checked, even after a nil slice.
    other = tmp_path / 'other.json'
    key = deepcopy(inventory[2][0])
    key.update(id='8', user='synthetic-other', reusable=True)
    other.write_text(json.dumps([key]))
    result = run_check(tmp_path, inventory, '--keys-json', str(empty), str(other))
    assert result.returncode == 1
    assert 'reusable key-id=8' in result.stdout
    result = run_check(tmp_path, ([], None, None))
    assert result.returncode == 0
