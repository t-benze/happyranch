"""I3a/I3b private mounts with test-side native transport seams, no host bus."""
from __future__ import annotations

import importlib.util
import fcntl
import json
import os
import sys
import threading
import time
import ssl
import hashlib
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock
from typing import Any

import pytest

from runtime.remote_access import home_activation_backend as backend
from scripts.test_home_activation import store, put_pair, put, encoded, receipt_record, RECEIPT  # noqa: F401


class Variant:
    def __init__(self, signature: str, value: Any) -> None:
        self.value = value

    def unpack(self) -> Any:
        return self.value


@pytest.fixture
def mount(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> SimpleNamespace:
    # Only the unavailable transport is simulated. The shipping helper, B9
    # capture/slot/budget/auth callback and B16 durable writer execute unchanged.
    repository = ModuleType('gi.repository')
    repository.Gio = SimpleNamespace(DBusCallFlags=SimpleNamespace(NONE=0))
    repository.GLib = SimpleNamespace(Variant=Variant, timeout_add=lambda *a: 1,
                                     source_remove=lambda *a: None, Error=RuntimeError,
                                     VariantType=SimpleNamespace(new=lambda s: s))
    monkeypatch.setitem(sys.modules, 'gi', ModuleType('gi'))
    monkeypatch.setitem(sys.modules, 'gi.repository', repository)
    spec = importlib.util.spec_from_file_location('i3a_fixed_helper', Path(__file__).resolve().parents[1] /
                                                 'runtime/remote_access/remote_access_helper.py')
    h = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, h)
    spec.loader.exec_module(h)
    helper = h.RemoteAccessHelper.__new__(h.RemoteAccessHelper)
    helper.lock = threading.RLock()
    helper.inflight = None
    helper.clock = lambda: 100.0
    helper.last_activity = 0
    helper.cancelled = helper.authorization_terminal = False
    helper.timer = 0
    helper.lifecycle_operation = helper.lifecycle_result = None
    helper.release_uid = None
    owner = SimpleNamespace(uid=1000)
    helper.connection = SimpleNamespace(call_sync=Mock(return_value=Variant('', (1000,))))
    monkeypatch.setattr(h, 'bootstrap_owner', lambda uid: owner)
    helper.authority = SimpleNamespace(call=Mock(), call_finish=lambda result: result)
    helper._reply, helper._error = Mock(), Mock()
    helper.audit = h._home_audit.HomeAudit(tmp_path / 'audit.jsonl', uid=os.getuid(), gid=os.getgid())
    effects = []
    token = 'protected-generation-and-operation'
    reader = SimpleNamespace(current_generation=Mock(side_effect=lambda _: token))
    def effect(step: str, expected: str, remaining: float) -> dict:
        assert helper.inflight is not None and expected == 'protected-generation-and-operation'
        assert 0 < remaining <= 300
        assert json.loads(helper.audit.path.read_text().splitlines()[-1])['stage'] == 'intent'
        effects.append(step)
        return {'outcome': 'off', 'residue': []}
    reader.effect = Mock(side_effect=effect)
    monkeypatch.setattr(h._helper_generation, 'HomeLifecycleClient', lambda method, uid: reader)
    def revoke(generation: int, deadline: float, commit: Any) -> None:
        assert helper.inflight is not None and generation == 9 and deadline <= 220
        commit()
        effects.append('revoke')
    helper.control = SimpleNamespace(status=lambda: SimpleNamespace(owned=True, release_uid=1000, generation=9),
                                     revoke_all=Mock(side_effect=revoke))
    # Run the actual worker synchronously after the actual auth callback.
    monkeypatch.setattr(h.threading, 'Thread', lambda target, args, **kw: SimpleNamespace(start=lambda: target(*args)))
    message = SimpleNamespace(get_member=lambda: 'Disable', get_sender=lambda: ':1.42',
        get_unix_fd_list=lambda: None, get_interface=lambda: h.INTERFACE, get_signature=lambda: '')
    def admit() -> None:
        helper._start_lifecycle(message)
        capture = helper.inflight
        helper._authorized(helper.authority, Variant('', ((True, False, {}),)), capture)
    return SimpleNamespace(h=h, helper=helper, reader=reader, effects=effects, admit=admit, message=message)


