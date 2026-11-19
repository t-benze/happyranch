"""Standalone B10 checks on private Unix streams and synthetic anchored state."""
from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
import threading
import tempfile
import time
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.remote_access import helper_control as control

METHODS = ('invite-reserve@1', 'invite-invalidate@1', 'revoke-all@1')
DIGEST = hashlib.sha256(b'synthetic-invite-secret').hexdigest()
NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


class LegacyOperations:
    def __init__(self):
        self.snapshot = control.Snapshot(7, os.getuid(), True, 'ready', True, None, ())
        self.effects = []
        self.lock = threading.RLock()

    def mutation_lock(self):
        return self.lock

    def status(self):
        return self.snapshot

    def approve_pending(self, record, generation):
        raise AssertionError('legacy operation called')

    reject_pending = revoke_device = approve_pending


class InviteOperations(LegacyOperations):
    """Operation spy: protocol assertions observe frames, never manager behavior."""
    def record(self, method, generation, *args):
        self.effects.append((method, generation, *args))
        return generation + 1

    def reserve_invite(self, generation, digest, expires_at, *, deadline):
        return self.record(METHODS[0], generation, digest, expires_at)

    def invalidate_invite(self, generation, *, deadline):
        return self.record(METHODS[1], generation)

    def revoke_all(self, generation, *, deadline):
        return self.record(METHODS[2], generation)


def request(method):
    value = {'method': method, 'expected_generation': 7, 'deadline': time.monotonic() + 60}
    if method == METHODS[0]:
        value.update(digest=DIGEST, expires_at=int(time.time()) + 240)
    return value


def encode(value):
    return json.dumps(value, separators=(',', ':')).encode() + b'\n'


class Peer:
    def __init__(self, connection, frames, uid=0, drop_reply=False):
        self.connection, self.frames, self.uid = connection, frames, uid
        self.drop_reply, self.dropped = drop_reply, False

    def getsockopt(self, *args):
        return struct.pack('3i', os.getpid(), self.uid, self.uid)

    def recvmsg(self, *args):
        assert self.uid == 0, 'nonroot peer reached parsing'
        result = self.connection.recvmsg(*args)
        self.frames.append(result[0])
        return result

    def sendall(self, raw):
        self.frames.append(raw)
        if self.dropped or self.drop_reply and b'"result":"ok"' in raw:
            self.dropped = True
            raise OSError('synthetic reply loss')
        return self.connection.sendall(raw)

    def __getattr__(self, name):
        return getattr(self.connection, name)


@contextmanager
def exchange(operations, *, uid=0, initial=None):
    left, right = socket.socketpair()
    frames = []
    if initial is not None:
        right.sendall(initial)
    def serve():
        with left:
            control.ControlHandler(operations).handle(Peer(left, frames, uid))
    thread = threading.Thread(target=serve)
    thread.start()
    right.settimeout(3)
    try:
        yield right, frames
    finally:
        right.close()
        thread.join(3)
        assert not thread.is_alive()


@pytest.mark.parametrize('method', METHODS)
def test_wire_commit_and_secret_free_frames(method):
    operations = InviteOperations()
    with exchange(operations) as (connection, frames):
        value = request(method)
        connection.sendall(encode(value))
        assert control._receive(connection) == {'result': 'ready'}
        assert not operations.effects
        connection.sendall(b'{"commit":true}\n')
        assert control._receive(connection) == {'result': 'ok', 'generation': 8}
    expected = [(method, 7, DIGEST, value['expires_at'])] if method == METHODS[0] else [(method, 7)]
    assert operations.effects == expected
    raw = b''.join(frames)
    for secret in (b'synthetic-invite-secret', b'synthetic-bearer', b'hrinvite2.synthetic', b'hrpin_'):
        assert secret not in raw
    assert frames == [encode(value), b'{"result":"ready"}\n',
                      b'{"commit":true}\n', b'{"result":"ok","generation":8}\n']
    assert all(len(frame) <= control.MAX_FRAME for frame in frames)


