"""Active preparation/cutoff through real inventory, journals and private IPC.

Run: uv run --frozen pytest scripts/test_home_retirement_coordinator.py -v --tb=short
Only OS ownership/NSS/proc/systemd and the installed package are synthetic.
No fixture computes an inventory, acknowledgement or retirement receipt.
"""
from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import home_retirement_cutoff as cutoff
from runtime.remote_access.home_retirement import HomeRetirement, RetirementUncertain, STATE_DIRECTORY
from runtime.remote_access.home_retirement_coordinator import ActiveRetirement, StoppedRetirement
from runtime.remote_access.home_retirement_inventory import LegacyInventory
from runtime.remote_access.home_retirement_journal import RetirementJournal
from runtime.remote_access.home_retirement_transport import ManagedTransport
from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal
from runtime.remote_access.installer import ManagedInstaller
from runtime.remote_access.streams import StreamClosed
from runtime.remote_access.pairing import PairingError
from scripts.test_home_retirement_cutoff import RecoveryFixture, PIN
from scripts.test_home_retirement_inventory import InventoryFixture, managed_setup, ACCOUNT_RECORD, PUBLICATION
from scripts.test_home_retirement_service import MANAGED_UNITS, LINK, BINARY, ManagedFixture

INVENTORY = STATE_DIRECTORY + '/home-retirement-inventory.json'
NATIVE = STATE_DIRECTORY + '/home-retirement-transport.json'
FINAL = STATE_DIRECTORY + '/home-retirement.json'


@pytest.fixture
def active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request):
    inert = getattr(request, 'param', 'active') == 'inert'
    native = ManagedFixture(tmp_path / 'root', home=False) if inert else RecoveryFixture(tmp_path / 'root')
    legacy = InventoryFixture(tmp_path / 'legacy-input', monkeypatch)
    managed_setup(legacy, live=not inert)
    if inert:
        legacy.set_unit(home=False)
        legacy.stopped()
        legacy.properties['UnitFileState'] = 'disabled'
        for path in (STATE_DIRECTORY + '/home-activation.json', LINK, 'etc/happyranch/invite-issuer.json',
                'etc/happyranch/headscale/policy.hujson',
                *('var/lib/happyranch-connector/trust-state.json' + suffix for suffix in ('', '.anchor', '.lock'))):
            (legacy.root / path).unlink()
        for props in native.native.values():
            props.update(ActiveState='inactive', SubState='dead')
            if 'MainPID' in props:
                props.update(MainPID='0', ControlGroup='')
    # Overlay independent historical inputs and the native package fixture;
    # the active variant also retains its real trust pair and serving owner.
    for source in legacy.root.rglob('*'):
        target = native.root / source.relative_to(legacy.root)
        if target.exists() or target.is_symlink():
            continue
        if source.is_symlink():
            target.symlink_to(str(source.readlink()).replace(str(legacy.root), str(native.root)))
        elif source.is_dir():
            target.mkdir(mode=source.stat().st_mode & 0o777)
        else:
            shutil.copy2(source, target)
    legacy.root = native.root
    legacy.io = native.io
    legacy.properties['FragmentPath'] = str(native.root / 'etc/systemd/system/happyranch-headscale.service')
    # A distinct synthetic system account is essential: an exclusive controller
    # cannot share the managed account or its process inventory. The existing
    # metadata seam models foreign numeric custody without host chown/useradd.
    uid = gid = os.getuid() + 1
    legacy.record.update(uid=uid, gid=gid)
    legacy.publication.update(uid=uid, gid=gid)
    legacy.put_json(ACCOUNT_RECORD, legacy.record)
    legacy.put_json(PUBLICATION, legacy.publication)
    legacy.file('etc/login.defs', f'UID_MIN {uid + 1}\nGID_MIN {gid + 1}\nSYS_UID_MIN 1\nSYS_GID_MIN 1\n'.encode(), 0o644)
    legacy.file('proc/123/status', f'Uid:\t{uid}\t{uid}\t{uid}\t{uid}\nGid:\t{gid}\t{gid}\t{gid}\t{gid}\n'.encode())
    for relative in ('etc/happyranch/headscale', 'var/lib/happyranch-headscale', 'run/happyranch-headscale',
                     'var/lib/happyranch-connector', 'run/happyranch-connector',
                     'var/lib/happyranch-tsnet-sidecar', 'run/happyranch-tsnet-sidecar'):
        (native.root / relative).chmod(0o700)
    original_run = native.run
    def run(argv, **kwargs):
        if argv[:1] == ['systemctl'] and argv[2] == 'happyranch-headscale.service':
            native.calls.append(argv)
            return legacy.run(argv, **kwargs)
        if argv[:1] == ['getent']:
            native.calls.append(argv)
            return (0, f'happyranch-headscale:x:{uid}:{gid}::/nonexistent:/usr/sbin/nologin\n'
                    if argv[1] == 'passwd' else f'happyranch-headscale:x:{gid}:\n')
        if argv == ['passwd', '--status', 'happyranch-headscale']:
            native.calls.append(argv)
            return 0, 'happyranch-headscale L 2026-01-01 0 99999 7 -1\n'
        if argv[:1] == ['ps']:
            code, output = original_run(argv, **kwargs)
            return code, output + (f'123 {uid} {uid} {uid} {uid}\n' if legacy.properties['MainPID'] != '0' else '')
        return original_run(argv, **kwargs)
    def user(name):
        if name == 'happyranch-headscale':
            return SimpleNamespace(pw_name=name, pw_uid=uid, pw_gid=gid, pw_dir='/nonexistent', pw_shell='/usr/sbin/nologin')
        assert name in ('happyranch', 'root', 'release')
        return SimpleNamespace(pw_name=name, pw_uid=os.getuid(), pw_gid=os.getgid())
    def group(name):
        assert name in ('happyranch-headscale', 'happyranch')
        return SimpleNamespace(gr_name=name, gr_gid=gid if name == 'happyranch-headscale' else os.getgid(), gr_mem=[])
    native.io._run, native.io._lookup_user, native.io._lookup_group = run, user, group
    native.io.manager._run = run
    real_stat, real_fstat = os.stat, os.fstat
    prefixes = tuple(native.root / name for name in ('etc/happyranch/headscale', 'var/lib/happyranch-headscale',
        'run/happyranch-headscale', 'proc/123'))
    def owner(info, path):
        if any(path == prefix or path.is_relative_to(prefix) for prefix in prefixes):
            result = SimpleNamespace(**{key: getattr(info, key) for key in dir(info) if key.startswith('st_')})
            result.st_uid, result.st_gid = uid, gid
            return result
        return info
    def observed_stat(name, *args, dir_fd=None, **kwargs):
        info = real_stat(name, *args, dir_fd=dir_fd, **kwargs)
        path = Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name if dir_fd is not None else Path(name)
        return owner(info, path)
    monkeypatch.setattr(os, 'stat', observed_stat)
    monkeypatch.setattr(os, 'fstat', lambda fd: owner(real_fstat(fd), Path(os.readlink(f'/proc/self/fd/{fd}'))))
    native.inventory = LegacyInventory(native.io)
    native.controller = legacy
    try:
        yield native
    finally:
        native.close()


def test_active_preparation_pins_all_original_evidence_before_real_revoke(active, monkeypatch):
    device, _ = active.grant('device')
    stream = active.stream(device, 'device')
    real_sync, real_send = os.fsync, cutoff._send
    synced, sent = set(), []
    def sync(fd):
        real_sync(fd)
        info = os.fstat(fd)
        synced.add((info.st_dev, info.st_ino))
    def send(connection, request):
        method = request.get('method', 'commit')
        if not sent:
            assert request['method'] == 'status'
            assert not (active.root / INVENTORY).exists() and not (active.root / NATIVE).exists()
        paths = (PIN, INVENTORY, NATIVE) if method in ('revoke-all@1', 'commit') else (PIN,)
        for path in paths:
            info = (active.root / path).stat()
            parent = (active.root / path).parent.stat()
            assert (info.st_dev, info.st_ino) in synced
            assert (parent.st_dev, parent.st_ino) in synced
        sent.append(method)
        return real_send(connection, request)
    monkeypatch.setattr(os, 'fsync', sync)
    monkeypatch.setattr(cutoff, '_send', send)
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        assert owner.original == RetirementJournal(active.io, active.fence).load()
        assert owner.native == ManagedTransportJournal(ManagedTransport(active.io, active.fence, owner.original.managed)).load()
        assert not active.reopened()[0].devices[device].revoked
        assert active.closed == [] and stream.receive() == b'synthetic-live-frame'
        assert active.effects() == [] and not (active.root / FINAL).exists()
        receipt = owner.revoke()
        receipt.require()
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        with pytest.raises(StreamClosed):
            stream.receive()
        assert active.effects() == [] and not (active.root / FINAL).exists()
        assert (active.root / 'retained-native/saved-home').read_bytes() == b'synthetic-P25-saved-home'
        assert all((active.root / path).is_file() for path in (PIN, INVENTORY, NATIVE, BINARY))
        assert (active.root / LINK).is_symlink()
        assert 'revoke-all@1' in sent and 'commit' in sent


