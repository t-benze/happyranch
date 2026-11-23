"""Canonical fixed entry and real retirement/recovery composition.

Run: uv run --frozen pytest scripts/test_home_retirement_entry.py -v --tb=short
Filesystem/NSS/native observations use the existing independent adapters.
The entry, original journals, revocation, cleanup and final receipt are real.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import home_retirement as entry
from runtime.remote_access import installer
from runtime.remote_access import pairing
from runtime.remote_access.home_retirement import HomeRetirement
from runtime.remote_access.pairing import PairingError
from scripts.test_home_retirement import Fixture
from scripts.test_home_retirement_account import account_adapter, deletions
from scripts.test_home_retirement_coordinator import active, native_target_stop, FINAL, INVENTORY, PIN
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


@pytest.mark.parametrize(('active', 'boundary'), [
    ('active', 'fragment'), ('inert', 'fragment'),
    ('active', 'publication'), ('inert', 'publication'),
    ('active', 'preparation'), ('active', 'ceremony'), ('active', 'first-device'),
    ('active', 'failed-close'), ('active', 'controller-stop'), ('active', 'controller-disable'),
    ('active', 'account'), ('active', 'before-publication'),
], indirect=['active'])
def test_canonical_entry_completes_actual_interrupted_retirement_and_refuses_reappearance(active, monkeypatch, capsys, request, boundary):
    inert = request.node.callspec.params['active'] == 'inert'
    auxiliary_journal_setup(active.controller)
    if inert:
        (active.root / TRANSACTION).unlink()
    else:
        def failed_close():
            raise OSError('synthetic failed original physical cleanup')
        nodes = ('first', 'second') if boundary in ('ceremony', 'first-device') else ('device',)
        token_hex, identifiers = pairing.secrets.token_hex, iter(('1' * 32, '2' * 32))
        with monkeypatch.context() as patch:
            patch.setattr(pairing, 'secrets', SimpleNamespace(token_hex=
                lambda size: next(identifiers) if size == 16 else token_hex(size)))
            grants = [active.grant(node) for node in nodes]
        devices = [device for device, _ in grants]
        streams = [active.stream(device, node, close=failed_close if boundary == 'failed-close' else None)
                   for device, node in zip(devices, nodes)]
        if boundary in ('ceremony', 'first-device'):
            active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
        native_target_stop(active)
    state = account_adapter(active.io, active.calls, fault='user-uncertain' if boundary == 'account' else '')
    previous_run = active.io._run
    reloads, interrupted, emitted = [], [], []
    uncertain_command = (['systemctl', boundary.removeprefix('controller-'), entry.UNIT]
                         if boundary.startswith('controller-') else None)
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
            assert all(active.reopened()[0].devices[device].revoked for device in devices)
            assert active.closed == list(nodes)
        result = previous_run(argv, **kwargs)
        if not interrupted and (argv == uncertain_command or (boundary == 'account' and argv[0] == 'userdel')):
            interrupted.append(True)
            return 1, 'synthetic uncertainty after actual native/account effect'
        return result
    active.io._run = active.io.manager._run = native
    installer_type, constructed = installer.ManagedInstaller, []
    def fresh_installer(**kwargs):
        assert kwargs == {'layout': 'deb'}
        io = installer_type(root=active.root, root_owner=active.io.root_owner, layout='deb',
            run=active.io._run, lookup_user=active.io._lookup_user, lookup_group=active.io._lookup_group)
        constructed.append(True)
        return io
    monkeypatch.setattr(installer, 'ManagedInstaller', fresh_installer)
    real_unlink, real_write, real_open = os.unlink, os.write, os.open
    revoke, revoke_calls = active.manager.revoke_device if not inert else None, []
    def interrupt_revoke(device, **kwargs):
        revoke_calls.append(device)
        if len(revoke_calls) == (1 if boundary == 'ceremony' else 2):
            interrupted.append(True)
            raise PairingError('authorization_expired')
        return revoke(device, **kwargs)
    def open_file(name, flags, *args, **kwargs):
        selected = (boundary == 'preparation' and name == INVENTORY.rsplit('/', 1)[1]
                    or boundary == 'before-publication' and name == '.home-retirement.json.pending')
        if selected and flags & os.O_CREAT and not interrupted:
            interrupted.append(True)
            raise OSError('synthetic interruption before original/final publication')
        return real_open(name, flags, *args, **kwargs)
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
    with ExitStack() as serving:
        if not inert:
            serving.enter_context(active.serving(monkeypatch))
        # Simulate root admission only here; real listener/store custody still
        # observes the test user's identity throughout the retirement.
        monkeypatch.setattr(entry, 'os', SimpleNamespace(**(vars(os) | {'geteuid': lambda: 0})))
        active.fence_context.__exit__(None, None, None)
        with monkeypatch.context() as patch:
            patch.setattr(os, 'unlink', unlink)
            patch.setattr(os, 'write', write)
            patch.setattr(os, 'open', open_file)
            if boundary in ('ceremony', 'first-device'):
                patch.setattr(active.manager, 'revoke_device', interrupt_revoke)
            assert entry.main([]) == 1
        if boundary == 'failed-close':
            assert streams[0].cleanup_failed and active.reopened()[0].devices[devices[0]].revoked
            interrupted.append(True)
        assert interrupted and not (active.root / FINAL).exists()
        # Fresh detection neither repairs the interrupted phase nor supplies
        # effect authority. Explicit recovery with incomplete original custody
        # must also preserve every remaining artifact/native/account effect.
        before, effects = active.snapshot(), active.effects()
        assert HomeRetirement(fresh_installer(layout='deb')).detect().state == 'migration pending'
        assert active.snapshot() == before and active.effects() == effects
        proof = active.root / (PIN if boundary == 'preparation' else INVENTORY)
        proof.chmod(0o644)
        damaged, effects, deleted = active.snapshot(), active.effects(), deletions(active.calls)
        assert entry.main([]) == 1
        assert active.snapshot() == damaged and active.effects() == effects and deletions(active.calls) == deleted
        proof.chmod(0o600)
        if boundary in ('preparation', 'ceremony', 'first-device', 'failed-close'):
            saved, _ = active.reopened()
            assert sum(device.revoked for device in saved.devices.values()) == (
                1 if boundary in ('first-device', 'failed-close') else 0)
            if boundary in ('ceremony', 'first-device'):
                assert saved.invite_record is saved.pending_approval is saved.delivery is None
            assert active.effects() == [] and deletions(active.calls) == []
        if boundary == 'failed-close':
            from scripts.test_home_retirement_coordinator import NATIVE
            originals = {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                         for path in (PIN, INVENTORY, NATIVE)}
            effects, frames = active.effects(), len(active.frames)
            assert entry.main([]) == 1
            # A retry may commit further ceremony burns/generations. It must
            # preserve denial, originals and the failed physical acknowledgement.
            assert active.reopened()[0].devices[devices[0]].revoked
            assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                    for path in originals} == originals
            assert active.effects() == effects == [] and deletions(active.calls) == []
            assert active.closed == ['device'] and streams[0].cleanup_failed
            assert len(active.frames) > frames  # Same original owner, never an empty replacement registry.
            serving.close()
            active.native_gone('absent')
        if not inert and boundary not in ('preparation', 'ceremony', 'first-device'):
            from runtime.remote_access.streams import StreamClosed
            with pytest.raises(StreamClosed):
                streams[0].receive()
        state['fault'] = ''
        assert entry.main([]) == 0
    produced = (active.root / FINAL).read_bytes()
    assert json.loads(produced) == {'account': 'absent', 'format': 'home-retirement@1', 'state': 'retired'}
    assert not state['user'] and not state['group']
    assert deletions(active.calls) == ['userdel', 'groupdel'] and reloads == [True]
    if uncertain_command is not None:
        assert active.calls.count(uncertain_command) == 1
    assert len(constructed) >= 4
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
