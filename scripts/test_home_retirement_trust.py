"""Denied-trust persistence/removal and original native-proof composition.

Run: uv run --frozen pytest scripts/test_home_retirement_trust.py -v --tb=short
The lower table isolates native observation, with real preparation/revocation,
pair parsing, record publication and filesystem removal. The composed table
retains every production native/death check and actual stop/disable path.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from runtime.remote_access.home_retirement import RetirementUncertain, STATE_DIRECTORY
from runtime.remote_access.home_retirement_cleanup import ManagedCleanup
from runtime.remote_access.home_retirement_coordinator import ActiveRetirement, StoppedRetirement
from runtime.remote_access.home_retirement_trust import TrustCleanup
from runtime.remote_access.installer import ManagedInstaller
from runtime.remote_access.streams import StreamClosed
from scripts.test_home_retirement_coordinator import active, native_target_stop, INVENTORY, NATIVE, FINAL
from scripts.test_home_retirement_cutoff import PIN, DIGEST
from scripts.test_home_retirement_inventory import writer_lock

ROOT = 'var/lib/happyranch-connector'
RECORD = STATE_DIRECTORY + '/home-retirement-denied.json'
TRIPLE = [ROOT + '/trust-state.json' + suffix for suffix in ('', '.anchor', '.lock')]


@pytest.mark.parametrize('case', ['complete', 'record-sync', 'leaf-sync', 'root-sync',
    'live-trust', 'lock-busy', 'replacement', 'missing-record', 'record-mode', 'corrupt-anchor'])
def test_denied_trust_cleanup_persists_only_real_denial_and_recovers_original_removal(active, monkeypatch, case):
    from datetime import datetime, timedelta, timezone
    device, _ = active.grant('device')
    stream = active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        owner.revoke().require()
    assert active.reopened()[0].devices[device].revoked and active.closed == ['device']
    with pytest.raises(StreamClosed):
        stream.receive()
    # Storage-only layer: native admission is owned by the composed table.
    monkeypatch.setattr(TrustCleanup, '_native', lambda self: None)
    ManagedCleanup(owner.journal, owner.original, before_effect=lambda: None).remove()
    if case == 'live-trust':
        active.manager.issue_invite_record(DIGEST, datetime.now(timezone.utc) + timedelta(minutes=2))
    elif case == 'corrupt-anchor':
        (active.root / TRIPLE[1]).write_bytes(b'synthetic-malformed-private-anchor')
    retained = {path: ((active.root / path).stat().st_ino, (active.root / path).read_bytes())
        for path in (INVENTORY, NATIVE, PIN, 'retained-native/saved-home')}
    before, effects = active.snapshot(), active.effects()
    real_unlink, real_rmdir, real_sync = os.unlink, os.rmdir, os.fsync
    removed, interruptions, synced = [], [], []
    def unlink(name, *, dir_fd=None):
        path = str((Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name).relative_to(active.root))
        if path in TRIPLE:
            assert (active.root / RECORD).is_file(), 'no denial evidence before unlink'
            record_info = (active.root / RECORD).stat()
            parent_info = (active.root / RECORD).parent.stat()
            assert {(record_info.st_dev, record_info.st_ino),
                    (parent_info.st_dev, parent_info.st_ino)} <= set(synced)
            assert writer_lock(active.root, TRIPLE[-1]) == 73
        real_unlink(name, dir_fd=dir_fd)
        removed.append(path)
        if case == 'replacement' and len(removed) == 1:
            (active.root / TRIPLE[1]).write_bytes(b'synthetic-replaced-denied-anchor')
    def rmdir(name, *, dir_fd=None):
        real_rmdir(name, dir_fd=dir_fd)
        removed.append(ROOT)
    def sync(fd):
        path = Path(os.readlink(f'/proc/self/fd/{fd}'))
        fail = (case == 'record-sync' and path == active.root / STATE_DIRECTORY and not removed
            or case == 'leaf-sync' and removed == TRIPLE[:1] and path == active.root / ROOT
            or case == 'root-sync' and removed == [*TRIPLE, ROOT] and path == active.root / 'var/lib')
        if fail and not interruptions:
            interruptions.append(True)
            raise OSError('synthetic denied trust durability interruption')
        real_sync(fd)
        info = os.fstat(fd)
        synced.append((info.st_dev, info.st_ino))
    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'unlink', unlink)
        scoped.setattr(os, 'rmdir', rmdir)
        scoped.setattr(os, 'fsync', sync)
        if case == 'lock-busy':
            import fcntl
            with (active.root / TRIPLE[-1]).open('rb') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    TrustCleanup(owner).remove()
            assert active.snapshot() == before and removed == []
        elif case in ('missing-record', 'record-mode'):
            # An interrupted real first unlink creates the recovery prestate.
            def interrupted(name, *, dir_fd=None):
                unlink(name, dir_fd=dir_fd)
                raise OSError('synthetic interruption after first trust unlink')
            scoped.setattr(os, 'unlink', interrupted)
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                TrustCleanup(owner).remove()
            if case == 'missing-record':
                real_unlink(active.root / RECORD)
            else:
                (active.root / RECORD).chmod(0o644)
            after = active.snapshot()
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                TrustCleanup(owner).remove()
            assert active.snapshot() == after and removed == TRIPLE[:1]
        elif case == 'complete':
            TrustCleanup(owner).remove()
        else:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                TrustCleanup(owner).remove()
            if case in ('live-trust', 'corrupt-anchor'):
                assert active.snapshot() == before and removed == []
            elif case == 'replacement':
                assert removed == TRIPLE[:1]
                after = active.snapshot()
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    TrustCleanup(owner).remove()
                assert active.snapshot() == after
        if case in ('complete', 'record-sync', 'leaf-sync', 'root-sync'):
            assert case == 'complete' or interruptions
            for _ in range(2):
                TrustCleanup(owner).remove()
            assert removed == [*TRIPLE, ROOT] and not (active.root / ROOT).exists()
            data = (active.root / RECORD).read_bytes()
            assert b'synthetic-retirement-invite' not in data and b'devices' not in data
            assert (active.root / 'var/lib').stat().st_ino in {value[1] for value in synced}
    assert active.effects() == effects
    assert {path: ((active.root / path).stat().st_ino, (active.root / path).read_bytes())
        for path in retained} == retained
    assert active.io.home_retirement_status()['state'] == 'migration pending'
    assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('boundary', ['complete', 'leaf-sync', 'native-after-unlink'])
def test_active_trust_cleanup_uses_current_original_death_and_recovers_without_replay(active, monkeypatch, boundary):
    device, _ = active.grant('device')
    stream = active.stream(device, 'device')
    with active.serving(monkeypatch):
        owner = ActiveRetirement.capture(active.io, active.fence)
        native_target_stop(active)
        stopped = owner.revoke_and_stop_services()
        stopped.remove_managed()
    effects, frames = active.effects(), list(active.frames)
    retained = {path: ((active.root / path).stat().st_ino, (active.root / path).read_bytes())
        for path in (INVENTORY, NATIVE, PIN, 'retained-native/saved-home')}
    real_unlink, real_sync = os.unlink, os.fsync
    removed, interrupted = [], []
    def unlink(name, *, dir_fd=None):
        path = str((Path(os.readlink(f'/proc/self/fd/{dir_fd}')) / name).relative_to(active.root))
        real_unlink(name, dir_fd=dir_fd)
        if path in TRIPLE:
            removed.append(path)
            if boundary == 'native-after-unlink':
                active.native['happyranch-managed.target'].update(ActiveState='active', SubState='active')
    def sync(fd):
        if (boundary == 'leaf-sync' and removed and not interrupted
                and Path(os.readlink(f'/proc/self/fd/{fd}')) == active.root / ROOT):
            interrupted.append(True)
            raise OSError('synthetic trust parent sync interruption')
        real_sync(fd)
    def fresh():
        io = ManagedInstaller(root=active.root, root_owner=active.io.root_owner, layout='deb',
            run=active.io._run, lookup_user=active.io._lookup_user, lookup_group=active.io._lookup_group)
        StoppedRetirement.remove_trust(io, active.fence)
    with monkeypatch.context() as scoped:
        scoped.setattr(os, 'unlink', unlink)
        scoped.setattr(os, 'fsync', sync)
        if boundary == 'complete':
            fresh()
        else:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
            assert removed == TRIPLE[:1]
        if boundary == 'native-after-unlink':
            after = active.snapshot()
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
            assert active.snapshot() == after
        else:
            fresh()
            assert removed == TRIPLE and not (active.root / ROOT).exists()
            # Reappearance is refused without deletion or regained admission.
            active.directory(ROOT).chmod(0o700)
            active.file(TRIPLE[0], b'synthetic-reappearing-authority', 0o600)
            after = active.snapshot()
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                fresh()
            assert active.snapshot() == after
    assert active.effects() == effects and active.frames == frames and active.closed == ['device']
    with pytest.raises(StreamClosed):
        stream.receive()
    assert {path: ((active.root / path).stat().st_ino, (active.root / path).read_bytes())
        for path in retained} == retained
    assert not (active.root / FINAL).exists()