@pytest.mark.parametrize('damage', ['controller', 'managed-config', 'package', 'original-process',
    'inventory-unknown', 'native-unknown', 'inventory-sync', 'native-sync'])
def test_active_capture_uncertainty_preserves_authority_and_never_reaches_revoke(active, monkeypatch, damage):
    device, _ = active.grant('device')
    stream = active.stream(device, 'device')
    unknown = None
    if damage == 'controller':
        (active.root / 'etc/systemd/system/happyranch-headscale.service').write_bytes(b'foreign controller')
    elif damage == 'managed-config':
        (active.root / 'etc/happyranch/connector.json').write_bytes(b'{}')
    elif damage == 'package':
        (active.root / 'var/lib/dpkg/info/happyranch.md5sums').unlink()
    elif damage == 'original-process':
        active.native[MANAGED_UNITS[0]].update(MainPID='0', ActiveState='inactive', SubState='dead', ControlGroup='')
    elif damage.endswith('-unknown'):
        unknown = active.root / (INVENTORY if damage.startswith('inventory') else NATIVE)
        unknown.write_bytes(b'synthetic-unknown-evidence')
        unknown.chmod(0o600)
    else:
        target = active.root / (INVENTORY if damage.startswith('inventory') else NATIVE)
        real_sync = os.fsync
        def sync(fd):
            if Path(os.readlink(f'/proc/self/fd/{fd}')) == target:
                raise OSError('synthetic interrupted original journal sync')
            return real_sync(fd)
        monkeypatch.setattr(os, 'fsync', sync)
    with active.serving(monkeypatch):
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            ActiveRetirement.capture(active.io, active.fence)
        assert not active.reopened()[0].devices[device].revoked
        assert active.closed == [] and stream.receive() == b'synthetic-live-frame'
        assert not any(b'"revoke-all@1"' in frame for frame in active.frames)
        assert active.effects() == [] and not (active.root / FINAL).exists()
        if unknown is not None:
            assert unknown.read_bytes() == b'synthetic-unknown-evidence'
        assert (active.root / 'retained-native/saved-home').read_bytes() == b'synthetic-P25-saved-home'


@pytest.mark.parametrize('damage', ['private-key', 'controller-process', 'managed-process',
    'inventory-replacement', 'native-replacement', 'peer-binding', 'trust-generation'])
def test_active_revoke_revalidates_original_owners_before_any_mutation(active, monkeypatch, damage):
    device, _ = active.grant('device')
    stream = active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        if damage == 'private-key':
            (active.root / (STATE_DIRECTORY + '/home-ca/ca.key')).write_bytes(b'synthetic-changed-key')
        elif damage in ('controller-process', 'managed-process'):
            path = active.root / f"proc/{123 if damage == 'controller-process' else active.pid}/stat"
            path.write_bytes(path.read_bytes().replace(b'321' if damage == 'controller-process' else b'456', b'987'))
        elif damage.endswith('-replacement'):
            path = active.root / (INVENTORY if damage.startswith('inventory') else NATIVE)
            replacement = path.with_name('.same-bytes-replacement')
            replacement.write_bytes(path.read_bytes())
            replacement.chmod(0o600)
            replacement.replace(path)
        elif damage == 'peer-binding':
            owner.cutoff.peer = replace(owner.cutoff.peer, started=owner.cutoff.peer.started + 1)
        else:
            active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
        frames = len(active.frames)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            owner.revoke()
        assert len(active.frames) == frames
        assert not active.reopened()[0].devices[device].revoked
        assert active.closed == [] and stream.receive() == b'synthetic-live-frame'
        assert active.effects() == [] and not (active.root / FINAL).exists()


def test_active_failed_close_preserves_real_denial_and_all_original_evidence(active, monkeypatch):
    device, _ = active.grant('device')
    def failed_close():
        raise OSError('synthetic physical close failure')
    stream = active.stream(device, 'device', close=failed_close)
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        before = {path: (active.root / path).read_bytes() for path in (PIN, INVENTORY, NATIVE)}
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            owner.revoke()
        assert active.reopened()[0].devices[device].revoked
        assert active.closed == ['device'] and owner.cutoff._acknowledged is None
        assert stream.cleanup_failed
        with pytest.raises(StreamClosed):
            stream.receive()
        assert {path: (active.root / path).read_bytes() for path in before} == before
        assert active.effects() == [] and not (active.root / FINAL).exists()



def test_active_controller_stop_requires_real_cutoff_and_retains_original_artifacts(active, monkeypatch):
    from runtime.remote_access.home_retirement_service import OwnedService
    device, _ = active.grant('device')
    stream = active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        def before_native(argv):
            assert active.reopened()[0].devices[device].revoked
            assert active.closed == ['device']
            with pytest.raises(StreamClosed):
                stream.receive()
            assert all((active.root / path).is_file() for path in (PIN, INVENTORY, NATIVE, BINARY))
        active.controller.effect = before_native
        receipt = owner.revoke_and_stop_controller()
        OwnedService(active.io, owner.original.ownership.service).confirm(receipt)
        assert active.effects() == [['systemctl', 'stop', 'happyranch-headscale.service'],
            ['systemctl', 'disable', 'happyranch-headscale.service']]
        assert active.native[MANAGED_UNITS[0]]['ActiveState'] == 'active'
        assert all((active.root / path).is_file() for path in (PIN, INVENTORY, NATIVE, BINARY))
        assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('drift', ['process-after-revoke', 'process-during-guard', 'denial-before-stop', 'denial-before-disable'])
def test_active_controller_stop_rechecks_original_process_and_cutoff_before_each_effect(active, monkeypatch, drift):
    device, _ = active.grant('device')
    changed, armed = [], []
    def process_change():
        path = active.root / 'proc/123/stat'
        path.write_bytes(path.read_bytes().replace(b'321', b'987'))
        changed.append(True)
    def deny_change():
        active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
        changed.append(True)
    active.stream(device, 'device', close=process_change if drift == 'process-after-revoke' else None)
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        def show(count):
            if owner.cutoff._acknowledged is not None and not armed:
                armed.append(True)
                if drift == 'denial-before-stop':
                    deny_change()
        active.controller.on_show = show
        if drift == 'process-during-guard':
            active.status_hook = lambda: process_change() if armed and not changed else None
        if drift == 'denial-before-disable':
            active.controller.effect = lambda argv: deny_change() if argv[1] == 'stop' else None
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            owner.revoke_and_stop_controller()
        assert changed == [True]
        expected = [['systemctl', 'stop', 'happyranch-headscale.service']] if drift == 'denial-before-disable' else []
        assert active.effects() == expected
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        assert all((active.root / path).is_file() for path in (PIN, INVENTORY, NATIVE, BINARY))
        assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('failure', ['stop', 'disable', 'lost-stop-reply', 'lost-disable-reply'])