@pytest.mark.parametrize('revoke', ['succeeded', 'unavailable', 'changed', 'expired', 'lost_reply'])
@pytest.mark.parametrize('cleanup', ['clean', 'residue', 'failed_stop'])
def test_disable_order_slot_audit_and_r7(mount: Any, revoke: str, cleanup: str) -> None:
    v = mount
    if revoke != 'succeeded':
        def failed(*args: Any) -> None:
            if revoke == 'lost_reply':
                args[-1]()
            raise v.h.Refused('unavailable' if revoke == 'lost_reply' else revoke)
        v.helper.control.revoke_all.side_effect = failed
    original = v.reader.effect.side_effect
    def effect(step: str, *args: Any) -> dict:
        result = original(step, *args)
        if cleanup == 'failed_stop' and step == 'stop':
            raise OSError('synthetic lost stop')
        if cleanup == 'residue' and step == 'cleanup':
            result = {'outcome': 'partial_cleanup', 'residue': ['CA staging and B7/B11 evidence']}
        # Second admission refuses while the first call still owns the slot.
        with pytest.raises(v.h.Refused, match='busy'):
            v.helper._start_lifecycle(v.message)
        return result
    v.reader.effect.side_effect = effect
    v.admit()
    assert v.effects == (['revoke'] if revoke == 'succeeded' else []) + (
        ['stop'] if cleanup == 'failed_stop' else ['stop', 'cleanup'])
    complete = revoke == 'succeeded' and cleanup == 'clean'
    assert v.helper.lifecycle_result['outcome'] == ('off' if complete else 'partial_cleanup')
    if revoke != 'succeeded':
        assert any('un-revoked connector trust state' in r for r in v.helper.lifecycle_result['residue'])
    assert v.helper.inflight is None
    rows = [json.loads(line) for line in v.helper.audit.path.read_text().splitlines()]
    assert all(row['stage'] in ('intent', 'outcome', 'uncertain') for row in rows)
    assert [r['component'] for r in rows if r['stage'] == 'intent'] == (
        ['credentials', 'units'] if cleanup == 'failed_stop' else ['credentials', 'units', 'live_state'])
    assert any(r['stage'] == ('uncertain' if revoke == 'lost_reply' else 'outcome') for r in rows)
    assert v.helper.control.revoke_all.call_count == (2 if revoke == 'changed' else 1)


