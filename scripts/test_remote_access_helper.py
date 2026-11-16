"""Disposable Gio/private-bus contract tests; no installed service is contacted."""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import ExitStack
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

# Test process only: Gio is an already-required system dependency, not a pip package.
sys.path.insert(0, '/usr/lib/python3/dist-packages')
from gi.repository import Gio, GLib
ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('fixed_remote_access_helper',
    ROOT / 'runtime/remote_access/remote_access_helper.py')
HELPER = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = HELPER
SPEC.loader.exec_module(HELPER)
ControlHandler, Device, Snapshot = HELPER._control.ControlHandler, HELPER.Device, HELPER.Snapshot
BUS_NAME, INTERFACE, OBJECT_PATH = HELPER.BUS_NAME, HELPER.INTERFACE, HELPER.OBJECT_PATH
RemoteAccessHelper = HELPER.RemoteAccessHelper
AUTH_XML = '''<node><interface name="org.freedesktop.PolicyKit1.Authority">
<method name="CheckAuthorization"><arg type="(sa{sv})" direction="in"/><arg type="s" direction="in"/>
<arg type="a{ss}" direction="in"/><arg type="u" direction="in"/><arg type="s" direction="in"/>
<arg type="(bba{ss})" direction="out"/></method>
<method name="CancelCheckAuthorization"><arg type="s" direction="in"/></method>
</interface></node>'''


def eventually(predicate: Any, timeout: Any = 3) -> Any:
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return
        time.sleep(.005)
    assert predicate(), 'boundary did not settle within timeout'


def connect(address: Any) -> Any:
    return Gio.DBusConnection.new_for_address_sync(address,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)


def own(connection: Any, name: Any) -> Any:
    result = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
        'org.freedesktop.DBus', 'RequestName', GLib.Variant('(su)', (name, 4)), None,
        Gio.DBusCallFlags.NONE, 2000, None)
    assert result.unpack() == (1,)


class Authority:
    def __init__(self, connection: Any) -> None:
        self.calls, self.waiting, self.cancels = [], [], []
        self.mode = 'grant'
        self.cancel_fails = False
        self.registration = connection.register_object('/org/freedesktop/PolicyKit1/Authority',
            Gio.DBusNodeInfo.new_for_xml(AUTH_XML).interfaces[0], self.method, None, None)
        own(connection, 'org.freedesktop.PolicyKit1')

    def method(self, connection: Any, sender: Any, path: Any, interface: Any, method: Any, parameters: Any, invocation: Any) -> Any:
        if method == 'CheckAuthorization':
            values = parameters.unpack()
            self.calls.append(values)
            if self.mode == 'delay':
                self.waiting.append((values[-1], invocation))
            elif self.mode == 'cancel':
                invocation.return_dbus_error('org.freedesktop.PolicyKit1.Error.Cancelled', 'cancelled')
            else:
                invocation.return_value(GLib.Variant('((bba{ss}))', ((self.mode == 'grant', False, {}),)))
        else:
            cancellation_id = parameters.unpack()[0]
            self.cancels.append(cancellation_id)
            if self.cancel_fails or not any(ident == cancellation_id for ident, _ in self.waiting):
                invocation.return_dbus_error('org.freedesktop.PolicyKit1.Error.Failed', 'No such cancellation_id')
                return
            for ident, pending in list(self.waiting):
                if ident == cancellation_id:
                    pending.return_dbus_error('org.freedesktop.PolicyKit1.Error.Cancelled', 'cancelled')
                    self.waiting.remove((ident, pending))
            invocation.return_value(GLib.Variant('()', ()))

    def grant(self) -> Any:
        _, invocation = self.waiting.pop(0)
        invocation.return_value(GLib.Variant('((bba{ss}))', ((True, False, {}),)))


