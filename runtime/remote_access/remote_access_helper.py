"""Fixed no-input Gio service, intended for system Python -I as root.

The installed entry binds each authenticated sender to its validated release
home before connector reads or authorization. Lifecycle effects remain absent.
"""
from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

from gi.repository import Gio, GLib

if TYPE_CHECKING:
    from .helper_lifecycle import LifecycleCapture

# The existing remote_access package initializer imports the connector stack.
# Load ONLY this root-owned sibling, also when invoked by system Python -I.
_CONTROL_SPEC = importlib.util.spec_from_file_location(
    'happyranch_fixed_helper_control', Path(__file__).with_name('helper_control.py'))
_control = importlib.util.module_from_spec(_CONTROL_SPEC)
sys.modules[_CONTROL_SPEC.name] = _control
_CONTROL_SPEC.loader.exec_module(_control)
CONTROL_SOCKET, MUTATIONS = _control.CONTROL_SOCKET, _control.MUTATIONS
ControlClient, Device, Refused, Snapshot = (_control.ControlClient, _control.Device,
                                         _control.Refused, _control.Snapshot)

_OWNER_SPEC = importlib.util.spec_from_file_location(
    'happyranch_fixed_helper_owner', Path(__file__).with_name('helper_owner.py'))
_owner = importlib.util.module_from_spec(_OWNER_SPEC)
sys.modules[_OWNER_SPEC.name] = _owner
_OWNER_SPEC.loader.exec_module(_owner)
bootstrap_owner, OwnerRefused, Owner = _owner.bootstrap_owner, _owner.OwnerRefused, _owner.Owner

SYSTEM_BUS_ADDRESS = 'unix:path=/run/dbus/system_bus_socket'
BUS_NAME = 'ai.happyranch.RemoteAccess1'
OBJECT_PATH = '/ai/happyranch/RemoteAccess1'
INTERFACE = BUS_NAME
ACTION_PREFIX = 'ai.happyranch.remote-access.'
AUTH_DEADLINE = 120.0
IDLE_SECONDS = 60.0
METHODS = frozenset({'Status', 'NextDevice', *MUTATIONS})
INTROSPECTION = '<node><interface name="' + INTERFACE + '">' + ''.join(
    '<method name="' + name + '"><arg name="result" type="s" direction="out"/></method>'
    for name in sorted(METHODS)) + '</interface></node>'
LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Capture:
    method: str
    sender: str
    uid: int
    owner: Owner
    snapshot: Snapshot
    record: Device
    deadline: float
    cancellation_id: str
    message: Gio.DBusMessage


