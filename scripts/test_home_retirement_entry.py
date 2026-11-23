"""Canonical fixed entry and real retirement/recovery composition.

Run: uv run --frozen pytest scripts/test_home_retirement_entry.py -v --tb=short
Filesystem/NSS/native observations use the existing independent adapters.
The entry, original journals, revocation, cleanup and final receipt are real.
"""
from __future__ import annotations

from contextlib import nullcontext
import json
import os
from pathlib import Path

import pytest

from runtime.remote_access import home_retirement as entry
from runtime.remote_access import installer
from scripts.test_home_retirement import Fixture
from scripts.test_home_retirement_account import account_adapter, deletions
from scripts.test_home_retirement_coordinator import active, native_target_stop, FINAL
from scripts.test_home_retirement_inventory import auxiliary_journal_setup, TRANSACTION


@pytest.mark.parametrize('case', ['fresh', 'retired', 'drift', 'malformed', 'arguments', 'nonroot'])
def test_fixed_entry_preserves_fresh_completed_and_refused_layouts(tmp_path, monkeypatch, capsys, case):
    fixture = Fixture(tmp_path / 'root')
    if case in ('retired', 'drift', 'malformed'):
        fixture.file(FINAL, b'{"account":"absent","format":"home-retirement@1","state":"retired"}\n'
            if case != 'malformed' else b'synthetic-unknown-final')
    if case == 'drift':
        fixture.file('etc/happyranch/invite-issuer.json')
    before = fixture.snapshot()
    constructed = []
    def installed(**kwargs):
        assert kwargs == {'layout': 'deb'}
        constructed.append(True)
        return fixture.io
    monkeypatch.setattr(installer, 'ManagedInstaller', installed)
    monkeypatch.setattr(os, 'geteuid', lambda: 1000 if case == 'nonroot' else 0)
    result = entry.main(['--root=/caller-selected'] if case == 'arguments' else [])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == entry.MESSAGE and len(lines) == 2
    status = json.loads(lines[1])
    assert result == (0 if case in ('fresh', 'retired') else 1)
    assert status['state'] == (case if case in ('fresh', 'retired') else 'migration pending')
    assert constructed == ([] if case in ('arguments', 'nonroot') else [True])
    assert fixture.snapshot() == before
    assert all(call[:3] == ['systemctl', 'show', entry.UNIT] for call in fixture.calls)


@pytest.mark.parametrize('active', ['active', 'inert'], indirect=True)
@pytest.mark.parametrize('boundary', ['fragment', 'publication'])
def test_canonical_entry_completes_actual_interrupted_retirement_and_refuses_reappearance(active, monkeypatch, capsys, request, boundary):
    inert = request.node.callspec.params['active'] == 'inert'
    auxiliary_journal_setup(active.controller)
    if inert:
        (active.root / TRANSACTION).unlink()
    else:
        device, _ = active.grant('device')
        stream = active.stream(device, 'device')
        native_target_stop(active)
    state = account_adapter(active.io, active.calls)
    previous_run = active.io._run
    reloads = []
    def native(argv, **kwargs):
        if argv == ['systemctl', 'daemon-reload']:
            active.calls.append(argv)
            reloads.append(True)
            for props in (active.controller.properties, *active.native.values()):
                assert not (active.root / 'etc/systemd/system' / props['Id']).exists()
                props.update(LoadState='not-found', FragmentPath='', NeedDaemonReload='no',
                    ActiveState='inactive', SubState='dead', UnitFileState='')
                if 'MainPID' in props:
                    props.update(MainPID='0', ControlPID='0', ControlGroup='', User='', Group='')
            return 0, ''
        if argv[:2] == ['systemctl', 'stop'] and not inert:
            assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        return previous_run(argv, **kwargs)
    active.io._run = active.io.manager._run = native
    monkeypatch.setattr(installer, 'ManagedInstaller', lambda **kwargs: active.io)
    real_unlink, real_write = os.unlink, os.write
    interrupted, emitted = [], []
    def unlink(name, *args, **kwargs):
        real_unlink(name, *args, **kwargs)
        if boundary == 'fragment' and not interrupted and name == 'happyranch-headscale.service':
            interrupted.append(True)
            raise OSError('synthetic interrupted original fragment removal')
    def write(fd, data):
        if not emitted:
            assert capsys.readouterr().out.startswith(entry.MESSAGE + '\n')
            emitted.append(True)
        if boundary == 'publication' and not interrupted and Path(os.readlink(f'/proc/self/fd/{fd}')) == (
                active.root / entry.STATE_DIRECTORY / '.home-retirement.json.pending'):
            interrupted.append(True)
            return real_write(fd, data[:11])
        return real_write(fd, data)
    # The existing lower fixture owns a fence for direct-owner tests. End that
    # test-side context so the shipping entry acquires the real directory flock.
    serving = nullcontext() if inert else active.serving(monkeypatch)
    with serving:
        # Bind under the real test-user custody before simulating root entry.
        monkeypatch.setattr(os, 'geteuid', lambda: 0)
        active.fence_context.__exit__(None, None, None)
        with monkeypatch.context() as patch:
            patch.setattr(os, 'unlink', unlink)
            patch.setattr(os, 'write', write)
            assert entry.main([]) == 1
        assert interrupted and not (active.root / FINAL).exists()
        assert active.io.home_retirement_status()['state'] == 'migration pending'
        if not inert:
            from runtime.remote_access.streams import StreamClosed
            with pytest.raises(StreamClosed):
                stream.receive()
        assert entry.main([]) == 0
    produced = (active.root / FINAL).read_bytes()
    assert json.loads(produced) == {'account': 'absent', 'format': 'home-retirement@1', 'state': 'retired'}
    assert not state['user'] and not state['group']
    assert deletions(active.calls) == ['userdel', 'groupdel'] and reloads == [True]
    before, effects = active.snapshot(), active.effects()
    frames = list(getattr(active, 'frames', []))
    assert entry.main([]) == entry.main([]) == 0
    assert active.snapshot() == before and active.effects() == effects
    assert list(getattr(active, 'frames', [])) == frames
    assert deletions(active.calls) == ['userdel', 'groupdel'] and reloads == [True]
    active.controller.file('etc/happyranch/invite-issuer.json', b'synthetic-foreign-reappearance')
    drift = active.snapshot()
    assert entry.main([]) == 1
    assert active.snapshot() == drift and (active.root / FINAL).read_bytes() == produced
    assert active.effects() == effects and deletions(active.calls) == ['userdel', 'groupdel']
    output = capsys.readouterr().out
    assert 'synthetic-' not in output