def test_active_controller_native_failure_retains_denial_and_recovery_evidence(active, monkeypatch, failure):
    device, _ = active.grant('device')
    active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        operation = 'disable' if 'disable' in failure else 'stop'
        run = active.io._run
        if failure.startswith('lost-'):
            def lose_reply(argv, **kwargs):
                result = run(argv, **kwargs)
                return (1, 'synthetic lost controller reply') if argv == [
                    'systemctl', operation, 'happyranch-headscale.service'] else result
            active.io._run = active.io.manager._run = lose_reply
        else:
            active.controller.fail = failure
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            owner.revoke_and_stop_controller()
        assert active.effects() == [['systemctl', 'stop', 'happyranch-headscale.service']] + (
            [['systemctl', 'disable', 'happyranch-headscale.service']] if operation == 'disable' else [])
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        assert all((active.root / path).is_file() for path in (PIN, INVENTORY, NATIVE, BINARY))
        assert not (active.root / FINAL).exists()
        active.controller.fail = None
        active.io._run = active.io.manager._run = run
        if failure == 'lost-disable-reply':
            # Real stop can flush an original database and remove its WAL.
            # Recovery must retain the original surviving inode/custody.
            database = active.root / 'var/lib/happyranch-headscale/db.sqlite'
            database.write_bytes(database.read_bytes() + b'synthetic-stop-flush')
            (database.parent / 'db.sqlite-wal').unlink(missing_ok=True)
        records = {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                   for path in (PIN, INVENTORY, NATIVE)}
        effects = len(active.effects())
        native_target_stop(active)
        recovered = (ActiveRetirement.resume_revoke if failure == 'stop' else
                     ActiveRetirement.resume_controller)(active.io, active.fence)
        recovered.require()
        remaining = (['stop', 'disable'] if failure == 'stop' else
                     [] if failure == 'lost-disable-reply' else ['disable'])
        assert [argv[1] for argv in active.effects()[effects:]
                if argv[2] == 'happyranch-headscale.service'] == remaining
        assert active.closed == ['device']
        assert recovered._owner.original == owner.original and recovered._owner.native == owner.native
        assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                for path in records} == records
        assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('drift', ['controller-process', 'controller-link', 'private-key',
    'runtime-inode', 'runtime-unknown', 'managed-process', 'failed-close'])
def test_controller_recovery_refuses_changed_originals_and_failed_close(active, monkeypatch, drift):
    device, _ = active.grant('device')
    def close():
        if drift == 'failed-close':
            raise OSError('synthetic terminal physical close failure')
    stream = active.stream(device, 'device', close=close)
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        active.controller.fail = 'disable'
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            owner.revoke_and_stop_controller()
        active.controller.fail = None
        if drift == 'controller-process':
            active.controller.properties.update(ActiveState='active', SubState='running', MainPID='123',
                ControlGroup='/system.slice/happyranch-headscale.service')
            active.controller.process_start(999)
        elif drift == 'controller-link':
            path = active.root / LINK
            path.rename(active.root.parent / 'kept-controller-link')
            path.symlink_to('/etc/systemd/system/happyranch-headscale.service')
        elif drift == 'private-key':
            (active.root / (STATE_DIRECTORY + '/home-ca/ca.key')).write_bytes(b'synthetic-changed-key')
        elif drift == 'runtime-inode':
            path = active.root / 'var/lib/happyranch-headscale/db.sqlite'
            path.rename(active.root.parent / 'kept-runtime')
            path.write_bytes(b'synthetic-replaced-runtime')
            path.chmod(0o600)
        elif drift == 'runtime-unknown':
            (active.root / 'var/lib/happyranch-headscale/unknown').write_bytes(b'synthetic-unknown')
        elif drift == 'managed-process':
            path = active.root / f'proc/{active.pid}/stat'
            path.write_bytes(path.read_bytes().replace(b'456', b'999'))
        before, effects, closed = active.snapshot(), active.effects(), list(active.closed)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            ActiveRetirement.resume_controller(active.io, active.fence)
        assert active.snapshot() == before and active.effects() == effects and active.closed == closed
        assert active.reopened()[0].devices[device].revoked
        assert stream.closed and (stream.cleanup_failed is (drift == 'failed-close'))
        assert not (active.root / FINAL).exists()



@pytest.mark.parametrize('case', ['target-failure', 'target-lost-reply', 'sidecar-failure',
    'sidecar-lost-reply', 'sidecar-inode', 'sidecar-unknown', 'live-sidecar-flush',
    'connector-process', 'connector-runtime', 'managed-config'])
def test_partial_managed_stop_recovery_rechecks_originals_and_skips_complete_effects(active, monkeypatch, case):
    device, _ = active.grant('device')
    stream = active.stream(device, 'device')
    unit = ('happyranch-managed.target' if case.startswith('target-') else
            'happyranch-tsnet-sidecar.service')
    lost = case not in ('target-failure', 'sidecar-failure', 'live-sidecar-flush')
    run = active.io._run
    def interrupt(argv, **kwargs):
        if argv == ['systemctl', 'stop', unit]:
            if lost:
                run(argv, **kwargs)
            return 1, 'synthetic interrupted managed stop'
        return run(argv, **kwargs)
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        active.io._run = active.io.manager._run = interrupt
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            owner.revoke_and_stop_services()
        active.io._run = active.io.manager._run = run
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        assert stream.closed and active.native['happyranch-connector.service']['ActiveState'] == 'active'
        sidecar_state = active.root / 'var/lib/happyranch-tsnet-sidecar/tailscaled.state'
        if case in ('sidecar-lost-reply', 'live-sidecar-flush'):
            sidecar_state.write_bytes(sidecar_state.read_bytes() + b'synthetic-stopped-flush')
            if case == 'sidecar-lost-reply':
                (active.root / 'run/happyranch-tsnet-sidecar/health.json').unlink(missing_ok=True)
        elif case == 'sidecar-inode':
            data = sidecar_state.read_bytes()
            sidecar_state.rename(active.root.parent / 'retained-sidecar-state')
            sidecar_state.write_bytes(data)
            sidecar_state.chmod(0o600)
        elif case == 'sidecar-unknown':
            (sidecar_state.parent / 'unknown').write_bytes(b'synthetic-unknown')
        elif case == 'connector-process':
            path = active.root / f'proc/{active.pid}/stat'
            path.write_bytes(path.read_bytes().replace(b'456', b'999'))
        elif case == 'connector-runtime':
            (active.root / 'run/happyranch-connector/unknown').write_bytes(b'synthetic-unknown')
        elif case == 'managed-config':
            (active.root / 'etc/happyranch/connector.json').write_bytes(b'synthetic-changed-config')
        records = {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                   for path in (PIN, INVENTORY, NATIVE)}
        before, effects = active.snapshot(), active.effects()
        if case.endswith(('failure', 'lost-reply')):
            # The manager finishes the interrupted target's consumer shutdown
            # while the next stop returns. Supply only native observations,
            # never a cutoff/stop receipt or installed-systemd claim.
            def finish_native(argv, **kwargs):
                result = run(argv, **kwargs)
                if argv[:2] == ['systemctl', 'stop'] and result[0] == 0:
                    active.native_gone('absent')
                return result
            active.io._run = active.io.manager._run = finish_native
            stopped = ActiveRetirement.resume_controller(active.io, active.fence)
            stopped.require()
            remaining = [argv for argv in active.effects()[len(effects):] if argv[1] == 'stop']
            expected = ('happyranch-managed.target' if case == 'target-failure' else
                        'happyranch-connector.service' if case == 'sidecar-lost-reply' else
                        'happyranch-tsnet-sidecar.service')
            assert remaining == [['systemctl', 'stop', expected]]
            assert stopped._owner.original == owner.original and stopped._owner.native == owner.native
        else:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                ActiveRetirement.resume_controller(active.io, active.fence)
            assert active.snapshot() == before and active.effects() == effects
        assert active.closed == ['device']
        assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                for path in records} == records
        assert not (active.root / FINAL).exists()


def native_target_stop(active, *, drift=None):
    """Synthetic systemd PartOf result and proc facts, never a cutoff proof."""
    run = active.io._run
    def native(argv, **kwargs):
        result = run(argv, **kwargs)
        if argv == ['systemctl', 'stop', 'happyranch-managed.target'] and result[0] == 0:
            active.native_gone('absent')
            if drift == 'denial':
                active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
            elif drift == 'unit':
                (active.root / 'etc/systemd/system/happyranch-connector.service').write_bytes(b'foreign managed unit')
        return result
    active.io._run = active.io.manager._run = native


