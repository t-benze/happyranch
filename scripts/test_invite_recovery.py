"""B11 standalone checks: real protected journal, pairing state and private IPC."""
from __future__ import annotations

import fcntl
import json
import socket
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, ContextManager, Iterator

import pytest

from runtime.remote_access import helper_control as control
from runtime.remote_access import invite_recovery as recovery
from runtime.remote_access.invite_control import InviteControl
from runtime.remote_access.invite_issuer import HomeInviteIssuer
from runtime.remote_access.invite_v2 import decode
from runtime.remote_access.pairing import PinnedPairingManager
from runtime.remote_access.state_store import AtomicFileTrustStateStore
from runtime.remote_access.status_v2 import CleanupState, InvitePhase
from scripts.test_invite_control import Peer
from scripts.test_home_minter import key_record, setup as minter_setup
from scripts.test_invite_issuer import category, record, setup

OPERATIONS = (recovery.cancel_invite, recovery.recover_invite)
CANARY = 'SYNTHETIC-PRIVATE-DEPENDENCY-ERROR'


@contextmanager
def server(tmp_path: Path, operations: Operations, *, drop_reply: bool = False) -> Iterator[tuple[control.ControlClient, list[bytes]]]:
    # Bounded accept matters for regression proof: production may refuse before
    # opening IPC. Reuse B10's root-peer/lost-reply transport observation only.
    with tempfile.TemporaryDirectory(prefix='hr-b11-', dir='/tmp') as directory:
        path = directory + '/control.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(path)
            listener.listen(1)
            listener.settimeout(2)
            frames = []
            def serve() -> None:
                try:
                    connection, _ = listener.accept()
                except TimeoutError:
                    return
                with connection:
                    control.ControlHandler(operations).handle(Peer(connection, frames, drop_reply=drop_reply))
            thread = threading.Thread(target=serve)
            thread.start()
            try:
                yield control.ControlClient(path), frames
            finally:
                thread.join(3)
                assert not thread.is_alive()


class Minter:
    """Only the injected cleanup proof is simulated; no mint capability exists."""

    def __init__(self, inspect: Callable[[], None] = lambda: None) -> None:
        self.calls = []
        self.result = True
        self.inspect = inspect

    def recover(self, operation_id: str, *, deadline: float) -> object:
        self.calls.append((operation_id, deadline))
        self.inspect()
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


class Operations:
    """B10 fixture binding to the real existing serving pairing owner."""

    def __init__(self, issuer: HomeInviteIssuer, manager: PinnedPairingManager, store: AtomicFileTrustStateStore) -> None:
        self.issuer, self.manager, self.store = issuer, manager, store
        self.adapter = InviteControl(manager)

    def mutation_lock(self) -> ContextManager[None]:
        return self.manager.mutation_lock()

    def status(self) -> control.Snapshot:
        # Real IPC must not retain the issuer flock. Distinct flock acquisition
        # plus the trust snapshot would fail/deadlock if either were retained.
        with self.issuer._recovery_record() as (_, selected):
            assert selected['outcome'] == 'uncertain'
        return control.Snapshot(self.store.snapshot()[1], 1000, True, 'ready', True, None, ())

    def reserve_invite(self, generation: int, digest: str, expires_at: int, *, deadline: float) -> int:
        return self.adapter.reserve_invite(generation, digest, expires_at, deadline=deadline)

    def invalidate_invite(self, generation: int, *, deadline: float) -> int:
        return self.adapter.invalidate_invite(generation, deadline=deadline)

    def revoke_all(self, generation: int, *, deadline: float) -> int:
        return self.adapter.revoke_all(generation, deadline=deadline)


class LocalControl:
    """Fault injection around the real InviteControl transaction."""

    def __init__(self, issuer: HomeInviteIssuer, manager: PinnedPairingManager, store: AtomicFileTrustStateStore) -> None:
        self.issuer, self.adapter, self.store = issuer, InviteControl(manager), store
        self.calls = []
        self.after = lambda: None

    def invalidate_invite(self, generation: int, deadline: float, before_commit: Callable[[], None]) -> int:
        self.calls.append((generation, deadline))
        with self.issuer._recovery_record() as (_, selected):
            assert selected['outcome'] == 'uncertain'
        before_commit()
        result = self.adapter.invalidate_invite(generation, deadline=deadline)
        self.after()
        return result


