"""Dedicated account effects and saved-original partial recovery.

Run: uv run --frozen pytest scripts/test_home_retirement_account.py -v --tb=short
The lower table isolates full cleanup while using the real controller observer.
The composed table retains real revoke, native, cleanup and account-entry proof.
Only NSS/native OS effects are synthetic; no final receipt is seeded.
"""
from __future__ import annotations

import os

import pytest

from runtime.remote_access.home_retirement import ACCOUNT, RetirementUncertain
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
@pytest.mark.parametrize('boundary', ['group-failure', 'native-after-userdel'])
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
    for method in ('remove_unit_fragments', 'remove_controller_executable', 'remove_managed_directories',
                   'remove_operation_domains', 'remove_connector_directory' if inert else 'remove_trust'):
        getattr(cleanup, method)(io(), active.fence)
    state = account_adapter(active.io, active.calls, fault=boundary)
    if boundary == 'native-after-userdel':
        state['after_user'] = lambda: active.native['happyranch-managed.target'].update(
            ActiveState='active', SubState='active')
    records = {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
        for path in (INVENTORY, NATIVE, ACCOUNT_RECORD, 'retained-native/saved-home')}
    before, effects, frames = active.snapshot(), active.effects(), list(getattr(active, 'frames', []))
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
    assert cleanup.retire_account(io(), active.fence) == 'absent'
    assert cleanup.retire_account(io(), active.fence) == 'absent'
    assert deletions(active.calls) == (['userdel', 'groupdel', 'groupdel']
        if boundary == 'group-failure' else ['userdel', 'groupdel'])
    assert active.snapshot() == before and active.effects() == effects
    assert list(getattr(active, 'frames', [])) == frames
    assert {path: ((active.root / path).read_bytes(), (active.root / path).stat().st_ino)
        for path in records} == records
    assert not state['user'] and not state['group'] and not (active.root / FINAL).exists()
    assert active.io.home_retirement_status()['state'] == 'migration pending'
    if not inert:
        assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
        with pytest.raises(StreamClosed):
            stream.receive()