class RemoteAccessHelper:
    def __init__(self, connection: Gio.DBusConnection, release_uid: int | None = None,
                 control_path: str = CONTROL_SOCKET, clock: Callable[[], float] = time.monotonic,
                 deadline: float = AUTH_DEADLINE) -> None:
        if ((release_uid is not None and (type(release_uid) is not int or release_uid <= 0))
                or not 0 < deadline <= AUTH_DEADLINE):
            raise Refused('unavailable')
        self.connection, self.release_uid = connection, release_uid
        self.control = ControlClient(control_path)
        self.clock, self.deadline = clock, deadline
        self.last_activity = self.clock()
        self.lock = threading.RLock()
        self.cursors: dict[str, str] = {}
        self.inflight: Capture | LifecycleCapture | None = None
        self.timer = 0
        self.cancelled = False
        self.authorization_terminal = False
        self.authority = Gio.DBusProxy.new_sync(connection, Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES,
            None, 'org.freedesktop.PolicyKit1', '/org/freedesktop/PolicyKit1/Authority',
            'org.freedesktop.PolicyKit1.Authority', None)
        self.filter_id = connection.add_filter(self._filter, None)
        self.subscription = connection.signal_subscribe('org.freedesktop.DBus',
            'org.freedesktop.DBus', 'NameOwnerChanged', '/org/freedesktop/DBus', None,
            Gio.DBusSignalFlags.NONE, self._owner_changed, None)
        info = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION).interfaces[0]
        self.registration = connection.register_object(OBJECT_PATH, info, None, None, None)
        result = connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
            'org.freedesktop.DBus', 'RequestName', GLib.Variant('(su)', (BUS_NAME, 4)),
            GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 2000, None)
        if result.unpack()[0] != 1:
            self.close()
            raise Refused('unavailable')

    def try_claim(self, capture: LifecycleCapture) -> bool:
        """Inert lifecycle adapter to the existing global slot; never queues."""
        if not self.lock.acquire(blocking=False):
            return False
        try:
            if self.inflight is not None:
                return False
            self.inflight = capture
            self.last_activity = self.clock()
            return True
        finally:
            self.lock.release()

    @contextmanager
    def effect_guard(self, capture: LifecycleCapture) -> Iterator[None]:
        if not self.lock.acquire(blocking=False):
            raise Refused('busy')
        try:
            if self.inflight is not capture:
                raise Refused('changed')
            yield
        finally:
            self.lock.release()

    def release(self, capture: LifecycleCapture) -> None:
        with self.lock:
            if self.inflight is capture:
                self.inflight = None
                self.last_activity = self.clock()

    def _idle(self, loop: GLib.MainLoop) -> bool:
        if self.inflight is None and self.clock() - self.last_activity >= IDLE_SECONDS:
            loop.quit()
        return True  # serve removes the source even on the idle-exit path

    def close(self) -> None:
        if isinstance(self.inflight, Capture):
            self._cancel('unavailable')
        self.connection.remove_filter(self.filter_id)
        self.connection.signal_unsubscribe(self.subscription)
        self.connection.unregister_object(self.registration)
        self.cursors.clear()

    def _filter(self, connection: Gio.DBusConnection, message: Gio.DBusMessage,
                incoming: bool, data: object) -> Gio.DBusMessage | None:
        if (incoming and message.get_message_type() == Gio.DBusMessageType.METHOD_CALL
                and message.get_path() == OBJECT_PATH):
            # Gio filters run on its message thread; all work goes to the main context.
            # Preserve refusal at arrival even if main-context dispatch is busy
            # finishing the first operation. Never queue a second surprise dialog.
            busy = self.inflight is not None
            GLib.idle_add(self._dispatch, message, busy)
            return None
        return message

    def _reply(self, message: Gio.DBusMessage, value: dict | str) -> None:
        reply = message.new_method_reply()
        reply.set_body(GLib.Variant('(s)', (json.dumps(value, separators=(',', ':')),)))
        self.connection.send_message(reply, Gio.DBusSendMessageFlags.NONE)

    def _error(self, message: Gio.DBusMessage, category: str) -> None:
        category = category if category in _owner.FAILURES else Refused(category).category
        LOG.info('remote-access-helper %s', category)
        reply = message.new_method_error_literal(INTERFACE + '.Error.' + category.replace('-', '_'), category)
        self.connection.send_message(reply, Gio.DBusSendMessageFlags.NONE)

    def _uid(self, sender: str) -> int:
        try:
            result = self.connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'GetConnectionUnixUser', GLib.Variant('(s)', (sender,)),
                GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 2000, None)
            return result.unpack()[0]
        except Exception:
            raise Refused('disconnected') from None

    def _binding(self, uid: int) -> Owner:
        owner = bootstrap_owner(uid)
        if self.release_uid is not None and owner.uid != self.release_uid:
            raise Refused('wrong-uid')
        return owner

    def _snapshot(self, uid: int) -> Snapshot:
        snapshot = self.control.status()
        if not snapshot.owned or snapshot.release_uid != uid:
            raise Refused('changed')
        return snapshot

    def _target(self, method: str, sender: str, snapshot: Snapshot) -> Device:
        target = (snapshot.pending if method != 'RevokeSelected' else
                  next((d for d in snapshot.devices if d.record_id == self.cursors.get(sender)), None))
        if target is None:
            raise Refused('changed')
        if method != 'RevokeSelected' and target.expires_at <= time.time():
            raise Refused('expired')
        return target

    def _dispatch(self, message: Gio.DBusMessage, busy_on_arrival: bool = False) -> bool:
        self.last_activity = self.clock()
        try:
            fds = message.get_unix_fd_list()
            if message.get_signature() or (fds is not None and fds.get_length()):
                raise Refused('invalid-request')
            method, sender = message.get_member(), message.get_sender()
            if message.get_interface() != INTERFACE or method not in METHODS:
                raise Refused('unknown-method')
            if not sender or not sender.startswith(':'):
                raise Refused('invalid-request')
            # Refuse before taking a lock held by an effect or doing bus/file I/O.
            if method in MUTATIONS and (busy_on_arrival or self.inflight is not None):
                raise Refused('busy')
            with self.lock:
                uid = self._uid(sender)
                owner = self._binding(uid)
                if method in MUTATIONS and (busy_on_arrival or self.inflight is not None):
                    raise Refused('busy')
                snapshot = self._snapshot(uid)
                if method in ('Status', 'NextDevice'):
                    if method == 'NextDevice':
                        if self.inflight is not None and self.inflight.sender == sender:
                            raise Refused('busy')
                        ids = [d.record_id for d in snapshot.devices]
                        old = self.cursors.get(sender)
                        if ids:
                            self.cursors[sender] = ids[(ids.index(old) + 1) % len(ids)] if old in ids else ids[0]
                        else:
                            self.cursors.pop(sender, None)
                    selected = next((d for d in snapshot.devices if d.record_id == self.cursors.get(sender)), None)
                    self._reply(message, {'phase': snapshot.phase, 'ready': snapshot.ready,
                        'devices': [d.public() for d in snapshot.devices],
                        'pending': snapshot.pending.public() if snapshot.pending else None,
                        'selected': selected.public() if selected else None})
                    return False
                record = self._target(method, sender, snapshot)
                capture = Capture(method, sender, uid, owner, snapshot, record, self.clock() + self.deadline,
                                  uuid.uuid4().hex, message)
                self.inflight, self.cancelled = capture, False
                self.authorization_terminal = False
                public = record.public()
                verb = {'ApprovePending': 'Approve device', 'RejectPending': 'Reject device',
                        'RevokeSelected': 'Revoke device'}[method]
                details = {'polkit.message': f'{verb}: {public["label"]}; stable-ID fingerprint '
                           f'{public["fingerprint"]}; comparison phrase {public["comparison_phrase"]}.'}
                self.timer = GLib.timeout_add(max(1, int(self.deadline * 1000)), self._expired)
            # No store/helper lock is held while polkit waits for authentication.
            self.authority.call('CheckAuthorization', GLib.Variant('((sa{sv})sa{ss}us)', (
                ('system-bus-name', {'name': GLib.Variant('s', sender)}), ACTION_PREFIX + MUTATIONS[method],
                details, 1, capture.cancellation_id)), Gio.DBusCallFlags.NONE,
                int(self.deadline * 1000) + 2000, None, self._authorized, capture)
        except Exception as error:
            if isinstance(self.inflight, Capture) and self.inflight.message == message:
                self._cancel('unavailable')
            else:
                self._error(message, error.category if isinstance(error, (Refused, OwnerRefused)) else 'unavailable')
        return False

    def _clear(self) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer, self.inflight = 0, None
        self.authorization_terminal = False
        self.last_activity = self.clock()

    def _authorized(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult, capture: Capture) -> None:
        terminal = False
        try:
            authorization = proxy.call_finish(result)
            terminal = True
            granted = authorization.unpack()[0][0] is True
        except Exception as error:
            granted = False
            # A polkit error reply is terminal too; a transport timeout/loss is not.
            remote = Gio.DBusError.get_remote_error(error) if isinstance(error, GLib.Error) else None
            terminal = terminal or bool(remote and remote.startswith('org.freedesktop.PolicyKit1.Error.'))
        with self.lock:
            if self.inflight is not capture:
                return
            self.authorization_terminal = terminal
            if self.cancelled:
                if terminal:
                    self._clear()  # A late reply settles even an uncertain cancellation; never commit it.
                return
            if not terminal:
                self._cancel('unavailable')
                return
            try:
                if self.clock() >= capture.deadline:
                    self._cancel('expired')
                    return
                if not granted:
                    raise Refused('denied')
                if self._uid(capture.sender) != capture.uid:
                    raise Refused('wrong-uid')
                if self._binding(capture.uid) != capture.owner:
                    raise Refused('changed')
                current = self._snapshot(capture.uid)
                if current != capture.snapshot or self._target(capture.method, capture.sender, current) != capture.record:
                    raise Refused('changed')
                if self.clock() >= capture.deadline:
                    self._cancel('expired')
                    return
                # Connector revalidates the SAME snapshot/record under its trust mutation lock.
                self.control.mutate(capture.method, capture.snapshot, capture.record, capture.deadline,
                                    lambda: self._before_commit(capture))
                self._reply(capture.message, 'ok')
            except Exception as error:
                if isinstance(error, Refused) and error.category == 'expired':
                    self._cancel('expired')
                    return
                self._error(capture.message, error.category if isinstance(error, (Refused, OwnerRefused)) else 'unavailable')
            self._clear()

    def _before_commit(self, capture: Capture) -> None:
        """Runs while BOTH helper and connector trust mutation locks are held."""
        if (self.clock() >= capture.deadline
                or (capture.method != 'RevokeSelected' and capture.record.expires_at <= time.time())):
            raise Refused('expired')
        if self._uid(capture.sender) != capture.uid:
            raise Refused('wrong-uid')
        if self._binding(capture.uid) != capture.owner:
            raise Refused('changed')
        if self.inflight is not capture or self.cancelled:
            raise Refused('changed')
        if self.clock() >= capture.deadline:
            raise Refused('expired')

    def _expired(self) -> bool:
        self.timer = 0
        self._cancel('expired')
        return False

    def _cancel(self, category: str) -> None:
        with self.lock:
            capture = self.inflight
            if not isinstance(capture, Capture) or self.cancelled:
                return
            self.cancelled = True
            self._error(capture.message, category)
            if self.authorization_terminal:
                self._clear()  # polkit already removed this completed cancellation ID.
                return
            # Keep the global slot until the Authority acknowledges cancellation.
            self.authority.call('CancelCheckAuthorization', GLib.Variant('(s)', (capture.cancellation_id,)),
                Gio.DBusCallFlags.NONE, 2000, None, self._cancelled, capture)

    def _cancelled(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult, capture: Capture) -> None:
        try:
            proxy.call_finish(result)
        except Exception:
            return  # Seal while the outstanding check has no terminal reply or cancellation acknowledgement.
        with self.lock:
            if self.inflight is capture:
                self._clear()

    def _owner_changed(self, connection: Gio.DBusConnection, sender: str, path: str,
                       interface: str, signal: str, parameters: GLib.Variant, data: object) -> None:
        name, old, new = parameters.unpack()
        if name.startswith(':') and old and not new:
            with self.lock:
                self.cursors.pop(name, None)
                if isinstance(self.inflight, Capture) and self.inflight.sender == name:
                    self._cancel('disconnected')


def serve() -> None:
    """Fixed production bootstrap; caller credentials select the owner per call."""
    if os.geteuid() != 0:
        raise Refused('wrong-uid')
    connection = Gio.DBusConnection.new_for_address_sync(SYSTEM_BUS_ADDRESS,
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
    helper = RemoteAccessHelper(connection)
    loop = GLib.MainLoop()
    timer = GLib.timeout_add_seconds(1, helper._idle, loop)
    try:
        loop.run()
    finally:
        GLib.source_remove(timer)
        helper.close()
        connection.close_sync(None)


if __name__ == '__main__':
    if len(sys.argv) != 1:
        raise SystemExit('invalid-request')
    serve()