@pytest.mark.parametrize('method', METHODS)
@pytest.mark.parametrize('case', ['nonroot', 'oversize', 'unknown', 'extra', 'duplicate', 'generation',
                                 'boolean-generation', 'expired', 'future', 'nan', 'absent-api'])
def test_wire_refuses_before_mutation(method, case):
    operations = LegacyOperations() if case == 'absent-api' else InviteOperations()
    value = request(method)
    category = 'invalid-request'
    if case == 'unknown':
        value['method'] += '-unknown'
    elif case == 'extra':
        value['invite_text'] = 'synthetic-forbidden'
    elif case == 'generation':
        value['expected_generation'] -= 1
        category = 'changed'
    elif case == 'boolean-generation':
        value['expected_generation'] = True
    elif case == 'expired':
        value['deadline'] = time.monotonic() - 1
        category = 'expired'
    elif case == 'future':
        value['deadline'] = time.monotonic() + 121
    elif case == 'nan':
        value['deadline'] = float('nan')
    elif case == 'absent-api':
        category = 'unavailable'
    elif case == 'nonroot':
        category = 'wrong-uid'
    raw = encode(value)
    if case == 'duplicate':
        raw = raw[:-2] + b',"expected_generation":7}\n'
    elif case in ('oversize', 'nonroot'):
        raw = b'x' * 32769
    with exchange(operations, uid=12345 if case == 'nonroot' else 0,
                  initial=raw if case == 'nonroot' else None) as (connection, _):
        if case != 'nonroot':
            connection.sendall(raw)
        assert control._receive(connection) == {'error': category}
    assert not operations.effects


@pytest.mark.parametrize('digest', ['', 'a' * 63, 'a' * 65, 'A' * 64, 'g' * 64, 123, None, 'a' * 63 + '\n'])
def test_wire_digest_format(digest):
    operations = InviteOperations()
    value = request(METHODS[0])
    value['digest'] = digest
    with exchange(operations) as (connection, _):
        connection.sendall(encode(value))
        assert control._receive(connection) == {'error': 'invalid-request'}
    assert not operations.effects


@pytest.mark.parametrize('expiry,category', [(-1, 'expired'), (301, 'invalid-request'),
                                           (True, 'invalid-request'), (None, 'invalid-request')])
def test_wire_invite_expiry(expiry, category):
    operations = InviteOperations()
    value = request(METHODS[0])
    value['expires_at'] = expiry if expiry is None or type(expiry) is bool else int(time.time()) + expiry
    with exchange(operations) as (connection, _):
        connection.sendall(encode(value))
        assert control._receive(connection) == {'error': category}
    assert not operations.effects


@pytest.mark.parametrize('method', METHODS)
@pytest.mark.parametrize('case', ['no-commit', 'bad-commit', 'extra-commit', 'changed', 'expired', 'unowned'])
def test_wire_revalidates_through_commit(method, case):
    operations = InviteOperations()
    value = request(method)
    if case == 'unowned':
        operations.snapshot = replace(operations.snapshot, owned=False)
    with exchange(operations) as (connection, _):
        connection.sendall(encode(value))
        if case == 'unowned':
            assert control._receive(connection) == {'error': 'changed'}
        else:
            assert control._receive(connection) == {'result': 'ready'}
            if case == 'no-commit':
                connection.shutdown(socket.SHUT_WR)
            else:
                if case == 'changed':
                    operations.snapshot = replace(operations.snapshot, generation=8)
                if case == 'expired':
                    # Revalidate the original monotonic deadline after root's hook.
                    original = time.monotonic
                    with pytest.MonkeyPatch.context() as patch:
                        patch.setattr(control.time, 'monotonic', lambda: original() + 121)
                        connection.sendall(b'{"commit":true}\n')
                        assert control._receive(connection) == {'error': 'expired'}
                    assert not operations.effects
                    return
                connection.sendall(b'{"commit":false}\n' if case == 'bad-commit' else
                                   b'{"commit":true,"extra":1}\n' if case == 'extra-commit' else b'{"commit":true}\n')
            assert control._receive(connection) == {'error': 'changed' if case == 'changed' else 'invalid-request'}
    assert not operations.effects