class MemoryOperations:
    def __init__(self) -> None:
        self.lock, self.effects = threading.RLock(), []
        self.status_reads = 0
        pending = Device('pending-c', 'stable-c', '<Alice>\nMac', 'blue apple', time.time() + 600)
        self.snapshot = Snapshot(1, os.getuid(), True, 'pending', True, pending,
            (Device('device-a', 'stable-a', 'Alice', 'blue apple', time.time() + 600),
             Device('device-b', 'stable-b', 'Bob', 'green plum', time.time() + 600)))

    def mutation_lock(self) -> Any:
        return self.lock

    def status(self) -> Any:
        self.status_reads += 1
        return self.snapshot

    def approve_pending(self, record: Any, generation: Any) -> Any:
        self.effects.append(('approve', record.record_id, generation))
        self.snapshot = replace(self.snapshot, generation=generation + 1, pending=None,
                                devices=(*self.snapshot.devices, record))

    def reject_pending(self, record: Any, generation: Any) -> Any:
        self.effects.append(('reject', record.record_id, generation))
        self.snapshot = replace(self.snapshot, generation=generation + 1, pending=None)

    def revoke_device(self, record: Any, generation: Any) -> Any:
        self.effects.append(('revoke', record.record_id, generation))
        self.snapshot = replace(self.snapshot, generation=generation + 1,
                                devices=tuple(d for d in self.snapshot.devices if d.record_id != record.record_id))


class RootPeer:
    """Test-side root credential emulation; real non-root refusal is tested separately."""
    def __init__(self, connection: Any) -> None:
        self.connection = connection

    def getsockopt(self, *args: Any) -> Any:
        return struct.pack('3i', os.getpid(), 0, 0)

    def __getattr__(self, name: Any) -> Any:
        return getattr(self.connection, name)


class ControlServer:
    def __init__(self, operations: Any) -> None:
        self.directory = tempfile.TemporaryDirectory(prefix='hr-TASK-16691-', dir='/tmp')
        self.path = self.directory.name + '/control'
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(self.path)
        self.listener.listen()
        self.handler, self.stopped = ControlHandler(operations), False
        self.thread = threading.Thread(target=self.run)
        self.thread.start()

    def run(self) -> Any:
        while True:
            connection, _ = self.listener.accept()
            with connection:
                if self.stopped:
                    return
                self.handler.handle(RootPeer(connection))

    def close(self) -> Any:
        self.stopped = True
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as wake:
            wake.connect(self.path)
        self.thread.join(3)
        assert not self.thread.is_alive()
        self.listener.close()
        self.directory.cleanup()


class Call:
    def __init__(self, connection: Any, method: Any = 'ApprovePending', interface: Any = INTERFACE, body: Any = None, fd: Any = False) -> None:
        self.done = threading.Event()
        self.reply, self.transport_error = None, None
        def run() -> Any:
            message = Gio.DBusMessage.new_method_call(BUS_NAME, OBJECT_PATH, interface, method)
            if body is not None:
                message.set_body(body)
            if fd:
                fds = Gio.UnixFDList.new()
                fds.append(1)
                message.set_unix_fd_list(fds)
            try:
                self.reply = connection.send_message_with_reply_sync(message, Gio.DBusSendMessageFlags.NONE,
                                                                      4000, None)[0]
            except GLib.Error as error:
                self.transport_error = error
            finally:
                self.done.set()
        self.thread = threading.Thread(target=run)
        self.thread.start()

    def finish(self, category: Any = None) -> Any:
        assert self.done.wait(5)
        self.thread.join()
        assert self.transport_error is None, self.transport_error
        if category:
            assert self.reply.get_message_type() == Gio.DBusMessageType.ERROR
            assert self.reply.get_body().unpack() == (category,)
        else:
            assert self.reply.get_message_type() == Gio.DBusMessageType.METHOD_RETURN, self.reply.get_body()
            return json.loads(self.reply.get_body().unpack()[0])


