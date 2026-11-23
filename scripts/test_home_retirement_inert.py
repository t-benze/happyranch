"""Prepared-inert retirement through real journals/native ownership readers.

Run: uv run --frozen pytest scripts/test_home_retirement_inert.py -v --tb=short
Only historical input files and NSS/systemd/package observations are synthetic.
No fixture supplies a cutoff acknowledgement or computes a retirement record.
"""
from __future__ import annotations

import os

import pytest

from runtime.remote_access.home_retirement import HomeRetirement, RetirementUncertain
from runtime.remote_access.home_retirement_inert import InertRetirement
from runtime.remote_access.installer import ManagedInstaller
from scripts.test_home_retirement_coordinator import active, INVENTORY, NATIVE, FINAL

TARGET = 'happyranch-managed.target'
SOCKET = 'happyranch-release-verifier.socket'
TRUST = 'var/lib/happyranch-connector/trust-state.json'


def fresh(fixture):
    return ManagedInstaller(root=fixture.root, root_owner=fixture.io.root_owner, layout='deb',
        run=fixture.io._run, lookup_user=fixture.io._lookup_user, lookup_group=fixture.io._lookup_group)


@pytest.mark.parametrize('active', ['inert'], indirect=True)
def test_inert_native_disable_has_no_trust_initialization_or_replay(active):
    owner = InertRetirement.capture(active.io, active.fence)
    originals = {path: ((active.root / path).stat().st_ino, (active.root / path).read_bytes())
                 for path in (INVENTORY, NATIVE)}
    dedicated, managed = owner.stop_and_disable()
    assert dedicated.observation.process is None
    assert all(process is None for process in managed.observation.processes.values())
    assert active.effects() == [['systemctl', 'disable', TARGET], ['systemctl', 'disable', SOCKET]]
    active.calls.clear()
    for _ in range(2):
        io = fresh(active)
        recovered = InertRetirement.from_saved(io, active.fence)
        recovered.stop_and_disable()
        recovered.require(disabled=True)
    assert active.effects() == []
    assert all(((active.root / path).stat().st_ino, (active.root / path).read_bytes()) == saved
               for path, saved in originals.items())
    assert not (active.root / TRUST).exists() and not (active.root / FINAL).exists()
    assert (active.root / 'retained-native/saved-home').read_bytes() == b'synthetic-P25-saved-home'


@pytest.mark.parametrize('active', ['inert'], indirect=True)
@pytest.mark.parametrize('boundary', ['preparation', 'target-error', 'target-lost-reply',
    'socket-error', 'socket-lost-reply'])
def test_inert_interruption_reopens_originals_and_only_finishes_remaining_disable(active, monkeypatch, boundary):
    base_run, base_open = active.io._run, os.open
    unit = TARGET if boundary.startswith('target') else SOCKET
    def run(argv, **kwargs):
        result = base_run(argv, **kwargs)
        return (1, 'synthetic lost native reply') if argv == ['systemctl', 'disable', unit] else result
    def open_file(name, flags, *args, **kwargs):
        if name == NATIVE.rsplit('/', 1)[1] and flags & os.O_CREAT:
            raise OSError('synthetic interruption before first native publication')
        return base_open(name, flags, *args, **kwargs)
    with monkeypatch.context() as scoped:
        if boundary == 'preparation':
            scoped.setattr(os, 'open', open_file)
        elif boundary.endswith('error'):
            active.fail = ('disable', unit)
        else:
            scoped.setattr(active.io, '_run', run)
            scoped.setattr(active.io.manager, '_run', run)
        with pytest.raises(RetirementUncertain, match='^migration pending$'):
            InertRetirement.capture(active.io, active.fence).stop_and_disable()
    active.fail = None
    original = ((active.root / INVENTORY).stat().st_ino, (active.root / INVENTORY).read_bytes())
    completed = {argv[2] for argv in active.effects() if not boundary.endswith('error') or argv[2] != unit}
    active.calls.clear()
    detected = active.snapshot()
    assert HomeRetirement(fresh(active)).detect().state == 'migration pending'
    assert active.effects() == [] and active.snapshot() == detected
    # An independently unproven native state cannot authorize recovery.
    saved = dict(active.native[SOCKET])
    active.native[SOCKET].update(ActiveState='active', SubState='listening')
    before = active.snapshot()
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        InertRetirement.from_saved(fresh(active), active.fence).stop_and_disable()
    assert active.effects() == [] and active.snapshot() == before
    active.native[SOCKET] = saved
    recovered = InertRetirement.from_saved(fresh(active), active.fence)
    recovered.stop_and_disable()
    recovered.require(disabled=True)
    assert active.effects() == [['systemctl', 'disable', name] for name in (TARGET, SOCKET) if name not in completed]
    assert ((active.root / INVENTORY).stat().st_ino, (active.root / INVENTORY).read_bytes()) == original
    active.calls.clear()
    InertRetirement.from_saved(fresh(active), active.fence).stop_and_disable()
    assert active.effects() == []
    assert not (active.root / TRUST).exists() and not (active.root / FINAL).exists()


