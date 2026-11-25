"""Execute the handbook's exact operator projection with synthetic CLI output."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HANDBOOK = Path(__file__).resolve().parents[1] / 'docs/operations/linux-release-handbook.md'


def command() -> str:
    block = HANDBOOK.read_text().split('<!-- THR327-STATUS-PROJECTION -->', 1)[1]
    return block.split("<<'PY_STATUS'\n", 1)[1].split('\nPY_STATUS', 1)[0]


@pytest.mark.parametrize('case', [
    'ready', 'prepared_no_receipt', 'failed_uncertain', 'partial_cleanup', 'runtime_unavailable',
    'setup_unverified', 'renewal_failed', 'refused', 'permission', 'native-unavailable',
    'empty', 'non-json', 'exit2', 'timeout', 'duplicate', 'extra-refusal', 'bad-count', 'list',
])
def test_plan_status_projection(tmp_path, monkeypatch, capsys, case):
    output = tmp_path / 'evidence.json'
    output.write_text('old evidence\n')
    value = dict(observation='known', setup=case if case in (
        'prepared_no_receipt', 'failed_uncertain', 'partial_cleanup', 'runtime_unavailable',
        'setup_unverified', 'renewal_failed') else 'ready', runtime='available' if case == 'ready' else 'offline',
        devices=None, pending=None, selected=None, private='SYNTHETIC_PRIVATE_CANARY')
    code = 0
    if case in ('refused', 'permission', 'native-unavailable', 'extra-refusal'):
        code = 1
        value = dict(code='hostStateUnrecognized', category='unverified', effect='none', recovery='refresh')
        if case == 'permission': value = dict(code='permissionDenied', effect='none', recovery='nativePermission')
        if case == 'native-unavailable': value = dict(code='unavailable', effect='none', recovery='nativeRecovery')
        if case == 'extra-refusal': value['private'] = 'SYNTHETIC_PRIVATE_CANARY'
    if case == 'bad-count': value['devices'] = True
    if case == 'list': value = []
    raw = json.dumps(value).encode()
    if case == 'empty': raw = b''
    if case == 'non-json': raw = b'SYNTHETIC_PRIVATE_CANARY'
    if case == 'duplicate': raw = raw.replace(b'"setup":', b'"setup":"off","setup":')
    if case == 'exit2': code = 2
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs == dict(stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False, timeout=8)
        if case == 'timeout': raise subprocess.TimeoutExpired(argv, 8)
        return SimpleNamespace(returncode=code, stdout=raw)
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(sys, 'argv', ['-', str(output)])
    exec(compile(command(), str(HANDBOOK), 'exec'), {})
    observed = json.loads(output.read_text())
    if case in ('refused', 'permission', 'native-unavailable'):
        assert observed == dict(kind='refusal', cliExitCode=1, **value)
    elif case in ('ready', 'prepared_no_receipt', 'failed_uncertain', 'partial_cleanup',
                  'runtime_unavailable', 'setup_unverified', 'renewal_failed'):
        assert observed == dict(kind='status', cliExitCode=0, observation='known', setup=value['setup'],
                               runtime=value['runtime'], approvedDeviceCount=None,
                               pendingPresent=False, selectedPresent=False)
    else:
        assert observed == dict(kind='unavailable', category='status-unavailable',
                               cliExitCode=None if case == 'timeout' else code)
    assert calls == [['/usr/bin/happyranch', 'connection', 'host', 'status']]
    assert 'SYNTHETIC_PRIVATE_CANARY' not in output.read_text()
    assert capsys.readouterr() == ('', '')
    assert sorted(p.name for p in tmp_path.iterdir()) == ['evidence.json']


def test_plan_projection_preserves_previous_evidence_on_write_failure(tmp_path, monkeypatch, capsys):
    output = tmp_path / 'evidence.json'
    output.write_bytes(b'previous evidence\n')
    monkeypatch.setattr(subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=2, stdout=b''))
    monkeypatch.setattr(sys, 'argv', ['-', str(output)])
    def unavailable(*args):
        raise OSError('SYNTHETIC_PRIVATE_CANARY')
    monkeypatch.setattr(os, 'replace', unavailable)
    with pytest.raises(SystemExit) as error:
        exec(compile(command(), str(HANDBOOK), 'exec'), {})
    assert error.value.code == 2
    assert output.read_bytes() == b'previous evidence\n'
    assert capsys.readouterr() == ('', 'Status evidence could not be written.\n')
    assert sorted(p.name for p in tmp_path.iterdir()) == ['evidence.json']
