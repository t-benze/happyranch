"""I3a real private mount with test-side Gio/child transport seams, no host bus."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
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


@pytest.mark.parametrize('method,step,outcome', [('Enable', 'enable', 'ready'), ('ReplaceFailed', 'replace', 'off')])
def test_lifecycle_private_mount_fresh_auth_and_audit(mount: Any, method: str, step: str, outcome: str) -> None:
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
        assert v.helper.lifecycle_result == {'outcome': outcome, 'residue': []}
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
