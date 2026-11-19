"""Standalone B15 wire checks and v1 golden bytes from base 6f77d51a3."""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from typing import Any

import pytest

from runtime.remote_access import status_v2 as v2
from scripts.test_remote_access_helper import Call, Device, venue  # noqa: F401


@pytest.mark.parametrize('phase,ready,pending,devices,expected', [
    ('off', False, False, False,
     b'{"phase":"off","ready":false,"devices":[],"pending":null,"selected":null}'),
    ('ready', True, False, True,
     b'{"phase":"ready","ready":true,"devices":[{"label":"Lab &amp; &lt;Mac&gt;\\u96ea","fingerprint":"0445702fe77907cfe9060f0b","comparison_phrase":"blue apple"}],"pending":null,"selected":null}'),
    ('pending', True, True, False,
     b'{"phase":"pending","ready":true,"devices":[],"pending":{"label":"Lab &amp; &lt;Mac&gt;\\u96ea","fingerprint":"0445702fe77907cfe9060f0b","comparison_phrase":"blue apple"},"selected":null}'),
])
def test_v1_status_base_bytes(venue: Any, phase: str, ready: bool, pending: bool,
                              devices: bool, expected: bytes) -> None:
    _, _, operations, client, _ = venue
    record = Device('device-a', 'stable-a', 'Lab & <Mac>\n雪', 'blue apple', 2_000_000_000)
    operations.snapshot = replace(operations.snapshot, phase=phase, ready=ready,
                                  pending=record if pending else None,
                                  devices=(record,) if devices else ())
    call = Call(client, 'Status')
    call.finish()
    assert call.reply.get_body().unpack()[0].encode() == expected

# The wire fixture is independent of the encoder and fixes all public keys.

UNKNOWN_WIRE = (
    b'{"address":{"age_seconds":null,"state":"unknown"},'
    b'"cleanup":{"age_seconds":null,"state":"unknown"},'
    b'"invite":{"age_seconds":null,"deadline_utc_seconds":null,"state":"unknown"},'
    b'"policy":{"age_seconds":null,"state":"unknown"},'
    b'"runtime":{"age_seconds":null,"state":"unknown"},'
    b'"setup":{"age_seconds":null,"state":"unknown"},"version":2}\n')
# Closed public vocabularies from B15, rather than expectations derived from enums.
DIMENSIONS = [
    ('setup', v2.SetupObservation, v2.SetupPhase,
     'unknown stale ready off in_progress failed uncertain partial_cleanup'),
    ('runtime', v2.RuntimeObservation, v2.RuntimeAvailability,
     'unknown stale available unavailable'),
    ('invite', v2.InviteObservation, v2.InvitePhase,
     'unknown stale none minting encoding pairing issued pending_approval consumed cancelled recovered expired refused uncertain'),
    ('address', v2.AddressObservation, v2.AddressState,
     'unknown stale current changed refreshing identity_changed failed uncertain'),
    ('policy', v2.PolicyObservation, v2.PolicyState,
     'unknown stale current renewing expired renewal_failed uncertain'),
    ('cleanup', v2.CleanupObservation, v2.CleanupState,
     'unknown stale not_needed in_progress complete pending partial_cleanup failed uncertain'),
]


def test_canonical_wire_and_frozen_model() -> None:
    status = v2.StatusV2()
    assert v2.encode(status) == UNKNOWN_WIRE
    assert v2.encode(v2.decode(b' \t' + UNKNOWN_WIRE)) == UNKNOWN_WIRE
    assert v2.decode(UNKNOWN_WIRE[:-1]) == status
    for value in [status, v2.ConnectorObservation(), *(kind() for _, kind, _, _ in DIMENSIONS)]:
        with pytest.raises(FrozenInstanceError):
            setattr(value, 'state' if hasattr(value, 'state') else 'setup', None)


@pytest.mark.parametrize('name,kind,states,vocabulary', DIMENSIONS)
def test_closed_dimension_vocabulary(name: str, kind: Any, states: Any, vocabulary: str) -> None:
    assert {state.value for state in states} == set(vocabulary.split())
    for state in vocabulary.split():
        age = None if state == 'unknown' else 31 if state == 'stale' else 0
        extra = {'deadline_utc_seconds': 200} if state in ('issued', 'pending_approval') else {}
        observation = kind(states(state), age, **extra)
        model = v2.StatusV2(**{name: observation})
        wire = v2.encode(model)
        assert json.loads(wire)[name] == {'state': state, 'age_seconds': age, **(
            {'deadline_utc_seconds': extra.get('deadline_utc_seconds')} if name == 'invite' else {})}
        assert getattr(v2.decode(wire), name) == observation
        assert len(wire) < 16384