def test_active_all_services_stop_uses_original_death_after_actual_target_quiescence(active, monkeypatch):
    device, _ = active.grant('device')
    stream = active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        native_target_stop(active)
        receipt = owner.revoke_and_stop_services()
        receipt.require()
        assert repr(receipt) == 'StoppedRetirement(<redacted>)'
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        with pytest.raises(StreamClosed):
            stream.receive()
        assert active.effects() == [['systemctl', 'stop', 'happyranch-headscale.service'],
            ['systemctl', 'disable', 'happyranch-headscale.service'],
            ['systemctl', 'stop', 'happyranch-managed.target'],
            ['systemctl', 'disable', 'happyranch-managed.target'],
            ['systemctl', 'disable', 'happyranch-release-verifier.socket']]
        assert all(props['ActiveState'] == 'inactive' for props in active.native.values())
        assert all((active.root / path).is_file() for path in (PIN, INVENTORY, NATIVE, BINARY))
        assert not (active.root / FINAL).exists()
        with receipt.held_operations() as exclusion:
            exclusion.require()
            assert not (active.root / FINAL).exists()
        # A later mutation invalidates the readback rather than authorizing
        # cleanup from a stale successful native result.
        active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            receipt.require()
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            with receipt.held_operations():
                pytest.fail('stale native/cutoff proof must not enter operation exclusion')


@pytest.mark.parametrize('missing', ['partial-native-stop', 'denial', 'unit'])
def test_active_managed_stop_requires_complete_independent_proof_after_peer_loss(active, monkeypatch, missing):
    device, _ = active.grant('device')
    active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        if missing == 'partial-native-stop':
            run = active.io._run
            def native(argv, **kwargs):
                result = run(argv, **kwargs)
                if argv == ['systemctl', 'stop', 'happyranch-connector.service'] and result[0] == 0:
                    (active.root / f'proc/{active.pid}').rename(active.root.parent / 'retained-original-process')
                return result
            active.io._run = active.io.manager._run = native
        else:
            native_target_stop(active, drift=missing)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            owner.revoke_and_stop_services()
        attempted = active.effects()
        assert attempted[:3] == [['systemctl', 'stop', 'happyranch-headscale.service'],
            ['systemctl', 'disable', 'happyranch-headscale.service'], ['systemctl', 'stop', 'happyranch-managed.target']]
        if missing == 'partial-native-stop':
            assert attempted[3:] == [['systemctl', 'stop', 'happyranch-tsnet-sidecar.service'],
                ['systemctl', 'stop', 'happyranch-connector.service']]
            assert active.native['happyranch-release-verifier.service']['ActiveState'] == 'active'
        else:
            assert len(attempted) == 3
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        assert all((active.root / path).is_file() for path in (PIN, INVENTORY, NATIVE, BINARY))
        assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('case', ['same-boot', 'new-boot', 'missing-inventory', 'missing-native', 'missing-peer',
    'torn-inventory', 'changed-controller', 'changed-managed', 'active-managed', 'controller-enabled',
    'live-trust', 'missing-trust', 'original-live'])
def test_stopped_explicit_recovery_uses_saved_original_and_never_replays(active, monkeypatch, case):
    device, _ = active.grant('device')
    stream = active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        original, native = owner.original, owner.native
        native_target_stop(active)
        produced = owner.revoke_and_stop_services()
        produced.require()
    # The native post-effect inputs above came from the actual stop/disable
    # entry; no test fixture seeds an acknowledgement or recovery result.
    if case in ('missing-inventory', 'missing-native', 'missing-peer'):
        (active.root / {'missing-inventory': INVENTORY, 'missing-native': NATIVE, 'missing-peer': PIN}[case]).unlink()
    elif case == 'torn-inventory':
        (active.root / INVENTORY).write_bytes(b'{"format":')
    elif case in ('changed-controller', 'changed-managed'):
        name = 'happyranch-headscale.service' if case == 'changed-controller' else 'happyranch-connector.service'
        path = active.root / 'etc/systemd/system' / name
        saved = active.root.parent / 'original-native-fragment'
        path.rename(saved)
        shutil.copy2(saved, path)
    elif case == 'active-managed':
        active.native['happyranch-managed.target'].update(ActiveState='active', SubState='active')
    elif case == 'controller-enabled':
        (active.root / LINK).symlink_to('/etc/systemd/system/happyranch-headscale.service')
        active.controller.properties['UnitFileState'] = 'enabled'
    elif case == 'live-trust':
        active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
    elif case == 'missing-trust':
        (active.path.parent / 'trust-state.json.anchor').unlink()
    elif case == 'original-live':
        (active.root.parent / f'retained-proc-{active.pid}').rename(active.root / f'proc/{active.pid}')
    elif case == 'new-boot':
        path = active.root / 'proc/sys/kernel/random/boot_id'
        path.chmod(0o644)
        path.write_bytes(b'22222222-2222-4222-8222-222222222222\n')
        path.chmod(0o444)
    before, effects, frames = active.snapshot(), active.effects(), list(active.frames)
    if case in ('same-boot', 'new-boot'):
        recovered = StoppedRetirement.from_saved(active.io, active.fence)
        recovered.require()
        assert recovered._owner is not owner
        assert recovered._owner.original == original and recovered._owner.native == native
        assert recovered._owner.cutoff._acknowledged is None
        with recovered.held_operations() as exclusion:
            exclusion.require()
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        with pytest.raises(StreamClosed):
            stream.receive()
        assert active.io.home_retirement_status()['state'] == 'migration pending'
    else:
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            StoppedRetirement.from_saved(active.io, active.fence)
    assert active.snapshot() == before and active.effects() == effects and active.frames == frames
    assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('case', ['target', 'socket', 'lost-target-reply', 'later-boot',
    'denial', 'managed-active', 'controller-fragment', 'managed-link', 'controller-process',
    'controller-live-after-boot', 'denial-during-disable'])
def test_native_recovery_finishes_only_remaining_owned_effects(active, monkeypatch, case):
    from scripts.test_home_retirement_service import MANAGED_LINKS
    device, _ = active.grant('device')
    active.stream(device, 'device')
    target, verifier = 'happyranch-managed.target', 'happyranch-release-verifier.socket'
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        native_target_stop(active)
        if case == 'lost-target-reply':
            run = active.io._run
            def lose_reply(argv, **kwargs):
                result = run(argv, **kwargs)
                return (1, 'synthetic lost native reply') if argv == ['systemctl', 'disable', target] else result
            active.io._run = active.io.manager._run = lose_reply
        else:
            active.fail = ('disable', verifier if case == 'socket' else target)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            owner.revoke_and_stop_services()
    active.fail = None
    # Remove only the test-side lost-reply adapter, never production guards.
    if case == 'lost-target-reply':
        active.io._run = active.io.manager._run = run
    if case == 'denial':
        active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
    elif case == 'managed-active':
        active.native[target].update(ActiveState='active', SubState='active')
    elif case == 'controller-fragment':
        path = active.root / 'etc/systemd/system/happyranch-headscale.service'
        saved = active.root.parent / 'original-controller-fragment'
        path.rename(saved)
        shutil.copy2(saved, path)
    elif case == 'managed-link':
        path = active.root / MANAGED_LINKS[target]
        path.rename(active.root.parent / 'original-managed-link')
        path.symlink_to('/etc/systemd/system/' + target)
    elif case in ('later-boot', 'controller-live-after-boot'):
        path = active.root / 'proc/sys/kernel/random/boot_id'
        path.chmod(0o644)
        path.write_bytes(b'22222222-2222-4222-8222-222222222222\n')
        path.chmod(0o444)
    if case in ('controller-process', 'controller-live-after-boot'):
        active.controller.properties.update(ActiveState='active', SubState='running', MainPID='123',
            ControlGroup='/system.slice/happyranch-headscale.service')
        if case == 'controller-process':
            active.controller.process_start(999)
    changed = []
    if case == 'denial-during-disable':
        run = active.io._run
        def drift_after_disable(argv, **kwargs):
            result = run(argv, **kwargs)
            if argv == ['systemctl', 'disable', target] and result[0] == 0:
                active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
                changed.append(active.snapshot())
            return result
        active.io._run = active.io.manager._run = drift_after_disable
    before, effects, frames = active.snapshot(), active.effects(), list(active.frames)
    records = {path: (active.root / path).read_bytes() for path in (PIN, INVENTORY, NATIVE)}
    if case in ('target', 'socket', 'lost-target-reply', 'later-boot'):
        recovered = StoppedRetirement.resume_native(active.io, active.fence)
        recovered.require()
        remaining = [verifier] if case in ('socket', 'lost-target-reply') else [target, verifier]
        assert active.effects()[len(effects):] == [['systemctl', 'disable', name] for name in remaining]
        assert all(not (active.root / path).exists() for path in MANAGED_LINKS.values())
        count = len(active.effects())
        again = StoppedRetirement.resume_native(active.io, active.fence)
        again.require()
        assert len(active.effects()) == count
        assert recovered._owner.original == owner.original and recovered._owner.native == owner.native
    else:
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            StoppedRetirement.resume_native(active.io, active.fence)
        if case == 'denial-during-disable':
            assert changed
            assert active.effects()[len(effects):] == [['systemctl', 'disable', target]]
            assert active.snapshot() == changed[0]
        else:
            assert active.snapshot() == before and active.effects() == effects
    assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
    assert active.frames == frames
    assert {path: (active.root / path).read_bytes() for path in records} == records
    assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('boundary', ['complete', 'denial-between-leaves', 'denial-between-directories'])