def issued(tmp_path: Path) -> tuple:
    issuer, original, manager, store, clock = setup(tmp_path)
    invite = issuer.issue()
    client = LocalControl(issuer, manager, store)
    def inspect() -> None:
        state = store.load()
        assert state.invite_record is state.pending_approval is state.delivery is None, 'connector must invalidate FIRST'
        with issuer._recovery_record() as (_, selected):
            assert selected['outcome'] == 'uncertain'
    minter = Minter(inspect)
    args = dict(issuer=issuer, control=client, minter=minter, generation=store.snapshot()[1],
                deadline=time.monotonic() + 60, before_commit=lambda: None)
    return issuer, original, manager, store, clock, invite, client, minter, args


@pytest.mark.parametrize('operation', OPERATIONS)
@pytest.mark.parametrize('phase', ['issued', 'pending', 'delivery', 'lost-reserve', 'uncertain',
                                 'refused', 'expired', 'encoding', 'pairing', 'cancelled', 'recovered'])
def test_current_operation_private_ipc_consumes_ceremony(tmp_path: Path, operation: Callable, phase: str) -> None:
    issuer, original, manager, store, clock, invite, _, minter, args = issued(tmp_path)
    secret = decode(invite.invite_text, now=int(clock().timestamp())).pair_secret
    pending = None
    if phase in ('pending', 'delivery'):
        pending = manager.redeem(secret, 'node', 'control', clock())
    if phase == 'delivery':
        manager.approve_pending(pending.generation, ('control', 'node'))
    if phase == 'cancelled':
        assert recovery.cancel_invite(**args).invite == InvitePhase.CANCELLED
        minter.calls.clear()  # Observe the subsequent separately authorized call.
    elif phase not in ('issued', 'pending', 'delivery'):
        with issuer._recovery_record() as (parent, selected):
            selected['outcome'] = 'minting' if phase == 'lost-reserve' else phase
            issuer._write(parent, selected)
    before = record(tmp_path)
    args['generation'] = store.snapshot()[1]
    with server(tmp_path, Operations(issuer, manager, store)) as (client, frames):
        result = operation(**(args | {'control': client}))
    expected = InvitePhase.CANCELLED if operation is recovery.cancel_invite else InvitePhase.RECOVERED
    assert result == recovery.RecoveryResult(recovery.RecoveryOutcome.COMPLETE, expected, CleanupState.COMPLETE)
    assert minter.calls == [(before['operation_id'], args['deadline'])]
    after = record(tmp_path)
    assert after == before | {'outcome': 'recovered'}
    assert category(issuer.issue) == 'rate_limited', 'cleanup must not reset one/minute'
    assert len(original.calls) == 1, 'cancel/recover must never mint'
    assert manager.redeem(secret, 'node', 'control', clock()) is None
    if pending:
        assert manager.complete(pending.completion_handle, 'node', 'control', clock()) is None
    wire = b''.join(frames)
    for private in (invite.invite_text, secret, original.key, before['operation_id'], before['binding_digest']):
        assert private.encode() not in wire
        assert private not in repr(result)
    request = json.loads(frames[0])
    assert request == {'method': 'invite-invalidate@1', 'expected_generation': args['generation'],
                       'deadline': args['deadline']}
    clock.advance(60)
    assert issuer.issue().operation_id != before['operation_id']


@pytest.mark.parametrize('operation', OPERATIONS)
@pytest.mark.parametrize('proof', [False, None, 1, RuntimeError(CANARY)])
def test_cleanup_requires_exact_proof_and_preserves_uncertainty(tmp_path: Path, operation: Callable, proof: object) -> None:
    issuer, original, _, _, clock, _, _, minter, args = issued(tmp_path)
    minter.result = proof
    result = operation(**args)
    assert result == recovery.RecoveryResult(recovery.RecoveryOutcome.PARTIAL_CLEANUP,
                                             InvitePhase.CANCELLED, CleanupState.UNCERTAIN)
    assert len(minter.calls) == 1
    assert record(tmp_path)['outcome'] == 'uncertain'
    clock.advance(3600)
    assert category(issuer.issue) == 'uncertain'
    assert len(original.calls) == 1
    assert CANARY not in repr(result)