@contextmanager
def server(tmp_path, operations, *, drop_reply=False):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    directory = tempfile.TemporaryDirectory(prefix='hr-b10-', dir='/tmp')
    path = directory.name + '/control.sock'
    listener.bind(path)
    listener.listen(1)
    frames = []
    def serve():
        connection, _ = listener.accept()
        with connection:
            control.ControlHandler(operations).handle(Peer(connection, frames, drop_reply=drop_reply))
    thread = threading.Thread(target=serve)
    thread.start()
    try:
        yield control.ControlClient(path), frames
    finally:
        listener.close()
        thread.join(3)
        assert not thread.is_alive()
        directory.cleanup()


def call(client, method, hook):
    if method == METHODS[0]:
        return client.reserve_invite(7, DIGEST, int(time.time()) + 240, time.monotonic() + 60, hook)
    return getattr(client, 'invalidate_invite' if method == METHODS[1] else 'revoke_all')(
        7, time.monotonic() + 60, hook)


@pytest.mark.parametrize('method', METHODS)
@pytest.mark.parametrize('case', ['ok', 'lost-reply', 'hook-refused'])
def test_client_handshake_and_transport_loss(tmp_path, method, case):
    operations = InviteOperations()
    hooks = []
    def hook():
        assert not operations.effects
        hooks.append(True)
        if case == 'hook-refused':
            raise control.Refused('disconnected')
    with server(tmp_path, operations, drop_reply=case == 'lost-reply') as (client, _):
        if case == 'ok':
            assert call(client, method, hook) == 8
        else:
            with pytest.raises(control.Refused, match='disconnected' if case == 'hook-refused' else 'unavailable'):
                call(client, method, hook)
    assert hooks == [True]
    assert bool(operations.effects) is (case != 'hook-refused')


@pytest.fixture
def owner(tmp_path):
    from runtime.remote_access.authorization import TrustState
    from runtime.remote_access.identity import ConnectorIdentity
    from runtime.remote_access.invite_control import InviteControl
    from runtime.remote_access.pairing import PinnedPairingManager
    from runtime.remote_access.state_store import AtomicFileTrustStateStore
    from runtime.remote_access.streams import StreamRegistry
    store = AtomicFileTrustStateStore(tmp_path / 'trust.json', TrustState(ConnectorIdentity(tenant_id='tenant', home_id='home', connector_id='connector'), 0, 0))
    registry = StreamRegistry()
    manager = PinnedPairingManager(state_store=store, identity=store.load().connector_identity,
                                   registry=registry, now_fn=lambda: NOW)
    return InviteControl(manager), manager, store, registry


def test_adapter_reserves_digest_and_deadline(owner):
    adapter, _, store, _ = owner
    _, generation = store.snapshot()
    expiry = NOW + timedelta(minutes=4)
    result = adapter.reserve_invite(generation, DIGEST, int(expiry.timestamp()))
    state, committed = store.snapshot()
    assert result == committed == generation + 1
    assert state.invite_record is not None
    assert state.invite_record.secret_digest == DIGEST
    assert state.invite_record.expires_at == expiry
    assert state.invite_record.generation == committed
    assert b'synthetic-invite-secret' not in Path(store._path).read_bytes()