def test_stopped_private_cleanup_uses_real_proofs_and_preserves_explicit_recovery(active, monkeypatch, boundary):
    if boundary != 'denial-between-leaves':
        from scripts.test_home_retirement_inventory import auxiliary_journal_setup
        auxiliary_journal_setup(active.controller)
    device, _ = active.grant('device')
    active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        native_target_stop(active)
        stopped = owner.revoke_and_stop_services()
        effects = active.effects()
        real_unlink, removed = os.unlink, []
        private = {str(active.root / path) for path in owner.original.private.members}
        def unlink(name, *, dir_fd=None):
            real_unlink(name, dir_fd=dir_fd)
            path = str(Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name)
            if path in private:
                removed.append(path)
                if boundary == 'denial-between-leaves' and len(removed) == 1:
                    active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
        with monkeypatch.context() as scoped:
            scoped.setattr(os, 'unlink', unlink)
            if boundary == 'denial-between-leaves':
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    stopped.remove_private()
                assert len(removed) == 1
            else:
                stopped.remove_private()
                assert len(removed) > 10
                count = len(removed)
                # Saved objects can reopen the partially cleaned layout because
                # denial, native artifacts and immutable originals survive.
                recovered = StoppedRetirement.from_saved(active.io, active.fence)
                recovered.remove_private()
                assert len(removed) == count
                assert all(not (active.root / path).exists() for path in owner.original.private.members)
        if boundary != 'denial-between-leaves':
            from scripts.test_home_retirement_inventory import OPERATION_LOCKS, writer_lock
            roots = ('etc/happyranch/headscale', 'run/happyranch-headscale',
                'var/lib/happyranch-headscale', STATE_DIRECTORY + '/home-ca')
            def fresh():
                io = ManagedInstaller(root=active.root, root_owner=active.io.root_owner, layout='deb',
                    run=active.io._run, lookup_user=active.io._lookup_user, lookup_group=active.io._lookup_group)
                return StoppedRetirement.from_saved(io, active.fence)
            real_rmdir, directories = os.rmdir, []
            def rmdir(name, *, dir_fd=None):
                assert all(writer_lock(active.root, lock) == 73 for lock in OPERATION_LOCKS)
                real_rmdir(name, dir_fd=dir_fd)
                directories.append(name)
                if boundary == 'denial-between-directories' and len(directories) == 1:
                    active.manager.issue_invite_record('e' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
            with monkeypatch.context() as scoped:
                scoped.setattr(os, 'rmdir', rmdir)
                if boundary == 'complete':
                    fresh().remove_private_directories()
                    assert all(not (active.root / path).exists() for path in roots)
                    fresh().remove_private_directories()
                    assert len(directories) == len(roots)
                else:
                    with pytest.raises(RetirementUncertain, match='^migration pending$'):
                        fresh().remove_private_directories()
                    assert len(directories) == 1
                    after = active.snapshot()
                    with pytest.raises(RetirementUncertain, match='^migration pending$'):
                        fresh().remove_private_directories()
                    assert active.snapshot() == after
        assert active.effects() == effects
        assert all((active.root / path).is_file() for path in (PIN, INVENTORY, NATIVE, BINARY))
        assert RetirementJournal(active.io, active.fence).load() == owner.original
        assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('case', ['complete', 'lost-sync', 'denial-between-units', 'foreign-unit',
    'foreign-parent', 'native-active'])
def test_stopped_unit_cleanup_recovers_only_original_fragments(active, monkeypatch, case):
    from scripts.test_home_retirement_inventory import auxiliary_journal_setup, OPERATION_LOCKS, writer_lock
    auxiliary_journal_setup(active.controller)
    device, _ = active.grant('device')
    stream = active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        native_target_stop(active)
        owner.revoke_and_stop_services()
    paths = {'etc/systemd/system/' + name for name in (*MANAGED_UNITS, 'happyranch-headscale.service')}
    retained = {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                for path in (PIN, INVENTORY, NATIVE, BINARY)}
    effects, frames = active.effects(), list(active.frames)
    if case == 'foreign-unit':
        path = active.root / 'etc/systemd/system/happyranch-connector.service'
        data = path.read_bytes()
        path.rename(active.root.parent / 'original-unit')
        active.file(str(path.relative_to(active.root)), data, 0o644)
    elif case == 'native-active':
        active.native['happyranch-managed.target'].update(ActiveState='active', SubState='active')
    elif case == 'foreign-parent':
        path = active.root / 'etc/systemd/system'
        saved = active.root.parent / 'original-unit-parent'
        path.rename(saved)
        shutil.copytree(saved, path, symlinks=True)
    before = active.snapshot()
    # Fresh detection is read-only and cannot resume cleanup on its own.
    assert active.io.home_retirement_status()['state'] == 'migration pending'
    assert active.snapshot() == before and active.effects() == effects
    real_unlink, real_sync = os.unlink, os.fsync
    removed, synced = [], []
    def unlink(name, *, dir_fd=None):
        path = str((Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name).relative_to(active.root))
        if path in paths:
            assert all(writer_lock(active.root, lock) == 73 for lock in OPERATION_LOCKS)
        real_unlink(name, dir_fd=dir_fd)
        if path in paths:
            removed.append(path)
            if case == 'denial-between-units' and len(removed) == 1:
                active.manager.issue_invite_record('e' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
    def sync(fd):
        if Path(os.readlink(f'/proc/self/fd/{fd}')) == active.root / 'etc/systemd/system':
            synced.append(len(removed))
            if case == 'lost-sync' and len(synced) == 1:
                raise OSError('synthetic lost unit-parent sync')
        real_sync(fd)
    def fresh():
        io = ManagedInstaller(root=active.root, root_owner=active.io.root_owner, layout='deb',
            run=active.io._run, lookup_user=active.io._lookup_user, lookup_group=active.io._lookup_group)
        StoppedRetirement.remove_unit_fragments(io, active.fence)
    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'unlink', unlink)
        scoped.setattr(os, 'fsync', sync)
        if case == 'complete':
            fresh()
        else:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
            assert len(removed) == (1 if case in ('lost-sync', 'denial-between-units') else 0)
        if case in ('complete', 'lost-sync'):
            fresh()
            assert set(removed) == paths and len(removed) == len(paths)
            assert all(not (active.root / path).exists() for path in paths)
            sync_count = len(synced)
            fresh()
            assert len(removed) == len(paths) and len(synced) == sync_count + len(paths)
        else:
            after = active.snapshot()
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
            assert active.snapshot() == after
            if not removed:
                assert after == before
    assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
    with pytest.raises(StreamClosed):
        stream.receive()
    assert active.effects() == effects and active.frames == frames
    assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
            for path in retained} == retained
    assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('active,boundary', [('active', 'complete'), ('active', 'lost-sync'),
    ('active', 'authority-between-directories'), ('active', 'foreign-parent'),
    ('inert', 'complete'), ('inert', 'authority-between-directories')], indirect=['active'])
