"""Fixed no-input Gio service, intended for system Python -I as root.

The protected release UID is a mandatory constructor value supplied by the
root-owned package launcher, never a bus argument or environment/config value.
Package activation and reading the release record belong to slice 3b.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass

from gi.repository import Gio, GLib

from .helper_control import CONTROL_SOCKET, MUTATIONS, ControlClient, Device, Refused, Snapshot

BUS_NAME = 'ai.happyranch.RemoteAccess1'
OBJECT_PATH = '/ai/happyranch/RemoteAccess1'
INTERFACE = BUS_NAME
ACTION_PREFIX = 'ai.happyranch.remote-access.'
AUTH_DEADLINE = 120.0
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
    snapshot: Snapshot
    record: Device
    deadline: float
    cancellation_id: str
    message: Gio.DBusMessage


class RemoteAccessHelper:
    def __init__(self, connection: Gio.DBusConnection, release_uid: int,
                 control_path: str = CONTROL_SOCKET, clock=time.monotonic,
                 deadline: float = AUTH_DEADLINE) -> None:
        if type(release_uid) is not int or release_uid <= 0 or not 0 < deadline <= AUTH_DEADLINE:
            raise Refused('unavailable')
        self.connection, self.release_uid = connection, release_uid
        self.control = ControlClient(control_path)
        self.clock, self.deadline = clock, deadline
        self.lock = threading.RLock()
        self.cursors: dict[str, str] = {}
        self.inflight: Capture | None = None
        self.timer = 0
        self.cancelled = False
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

    def close(self) -> None:
        if self.inflight is not None:
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
            GLib.idle_add(self._dispatch, message)
            return None
        return message

    def _reply(self, message: Gio.DBusMessage, value: dict | str) -> None:
        reply = message.new_method_reply()
        reply.set_body(GLib.Variant('(s)', (json.dumps(value, separators=(',', ':')),)))
        self.connection.send_message(reply, Gio.DBusSendMessageFlags.NONE)

    def _error(self, message: Gio.DBusMessage, category: str) -> None:
        category = Refused(category).category
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

    def _snapshot(self) -> Snapshot:
        snapshot = self.control.status()
        if not snapshot.owned or snapshot.release_uid != self.release_uid:
            raise Refused('changed')
        return snapshot

    def _target(self, method: str, sender: str, snapshot: Snapshot) -> Device:
        target = (snapshot.pending if method != 'RevokeSelected' else
                  next((d for d in snapshot.devices if d.record_id == self.cursors.get(sender)), None))
        if target is None:
            raise Refused('changed')
        if target.expires_at <= time.time():
            raise Refused('expired')
        return target

    def _dispatch(self, message: Gio.DBusMessage) -> bool:
        try:
            fds = message.get_unix_fd_list()
            if message.get_signature() or (fds is not None and fds.get_length()):
                raise Refused('invalid-request')
            method, sender = message.get_member(), message.get_sender()
            if message.get_interface() != INTERFACE or method not in METHODS:
                raise Refused('unknown-method')
            if not sender or not sender.startswith(':'):
                raise Refused('invalid-request')
            with self.lock:
                uid = self._uid(sender)
                if uid != self.release_uid:
                    raise Refused('wrong-uid')
                if method in MUTATIONS and self.inflight is not None:
                    raise Refused('busy')
                snapshot = self._snapshot()
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
                capture = Capture(method, sender, uid, snapshot, record, self.clock() + self.deadline,
                                  uuid.uuid4().hex, message)
                self.inflight, self.cancelled = capture, False
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
            if self.inflight is not None and self.inflight.message == message:
                self._cancel('unavailable')
            else:
                self._error(message, error.category if isinstance(error, Refused) else 'unavailable')
        return False

    def _clear(self) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
        self.timer, self.inflight = 0, None

    def _authorized(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult, capture: Capture) -> None:
        try:
            authorization = proxy.call_finish(result)
            granted = authorization.unpack()[0][0] is True
        except Exception:
            granted = False
        with self.lock:
            if self.inflight is not capture or self.cancelled:
                return
            try:
                if self.clock() >= capture.deadline:
                    self._cancel('expired')
                    return
                if not granted:
                    raise Refused('denied')
                if self._uid(capture.sender) != capture.uid or capture.uid != self.release_uid:
                    raise Refused('wrong-uid')
                current = self._snapshot()
                if current != capture.snapshot or self._target(capture.method, capture.sender, current) != capture.record:
                    raise Refused('changed')
                if self.clock() >= capture.deadline:
                    self._cancel('expired')
                    return
                # Connector revalidates the SAME snapshot/record under its trust mutation lock.
                self.control.mutate(capture.method, capture.snapshot, capture.record)
                self._reply(capture.message, 'ok')
            except Exception as error:
                self._error(capture.message, error.category if isinstance(error, Refused) else 'unavailable')
            self._clear()

    def _expired(self) -> bool:
        self.timer = 0
        self._cancel('expired')
        return False

    def _cancel(self, category: str) -> None:
        with self.lock:
            capture = self.inflight
            if capture is None or self.cancelled:
                return
            self.cancelled = True
            self._error(capture.message, category)
            # Keep the global slot until the Authority acknowledges cancellation.
            self.authority.call('CancelCheckAuthorization', GLib.Variant('(s)', (capture.cancellation_id,)),
                Gio.DBusCallFlags.NONE, 2000, None, self._cancelled, capture)

    def _cancelled(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult, capture: Capture) -> None:
        try:
            proxy.call_finish(result)
        except Exception:
            return  # Uncertain prompt cancellation seals mutations until helper restart.
        with self.lock:
            if self.inflight is capture:
                self._clear()

    def _owner_changed(self, connection: Gio.DBusConnection, sender: str, path: str,
                       interface: str, signal: str, parameters: GLib.Variant, data: object) -> None:
        name, old, new = parameters.unpack()
        if name.startswith(':') and old and not new:
            with self.lock:
                self.cursors.pop(name, None)
                if self.inflight is not None and self.inflight.sender == name:
                    self._cancel('disconnected')


def serve(release_uid: int) -> None:
    """Fixed production bootstrap; only the protected launcher supplies the UID."""
    if os.geteuid() != 0:
        raise Refused('wrong-uid')
    connection = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    helper = RemoteAccessHelper(connection, release_uid)
    try:
        GLib.MainLoop().run()
    finally:
        helper.close()