@pytest.fixture
def venue(tmp_path: Any, request: Any) -> Any:
    # Bus config belongs to task scratch; abstract address avoids Unix path limits.
    config = tmp_path / 'bus.conf'
    config.write_text('<busconfig><type>session</type><apparmor mode="disabled"/>'
        '<listen>unix:abstract=hr-16691-' + uuid.uuid4().hex +
        '</listen><auth>EXTERNAL</auth><policy context="default"><allow own="*"/>'
        '<allow send_destination="*"/><allow receive_sender="*"/></policy></busconfig>')
    with ExitStack() as cleanup:
        daemon = subprocess.Popen(['/usr/bin/dbus-daemon', '--config-file=' + str(config),
            '--print-address', '--nofork'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        def stop_daemon() -> Any:
            daemon.terminate()
            daemon.communicate(timeout=3)
        cleanup.callback(stop_daemon)
        address = daemon.stdout.readline().strip()
        assert address.startswith('unix:'), daemon.stderr.read()
        def new() -> Any:
            connection = connect(address)
            cleanup.callback(lambda: None if connection.is_closed() else connection.close_sync(None))
            return connection
        authority = Authority(new())
        loop = GLib.MainLoop()
        thread = threading.Thread(target=loop.run)
        thread.start()
        eventually(loop.is_running)
        def stop_loop() -> Any:
            loop.quit()
            thread.join(3)
            assert not thread.is_alive()
        cleanup.callback(stop_loop)
        operations = MemoryOperations()
        options = getattr(request, 'param', {})
        operations.snapshot = replace(operations.snapshot, release_uid=options.get('release_uid', os.getuid()))
        server = ControlServer(operations)
        cleanup.callback(server.close)
        helper = RemoteAccessHelper(new(), options.get('release_uid', os.getuid()), server.path,
                                    deadline=options.get('deadline', 120))
        cleanup.callback(helper.close)
        yield helper, authority, operations, new(), new


@pytest.mark.parametrize('method,interface,body,fd,category', [
    ('ApprovePending', INTERFACE, GLib.Variant('(s)', ('secret-option',)), False, 'invalid-request'),
    ('ApprovePending', INTERFACE, None, True, 'invalid-request'),
    *[(method, INTERFACE, None, False, 'unknown-method') for method in
      ('EnableOwnServer', 'IssueOwnServerInvite', 'Enable', 'Disable', 'ReplaceFailed',
       'RefreshHomeAddress', 'IssueInvite', 'RetryPolicyRenewal', 'Bogus')],
    ('ApprovePending', 'untrusted.Override', None, False, 'unknown-method'),
    ('ApprovePending', INTERFACE, GLib.Variant('(a{ss})', ({'env': 'SECRET'},)), False, 'invalid-request'),
])
def test_no_input_boundary(venue: Any, method: Any, interface: Any, body: Any, fd: Any, category: Any) -> None:
    _, authority, operations, client, _ = venue
    Call(client, method, interface, body, fd).finish(category)
    assert not authority.calls and not operations.effects
    assert operations.snapshot.generation == 1


@pytest.mark.parametrize('mode', ['deny', 'cancel'])
def test_prompt_without_silent_approval(venue: Any, mode: Any) -> None:
    _, authority, operations, client, _ = venue
    authority.mode = mode
    Call(client).finish('denied')
    subject, action, details, flags, cancellation = authority.calls[0]
    assert subject == ('system-bus-name', {'name': client.get_unique_name()})
    assert action == 'ai.happyranch.remote-access.approve-pending' and flags == 1
    assert '&lt;Alice&gt;Mac' in details['polkit.message']
    assert 'fingerprint' in details['polkit.message'] and 'blue apple' in details['polkit.message']
    assert cancellation and not operations.effects


@pytest.mark.parametrize('change', ['pending', 'generation', 'ownership', 'expiry', 'selected'])
def test_target_capture_survives_dialog(venue: Any, change: Any) -> None:
    _, authority, operations, client, _ = venue
    authority.mode = 'delay'
    method = 'ApprovePending'
    if change == 'selected':
        Call(client, 'NextDevice').finish()
        method = 'RevokeSelected'
    call = Call(client, method)
    eventually(lambda: len(authority.waiting) == 1)
    with operations.lock:
        old = operations.snapshot
        if change == 'pending':
            operations.snapshot = replace(old, pending=replace(old.pending, record_id='swapped'))
        elif change == 'generation':
            operations.snapshot = replace(old, generation=old.generation + 1)
        elif change == 'ownership':
            operations.snapshot = replace(old, owned=False)
        elif change == 'expiry':
            operations.snapshot = replace(old, pending=replace(old.pending, expires_at=time.time() - 1))
        else:
            operations.snapshot = replace(old, devices=(replace(old.devices[0], stable_id='swapped'), old.devices[1]))
    authority.grant()
    call.finish('changed')
    assert not operations.effects


def test_fresh_authorization_and_operations(venue: Any) -> None:
    _, authority, operations, client, _ = venue
    Call(client).finish()
    assert operations.snapshot.pending is None
    assert any(d.record_id == 'pending-c' for d in operations.snapshot.devices)
    with operations.lock:
        operations.snapshot = replace(operations.snapshot, generation=3,
            pending=Device('pending-d', 'stable-d', 'Second Mac', 'red pear', time.time() + 600))
    Call(client).finish()
    assert len(authority.calls) == 2 and authority.calls[0][-1] != authority.calls[1][-1]
    assert authority.calls[0][1] == authority.calls[1][1] == 'ai.happyranch.remote-access.approve-pending'
    with operations.lock:
        operations.snapshot = replace(operations.snapshot, generation=5,
            pending=Device('pending-e', 'stable-e', 'Third Mac', 'gold peach', time.time() + 600))
    Call(client, 'RejectPending').finish()
    Call(client, 'NextDevice').finish()
    Call(client, 'RevokeSelected').finish()
    assert operations.snapshot.pending is None
    assert all(d.record_id != 'device-a' for d in operations.snapshot.devices)
    assert operations.snapshot.generation == 7
    assert [row[0] for row in operations.effects] == ['approve', 'approve', 'reject', 'revoke']
    assert len(authority.calls) == 4 and len({row[-1] for row in authority.calls}) == 4
    assert [row[1] for row in authority.calls] == ['ai.happyranch.remote-access.' + action for action in
                            ('approve-pending', 'approve-pending', 'reject-pending', 'revoke-selected')]


@pytest.mark.parametrize('venue', [{'release_uid': os.getuid() + 1}], indirect=True)
def test_wrong_uid(venue: Any) -> None:
    _, authority, operations, client, _ = venue
    # Constructor UID mismatch; actual bus UID source remains unchanged.
    Call(client).finish('wrong-uid')
    assert not authority.calls and not operations.effects
    assert operations.snapshot.generation == 1


def test_disconnect_and_cursor_cleanup(venue: Any) -> None:
    helper, authority, operations, client, new = venue
    authority.mode = 'delay'
    Call(client, 'NextDevice').finish()
    sender = client.get_unique_name()
    call = Call(client)
    eventually(lambda: len(authority.waiting) == 1)
    client.close_sync(None)
    eventually(lambda: sender not in helper.cursors and bool(authority.cancels))
    eventually(lambda: helper.inflight is None)
    call.thread.join(5)  # Local transport receives disconnected, not a helper response.
    assert call.done.is_set() and call.transport_error is not None
    assert not operations.effects
    assert Call(new(), 'Status').finish()['selected'] is None


def test_one_dialog_globally(venue: Any) -> None:
    _, authority, operations, client, new = venue
    authority.mode = 'delay'
    first = Call(client)
    eventually(lambda: len(authority.waiting) == 1)
    Call(new()).finish('busy')
    assert len(authority.calls) == 1
    authority.grant()
    first.finish()
    assert len(operations.effects) == 1


@pytest.mark.parametrize('venue', [{'deadline': .05}], indirect=True)
def test_deadline_cancels(venue: Any) -> None:
    helper, authority, operations, client, _ = venue
    authority.mode = 'delay'
    began = time.monotonic()
    Call(client).finish('expired')
    assert time.monotonic() - began < .5, 'deadline must not wait for the transport timeout'
    eventually(lambda: bool(authority.cancels) and helper.inflight is None)
    assert authority.cancels == [authority.calls[0][-1]]
    assert not operations.effects


def test_read_only_per_caller_cursor(venue: Any) -> None:
    _, authority, operations, client, new = venue
    other = new()
    assert Call(client, 'Status').finish()['selected'] is None
    assert Call(client, 'NextDevice').finish()['selected']['label'] == 'Alice'
    assert Call(client, 'NextDevice').finish()['selected']['label'] == 'Bob'
    assert Call(other, 'NextDevice').finish()['selected']['label'] == 'Alice'
    assert Call(client, 'Status').finish()['selected']['label'] == 'Bob'
    assert not authority.calls and not operations.effects
    assert operations.snapshot.generation == 1


def test_control_socket_nonroot_before_effect() -> None:
    operations = MemoryOperations()
    left, right = socket.socketpair()
    with left, right:
        worker = threading.Thread(target=ControlHandler(operations).handle, args=(left,))
        worker.start()
        right.sendall(json.dumps({'method': 'approve-pending', 'snapshot': asdict(operations.snapshot),
                                 'record': asdict(operations.snapshot.pending), 'deadline': time.monotonic() + 120}).encode() + b'\n')
        reply = json.loads(right.recv(32768))
        if reply == {'result': 'ready'}:
            right.sendall(b'{"commit":true}\n')
            reply = json.loads(right.recv(32768))
        worker.join(3)
    assert os.getuid() != 0, 'This contract requires an ordinary-user venue'
    assert reply == {'error': 'wrong-uid'} and not operations.effects
    assert operations.status_reads == 0


def test_no_secret_in_logs_or_replies(venue: Any, caplog: Any) -> None:
    _, authority, operations, client, _ = venue
    marker = 'PLANTED-CREDENTIAL-PAIRING-TOKEN'
    caplog.set_level(logging.INFO)
    # Unknown fields and arbitrary internal exception contents are never serialized.
    operations.secret = marker
    status = Call(client, 'Status').finish()
    original = operations.status
    def broken() -> Any:
        raise ValueError(marker)
    operations.status = broken
    Call(client).finish('unavailable')
    operations.status = original
    Call(client, body=GLib.Variant('(s)', (marker,))).finish('invalid-request')
    assert marker not in json.dumps(status) + caplog.text
    assert not operations.effects and not authority.calls


def test_polkit_policy_shape() -> None:
    path = ROOT / 'packaging/linux/polkit/ai.happyranch.remote-access.policy'
    root = ET.parse(path).getroot()
    actions = root.findall('action')
    assert {a.attrib['id'] for a in actions} == {'ai.happyranch.remote-access.' + suffix for suffix in
        ('approve-pending', 'reject-pending', 'revoke-selected')}
    assert len(actions) == 3
    for action in actions:
        assert {element.tag: element.text for element in action.find('defaults')} == {
            'allow_any': 'no', 'allow_inactive': 'no', 'allow_active': 'auth_admin'}
    assert '_keep' not in path.read_text() and '*' not in path.read_text()


def test_disconnect_under_store_lock(venue: Any) -> None:
    helper, authority, operations, client, _ = venue
    authority.mode = 'delay'
    call = Call(client)
    eventually(lambda: bool(authority.waiting))
    entered, release = threading.Event(), threading.Event()
    original = operations.status
    counts = []
    def status() -> Any:
        counts.append(1)
        if len(counts) == 2:  # post-dialog snapshot, then mutation snapshot under store lock
            entered.set()
            assert release.wait(2)
        return original()
    operations.status = status
    authority.grant()
    assert entered.wait(2)
    client.close_sync(None)
    release.set()
    eventually(lambda: helper.inflight is None and call.done.is_set())
    assert not operations.effects and call.transport_error is not None


def test_second_arrival_during_commit_refused(venue: Any) -> None:
    _, authority, operations, client, new = venue
    other = new()
    entered, release = threading.Event(), threading.Event()
    original = operations.approve_pending
    def approve(record: Any, generation: Any) -> Any:
        entered.set()
        assert release.wait(2)
        original(record, generation)
    operations.approve_pending = approve
    Call(other, 'NextDevice').finish()
    first = Call(client)
    assert entered.wait(2)
    second = Call(other, 'RevokeSelected')
    # Flush sends all request bytes; allow Gio's dedicated filter to observe arrival.
    other.flush_sync(None)
    time.sleep(.03)
    release.set()
    first.finish()
    second.finish('busy')
    assert len(authority.calls) == 1 and len(operations.effects) == 1


def test_control_snapshot_changed_after_dialog(venue: Any) -> None:
    _, authority, operations, client, _ = venue
    authority.mode = 'delay'
    call = Call(client)
    eventually(lambda: bool(authority.waiting))
    original = operations.status
    counts = []
    def status() -> Any:
        counts.append(1)
        if len(counts) == 2:  # Race after helper's revalidation, before connector admission.
            operations.snapshot = replace(operations.snapshot, generation=2)
        return original()
    operations.status = status
    authority.grant()
    call.finish('changed')
    assert not operations.effects


def test_system_python_isolated_import() -> None:
    source = ROOT / 'runtime/remote_access/remote_access_helper.py'
    # runpy uses the same source entry without importing runtime.remote_access.
    code = ('import runpy,sys; runpy.run_path(' + repr(str(source)) + '); '
            'assert not any(n.startswith("runtime.remote_access") for n in sys.modules); '
            'assert "pydantic" not in sys.modules')
    result = subprocess.run(['/usr/bin/python3', '-I', '-c', code], capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('venue', [{'deadline': .05}], indirect=True)
@pytest.mark.parametrize('delayed_read', [2, 3, 4])
def test_deadline_under_store_lock(venue: Any, delayed_read: Any) -> None:
    helper, authority, operations, client, _ = venue
    original = operations.status
    counts = []
    def status() -> Any:
        counts.append(1)
        # Capture, post-auth snapshot, connector admission, final locked revalidation.
        if len(counts) == delayed_read:
            time.sleep(.07)
        return original()
    operations.status = status
    Call(client).finish('expired')
    eventually(lambda: helper.inflight is None)
    assert operations.snapshot.generation == 1 and not operations.effects
    assert not authority.cancels, 'terminal authorization must not cancel a removed polkit ID'
    operations.status = original
    assert Call(client).finish() == 'ok'
    assert len(authority.calls) == 2 and len({row[-1] for row in authority.calls}) == 2
    assert operations.effects == [('approve', 'pending-c', 1)]


def test_target_expiry_after_authorization(venue: Any) -> None:
    helper, authority, operations, client, _ = venue
    with operations.lock:
        operations.snapshot = replace(operations.snapshot,
            pending=replace(operations.snapshot.pending, expires_at=time.time() + .05))
    original = operations.status
    counts = []
    def status() -> Any:
        counts.append(1)
        if len(counts) == 2:
            time.sleep(.07)
        return original()
    operations.status = status
    Call(client).finish('expired')
    eventually(lambda: helper.inflight is None)
    assert not operations.effects and operations.snapshot.generation == 1
    assert not authority.cancels
    operations.status = original
    with operations.lock:
        operations.snapshot = replace(operations.snapshot, generation=2,
            pending=replace(operations.snapshot.pending, expires_at=time.time() + 600))
    assert Call(client).finish() == 'ok'
    assert len(authority.calls) == 2 and len({row[-1] for row in authority.calls}) == 2
    assert operations.effects == [('approve', 'pending-c', 2)]


@pytest.mark.parametrize('venue', [{'deadline': .05}], indirect=True)
@pytest.mark.parametrize('late_completion', [False, True])
def test_deadline_uncertain_cancellation(venue: Any, late_completion: Any) -> None:
    helper, authority, operations, client, new = venue
    authority.mode, authority.cancel_fails = 'delay', True
    Call(client).finish('expired')
    eventually(lambda: bool(authority.cancels))
    # Drain the cancellation error via another bus request before checking sealing.
    Call(new()).finish('busy')
    assert len(authority.calls) == 1 and len(authority.waiting) == 1
    assert helper.inflight is not None and helper.cancelled and not operations.effects
    if late_completion:
        authority.grant()
        eventually(lambda: helper.inflight is None)
        assert not operations.effects and operations.snapshot.generation == 1
        authority.mode, authority.cancel_fails = 'grant', False
        assert Call(client).finish() == 'ok'
        assert len(authority.calls) == 2 and len({row[-1] for row in authority.calls}) == 2
        assert operations.effects == [('approve', 'pending-c', 1)]