@pytest.mark.parametrize('active', ['inert'], indirect=True)
@pytest.mark.parametrize('drift', ['trust', 'trust-lock', 'socket', 'peer', 'activation', 'native-active', 'fragment', 'native-record'])
def test_inert_saved_recovery_refuses_authority_or_original_native_drift(active, drift):
    InertRetirement.capture(active.io, active.fence)
    if drift in ('trust', 'trust-lock', 'socket', 'peer', 'activation'):
        path = {'trust': TRUST, 'trust-lock': TRUST + '.lock', 'socket': 'run/happyranch-connector/control.sock',
            'peer': 'var/lib/happyranch-remote-access/home-retirement-peer.json',
            'activation': 'var/lib/happyranch-remote-access/home-activation.json'}[drift]
        (active.root / path).write_bytes(b'synthetic unexpected authority evidence')
        (active.root / path).chmod(0o600)
    elif drift == 'native-active':
        active.native[SOCKET].update(ActiveState='active', SubState='listening')
    elif drift == 'fragment':
        path = active.root / 'etc/systemd/system/happyranch-headscale.service'
        path.rename(active.root / 'preserved-original-fragment')
        path.write_bytes((active.root / 'preserved-original-fragment').read_bytes())
        path.chmod(0o644)
    else:
        (active.root / NATIVE).write_bytes(b'{')
    active.calls.clear()
    before = active.snapshot()
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        InertRetirement.from_saved(fresh(active), active.fence).stop_and_disable()
    assert active.effects() == [] and active.snapshot() == before
    assert not (active.root / FINAL).exists()


@pytest.mark.parametrize('active', ['inert'], indirect=True)
def test_inert_rechecks_authority_before_each_remaining_native_effect(active, monkeypatch):
    owner = InertRetirement.capture(active.io, active.fence)
    base_run = active.io._run
    def run(argv, **kwargs):
        result = base_run(argv, **kwargs)
        if argv == ['systemctl', 'disable', TARGET]:
            (active.root / TRUST).write_bytes(b'synthetic unexpected trust after first disable')
            (active.root / TRUST).chmod(0o600)
        return result
    monkeypatch.setattr(active.io, '_run', run)
    monkeypatch.setattr(active.io.manager, '_run', run)
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        owner.stop_and_disable()
    assert active.effects() == [['systemctl', 'disable', TARGET]]
    assert (active.root / TRUST).read_bytes() == b'synthetic unexpected trust after first disable'
    assert not (active.root / FINAL).exists()


def test_inert_capture_never_becomes_an_active_retirement_fallback(active):
    before = active.snapshot()
    with pytest.raises(RetirementUncertain, match='^migration pending$'):
        InertRetirement.capture(active.io, active.fence)
    assert active.effects() == [] and active.snapshot() == before
    assert not (active.root / INVENTORY).exists() and not (active.root / FINAL).exists()
