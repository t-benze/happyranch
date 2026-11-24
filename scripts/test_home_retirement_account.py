"""Dedicated account effects and saved-original partial recovery.

Run: uv run --frozen pytest scripts/test_home_retirement_account.py -v --tb=short
The lower table isolates full cleanup while using the real controller observer.
The composed table retains real revoke, native, cleanup and account-entry proof.
Only NSS/native OS effects are synthetic; no final receipt is seeded.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from runtime.remote_access.home_retirement import ACCOUNT, RetirementUncertain, STATE_DIRECTORY as STATE
from runtime.remote_access.home_retirement_account import AccountDisposition
from runtime.remote_access.home_retirement_fence import retirement_fence
from runtime.remote_access.home_retirement_journal import RetirementJournal
from runtime.remote_access.home_retirement_service import OwnedService
from scripts.test_home_retirement_inventory import InventoryFixture, managed_setup, ACCOUNT_RECORD
from scripts.test_home_retirement_service import LINK
from scripts.test_home_retirement_coordinator import active, native_target_stop, INVENTORY, NATIVE, FINAL


def account_adapter(io, calls, *, fault=''):
    """NSS and shadow-utils observations only; no retirement decisions."""
    original_run, original_user, original_group = io._run, io._lookup_user, io._lookup_group
    user, group = original_user(ACCOUNT), original_group(ACCOUNT)
    state = dict(user=True, group=True, fault=fault, after_user=lambda: None)
    def lookup_user(name):
        if name == ACCOUNT and not state['user']:
            raise KeyError(name)
        return original_user(name)
    def lookup_group(name):
        if name == ACCOUNT and not state['group']:
            raise KeyError(name)
        result = original_group(name)
        if name == ACCOUNT and state['fault'] == 'group-member':
            from types import SimpleNamespace
            return SimpleNamespace(gr_name=name, gr_gid=group.gr_gid, gr_mem=['foreign'])
        return result
    def run(argv, **kwargs):
        if argv[0] in ('userdel', 'groupdel'):
            calls.append(argv)
            key = 'user' if argv[0] == 'userdel' else 'group'
            if state['fault'] != key + '-failure':
                state[key] = False
                if key == 'user':
                    state['after_user']()
            return (1 if state['fault'] in (key + '-failure', key + '-uncertain') else 0), 'synthetic-private-command-output'
        if argv[0] == 'getent':
            calls.append(argv)
            database = argv[1]
            present = state['user' if database == 'passwd' else 'group']
            value = (f'{ACCOUNT}:x:{user.pw_uid}:{user.pw_gid}::/nonexistent:/usr/sbin/nologin\n'
                if database == 'passwd' else f'{ACCOUNT}:x:{group.gr_gid}:\n') if present else ''
            if database == 'passwd' and state['fault'] == 'primary-shared':
                value += f'foreign:x:{user.pw_uid + 10}:{group.gr_gid}::/nonexistent:/usr/sbin/nologin\n'
            if database == 'passwd' and state['fault'] == 'uid-reused':
                value += f'foreign:x:{user.pw_uid}:{group.gr_gid + 10}::/nonexistent:/usr/sbin/nologin\n'
            if len(argv) == 3 and database == 'passwd' and state['fault'] == 'numeric-only-reuse':
                return 0, f'foreign:x:{user.pw_uid}:{group.gr_gid}::/nonexistent:/usr/sbin/nologin\n'
            return (0 if present or len(argv) == 2 else 2), value
        return original_run(argv, **kwargs)
    io._run, io._lookup_user, io._lookup_group = run, lookup_user, lookup_group
    io.manager._run = run
    return state


def deletions(calls):
    return [argv[0] for argv in calls if argv[0] in ('userdel', 'groupdel')]


@pytest.mark.parametrize('case', ['exclusive', 'retained-consumer', 'user-failure', 'group-failure',
    'user-uncertain', 'group-uncertain', 'uid-reused', 'numeric-only-reuse', 'group-member',
    'primary-shared', 'process', 'receipt-replaced', 'guard-failure', 'consumer-race'])
def test_dedicated_account_disposition_rechecks_original_identity_and_recovers_without_replay(tmp_path, monkeypatch, case):
    fixture = InventoryFixture(tmp_path / 'root', monkeypatch)
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        fixture.stopped()
        (fixture.root / LINK).unlink()
        fixture.properties['UnitFileState'] = 'disabled'
        state = account_adapter(fixture.io, fixture.calls, fault=case)
        if case == 'numeric-only-reuse':
            state['user'] = False
        if case == 'retained-consumer':
            fixture.file('etc/systemd/system/operator.service', b'[Service]\nUser=happyranch-headscale\n', 0o644)
        if case == 'receipt-replaced':
            path = fixture.root / ACCOUNT_RECORD
            path.rename(fixture.root / 'original-receipt-kept')
            fixture.put_json(ACCOUNT_RECORD, fixture.record)
        if case == 'process':
            fixture.ps_output = f'999 {os.getuid()} {os.getuid()} {os.getuid()} {os.getuid()}\n'
        guarded = []
        def proof():
            guarded.append(True)
            if case == 'guard-failure':
                raise RetirementUncertain()
            if case == 'consumer-race' and len(guarded) == 2:
                fixture.file('etc/systemd/system/operator.service', b'[Service]\nUser=happyranch-headscale\n', 0o644)
            OwnedService(fixture.io, original.ownership.service).observe_cleanup(
                original.ownership.native, account_cleanup=True)
        def fresh():
            return AccountDisposition(RetirementJournal(fixture.io, fence), original, before_effect=proof).retire()
        before = fixture.snapshot()
        retained = {path: ((fixture.root / path).read_bytes(), (fixture.root / path).stat().st_ino)
            for path in (ACCOUNT_RECORD, 'var/lib/happyranch-remote-access/home-retirement-inventory.json',
                         'retained-native/saved-home')}
        if case == 'exclusive':
            assert fresh() == 'absent'
        elif case == 'retained-consumer':
            assert fresh() == 'retained-disabled: static consumer'
            assert state['user'] and state['group'] and deletions(fixture.calls) == []
            with pytest.raises(RetirementUncertain):
                OwnedService(fixture.io, original.ownership.service).observe_cleanup(original.ownership.native)
        else:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
        if case in ('exclusive', 'user-failure', 'group-failure', 'user-uncertain', 'group-uncertain'):
            first = deletions(fixture.calls)
            state['fault'] = ''
            assert fresh() == fresh() == 'absent'
            assert not state['user'] and not state['group']
            expected = ['userdel', 'groupdel']
            if case == 'user-failure':
                expected.insert(0, 'userdel')
            elif case == 'group-failure':
                expected.append('groupdel')
            assert deletions(fixture.calls) == expected, first
            with pytest.raises(RetirementUncertain):
                OwnedService(fixture.io, original.ownership.service).observe_cleanup(original.ownership.native)
        elif case != 'retained-consumer':
            assert deletions(fixture.calls) == []
            assert fixture.snapshot() == before or case == 'consumer-race'
        assert {path: ((fixture.root / path).read_bytes(), (fixture.root / path).stat().st_ino)
            for path in retained} == retained
        assert guarded and fixture.effects() == []
        assert not (fixture.root / 'var/lib/happyranch-remote-access/home-retirement.json').exists()


@pytest.mark.parametrize('active', ['active', 'inert'], indirect=True)
@pytest.mark.parametrize('boundary', ['group-failure', 'native-after-userdel', 'retained-consumer'])
def test_account_disposition_composes_real_cleanup_and_rechecks_native_after_user_effect(active, monkeypatch, request, boundary):
    from runtime.remote_access.home_retirement_coordinator import ActiveRetirement, StoppedRetirement
    from runtime.remote_access.home_retirement_inert import InertRetirement
    from runtime.remote_access.installer import ManagedInstaller
    from runtime.remote_access.streams import StreamClosed
    from scripts.test_home_retirement_inventory import auxiliary_journal_setup, TRANSACTION
    inert = request.node.callspec.params['active'] == 'inert'
    auxiliary_journal_setup(active.controller)
    if inert:
        (active.root / TRANSACTION).unlink()
        owner = InertRetirement.capture(active.io, active.fence)
        owner.stop_and_disable()
    else:
        device, _ = active.grant('device')
        stream = active.stream(device, 'device')
        with active.serving(monkeypatch):
            owner = ActiveRetirement.capture(active.io, active.fence)
            native_target_stop(active)
            owner.revoke_and_stop_services()
        # Read committed denial while the real trust pair still exists.
        # Reopening its store after intentional removal would create a new lock.
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        with pytest.raises(StreamClosed):
            stream.receive()
        denied_members = {}
        for suffix in ('', '.anchor', '.lock'):
            path = 'var/lib/happyranch-connector/trust-state.json' + suffix
            info = (active.root / path).stat()
            denied_members[path] = [info.st_dev, info.st_ino]
    def io():
        return ManagedInstaller(root=active.root, root_owner=active.io.root_owner, layout='deb',
            run=active.io._run, lookup_user=active.io._lookup_user, lookup_group=active.io._lookup_group)
    cleanup = InertRetirement if inert else StoppedRetirement
    # Each explicit entry loads the same saved originals; no prestate recapture
    # or deadline reset within an in-flight effect supplies recovery authority.
    if inert:
        InertRetirement.resume_cleanup(io(), active.fence).remove_leaves()
        InertRetirement.resume_cleanup(io(), active.fence).remove_private_directories()
    else:
        for method in ('remove_auxiliary', 'remove_private', 'remove_managed', 'remove_private_directories'):
            getattr(StoppedRetirement.from_saved(io(), active.fence), method)()
    # Inert connector removal holds the domains; active domain removal reads
    # the denied trust pair. Preserve both existing dependency orders.
    last = (('remove_connector_directory', 'remove_operation_domains') if inert
        else ('remove_operation_domains', 'remove_trust'))
    for method in ('remove_unit_fragments', 'remove_controller_executable', 'remove_managed_directories', *last):
        getattr(cleanup, method)(io(), active.fence)
    state = account_adapter(active.io, active.calls, fault=boundary)
    retained = boundary == 'retained-consumer'
    if retained:
        active.controller.file('etc/systemd/system/operator.service',
            b'[Service]\nUser=happyranch-headscale\n', 0o644)
    if boundary == 'native-after-userdel':
        state['after_user'] = lambda: active.native['happyranch-managed.target'].update(
            ActiveState='active', SubState='active')
    records = {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
        for path in (INVENTORY, NATIVE, ACCOUNT_RECORD, 'retained-native/saved-home',
            *(() if inert else ('var/lib/happyranch-remote-access/home-retirement-denied.json',)))}
    before, effects, frames = active.snapshot(), active.effects(), list(getattr(active, 'frames', []))
    disposition = 'retained-disabled: static consumer' if retained else 'absent'
    expected_deletions = [] if retained else (['userdel', 'groupdel', 'groupdel']
        if boundary == 'group-failure' else ['userdel', 'groupdel'])
    if retained:
        assert cleanup.retire_account(io(), active.fence) == disposition
        assert state['user'] and state['group'] and not deletions(active.calls)
    else:
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            cleanup.retire_account(io(), active.fence)
        assert not state['user'] and state['group']
        assert deletions(active.calls) == (['userdel', 'groupdel'] if boundary == 'group-failure' else ['userdel'])
    count = len(deletions(active.calls))
    if boundary == 'native-after-userdel':
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            cleanup.retire_account(io(), active.fence)
        assert len(deletions(active.calls)) == count
        active.native['happyranch-managed.target'].update(ActiveState='inactive', SubState='dead')
    state['fault'] = ''
    assert cleanup.retire_account(io(), active.fence) == disposition
    assert cleanup.retire_account(io(), active.fence) == disposition
    assert deletions(active.calls) == expected_deletions
    assert active.snapshot() == before and active.effects() == effects
    assert list(getattr(active, 'frames', [])) == frames
    assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
        for path in records} == records
    assert (state['user'], state['group']) == (retained, retained) and not (active.root / FINAL).exists()
    assert active.io.home_retirement_status()['state'] == 'migration pending'
    if not inert:
        bridge = json.loads((active.root / 'var/lib/happyranch-remote-access/home-retirement-denied.json').read_bytes())
        assert {path: signature[:2] for path, signature in bridge['members'].items()} == denied_members
        assert all(not (active.root / path).exists() for path in denied_members)
        assert active.closed == ['device']
        with pytest.raises(StreamClosed):
            stream.receive()
    # Final receipt cleanup is a separate explicit production entry. Account
    # effects and their evidence assertions above must already have completed.
    originals = [STATE + '/' + name for name in owner.original.ownership.receipt_identities]
    real_sync = os.fsync
    interrupted = []
    def lost_receipt_sync(fd):
        if (not interrupted and not (active.root / ACCOUNT_RECORD).exists()
                and Path(os.readlink(f'/proc/self/fd/{fd}')) == active.root / STATE):
            interrupted.append(True)
            raise OSError('synthetic original receipt parent sync interruption')
        real_sync(fd)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', lost_receipt_sync)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            cleanup.remove_original_receipts(io(), active.fence)
    assert interrupted and not (active.root / ACCOUNT_RECORD).exists()
    assert active.io.home_retirement_status()['state'] == 'migration pending'
    cleanup.remove_original_receipts(io(), active.fence)
    assert all(not (active.root / path).exists() for path in originals)
    after = active.snapshot()
    cleanup.remove_original_receipts(io(), active.fence)
    assert active.snapshot() == after and active.effects() == effects
    assert deletions(active.calls) == expected_deletions
    assert list(getattr(active, 'frames', [])) == frames
    assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
        for path in records if path not in originals} == {
        path: value for path, value in records.items() if path not in originals}
    assert not (active.root / FINAL).exists()
    assert active.io.home_retirement_status()['state'] == 'migration pending'
    # The production final entry decides when complete facts permit reload and
    # publication. This adapter implements only the systemd cache observation.
    previous_run = active.io._run
    reloads = []
    def native_reload(argv, **kwargs):
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
        return previous_run(argv, **kwargs)
    active.io._run = active.io.manager._run = native_reload
    real_write = os.write
    partial_writes = []
    def interrupted_publication_write(fd, data):
        if Path(os.readlink(f'/proc/self/fd/{fd}')) == active.root / STATE / '.home-retirement.json.pending':
            partial_writes.append(data[:11])
            return real_write(fd, data[:11])
        return real_write(fd, data)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'write', interrupted_publication_write)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            cleanup.publish_final(io(), active.fence)
    staged = active.root / STATE / '.home-retirement.json.pending'
    staged_inode = staged.stat().st_ino
    assert partial_writes == [b'{"account":'] and staged.read_bytes() == partial_writes[0]
    assert not (active.root / FINAL).exists()
    assert io().home_retirement_status()['state'] == 'migration pending'
    real_sync = os.fsync
    publication_interrupted = []
    def lost_publication_sync(fd):
        path = Path(os.readlink(f'/proc/self/fd/{fd}'))
        selected = (path == active.root / STATE / '.home-retirement.json.pending'
            if boundary == 'group-failure' else path == active.root / STATE and (active.root / FINAL).exists())
        if not publication_interrupted and selected:
            publication_interrupted.append(True)
            raise OSError('synthetic final publication sync interruption')
        real_sync(fd)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', lost_publication_sync)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            cleanup.publish_final(io(), active.fence)
    assert publication_interrupted and reloads == [True]
    produced = cleanup.publish_final(io(), active.fence)
    assert produced == (b'{"account":"retained-disabled: static consumer","format":"home-retirement@1","state":"retired"}\n'
        if retained else b'{"account":"absent","format":"home-retirement@1","state":"retired"}\n')
    assert (active.root / FINAL).read_bytes() == produced
    assert (active.root / FINAL).stat().st_ino == staged_inode
    complete = active.snapshot()
    identity = (active.root / FINAL).stat().st_ino
    # R03 consumes the record generated after the actual earlier interruption.
    status = io().home_retirement_status()
    assert status['state'] == 'retired'
    if retained:
        assert status['detail'] == 'retirement confirmed; account retained disabled: static consumer'
    assert io().home_retirement_status()['state'] == 'retired'
    assert cleanup.publish_final(io(), active.fence) == produced
    assert (active.root / FINAL).stat().st_ino == identity
    assert active.snapshot() == complete and active.effects() == effects
    assert list(getattr(active, 'frames', [])) == frames and reloads == [True]
    assert deletions(active.calls) == expected_deletions
    assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
        for path in records if path not in originals} == {
        path: value for path, value in records.items() if path not in originals}
    # R04's reappearance follows real publication, not a seeded final receipt.
    active.controller.file('etc/happyranch/invite-issuer.json', b'synthetic-foreign-reappearance')
    drift = active.snapshot()
    assert io().home_retirement_status()['state'] == 'migration pending'
    with pytest.raises(RetirementUncertain):
        cleanup.publish_final(io(), active.fence)
    assert active.snapshot() == drift and (active.root / FINAL).read_bytes() == produced


@pytest.mark.parametrize('case', ['complete', 'no-consumer', 'unlocked', 'process',
    'uid-reused', 'numeric-only-reuse', 'account-missing', 'group-member',
    'unit-consumer', 'alias', 'journal-mode', 'journal-corrupt', 'directory-mode'])
def test_retained_account_readback_requires_original_provenance_and_current_disabled_facts(tmp_path, monkeypatch, case):
    from runtime.remote_access.home_retirement_account import require_retained
    from runtime.remote_access.home_retirement_journal import observe_original
    fixture = InventoryFixture(tmp_path / 'root', monkeypatch)
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
    fixture.stopped()
    (fixture.root / LINK).unlink()
    fixture.properties['UnitFileState'] = 'disabled'
    state = account_adapter(fixture.io, fixture.calls, fault=case)
    consumer = 'etc/systemd/system/operator.service'
    if case != 'no-consumer':
        fixture.file(consumer, b'[Service]\nUser=happyranch-headscale\n', 0o644)
    if case == 'unlocked':
        fixture.lock_status = 'P'
    elif case == 'process':
        fixture.ps_output = f'999 {os.getuid()} {os.getuid()} {os.getuid()} {os.getuid()}\n'
    elif case in ('account-missing', 'numeric-only-reuse'):
        state['user'] = False
    elif case == 'unit-consumer':
        fixture.file(consumer, b'[Unit]\nRequires=happyranch-headscale.service\n', 0o644)
    elif case == 'alias':
        (fixture.root / 'etc/systemd/system/foreign.service').symlink_to('/etc/systemd/system/happyranch-headscale.service')
    elif case == 'journal-mode':
        (fixture.root / INVENTORY).chmod(0o644)
    elif case == 'journal-corrupt':
        (fixture.root / INVENTORY).write_bytes(b'{"synthetic":"unknown original"}')
    elif case == 'directory-mode':
        (fixture.root / STATE).chmod(0o755)
    before = fixture.snapshot()
    def no_sync(fd):
        pytest.fail('read-only retained-account observation must not fsync')
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', no_sync)
        def observe():
            saved = observe_original(fixture.io)
            assert saved == original
            return require_retained(fixture.io, saved.ownership.service)
        if case == 'complete':
            assert observe() == observe() == frozenset({consumer})
        else:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                observe()
    assert fixture.snapshot() == before and fixture.effects() == []
    assert not deletions(fixture.calls) and not (fixture.root / FINAL).exists()


@pytest.mark.parametrize('case', ['absent', 'retained-consumer', 'account-present', 'group-present',
    'replaced-receipt', 'leaf-sync', 'reappearance', 'account-reappears', 'guard-refusal'])
def test_original_receipt_cleanup_requires_final_account_and_recovers_without_replay(tmp_path, monkeypatch, case):
    from runtime.remote_access.home_retirement_cleanup import OriginalReceiptCleanup
    fixture = InventoryFixture(tmp_path / 'root', monkeypatch)
    managed_setup(fixture)
    with retirement_fence(fixture.io) as fence:
        journal = RetirementJournal(fixture.io, fence)
        original = journal.capture(fixture.inventory.observe(fence=fence))
        fixture.stopped()
        (fixture.root / LINK).unlink()
        fixture.properties['UnitFileState'] = 'disabled'
        state = account_adapter(fixture.io, fixture.calls)
        guarded = []
        def proof():
            guarded.append(True)
            if case == 'guard-refusal':
                raise RetirementUncertain()
            # This lower keeper isolates full artifact/cutoff proof. The real
            # composed keeper above supplies it without a synthetic success.
            OwnedService(fixture.io, original.ownership.service).observe_cleanup(
                original.ownership.native, account_cleanup=True)
        if case == 'retained-consumer':
            fixture.file('etc/systemd/system/operator.service', b'[Service]\nUser=happyranch-headscale\n', 0o644)
        elif case == 'group-present':
            state['user'] = False
        elif case not in ('account-present', 'guard-refusal'):
            assert AccountDisposition(journal, original, before_effect=proof).retire() == 'absent'
        paths = {STATE + '/' + name for name in original.ownership.receipt_identities}
        if case == 'replaced-receipt':
            path = fixture.root / (STATE + '/headscale-publication.json')
            data = path.read_bytes()
            path.rename(fixture.root / 'saved-publication')
            fixture.file(str(path.relative_to(fixture.root)), data)
        preserved = {path: fixture.snapshot()[path] for path in (INVENTORY, 'retained-native/saved-home',
            STATE + '/.home-activation.json.pending')}
        before, prior_deletions = fixture.snapshot(), deletions(fixture.calls)
        removed, synced = [], []
        real_unlink, real_sync = os.unlink, os.fsync
        interrupted = False
        def unlink(name, *args, **kwargs):
            real_unlink(name, *args, **kwargs)
            if STATE + '/' + name in paths:
                removed.append(name)
                if case == 'reappearance' and len(removed) == 1:
                    fixture.file(STATE + '/' + name, b'reappeared-foreign-evidence')
                if case == 'account-reappears' and len(removed) == 1:
                    state['user'] = state['group'] = True
        def sync(fd):
            nonlocal interrupted
            if Path(os.readlink(f'/proc/self/fd/{fd}')) == fixture.root / STATE:
                synced.append(True)
                if not interrupted and case == 'leaf-sync' and removed:
                    interrupted = True
                    raise OSError('synthetic interrupted parent sync')
            real_sync(fd)
        def fresh():
            current = RetirementJournal(fixture.io, fence)
            disposition = AccountDisposition(current, original, before_effect=proof)
            OriginalReceiptCleanup(current, original, before_effect=disposition.require_retired).remove()
        with monkeypatch.context() as patch:
            patch.setattr(os, 'unlink', unlink)
            patch.setattr(os, 'fsync', sync)
            if case in ('absent', 'retained-consumer'):
                fresh()
            else:
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    fresh()
        if case in ('absent', 'retained-consumer', 'leaf-sync'):
            first = list(removed)
            with monkeypatch.context() as patch:
                patch.setattr(os, 'unlink', unlink)
                fresh()
                count = len(removed)
                fresh()
            assert all(not (fixture.root / path).exists() for path in paths)
            assert len(removed) == count
            assert len(removed) == len(paths)
            assert first == removed[:len(first)] and synced
        elif case == 'reappearance':
            assert len(removed) == 1
            assert (fixture.root / STATE / removed[0]).read_bytes() == b'reappeared-foreign-evidence'
            with pytest.raises(RetirementUncertain):
                fresh()
        elif case == 'account-reappears':
            assert len(removed) == 1 and state['user'] and state['group']
            assert sum((fixture.root / path).exists() for path in paths) == len(paths) - 1
            with pytest.raises(RetirementUncertain):
                fresh()
            # Strict deletion entry cannot adopt missing final-stage receipts.
            with pytest.raises(RetirementUncertain):
                AccountDisposition(journal, original, before_effect=proof).retire()
        else:
            assert not removed and fixture.snapshot() == before
        assert deletions(fixture.calls) == prior_deletions
        assert {path: fixture.snapshot()[path] for path in preserved} == preserved
        assert guarded and fixture.effects() == [] and not (fixture.root / FINAL).exists()
