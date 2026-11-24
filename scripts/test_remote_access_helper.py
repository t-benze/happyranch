"""Disposable Gio/private-bus contract tests; no installed service is contacted."""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import pwd
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from contextlib import ExitStack, contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock
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
def venue(tmp_path: Any, request: Any, monkeypatch: Any) -> Any:
    # Exercise the real owner resolver against an isolated passwd home. Only
    # the external protected-record read is isolated as clean-host absence.
    home = tmp_path / 'owner'
    (home / '.local/share/happyranch').mkdir(parents=True, mode=0o700)
    row = pwd.struct_passwd(('owner', 'x', os.getuid(), os.getgid(), '', str(home), '/bin/sh'))
    monkeypatch.setattr(HELPER, 'bootstrap_owner', partial(HELPER._owner.bootstrap_owner,
        passwd=SimpleNamespace(getpwall=lambda: [row])))
    def absent_record(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError
    monkeypatch.setattr(HELPER._owner._verifier, '_read', absent_record)
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
        helper = RemoteAccessHelper(new(), None if options.get('bootstrap') else options.get('release_uid', os.getuid()), server.path,
                                    deadline=options.get('deadline', 120))
        helper.audit = HELPER._home_audit.HomeAudit(tmp_path / 'home-audit.jsonl',
                                                   uid=os.getuid(), gid=os.getgid())
        cleanup.callback(helper.close)
        yield helper, authority, operations, new(), new


@pytest.mark.parametrize('method,interface,body,fd,category', [
    ('ApprovePending', INTERFACE, GLib.Variant('(s)', ('secret-option',)), False, 'invalid-request'),
    ('ApprovePending', INTERFACE, None, True, 'invalid-request'),
    *[(method, INTERFACE, GLib.Variant('(s)', ('extra',)), False, 'invalid-request') for method in
      ('Enable', 'Disable', 'ReplaceFailed', 'IssueInvite', 'CancelInvite', 'RecoverInvite', 'RetryPolicyRenewal')],
    *[(method, INTERFACE, None, False, 'unknown-method') for method in
      ('EnableOwnServer', 'IssueOwnServerInvite', 'RefreshHomeAddress', 'Bogus')],
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


@pytest.mark.parametrize('venue', [{}, {'bootstrap': True}], indirect=True)
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
        ('approve-pending', 'reject-pending', 'revoke-selected', 'enable', 'replace-failed', 'disable',
         'cancel-invite', 'recover-invite', 'issue-invite', 'retry-policy-renewal')}
    assert len(actions) == 10
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


PYTHON_FLOOR_SMOKE = r'''
import enum, importlib, importlib.util, json, os, pathlib, runpy, sys, tempfile, types
root = pathlib.Path(sys.argv[1])
strenum = getattr(enum, 'StrEnum', None)
if sys.argv[2] == 'without-strenum' and hasattr(enum, 'StrEnum'):
    del enum.StrEnum
source = root / 'runtime/remote_access'
for name in ('status_v2', 'home_audit', 'legacy_home_readback', 'helper_generation',
             'helper_lifecycle', 'remote_access_helper', 'helper_control',
             'helper_owner', 'release_verifier'):
    path = source / (name + '.py')
    compile(path.read_bytes(), str(path), 'exec')
def load(name):
    spec = importlib.util.spec_from_file_location('floor_' + name, source / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
modules = {name: load(name) for name in (
    'status_v2', 'home_audit', 'helper_generation', 'helper_control',
    'helper_owner', 'release_verifier')}
# Host Gio is a system dependency. A test-only 3.10 interpreter may lack its ABI.
sys.path.insert(0, '/usr/lib/python3/dist-packages')
try:
    from gi.repository import Gio, GLib
except ImportError:
    print('Gio unavailable: helper entry NOT RUN; lifecycle pure paths only')
    helper = types.ModuleType('runtime.remote_access.remote_access_helper')
    helper.AUTH_DEADLINE = 120.0
    sys.modules[helper.__name__] = helper
else:
    runpy.run_path(str(source / 'remote_access_helper.py'))
    assert not any(n.startswith('runtime.remote_access') for n in sys.modules)
    assert 'pydantic' not in sys.modules
    print('isolated helper entry PASS (Gio available; no bus registration)')
# Only the bundled-child modules need the connector package and its dependencies.
# Host stdlib (e.g. Python 3.14's http) may itself require StrEnum; do not emulate it.
if strenum is not None:
    enum.StrEnum = strenum
sys.path.insert(0, str(root))
activation = importlib.import_module('runtime.remote_access.legacy_home_readback')
if sys.argv[2] == 'without-strenum' and strenum is not None:
    del enum.StrEnum
lifecycle = importlib.import_module('runtime.remote_access.helper_lifecycle')
if strenum is not None:
    enum.StrEnum = strenum
assert activation.LegacyHomeReadback is not None
assert lifecycle.OperationBudget.status().remaining() > 0
v2, audit = modules['status_v2'], modules['home_audit']
vocabularies = [
    (v2.SetupPhase, 'unknown stale ready off in_progress failed uncertain partial_cleanup'),
    (v2.RuntimeAvailability, 'unknown stale available unavailable'),
    (v2.InvitePhase, 'unknown stale none minting encoding pairing issued pending_approval consumed cancelled recovered expired refused uncertain'),
    (v2.AddressState, 'unknown stale current changed refreshing identity_changed failed uncertain'),
    (v2.PolicyState, 'unknown stale current renewing expired renewal_failed uncertain'),
    (v2.CleanupState, 'unknown stale not_needed in_progress complete pending partial_cleanup failed uncertain'),
    (v2.ConnectorState, 'unknown stale running stopped unreachable'),
    (audit.Event, 'activation invite_issued invite_cancelled invite_recovered invite_expired device_approved device_rejected device_revoked refresh policy_renew policy_fail turn_off uncertain'),
    (audit.Phase, 'prepared activating joined ready failed_uncertain'),
    (audit.Component, 'sidecar connector headscale units live_state credentials accounts'),
    (audit.Outcome, 'succeeded failed refused absent retained partial_cleanup uncertain'),
    (audit.Stage, 'intent outcome uncertain'),
    (lifecycle.CallReply, 'accepted refused'),
]
for kind, words in vocabularies:
    values = words.split()
    assert [member.value for member in kind] == values
    assert json.dumps(list(kind)) == json.dumps(values)
    for member, value in zip(kind, values):
        assert member == value and hash(member) == hash(value)
        assert str(member) == format(member) == f'{member}' == value
        assert format(member, '>24') == format(value, '>24')
expected = (b'{"address":{"age_seconds":null,"state":"unknown"},'
    b'"cleanup":{"age_seconds":null,"state":"unknown"},'
    b'"invite":{"age_seconds":null,"deadline_utc_seconds":null,"state":"unknown"},'
    b'"policy":{"age_seconds":null,"state":"unknown"},'
    b'"runtime":{"age_seconds":null,"state":"unknown"},'
    b'"setup":{"age_seconds":null,"state":"unknown"},"version":2}\n')
assert v2.encode(v2.build_status(now_utc_seconds=100)) == expected
assert v2.encode(v2.decode(expected)) == expected
expected = (b'{"component":null,"event":"device_approved","intent_id":"intent-1",'
    b'"operation_id":"op-1","outcome":"succeeded","phase":null,"stage":"outcome",'
    b'"subject_id":null,"timestamp_ns":100,"version":1}\n')
record = audit.Record(audit.Event.DEVICE_APPROVED, audit.Stage.OUTCOME,
    'intent-1', 'op-1', 100, outcome=audit.Outcome.SUCCEEDED)
assert audit.encode(record) == expected
with tempfile.TemporaryDirectory() as parent:
    path = pathlib.Path(parent) / 'audit.jsonl'
    audit.HomeAudit(path, uid=os.getuid(), gid=os.getgid()).append(record)
    assert path.read_bytes() == expected
    assert json.loads(path.read_bytes())['outcome'] == 'succeeded'
print('all sibling imports, enum text/format/JSON/comparison, StatusV2 and audit bytes PASS')
'''


def test_python310_stdlib_and_wire_contract() -> None:
    # Version-independent regression; real floor/bundled runs use this same code.
    result = subprocess.run([sys.executable, '-I', '-c', PYTHON_FLOOR_SMOKE,
                             str(ROOT), 'without-strenum'],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


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


@contextmanager
def bound_control(monkeypatch: Any) -> Any:
    from runtime.remote_access import helper_control as control
    from scripts.test_managed_admission import running
    with running(monkeypatch, helper=True) as (path, supervisor):
        assert Path(control.CONTROL_SOCKET).is_socket(), 'supervisor must bind the private control listener'
        listener = supervisor._control_listener
        # Only emulate the unavailable root credential. The accepted stream,
        # framing, adapter, manager, anchored store and registry are real.
        original = listener.handler.handle
        monkeypatch.setattr(listener.handler, 'handle', lambda connection: original(RootPeer(connection)))
        yield control.ControlClient(control.CONTROL_SOCKET), supervisor, path


def pending_real(supervisor: Any, node: str = 'node-a') -> Any:
    import hashlib
    from datetime import datetime, timedelta, timezone
    manager = supervisor._pinned_pairing_manager
    now = datetime.now(timezone.utc)
    manager.issue_invite_record(hashlib.sha256(b'synthetic-bound-invite').hexdigest(), now + timedelta(minutes=4))
    pending = manager.redeem('synthetic-bound-invite', node, 'protected-control', now)
    assert pending is not None
    return pending


@pytest.mark.parametrize('node', ['node-a', 'n' * 200])
def test_bound_legacy_label_fallback(monkeypatch: Any, node: str) -> None:
    from datetime import datetime, timezone
    with bound_control(monkeypatch) as (client, supervisor, path):
        pending = pending_real(supervisor, node)
        assert client.status().pending.label == node[:96]
        mutate_real(client, 'ApprovePending')
        assert client.status().devices[0].label == node[:96]
        assert supervisor._pinned_pairing_manager.complete(
            pending.completion_handle, node, 'protected-control', datetime.now(timezone.utc))


@pytest.mark.parametrize('name,expected', [
    ('  Alice\x00\r\n\t\x7f\x85电脑\u202e\u2066\u2069\u200b\u200d\ufeff  ', 'Alice电脑'),
    ('x' * 200, 'x' * 96),
    ('电脑' * 120, '电脑' * 48),
    ('\x00\u202e\u2066\u200b  ', 'node-a'),
    ('', 'node-a'),
    ('<My & computer>', '<My & computer>'),
])
def test_bound_os_label_ceremony(monkeypatch: Any, name: str, expected: str) -> None:
    import hashlib
    from datetime import datetime, timedelta, timezone
    from scripts.test_identity_ingress import exchange, frame_v2
    from scripts.test_managed_admission import request, result
    with bound_control(monkeypatch) as (client, supervisor, path):
        manager = supervisor._pinned_pairing_manager
        now = datetime.now(timezone.utc)
        manager.issue_invite_record(hashlib.sha256(b'synthetic-label-invite').hexdigest(),
                                    now + timedelta(minutes=4))
        status, reply = result(exchange(str(path), frame_v2(b'node-a', name.encode()) + request(
            '/pair/invite', body=b'synthetic-label-invite', headers=(
                'X-HappyRanch-Device-Label: forged-name', 'X-Tailscale-Hostname: forged-name'))))
        assert status == 200
        assert set(reply) == {'state', 'completion_handle', 'comparison_phrase', 'expires_at'}
        pending = client.status().pending
        assert pending.label == expected and pending.stable_id == 'node-a'
        assert pending.comparison_phrase == reply['comparison_phrase']
        assert pending.public()['fingerprint'] == hashlib.sha256(b'node-a').hexdigest()[:24]
        import html
        assert pending.public()['label'] == html.escape(expected, quote=True)
        before = Path(supervisor.config.state_path).read_bytes()
        assert client.status().pending == pending
        assert Path(supervisor.config.state_path).read_bytes() == before
        mutate_real(client, 'ApprovePending')
        state = supervisor.state_store.load()
        device = next(iter(state.devices.values()))
        stored_name = '' if expected == 'node-a' else expected
        assert state.delivery.device_label == device.device_label == stored_name
        assert client.status().devices[0].label == expected
        status, complete = result(exchange(str(path), frame_v2(b'node-a', b'changed-name') + request(
            '/pair/complete', body=reply['completion_handle'].encode())))
        assert status == 200 and set(complete) == {'credential'}
        assert supervisor.state_store.load().delivery is None
        seen = []
        original = manager.admit
        def admit(credential: str, stable_node_id: str, control_plane_id: str, now: Any) -> Any:
            seen.append((stable_node_id, control_plane_id))
            return original(credential, stable_node_id, control_plane_id, now)
        monkeypatch.setattr(manager, 'admit', admit)
        raw = exchange(str(path), frame_v2(b'node-a', b'node-b') + request(
            credential=complete['credential']))
        # Admission runs before the synthetic loopback upstream is attempted.
        assert raw and seen == [('node-a', 'protected-control')]
        assert manager.admit(complete['credential'], 'node-a', 'protected-control', now).ok
        assert not manager.admit(complete['credential'], 'node-b', 'protected-control', now).ok
        assert supervisor.state_store.load().devices[device.device_id].device_label == stored_name


def mutate_real(client: Any, method: str, *, before: Any = lambda: None) -> Any:
    snapshot = client.status()
    target = snapshot.devices[0] if method == 'RevokeSelected' else snapshot.pending
    client.mutate(method, snapshot, target, time.monotonic() + 60, before)
    return target


@pytest.mark.parametrize('method', ['ApprovePending', 'RejectPending', 'RevokeSelected'])
@pytest.mark.parametrize('legacy', [False, True])
def test_bound_os_labels_at_capacity(monkeypatch: Any, method: str, legacy: bool) -> None:
    import hashlib
    from datetime import datetime, timedelta, timezone
    from scripts.test_identity_ingress import exchange, frame_v2
    from scripts.test_managed_admission import request, result
    with bound_control(monkeypatch) as (client, supervisor, path):
        manager = supervisor._pinned_pairing_manager
        name = '\U0001f60a' * 96  # Each accepted character costs 12 JSON-escaped bytes.
        legacy_node = 'legacy-' + name if legacy else ''
        for count in range(1, 33):
            node, secret = f'node-{count}', f'synthetic-capacity-{count}'
            if legacy and count == 1:
                node = legacy_node
            recorded_name = '' if node == legacy_node else name
            now = datetime.now(timezone.utc)
            manager.issue_invite_record(hashlib.sha256(secret.encode()).hexdigest(),
                                        now + timedelta(minutes=4))
            status, reply = result(exchange(str(path), frame_v2(node.encode(), recorded_name.encode()) + request(
                '/pair/invite', body=secret.encode())))
            assert status == 200
            if count < 32:
                # Populate through the real core so a projection failure cannot
                # prevent reaching the capacity boundary under test.
                pending = supervisor.state_store.load().pending_approval
                assert manager.approve_pending(pending.generation, ('protected-control', node))
                assert manager.complete(reply['completion_handle'], node, 'protected-control', now)
        before = supervisor.state_store.load()
        assert len(before.devices) == 31 and before.pending_approval.device_label == name
        assert all(device.device_label == ('' if device.stable_node_id == legacy_node else name)
                   for device in before.devices.values())
        snapshot = client.status()
        assert snapshot.ready and len(snapshot.devices) == 31 and snapshot.pending is not None
        for record in (*snapshot.devices, snapshot.pending):
            if record.stable_id == legacy_node:
                assert record.label == legacy_node[:96], 'legacy fallback must keep its exact byte cost'
            else:
                assert record.label and name.startswith(record.label)
            assert record.public()['fingerprint'] == hashlib.sha256(record.stable_id.encode()).hexdigest()[:24]
        assert snapshot == client.status(), 'projection must be stable across snapshot comparisons'
        target = mutate_real(client, method)
        after = supervisor.state_store.load()
        if method == 'ApprovePending':
            assert after.pending_approval is None and len(after.devices) == 32
            assert manager.complete(reply['completion_handle'], node, 'protected-control', now)
            assert len(client.status().devices) == 32
        elif method == 'RejectPending':
            assert after.pending_approval is None and after.delivery is None
            assert after.devices == before.devices and len(client.status().devices) == 31
        else:
            assert after.pending_approval == before.pending_approval
            assert after.devices[target.record_id].revoked
            assert all(device == before.devices[key] for key, device in after.devices.items()
                       if key != target.record_id)
            assert len(client.status().devices) == 30
        assert all(device.device_label == ('' if device.stable_node_id == legacy_node else name)
                   for device in after.devices.values())


@pytest.mark.parametrize('operation', ['ApprovePending', 'RejectPending'])
def test_bound_pending_translation_and_delivery(monkeypatch: Any, operation: str) -> None:
    from datetime import datetime, timezone
    with bound_control(monkeypatch) as (client, supervisor, path):
        pending = pending_real(supervisor)
        # An unrelated anchored write makes snapshot generation DIFFERENT
        # from the PendingApproval generation before capture.
        supervisor.state_store.save(supervisor.state_store.load())
        before = client.status()
        assert before.generation != pending.generation
        mutate_real(client, operation)
        state = supervisor.state_store.load()
        assert state.pending_approval is None
        if operation == 'ApprovePending':
            assert len(state.devices) == 1
            credential = supervisor._pinned_pairing_manager.complete(
                pending.completion_handle, 'node-a', 'protected-control', datetime.now(timezone.utc))
            assert credential and supervisor._pinned_pairing_manager.admit(
                credential, 'node-a', 'protected-control', datetime.now(timezone.utc)).ok
        else:
            assert not state.devices and state.delivery is None
        reply = json.dumps(asdict(client.status()))
        assert pending.completion_handle not in reply and 'hrpin_' not in reply and 'digest' not in reply


@pytest.mark.parametrize('phase', ['invite', 'pending', 'delivery'])
def test_bound_status_reads_preserve_ceremony(monkeypatch: Any, phase: str) -> None:
    from pathlib import Path
    with bound_control(monkeypatch) as (client, supervisor, path):
        pending_real(supervisor)
        manager = supervisor._pinned_pairing_manager
        if phase == 'invite':
            from datetime import datetime, timedelta, timezone
            import hashlib
            manager.issue_invite_record(hashlib.sha256(b'synthetic-second').hexdigest(),
                                        datetime.now(timezone.utc) + timedelta(minutes=4))
        elif phase == 'delivery':
            mutate_real(client, 'ApprovePending')
        store_path = Path(supervisor.config.state_path)
        before = (store_path.read_bytes(), Path(str(store_path) + '.anchor').read_bytes())
        for _ in range(3):
            assert client.status().owned
        assert before == (store_path.read_bytes(), Path(str(store_path) + '.anchor').read_bytes())
        assert supervisor._pinned_pairing_manager is manager


@pytest.mark.parametrize('change', ['replace', 'cross-writer', 'expired'])
def test_bound_changed_pending_refuses_after_ready(monkeypatch: Any, change: str) -> None:
    from runtime.remote_access import helper_control as control
    from datetime import datetime, timedelta, timezone
    with bound_control(monkeypatch) as (client, supervisor, path):
        pending_real(supervisor)
        snapshot = client.status()
        def change_state() -> None:
            if change == 'replace':
                # Different manager/process cannot be excluded by the local RLock.
                from runtime.remote_access.pairing import PinnedPairingManager
                other = PinnedPairingManager(state_store=supervisor.state_store,
                    identity=supervisor._configured_identity(), registry=supervisor.registry)
                import hashlib
                other.issue_invite_record(hashlib.sha256(b'new').hexdigest(),
                                         datetime.now(timezone.utc) + timedelta(minutes=4))
                assert other.redeem('new', 'node-b', 'protected-control', datetime.now(timezone.utc))
            elif change == 'cross-writer':
                completed = subprocess.run([sys.executable, '-c',
                    'import sys; from pathlib import Path; '
                    'from runtime.remote_access.state_store import AtomicFileTrustStateStore; '
                    'from runtime.remote_access.authorization import TrustState; '
                    'from runtime.remote_access.identity import ConnectorIdentity; '
                    's=AtomicFileTrustStateStore(Path(sys.argv[1]), '
                    'TrustState(ConnectorIdentity(tenant_id="tenant",home_id="home",connector_id="connector"),0,0)); '
                    's.save(s.load())', supervisor.config.state_path], capture_output=True)
                assert completed.returncode == 0, completed.stderr
            else:
                with supervisor.state_store.transaction() as tx:
                    tx.state.pending_approval = replace(tx.state.pending_approval,
                        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        with pytest.raises(control.Refused, match='changed'):
            client.mutate('ApprovePending', snapshot, snapshot.pending, time.monotonic() + 60, change_state)
        assert not supervisor.state_store.load().devices


@pytest.mark.parametrize('fails', [False, True])
def test_bound_revoke_only_selected_and_retains_incomplete(monkeypatch: Any, fails: bool) -> None:
    from runtime.remote_access import helper_control as control
    from scripts.test_managed_admission import grant, ControlledHandle
    from runtime.remote_access.revocation import RevocationIncomplete
    with bound_control(monkeypatch) as (client, supervisor, path):
        a, _ = grant(supervisor, path, 'node-a', 'invite-a')
        b, _ = grant(supervisor, path, 'node-b', 'invite-b')
        state = supervisor.state_store.load()
        class Handle:
            closed = False
            def close(self) -> None:
                # The real durable deny must precede the callback; SH/EX can
                # be acquired by another store here (no flock held in callback).
                from runtime.remote_access.state_store import AtomicFileTrustStateStore
                other = AtomicFileTrustStateStore(Path(supervisor.config.state_path), supervisor.initial_state())
                assert other.load().devices[a].revoked
                self.closed = True
                if fails:
                    raise OSError('synthetic-secret-must-not-escape')
        ha, hb = Handle(), ControlledHandle('b')
        supervisor.registry.open('a-stream', ha, device_id=a, epoch=state.devices[a].authorization_epoch)
        survivor = supervisor.registry.open('b-stream', hb, device_id=b, epoch=state.devices[b].authorization_epoch)
        snapshot = client.status()
        target = next(d for d in snapshot.devices if d.record_id == a)
        if fails:
            with pytest.raises(control.Refused, match='unavailable'):
                client.mutate('RevokeSelected', snapshot, target, time.monotonic() + 60, lambda: None)
            assert client.status().phase == 'failed'
            with pytest.raises(RevocationIncomplete, match='revocation epoch'):
                supervisor._reconcile_revocation()
            assert client.status().phase == 'failed'
        else:
            client.mutate('RevokeSelected', snapshot, target, time.monotonic() + 60, lambda: None)
            supervisor._reconcile_revocation()
        assert ha.closed and not survivor.closed
        assert supervisor.state_store.load().devices[a].revoked
        assert not supervisor.state_store.load().devices[b].revoked
        assert supervisor._listener_running()


@pytest.mark.parametrize('boundary', ['helper', 'control'])
def test_bound_revoke_expired_grant_closes_only_selected(venue: Any, monkeypatch: Any, boundary: str) -> None:
    from datetime import datetime, timedelta, timezone
    from scripts.test_managed_admission import (
        ControlledForwarder, ControlledHandle, call, connect, grant, read_all, result, supervisor_module,
    )
    helper, authority, _, caller, _ = venue
    transport = ControlledForwarder()
    monkeypatch.setattr(supervisor_module, 'HttpLoopbackForwarder', lambda *a, **kw: transport)
    with bound_control(monkeypatch) as (client, supervisor, path):
        helper.control = HELPER.ControlClient(client.path)
        device_a, credential_a = grant(supervisor, path)
        device_b, credential_b = grant(supervisor, path, 'node-b', 'synthetic-invite-b')
        with supervisor.state_store.transaction() as tx:
            tx.state.devices[device_a] = replace(tx.state.devices[device_a],
                expires_at=datetime.now(timezone.utc) + timedelta(seconds=2))
        expiry = supervisor.state_store.load().devices[device_a].expires_at
        denied_before_close = set()
        checking_deny = False
        original_close = ControlledHandle.close
        def close(handle: Any) -> None:
            if checking_deny and handle.label.startswith('a-'):
                # Observe the real persisted deny at the transport callback.
                assert supervisor.state_store.load().devices[device_a].revoked
                denied_before_close.add(handle.label)
            original_close(handle)
        monkeypatch.setattr(ControlledHandle, 'close', close)
        clients, handles = {}, {}
        try:
            for owner, node, credential in [('a', 'node-a', credential_a), ('b', 'node-b', credential_b)]:
                for kind in ('http', 'websocket', 'sse'):
                    label = owner + '-' + kind
                    clients[label] = connect(path, node, credential, kind, label)
                    handle = transport.handles.get(timeout=3)
                    assert handle.label == label and handle.started.wait(3)
                    handles[label] = handle
                    if kind != 'http':
                        assert b'200 OK' in clients[label].recv(4096)
            assert datetime.now(timezone.utc) < expiry, 'all streams must open before credential expiry'
            while datetime.now(timezone.utc) <= expiry:
                time.sleep(.02)
            assert not any(handle.closed for handle in handles.values())
            snapshot = client.status()
            target = next(d for d in snapshot.devices if d.record_id == device_a)
            assert target.expires_at == expiry.timestamp() < time.time()
            assert snapshot.ready and snapshot.phase == 'ready'
            checking_deny = True
            if boundary == 'helper':
                for _ in snapshot.devices:
                    shown = Call(caller, 'NextDevice').finish()
                    if shown['selected']['label'] == 'node-a':
                        break
                assert shown['selected']['label'] == 'node-a', 'expired grant must remain selectable'
                authority.mode = 'delay'
                revoke = Call(caller, 'RevokeSelected')
                eventually(lambda: len(authority.waiting) == 1 or revoke.done.is_set())
                if revoke.done.is_set():
                    revoke.finish()  # Surface an early refusal at the Gio boundary.
                assert len(authority.calls) == 1 and 'node-a' in authority.calls[0][2]['polkit.message']
                assert not supervisor.state_store.load().devices[device_a].revoked
                authority.grant()
                assert revoke.finish() == 'ok'
            else:
                # Exercise the listener's own expiry checks independently of Gio.
                client.mutate('RevokeSelected', snapshot, target, time.monotonic() + 60, lambda: None)
            state = supervisor.state_store.load()
            assert state.devices[device_a].revoked and not state.devices[device_b].revoked
            assert denied_before_close == {'a-http', 'a-websocket', 'a-sse'}
            for kind in ('http', 'websocket', 'sse'):
                assert handles['a-' + kind].closed and not handles['b-' + kind].closed
                closed = read_all(clients['a-' + kind])
                assert result(closed)[0] == 403 if kind == 'http' else closed == b''
                handles['b-' + kind].events.put(b'data: survivor-after-expired-revoke\n\n')
                if kind == 'http':
                    handles['b-http'].events.put(None)
                    assert b'survivor-after-expired-revoke' in read_all(clients['b-http'])
                else:
                    assert b'survivor-after-expired-revoke' in clients['b-' + kind].recv(4096)
            assert result(call(path, 'node-a', credential=credential_a))[0] == 403
            assert b'200 OK' in call(path, 'node-b', credential=credential_b)
        finally:
            checking_deny = False
            supervisor.shutdown()
            for wire in clients.values():
                wire.close()


@pytest.mark.parametrize('method', ['ApprovePending', 'RejectPending', 'RevokeSelected'])
def test_bound_generation_cas_at_actual_mutation(monkeypatch: Any, method: str) -> None:
    from runtime.remote_access import helper_control as control
    with bound_control(monkeypatch) as (client, supervisor, path):
        if method == 'RevokeSelected':
            from scripts.test_managed_admission import grant
            device_id, _ = grant(supervisor, path)
        else:
            pending_real(supervisor)
        manager = supervisor._pinned_pairing_manager
        name = {'ApprovePending': 'approve_pending', 'RejectPending': 'reject_pending',
                'RevokeSelected': 'revoke_device'}[method]
        original = getattr(manager, name)
        def racing(*args: Any, **kwargs: Any) -> Any:
            # A separate store writer lands AFTER the handler's final read.
            supervisor.state_store.save(supervisor.state_store.load())
            return original(*args, **kwargs)
        monkeypatch.setattr(manager, name, racing)
        with pytest.raises(control.Refused, match='changed'):
            mutate_real(client, method)
        state = supervisor.state_store.load()
        if method == 'RevokeSelected':
            assert not state.devices[device_id].revoked
        else:
            assert not state.devices


@pytest.mark.parametrize('case', ['legacy-33', 'unicode-bound'])
def test_bound_projection_preserves_store_bounds(monkeypatch: Any, case: str) -> None:
    from runtime.remote_access.authorization import DeviceAuthorization
    from datetime import datetime, timedelta, timezone
    with bound_control(monkeypatch) as (client, supervisor, path):
        if case == 'legacy-33':
            with supervisor.state_store.transaction() as tx:
                for i in range(33):
                    tx.state.apply_pairing(DeviceAuthorization(device_id=f'legacy-{i}', tenant_id='tenant',
                        home_id='home', authorization_epoch=i + 1,
                        expires_at=datetime.now(timezone.utc) + timedelta(days=1)))
            assert client.status().devices == ()
            assert len(supervisor.state_store.load().devices) == 33
        else:
            pending_real(supervisor, '\U0001f60a' * 256)
            assert len(client.status().pending.stable_id) == 256
            mutate_real(client, 'ApprovePending')
            assert len(client.status().devices) == 1


def test_bound_helper_dialog_cursor_and_real_target(venue: Any, monkeypatch: Any) -> None:
    helper, authority, _, caller, _ = venue
    with bound_control(monkeypatch) as (client, supervisor, path):
        helper.control = HELPER.ControlClient(client.path)
        pending_real(supervisor)
        shown = Call(caller, 'Status').finish()
        assert shown['pending']['label'] == 'node-a'
        authority.mode = 'delay'
        call = Call(caller, 'ApprovePending')
        eventually(lambda: len(authority.waiting) == 1)
        assert 'node-a' in authority.calls[0][2]['polkit.message']
        pending_real(supervisor, 'node-b')
        authority.grant()
        call.finish('changed')
        assert not supervisor.state_store.load().devices
        # A new explicit call raises its own prompt and approves only the new node.
        call = Call(caller, 'ApprovePending')
        eventually(lambda: len(authority.waiting) == 1)
        authority.grant()
        assert call.finish() == 'ok'
        assert len(authority.calls) == 2
        assert {d.stable_node_id for d in supervisor.state_store.load().devices.values()} == {'node-b'}
        store = Path(supervisor.config.state_path)
        before = store.read_bytes()
        selected = Call(caller, 'NextDevice').finish()
        assert selected['selected']['label'] == 'node-b'
        Call(caller, 'Status').finish()
        assert store.read_bytes() == before
        authority.mode = 'grant'
        assert Call(caller, 'RevokeSelected').finish() == 'ok'
        assert all(d.revoked for d in supervisor.state_store.load().devices.values())


@pytest.mark.parametrize('case', ['wrong-uid', 'extra-args', 'oversize', 'expired', 'unsafe-record'])
def test_bound_control_refusals_are_read_only(monkeypatch: Any, case: str) -> None:
    from runtime.remote_access import helper_control as control, release_verifier
    with bound_control(monkeypatch) as (client, supervisor, path):
        pending_real(supervisor)
        store = Path(supervisor.config.state_path)
        before = store.read_bytes()
        listener = supervisor._control_listener
        if case == 'wrong-uid':
            monkeypatch.setattr(listener.handler, 'handle',
                                control.ControlHandler(listener.handler.operations).handle)
            with pytest.raises(control.Refused, match='wrong-uid'):
                client.status()
        elif case == 'unsafe-record':
            def refused(*args: Any, **kwargs: Any) -> Any:
                raise OSError('synthetic-secret')
            monkeypatch.setattr(release_verifier, '_read', refused)
            with pytest.raises(control.Refused, match='unavailable'):
                client.status()
        elif case == 'expired':
            snapshot = client.status()
            with pytest.raises(control.Refused, match='expired'):
                client.mutate('ApprovePending', snapshot, snapshot.pending, time.monotonic() - 1, lambda: None)
        else:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.connect(client.path)
                raw = (json.dumps({'method': 'status', 'args': []}).encode() + b'\n'
                       if case == 'extra-args' else b'x' * (control.MAX_FRAME + 1))
                connection.sendall(raw)
                assert control._receive(connection) == {'error': 'invalid-request'}
        assert store.read_bytes() == before
        assert not supervisor.state_store.load().devices


def test_bound_projection_over_frame_refuses_without_store_veto(monkeypatch: Any) -> None:
    from datetime import datetime, timedelta, timezone
    from runtime.remote_access import helper_control as control
    from runtime.remote_access.authorization import DeviceAuthorization
    with bound_control(monkeypatch) as (client, supervisor, path):
        with supervisor.state_store.transaction() as tx:
            for i in range(32):
                tx.state.apply_pairing(DeviceAuthorization(device_id=f'pinned-{i}', tenant_id='tenant',
                    home_id='home', authorization_epoch=i + 1,
                    expires_at=datetime.now(timezone.utc) + timedelta(days=1),
                    credential_digest='a' * 64, control_plane_id='protected-control',
                    stable_node_id='\U0001f60a' * 256))
        before = Path(supervisor.config.state_path).read_bytes()
        with pytest.raises(control.Refused, match='unavailable'):
            client.status()
        assert len(supervisor.state_store.load().devices) == 32
        assert Path(supervisor.config.state_path).read_bytes() == before


@pytest.mark.parametrize('method', ['ApprovePending', 'RejectPending'])
def test_bound_null_core_result_is_refused(monkeypatch: Any, method: str) -> None:
    from runtime.remote_access import helper_control as control
    with bound_control(monkeypatch) as (client, supervisor, path):
        pending_real(supervisor)
        before = Path(supervisor.config.state_path).read_bytes()
        name = 'approve_pending' if method == 'ApprovePending' else 'reject_pending'
        monkeypatch.setattr(supervisor._pinned_pairing_manager, name, lambda *a, **kw: None)
        with pytest.raises(control.Refused):
            mutate_real(client, method)
        assert Path(supervisor.config.state_path).read_bytes() == before


def test_bound_expired_pending_remains_reported(monkeypatch: Any) -> None:
    from datetime import datetime, timedelta, timezone
    from runtime.remote_access import helper_control as control
    with bound_control(monkeypatch) as (client, supervisor, path):
        pending_real(supervisor)
        with supervisor.state_store.transaction() as tx:
            tx.state.pending_approval = replace(tx.state.pending_approval,
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        before = Path(supervisor.config.state_path).read_bytes()
        for _ in range(2):
            assert client.status().phase == 'failed'
            with pytest.raises(control.Refused, match='expired'):
                mutate_real(client, 'ApprovePending')
        assert supervisor.state_store.load().pending_approval is not None
        assert Path(supervisor.config.state_path).read_bytes() == before


def test_bound_socket_custody_and_cleanup(monkeypatch: Any) -> None:
    from runtime.remote_access import helper_control as control
    with bound_control(monkeypatch) as (client, supervisor, path):
        socket_path = Path(client.path)
        assert socket_path.stat().st_uid == os.geteuid()
        assert socket_path.stat().st_mode & 0o777 == 0o600
        operations = supervisor._control_listener.handler.operations
        with pytest.raises(control.Refused, match='unavailable'):
            control.ControlListener(operations)
        assert client.status().owned  # failed second bind did not delete the live socket
        supervisor.shutdown()
        assert not socket_path.exists()
        victim = socket_path.parent / 'victim'
        victim.write_text('synthetic-untouched')
        socket_path.symlink_to(victim)
        with pytest.raises(control.Refused, match='unavailable'):
            control.ControlListener(operations)
        assert socket_path.is_symlink() and victim.read_text() == 'synthetic-untouched'


@pytest.mark.parametrize('method', ['ApprovePending', 'RejectPending', 'RevokeSelected'])
def test_bound_publication_uncertainty_stays_reported(monkeypatch: Any, method: str) -> None:
    from runtime.remote_access import helper_control as control
    from runtime.remote_access.state_store import StateStoreError
    with bound_control(monkeypatch) as (client, supervisor, path):
        if method == 'RevokeSelected':
            from scripts.test_managed_admission import grant
            grant(supervisor, path)
        else:
            pending_real(supervisor)
        before = Path(supervisor.config.state_path).read_bytes()
        def failed_write(*args: Any, **kwargs: Any) -> None:
            raise StateStoreError('synthetic-secret-publication-fault')
        monkeypatch.setattr(supervisor.state_store, '_write_pair', failed_write)
        with pytest.raises(control.Refused, match='unavailable'):
            mutate_real(client, method)
        assert Path(supervisor.config.state_path).read_bytes() == before
        for _ in range(2):
            assert client.status().phase == 'failed' and not client.status().ready


@pytest.mark.parametrize('case,category', [
    ('unknown', 'owner_unknown'), ('ambiguous', 'owner_ambiguous'),
    ('missing', 'release_home_invalid'), ('mismatch', 'owner_mismatch'),
])
def test_bootstrap_refuses_before_connector_or_auth(venue: Any, monkeypatch: Any,
                                                     tmp_path: Any, case: Any, category: Any) -> None:
    _, authority, operations, client, _ = venue
    home = tmp_path / 'owner'
    row = pwd.struct_passwd(('owner', 'x', os.getuid(), os.getgid(), '', str(home), '/bin/sh'))
    rows = [] if case == 'unknown' else [row, row] if case == 'ambiguous' else [row]
    if case == 'missing':
        (home / '.local/share/happyranch').rmdir()
    if case == 'mismatch':
        record = dict(user='another', uid=os.getuid(), data_home=str(home / '.local/share/happyranch'),
                      unit='happyranch-daemon.service', fragment_path='/usr/lib/systemd/user/happyranch-daemon.service')
        monkeypatch.setattr(HELPER._owner._verifier, '_read', lambda *a, **k: json.dumps(record).encode())
    monkeypatch.setattr(HELPER, 'bootstrap_owner', partial(HELPER._owner.bootstrap_owner,
        passwd=SimpleNamespace(getpwall=lambda: rows)))
    Call(client).finish(category)
    assert operations.status_reads == 0 and not operations.effects and not authority.calls


@pytest.mark.parametrize('fragment,category', [
    ('/usr/lib/systemd/user/happyranch-daemon.service', None),
    ('/etc/systemd/user/happyranch-daemon.service', 'release_home_invalid'),
])
def test_retained_manual_record_status_binding(venue: Any, monkeypatch: Any, tmp_path: Any,
                                               fragment: str, category: str | None) -> None:
    _, authority, operations, client, _ = venue
    home = tmp_path / 'owner'
    record = dict(user='owner', uid=os.getuid(), data_home=str(home / '.local/share/happyranch'),
                  unit='happyranch-daemon.service', fragment_path=fragment)
    # Existing private-bus venue isolates root custody; the packaged resolver
    # still parses the retained manual record and validates its actual home.
    monkeypatch.setattr(HELPER._owner._verifier, '_read', lambda *a, **k: json.dumps(record).encode())
    monkeypatch.setattr(HELPER._owner._verifier.pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_dir=str(home)))
    if category is None:
        HELPER._owner._verifier.validate_record(record)
        assert HELPER.bootstrap_owner(os.getuid()).data_home == home / '.local/share/happyranch'
    before = operations.status_reads
    value = Call(client, 'Status').finish(category)
    if category is None:
        assert value['phase'] == 'pending' and operations.status_reads == before + 1
    else:
        assert operations.status_reads == before
    assert not operations.effects and not authority.calls


def test_idle_exit_after_sixty_idle_seconds(venue: Any) -> None:
    helper, _, _, client, _ = venue
    now = [0.0]
    helper.clock = lambda: now[0]
    helper.last_activity = 0.0
    loop = SimpleNamespace(quit=Mock())
    now[0] = 59.999
    assert helper._idle(loop) is True
    loop.quit.assert_not_called()
    Call(client, 'Status').finish()  # real dispatch resets the idle clock
    now[0] = 119.998
    helper._idle(loop)
    loop.quit.assert_not_called()
    helper.inflight = object()  # an outstanding authorization is never idle
    now[0] = 180.0
    helper._idle(loop)
    loop.quit.assert_not_called()
    helper.inflight = None
    helper._clear()  # a settled authorization starts a new idle interval
    now[0] = 239.999
    helper._idle(loop)
    loop.quit.assert_not_called()
    now[0] = 240.0
    helper._idle(loop)
    loop.quit.assert_called_once_with()


def test_exit_releases_name_before_quit_and_refuses_unique_name_race(venue: Any) -> None:
    helper, authority, operations, client, _ = venue
    def quit() -> None:
        owned = client.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'NameHasOwner', GLib.Variant('(s)', (BUS_NAME,)),
            GLib.VariantType.new('(b)'), Gio.DBusCallFlags.NONE, 2000, None)
        assert owned.unpack() == (False,)
    loop = SimpleNamespace(quit=Mock(side_effect=quit))
    helper.exit_requested = True
    helper._idle(loop)
    loop.quit.assert_called_once_with()
    # A caller that already resolved the old unique owner can still reach the
    # registered object until close; it must get an existing non-success error.
    with pytest.raises(GLib.Error, match='unavailable'):
        client.call_sync(helper.connection.get_unique_name(), OBJECT_PATH, INTERFACE,
            'Status', None, GLib.VariantType.new('(s)'), Gio.DBusCallFlags.NONE, 2000, None)
    assert not authority.calls and not operations.effects


@pytest.mark.parametrize('phase,expected', [('prepared', 'in_progress'), ('ready', 'ready'),
                                          ('failed_uncertain', 'uncertain'), (None, 'unknown')])
def test_status_v2_bus_projection(venue: Any, monkeypatch: Any, phase: str | None, expected: str) -> None:
    helper, authority, operations, client, _ = venue
    observed = None if phase is None else HELPER._helper_generation.GenerationSnapshot(7, 'a' * 32, os.getuid(), phase)
    monkeypatch.setattr(helper.generations, 'current_generation', lambda _: observed)
    before = Call(client, 'Status').finish()
    status = Call(client, 'StatusV2')
    value = status.finish()
    raw = status.reply.get_body().unpack()[0].encode()
    assert HELPER._status_v2.decode(raw).setup.state.value == expected
    assert len(raw) <= 16384 and raw.endswith(b'\n')
    assert value['runtime'] == {'state': 'available', 'age_seconds': 0}
    assert value['setup']['age_seconds'] == (None if phase is None else 0)
    for dimension in ('invite', 'address', 'policy', 'cleanup'):
        assert value[dimension]['state'] == 'unknown' and value[dimension]['age_seconds'] is None
    assert Call(client, 'Status').finish() == before
    assert not authority.calls and not operations.effects and not helper.cursors
    assert not helper.audit.path.exists()


def test_status_v2_stopped_connector_and_unreadable_store(venue: Any, monkeypatch: Any) -> None:
    helper, authority, operations, client, _ = venue
    helper.control.path += '-absent'
    def unavailable(_: float) -> None:
        raise HELPER._helper_generation.GenerationUnavailable()
    monkeypatch.setattr(helper.generations, 'current_generation', unavailable)
    Call(client, 'StatusV2').finish('host-state-unrecognized')
    assert not authority.calls and not operations.effects


@pytest.mark.parametrize('policy', ['current', 'renewing', 'expired', 'renewal_failed', 'uncertain'])
def test_status_v2_policy_projection(venue: Any, monkeypatch: Any, policy: str) -> None:
    helper, authority, operations, client, _ = venue
    calls = []
    class PolicyReader:
        def __init__(self, method: str, uid: int) -> None:
            assert method == 'RetryPolicyRenewal' and uid == os.getuid()
        def policy_status(self, remaining: float) -> dict:
            assert 0 < remaining <= 2
            calls.append(policy)
            return {'policy': policy}
    monkeypatch.setattr(HELPER._helper_generation, 'HomeLifecycleClient', PolicyReader)
    monkeypatch.setattr(helper.generations, 'current_generation', lambda _: None)
    before = Call(client, 'Status').finish()
    value = Call(client, 'StatusV2').finish()
    assert value['policy'] == {'state': policy, 'age_seconds': 0} and calls == [policy]
    assert set(value) == {'version', 'setup', 'runtime', 'invite', 'address', 'policy', 'cleanup'}
    assert Call(client, 'Status').finish() == before
    assert not authority.calls and not operations.effects and not helper.audit.path.exists()


@pytest.mark.parametrize('case', ['body', 'fd', 'owner', 'receipt_owner'])
def test_status_v2_preserves_read_boundary(venue: Any, monkeypatch: Any, case: str) -> None:
    helper, authority, operations, client, _ = venue
    if case == 'owner':
        def refuse(_: int) -> None:
            raise HELPER.OwnerRefused('owner_mismatch')
        monkeypatch.setattr(helper, '_binding', refuse)
    if case == 'receipt_owner':
        monkeypatch.setattr(helper.generations, 'current_generation', lambda _: (
            HELPER._helper_generation.GenerationSnapshot(7, 'a' * 32, os.getuid() + 1, 'ready')))
    call = Call(client, 'StatusV2', body=GLib.Variant('(s)', ('input',)) if case == 'body' else None,
                fd=case == 'fd')
    call.finish('invalid-request' if case in ('body', 'fd') else
                'owner_mismatch' if case == 'owner' else 'changed')
    assert not authority.calls and not operations.effects and operations.status_reads == 0


def test_status_v2_deadline_and_single_worker(venue: Any, monkeypatch: Any) -> None:
    helper, authority, operations, client, new = venue
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(helper.generations, 'current_generation', lambda _: None)
    original = helper.control.status
    def blocked() -> Any:
        entered.set()
        assert release.wait(3)
        return original()
    monkeypatch.setattr(helper.control, 'status', blocked)
    started = time.monotonic()
    first = Call(client, 'StatusV2')
    assert entered.wait(1)
    try:
        Call(new(), 'StatusV2').finish('busy')
        first.finish('expired')
        assert time.monotonic() - started < 2.1
    finally:
        release.set()
    eventually(lambda: helper.status_worker is None)
    assert Call(client, 'StatusV2').finish()['runtime']['state'] == 'available'
    assert not authority.calls and not operations.effects


@pytest.mark.parametrize('method,event', [('ApprovePending', 'device_approved'),
                                         ('RejectPending', 'device_rejected'),
                                         ('RevokeSelected', 'device_revoked')])
def test_device_audit_intent_before_effect_and_outcome(venue: Any, method: str, event: str) -> None:
    helper, _, operations, client, _ = venue
    if method == 'RevokeSelected':
        Call(client, 'NextDevice').finish()
    original = helper.control.mutate
    def checked(*args: Any) -> Any:
        assert helper.audit.path.exists(), 'durable intent must precede connector admission'
        lines = [json.loads(line) for line in helper.audit.path.read_bytes().splitlines()]
        assert len(lines) == 1 and lines[0]['stage'] == 'intent' and lines[0]['event'] == event
        assert not operations.effects
        return original(*args)
    helper.control.mutate = checked
    assert Call(client, method).finish() == 'ok'
    rows = [json.loads(line) for line in helper.audit.path.read_bytes().splitlines()]
    assert [(row['event'], row['stage'], row['outcome']) for row in rows] == [
        (event, 'intent', None), (event, 'outcome', 'succeeded')]
    assert rows[0]['intent_id'] == rows[1]['intent_id'] and len(operations.effects) == 1
    assert helper.audit.path.stat().st_mode & 0o777 == 0o600
    assert all(row['subject_id'] is None for row in rows)
    assert b'stable-c' not in helper.audit.path.read_bytes()


@pytest.mark.parametrize('case', ['intent_failure', 'before_commit', 'lost_reply', 'outcome_failure'])
def test_device_audit_failure_settlement(venue: Any, monkeypatch: Any, case: str) -> None:
    helper, _, operations, client, _ = venue
    original = helper.control.mutate
    if case == 'intent_failure':
        monkeypatch.setattr(helper.audit, 'intent', Mock(side_effect=HELPER._home_audit.AuditRefused('unavailable')))
    elif case == 'before_commit':
        monkeypatch.setattr(helper.control, 'mutate', Mock(side_effect=HELPER.Refused('changed')))
    elif case == 'outcome_failure':
        monkeypatch.setattr(helper.audit, 'outcome', Mock(side_effect=HELPER._home_audit.AuditRefused('unavailable')))
    else:
        def lost(*args: Any) -> None:
            original(*args)
            raise ConnectionError('synthetic lost settlement')
        monkeypatch.setattr(helper.control, 'mutate', lost)
    Call(client).finish('changed' if case == 'before_commit' else 'unavailable')
    rows = ([json.loads(line) for line in helper.audit.path.read_bytes().splitlines()]
            if helper.audit.path.exists() else [])
    if case == 'intent_failure':
        assert not rows and not operations.effects
    else:
        assert rows, 'intent must survive the attempted mutation'
        assert rows[0]['stage'] == 'intent'
        assert rows[-1]['outcome'] == ('refused' if case == 'before_commit' else 'uncertain')
        assert rows[-1]['event'] == ('device_approved' if case == 'before_commit' else 'uncertain')
        assert len(operations.effects) == (0 if case == 'before_commit' else 1)


def test_status_v2_responds_while_mutation_context_is_busy(venue: Any, monkeypatch: Any) -> None:
    helper, authority, operations, client, _ = venue
    monkeypatch.setattr(helper.generations, 'current_generation', lambda _: None)
    held, release = threading.Event(), threading.Event()
    def hold_context() -> bool:
        with helper.lock:
            held.set()
            assert release.wait(3)
        return False
    GLib.idle_add(hold_context)
    assert held.wait(1)
    try:
        started = time.monotonic()
        assert Call(client, 'StatusV2').finish()['runtime']['state'] == 'available'
        assert time.monotonic() - started < 2
    finally:
        release.set()
    assert not authority.calls and not operations.effects
