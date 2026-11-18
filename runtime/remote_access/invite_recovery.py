"""Inert B11 backend; the authenticated helper holds its mutation slot throughout.

Lock order is helper slot -> short issuer journal transaction -> IPC, with no
journal/trust flock held across IPC or minter calls. Durable uncertain intent
fences new issuance while unlocked; final publication compares the whole record.
No operation ID, secret, raw dependency exception or fresh authority is returned.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Callable, Protocol

from runtime.remote_access.home_minter import (
    HeadscaleAdminOperations, HeadscaleJoinKeyMinter, OperationRecordStore, PreAuthKey,
)
from runtime.remote_access.invite_issuer import HomeInviteIssuer, IssuerError
from runtime.remote_access.status_v2 import CleanupState, InvitePhase


class InviteControlClient(Protocol):
    """The existing ControlClient B10 capability; no second pairing owner."""

    def invalidate_invite(self, generation: int, deadline: float,
                          before_commit: Callable[[], None]) -> int: ...


class RecoveryMinter(Protocol):
    def recover(self, operation_id: str, *, deadline: float) -> bool:
        """Expire and completely re-list; True only if no usable key can remain.

        Carry this original monotonic deadline into EVERY admin call, clipping
        its timeout to remaining time, including lock/read/list/expire steps.
        Preserve the existing JoinKeyMinter recovery/late-create semantics.
        The legacy one-argument recover requires an adapter, never a fallback.
        """
        ...


@dataclass(frozen=True, repr=False)
class _RecoveryAdmin:
    admin: HeadscaleAdminOperations
    deadline: float

    def _timeout(self, timeout_s: float) -> float:
        _check_deadline(self.deadline)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise IssuerError('state')
        return min(timeout_s, remaining)

    def list_pre_auth_keys(self, *, user: str, timeout_s: float) -> tuple[PreAuthKey, ...]:
        result = self.admin.list_pre_auth_keys(user=user, timeout_s=self._timeout(timeout_s))
        _check_deadline(self.deadline)
        return result

    def expire_pre_auth_key(self, *, user: str, key: str, timeout_s: float) -> None:
        self.admin.expire_pre_auth_key(user=user, key=key, timeout_s=self._timeout(timeout_s))
        _check_deadline(self.deadline)

    def create_pre_auth_key(self, *, user: str, reusable: bool, ephemeral: bool,
                            expiration: datetime, timeout_s: float) -> PreAuthKey:
        raise IssuerError('refused')  # Recovery grants no create capability.


class HeadscaleRecoveryMinter:
    """Deadline-clipped existing recovery semantics, with injected custody only.

    The root caller also holds the existing dedicated invite-user mutation lock
    within the original budget; this adapter creates no lock/store/transport.
    """

    def __init__(self, *, admin: HeadscaleAdminOperations, invite_user: str,
                 now_fn: Callable[[], datetime], records: OperationRecordStore) -> None:
        self._admin, self._user, self._now_fn, self._records = admin, invite_user, now_fn, records

    def __repr__(self) -> str:
        return 'HeadscaleRecoveryMinter(<redacted>)'

    def recover(self, operation_id: str, *, deadline: float) -> bool:
        try:
            _check_deadline(deadline)
            minter = HeadscaleJoinKeyMinter(admin=_RecoveryAdmin(self._admin, deadline),
                                          invite_user=self._user, now_fn=self._now_fn, records=self._records)
            cleared = minter.recover(operation_id)
            _check_deadline(deadline)
            return cleared is True
        except BaseException:
            return False


class RecoveryOutcome(StrEnum):
    COMPLETE = 'complete'
    REFUSED = 'refused'
    UNCERTAIN = 'uncertain'
    PARTIAL_CLEANUP = 'partial_cleanup'


@dataclass(frozen=True)
class RecoveryResult:
    outcome: RecoveryOutcome
    invite: InvitePhase
    cleanup: CleanupState


def _check_deadline(deadline: float) -> None:
    now = time.monotonic()
    if (type(deadline) not in (int, float) or not math.isfinite(deadline)
            or not now < deadline <= now + 120):
        raise IssuerError('state')


def _operate(*, issuer: HomeInviteIssuer, control: InviteControlClient,
             minter: RecoveryMinter, generation: int, deadline: float,
             before_commit: Callable[[], None], terminal: InvitePhase) -> RecoveryResult:
    # Even unavailable/expired transport after dispatch cannot imply no change.
    outcome = RecoveryOutcome.REFUSED
    invite, cleanup = InvitePhase.UNKNOWN, CleanupState.UNKNOWN
    try:
        def revalidate() -> None:
            _check_deadline(deadline)
            before_commit()  # Root revalidates the live caller at B10 ready.
            _check_deadline(deadline)

        _check_deadline(deadline)
        if type(generation) is not int or generation < 0:
            raise IssuerError('state')
        revalidate()
        with issuer._recovery_record() as (parent, record):
            _check_deadline(deadline)
            if record is None:
                return RecoveryResult(RecoveryOutcome.COMPLETE, InvitePhase.NONE, CleanupState.NOT_NEEDED)
            # Every journal phase may conceal a key or ceremony, including an
            # expired invite or lost reserve reply. Never infer cleanup by TTL.
            record['outcome'] = 'uncertain'
            outcome, invite, cleanup = RecoveryOutcome.UNCERTAIN, InvitePhase.UNCERTAIN, CleanupState.UNCERTAIN
            issuer._write(parent, record)
            _check_deadline(deadline)

        revalidate()
        committed = control.invalidate_invite(generation, deadline, revalidate)
        if type(committed) is not int or committed <= generation:
            raise IssuerError('uncertain')
        # An acknowledgement proves product invalidation separately, even if
        # time ran out or network-key cleanup cannot subsequently be proved.
        invite, outcome = InvitePhase.CANCELLED, RecoveryOutcome.PARTIAL_CLEANUP
        _check_deadline(deadline)
        revalidate()
        if minter.recover(record['operation_id'], deadline=deadline) is not True:
            return RecoveryResult(outcome, invite, cleanup)
        _check_deadline(deadline)
        cleanup = CleanupState.COMPLETE
        revalidate()
        with issuer._recovery_record() as (parent, current):
            _check_deadline(deadline)
            if current != record:
                # A changed current ceremony invalidates our product projection.
                return RecoveryResult(RecoveryOutcome.UNCERTAIN, InvitePhase.UNCERTAIN, CleanupState.UNCERTAIN)
            # Preserve the existing installer/core journal vocabulary: both
            # actions prove recovery; cancellation is a separate result axis.
            current['outcome'] = 'recovered'
            issuer._write(parent, current)
            _check_deadline(deadline)
        return RecoveryResult(RecoveryOutcome.COMPLETE, terminal, cleanup)
    except BaseException:
        # No adapter error, chain, operation ID or key crosses this boundary.
        return RecoveryResult(outcome, invite, cleanup)


def cancel_invite(*, issuer: HomeInviteIssuer, control: InviteControlClient,
                  minter: RecoveryMinter, generation: int, deadline: float,
                  before_commit: Callable[[], None]) -> RecoveryResult:
    """Invalidate CURRENT ceremony first, then prove key expiry; never mint."""
    return _operate(issuer=issuer, control=control, minter=minter, generation=generation,
                    deadline=deadline, before_commit=before_commit, terminal=InvitePhase.CANCELLED)


def recover_invite(*, issuer: HomeInviteIssuer, control: InviteControlClient,
                   minter: RecoveryMinter, generation: int, deadline: float,
                   before_commit: Callable[[], None]) -> RecoveryResult:
    """Consume CURRENT ceremony and reconcile keys; fresh issuance is separate."""
    return _operate(issuer=issuer, control=control, minter=minter, generation=generation,
                    deadline=deadline, before_commit=before_commit, terminal=InvitePhase.RECOVERED)