def test_managed_directory_cleanup_keeps_fresh_active_or_inert_proof(active, monkeypatch, request, boundary):
    from runtime.remote_access.home_retirement_inert import InertRetirement
    from scripts.test_home_retirement_inventory import auxiliary_journal_setup, OPERATION_LOCKS, TRANSACTION, writer_lock
    inert = request.node.callspec.params['active'] == 'inert'
    auxiliary_journal_setup(active.controller)
    if inert:
        (active.root / TRANSACTION).unlink()
        owner = InertRetirement.capture(active.io, active.fence)
        owner.stop_and_disable()
        owner.remove_leaves()
    else:
        device, _ = active.grant('device')
        stream = active.stream(device, 'device')
        with active.serving(monkeypatch):
            owner = ActiveRetirement.capture(active.io, active.fence)
            native_target_stop(active)
            owner.revoke_and_stop_services()
        # Fresh recovery owns a new request, never a reset within an effect.
        StoppedRetirement.from_saved(active.io, active.fence).remove_managed()
    cleanup = InertRetirement if inert else StoppedRetirement
    if boundary == 'complete':
        # Compose actual successful original-unit removal with this next phase.
        cleanup.remove_unit_fragments(active.io, active.fence)
    paths = {'var/lib/happyranch-tsnet-sidecar', 'run/happyranch-connector', 'run/happyranch-tsnet-sidecar'}
    trust = 'var/lib/happyranch-connector/trust-state.json'
    retained = {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                for path in (INVENTORY, NATIVE, BINARY, ACCOUNT_RECORD, PUBLICATION,
                    *((PIN, trust + '.lock') if not inert else ()),
                    *((trust, trust + '.anchor') if not inert and boundary != 'authority-between-directories' else ()))}
    effects, frames = active.effects(), list(getattr(active, 'frames', []))
    if boundary == 'foreign-parent':
        path = active.root / 'run'
        saved = active.root.parent / 'original-managed-run'
        path.rename(saved)
        shutil.copytree(saved, path, symlinks=True)
    before = active.snapshot()
    assert active.io.home_retirement_status()['state'] == 'migration pending'
    assert active.snapshot() == before and active.effects() == effects
    real_rmdir, real_sync = os.rmdir, os.fsync
    removed, synced, injected = [], [], []
    def rmdir(name, *, dir_fd=None):
        path = str((Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name).relative_to(active.root))
        if path in paths:
            assert all(writer_lock(active.root, lock) == 73 for lock in OPERATION_LOCKS)
        real_rmdir(name, dir_fd=dir_fd)
        if path in paths:
            removed.append(path)
            if boundary == 'authority-between-directories' and len(removed) == 1:
                if inert:
                    active.file(trust, b'synthetic-reappearing-authority', 0o600)
                else:
                    active.manager.issue_invite_record('e' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
                injected.append(active.snapshot())
    def sync(fd):
        parent = Path(os.readlink(f'/proc/self/fd/{fd}'))
        if parent in {active.root / 'run', active.root / 'var/lib'}:
            synced.append(parent)
            if boundary == 'lost-sync' and removed and len(synced) == 1:
                raise OSError('synthetic lost managed-directory parent sync')
        real_sync(fd)
    def fresh():
        io = ManagedInstaller(root=active.root, root_owner=active.io.root_owner, layout='deb',
            run=active.io._run, lookup_user=active.io._lookup_user, lookup_group=active.io._lookup_group)
        cleanup.remove_managed_directories(io, active.fence)
    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'rmdir', rmdir)
        scoped.setattr(os, 'fsync', sync)
        if boundary == 'complete':
            fresh()
        else:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
            assert len(removed) == (0 if boundary == 'foreign-parent' else 1)
        if boundary in ('complete', 'lost-sync'):
            fresh()
            assert set(removed) == paths and len(removed) == len(paths)
            count = len(synced)
            fresh()
            assert len(removed) == len(paths) and len(synced) == count + len(paths)
            assert all(not (active.root / path).exists() for path in paths)
        else:
            after = active.snapshot()
            assert after == (injected[0] if injected else before)
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
            assert active.snapshot() == after
    assert active.effects() == effects and list(getattr(active, 'frames', [])) == frames
    assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
            for path in retained} == retained
    assert all((active.root / path).exists() for path in OPERATION_LOCKS)
    assert (active.root / 'retained-native/saved-home').read_bytes() == b'synthetic-P25-saved-home'
    if not inert:
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        with pytest.raises(StreamClosed):
            stream.receive()
    assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('active', ['active', 'inert'], indirect=True)
@pytest.mark.parametrize('boundary', ['complete', 'lost-sync', 'native-after-unlink'])
def test_controller_executable_cleanup_recovers_with_fresh_active_or_inert_proof(
        active, monkeypatch, request, boundary):
    from runtime.remote_access.home_retirement_inert import InertRetirement
    from scripts.test_home_retirement_inventory import auxiliary_journal_setup, OPERATION_LOCKS, TRANSACTION, writer_lock
    inert = request.node.callspec.params['active'] == 'inert'
    auxiliary_journal_setup(active.controller)
    if inert:
        (active.root / TRANSACTION).unlink()
        owner = InertRetirement.capture(active.io, active.fence)
        owner.stop_and_disable()
        owner.remove_leaves()
    else:
        device, _ = active.grant('device')
        stream = active.stream(device, 'device')
        with active.serving(monkeypatch):
            owner = ActiveRetirement.capture(active.io, active.fence)
            native_target_stop(active)
            owner.revoke_and_stop_services()
    cleanup = InertRetirement if inert else StoppedRetirement
    if boundary == 'complete':
        cleanup.remove_unit_fragments(active.io, active.fence)
    retained_paths = [INVENTORY, NATIVE, ACCOUNT_RECORD, PUBLICATION,
        'opt/happyranch-desktop/python/bin/python3.12',
        'opt/happyranch-desktop/bin/happyranch-tsnet-sidecar',
        'var/lib/dpkg/info/happyranch.md5sums', 'retained-native/saved-home']
    if not inert:
        retained_paths.extend([PIN, *('var/lib/happyranch-connector/trust-state.json' + suffix
            for suffix in ('', '.anchor', '.lock'))])
    retained = {path: ((active.root / path).stat().st_ino, (active.root / path).read_bytes()) for path in retained_paths}
    effects, frames = active.effects(), list(getattr(active, 'frames', []))
    target = active.root / BINARY
    real_unlink, real_sync = os.unlink, os.fsync
    removed, synced = [], []
    def unlink(name, *, dir_fd=None):
        path = Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name
        if path == target:
            assert all(writer_lock(active.root, lock) == 73 for lock in OPERATION_LOCKS)
        real_unlink(name, dir_fd=dir_fd)
        if path == target:
            removed.append(name)
            if boundary == 'native-after-unlink':
                active.native['happyranch-managed.target'].update(ActiveState='active', SubState='active')
    def sync(fd):
        if Path(os.readlink(f'/proc/self/fd/{fd}')) == target.parent:
            synced.append(True)
            if boundary == 'lost-sync' and len(synced) == 1:
                raise OSError('synthetic dedicated executable parent sync interruption')
        real_sync(fd)
    def fresh():
        io = ManagedInstaller(root=active.root, root_owner=active.io.root_owner, layout='deb',
            run=active.io._run, lookup_user=active.io._lookup_user, lookup_group=active.io._lookup_group)
        cleanup.remove_controller_executable(io, active.fence)
    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'unlink', unlink)
        scoped.setattr(os, 'fsync', sync)
        if boundary == 'complete':
            fresh()
        else:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
        assert removed == ['headscale'] and not target.exists()
        if boundary == 'native-after-unlink':
            after = active.snapshot()
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
            assert active.snapshot() == after
        else:
            fresh()
            count = len(synced)
            fresh()
            assert len(synced) == count + 1
        assert removed == ['headscale']
    assert active.effects() == effects and list(getattr(active, 'frames', [])) == frames
    assert {path: ((active.root / path).stat().st_ino, (active.root / path).read_bytes()) for path in retained} == retained
    assert all((active.root / path).exists() for path in OPERATION_LOCKS)
    assert active.io.home_retirement_status()['state'] == 'migration pending'
    if not inert:
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        with pytest.raises(StreamClosed):
            stream.receive()
    assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('active', ['active', 'inert'], indirect=True)
