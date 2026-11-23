"""Active preparation/cutoff through real inventory, journals and private IPC.

Run: uv run --frozen pytest scripts/test_home_retirement_coordinator.py -v --tb=short
Only OS ownership/NSS/proc/systemd and the installed package are synthetic.
No fixture computes an inventory, acknowledgement or retirement receipt.
"""
from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from runtime.remote_access import home_retirement_cutoff as cutoff
from runtime.remote_access.home_retirement import RetirementUncertain, STATE_DIRECTORY
from runtime.remote_access.home_retirement_coordinator import ActiveRetirement, StoppedRetirement
from runtime.remote_access.home_retirement_inventory import LegacyInventory
from runtime.remote_access.home_retirement_journal import RetirementJournal
from runtime.remote_access.home_retirement_transport import ManagedTransport
from runtime.remote_access.home_retirement_transport_journal import ManagedTransportJournal
from runtime.remote_access.streams import StreamClosed
from scripts.test_home_retirement_cutoff import RecoveryFixture, PIN
from scripts.test_home_retirement_inventory import InventoryFixture, managed_setup, ACCOUNT_RECORD, PUBLICATION
from scripts.test_home_retirement_service import MANAGED_UNITS, LINK, BINARY

INVENTORY = STATE_DIRECTORY + '/home-retirement-inventory.json'
NATIVE = STATE_DIRECTORY + '/home-retirement-transport.json'
FINAL = STATE_DIRECTORY + '/home-retirement.json'


@pytest.fixture
def active(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    native = RecoveryFixture(tmp_path / 'root')
    legacy = InventoryFixture(tmp_path / 'legacy-input', monkeypatch)
    managed_setup(legacy)
    # Overlay independent historical inputs, retaining the real trust pair,
    # socket owner and native package fixture already present in the root.
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


@pytest.mark.parametrize('failure', ['stop', 'disable'])
def test_active_controller_native_failure_retains_denial_and_recovery_evidence(active, monkeypatch, failure):
    device, _ = active.grant('device')
    active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        active.controller.fail = failure
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            owner.revoke_and_stop_controller()
        assert active.effects() == [['systemctl', 'stop', 'happyranch-headscale.service']] + (
            [['systemctl', 'disable', 'happyranch-headscale.service']] if failure == 'disable' else [])
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        assert all((active.root / path).is_file() for path in (PIN, INVENTORY, NATIVE, BINARY))
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
