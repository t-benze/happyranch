"""B9 in the existing private-bus venue; no installed service or lifecycle effect."""
from __future__ import annotations

import os
import threading
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from scripts.test_remote_access_helper import Call, HELPER, eventually, venue  # noqa: F401
from runtime.remote_access import helper_lifecycle as lifecycle


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def prepare(venue: Any) -> tuple[Any, Any, Any, Any]:
    helper, _, _, client, _ = venue
    generations = SimpleNamespace(current_generation=Mock(return_value=object()))
    helper.control.status = Mock(side_effect=AssertionError('connector must not be read'))
    return helper, client, generations, Clock()


@pytest.mark.parametrize('method', ['Enable', 'Disable', 'ReplaceFailed', 'RefreshHomeAddress'])
def test_capture_without_connector_uses_bus_uid(venue: Any, method: str) -> None:
    helper, client, generations, _ = prepare(venue)
    authorize = Mock(return_value=True)
    reply, operation = lifecycle.LifecycleOperation.admit(
        method, client.get_unique_name(), helper, generations, authorize)
    assert reply.value == 'accepted'
    capture, timeout = authorize.call_args.args
    assert capture == operation.capture
    assert capture.sender == client.get_unique_name() and capture.uid == os.getuid()
    assert capture.owner.uid == os.getuid() and capture.generation is generations.current_generation.return_value
    assert all(0 < call.args[0] <= (300 if method == 'Disable' else 480)
               for call in generations.current_generation.call_args_list)
    assert not hasattr(capture, 'snapshot') and not hasattr(capture, 'record')
    assert 0 < timeout <= 120 and not helper.control.status.called
    assert not venue[1].calls and not venue[2].effects
    assert operation.complete(lifecycle.SetupPhase.FAILED).value == 'failed'
    # A forged/unowned unique name cannot supply a caller-selected UID.
    refused, missing = lifecycle.LifecycleOperation.admit(
        method, ':99999.99999', helper, generations, authorize)
    assert refused.value == 'refused' and missing is None and authorize.call_count == 1


@pytest.mark.parametrize('change', ['generation', 'uid', 'owner', 'disconnect', 'read_deadline'])
def test_revalidation_before_each_effect(venue: Any, monkeypatch: Any, change: str) -> None:
    helper, client, generations, clock = prepare(venue)
    helper.clock = clock
    reply, operation = lifecycle.LifecycleOperation.admit(
        'Enable', client.get_unique_name(), helper, generations, lambda *_: True)
    assert reply.value == 'accepted'
    first, second = Mock(), Mock()
    assert operation.run_step(first).value == 'in_progress'
    first.assert_called_once_with(operation.capture, 480)
    if change == 'generation':
        generations.current_generation.return_value = object()
    elif change == 'uid':
        monkeypatch.setattr(helper, '_uid', lambda _: os.getuid() + 1)
    elif change == 'owner':
        monkeypatch.setattr(helper, '_binding', lambda _: replace(operation.capture.owner, data_home='/changed'))
    elif change == 'disconnect':
        client.close_sync(None)
    else:
        def slow_read(remaining: float) -> object:
            clock.advance(480)
            return operation.capture.generation
        generations.current_generation.side_effect = slow_read
    with pytest.raises((lifecycle.LifecycleRefused, lifecycle.DeadlineExpired, HELPER.Refused)):
        operation.run_step(second)
    assert not second.called and not helper.control.status.called
    assert operation.complete(lifecycle.SetupPhase.READY).value == 'uncertain'


def test_generation_change_during_auth_refuses_before_effect(venue: Any) -> None:
    helper, client, generations, _ = prepare(venue)
    def authorize(*_: Any) -> bool:
        generations.current_generation.return_value = object()
        return True
    reply, operation = lifecycle.LifecycleOperation.admit(
        'Enable', client.get_unique_name(), helper, generations, authorize)
    assert reply.value == 'refused' and operation.outcome.value == 'failed'
    assert helper.inflight is None
    effect = Mock()
    with pytest.raises(lifecycle.LifecycleRefused):
        operation.run_step(effect)
    assert not effect.called