@pytest.mark.parametrize('name,kind,states,vocabulary', DIMENSIONS)
def test_builder_freshness(name: str, kind: Any, states: Any, vocabulary: str) -> None:
    state = states(vocabulary.split()[2])
    for age in (0, 30, 31, 2_147_483_647):
        observation = kind(state, age)
        inputs = {name: observation}
        if name == 'runtime':
            inputs['connector'] = v2.ConnectorObservation(v2.ConnectorState.RUNNING, 0)
        result = getattr(v2.build_status(now_utc_seconds=100, **inputs), name)
        assert result.state.value == (state.value if age <= 30 else 'stale')
        assert result.age_seconds == age
    assert getattr(v2.build_status(now_utc_seconds=100), name).state.value == 'unknown'
    for age in (True, -1, 1.5, 2_147_483_648, None):
        with pytest.raises(v2.StatusError):
            v2.build_status(now_utc_seconds=100, **{name: kind(state, age)})
    with pytest.raises(v2.StatusError):
        v2.StatusV2(**{name: kind(state, 31)})


@pytest.mark.parametrize('connector', [v2.ConnectorState.STOPPED, v2.ConnectorState.UNREACHABLE])
def test_stopped_connector_never_infers_off(connector: v2.ConnectorState) -> None:
    for setup in (None, v2.SetupObservation(v2.SetupPhase.READY, 0),
                  v2.SetupObservation(v2.SetupPhase.IN_PROGRESS, 0)):
        model = v2.build_status(now_utc_seconds=100, setup=setup,
            connector=v2.ConnectorObservation(connector, 0),
            runtime=v2.RuntimeObservation(v2.RuntimeAvailability.AVAILABLE, 0))
        assert model.runtime == v2.RuntimeObservation(v2.RuntimeAvailability.UNAVAILABLE, 0)
        assert model.setup == (setup or v2.SetupObservation())
        assert b'"off"' not in v2.encode(model)
    assert 'off' not in {state.value for state in v2.RuntimeAvailability}


@pytest.mark.parametrize('state,age,expected', [
    (v2.ConnectorState.RUNNING, 0, 'available'),
    (v2.ConnectorState.RUNNING, 30, 'available'),
    (v2.ConnectorState.RUNNING, 31, 'stale'),
    (v2.ConnectorState.STOPPED, 31, 'stale'),
    (v2.ConnectorState.UNREACHABLE, 31, 'stale'),
    (v2.ConnectorState.UNKNOWN, None, 'unknown'),
    (v2.ConnectorState.STALE, 31, 'stale'),
])
def test_runtime_requires_fresh_dependencies(state: v2.ConnectorState,
                                             age: int | None, expected: str) -> None:
    result = v2.build_status(now_utc_seconds=100,
        connector=v2.ConnectorObservation(state, age),
        runtime=v2.RuntimeObservation(v2.RuntimeAvailability.AVAILABLE, 0))
    assert result.runtime.state.value == expected
    assert result.runtime.age_seconds == age
    assert v2.build_status(now_utc_seconds=100,
        connector=v2.ConnectorObservation(v2.ConnectorState.RUNNING, 0)).runtime.state.value == 'unknown'
    assert v2.build_status(now_utc_seconds=100,
        connector=v2.ConnectorObservation(v2.ConnectorState.RUNNING, 1),
        runtime=v2.RuntimeObservation(v2.RuntimeAvailability.AVAILABLE, 7)).runtime.age_seconds == 7


@pytest.mark.parametrize('phase', [v2.InvitePhase.ISSUED, v2.InvitePhase.PENDING_APPROVAL])
def test_invite_utc_deadline_and_stale_countdown(phase: v2.InvitePhase) -> None:
    for deadline, expected in [(0, 'expired'), (99, 'expired'), (100, 'expired'),
                               (101, phase.value), (9_223_372_036_854_775_807, phase.value)]:
        observation = v2.InviteObservation(phase, 0, deadline)
        result = v2.build_status(now_utc_seconds=100, invite=observation)
        assert result.invite.state.value == expected
        assert result.invite.deadline_utc_seconds == (deadline if expected == phase.value else None)
        # Pure display normalization never modifies the supplied fact.
        assert observation.state == phase and observation.deadline_utc_seconds == deadline
    result = v2.build_status(now_utc_seconds=100, invite=v2.InviteObservation(phase, 31, 200))
    assert result.invite == v2.InviteObservation(v2.InvitePhase.STALE, 31, None)
    for bad in (None, True, -1, 1.5, 9_223_372_036_854_775_808):
        with pytest.raises(v2.StatusError):
            v2.build_status(now_utc_seconds=100, invite=v2.InviteObservation(phase, 0, bad))