@pytest.mark.parametrize('change', ['generation', 'operation', 'uid', 'owner', 'denied', 'deadline'])
def test_native_capture_action_and_cas_before_effect(mount: Any, monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    v = mount
    v.helper._start_lifecycle(v.message)
    capture = v.helper.inflight
    call = v.helper.authority.call.call_args
    assert call.args[0] == 'CheckAuthorization'
    subject, action, details, flags, cancellation = call.args[1].unpack()
    assert subject[0] == 'system-bus-name' and subject[1]['name'].unpack() == ':1.42'
    assert action == 'ai.happyranch.remote-access.disable' and flags == 1 and cancellation
    if change in ('generation', 'operation'):
        v.reader.current_generation.side_effect = lambda _: change
    elif change == 'uid':
        v.helper.connection.call_sync.return_value = Variant('', (1001,))
    elif change == 'owner':
        monkeypatch.setattr(v.h, 'bootstrap_owner', lambda _: object())
    elif change == 'deadline':
        v.helper.clock = lambda: capture.deadline
    v.helper._authorized(v.helper.authority, Variant('', ((change != 'denied', False, {}),)), capture)
    assert not v.effects and not v.reader.effect.called
    assert not v.helper.audit.path.exists() and v.helper.inflight is None
    assert v.helper._error.called and not v.helper._reply.called


@pytest.mark.parametrize('method,step,outcome,setup', [('Enable', 'enable', 'ready', 'ready'),
    ('Enable', 'enable', 'prepared', 'in_progress'), ('ReplaceFailed', 'replace', 'off', 'off')])
def test_lifecycle_private_mount_fresh_auth_and_audit(
        mount: Any, method: str, step: str, outcome: str, setup: str) -> None:
    v = mount
    v.message.get_member = lambda: method
    def effect(actual: str, expected: str, remaining: float) -> dict:
        assert actual == step and 0 < remaining <= 480 and v.helper.inflight
        assert json.loads(v.helper.audit.path.read_text().splitlines()[-1])['event'] == 'activation'
        return {'outcome': outcome, 'residue': []}
    v.reader.effect.side_effect = effect
    v.helper._dispatch(v.message)
    v.helper._error.assert_called_once_with(v.message, 'unknown-method')
    assert not v.helper.authority.call.called and not v.reader.effect.called
    for _ in range(2):
        v.admit()
        assert v.helper.lifecycle_result == {'outcome': setup, 'residue': []}
        assert v.helper.inflight is None
    assert [row['outcome'] for row in map(json.loads, v.helper.audit.path.read_text().splitlines())
            if row['stage'] == 'outcome'] == ['succeeded', 'succeeded']
    assert v.helper.authority.call.call_count == 2
    assert v.helper.authority.call.call_args.args[1].unpack()[1] == (
        'ai.happyranch.remote-access.' + v.h.LIFECYCLE_ACTIONS[method])
    assert all(name not in v.h.METHODS and name not in v.h.INTROSPECTION
               for name in ('Enable', 'ReplaceFailed', 'Disable'))


def test_auth_loss_retains_slot_until_terminal_ack(mount: Any) -> None:
    v = mount
    v.helper._start_lifecycle(v.message)
    capture = v.helper.inflight
    v.helper.authority.call_finish = Mock(side_effect=OSError('transport loss'))
    v.helper._authorized(v.helper.authority, None, capture)
    assert v.helper.inflight is capture and v.helper.cancelled and not v.effects
    assert v.helper.authority.call.call_args.args[0] == 'CancelCheckAuthorization'
    v.helper.authority.call_finish = lambda _: Variant('', ())
    v.helper._cancelled(v.helper.authority, None, capture)
    assert v.helper.inflight is None


def test_backend_expectations_come_from_protected_store(store: Any) -> None:
    put_pair(store._io.root)
    io = store._io
    io.layout = 'deb'
    lifecycle = backend.HelperLifecycleBackend(io)
    expected = lifecycle.capture('Disable', 1000)
    io.cleanup_headscale = Mock(return_value={'state': 'removed'})
    assert lifecycle.dispatch('Disable', 1000, 'cleanup', expected) == {'outcome': 'off', 'residue': []}
    assert io.cleanup_headscale.call_args.kwargs == dict(expected_release_uid=1000, expected_home_id='home',
        expected_control_plane_id='control', expected_connector_id='connector', expected_generation=7,
        expected_operation_id='b' * 32)
    updated = receipt_record('ready')
    updated['operation_id'] = 'e' * 32
    put(io.root, RECEIPT, encoded(updated))
    io.cleanup_headscale.reset_mock()
    with pytest.raises(backend.ActivationRefused):
        lifecycle.dispatch('Disable', 1000, 'cleanup', expected)
    assert not io.cleanup_headscale.called


def test_replacement_binds_inventory_before_auth_and_uses_captured_uid(store: Any) -> None:
    io = store._io
    io.replacement_inventory = Mock(return_value=SimpleNamespace(
        journal_generation=None, inventory_generation='a' * 64,
        installation=SimpleNamespace(state='known-failed-or-partial')))
    io.begin_replacement = Mock()
    lifecycle = backend.HelperLifecycleBackend(io)
    expected = lifecycle.capture('ReplaceFailed', 1000)
    lifecycle.dispatch('ReplaceFailed', 1000, 'replace', expected)
    io.begin_replacement.assert_called_once_with(expected_release_uid=1000, expected_generation='a' * 64)
    io.begin_replacement.reset_mock()
    io.replacement_inventory.return_value.inventory_generation = 'b' * 64
    with pytest.raises(backend.ActivationRefused):
        lifecycle.dispatch('ReplaceFailed', 1000, 'replace', expected)
    assert not io.begin_replacement.called


def test_stop_includes_headscale_and_disables_only_owned_units(store: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from runtime.remote_access import home_unit_gate
    put_pair(store._io.root)
    io = store._io
    events = []
    io.stop_and_confirm = lambda: events.append('stop-owned')
    io.inventory = lambda: SimpleNamespace(state='owned-activated')
    io._confirm_quiescent = lambda: events.append('confirm-owned')
    io._headscale_receipt = lambda: {}
    io._headscale_cleanup_identity = lambda _: None
    io._headscale_owner = (1234, 1234)
    io._headscale_stopped = lambda: events.append('confirm-headscale')
    monkeypatch.setattr(home_unit_gate.HomeUnitGate, 'inspect', lambda _: events.append('inspect-custody'))
    def properties(name: str, keys: tuple) -> dict:
        return dict(LoadState='loaded', DropInPaths='', FragmentPath=str(io.root / 'etc/systemd/system' / name))
    io.manager = SimpleNamespace(show_properties=properties,
        stop=lambda name, **kw: events.append(('stop', name)),
        disable=lambda name: events.append(('disable', name)))
    lifecycle = backend.HelperLifecycleBackend(io)
    lifecycle.dispatch('Disable', 1000, 'stop', lifecycle.capture('Disable', 1000))
    assert events[:4] == ['stop-owned', 'inspect-custody', ('stop', 'happyranch-headscale.service'), 'confirm-headscale']
    assert [item[1] for item in events if isinstance(item, tuple) and item[0] == 'disable'] == [
        'happyranch-managed.target', 'happyranch-connector.service', 'happyranch-tsnet-sidecar.service',
        'happyranch-release-verifier.socket']
    assert events[-2:] == ['confirm-owned', 'confirm-headscale']


@pytest.mark.parametrize('boundary', ['intent', 'effect_return', 'disconnect'])
def test_lifecycle_failed_settlement_never_reports_success_or_releases_early(mount: Any, boundary: str) -> None:
    v = mount
    if boundary == 'intent':
        v.helper.audit.intent = Mock(side_effect=OSError('synthetic audit unavailable'))
    else:
        original = v.reader.effect.side_effect
        def effect(step: str, *args: Any) -> dict:
            result = original(step, *args)
            if boundary == 'effect_return':
                v.helper.clock = lambda: 1000.0
                v.helper.lifecycle_operation.budget.clock = v.helper.clock
            else:
                capture = v.helper.inflight
                v.helper._cancel('disconnected')
                assert v.helper.inflight is capture  # Worker retains ownership.
                v.helper.connection.call_sync.side_effect = OSError('sender disconnected')
            return result
        v.reader.effect.side_effect = effect
    v.admit()
    assert v.helper.lifecycle_result['outcome'] == 'partial_cleanup'
    if boundary == 'intent':
        assert not v.effects
    assert v.helper.inflight is None
    assert v.helper._reply.call_count == 1  # No second reply on worker disconnect.


@pytest.fixture
def invite_mount(mount: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    from runtime.remote_access import helper_invite_backend as b
    from scripts.test_invite_recovery import issued
    from scripts.test_home_minter import setup, key_record
    v = mount
    issuer, original, _, trust, _, _, control, _, _ = issued(tmp_path)
    destination = tmp_path / b.HOME_ISSUER
    destination.parent.mkdir(parents=True)
    issuer._state_dir.rename(destination)
    issuer._state_dir = destination
    locked, admin, _, _, events = setup()
    admin.keys = (key_record(key=original.key),)
    held = []
    @contextmanager
    def lock() -> Any:
        held.append(True)
        try:
            yield locked
        finally:
            held.pop()
            if getattr(v, 'lock_exit_failure', False):
                raise OSError('synthetic lost settlement')
    monkeypatch.setattr(b.home_admin, '_locked', lock)
    v.helper.clock = time.monotonic
    token = dict(uid=1000, generation=7, operation='b' * 32, configuration='c' * 64, ca='d' * 64)
    child = b.HelperInviteBackend(SimpleNamespace(root=tmp_path, root_owner=(os.getuid(), os.getgid())))
    monkeypatch.setattr(child, '_config', lambda uid: (token, issuer._config))
    control.status = Mock(side_effect=lambda: SimpleNamespace(owned=True, release_uid=1000,
                                                            generation=trust.snapshot()[1]))
    monkeypatch.setattr(b, 'ControlClient', lambda: control)
    v.reader.current_generation.side_effect = lambda _: child.capture(1000)
    def recover(expected: str, deadline: float, commit: Any) -> dict:
        assert v.helper.inflight and deadline == v.helper.lifecycle_operation.budget.deadline
        assert 0 < deadline - v.helper.clock() <= 120
        rows = list(map(json.loads, v.helper.audit.path.read_text().splitlines()))
        assert rows[-1]['stage'] == 'intent'
        with pytest.raises(v.h.Refused, match='busy'):
            v.helper._start_lifecycle(v.message)
        return child.dispatch(v.message.get_member(), 1000, 'recover', expected,
                              deadline=deadline, before_commit=commit)
    v.reader.recover_invite = Mock(side_effect=recover)
    original_list = admin.list_pre_auth_keys
    def list_keys(**kw: Any) -> tuple:
        assert held and trust.load().invite_record is None
        with issuer._recovery_record() as (_, current):
            assert current['outcome'] == 'uncertain', 'journal flock released before minter'
        return original_list(**kw)
    admin.list_pre_auth_keys = list_keys
    v.issuer, v.original, v.trust, v.control = issuer, original, trust, control
    v.admin, v.events, v.child, v.token = admin, events, child, token
    v.journal = destination / 'invite-issuer.json'
    v.record = lambda: json.loads(v.journal.read_bytes())
    return v


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
@pytest.mark.parametrize('case', ['complete', 'absent', 'minter', 'ipc', 'lost_reply', 'journal_busy', 'late', 'connector_uid', 'lock_exit'])
def test_invite_mount_current_operation_and_audit(invite_mount: Any, method: str, case: str) -> None:
    v = invite_mount
    v.message.get_member = lambda: method
    initial = v.record()
    lock = None
    if case == 'absent':
        v.journal.unlink()
        v.control.status.side_effect = OSError('connector unavailable')
    elif case in ('ipc', 'lost_reply'):
        original = v.control.invalidate_invite
        def fail(*args: Any) -> None:
            if case == 'lost_reply':
                original(*args)
            raise OSError('synthetic lost IPC')
        v.control.invalidate_invite = fail
    elif case == 'minter':
        v.admin.ignore_expire = True
    elif case == 'journal_busy':
        lock = (v.journal.parent / 'invite-issuer.lock').open('rb')
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif case == 'connector_uid':
        v.control.status.side_effect = lambda: SimpleNamespace(owned=True, release_uid=1001, generation=9)
    elif case == 'lock_exit':
        v.lock_exit_failure = True
    elif case == 'late':
        original = v.reader.recover_invite.side_effect
        def late(*args: Any) -> dict:
            result = original(*args)
            v.helper.clock = v.helper.lifecycle_operation.budget.clock = lambda: args[1]
            return result
        v.reader.recover_invite.side_effect = late
    try:
        v.admit()
    finally:
        if lock:
            lock.close()
    terminal = 'cancelled' if method == 'CancelInvite' else 'recovered'
    expected = dict(outcome='complete', invite=terminal, cleanup='complete')
    if case == 'absent': expected = dict(outcome='complete', invite='none', cleanup='not_needed')
    if case == 'minter': expected = dict(outcome='partial_cleanup', invite='cancelled', cleanup='uncertain')
    if case in ('journal_busy', 'connector_uid'): expected = dict(outcome='refused', invite='unknown', cleanup='unknown')
    if case in ('ipc', 'lost_reply', 'late', 'lock_exit'): expected = dict(outcome='uncertain', invite='uncertain', cleanup='uncertain')
    assert v.helper.lifecycle_result == expected
    assert v.helper.inflight is None and len(v.original.calls) == 1, 'no new mint'
    assert bool(v.events) == (case in ('complete', 'minter', 'late', 'lock_exit'))
    if case in ('complete', 'late', 'lock_exit'):
        assert v.record() == initial | {'outcome': 'recovered'}
    elif case != 'absent':
        assert v.record() == initial | {'outcome': 'issued' if case in ('journal_busy', 'connector_uid') else 'uncertain'}
    if case in ('complete', 'minter', 'lost_reply', 'late', 'lock_exit'):
        assert v.trust.load().invite_record is None
    else:
        assert v.trust.load().invite_record is not None
    rows = list(map(json.loads, v.helper.audit.path.read_text().splitlines()))
    assert len(rows) == 2 and rows[0]['stage'] == 'intent'
    assert rows[0]['event'] == ('invite_cancelled' if method == 'CancelInvite' else 'invite_recovered')
    assert rows[-1]['outcome'] == ('uncertain' if case in ('ipc', 'lost_reply', 'late', 'lock_exit') else
                                   'refused' if case in ('journal_busy', 'connector_uid') else 'succeeded')
    call = v.helper.authority.call.call_args.args[1].unpack()
    assert call[0][1]['name'].unpack() == ':1.42'
    assert call[1] == 'ai.happyranch.remote-access.' + v.h.LIFECYCLE_ACTIONS[method]
    assert call[3] == 1 and call[4]
    assert method not in v.h.METHODS and method not in v.h.INTROSPECTION
    v.helper._dispatch(v.message)
    v.helper._error.assert_called_with(v.message, 'unknown-method')


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
@pytest.mark.parametrize('change', ['uid', 'owner', 'denied', 'deadline', 'generation', 'busy', 'app_id', 'fd', 'sender', 'capture_uid'])
def test_invite_capture_refuses_before_effect(invite_mount: Any, monkeypatch: pytest.MonkeyPatch,
                                             method: str, change: str) -> None:
    v = invite_mount
    v.message.get_member = lambda: method
    initial = v.journal.read_bytes()
    if change in ('busy', 'app_id', 'fd', 'sender', 'capture_uid'):
        if change == 'busy': v.helper.inflight = object()
        if change == 'app_id': v.message.get_signature = lambda: 's'
        if change == 'fd': v.message.get_unix_fd_list = lambda: SimpleNamespace(get_length=lambda: 1)
        if change == 'sender': v.message.get_sender = lambda: 'app.selected.identity'
        if change == 'capture_uid':
            v.helper.connection.call_sync.side_effect = [Variant('', (1000,)), Variant('', (1001,))]
        with pytest.raises((v.h.Refused, v.h._helper_lifecycle.LifecycleRefused)):
            v.helper._start_lifecycle(v.message)
        assert not v.helper.authority.call.called
    else:
        v.helper._start_lifecycle(v.message)
        capture = v.helper.inflight
        if change == 'uid': v.helper.connection.call_sync.return_value = Variant('', (1001,))
        if change == 'owner': monkeypatch.setattr(v.h, 'bootstrap_owner', lambda _: object())
        if change == 'generation': v.token['operation'] = 'e' * 32
        if change == 'deadline': v.helper.clock = lambda: capture.deadline
        v.helper._authorized(v.helper.authority, Variant('', ((change != 'denied', False, {}),)), capture)
        assert v.helper._error.called and not v.helper._reply.called
        assert v.helper.inflight is None
    assert v.journal.read_bytes() == initial and v.trust.load().invite_record is not None
    assert not v.events and not v.reader.recover_invite.called and not v.helper.audit.path.exists()


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
@pytest.mark.parametrize('change', ['uid', 'owner', 'generation', 'deadline', 'disconnect'])
def test_invite_live_callback_refuses_changes(invite_mount: Any, monkeypatch: pytest.MonkeyPatch,
                                            method: str, change: str) -> None:
    v = invite_mount
    v.message.get_member = lambda: method
    original = type(v.issuer)._write
    def write(issuer: Any, parent: int, record: dict) -> None:
        original(issuer, parent, record)
        if change == 'uid': v.helper.connection.call_sync.return_value = Variant('', (1001,))
        if change == 'owner': monkeypatch.setattr(v.h, 'bootstrap_owner', lambda _: object())
        if change == 'generation': v.token['operation'] = 'e' * 32
        if change == 'deadline':
            v.helper.clock = v.helper.lifecycle_operation.budget.clock = lambda: v.helper.lifecycle_operation.budget.deadline
        if change == 'disconnect': v.helper.connection.call_sync.side_effect = OSError('caller gone')
    monkeypatch.setattr(type(v.issuer), '_write', write)
    v.admit()
    assert v.record()['outcome'] == 'uncertain'
    assert v.helper.lifecycle_result['outcome'] == 'uncertain'
    assert v.trust.load().invite_record is not None and not v.control.calls and not v.events
    assert v.helper.inflight is None


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
def test_invite_audit_failure_blocks_effects(invite_mount: Any, method: str) -> None:
    v = invite_mount
    v.message.get_member = lambda: method
    initial = v.journal.read_bytes()
    v.helper.audit.intent = Mock(side_effect=OSError('audit unavailable'))
    v.admit()
    assert v.helper.lifecycle_result['outcome'] == 'refused'
    assert v.journal.read_bytes() == initial and v.trust.load().invite_record is not None
    assert not v.reader.recover_invite.called and not v.events


@pytest.mark.parametrize('method', ['CancelInvite', 'RecoverInvite'])
def test_invite_status_staleness_and_binding(invite_mount: Any, monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    v = invite_mount
    v.message.get_member = lambda: method
    v.admit()
    v.helper.status_lock, v.helper.status_worker = threading.Lock(), None
    observed = SimpleNamespace(generation=7, operation='b' * 32, release_uid=1000, phase='ready')
    v.helper.generations = SimpleNamespace(current_generation=lambda _: observed)
    v.helper.control.status = lambda: SimpleNamespace(owned=True, release_uid=1000, ready=True)
    monkeypatch.setattr(v.h.threading, 'Thread', lambda target, args=(), **kw: SimpleNamespace(start=lambda: target(*args)))
    monkeypatch.setattr(v.h.threading, 'Timer', lambda *args: SimpleNamespace(start=lambda: None, cancel=lambda: None))
    v.h.Gio.DBusSendMessageFlags = SimpleNamespace(NONE=0)
    reply = SimpleNamespace(set_body=lambda body: setattr(reply, 'body', body))
    v.message.new_method_reply = lambda: reply
    v.helper.connection.send_message = Mock()
    at = v.helper.invite_observation[-1]
    for case in ('fresh', 'stale', 'operation', 'uid'):
        monkeypatch.setattr(v.h.time, 'monotonic', lambda: at + (31 if case == 'stale' else 1))
        observed.operation = 'e' * 32 if case == 'operation' else 'b' * 32
        observed.release_uid = 1001 if case == 'uid' else 1000
        v.helper._status_v2(v.message, ':1.42')
        if case == 'uid':
            v.helper._error.assert_called_with(v.message, 'changed')
            continue
        body = v.helper.connection.send_message.call_args.args[0].body.unpack()[0]
        result = json.loads(body)
        assert set(result) == {'version', 'setup', 'runtime', 'invite', 'address', 'policy', 'cleanup'}
        expected = ('cancelled' if method == 'CancelInvite' else 'recovered') if case == 'fresh' else (
            'stale' if case == 'stale' else 'unknown')
        assert result['invite']['state'] == expected
        assert result['cleanup']['state'] == ('complete' if case == 'fresh' else 'stale' if case == 'stale' else 'unknown')


@pytest.mark.parametrize('case', ['acknowledged', 'denied', 'late'])
def test_invite_pipe_live_callback(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    from runtime.remote_access import helper_invite
    called = []
    source = Path(__file__).resolve().parents[1] / 'runtime/remote_access/helper_invite.py'
    script = ('import sys,json,runpy; h=runpy.run_path(sys.argv[1]); '
              'r=json.loads(sys.stdin.buffer.readline()); h["confirm"](r["deadline"]); '
              'print(\'{"outcome":"complete","invite":"none","cleanup":"not_needed"}\',flush=True)')
    deadline = time.monotonic() + 2
    def commit() -> None:
        called.append(True)
        if case == 'denied': raise ValueError('caller gone')
        if case == 'late': monkeypatch.setattr(helper_invite.time, 'monotonic', lambda: deadline)
    if case == 'acknowledged':
        result = helper_invite.effect((sys.executable, '-I', '-c', script, str(source)), {'deadline': deadline}, commit)
        assert result == dict(outcome='complete', invite='none', cleanup='not_needed')
    else:
        with pytest.raises((ValueError, TimeoutError)):
            helper_invite.effect((sys.executable, '-I', '-c', script, str(source)), {'deadline': deadline}, commit)
    assert called == [True]


@pytest.mark.parametrize('change', ['current', 'uid', 'config', 'ca', 'operation'])
def test_invite_protected_capture(store: Any, change: str) -> None:
    from runtime.remote_access.helper_invite_backend import HelperInviteBackend
    from scripts.test_home_activation import config_record, CONFIG
    from scripts.test_invite_v2 import SWIFT_CA
    ca = ssl.DER_cert_to_PEM_cert(SWIFT_CA).encode('ascii')
    config, receipt = config_record(), receipt_record('ready')
    config['ca_der_sha256'] = receipt['ca_der_sha256'] = hashlib.sha256(SWIFT_CA).hexdigest()
    receipt['member_digests']['/' + CONFIG] = hashlib.sha256(encoded(config)).hexdigest()
    receipt['member_digests']['/etc/happyranch/control-ca.pem'] = hashlib.sha256(ca).hexdigest()
    put(store._io.root, CONFIG, encoded(config))
    put(store._io.root, RECEIPT, encoded(receipt))
    path = put(store._io.root, 'etc/happyranch/control-ca.pem', ca)
    path.chmod(0o640)
    store._io._account = lambda **kw: (os.getuid(), os.getgid())
    store._io._headscale_members = lambda: {'/etc/happyranch/control-ca.pem': ((os.getuid(), os.getgid()), 0o640)}
    child = HelperInviteBackend(store._io)
    initial = child.capture(1000)
    assert json.loads(initial) == dict(uid=1000, generation=7, operation='b' * 32,
        configuration=receipt['member_digests']['/' + CONFIG], ca=receipt['ca_der_sha256'])
    assert backend.HelperLifecycleBackend(store._io).capture('CancelInvite', 1000) == initial
    assert backend.HelperLifecycleBackend(store._io).capture('RecoverInvite', 1000) == initial
    if change == 'uid':
        with pytest.raises(ValueError): child.capture(1001)
    elif change == 'config':
        config['dial_ip'] = '192.168.1.41'
        put(store._io.root, CONFIG, encoded(config))
        with pytest.raises(ValueError): child.capture(1000)
    elif change == 'ca':
        path.write_bytes(ca + b'\n')
        with pytest.raises(ValueError): child.capture(1000)
    elif change == 'operation':
        receipt['operation_id'] = 'e' * 32
        put(store._io.root, RECEIPT, encoded(receipt))
        assert child.capture(1000) != initial
    else:
        assert child.capture(1000) == initial