@pytest.mark.parametrize('phase', ['invite', 'pending', 'delivery'])
def test_adapter_invalidation_burns_current_ceremony(owner, phase):
    adapter, manager, store, _ = owner
    manager.issue_invite_record(DIGEST, NOW + timedelta(minutes=4))
    pending = None
    device = None
    if phase != 'invite':
        pending = manager.redeem('synthetic-invite-secret', 'node-a', 'control', NOW)
    if phase == 'delivery':
        device = manager.approve_pending(pending.generation, ('control', 'node-a'))
    _, generation = store.snapshot()
    assert adapter.invalidate_invite(generation) == generation + 1
    state, _ = store.snapshot()
    assert state.invite_record is state.pending_approval is state.delivery is None
    assert manager.redeem('synthetic-invite-secret', 'node-a', 'control', NOW) is None
    if pending:
        assert manager.complete(pending.completion_handle, 'node-a', 'control', NOW) is None
    if device:
        assert state.devices[device].revoked


@pytest.mark.parametrize('method', METHODS)
def test_adapter_generation_cas(owner, method):
    adapter, manager, store, _ = owner
    manager.issue_invite_record(DIGEST, NOW + timedelta(minutes=4))
    _, generation = store.snapshot()
    before = Path(store._path).read_bytes()
    with pytest.raises(control.Refused, match='changed'):
        if method == METHODS[0]:
            adapter.reserve_invite(generation - 1, 'b' * 64, int((NOW + timedelta(minutes=4)).timestamp()))
        else:
            getattr(adapter, 'invalidate_invite' if method == METHODS[1] else 'revoke_all')(generation - 1)
    assert Path(store._path).read_bytes() == before


@pytest.mark.parametrize('fails', [False, True])
def test_adapter_revoke_all_denies_and_closes_every_device(owner, fails):
    from runtime.remote_access.streams import StreamClosed
    adapter, manager, store, registry = owner
    grants, closed = [], []
    for node in ('node-a', 'node-b'):
        manager.issue_invite_record(DIGEST, NOW + timedelta(minutes=4))
        pending = manager.redeem('synthetic-invite-secret', node, 'control', NOW)
        device = manager.approve_pending(pending.generation, ('control', node))
        credential = manager.complete(pending.completion_handle, node, 'control', NOW)
        grants.append((device, credential, node))
        class Handle:
            def __init__(self, device, credential, node):
                self.device, self.credential, self.node = device, credential, node
            def close(self):
                assert not manager.admit(self.credential, self.node, 'control', NOW).ok
                # A second store can take its flock inside a transport callback.
                assert store.snapshot()[0].devices[self.device].revoked
                closed.append(self.device)
                if fails and self.node == 'node-a':
                    raise OSError('synthetic close failure')
        registry.open(node, Handle(device, credential, node), device_id=device,
                      epoch=store.load().devices[device].authorization_epoch)
    manager.issue_invite_record(DIGEST, NOW + timedelta(minutes=4))
    _, generation = store.snapshot()
    if fails:
        with pytest.raises(control.Refused, match='unavailable'):
            adapter.revoke_all(generation)
    else:
        assert adapter.revoke_all(generation) == store.snapshot()[1]
    state, _ = store.snapshot()
    assert set(closed) == {d for d, _, _ in grants}
    assert state.invite_record is None and all(d.revoked for d in state.devices.values())
    for device, credential, node in grants:
        assert not manager.admit(credential, node, 'control', NOW).ok
        with pytest.raises(StreamClosed):
            registry.open('late-' + node, Handle(device, credential, node), device_id=device,
                          epoch=state.devices[device].authorization_epoch)


@pytest.mark.parametrize('method', METHODS)
def test_kernel_nonroot_refused_without_parsing(method):
    if os.getuid() == 0:
        pytest.skip('kernel nonroot peer requires a nonroot test process')
    operations = InviteOperations()
    left, right = socket.socketpair()
    try:
        right.sendall(encode(request(method))[:-1] + b'broken-json\n')
        control.ControlHandler(operations).handle(left)
        assert control._receive(right) == {'error': 'wrong-uid'}
    finally:
        left.close()
        right.close()
    assert not operations.effects


