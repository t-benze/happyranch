from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import thr211_window as window


@pytest.fixture
def native(tmp_path, monkeypatch):
    clock = [0.0]
    commands = []
    census = ['20 999 999', '', '', '', '', '', '', '', '', '', '', '', '', '', '']
    facts = dict(uid=999, host='native', expiry=4100, ready=str(tmp_path / 'ready'), agent_pids=[20],
                 binding=['request', 'a'*40, 'b'*64, 'c'*64, 'd'*64, ''])
    for key in ('native_semantics_review', 'source_inhibition_receipt', 'drain_receipt', 'original_state_receipt',
                'export_receipt', 'stopped_sources_receipt', 'restored_state_receipt'):
        path = tmp_path / key
        path.write_text(json.dumps({'offline': True, 'disabled': True, 'loaded': False}))
        facts[key] = str(path)
    for key in ('native_semantics_review', 'source_inhibition_receipt', 'drain_receipt', 'export_receipt', 'stopped_sources_receipt'):
        state = {'source_inhibition_receipt': 'HELD', 'drain_receipt': 'DRAINED', 'stopped_sources_receipt': 'STOPPED_NO_PENDING_LAUNCH'}.get(key)
        Path(facts[key]).write_text(json.dumps(dict(request='request', source='a'*40, uid=999, host='native', state=state)))
    facts['binding'][5] = hashlib.sha256(Path(facts['original_state_receipt']).read_bytes()).hexdigest()
    facts['commands'] = dict(id=['/usr/bin/id', '-u', 'jenkins'],
                             dscl=['/usr/bin/dscl', '.', '-read', '/Users/jenkins', 'UniqueID'],
                             census=['/bin/ps', '-axo', 'pid=,ruid=,uid='],
                             term=['/usr/bin/pkill', '-TERM', '-U', '999', '-u', '999'],
                             kill=['/usr/bin/pkill', '-KILL', '-U', '999', '-u', '999'])
    def run(argv, **kwargs):
        commands.append(argv)
        name = next(k for k,v in facts['commands'].items() if argv == v)
        values = dict(id='999', dscl='UniqueID: 999', census=census.pop(0) if name == 'census' and census else '')
        return SimpleNamespace(returncode=0, stdout=values.get(name, ''))
    monkeypatch.setattr(window.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(window.platform, 'node', lambda: 'native')
    monkeypatch.setattr(window.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(window, 'sys', SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True), stdout=SimpleNamespace(isatty=lambda: True)))
    monkeypatch.setattr(window, 'private_control', lambda *a, **kw: None)
    # Native commands are controlled. No signal is sent to any real process.
    instance = window.Window(facts, tmp_path, clock=lambda: clock[0], wall=lambda: clock[0],
                             sleep=lambda amount: clock.__setitem__(0, clock[0]+amount), run=run)
    for path in tmp_path.iterdir():
        window.os.utime(path, (0, 0))
    return instance, commands, census, clock


@pytest.mark.parametrize('failure', ['zero', 'uid', 'dscl_failure', 'host', 'terminal', 'evidence', 'hold', 'busy', 'mixed'])
def test_native_refusal_before_signals(native, failure, monkeypatch):
    instance, commands, census, _ = native
    if failure == 'zero': instance.facts['uid'] = 0
    if failure == 'uid': instance.facts['uid'] = 998
    if failure == 'host': instance.facts['host'] = 'wrong'
    if failure == 'terminal': monkeypatch.setattr(window.sys, 'stdin', SimpleNamespace(isatty=lambda: False))
    if failure in ('evidence', 'hold'):
        Path(instance.facts['original_state_receipt' if failure == 'evidence' else 'source_inhibition_receipt']).write_text('')
    if failure == 'busy': census[0] = '20 999 999\n21 999 999'
    if failure == 'mixed': census[0] = '20 999 998'
    if failure == 'dscl_failure':
        original = instance.run
        instance.run = lambda argv, **kw: SimpleNamespace(returncode=7, stdout='UniqueID: 999') if argv[0].endswith('dscl') else original(argv, **kw)
    with pytest.raises(window.Refused): instance.ready()
    assert all('/usr/bin/pkill' not in argv for argv in commands)


