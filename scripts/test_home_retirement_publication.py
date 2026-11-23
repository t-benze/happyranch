"""Minimized publication I/O; complete native/cutoff proof is composed separately.

Run: uv run --frozen pytest scripts/test_home_retirement_publication.py -v --tb=short
The lower keeper isolates the caller proof, never claiming a migration success.
Production active/inert entries supply the real complete proof in the account keeper.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from runtime.remote_access.home_retirement import RetirementUncertain, STATE_DIRECTORY as STATE
from runtime.remote_access.home_retirement_fence import retirement_fence
from runtime.remote_access.home_retirement_publication import publish_final
from runtime.remote_access.home_retirement_upgrade import retire
from scripts.test_home_retirement import Fixture, FINAL

EXPECTED = b'{"account":"absent","format":"home-retirement@1","state":"retired"}\n'
PENDING = STATE + '/.home-retirement.json.pending'


@pytest.mark.parametrize('case', ['complete', 'file-sync', 'parent-sync',
    'replacement-during-sync', 'reappearance-during-sync'])
def test_completed_entry_resyncs_real_published_receipt_before_success(tmp_path, monkeypatch, case):
    fixture = Fixture(tmp_path / 'root')
    fixture.directory(STATE)
    with retirement_fence(fixture.io) as fence:
        assert publish_final(fixture.io, fence, require=lambda: 'absent') == EXPECTED
    final = fixture.root / FINAL
    original_inode = final.stat().st_ino
    before, syncs, changed = fixture.snapshot(), [], []
    real_sync = os.fsync
    def sync(fd):
        path = Path(os.readlink(f'/proc/self/fd/{fd}'))
        syncs.append(str(path.relative_to(fixture.root)))
        if (case == 'file-sync' and path == final
                or case == 'parent-sync' and path == fixture.root / STATE):
            raise OSError('synthetic interrupted completed-receipt sync')
        real_sync(fd)
        if path == final and case == 'replacement-during-sync':
            final.rename(final.with_name('original-receipt'))
            fixture.file(FINAL, EXPECTED)
            changed.append(fixture.snapshot())
        if path == final and case == 'reappearance-during-sync':
            fixture.file('etc/happyranch/invite-issuer.json', b'synthetic-reappearance')
            changed.append(fixture.snapshot())
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', sync)
        # Ordinary detection never completes durability or mutates evidence.
        fixture.detect('retired')
        assert syncs == []
        if case == 'complete':
            assert retire(fixture.io).state == 'retired'
        else:
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                retire(fixture.io)
    assert syncs == ([FINAL] if case == 'file-sync' else [FINAL, STATE])
    assert fixture.snapshot() == (changed[-1] if changed else before)
    assert final.read_bytes() == EXPECTED
    if case == 'replacement-during-sync':
        assert final.stat().st_ino != original_inode
        assert final.with_name('original-receipt').stat().st_ino == original_inode
    else:
        assert final.stat().st_ino == original_inode
    if not changed:
        assert retire(fixture.io).state == 'retired'
        assert fixture.snapshot() == before
    # Fixture.run permits only read-only systemctl show, never destructive effects.
    assert all(call[:3] == ['systemctl', 'show', 'happyranch-headscale.service'] for call in fixture.calls)


@pytest.mark.parametrize('case', ['complete', 'file-sync', 'stage-parent-sync', 'rename',
    'final-parent-sync', 'postpublication-proof', 'reappearance', 'short-write',
    'foreign-final', 'retained-account', 'native-unknown', 'late-final'])
def test_final_publication_requires_facts_syncs_and_recovers_without_rewrite(tmp_path, monkeypatch, case):
    fixture = Fixture(tmp_path / 'root')
    fixture.directory(STATE)
    fixture.file('retained-local/task.db', b'synthetic-local-work')
    proof_calls, writes, renames, syncs = [], [], [], []
    real_write, real_rename, real_sync = os.write, fixture.io._rename_provision_entry, os.fsync
    faulted = False
    def require():
        proof_calls.append(True)
        if case == 'postpublication-proof' and (fixture.root / FINAL).exists() and not faulted:
            raise RetirementUncertain()
        return 'retained-disabled: static consumer' if case == 'retained-account' else 'absent'
    def write(fd, data):
        writes.append(data)
        if case == 'short-write':
            return real_write(fd, data[:-1])
        return real_write(fd, data)
    def rename(parent, src, dst, *, updating=False):
        nonlocal faulted
        if case == 'rename' and not faulted:
            faulted = True
            raise OSError('synthetic interrupted rename')
        if case == 'late-final':
            fixture.file(FINAL, b'synthetic-late-foreign-final')
        real_rename(parent, src, dst, updating=updating)
        renames.append((src, dst))
        if case == 'reappearance':
            fixture.file('etc/happyranch/invite-issuer.json', b'synthetic-foreign-evidence')
    def sync(fd):
        nonlocal faulted
        path = Path(os.readlink(f'/proc/self/fd/{fd}'))
        syncs.append(str(path.relative_to(fixture.root)))
        fail = ((case == 'file-sync' and path == fixture.root / PENDING)
            or (case == 'stage-parent-sync' and path == fixture.root / STATE
                and (fixture.root / PENDING).exists())
            or (case == 'final-parent-sync' and path == fixture.root / STATE
                and (fixture.root / FINAL).exists()))
        if fail and not faulted:
            faulted = True
            raise OSError('synthetic interrupted sync')
        real_sync(fd)
    with retirement_fence(fixture.io) as fence:
        if case == 'foreign-final':
            fixture.file(FINAL, b'synthetic-foreign-final')
        if case == 'native-unknown':
            fixture.properties['MainPID'] = '42'
            # A zero reload exit cannot establish native retirement.
            def run(argv, **kwargs):
                if argv == ['systemctl', 'daemon-reload']:
                    fixture.calls.append(argv)
                    return 0, ''
                return fixture.run(argv, **kwargs)
            fixture.io.manager._run = run
        before = fixture.snapshot()
        with monkeypatch.context() as patch:
            patch.setattr(os, 'write', write)
            patch.setattr(fixture.io, '_rename_provision_entry', rename)
            patch.setattr(os, 'fsync', sync)
            if case == 'complete':
                assert publish_final(fixture.io, fence, require=require) == EXPECTED
            else:
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    publish_final(fixture.io, fence, require=require)
        recoverable = ('complete', 'file-sync', 'stage-parent-sync', 'rename',
            'final-parent-sync', 'postpublication-proof')
        if case in recoverable:
            faulted = True
            with monkeypatch.context() as patch:
                patch.setattr(os, 'write', write)
                patch.setattr(fixture.io, '_rename_provision_entry', rename)
                patch.setattr(os, 'fsync', sync)
                assert publish_final(fixture.io, fence, require=require) == EXPECTED
                identity = (fixture.root / FINAL).stat().st_ino
                assert publish_final(fixture.io, fence, require=require) == EXPECTED
            assert (fixture.root / FINAL).stat().st_ino == identity
            assert writes == [EXPECTED] and len(renames) == 1
            assert PENDING in syncs and STATE in syncs
            assert not (fixture.root / PENDING).exists()
            assert fixture.detect('retired')['detail'] == 'retirement confirmed'
            assert fixture.detect('retired')['detail'] == 'retirement confirmed'
            # R04 consumes this actual writer output, never a seeded final.
            fixture.file('etc/happyranch/invite-issuer.json', b'synthetic-reappearance')
            fixture.detect('migration pending')
            with pytest.raises(RetirementUncertain):
                publish_final(fixture.io, fence, require=require)
        else:
            assert fixture.io.home_retirement_status()['state'] == 'migration pending'
            if case in ('foreign-final', 'retained-account', 'native-unknown'):
                assert fixture.snapshot() == before and not writes and not renames
            if case == 'late-final':
                assert (fixture.root / FINAL).read_bytes() == b'synthetic-late-foreign-final'
                assert (fixture.root / PENDING).read_bytes() == EXPECTED
                assert not renames
            if case == 'short-write':
                assert (fixture.root / PENDING).read_bytes() == EXPECTED[:-1]
                # The initial interrupted call and read-only detection stay
                # pending; explicit fresh proof may complete its missing byte.
                staged_inode = (fixture.root / PENDING).stat().st_ino
                assert publish_final(fixture.io, fence, require=require) == EXPECTED
                assert (fixture.root / FINAL).stat().st_ino == staged_inode
                fixture.detect('retired')
            if case == 'reappearance':
                assert (fixture.root / FINAL).read_bytes() == EXPECTED
                with pytest.raises(RetirementUncertain):
                    publish_final(fixture.io, fence, require=require)
        assert bool(proof_calls) == (case != 'foreign-final')
        assert (fixture.root / 'retained-local/task.db').read_bytes() == b'synthetic-local-work'
        assert not any(argv[:2] in (['systemctl', 'stop'], ['systemctl', 'disable'])
            or argv[0] in ('userdel', 'groupdel') for argv in fixture.calls)


@pytest.mark.parametrize('case', ['inventory', 'transport', 'peer', 'denied',
    'unsafe-record', 'missing-fence', 'changed-fence', 'unknown-state'])
def test_produced_final_keeps_only_private_inert_provenance_with_exact_fence(tmp_path, case):
    fixture = Fixture(tmp_path / 'root')
    fixture.directory(STATE)
    with retirement_fence(fixture.io) as fence:
        assert publish_final(fixture.io, fence, require=lambda: 'absent') == EXPECTED
    name = case if case in ('inventory', 'transport', 'peer', 'denied') else 'inventory'
    fixture.file(STATE + '/home-retirement-' + name + '.json', b'synthetic-inert-provenance')
    if case == 'unsafe-record':
        (fixture.root / STATE / 'home-retirement-inventory.json').chmod(0o644)
    elif case == 'missing-fence':
        (fixture.root / STATE / '.home-activation.json.pending').unlink()
    elif case == 'changed-fence':
        (fixture.root / STATE / '.home-activation.json.pending').write_bytes(b'unknown-pending-intent')
    elif case == 'unknown-state':
        fixture.file(STATE + '/home-unknown.json', b'synthetic-unknown-evidence')
    fixture.detect('retired' if case in ('inventory', 'transport', 'peer', 'denied') else 'migration pending')
    assert (fixture.root / FINAL).read_bytes() == EXPECTED


@pytest.mark.parametrize('case', ['empty', 'prefix', 'last-byte', 'repair-short-write',
    'proof-lost', 'nonprefix', 'mode', 'hardlink', 'symlink', 'replacement-during-proof'])
def test_interrupted_stage_requires_canonical_prefix_and_fresh_proof_before_completion(tmp_path, monkeypatch, case):
    fixture = Fixture(tmp_path / 'root')
    fixture.directory(STATE)
    fixture.file('retained-local/work', b'synthetic-retained-work')
    stage = fixture.root / PENDING
    real_write, real_open = os.write, os.open
    prefix = 0 if case == 'empty' else len(EXPECTED) - 1 if case == 'last-byte' else 11
    with retirement_fence(fixture.io) as fence:
        def interrupted_write(fd, data):
            return real_write(fd, data[:prefix])
        with monkeypatch.context() as patch:
            patch.setattr(os, 'write', interrupted_write)
            with pytest.raises(RetirementUncertain, match='^migration pending$'):
                publish_final(fixture.io, fence, require=lambda: 'absent')
        assert stage.read_bytes() == EXPECTED[:prefix]
        original_inode = stage.stat().st_ino
        # Detection sees an actual interrupted production write and never repairs.
        fixture.detect('migration pending')
        fixture.detect('migration pending')
        if case == 'nonprefix':
            stage.write_bytes(b'synthetic-unknown-stage')
        elif case == 'mode':
            stage.chmod(0o644)
        elif case == 'hardlink':
            os.link(stage, fixture.root / 'retained-local/stage-link')
        elif case == 'symlink':
            stage.unlink()
            stage.symlink_to(fixture.root / 'retained-local/work')
        before, repairs, opened = fixture.snapshot(), [], []
        def open_stage(path, flags, *args, **kwargs):
            descriptor = real_open(path, flags, *args, **kwargs)
            if path == Path(PENDING).name and flags & os.O_RDWR:
                opened.append(descriptor)
            return descriptor
        replaced = False
        def require():
            nonlocal replaced
            if case == 'proof-lost':
                raise RetirementUncertain()
            if case == 'replacement-during-proof' and opened and not replaced:
                replaced = True
                stage.rename(fixture.root / 'retained-local/original-stage')
                fixture.file(PENDING, EXPECTED[:prefix])
            return 'absent'
        def write(fd, data):
            repairs.append(data)
            return real_write(fd, data[:1] if case == 'repair-short-write' else data)
        with monkeypatch.context() as patch:
            patch.setattr(os, 'open', open_stage)
            patch.setattr(os, 'write', write)
            if case in ('empty', 'prefix', 'last-byte'):
                assert publish_final(fixture.io, fence, require=require) == EXPECTED
            else:
                with pytest.raises(RetirementUncertain, match='^migration pending$'):
                    publish_final(fixture.io, fence, require=require)
        if case in ('empty', 'prefix', 'last-byte', 'repair-short-write'):
            if case == 'repair-short-write':
                assert repairs == [EXPECTED[prefix:]]
                assert stage.read_bytes() == EXPECTED[:prefix + 1]
                assert not (fixture.root / FINAL).exists()
                assert publish_final(fixture.io, fence, require=require) == EXPECTED
            assert (fixture.root / FINAL).stat().st_ino == original_inode
            assert not stage.exists()
            complete = fixture.snapshot()
            assert publish_final(fixture.io, fence, require=require) == EXPECTED
            assert fixture.snapshot() == complete
            fixture.detect('retired')
            fixture.detect('retired')
            fixture.file('etc/happyranch/invite-issuer.json', b'synthetic-reappearance')
            fixture.detect('migration pending')
            with pytest.raises(RetirementUncertain):
                publish_final(fixture.io, fence, require=require)
        else:
            assert not repairs and not (fixture.root / FINAL).exists()
            if case == 'replacement-during-proof':
                assert replaced and stage.stat().st_ino != original_inode
                assert stage.read_bytes() == EXPECTED[:prefix]
                assert (fixture.root / 'retained-local/original-stage').read_bytes() == EXPECTED[:prefix]
            else:
                assert fixture.snapshot() == before
        assert (fixture.root / 'retained-local/work').read_bytes() == b'synthetic-retained-work'
        assert not any(argv[0] in ('userdel', 'groupdel') or argv[:2] in
            (['systemctl', 'stop'], ['systemctl', 'disable']) for argv in fixture.calls)