@pytest.mark.parametrize('boundary', ['complete', 'lost-sync', 'native-after-unlink'])
def test_operation_domain_cleanup_keeps_independent_active_or_inert_proof(
        active, monkeypatch, request, boundary):
    from runtime.remote_access.home_retirement_inert import InertRetirement
    from scripts.test_home_retirement_inventory import auxiliary_journal_setup, ISSUER, MINTER, OPERATION_LOCKS, TRANSACTION, writer_lock
    inert = request.node.callspec.params['active'] == 'inert'
    auxiliary_journal_setup(active.controller)
    if inert:
        (active.root / TRANSACTION).unlink()
        owner = InertRetirement.capture(active.io, active.fence)
        owner.stop_and_disable()
        owner.remove_leaves()
    else:
        device, _ = active.grant('device')
        stream = active.stream(device, 'device')
        with active.serving(monkeypatch):
            owner = ActiveRetirement.capture(active.io, active.fence)
            native_target_stop(active)
            stopped = owner.revoke_and_stop_services()
            stopped.remove_auxiliary()
    cleanup = InertRetirement if inert else StoppedRetirement
    if boundary == 'complete':
        cleanup.remove_controller_executable(active.io, active.fence)
    retained_paths = [INVENTORY, NATIVE, ACCOUNT_RECORD, PUBLICATION, 'retained-native/saved-home']
    if not inert:
        retained_paths.extend([PIN, *('var/lib/happyranch-connector/trust-state.json' + suffix
            for suffix in ('', '.anchor', '.lock'))])
    retained = {path: ((active.root / path).stat().st_ino, (active.root / path).read_bytes()) for path in retained_paths}
    effects, frames = active.effects(), list(getattr(active, 'frames', []))
    domains = {*OPERATION_LOCKS, ISSUER, MINTER}
    real_unlink, real_rmdir, real_sync = os.unlink, os.rmdir, os.fsync
    removed, interrupted = [], []
    def remove(operation, name, dir_fd):
        path = str((Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name).relative_to(active.root))
        if path in domains:
            assert all(writer_lock(active.root, lock) == 73 for lock in OPERATION_LOCKS
                       if (active.root / lock).exists())
        operation(name, dir_fd=dir_fd)
        if path in domains:
            removed.append(path)
            if boundary == 'native-after-unlink':
                active.native['happyranch-managed.target'].update(ActiveState='active', SubState='active')
    def unlink(name, *, dir_fd=None):
        remove(real_unlink, name, dir_fd)
    def rmdir(name, *, dir_fd=None):
        remove(real_rmdir, name, dir_fd)
    def sync(fd):
        if (boundary == 'lost-sync' and removed and not interrupted
                and Path(os.readlink(f'/proc/self/fd/{fd}')) == active.root / ISSUER):
            interrupted.append(True)
            raise OSError('synthetic original issuer lock parent sync interruption')
        real_sync(fd)
    def fresh():
        io = ManagedInstaller(root=active.root, root_owner=active.io.root_owner, layout='deb',
            run=active.io._run, lookup_user=active.io._lookup_user, lookup_group=active.io._lookup_group)
        cleanup.remove_operation_domains(io, active.fence)
    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'unlink', unlink)
        scoped.setattr(os, 'rmdir', rmdir)
        scoped.setattr(os, 'fsync', sync)
        if boundary == 'complete':
            fresh()
        else:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
            assert removed == [OPERATION_LOCKS[0]]
        if boundary == 'native-after-unlink':
            after = active.snapshot()
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
            assert active.snapshot() == after
        else:
            fresh()
            fresh()
            assert set(removed) == domains and len(removed) == len(domains)
            assert all(not (active.root / path).exists() for path in domains)
    assert active.effects() == effects and list(getattr(active, 'frames', [])) == frames
    assert {path: ((active.root / path).stat().st_ino, (active.root / path).read_bytes()) for path in retained} == retained
    assert active.io.home_retirement_status()['state'] == 'migration pending'
    if not inert:
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        with pytest.raises(StreamClosed):
            stream.receive()
    assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('boundary', ['complete', 'denial-between-leaves'])
def test_stopped_auxiliary_cleanup_joins_real_cutoff_and_recovers_without_replay(active, monkeypatch, boundary):
    from scripts.test_home_retirement_inventory import auxiliary_journal_setup, OPERATION_LOCKS, writer_lock
    auxiliary_journal_setup(active.controller)
    device, _ = active.grant('device')
    active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        native_target_stop(active)
        stopped = owner.revoke_and_stop_services()
        effects, frames = active.effects(), list(active.frames)
        real_unlink, removed = os.unlink, []
        auxiliary = {str(active.root / path) for path in owner.original.auxiliary.members}
        leaves = set(owner.original.managed.members) - {
            'var/lib/happyranch-connector/trust-state.json',
            'var/lib/happyranch-connector/trust-state.json.anchor',
            'var/lib/happyranch-connector/trust-state.json.lock'}
        managed_paths = {str(active.root / path) for path in leaves}
        managed_removals = []
        def unlink(name, *, dir_fd=None):
            path = str(Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name)
            if path in auxiliary or path in managed_paths:
                assert all(writer_lock(active.root, lock) == 73 for lock in OPERATION_LOCKS)
            real_unlink(name, dir_fd=dir_fd)
            if path in managed_paths:
                managed_removals.append(path)
            if path in auxiliary:
                removed.append(path)
                if boundary == 'denial-between-leaves' and len(removed) == 1:
                    active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
        with monkeypatch.context() as scoped:
            scoped.setattr(os, 'unlink', unlink)
            if boundary == 'denial-between-leaves':
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    stopped.remove_auxiliary()
                assert len(removed) == 1
            else:
                stopped.remove_auxiliary()
                assert len(removed) == 5
                recovered = StoppedRetirement.from_saved(active.io, active.fence)
                recovered.remove_auxiliary()
                assert len(removed) == 5
                # Explicit recovery can finish private cleanup after auxiliary
                # removal, then repeat both phases without replaying effects.
                recovered.remove_private()
                again = StoppedRetirement.from_saved(active.io, active.fence)
                again.remove_private()
                again.remove_auxiliary()
                assert len(removed) == 5
                assert all(not (active.root / path).exists() for path in owner.original.private.members)
                # A new explicit operation retains the original records and
                # cutoff proof after the other two cleanup families are gone.
                managed = StoppedRetirement.from_saved(active.io, active.fence)
                managed.remove_managed()
                assert all(not (active.root / path).exists() for path in leaves)
                count = len(managed_removals)
                assert count > 0 and len(set(managed_removals)) == count
                final = StoppedRetirement.from_saved(active.io, active.fence)
                final.remove_managed()
                assert all(not (active.root / path).exists() for path in leaves)
                assert len(managed_removals) == count
                final.require()
        assert active.effects() == effects and active.frames == frames
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        assert all((active.root / path).exists() for path in OPERATION_LOCKS)
        assert all((active.root / path).is_file() for path in (PIN, INVENTORY, NATIVE, BINARY))
        assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('boundary', ['prepared', 'peer-file-sync', 'peer-parent-sync',
    'inventory-create', 'inventory-parent-sync', 'native-parent-sync'])
def test_saved_active_preparation_finishes_publication_before_real_revoke(active, monkeypatch, boundary):
    device, _ = active.grant('device')
    stream = active.stream(device, 'device')
    with active.serving(monkeypatch):
        if boundary == 'prepared':
            ActiveRetirement.capture(active.io, active.fence)
        else:
            target = PIN if boundary.startswith('peer-') else (
                INVENTORY if boundary.startswith('inventory-') else NATIVE)
            real_sync, real_open = os.fsync, os.open
            interrupted = []
            def sync(fd):
                current = Path(os.readlink(f'/proc/self/fd/{fd}'))
                if (current == active.root / (PIN if boundary == 'peer-file-sync' else STATE_DIRECTORY)
                        and (active.root / target).exists()):
                    interrupted.append(True)
                    raise OSError('synthetic interrupted preparation parent sync')
                real_sync(fd)
            def open_file(name, flags, *args, **kwargs):
                if name == INVENTORY.rsplit('/', 1)[1] and flags & os.O_CREAT:
                    interrupted.append(True)
                    raise OSError('synthetic interruption before first inventory publication')
                return real_open(name, flags, *args, **kwargs)
            with monkeypatch.context() as scoped:
                if boundary == 'inventory-create':
                    scoped.setattr(os, 'open', open_file)
                else:
                    scoped.setattr(os, 'fsync', sync)
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    ActiveRetirement.capture(active.io, active.fence)
            assert interrupted
        saved = {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                 for path in (PIN, INVENTORY, NATIVE) if (active.root / path).exists()}
        effects = active.effects()
        io = ManagedInstaller(root=active.root, root_owner=active.io.root_owner, layout='deb',
            run=active.io._run, lookup_user=active.io._lookup_user, lookup_group=active.io._lookup_group)
        detected = active.snapshot()
        assert HomeRetirement(io).detect().state == 'migration pending'
        assert active.snapshot() == detected and active.effects() == effects
        recovered = ActiveRetirement.from_saved(io, active.fence)
        assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                for path in saved} == saved
        assert (active.root / NATIVE).is_file()
        assert all(json.loads(frame).get('method', 'status') == 'status' for frame in active.frames if frame)
        assert not active.reopened()[0].devices[device].revoked and active.closed == []
        assert stream.receive() == b'synthetic-live-frame'
        receipt = recovered.revoke()
        receipt.require()
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        assert active.effects() == effects and not (active.root / FINAL).exists()