def test_cleanup_excludes_other_uid_and_restores_exact_state(native):
    instance, commands, census, clock = native
    instance.ready()
    census[:] = ['21 999 999\n22 1000 1000', '22 1000 1000'] + ['22 1000 1000'] * 12
    instance.cleanup()
    assert clock[0] <= 30
    assert instance.receipt['cleanup'] == 'OBSERVED_EMPTY_5S'
    assert [argv for argv in commands if argv[0].endswith('pkill')] == [instance.facts['commands']['term']]
    instance.restored()
    assert instance.receipt['restoration'] == 'READBACK_MATCH'


@pytest.mark.parametrize('failure', ['survivor', 'rearrival', 'expiry', 'export', 'hold', 'changed_uid'])
def test_cleanup_failure_keeps_restoration_unavailable(native, failure):
    instance, commands, census, clock = native
    instance.ready()
    if failure == 'survivor': census[:] = ['21 999 999'] * 20
    if failure == 'rearrival': census[:] = ['', '', '', '21 999 999']
    if failure == 'expiry': instance.deadline = 2
    if failure in ('export', 'hold'):
        Path(instance.facts['export_receipt' if failure == 'export' else 'stopped_sources_receipt']).write_text('')
    if failure == 'changed_uid': instance.facts['uid'] = 998
    with pytest.raises(window.Refused): instance.cleanup()
    assert instance.receipt['cleanup'] == 'INCOMPLETE'
    with pytest.raises(window.Refused): instance.restored()


def test_restore_refuses_changed_preexisting_states(native):
    instance, _, census, _ = native
    instance.ready()
    instance.cleanup()
    Path(instance.facts['restored_state_receipt']).write_text('{"offline": false, "disabled": false, "loaded": true}')
    with pytest.raises(window.Refused, match='restoration differs'): instance.restored()


def test_unbound_or_broadened_signal_vector_refuses(native):
    instance, commands, _, _ = native
    instance.facts['commands']['term'] = ['/usr/bin/pkill', '-TERM', 'java']
    with pytest.raises(window.Refused): instance.command('term')
    assert not commands


def test_independent_partial_retrieval_after_agent_loss(native, tmp_path):
    instance, _, _, _ = native
    root = tmp_path / 'thr211-4'
    artifacts = root / 'artifacts'
    artifacts.mkdir(parents=True)
    (artifacts / 'setup.log').write_text('partial setup, agent disconnected')
    # Use the fixture's real file UID for the read-only copying seam only.
    instance.facts['uid'] = (artifacts / 'setup.log').stat().st_uid
    run = tmp_path / 'run.json'
    run.write_text(json.dumps(dict(root=str(root), build=4, request='request', source='a'*40)))
    instance.facts['run_receipt'] = str(run)
    instance.capture()
    assert (tmp_path / 'setup.log').read_text() == 'partial setup, agent disconnected'
    assert instance.receipt['cleanup'] == 'NOT_STARTED'
    assert [r for r in instance.receipt['artifacts'] if r['name'] == 'integration.xml'][0]['result'] == 'UNAVAILABLE'


def test_partial_retrieval_refuses_foreign_symlink(native, tmp_path):
    instance, commands, _, _ = native
    root = tmp_path / 'thr211-4'
    root.mkdir()
    foreign = tmp_path / 'foreign'
    foreign.mkdir()
    (root / 'artifacts').symlink_to(foreign)
    run = tmp_path / 'run.json'
    run.write_text(json.dumps(dict(root=str(root), build=4, request='request', source='a'*40)))
    instance.facts['run_receipt'] = str(run)
    with pytest.raises(OSError): instance.capture()
    assert not commands and not list(foreign.iterdir())


def test_foreground_lease_binds_exact_run_and_expires_quickly(native):
    instance, _, _, clock = native
    instance.ready()
    instance.lease()
    first = Path(instance.facts['ready']).read_text().split()
    assert first[:6] == ['request', 'a'*40, 'b'*64, 'c'*64, '999', 'native']
    assert first[6:] == ['8', 'd'*64, 'ready']
    clock[0] = 2
    instance.lease()
    assert Path(instance.facts['ready']).read_text().split()[6] == '10'
    assert not Path(instance.facts['ready'] + '.next').exists()


def test_slow_native_calls_share_one_cleanup_deadline(native):
    instance, _, census, clock = native
    instance.ready()
    original = instance.run
    def slow(argv, **kwargs):
        assert 0 < kwargs['timeout'] <= 3
        clock[0] += kwargs['timeout']
        return original(argv, **kwargs)
    instance.run = slow
    with pytest.raises(window.Refused, match='deadline'):
        instance.cleanup()
    assert clock[0] <= 30
    assert instance.receipt['cleanup'] == 'INCOMPLETE'
