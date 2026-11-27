"""Fixed no-input Gio service, intended for system Python -I as root.

The installed entry binds each authenticated sender to its validated release
home before connector reads or authorization. Lifecycle calls use the same fixed public gate.
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
from dataclasses import dataclass, replace
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

# Stdlib-only package siblings; the receipt owner runs only in bundled Python -I.
for _name in ('status_v2', 'home_audit', 'helper_generation', 'helper_lifecycle', 'helper_invite'):
    _spec = importlib.util.spec_from_file_location(
        'happyranch_fixed_' + _name, Path(__file__).with_name(_name + '.py'))
    _module = importlib.util.module_from_spec(_spec)
    sys.modules[_spec.name] = _module
    _spec.loader.exec_module(_module)
    globals()['_' + _name] = _module

SYSTEM_BUS_ADDRESS = 'unix:path=/run/dbus/system_bus_socket'
BUS_NAME = 'ai.happyranch.RemoteAccess1'
OBJECT_PATH = '/ai/happyranch/RemoteAccess1'
INTERFACE = BUS_NAME
ACTION_PREFIX = 'ai.happyranch.remote-access.'
AUTH_DEADLINE = _helper_lifecycle.AUTH_DEADLINE
LIFECYCLE_ACTIONS = {'Enable': 'enable', 'ReplaceFailed': 'replace-failed', 'Disable': 'disable',
                     'RetryPolicyRenewal': 'retry-policy-renewal',
                     **_helper_invite.METHODS}
TERMINAL_METHODS = frozenset({'Enable', 'Disable', 'ReplaceFailed', 'CancelInvite',
                             'RecoverInvite', 'RetryPolicyRenewal'})
IDLE_SECONDS = 60.0
SERVICE_PATHS = tuple('/' + parent + '/happyranch-' + service
    for parent in ('var/lib', 'run') for service in ('headscale', 'connector', 'tsnet-sidecar'))
METHODS = frozenset({'Status', 'StatusV2', 'NextDevice', *MUTATIONS, *LIFECYCLE_ACTIONS})
INTROSPECTION = '<node><interface name="' + INTERFACE + '">' + ''.join(
    '<method name="' + name + '"><arg name="result" type="s" direction="out"/></method>'
    for name in sorted(METHODS)) + (
        '<signal name="LifecycleCompleted"><arg name="frame" type="s"/></signal></interface></node>')
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
        self.exit_requested = False
        self.name_owned = False
        self.lock = threading.RLock()
        self.cursors: dict[str, str] = {}
        self.inflight: Capture | LifecycleCapture | None = None
        self.generations = _helper_generation.HomeGenerationReader()
        self.audit = _home_audit.HomeAudit()
        self.lifecycle_operation: object | None = None
        self.lifecycle_result: dict | None = None
        self.invite_observation: tuple | None = None
        self.status_worker: object | None = None
        self.status_lock = threading.Lock()
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
        self.name_owned = True

    def try_claim(self, capture: LifecycleCapture) -> bool:
        """Inert lifecycle adapter to the existing global slot; never queues."""
        if not self.lock.acquire(blocking=False):
            return False
        try:
            if self.exit_requested or self.inflight is not None:
                return False
            self.inflight = capture
            self.last_activity = self.clock()
            return True
        finally:
            self.lock.release()

    @contextmanager
    def effect_guard(self, capture: LifecycleCapture) -> Iterator[None]:
        # Workers may briefly lose to a main-context status/admission reader.
        # The original operation budget is revalidated after this bounded wait.
        if not self.lock.acquire(timeout=1.0):
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
                if capture.method in LIFECYCLE_ACTIONS:
                    self.exit_requested = True
                self.inflight = None
                self.last_activity = self.clock()

    def _idle(self, loop: GLib.MainLoop) -> bool:
        # A worker can hold the mutation lock through a blocking effect. Never
        # hold the main context waiting for it, or exit before its settlement.
        if not self.lock.acquire(blocking=False):
            return True
        try:
            with self.status_lock:
                if (self.inflight is None and self.status_worker is None
                        and (self.exit_requested or self.clock() - self.last_activity >= IDLE_SECONDS)):
                    self.exit_requested = True
                    try:
                        self._release_name()
                    except Exception:
                        return True  # Stay sealed; retry release on the next tick.
                    loop.quit()
        finally:
            self.lock.release()
        return True  # serve removes the source even on the idle-exit path

    def _release_name(self) -> None:
        if self.name_owned:
            self.connection.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'ReleaseName', GLib.Variant('(s)', (BUS_NAME,)),
                GLib.VariantType.new('(u)'), Gio.DBusCallFlags.NONE, 2000, None)
            self.name_owned = False

    def close(self) -> None:
        self.exit_requested = True
        if isinstance(self.inflight, (Capture, _helper_lifecycle.LifecycleCapture)):
            self._cancel('unavailable')
        self._release_name()
        self.connection.remove_filter(self.filter_id)
        self.connection.signal_unsubscribe(self.subscription)
        self.connection.unregister_object(self.registration)
        self.cursors.clear()

    def _filter(self, connection: Gio.DBusConnection, message: Gio.DBusMessage,
                incoming: bool, data: object) -> Gio.DBusMessage | None:
        if (incoming and message.get_message_type() == Gio.DBusMessageType.METHOD_CALL
                and message.get_path() == OBJECT_PATH):
            # Gio filters run on its message thread. Mutations use the main context;
            # StatusV2 starts its bounded read worker directly below.
            # Preserve refusal at arrival even if main-context dispatch is busy
            # finishing the first operation. Never queue a second surprise dialog.
            busy = self.inflight is not None
            if message.get_member() == 'StatusV2':
                self._dispatch(message, busy)  # Read-only worker never waits on the main context.
            else:
                GLib.idle_add(self._dispatch, message, busy)
            return None
        return message

    def _reply(self, message: Gio.DBusMessage, value: dict | str) -> None:
        reply = message.new_method_reply()
        reply.set_body(GLib.Variant('(s)', (json.dumps(value, separators=(',', ':')),)))
        self.connection.send_message(reply, Gio.DBusSendMessageFlags.NONE)

    def _error(self, message: Gio.DBusMessage, category: str) -> None:
        category = (category if category in _owner.FAILURES or
                    (category == 'uncertain' and message.get_member() == 'IssueInvite')
                    else Refused(category).category)
        LOG.info('remote-access-helper %s', category)
        reply = message.new_method_error_literal(INTERFACE + '.Error.' + category.replace('-', '_'), category)
        self.connection.send_message(reply, Gio.DBusSendMessageFlags.NONE)

    def _status_v2(self, message: Gio.DBusMessage, sender: str, *, v1: bool = False) -> None:
        # One worker, no queue, no helper mutation lock. Slow filesystem/control
        # reads cannot hold the main context beyond the 2s response deadline.
        token = object()
        with self.status_lock:
            if self.exit_requested:
                raise Refused('unavailable')
            if self.status_worker is not None:
                raise Refused('busy')
            self.status_worker = token
        deadline = time.monotonic() + 2.0
        replied = False
        reply_lock = threading.Lock()

        def finish(raw: bytes | None, category: str | None = None) -> bool:
            nonlocal replied
            with reply_lock:
                if not replied:
                    replied = True
                    if raw is None or time.monotonic() >= deadline:
                        self._error(message, category or 'expired')
                    else:
                        reply = message.new_method_reply()
                        reply.set_body(GLib.Variant('(s)', (raw.decode('utf-8'),)))
                        self.connection.send_message(reply, Gio.DBusSendMessageFlags.NONE)
            return False

        def settled(raw: bytes | None, category: str | None) -> None:
            timer.cancel()
            try:
                finish(raw, category)
            finally:
                with self.status_lock:
                    if self.status_worker is token:
                        self.status_worker = None

        def observe() -> None:
            raw, category = None, None
            try:
                uid = self._uid(sender)
                self._binding(uid)  # Same B8 custody gate as Status v1; no polkit.
                setup = None
                try:
                    observed = self.generations.current_generation(min(.75, deadline - time.monotonic()))
                except _helper_generation.GenerationUnavailable:
                    observed = None
                observed_at = time.monotonic()
                if observed is not None:
                    if observed.release_uid != uid:
                        raise Refused('changed')
                    phase = ('ready' if observed.phase == 'ready' else
                             'uncertain' if observed.phase == 'failed_uncertain' else 'in_progress')
                    setup = _status_v2.SetupObservation(_status_v2.SetupPhase(phase), 0)
                closed = False
                if observed is None:
                    snapshot, closed, phase = self._status_snapshot(uid, deadline)
                else:
                    try:
                        snapshot, closed, phase = self._status_snapshot(uid, deadline)
                    except Refused as error:
                        if error.category != 'unavailable':
                            raise
                        snapshot = None
                if snapshot is not None:
                    setup = _status_v2.SetupObservation(_status_v2.SetupPhase(
                        'in_progress' if phase == 'pending' else phase), 0)
                    observed_at = time.monotonic()
                if closed:
                    setup = _status_v2.SetupObservation(_status_v2.SetupPhase.OFF, 0)
                    observed_at = time.monotonic()
                    connector = _status_v2.ConnectorObservation(_status_v2.ConnectorState.STOPPED, 0)
                    runtime = None
                elif snapshot is None:
                    connector = _status_v2.ConnectorObservation(_status_v2.ConnectorState.UNREACHABLE, 0)
                    runtime = None
                else:
                    connector = _status_v2.ConnectorObservation(_status_v2.ConnectorState.RUNNING, 0)
                    runtime = _status_v2.RuntimeObservation(
                        _status_v2.RuntimeAvailability.AVAILABLE if snapshot.ready else
                        _status_v2.RuntimeAvailability.UNAVAILABLE, 0)
                if v1:
                    if snapshot is None:
                        raise Refused('unavailable')
                    selected = next((d for d in snapshot.devices if d.record_id == self.cursors.get(sender)), None)
                    phase = {'prepared_no_receipt': 'pending', 'failed_uncertain': 'failed',
                        'partial_cleanup': 'failed', 'runtime_unavailable': 'failed',
                        'setup_unverified': 'failed', 'renewal_failed': 'paused'}.get(snapshot.phase, snapshot.phase)
                    raw = json.dumps({'phase': phase, 'ready': snapshot.ready,
                        'devices': [d.public() for d in snapshot.devices],
                        'pending': snapshot.pending.public() if snapshot.pending else None,
                        'selected': selected.public() if selected else None}, separators=(',', ':')).encode()
                    return settled(raw, None)
                if setup is not None:
                    setup = _status_v2.SetupObservation(setup.state, int(time.monotonic() - observed_at))
                invite, cleanup = self._invite_status(uid, observed, deadline)
                policy = None
                try:
                    state = _helper_generation.HomeLifecycleClient('RetryPolicyRenewal', uid).policy_status(
                        deadline - time.monotonic())
                    policy = _status_v2.PolicyObservation(_status_v2.PolicyState(state['policy']), 0)
                except Exception:
                    pass  # Unavailable evidence remains unknown, never current.
                firewall = None
                try:
                    # Keep encoding/delivery ahead of the 1.9s watchdog within
                    # the original 2s deadline. Advice must not erase facts.
                    remaining = deadline - time.monotonic() - .3
                    if remaining > .1:
                        state = _helper_generation.HomeLifecycleClient('Enable', uid).firewall_status(remaining)
                        if state != 'unknown':
                            firewall = _status_v2.FirewallObservation(_status_v2.FirewallState(state), 0)
                except Exception:
                    pass  # No firewall evidence must not change setup or runtime.
                raw = _status_v2.encode(_status_v2.build_status(now_utc_seconds=int(time.time()),
                    setup=setup, connector=connector, runtime=runtime, invite=invite, cleanup=cleanup, policy=policy, firewall=firewall))
            except Exception as error:
                category = error.category if isinstance(error, (Refused, OwnerRefused)) else 'unavailable'
            settled(raw, category)

        timer = threading.Timer(1.9, finish, (None, 'expired'))
        timer.daemon = True
        timer.start()
        threading.Thread(target=observe, daemon=True).start()

    def _invite_status(self, uid: int, observed: object, deadline: float) -> tuple:
        observation = getattr(self, 'invite_observation', None)
        if observation is None or observed is None or observation[0] != uid:
            if observed is not None and observed.release_uid == uid:
                try:
                    state = _helper_generation.HomeLifecycleClient('IssueInvite', uid).invite_status(
                        observed, deadline - time.monotonic())
                    return (_status_v2.InviteObservation(_status_v2.InvitePhase(state['invite']),
                                0, state['expires_at']), None)
                except Exception:
                    pass  # Missing/unsafe journal is never absence or cleanup proof.
            return None, None
        expected = json.loads(observation[1])
        if (expected['generation'], expected['operation'], uid) != (
                observed.generation, observed.operation, observed.release_uid):
            return None, None
        result, at = observation[2:]
        age = min(_status_v2.MAX_AGE_SECONDS, max(0, int(time.monotonic() - at)))
        invite = _status_v2.InvitePhase(result['invite'])
        cleanup = _status_v2.CleanupState(result['cleanup'])
        return (_status_v2.InviteObservation(invite, None if invite.value == 'unknown' else age,
                    result.get('expires_at')),
                _status_v2.CleanupObservation(cleanup, None if cleanup.value == 'unknown' else age))

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

    def _status_snapshot(self, uid: int, deadline: float) -> tuple[Snapshot, bool, str]:
        """Display-only absence proof; never supplies a mutation's snapshot.

        A missing connector proves no setup phase. Reuse the unchanged Enable
        capture's custody, provenance, residue and quiescence checks before
        reporting Off. Capture reads only; it neither claims a slot nor repairs.
        """
        try:
            snapshot = self._snapshot(uid)
            state = _helper_generation.HomeLifecycleClient('Enable', uid).setup_status(
                deadline - time.monotonic())['setup']
            if state == 'ready' and not snapshot.ready:
                state = 'runtime_unavailable'
            if state != 'ready':
                snapshot = replace(snapshot, phase=state, ready=False)
            return snapshot, False, state
        except Refused as error:
            if error.category != 'unavailable':
                raise
        try:
            raw = _helper_generation.HomeLifecycleClient('Enable', uid).current_generation(
                deadline - time.monotonic())
            state = json.loads(raw)
            if (type(state) is not dict or set(state) != {
                    'uid', 'activation', 'generation', 'replacement', 'configuration', 'prestate', 'inventory'}
                    or type(state['uid']) is not int or state['uid'] != uid
                    or state['activation'] is not None or state['replacement'] is not None
                    or state['configuration'] is not None or type(state['generation']) is not int
                    or state['generation'] != 0
                    or state['prestate'] not in ('clean', 'staged-inert', 'post-disable')
                    or type(state['inventory']) is not str or len(state['inventory']) != 64
                    or any(c not in '0123456789abcdef' for c in state['inventory'])):
                raise ValueError()
            if time.monotonic() >= deadline:
                raise ValueError()
        except Exception as error:
            # A ready receipt can outlive stopped units or B6's failed-revoke
            # partial publication. This is display only, never Off/revoke proof.
            try:
                state = _helper_generation.HomeLifecycleClient('Enable', uid).setup_status(
                    deadline - time.monotonic())['setup']
                if state == 'off':
                    raise ValueError()  # Keep the strict absence proof above.
                if state == 'ready':
                    state = 'runtime_unavailable'
                return Snapshot(0, uid, False, state, False, None, ()), False, state
            except Exception:
                if isinstance(error, _helper_generation.GenerationUnavailable):
                    category = 'unverified' if error.category == 'unknown' else error.category
                    raise Refused('host-state-unrecognized-' + category) from None
                raise Refused('host-state-unrecognized-unverified') from None
        return Snapshot(0, uid, True, 'off', False, None, ()), True, 'off'

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
            if self.exit_requested:
                raise Refused('unavailable')
            fds = message.get_unix_fd_list()
            if message.get_signature() or (fds is not None and fds.get_length()):
                raise Refused('invalid-request')
            method, sender = message.get_member(), message.get_sender()
            if message.get_interface() != INTERFACE or method not in METHODS:
                raise Refused('unknown-method')
            if not sender or not sender.startswith(':'):
                raise Refused('invalid-request')
            if method in LIFECYCLE_ACTIONS:
                if busy_on_arrival:
                    raise Refused('busy')
                self._start_lifecycle(message)
                return False
            if method == 'StatusV2':
                self._status_v2(message, sender)
                return False
            if method == 'Status':
                self._status_v2(message, sender, v1=True)
                return False
            # Refuse before taking a lock held by an effect or doing bus/file I/O.
            if method in MUTATIONS and (busy_on_arrival or self.inflight is not None):
                raise Refused('busy')
            with self.lock:
                if self.exit_requested:
                    raise Refused('unavailable')
                uid = self._uid(sender)
                owner = self._binding(uid)
                if method in MUTATIONS and (busy_on_arrival or self.inflight is not None):
                    raise Refused('busy')
                try:
                    snapshot = self._snapshot(uid)
                except Refused as error:
                    if method not in MUTATIONS or error.category != 'unavailable':
                        raise
                    # Use the existing absence proof only to refuse. Its
                    # display snapshot never admits a device mutation.
                    _, closed, _ = self._status_snapshot(uid, time.monotonic() + 2.0)
                    if closed:
                        raise Refused('host-state-unrecognized-not_applicable') from None
                    raise Refused('host-state-unrecognized-unverified') from None
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
            if message.get_member() in ('Disable', 'ReplaceFailed'):
                _helper_lifecycle.log_lifecycle_failure(error,
                    action='disable' if message.get_member() == 'Disable' else 'replace-failed', step='capture')
            if message.get_member() == 'Enable':
                _helper_lifecycle.log_enable_refusal(error)
            if isinstance(self.inflight, Capture) and self.inflight.message == message:
                self._cancel('unavailable')
            else:
                if isinstance(error, _helper_generation.GenerationUnavailable) and message.get_member() in LIFECYCLE_ACTIONS:
                    category = 'unverified' if error.category == 'unknown' else error.category
                    self._error(message, 'host-state-unrecognized-' + category)
                else:
                    self._error(message, error.category if isinstance(error, (Refused, OwnerRefused)) else 'unavailable')
            if message.get_member() in LIFECYCLE_ACTIONS and self.inflight is None:
                self.exit_requested = True
        return False

    def _clear(self) -> None:
        if self.timer:
            GLib.source_remove(self.timer)
        if isinstance(self.inflight, _helper_lifecycle.LifecycleCapture):
            self.exit_requested = True
        self.timer, self.inflight = 0, None
        self.authorization_terminal = False
        self.last_activity = self.clock()

    def _start_lifecycle(self, message: Gio.DBusMessage) -> None:
        """Capture and authorize a fixed public lifecycle action before effects."""
        method, sender = message.get_member(), message.get_sender()
        fds = message.get_unix_fd_list()
        if (method not in LIFECYCLE_ACTIONS or message.get_interface() != INTERFACE
                or message.get_signature() or (fds is not None and fds.get_length())):
            raise Refused('invalid-request')
        if self.inflight is not None:
            raise Refused('busy')
        if self.exit_requested:
            raise Refused('unavailable')
        budget = _helper_lifecycle.OperationBudget.lifecycle(method, self.clock)
        admission = budget.bounded(_helper_lifecycle.CALL_SECONDS)
        # Optional ReadWritePaths absent at helper start are not mounted RW.
        # Observe each path before capture/polkit/B16; absence needs no cleanup.
        try:
            for path in SERVICE_PATHS:
                try:
                    readonly = os.statvfs(path).f_flag & os.ST_RDONLY
                except FileNotFoundError:
                    continue
                if readonly:
                    raise Refused('unavailable')
        except Exception as error:
            if method == 'Enable':
                _helper_lifecycle.log_enable_refusal(error)
            self.exit_requested = True
            raise Refused('unavailable') from None
        uid = self._uid(sender)
        client = _helper_generation.HomeLifecycleClient(method, uid)
        capture = _helper_lifecycle.LifecycleCapture.take(method, sender, self, client, admission)
        if capture.uid != uid:
            raise Refused('wrong-uid')
        capture = replace(capture, deadline=min(admission.deadline, self.clock() + AUTH_DEADLINE),
                          cancellation_id=uuid.uuid4().hex, message=message)
        if not self.try_claim(capture):
            raise Refused('busy')
        operation = _helper_lifecycle.LifecycleOperation(capture, self, client, budget)
        self.lifecycle_operation = operation
        self.cancelled = self.authorization_terminal = False
        try:
            # Recheck the SAME original admission budget after every capture read.
            remaining = min(AUTH_DEADLINE, admission.remaining())
            self.timer = GLib.timeout_add(max(1, int(remaining * 1000)), self._expired)
            verb = {'Enable': 'Enable home remote access', 'ReplaceFailed':
                    'Replace failed remote access; existing pairings will be lost',
                    'Disable': 'Turn off home remote access and revoke all devices',
                    'CancelInvite': 'Cancel the current home remote access invite',
                    'RecoverInvite': 'Recover the current home remote access invite',
                    'RetryPolicyRenewal': 'Retry the fixed home remote access policy renewal',
                    'IssueInvite': 'Issue a home remote access invite'}[method]
            self.authority.call('CheckAuthorization', GLib.Variant('((sa{sv})sa{ss}us)', (
                ('system-bus-name', {'name': GLib.Variant('s', sender)}),
                ACTION_PREFIX + LIFECYCLE_ACTIONS[method], {'polkit.message': verb},
                1, capture.cancellation_id)), Gio.DBusCallFlags.NONE,
                max(1, int(remaining * 1000)) + 2000, None, self._authorized, capture)
        except Exception as error:
            if method == 'Enable':
                _helper_lifecycle.log_enable_refusal(error)
            self._cancel('unavailable')

    def _lifecycle_authorized(self, capture: object, terminal: bool, granted: bool) -> None:
        with self.lock:
            if self.inflight is not capture:
                return
            self.authorization_terminal = terminal
            if self.cancelled:
                if terminal:
                    self._clear()
                return
            if not terminal:
                self._cancel('unavailable')
                return
            operation = self.lifecycle_operation
            try:
                if self.clock() >= capture.deadline:
                    raise Refused('expired')
                if not granted:
                    raise Refused('denied')
                operation._revalidate(operation.budget.bounded(_helper_lifecycle.CALL_SECONDS))
                if self.clock() >= capture.deadline:
                    raise Refused('expired')
                operation.accepted = True
                if self.timer:
                    GLib.source_remove(self.timer)
                self.timer = 0  # Auth has settled; original operation budget continues.
                if capture.method != 'IssueInvite':
                    self._reply(capture.message, 'accepted')
                worker = threading.Thread(target=self._run_lifecycle, args=(operation,), daemon=True)
            except Exception as error:
                if capture.method == 'Enable':
                    _helper_lifecycle.log_enable_refusal(error)
                if capture.method != 'Enable' or not operation.accepted:
                    self._error(capture.message, error.category if isinstance(error, Refused) else 'changed')
                self._clear()
                return
        # The slot stays captured, but the worker must not contend with its own
        # authorization callback's lock at the nonblocking effect/settlement guards.
        try:
            worker.start()
        except Exception as error:
            with self.lock:
                if capture.method == 'Enable':
                    _helper_lifecycle.log_enable_refusal(error)
                if capture.method != 'Enable' or not operation.accepted:
                    self._error(capture.message, error.category if isinstance(error, Refused) else 'changed')
                self._clear()

    def _emit_enable_terminal(self, operation: object, outcome: object, category: str | None,
                              firewall: str | None = None) -> None:
        """One receive-only receipt, still held by the original worker/slot."""
        capture = operation.capture
        budget = operation.budget
        if (capture.method not in TERMINAL_METHODS or not operation.accepted or self.cancelled
                or self.inflight is not capture or budget.remaining() <= 2.0):
            return
        # _uid retains its fixed 2s authentication read. Never start it with less
        # budget, and recheck after each potentially blocking receipt read.
        if self._uid(capture.sender) != capture.uid:
            return
        budget.remaining()
        if self._binding(capture.uid) != capture.owner:
            return
        budget.remaining()
        if self.cancelled or self.inflight is not capture:
            return
        frame = dict(method=capture.method, correlation=dict(serial=capture.message.get_serial(),
            owner=self.connection.get_unique_name()), outcome='failed')
        if (category is None and capture.method == 'Enable'
                and outcome in (_helper_lifecycle.SetupPhase.FAILED, _helper_lifecycle.SetupPhase.UNCERTAIN)):
            category = 'activation-unverified'
        elif (category is None and capture.method not in ('Enable', 'Disable')
                and outcome in (_helper_lifecycle.SetupPhase.FAILED, _helper_lifecycle.SetupPhase.UNCERTAIN,
                                _helper_lifecycle.SetupPhase.PARTIAL_CLEANUP)):
            category = outcome.value
        if category is not None:
            frame['category'] = category
        elif capture.method == 'Disable' and outcome != _helper_lifecycle.SetupPhase.OFF:
            frame['category'] = ('partial_cleanup' if outcome == _helper_lifecycle.SetupPhase.PARTIAL_CLEANUP
                                 else 'uncertain' if outcome == _helper_lifecycle.SetupPhase.UNCERTAIN else 'failed')
        elif outcome not in (_helper_lifecycle.SetupPhase.FAILED, _helper_lifecycle.SetupPhase.UNCERTAIN):
            frame['outcome'] = 'completed'
        if capture.method == 'Enable' and firewall in ('likely_blocked', 'unverified', 'clear'):
            frame['firewallWarning'] = firewall
        raw = json.dumps(frame, separators=(',', ':'))
        budget.remaining()
        self.connection.emit_signal(capture.sender, OBJECT_PATH, INTERFACE,
                                    'LifecycleCompleted', GLib.Variant('(s)', (raw,)))
        # Flush on this worker's own main context. The app context never waits
        # for a worker that depends on it, and there is no new grace period.
        remaining = budget.remaining()
        context = GLib.MainContext.new()
        context.push_thread_default()
        loop = GLib.MainLoop.new(context, False)
        cancellable = Gio.Cancellable()
        timer = GLib.timeout_source_new(max(1, int(remaining * 1000)))
        def expired(*_: object) -> bool:
            cancellable.cancel()
            loop.quit()
            return False
        def flushed(connection: object, result: object, data: object) -> None:
            try:
                connection.flush_finish(result)
            except Exception:
                pass  # Delivery loss never alters effect or journal evidence.
            finally:
                loop.quit()
        try:
            timer.set_callback(expired)
            timer.attach(context)
            self.connection.flush(cancellable, flushed, None)
            loop.run()
            budget.remaining()
        finally:
            timer.destroy()
            cancellable.cancel()
            context.pop_thread_default()

    def _run_lifecycle(self, operation: object) -> None:
        if operation.capture.method == 'RetryPolicyRenewal':
            self._run_policy_renewal(operation)
            return
        if operation.capture.method == 'IssueInvite':
            self._run_issue_invite(operation)
            return
        if operation.capture.method in _helper_invite.METHODS:
            self._run_invite(operation)
            return
        lifecycle = _helper_lifecycle
        capture, client = operation.capture, operation.generations
        residue: list[str] = []
        outcome = lifecycle.SetupPhase.UNCERTAIN
        preparation_category, send_attempted = None, False
        firewall = None

        def deliver(settled: object) -> None:
            nonlocal send_attempted, firewall
            if send_attempted:
                return
            send_attempted = True
            # Outcome is already settled by complete(). Reserve the original
            # terminal/auth budget; advice has no effect on operation success.
            if capture.method == 'Enable':
                try:
                    if operation.budget.remaining() > 6.0:
                        observed = client.firewall_status(2.0)
                        if observed in ('likely_blocked', 'unverified', 'clear'):
                            firewall = observed
                except Exception:
                    firewall = 'unverified'
            try:
                self._emit_enable_terminal(operation, settled, preparation_category, firewall)
            except Exception:
                pass  # One attempt; receipt loss does not settle effects.

        def audited(step: str, component: object, effect: Callable[[float, Callable[[], None]], dict]) -> dict:
            result = {}
            executed = False
            def execute(_: object, remaining: float) -> None:
                nonlocal result, executed
                executed = True
                label = '-'.join(str(byte) for byte in os.urandom(12))
                kwargs = ({'component': component} if capture.method == 'Disable' else
                          {'phase': _home_audit.Phase.ACTIVATING})
                intent = self.audit.intent(_home_audit.Event.TURN_OFF if capture.method == 'Disable'
                    else _home_audit.Event.ACTIVATION, intent_id='intent-' + label,
                    operation_id='op-' + label, **kwargs)
                dispatched = False
                def before_commit() -> None:
                    nonlocal dispatched
                    operation._revalidate()
                    dispatched = True
                try:
                    result = effect(remaining, before_commit)
                    operation.budget.remaining()
                except Exception:
                    if dispatched:
                        self.audit.uncertain(intent)
                    else:
                        self.audit.outcome(intent, _home_audit.Outcome.REFUSED)
                    raise
                try:
                    self.audit.outcome(intent, _home_audit.Outcome.SUCCEEDED)
                except Exception:
                    self.audit.uncertain(intent)
                    raise
            # A failed step must not prevent Disable's remaining safety stop.
            # Preserve failure locally; the final closed result remains partial.
            previous = operation.outcome
            try:
                operation.run_step(execute)
                if not executed:
                    raise Refused('unavailable')
            finally:
                if capture.method == 'Disable':
                    operation.outcome = previous
            return result

        def child(step: str, component: object, *, revoke_succeeded: bool = False) -> dict:
            def effect(remaining: float, commit: Callable[[], None]) -> dict:
                nonlocal preparation_category
                commit()
                options = {'revoke_succeeded': revoke_succeeded} if step == 'cleanup' else {}
                try:
                    return client.effect(step, capture.generation, min(remaining, operation.budget.remaining()), **options)
                except _helper_generation.PreparationFailed as error:
                    if capture.method == 'Enable' and step == 'enable':
                        preparation_category = error.category
                    raise
            return audited(step, component, effect)

        try:
            if capture.method != 'Disable':
                step = 'enable' if capture.method == 'Enable' else 'replace'
                result = child(step, None)
                # B7 rollover publishes prepared, which B15 projects as in_progress.
                outcome = (lifecycle.SetupPhase.IN_PROGRESS
                           if capture.method == 'Enable' and result['outcome'] == 'prepared'
                           else lifecycle.SetupPhase(result['outcome']))
                residue = result['residue']
                if capture.method == 'Enable' and result['outcome'] == 'prepared':
                    preparation_category = 'activation-prepared-retry'
            else:
                revoked = False
                def revoke(remaining: float, commit: Callable[[], None]) -> dict:
                    snapshot = self._snapshot(capture.uid)
                    deadline = min(operation.budget.deadline, self.clock() + min(remaining, 120.0))
                    # R4: one changed-generation reread/retry only, same deadline.
                    for attempt in range(2):
                        try:
                            self.control.revoke_all(snapshot.generation, deadline, commit)
                            return {}
                        except Refused as error:
                            if error.category != 'changed' or attempt:
                                raise
                            snapshot = self._snapshot(capture.uid)
                    raise Refused('changed')
                try:
                    audited('revoke', _home_audit.Component.CREDENTIALS, revoke)
                    revoked = True
                except Exception as error:
                    lifecycle.log_lifecycle_failure(error, action='disable', step='revoke')
                    residue.append('un-revoked connector trust state and persisted sidecar state')
                try:
                    stopped = child('stop', _home_audit.Component.UNITS)
                except Exception as error:
                    lifecycle.log_lifecycle_failure(error, action='disable', step='stop')
                    residue.append('owned units: quiescence unconfirmed')
                else:
                    if stopped.get('outcome') != 'in_progress' or stopped.get('residue'):
                        residue.extend(stopped.get('residue') or ['owned units: quiescence unconfirmed'])
                    else:
                        try:
                            result = child('cleanup', _home_audit.Component.LIVE_STATE, revoke_succeeded=revoked)
                            residue.extend(result['residue'])
                            if result['outcome'] != 'off':
                                residue.append('B6 cleanup incomplete')
                        except Exception as error:
                            lifecycle.log_lifecycle_failure(error, action='disable', step='cleanup')
                            residue.append('B6 custody or retained CA staging and B7/B11 evidence')
                outcome = (lifecycle.SetupPhase.OFF if revoked and not residue else
                           lifecycle.SetupPhase.PARTIAL_CLEANUP)
        except Exception as error:
            lifecycle.log_lifecycle_failure(error,
                action={'Enable': 'enable', 'Disable': 'disable', 'ReplaceFailed': 'replace-failed'}[capture.method],
                step='enable' if capture.method == 'Enable' else 'replace' if capture.method == 'ReplaceFailed' else 'stop')
            outcome = lifecycle.SetupPhase.UNCERTAIN if operation.dispatched else lifecycle.SetupPhase.FAILED
        finally:
            # Main-context lock holders never join this worker. Wait here after
            # all effects return, so transient contention cannot strand its slot.
            with self.lock:
                try:
                    settled = operation.complete(outcome, before_release=deliver)
                    self.lifecycle_result = {'outcome': settled.value, 'residue': residue}
                except Exception:
                    deliver(lifecycle.SetupPhase.UNCERTAIN)
                    self.lifecycle_result = {'outcome': 'uncertain', 'residue': residue}
                finally:
                    # A failed completion is uncertain, never synthetic success.
                    # Seal admission and let the next activation read the journal.
                    self.exit_requested = True
                    if firewall is not None:
                        self.lifecycle_result['firewallWarning'] = firewall
                    self.release(capture)

    def _run_policy_renewal(self, operation: object) -> None:
        capture = operation.capture
        result = {'outcome': 'refused'}
        intent, dispatched = None, False
        try:
            with self.effect_guard(capture):
                operation._revalidate()
                label = '-'.join(str(byte) for byte in os.urandom(12))
                intent = self.audit.intent(_home_audit.Event.POLICY_RENEW,
                    intent_id='intent-' + label, operation_id='op-' + label)

                def before_commit() -> None:
                    if self.cancelled or self.inflight is not capture:
                        raise Refused('disconnected')
                    operation._revalidate()

                before_commit()
                dispatched = True
                result = operation.generations.retry_policy(capture.generation,
                    operation.budget.deadline, before_commit)
                operation.budget.remaining()
                if type(result) is not dict or set(result) != {'outcome'} or result['outcome'] not in ('complete', 'refused'):
                    raise Refused('unavailable')
                self.audit.outcome(intent, _home_audit.Outcome.SUCCEEDED if result['outcome'] == 'complete'
                                   else _home_audit.Outcome.REFUSED)
                # B16 and this last blocking UID/B8/G/O read share the original
                # deadline. Nothing blocking follows the final validation.
                before_commit()
                operation.budget.remaining()
        except Exception:
            result = {'outcome': 'uncertain' if dispatched else 'refused'}
            if intent is not None:
                try:
                    if dispatched:
                        self.audit.uncertain(intent)
                    else:
                        self.audit.outcome(intent, _home_audit.Outcome.REFUSED)
                except Exception:
                    pass
        finally:
            self.lifecycle_result = result
            try:
                self._emit_enable_terminal(operation, result['outcome'],
                    None if result['outcome'] == 'complete' else result['outcome'])
            except Exception:
                pass  # A lost terminal receipt never changes the settled worker result.
            finally:
                self.release(capture)

    def _run_issue_invite(self, operation: object) -> None:
        """One authenticated terminal reply; secret never enters helper state."""
        capture = operation.capture
        intent, dispatched, reply_attempted = None, False, False
        result = {'outcome': 'refused', 'invite': 'unknown', 'cleanup': 'unknown'}
        category = 'unavailable'
        try:
            with self.effect_guard(capture):
                operation._revalidate()
                label = '-'.join(str(byte) for byte in os.urandom(12))
                intent = self.audit.intent(_home_audit.Event.INVITE_ISSUED,
                    intent_id='intent-' + label, operation_id='op-' + label)

                def before_commit() -> None:
                    if self.cancelled or self.inflight is not capture:
                        raise Refused('disconnected')
                    operation._revalidate()

                before_commit()
                dispatched = True  # Child transport loss cannot prove no mint.
                frame = operation.generations.issue_invite(capture.generation,
                    operation.budget.deadline, before_commit)
                operation.budget.remaining()
                if frame == {'result': 'rate_limited'}:
                    dispatched = False  # Child positively proved a pre-effect refusal.
                    raise Refused('host-state-unrecognized-invite_rate_limited')
                if frame == {'result': 'refused'}:
                    dispatched = False  # Child positively proved refusal.
                    raise Refused('unavailable')
                _helper_invite.validate_issued(frame)
                before_commit()
                self.audit.outcome(intent, _home_audit.Outcome.SUCCEEDED)
                # B16 fsync and reply validation consume the SAME original budget.
                before_commit()
                _helper_invite.validate_issued(frame)  # Final blocking read may outlive the invite.
                operation.budget.remaining()
                result = {'outcome': 'complete', 'invite': 'issued', 'cleanup': 'not_needed',
                          'expires_at': frame['expires_at']}
                reply_attempted = True
                self._reply(capture.message, frame)
        except Exception as error:
            result = ({'outcome': 'uncertain', 'invite': 'uncertain', 'cleanup': 'uncertain'} if dispatched
                      else {'outcome': 'refused', 'invite': 'unknown', 'cleanup': 'unknown'})
            category = 'uncertain' if dispatched else (error.category if isinstance(error, Refused) else 'unavailable')
            if intent is not None:
                try:
                    if dispatched:
                        self.audit.uncertain(intent)
                    else:
                        self.audit.outcome(intent, _home_audit.Outcome.REFUSED)
                        operation.budget.remaining()
                except Exception:
                    if dispatched:
                        category = 'uncertain'
            if not reply_attempted:
                reply_attempted = True  # A failed/lost send never authorizes another reply.
                self._error(capture.message, category)
        finally:
            self.lifecycle_result = result
            # A positive refusal observed no invite-state effect. Preserve the
            # earlier observation; a fresh helper still uses the existing journal.
            if result['invite'] != 'unknown':
                self.invite_observation = (capture.uid, capture.generation, result, time.monotonic())
            self.release(capture)  # Held through the sole terminal reply attempt.

    def _run_invite(self, operation: object) -> None:
        capture = operation.capture
        result = {'outcome': 'refused', 'invite': 'unknown', 'cleanup': 'unknown'}
        intent, dispatched = None, False
        try:
            with self.effect_guard(capture):
                operation._revalidate()
                label = '-'.join(str(byte) for byte in os.urandom(12))
                event = (_home_audit.Event.INVITE_CANCELLED if capture.method == 'CancelInvite'
                         else _home_audit.Event.INVITE_RECOVERED)
                intent = self.audit.intent(event, intent_id='intent-' + label, operation_id='op-' + label)

                def before_commit() -> None:
                    if self.cancelled or self.inflight is not capture:
                        raise Refused('disconnected')
                    operation._revalidate()

                before_commit()
                dispatched = True
                result = operation.generations.recover_invite(capture.generation,
                    operation.budget.deadline, before_commit)
                operation.budget.remaining()  # Late success never crosses the helper boundary.
                if (type(result) is not dict or set(result) != {'outcome', 'invite', 'cleanup'}
                        or result['outcome'] not in _helper_invite.OUTCOMES):
                    raise Refused('unavailable')
                _status_v2.InvitePhase(result['invite'])
                _status_v2.CleanupState(result['cleanup'])
                if result['outcome'] == 'uncertain':
                    self.audit.uncertain(intent)
                else:
                    # Same I3a mapping: a settled partial cleanup is an observed
                    # outcome, while its B11 cleanup axis stays uncertain.
                    self.audit.outcome(intent, _home_audit.Outcome.REFUSED if result['outcome'] == 'refused'
                                       else _home_audit.Outcome.SUCCEEDED)
                operation.budget.remaining()  # B16 settlement consumes the original budget too.
        except Exception:
            result = ({'outcome': 'uncertain', 'invite': 'uncertain', 'cleanup': 'uncertain'} if dispatched
                      else {'outcome': 'refused', 'invite': 'unknown', 'cleanup': 'unknown'})
            if intent is not None:
                try:
                    if dispatched:
                        self.audit.uncertain(intent)
                    else:
                        self.audit.outcome(intent, _home_audit.Outcome.REFUSED)
                except Exception:
                    pass  # An unavailable audit never becomes successful settlement.
        finally:
            self.lifecycle_result = result
            self.invite_observation = (capture.uid, capture.generation, result, time.monotonic())
            try:
                self._emit_enable_terminal(operation, result['outcome'],
                    None if result['outcome'] == 'complete' else result['outcome'])
            except Exception:
                pass  # One attempt, held through delivery on the original caller/deadline.
            finally:
                self.release(capture)

    def _authorized(self, proxy: Gio.DBusProxy, result: Gio.AsyncResult, capture: Capture | LifecycleCapture) -> None:
        terminal = False
        try:
            authorization = proxy.call_finish(result)
            terminal = True
            granted = authorization.unpack()[0][0] is True
        except Exception as error:
            granted = False
            if capture.method == 'Enable':
                _helper_lifecycle.log_enable_refusal(error)
            # A polkit error reply is terminal too; a transport timeout/loss is not.
            remote = Gio.DBusError.get_remote_error(error) if isinstance(error, GLib.Error) else None
            terminal = terminal or bool(remote and remote.startswith('org.freedesktop.PolicyKit1.Error.'))
        if isinstance(capture, _helper_lifecycle.LifecycleCapture):
            self._lifecycle_authorized(capture, terminal, granted)
            return
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
                # B16 brackets this device boundary; the private lifecycle worker
                # brackets its own activation/replacement/Turn off boundaries.
                event = {'ApprovePending': _home_audit.Event.DEVICE_APPROVED,
                         'RejectPending': _home_audit.Event.DEVICE_REJECTED,
                         'RevokeSelected': _home_audit.Event.DEVICE_REVOKED}[capture.method]
                # Local random correlation, never a node ID or digest prefix.
                label = '-'.join(str(byte) for byte in os.urandom(12))
                intent = self.audit.intent(event, intent_id='intent-' + label, operation_id='op-' + label)
                dispatched = False
                def before_commit() -> None:
                    nonlocal dispatched
                    self._before_commit(capture)
                    dispatched = True  # commit may be sent; loss can no longer prove refusal.
                try:
                    self.control.mutate(capture.method, capture.snapshot, capture.record, capture.deadline,
                                        before_commit)
                except Exception:
                    if dispatched:
                        self.audit.uncertain(intent)
                    else:
                        self.audit.outcome(intent, _home_audit.Outcome.REFUSED)
                    raise
                try:
                    self.audit.outcome(intent, _home_audit.Outcome.SUCCEEDED)
                except Exception:
                    self.audit.uncertain(intent)
                    raise
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
            if not isinstance(capture, (Capture, _helper_lifecycle.LifecycleCapture)) or self.cancelled:
                return
            self.cancelled = True
            if isinstance(capture, _helper_lifecycle.LifecycleCapture) and self.lifecycle_operation.accepted:
                return  # Worker still owns the slot; never send a second admission reply.
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
                if isinstance(self.inflight, (Capture, _helper_lifecycle.LifecycleCapture)) and self.inflight.sender == name:
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