@pytest.mark.parametrize('drift', ['missing-peer', 'missing-inventory', 'torn-native',
    'private-key', 'managed-process', 'denial', 'preinventory-missing-peer', 'preinventory-torn-peer',
    'preinventory-orphan-native', 'preinventory-managed-process', 'preinventory-peer-mode',
    'preinventory-managed-config'])
def test_saved_active_preparation_refuses_uncertain_originals_without_recapture(active, monkeypatch, drift):
    from scripts.test_home_retirement_inventory import PRIVATE_KEY
    device, _ = active.grant('device')
    active.stream(device, 'device')
    with active.serving(monkeypatch):
        preinventory = drift.startswith('preinventory-')
        if preinventory:
            real_open = os.open
            def open_file(name, flags, *args, **kwargs):
                if name == INVENTORY.rsplit('/', 1)[1] and flags & os.O_CREAT:
                    raise OSError('synthetic interruption before first inventory publication')
                return real_open(name, flags, *args, **kwargs)
            with monkeypatch.context() as scoped:
                scoped.setattr(os, 'open', open_file)
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    ActiveRetirement.capture(active.io, active.fence)
            assert (active.root / PIN).exists()
            assert not (active.root / INVENTORY).exists() and not (active.root / NATIVE).exists()
            drift = drift.removeprefix('preinventory-')
        else:
            ActiveRetirement.capture(active.io, active.fence)
        if drift == 'missing-peer':
            (active.root / PIN).unlink()
        elif drift == 'missing-inventory':
            (active.root / INVENTORY).unlink()
        elif drift == 'torn-native':
            (active.root / NATIVE).write_bytes(b'{synthetic-interrupted')
        elif drift == 'torn-peer':
            (active.root / PIN).write_bytes(b'{synthetic-interrupted')
        elif drift == 'orphan-native':
            (active.root / NATIVE).write_bytes(b'{}')
            (active.root / NATIVE).chmod(0o600)
        elif drift == 'peer-mode':
            (active.root / PIN).chmod(0o644)
        elif drift == 'managed-config':
            (active.root / 'etc/happyranch/connector.json').write_bytes(b'{}')
        elif drift == 'private-key':
            (active.root / PRIVATE_KEY).write_bytes(b'synthetic-changed-private-key')
        elif drift == 'managed-process':
            path = active.root / f'proc/{active.pid}/stat'
            path.write_bytes(path.read_bytes().replace(b'456', b'999'))
        else:
            active.manager.revoke_all(expected_generation=active.reopened()[1])
        before, effects = active.snapshot(), active.effects()
        frames = list(active.frames)
        io = ManagedInstaller(root=active.root, root_owner=active.io.root_owner, layout='deb',
            run=active.io._run, lookup_user=active.io._lookup_user, lookup_group=active.io._lookup_group)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            ActiveRetirement.from_saved(io, active.fence)
        assert active.snapshot() == before and active.effects() == effects
        assert all(json.loads(frame).get('method', 'status') == 'status' for frame in active.frames[len(frames):] if frame)
        assert not (active.root / FINAL).exists()
        assert active.reopened()[0].devices[device].revoked is (drift == 'denial')


@pytest.mark.parametrize('phase', ['ceremony', 'first-device'])
def test_partial_revoke_recovery_preserves_commits_and_finishes_through_original_owner(active, monkeypatch, phase):
    from runtime.remote_access import pairing
    # Persisted JSON orders device IDs, independently of grant creation order.
    # Keep the interruption's first/second labels deterministic without changing
    # real grant, revoke, stream or recovery behavior.
    token_hex, identifiers = pairing.secrets.token_hex, iter(('1' * 32, '2' * 32))
    with monkeypatch.context() as scoped:
        scoped.setattr(pairing, 'secrets', SimpleNamespace(token_hex=
            lambda size: next(identifiers) if size == 16 else token_hex(size)))
        grants = [active.grant(node) for node in ('first', 'second')]
    streams = [active.stream(device, node) for (device, _), node in zip(grants, ('first', 'second'))]
    active.manager.issue_invite_record('f' * 64, datetime.now(timezone.utc) + timedelta(minutes=2))
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        revoke, calls = active.manager.revoke_device, []
        def interrupt(device, **kwargs):
            calls.append(device)
            if len(calls) == (1 if phase == 'ceremony' else 2):
                raise PairingError('authorization_expired')
            return revoke(device, **kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(active.manager, 'revoke_device', interrupt)
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                owner.revoke()
        state, _ = active.reopened()
        assert state.invite_record is state.pending_approval is state.delivery is None
        assert sum(device.revoked for device in state.devices.values()) == (phase == 'first-device')
        assert active.closed == ([] if phase == 'ceremony' else ['first'])
        assert streams[0].closed is (phase == 'first-device') and not streams[1].closed
        assert active.effects() == []
        records = {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                   for path in (PIN, INVENTORY, NATIVE)}
        native_target_stop(active)
        stopped = ActiveRetirement.resume_revoke(active.io, active.fence)
        stopped.require()
        assert all(device.revoked for device in active.reopened()[0].devices.values())
        assert active.closed == ['first', 'second'] and all(stream.closed for stream in streams)
        assert active.effects() and all(props['ActiveState'] == 'inactive' for props in active.native.values())
        assert stopped._owner.original == owner.original and stopped._owner.native == owner.native
        assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
                for path in records} == records
        assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('drift', ['private-key', 'managed-process', 'trust-lock', 'corrupt-trust', 'failed-close'])
def test_partial_revoke_recovery_keeps_uncertainty_and_failed_physical_close_pending(active, monkeypatch, drift):
    device, _ = active.grant('device')
    def fail_close():
        raise OSError('synthetic failed original physical cleanup')
    stream = active.stream(device, 'device', close=fail_close if drift == 'failed-close' else None)
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        if drift == 'failed-close':
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                owner.revoke()
        else:
            def interrupt(*args, **kwargs):
                raise PairingError('authorization_expired')
            with monkeypatch.context() as scoped:
                scoped.setattr(active.manager, 'revoke_device', interrupt)
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    owner.revoke()
            if drift == 'private-key':
                (active.root / (STATE_DIRECTORY + '/home-ca/ca.key')).write_bytes(b'synthetic-changed-key')
            elif drift == 'managed-process':
                path = active.root / f'proc/{active.pid}/stat'
                path.write_bytes(path.read_bytes().replace(b'456', b'999'))
            elif drift == 'trust-lock':
                path = active.path.with_name(active.path.name + '.lock')
                path.rename(active.root / 'kept-original-trust-lock')
                path.write_bytes(b'')
                path.chmod(0o600)
            else:
                active.path.write_bytes(b'{synthetic-corrupt-trust')
        before, effects, closed = active.snapshot(), active.effects(), list(active.closed)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            ActiveRetirement.resume_revoke(active.io, active.fence)
        assert active.effects() == effects == [] and active.closed == closed
        if drift == 'failed-close':
            assert stream.cleanup_failed and stream.closed
            assert active.reopened()[0].devices[device].revoked
        else:
            assert active.snapshot() == before
        assert not (active.root / FINAL).exists()