def test_client_rejects_credential_like_digest_before_ipc(tmp_path):
    # A missing endpoint would yield unavailable if validation were too late.
    client = control.ControlClient(str(tmp_path / 'absent'))
    for digest in ('hrinvite2.synthetic', 'synthetic-bearer', 'synthetic-key', 'A' * 64):
        with pytest.raises(control.Refused, match='invalid-request'):
            client.reserve_invite(7, digest, int(time.time()) + 240, time.monotonic() + 60, lambda: None)


@pytest.mark.parametrize('method', METHODS)
def test_wire_checks_deadline_after_final_status(method, monkeypatch):
    operations = InviteOperations()
    clock = [time.monotonic()]
    monkeypatch.setattr(control.time, 'monotonic', lambda: clock[0])
    with exchange(operations) as (connection, _):
        connection.sendall(encode(request(method)))
        assert control._receive(connection) == {'result': 'ready'}
        def blocking_status():
            clock[0] += 121
            return operations.snapshot
        operations.status = blocking_status
        connection.sendall(b'{"commit":true}\n')
        assert control._receive(connection) == {'error': 'expired'}
    assert not operations.effects


@pytest.mark.parametrize('method,bound', [(method, 'deadline') for method in METHODS] +
                         [(METHODS[0], 'invite-expiry')])
def test_wire_deadline_expires_waiting_for_trust_writer(owner, method, bound):
    """A coherent shared reader changes no generation but delays mutation."""
    from runtime.remote_access.state_store import AtomicFileTrustStateStore
    adapter, manager, store, _ = owner
    # This wire test uses real UTC, like the reviewer probe, rather than NOW.
    clock = [datetime.now(timezone.utc)]
    manager._now_fn = lambda: clock[0]
    manager.issue_invite_record(DIGEST, datetime.now(timezone.utc) + timedelta(minutes=4))
    _, generation = store.snapshot()
    before = Path(store._path).read_bytes()
    reader = AtomicFileTrustStateStore(store._path, store.load())
    held = threading.Event()
    def hold_reader():
        with reader._lock_file(shared=True):
            held.set()
            time.sleep(.4)
            if bound == 'invite-expiry':
                clock[0] += timedelta(minutes=5)
    holder = threading.Thread(target=hold_reader)
    class Operations:
        reads = 0
        def mutation_lock(self):
            return manager.mutation_lock()
        def status(self):
            _, current = store.snapshot()
            self.reads += 1
            if self.reads == 2:
                holder.start()
                assert held.wait(1)
            return control.Snapshot(current, os.getuid(), True, 'ready', True, None, ())
        def reserve_invite(self, *args, **kwargs):
            return adapter.reserve_invite(*args, **kwargs)
        def invalidate_invite(self, *args, **kwargs):
            return adapter.invalidate_invite(*args, **kwargs)
        def revoke_all(self, *args, **kwargs):
            return adapter.revoke_all(*args, **kwargs)
    with exchange(Operations()) as (connection, _):
        value = request(method)
        value.update(expected_generation=generation,
                     deadline=time.monotonic() + (.25 if bound == 'deadline' else 60))
        connection.sendall(encode(value))
        assert control._receive(connection) == {'result': 'ready'}
        connection.sendall(b'{"commit":true}\n')
        reply = control._receive(connection)
    holder.join(2)
    assert not holder.is_alive()
    assert reply == {'error': 'expired'}
    assert store.snapshot()[1] == generation
    assert Path(store._path).read_bytes() == before


