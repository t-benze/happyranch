"""Inert B15 display model; no I/O, authority, helper wiring or v1 imports.

Callers supply observation ages measured with their own monotonic clock. Ages
above 30 seconds become stale; deadlines are Unix UTC seconds, never monotonic.
Observations can be old inputs; StatusV2 itself admits only normalized values.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TypeVar

MAX_BYTES = 16384  # Includes the terminating newline.
STALE_AFTER_SECONDS = 30
MAX_AGE_SECONDS = 2_147_483_647
MAX_DEADLINE_UTC_SECONDS = 9_223_372_036_854_775_807


class StatusError(ValueError):
    """Fixed, secret-free rejection; never echo input or library exceptions."""

    def __init__(self) -> None:
        super().__init__('invalid-status-v2')


class SetupPhase(StrEnum):
    UNKNOWN = 'unknown'
    STALE = 'stale'
    READY = 'ready'
    OFF = 'off'
    IN_PROGRESS = 'in_progress'
    FAILED = 'failed'
    UNCERTAIN = 'uncertain'
    PARTIAL_CLEANUP = 'partial_cleanup'


class RuntimeAvailability(StrEnum):
    UNKNOWN = 'unknown'
    STALE = 'stale'
    AVAILABLE = 'available'
    UNAVAILABLE = 'unavailable'


class InvitePhase(StrEnum):
    UNKNOWN = 'unknown'
    STALE = 'stale'
    NONE = 'none'
    MINTING = 'minting'
    ENCODING = 'encoding'
    PAIRING = 'pairing'
    ISSUED = 'issued'
    PENDING_APPROVAL = 'pending_approval'
    CONSUMED = 'consumed'
    CANCELLED = 'cancelled'
    RECOVERED = 'recovered'
    EXPIRED = 'expired'
    REFUSED = 'refused'
    UNCERTAIN = 'uncertain'


class AddressState(StrEnum):
    UNKNOWN = 'unknown'
    STALE = 'stale'
    CURRENT = 'current'
    CHANGED = 'changed'
    REFRESHING = 'refreshing'
    IDENTITY_CHANGED = 'identity_changed'
    FAILED = 'failed'
    UNCERTAIN = 'uncertain'


class PolicyState(StrEnum):
    UNKNOWN = 'unknown'
    STALE = 'stale'
    CURRENT = 'current'
    RENEWING = 'renewing'
    EXPIRED = 'expired'
    RENEWAL_FAILED = 'renewal_failed'
    UNCERTAIN = 'uncertain'


class CleanupState(StrEnum):
    UNKNOWN = 'unknown'
    STALE = 'stale'
    NOT_NEEDED = 'not_needed'
    IN_PROGRESS = 'in_progress'
    COMPLETE = 'complete'
    PENDING = 'pending'
    PARTIAL_CLEANUP = 'partial_cleanup'
    FAILED = 'failed'
    UNCERTAIN = 'uncertain'


@dataclass(frozen=True)
class SetupObservation:
    state: SetupPhase = SetupPhase.UNKNOWN
    age_seconds: int | None = None


@dataclass(frozen=True)
class RuntimeObservation:
    state: RuntimeAvailability = RuntimeAvailability.UNKNOWN
    age_seconds: int | None = None


@dataclass(frozen=True)
class InviteObservation:
    state: InvitePhase = InvitePhase.UNKNOWN
    age_seconds: int | None = None
    deadline_utc_seconds: int | None = None


@dataclass(frozen=True)
class AddressObservation:
    state: AddressState = AddressState.UNKNOWN
    age_seconds: int | None = None


@dataclass(frozen=True)
class PolicyObservation:
    state: PolicyState = PolicyState.UNKNOWN
    age_seconds: int | None = None


@dataclass(frozen=True)
class CleanupObservation:
    state: CleanupState = CleanupState.UNKNOWN
    age_seconds: int | None = None


class ConnectorState(StrEnum):
    UNKNOWN = 'unknown'
    STALE = 'stale'
    RUNNING = 'running'
    STOPPED = 'stopped'
    UNREACHABLE = 'unreachable'


@dataclass(frozen=True)
class ConnectorObservation:
    """Builder input only; running alone does not prove runtime availability."""
    state: ConnectorState = ConnectorState.UNKNOWN
    age_seconds: int | None = None


@dataclass(frozen=True)
class StatusV2:
    setup: SetupObservation = field(default_factory=SetupObservation)
    runtime: RuntimeObservation = field(default_factory=RuntimeObservation)
    invite: InviteObservation = field(default_factory=InviteObservation)
    address: AddressObservation = field(default_factory=AddressObservation)
    policy: PolicyObservation = field(default_factory=PolicyObservation)
    cleanup: CleanupObservation = field(default_factory=CleanupObservation)

    def __post_init__(self) -> None:
        _validate_status(self)


_DIMENSIONS = (
    ('setup', SetupObservation, SetupPhase),
    ('runtime', RuntimeObservation, RuntimeAvailability),
    ('invite', InviteObservation, InvitePhase),
    ('address', AddressObservation, AddressState),
    ('policy', PolicyObservation, PolicyState),
    ('cleanup', CleanupObservation, CleanupState),
)
_ACTIVE_INVITE = frozenset({InvitePhase.ISSUED, InvitePhase.PENDING_APPROVAL})
O = TypeVar('O', SetupObservation, RuntimeObservation, InviteObservation,
            AddressObservation, PolicyObservation, CleanupObservation, ConnectorObservation)


def _integer(value: object, maximum: int) -> bool:
    return type(value) is int and 0 <= value <= maximum


def _validate_observation(value: O, kind: type[O], states: type[StrEnum]) -> None:
    if type(value) is not kind or type(value.state) is not states:
        raise StatusError()
    age = value.age_seconds
    if value.state == 'unknown':
        if age is not None:
            raise StatusError()
    elif not _integer(age, MAX_AGE_SECONDS):
        raise StatusError()
    if value.state == 'stale' and age <= STALE_AFTER_SECONDS:
        raise StatusError()
    if kind is InviteObservation:
        deadline = value.deadline_utc_seconds
        if value.state in _ACTIVE_INVITE:
            if not _integer(deadline, MAX_DEADLINE_UTC_SECONDS):
                raise StatusError()
        elif deadline is not None:
            raise StatusError()


def _validate_status(value: StatusV2) -> None:
    if type(value) is not StatusV2:
        raise StatusError()
    for name, kind, states in _DIMENSIONS:
        observation = getattr(value, name)
        _validate_observation(observation, kind, states)
        if (observation.age_seconds is not None
                and observation.age_seconds > STALE_AFTER_SECONDS
                and observation.state != 'stale'):
            raise StatusError()


def _fresh(value: O | None, kind: type[O], states: type[StrEnum]) -> O:
    if value is None:
        return kind()
    _validate_observation(value, kind, states)
    if value.age_seconds is not None and value.age_seconds > STALE_AFTER_SECONDS:
        fields = {'state': states('stale')}
        if kind is InviteObservation:
            fields['deadline_utc_seconds'] = None
        return replace(value, **fields)
    return value


def build_status(*, now_utc_seconds: int, connector: ConnectorObservation | None = None,
                 setup: SetupObservation | None = None, runtime: RuntimeObservation | None = None,
                 invite: InviteObservation | None = None, address: AddressObservation | None = None,
                 policy: PolicyObservation | None = None,
                 cleanup: CleanupObservation | None = None) -> StatusV2:
    """Normalize supplied facts without querying services or changing authority.

    A fresh stopped/unreachable connector proves runtime unavailable only.
    Availability requires both connector and runtime observations; its age is
    the older dependency. Setup Off requires its own explicit observation.
    Expiry changes this display only, never consumes or invalidates an invite.
    """
    if not _integer(now_utc_seconds, MAX_DEADLINE_UTC_SECONDS):
        raise StatusError()
    connection = _fresh(connector, ConnectorObservation, ConnectorState)
    availability = _fresh(runtime, RuntimeObservation, RuntimeAvailability)
    if connection.state in (ConnectorState.STOPPED, ConnectorState.UNREACHABLE):
        availability = RuntimeObservation(RuntimeAvailability.UNAVAILABLE, connection.age_seconds)
    elif connection.state in (ConnectorState.UNKNOWN, ConnectorState.STALE):
        availability = RuntimeObservation(RuntimeAvailability(connection.state), connection.age_seconds)
    elif availability.age_seconds is not None:
        availability = replace(availability, age_seconds=max(
            connection.age_seconds, availability.age_seconds))
    invitation = _fresh(invite, InviteObservation, InvitePhase)
    if invitation.state in _ACTIVE_INVITE and invitation.deadline_utc_seconds <= now_utc_seconds:
        invitation = InviteObservation(InvitePhase.EXPIRED, invitation.age_seconds)
    return StatusV2(_fresh(setup, SetupObservation, SetupPhase), availability, invitation,
                    _fresh(address, AddressObservation, AddressState),
                    _fresh(policy, PolicyObservation, PolicyState),
                    _fresh(cleanup, CleanupObservation, CleanupState))


def encode(status: StatusV2) -> bytes:
    """Canonical compact sorted JSON, one newline, cap includes framing."""
    _validate_status(status)
    value = {'version': 2}
    for name, _, _ in _DIMENSIONS:
        observation = getattr(status, name)
        value[name] = {'state': observation.state.value, 'age_seconds': observation.age_seconds}
        if name == 'invite':
            value[name]['deadline_utc_seconds'] = observation.deadline_utc_seconds
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode() + b'\n'
    if len(raw) > MAX_BYTES:
        raise StatusError()
    return raw


def _unique(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise StatusError()
        value[key] = item
    return value


def _constant(value: str) -> None:
    raise StatusError()


def decode(raw: bytes) -> StatusV2:
    """Strict UTF-8, exact keys/types/vocabularies; no duplicate keys anywhere.

    Accepts ordinary JSON whitespace with an optional final newline. Encoding
    always canonicalizes it. The byte cap applies before UTF-8/JSON parsing.
    """
    if type(raw) is not bytes or len(raw) > MAX_BYTES:
        raise StatusError()
    # Raise outside library handlers so error context cannot retain raw input.
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique, parse_constant=_constant)
    except (ValueError, RecursionError):
        value = None
    if (type(value) is not dict or set(value) != {'version', *(d[0] for d in _DIMENSIONS)}
            or type(value['version']) is not int or value['version'] != 2):
        raise StatusError()
    observations = {}
    for name, kind, states in _DIMENSIONS:
        part = value[name]
        keys = {'state', 'age_seconds'} | ({'deadline_utc_seconds'} if name == 'invite' else set())
        if type(part) is not dict or set(part) != keys or type(part['state']) is not str:
            raise StatusError()
        # Enum lookup cannot return an arbitrary string or echo it on failure.
        state = next((s for s in states if s.value == part['state']), None)
        if state is None:
            raise StatusError()
        observations[name] = kind(**{**part, 'state': state})
    return StatusV2(**observations)