def malformed_wires() -> list[bytes]:
    wires = [b'', b'[]', b'null', b'{', b'\xff', UNKNOWN_WIRE + b'{}', b'[' * 2000]
    for version in (True, 2.0, '2', None, 1):
        value = json.loads(UNKNOWN_WIRE)
        value['version'] = version
        wires.append(json.dumps(value).encode())
    for key in ('extra', 'bearer', 'device_label', 'node_key', 'digest'):
        value = json.loads(UNKNOWN_WIRE)
        value[key] = 'SYNTHETIC-PRIVATE-MARKER'
        wires.append(json.dumps(value).encode())
    for name, _, _, _ in DIMENSIONS:
        value = json.loads(UNKNOWN_WIRE)
        del value[name]
        wires.append(json.dumps(value).encode())
        for bad in ([], None, 1):
            value = json.loads(UNKNOWN_WIRE)
            value[name] = bad
            wires.append(json.dumps(value).encode())
        for key, bad in [('state', 'SYNTHETIC-PRIVATE-MARKER'), ('state', 'x' * 15000),
                         ('state', []), ('state', 1), ('age_seconds', True),
                         ('age_seconds', 0), ('age_seconds', -1), ('age_seconds', float('nan')),
                         ('age_seconds', float('inf')), ('extra', 'SYNTHETIC-PRIVATE-MARKER')]:
            value = json.loads(UNKNOWN_WIRE)
            value[name][key] = bad
            wires.append(json.dumps(value).encode())
        for key in json.loads(UNKNOWN_WIRE)[name]:
            value = json.loads(UNKNOWN_WIRE)
            del value[name][key]
            wires.append(json.dumps(value).encode())
        wires.append(UNKNOWN_WIRE.replace(b'"' + name.encode() + b'":{',
            b'"' + name.encode() + b'":{"state":"unknown",'))
    wires.append(UNKNOWN_WIRE.replace(b'"version":2', b'"version":2,"version":2'))
    wires.append(UNKNOWN_WIRE.replace(b'"state":"unknown"', b'"state":"stale"'))
    wires.append(UNKNOWN_WIRE.replace(b'"deadline_utc_seconds":null', b'"deadline_utc_seconds":1'))
    return wires


@pytest.mark.parametrize('raw', malformed_wires(), ids=[str(i) for i in range(len(malformed_wires()))])
def test_strict_decoder_refuses_malformed_and_private_fields(raw: bytes) -> None:
    with pytest.raises(v2.StatusError) as failure:
        v2.decode(raw)
    assert str(failure.value) == 'invalid-status-v2'
    assert failure.value.__context__ is None and failure.value.__cause__ is None


def test_model_and_builder_reject_wrong_types() -> None:
    for name, kind, states, _ in DIMENSIONS:
        for value in (None, {}, kind('unknown'), kind(states.UNKNOWN, 0), kind(states.STALE, 30),
                      kind(states.STALE, None)):
            with pytest.raises(v2.StatusError):
                v2.StatusV2(**{name: value})
        with pytest.raises(v2.StatusError):
            v2.build_status(now_utc_seconds=100, **{name: {}})
    for bad in (True, -1, 1.5, 9_223_372_036_854_775_808, None):
        with pytest.raises(v2.StatusError):
            v2.build_status(now_utc_seconds=bad)
    with pytest.raises(v2.StatusError):
        v2.encode({})
    with pytest.raises(v2.StatusError):
        v2.StatusV2(setup=v2.RuntimeObservation(v2.RuntimeAvailability.UNKNOWN))


def test_wire_byte_cap_refuses_without_truncation(monkeypatch: Any) -> None:
    padded = UNKNOWN_WIRE + b' ' * (16384 - len(UNKNOWN_WIRE))
    assert v2.decode(padded) == v2.StatusV2()
    for raw in (padded + b' ', UNKNOWN_WIRE.decode(), bytearray(UNKNOWN_WIRE)):
        with pytest.raises(v2.StatusError):
            v2.decode(raw)
    # The closed model cannot naturally reach 16 KiB. Lower the real cap to
    # exercise the encoder's defensive refusal, including its framing byte.
    monkeypatch.setattr(v2, 'MAX_BYTES', len(UNKNOWN_WIRE))
    assert v2.encode(v2.StatusV2()) == UNKNOWN_WIRE
    monkeypatch.setattr(v2, 'MAX_BYTES', len(UNKNOWN_WIRE) - 1)
    with pytest.raises(v2.StatusError):
        v2.encode(v2.StatusV2())