@pytest.mark.parametrize('fails', [False, True])
def test_adapter_revoke_all_closes_removed_owner_before_success(owner, fails):
    from runtime.remote_access.pairing import PairingManager
    from runtime.remote_access.state_store import AtomicFileTrustStateStore
    from runtime.remote_access.streams import StreamClosed, StreamRegistry
    adapter, manager, store, registry = owner
    grants, streams, closed = [], [], []
    for node in ('node-a', 'node-b'):
        manager.issue_invite_record(DIGEST, NOW + timedelta(minutes=4))
        pending = manager.redeem('synthetic-invite-secret', node, 'control', NOW)
        device = manager.approve_pending(pending.generation, ('control', node))
        credential = manager.complete(pending.completion_handle, node, 'control', NOW)
        grants.append((device, credential, node))
        class Handle:
            def __init__(self, device, node):
                self.device, self.node = device, node
            def close(self):
                # A distinct store must acquire SH here: no EX across callbacks.
                snapshot = AtomicFileTrustStateStore(store._path, store.load()).load()
                assert self.device not in snapshot.devices or snapshot.devices[self.device].revoked
                closed.append(self.device)
                if fails and self.node == 'node-a':
                    raise OSError('synthetic removed-owner close failure')
            def receive(self):
                return b'synthetic-live-frame'
        streams.append(registry.open(node, Handle(device, node), device_id=device,
                                     epoch=store.load().devices[device].authorization_epoch))
    external = PairingManager(state_store=AtomicFileTrustStateStore(store._path, store.load()),
                              identity=store.load().connector_identity, registry=StreamRegistry())
    removed, credential, node = grants[0]
    old_epoch = store.load().devices[removed].authorization_epoch
    external.remove_device(removed)
    assert removed not in store.load().devices
    assert streams[0].receive() == b'synthetic-live-frame'
    _, generation = store.snapshot()
    if fails:
        with pytest.raises(control.Refused, match='unavailable'):
            adapter.revoke_all(generation)
    else:
        assert adapter.revoke_all(generation) == store.snapshot()[1]
    assert set(closed) == {device for device, _, _ in grants}
    assert all(stream.closed for stream in streams)
    for stream in streams:
        with pytest.raises(StreamClosed):
            stream.receive()
    assert not manager.admit(credential, node, 'control', NOW).ok
    assert all(device.revoked for device in store.load().devices.values())
    # Removed-owner fencing also rejects an admission delayed until after cleanup.
    with pytest.raises(StreamClosed):
        registry.open('late-removed', Handle(removed, node), device_id=removed, epoch=old_epoch)


def test_wire_bulk_expiry_preserves_committed_cleanup_and_stops_later_write(owner):
    from runtime.remote_access.state_store import AtomicFileTrustStateStore
    from runtime.remote_access.streams import StreamClosed
    adapter, manager, store, registry = owner
    for node in ('node-a', 'node-b'):
        manager.issue_invite_record(DIGEST, NOW + timedelta(minutes=4))
        pending = manager.redeem('synthetic-invite-secret', node, 'control', NOW)
        manager.approve_pending(pending.generation, ('control', node))
        assert manager.complete(pending.completion_handle, node, 'control', NOW)
    state, generation = store.snapshot()
    first, second = tuple(state.devices)
    held = threading.Event()
    reader = AtomicFileTrustStateStore(store._path, state)
    def hold_reader():
        with reader._lock_file(shared=True):
            held.set()
            time.sleep(.4)
    holder = threading.Thread(target=hold_reader)
    closed = []
    class Handle:
        def __init__(self, name):
            self.name = name
        def close(self):
            assert store.load().devices[first].revoked
            if self.name == 'first-a':
                holder.start()
                assert held.wait(1)
                # Expire during committed cleanup. Its remaining handle must
                # still close; the later device's EX waits on this real reader.
                time.sleep(.3)
            closed.append(self.name)
        def receive(self):
            return b'synthetic-live-frame'
    streams = [registry.open(name, Handle(name), device_id=device,
                             epoch=state.devices[device].authorization_epoch)
               for name, device in [('first-a', first), ('first-b', first), ('second', second)]]
    class Operations:
        def mutation_lock(self):
            return manager.mutation_lock()
        def status(self):
            return control.Snapshot(store.snapshot()[1], os.getuid(), True, 'ready', True, None, ())
        def reserve_invite(self, *args, **kwargs):
            return adapter.reserve_invite(*args, **kwargs)
        def invalidate_invite(self, *args, **kwargs):
            return adapter.invalidate_invite(*args, **kwargs)
        def revoke_all(self, *args, **kwargs):
            return adapter.revoke_all(*args, **kwargs)
    with exchange(Operations()) as (connection, _):
        value = request(METHODS[2])
        value.update(expected_generation=generation, deadline=time.monotonic() + .25)
        connection.sendall(encode(value))
        assert control._receive(connection) == {'result': 'ready'}
        connection.sendall(b'{"commit":true}\n')
        reply = control._receive(connection)
    holder.join(2)
    assert not holder.is_alive()
    assert reply == {'error': 'expired'}
    state, committed = store.snapshot()
    assert committed == generation + 2  # ceremony burn and one durable deny
    assert state.devices[first].revoked and not state.devices[second].revoked
    assert closed == ['first-a', 'first-b']
    for stream in streams[:2]:
        with pytest.raises(StreamClosed):
            stream.receive()
    assert streams[2].receive() == b'synthetic-live-frame'