def test_shared_slot_refuses_both_directions_without_queue(venue: Any) -> None:
    helper, authority, operations, client, new = venue
    authority.mode = 'delay'
    first = Call(client)
    eventually(lambda: len(authority.waiting) == 1)
    reader, authorize = Mock(), Mock(return_value=True)
    reply, operation = lifecycle.LifecycleOperation.admit(
        'Enable', new().get_unique_name(), helper, reader, authorize)
    assert reply.value == 'refused' and operation is None
    assert not reader.current_generation.called and not authorize.called
    authority.grant()
    first.finish()
    eventually(lambda: helper.inflight is None)
    helper, client, generations, _ = prepare(venue)
    reply, operation = lifecycle.LifecycleOperation.admit(
        'Enable', client.get_unique_name(), helper, generations, authorize)
    assert reply.value == 'accepted'
    Call(new()).finish('busy')
    assert len(authority.calls) == 1 and len(operations.effects) == 1
    operation.complete(lifecycle.SetupPhase.FAILED)
    assert helper.inflight is None and len(authority.calls) == 1


def test_slot_claim_is_nonblocking(venue: Any) -> None:
    helper, client, generations, _ = prepare(venue)
    held, release = threading.Event(), threading.Event()
    def hold() -> None:
        with helper.lock:
            held.set()
            assert release.wait(3)
    thread = threading.Thread(target=hold)
    thread.start()
    assert held.wait(3)
    authorize = Mock(return_value=True)
    try:
        started = time.monotonic()
        reply, operation = lifecycle.LifecycleOperation.admit(
            'Enable', client.get_unique_name(), helper, generations, authorize)
        assert time.monotonic() - started < .5
        assert reply.value == 'refused' and operation is None and not authorize.called
    finally:
        release.set()
        thread.join(3)
    assert not thread.is_alive()


@pytest.mark.parametrize('method,total', [('Enable', 480), ('ReplaceFailed', 480),
                                         ('RefreshHomeAddress', 480), ('Disable', 300)])
def test_original_budget_includes_auth_and_passes_remainder(venue: Any, method: str, total: int) -> None:
    helper, client, generations, clock = prepare(venue)
    helper.clock = clock
    timeouts = []
    def authorize(_: Any, remaining: float) -> bool:
        timeouts.append(remaining)
        clock.advance(119)
        return True
    reply, operation = lifecycle.LifecycleOperation.admit(
        method, client.get_unique_name(), helper, generations, authorize)
    assert reply.value == 'accepted' and timeouts == [120]
    assert clock.now == 219  # Acceptance precedes any 480/300-second root operation.
    def step(_: Any, remaining: float) -> None:
        timeouts.append(remaining)
        clock.advance(50)
    operation.run_step(step)
    operation.run_step(step)
    assert timeouts == [120, total - 119, total - 169]
    assert operation.complete(lifecycle.SetupPhase.READY).value == 'ready'


@pytest.mark.parametrize('boundary', ['before_step', 'step_return', 'completion'])
@pytest.mark.parametrize('method,total,success', [('Enable', 480, 'ready'), ('Disable', 300, 'off')])
def test_deadline_suppresses_late_success(venue: Any, boundary: str, method: str,
                                       total: int, success: str) -> None:
    helper, client, generations, clock = prepare(venue)
    helper.clock = clock
    reply, operation = lifecycle.LifecycleOperation.admit(
        method, client.get_unique_name(), helper, generations, lambda *_: True)
    assert reply.value == 'accepted'
    effect = Mock()
    if boundary == 'before_step':
        clock.advance(total)
        with pytest.raises(lifecycle.DeadlineExpired):
            operation.run_step(effect)
        assert not effect.called and operation.outcome.value == 'failed'
    elif boundary == 'step_return':
        def late(_: Any, remaining: float) -> None:
            effect(remaining)
            clock.advance(remaining)
        with pytest.raises(lifecycle.DeadlineExpired):
            operation.run_step(late)
        effect.assert_called_once_with(total)
    else:
        operation.run_step(effect)
        clock.advance(total)
    assert operation.complete(lifecycle.SetupPhase(success)).value == (
        'failed' if boundary == 'before_step' else 'uncertain')
    assert helper.inflight is None


@pytest.mark.parametrize('elapsed,granted', [(119, True), (120, True), (123, True), (1, False)])
def test_auth_bound_returns_closed_reply_without_effects(venue: Any, elapsed: int, granted: bool) -> None:
    helper, client, generations, clock = prepare(venue)
    helper.clock = clock
    def authorize(_: Any, timeout: float) -> bool:
        assert timeout == HELPER.AUTH_DEADLINE == 120
        clock.advance(elapsed)
        return granted
    reply, operation = lifecycle.LifecycleOperation.admit(
        'Enable', client.get_unique_name(), helper, generations, authorize)
    assert reply.value == ('accepted' if elapsed < 120 and granted else 'refused')
    if elapsed >= 120 or not granted:
        assert helper.inflight is None and not operation.accepted
    else:
        operation.complete(lifecycle.SetupPhase.FAILED)
    assert not venue[2].effects