@pytest.mark.parametrize('operation', OPERATIONS)
def test_lost_invalidation_reply_is_uncertain_then_fresh_recovery(tmp_path: Path, operation: Callable) -> None:
    issuer, original, manager, store, _, _, _, minter, args = issued(tmp_path)
    with server(tmp_path, Operations(issuer, manager, store), drop_reply=True) as (client, _):
        result = operation(**(args | {'control': client}))
    assert result == recovery.RecoveryResult(recovery.RecoveryOutcome.UNCERTAIN,
                                             InvitePhase.UNCERTAIN, CleanupState.UNCERTAIN)
    assert store.load().invite_record is None, 'lost reply can follow actual invalidation'
    assert not minter.calls and record(tmp_path)['outcome'] == 'uncertain'
    # Fresh issuer object reads the persisted uncertainty; no automatic remint.
    issuer = HomeInviteIssuer(config=issuer._config, minter=original, pairing=manager,
                              state_dir=tmp_path / 'issuer', now_fn=issuer._now_fn)
    args['issuer'] = issuer
    args['generation'] = store.snapshot()[1]
    with server(tmp_path, Operations(issuer, manager, store)) as (client, _):
        result = recovery.recover_invite(**(args | {'control': client}))
    assert result.invite == InvitePhase.RECOVERED and result.cleanup == CleanupState.COMPLETE
    assert len(original.calls) == 1


@pytest.mark.parametrize('case', ['missing', 'busy', 'symlink', 'mode', 'pending', 'binding', 'malformed', 'caller'])
def test_admission_refuses_without_dependency_effects(tmp_path: Path, case: str) -> None:
    issuer, _, _, _, _, _, client, minter, args = issued(tmp_path)
    journal = tmp_path / 'issuer' / 'invite-issuer.json'
    lock = None
    if case == 'missing':
        journal.unlink()
    elif case == 'busy':
        lock = (tmp_path / 'issuer' / 'invite-issuer.lock').open('rb')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif case == 'symlink':
        target = tmp_path / 'private-journal'
        journal.rename(target)
        journal.symlink_to(target)
    elif case == 'mode':
        journal.chmod(0o644)
    elif case == 'pending':
        (tmp_path / 'issuer' / 'invite-issuer.pending').write_bytes(b'preserve')
    elif case == 'binding':
        journal.write_text(json.dumps(record(tmp_path) | {'binding_digest': '0' * 64}))
    elif case == 'malformed':
        journal.write_bytes(b'{')
    elif case == 'caller':
        def disconnected() -> None:
            raise control.Refused('disconnected')
        args['before_commit'] = disconnected
    before = journal.read_bytes() if case != 'missing' else None
    try:
        result = recovery.recover_invite(**args)
    finally:
        if lock:
            lock.close()
    if case == 'missing':
        assert result == recovery.RecoveryResult(recovery.RecoveryOutcome.COMPLETE, InvitePhase.NONE, CleanupState.NOT_NEEDED)
        assert not journal.exists()
    else:
        assert result.outcome == recovery.RecoveryOutcome.REFUSED
        assert journal.read_bytes() == before
    assert not client.calls and not minter.calls


@pytest.mark.parametrize('bound', ['expired', 'future', 'nan', 'bool', 'generation', 'app-id'])
def test_invalid_inputs_never_dispatch(tmp_path: Path, bound: str) -> None:
    _, _, _, _, _, _, client, minter, args = issued(tmp_path)
    before = record(tmp_path)
    if bound == 'app-id':
        with pytest.raises(TypeError):
            recovery.cancel_invite(**args, operation_id=before['operation_id'])
    else:
        args.update({'generation': True} if bound == 'generation' else
                    {'deadline': {'expired': time.monotonic() - 1, 'future': time.monotonic() + 121,
                                  'nan': float('nan'), 'bool': True}[bound]})
        assert recovery.cancel_invite(**args).outcome == recovery.RecoveryOutcome.REFUSED
    assert record(tmp_path) == before
    assert not client.calls and not minter.calls


@pytest.mark.parametrize('stage', ['intent', 'ipc', 'cleanup', 'publication'])
def test_original_deadline_never_accepts_late_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str) -> None:
    issuer, _, _, _, _, _, client, minter, args = issued(tmp_path)
    original_write = issuer._write
    clock = [time.monotonic()]
    monkeypatch.setattr(recovery.time, 'monotonic', lambda: clock[0])
    def expire() -> None:
        clock[0] = args['deadline']
    def write(parent: int, selected: dict) -> None:
        original_write(parent, selected)
        if stage == 'intent' or stage == 'publication' and selected['outcome'] == 'recovered':
            expire()
    monkeypatch.setattr(issuer, '_write', write)
    if stage == 'ipc':
        client.after = expire
    elif stage == 'cleanup':
        minter.inspect = expire
    result = recovery.cancel_invite(**args)
    assert result.outcome != recovery.RecoveryOutcome.COMPLETE
    assert len(client.calls) == int(stage != 'intent')
    assert len(minter.calls) == int(stage in ('cleanup', 'publication'))
    assert record(tmp_path)['outcome'] == ('recovered' if stage == 'publication' else 'uncertain')


