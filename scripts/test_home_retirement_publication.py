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
from scripts.test_home_retirement import Fixture, FINAL

EXPECTED = b'{"account":"absent","format":"home-retirement@1","state":"retired"}\n'
PENDING = STATE + '/.home-retirement.json.pending'


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
                with pytest.raises(RetirementUncertain):
                    publish_final(fixture.io, fence, require=require)
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