def test_auth_transport_loss_seals_slot_until_terminal_ack(venue: Any) -> None:
    helper, client, generations, _ = prepare(venue)
    authorize = Mock(side_effect=TimeoutError('synthetic loss'))
    reply, operation = lifecycle.LifecycleOperation.admit(
        'Enable', client.get_unique_name(), helper, generations, authorize)
    assert reply.value == 'refused' and operation.outcome.value == 'uncertain'
    assert helper.inflight is operation.capture
    other, missing = lifecycle.LifecycleOperation.admit(
        'Disable', client.get_unique_name(), helper, generations, authorize)
    assert other.value == 'refused' and missing is None and authorize.call_count == 1
    operation.authorization_settled()
    assert helper.inflight is None


@pytest.mark.parametrize('slow_read', [1, 2])
def test_admission_reads_share_original_transport_deadline(venue: Any, slow_read: int) -> None:
    helper, client, generations, clock = prepare(venue)
    helper.clock = clock
    count = 0
    timeouts = []
    def read(remaining: float) -> int:
        nonlocal count
        count += 1
        timeouts.append(remaining)
        if count == slow_read:
            clock.advance(123)
        return 7
    generations.current_generation.side_effect = read
    authorize = Mock(return_value=True)
    reply, operation = lifecycle.LifecycleOperation.admit(
        'Enable', client.get_unique_name(), helper, generations, authorize)
    assert reply.value == 'refused' and helper.inflight is None
    assert authorize.call_count == slow_read - 1
    assert all(0 < remaining <= 122 for remaining in timeouts)
    assert operation is None if slow_read == 1 else not operation.accepted


@pytest.mark.parametrize('elapsed', [1.999, 2, 3])
def test_status_two_second_budget(elapsed: float) -> None:
    clock = Clock()
    budget = lifecycle.OperationBudget.status(clock)
    timeouts = []
    def read(remaining: float) -> str:
        timeouts.append(remaining)
        clock.advance(elapsed)
        return 'observed'
    if elapsed < 2:
        assert budget.run(read) == 'observed'
    else:
        with pytest.raises(lifecycle.DeadlineExpired):
            budget.run(read)
        unread = Mock()
        with pytest.raises(lifecycle.DeadlineExpired):
            budget.run(unread)
        assert not unread.called
    assert timeouts == [2]


def test_exact_lifecycle_and_reply_vocabulary(venue: Any) -> None:
    assert {phase.value for phase in lifecycle.OUTCOMES} == {
        'ready', 'off', 'in_progress', 'failed', 'uncertain', 'partial_cleanup'}
    assert {reply.value for reply in lifecycle.CallReply} == {'accepted', 'refused'}
    helper, client, generations, _ = prepare(venue)
    _, operation = lifecycle.LifecycleOperation.admit(
        'Enable', client.get_unique_name(), helper, generations, lambda *_: True)
    for invalid in [lifecycle.SetupPhase.UNKNOWN, lifecycle.SetupPhase.STALE, 'ready', 'no-change']:
        with pytest.raises(lifecycle.LifecycleRefused):
            operation.complete(invalid)
    assert operation.complete(lifecycle.SetupPhase.PARTIAL_CLEANUP).value == 'partial_cleanup'


@pytest.mark.parametrize('error', [ConnectionError, TimeoutError])
def test_transport_loss_after_dispatch_remains_uncertain(venue: Any, error: type[Exception]) -> None:
    helper, client, generations, _ = prepare(venue)
    _, operation = lifecycle.LifecycleOperation.admit(
        'Disable', client.get_unique_name(), helper, generations, lambda *_: True)
    effect = Mock(side_effect=error('synthetic dispatched loss'))
    with pytest.raises(error):
        operation.run_step(effect)
    assert effect.call_count == 1 and operation.outcome.value == 'uncertain'
    assert operation.complete(lifecycle.SetupPhase.OFF).value == 'uncertain'
    assert lifecycle.transport_outcome(dispatched=True).value == 'uncertain'
    assert lifecycle.transport_outcome(dispatched=True, in_progress=True).value == 'in_progress'


def test_lifecycle_methods_absent_from_exported_interface() -> None:
    # This is the schema consumed by Gio.register_object, independent of the
    # dispatcher refusing calls to unimplemented methods.
    interface = HELPER.Gio.DBusNodeInfo.new_for_xml(HELPER.INTROSPECTION).interfaces[0]
    expected = {'Status', 'NextDevice', 'ApprovePending', 'RejectPending', 'RevokeSelected'}
    assert {method.name for method in interface.methods} == expected
    assert HELPER.METHODS == expected