@pytest.mark.parametrize('failure', ['changed-generation', 'unavailable', 'commit-hook', 'invalid-ack', 'journal-drift', 'journal-write'])
def test_partial_effects_and_changed_current_never_settle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str) -> None:
    issuer, _, _, store, _, _, client, minter, args = issued(tmp_path)
    if failure == 'changed-generation':
        args['generation'] -= 1
    elif failure == 'unavailable':
        args['control'] = control.ControlClient(str(tmp_path / 'absent.sock'))
    elif failure == 'commit-hook':
        calls = []
        def disconnect() -> None:
            calls.append(1)
            if len(calls) == 3:  # Initial admission, before IPC, then ready.
                raise control.Refused('disconnected')
        args['before_commit'] = disconnect
    elif failure == 'invalid-ack':
        invoke = client.invalidate_invite
        def invalid(*values: object) -> bool:
            invoke(*values)
            return True
        monkeypatch.setattr(client, 'invalidate_invite', invalid)
    elif failure == 'journal-drift':
        def drift() -> None:
            with issuer._recovery_record() as (parent, selected):
                selected['operation_id'] = 'f' * 32
                issuer._write(parent, selected)
        minter.inspect = drift
    elif failure == 'journal-write':
        write = issuer._write
        def fail(parent: int, selected: dict) -> None:
            if selected['outcome'] == 'recovered':
                raise OSError(CANARY)
            write(parent, selected)
        monkeypatch.setattr(issuer, '_write', fail)
    result = recovery.cancel_invite(**args)
    assert result.outcome != recovery.RecoveryOutcome.COMPLETE
    assert record(tmp_path)['outcome'] == 'uncertain'
    assert CANARY not in repr(result)
    if failure in ('changed-generation', 'unavailable', 'commit-hook', 'invalid-ack'):
        assert not minter.calls
    else:
        assert store.load().invite_record is None and len(minter.calls) == 1
    if failure == 'journal-drift':
        assert record(tmp_path)['operation_id'] == 'f' * 32
        assert result.invite == InvitePhase.UNCERTAIN
        assert result.cleanup == CleanupState.UNCERTAIN


@pytest.mark.parametrize('case', ['clean', 'expire-ignored', 'lost-create', 'late-list', 'late-expire'])
def test_headscale_adapter_proves_absence_with_one_remaining_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    _, original, _, _, _, _, _, _, args = issued(tmp_path)
    existing, admin, records, clock, events = minter_setup()
    admin.keys = (key_record(),)
    ticks = [time.monotonic()]
    deadline = ticks[0] + 2
    args['deadline'] = deadline
    monkeypatch.setattr(recovery.time, 'monotonic', lambda: ticks[0])
    admin.ignore_expire = case == 'expire-ignored'
    if case == 'lost-create':
        admin.fail_at = 1  # Complete ordered list unavailable: a create may still commit.
    original_call = admin.call
    def call(name: str, values: dict) -> None:
        original_call(name, values)
        if case == 'late-list' or case == 'late-expire' and name == 'expire':
            ticks[0] = deadline
        else:
            ticks[0] += .25
    admin.call = call
    minter = recovery.HeadscaleRecoveryMinter(admin=admin, invite_user=existing._user,
                                             now_fn=clock, records=records)
    result = recovery.recover_invite(**(args | {'minter': minter}))
    if case == 'clean':
        assert result.invite == InvitePhase.RECOVERED and result.cleanup == CleanupState.COMPLETE
        assert [name for name, _ in events] == ['list', 'expire', 'list']
        assert [values['timeout_s'] for _, values in events] == [2, 1.75, 1.5]
        assert all(k.expiration <= clock() for k in admin.keys)
    else:
        assert result.outcome == recovery.RecoveryOutcome.PARTIAL_CLEANUP
        assert record(tmp_path)['outcome'] == 'uncertain'
        if case in ('expire-ignored', 'lost-create'):
            assert any(k.expiration > clock() for k in admin.keys)
    assert len(original.calls) == 1
    assert all(name in ('list', 'expire') for name, _ in events)
    assert not records.windows, 'recovery neither reserves nor creates'