@pytest.mark.parametrize('method', ['reserve', 'invalidate'])
def test_serving_facade_anchored_cas_and_owner(monkeypatch, method):
    from scripts.test_remote_access_helper import bound_control
    from runtime.remote_access.helper_invite import PairingProxy
    with bound_control(monkeypatch) as (client, supervisor, _):
        manager = supervisor._pinned_pairing_manager
        before = client.status().generation
        proxy = PairingProxy(client, before, time.monotonic() + 30, lambda: None)
        expiry = datetime.now(timezone.utc) + timedelta(seconds=120)
        assert proxy.issue_invite_record(DIGEST, expiry) == client.status().generation > before
        assert supervisor.state_store.load().invite_record.secret_digest == DIGEST
        assert supervisor._pinned_pairing_manager is manager
        original = manager.issue_invite_record if method == 'reserve' else manager.invalidate_invite
        def racing(*args, **kwargs):
            supervisor.state_store.save(supervisor.state_store.load())
            return original(*args, **kwargs)
        monkeypatch.setattr(manager, 'issue_invite_record' if method == 'reserve' else 'invalidate_invite', racing)
        with pytest.raises(control.Refused, match='changed'):
            if method == 'reserve':
                proxy.issue_invite_record('b' * 64, expiry)
            else:
                proxy.invalidate_invite(proxy.generation, time.monotonic() + 30, lambda: None)
        assert supervisor.state_store.load().invite_record.secret_digest == DIGEST
        assert proxy.generation < client.status().generation


@pytest.mark.parametrize('phase', ['capture', 'ready', 'generation'])
def test_serving_invite_target_binding(monkeypatch, phase):
    from runtime.remote_access.supervisor import _HelperOperations
    from scripts.test_remote_access_helper import bound_control
    with bound_control(monkeypatch) as (client, supervisor, _):
        generation = client.status().generation
        expected = {'home_id': 'home', 'control_plane_id': 'protected-control', 'connector_id': 'connector'}
        assert client.invite_binding(generation) == expected
        before = supervisor.state_store.snapshot()
        if phase == 'generation':
            with pytest.raises(control.Refused, match='changed'):
                client.invite_binding(generation + 1)
        else:
            def change():
                if phase == 'ready':
                    monkeypatch.setattr(_HelperOperations, 'invite_binding', lambda self: {**expected, 'home_id': 'wrong'})
            with pytest.raises(control.Refused, match='changed'):
                client.reserve_invite(generation, DIGEST, int(time.time()) + 120, time.monotonic() + 30,
                    change, target={**expected, 'home_id': 'wrong'} if phase == 'capture' else expected)
        assert supervisor.state_store.snapshot() == before
