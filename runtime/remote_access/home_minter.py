"""Pure, unwired Headscale v0.25.1 admin-operation adapter.

The invite user NAME must be dedicated to Mac invite keys. The protected helper
must serialize mint/recover against every other writer to that user. TASK-16780
owns transport, user/ACL creation and wiring; this module performs no I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Protocol

from runtime.remote_access.invite_issuer import MintedKey, MintRefused

_TIMEOUT_S = 5.0
_MAX_CLEANUP_KEYS = 128
# 366 days * 1440 mints/day = 527040 rows at the issuer's maximum rate.
# Keep a bounded complete inventory with margin; see the operator reset in
# the adapter contract. Creation reserves one slot for lost-reply recovery.
_MAX_INVENTORY_KEYS = 600_000


class AdminRefused(Exception):
    """Transport proves create made NO state change (never a timeout/lost reply).

    The transport must sanitize its own diagnostics, including this exception.
    """


class MintUncertain(Exception):
    """A create may have taken effect; only explicit recover can reconcile it."""

    def __init__(self) -> None:
        super().__init__("uncertain")


@dataclass(frozen=True, repr=False)
class PreAuthKey:
    """Complete transport projection; timestamps are decoded UTC datetimes.

    v0.25.1 proto/headscale/v1/preauthkey.proto:7-17 includes string user/id/key,
    bool reusable/ephemeral/used, expiration/created_at timestamps and acl_tags.
    Missing timestamps/fields must not be defaulted to plausible values.
    """

    user: str
    id: str
    key: str
    reusable: bool
    ephemeral: bool
    used: bool
    expiration: datetime
    created_at: datetime
    acl_tags: tuple[str, ...]

    def __repr__(self) -> str:
        return "PreAuthKey(<redacted>)"


class HeadscaleAdminOperations(Protocol):
    """v0.25.1 HeadscaleService Create/List/ExpirePreAuthKey operations.

    Every call honors timeout_s. List returns a COMPLETE bounded snapshot, never
    a truncated/filtered success. A successful list must be ordered after prior
    create effects, INCLUDING timed-out calls. If an earlier create could still
    commit a usable key later, list must fail until settlement or key expiry;
    RPC cancellation alone is not settlement proof. User is a NAME, not an ID. Create sends
    no ACL tags. Keys are in-memory values/request fields ONLY. Raw keys and
    request/response bodies must NEVER enter argv, env, URLs, logs or exceptions.
    CLI `preauthkeys expire KEY` is forbidden. A transport needs a separately
    protected admin socket; no transport/dependency is selected here.
    """

    def create_pre_auth_key(self, *, user: str, reusable: bool, ephemeral: bool,
                            expiration: datetime, timeout_s: float) -> PreAuthKey: ...

    def list_pre_auth_keys(self, *, user: str, timeout_s: float) -> tuple[PreAuthKey, ...]: ...

    def expire_pre_auth_key(self, *, user: str, key: str, timeout_s: float) -> None: ...


@dataclass(frozen=True)
class CreationWindow:
    started_at: datetime
    expires_at: datetime


class OperationRecordStore(Protocol):
    def reserve(self, operation_id: str, window: CreationWindow) -> bool:
        """Durably/atomically insert if absent BEFORE create, storing no secret.

        True only after persistence; False if already recorded. Never overwrite
        or discard a reservation, including after refusal/recovery. Exceptions
        refuse create. The protected helper supplies custody and serialization.
        """
        ...


def _utc(value: datetime) -> datetime:
    if (type(value) is not datetime or value.tzinfo is None
            or value.utcoffset() != timedelta(0)):
        raise ValueError("time")
    return value


class HeadscaleJoinKeyMinter:
    def __init__(self, *, admin: HeadscaleAdminOperations, invite_user: str,
                 now_fn: Callable[[], datetime], records: OperationRecordStore) -> None:
        self._admin, self._user = admin, invite_user
        self._now_fn, self._records = now_fn, records

    def __repr__(self) -> str:
        return "HeadscaleJoinKeyMinter(<redacted>)"

    def _record(self, record: PreAuthKey) -> PreAuthKey:
        if (type(record) is not PreAuthKey or type(record.key) is not str
                or re.fullmatch(r"[0-9a-f]{48}", record.key) is None
                or type(record.user) is not str or record.user != self._user
                or type(record.id) is not str or not record.id
                or any(type(flag) is not bool for flag in
                       (record.reusable, record.ephemeral, record.used))
                or type(record.acl_tags) is not tuple
                or any(type(tag) is not str for tag in record.acl_tags)):
            raise ValueError("record")
        _utc(record.expiration)
        _utc(record.created_at)
        return record

    def _list(self, now: datetime, *, reserve_slot: bool) -> tuple[PreAuthKey, ...]:
        records = self._admin.list_pre_auth_keys(user=self._user, timeout_s=_TIMEOUT_S)
        if type(records) is not tuple or len(records) + int(reserve_slot) > _MAX_INVENTORY_KEYS:
            raise ValueError("inventory")
        ids: set[str] = set()
        usable: list[PreAuthKey] = []
        for record in records:
            self._record(record)
            if record.id in ids:
                raise ValueError("inventory")
            ids.add(record.id)
            # Validate inert history too, but only usable keys consume the
            # operational cap or require an expire RPC. Used reusable keys
            # remain usable; no raw key is copied into validation errors.
            if self._usable(record, now):
                usable.append(record)
                if len(usable) > _MAX_CLEANUP_KEYS:
                    raise ValueError("inventory")
        return tuple(usable)

    def _usable(self, record: PreAuthKey, now: datetime) -> bool:
        # Used single-use keys cannot enroll again. A reusable key can, even
        # when marked used; conservatively expire it too if one is encountered.
        return (not record.used or record.reusable) and record.expiration > now

    def _cleanup(self, *, reserve_slot: bool = False) -> bool:
        before = _utc(self._now_fn())
        for record in self._list(before, reserve_slot=reserve_slot):
            self._admin.expire_pre_auth_key(user=self._user, key=record.key,
                                             timeout_s=_TIMEOUT_S)
        records = self._list(before, reserve_slot=reserve_slot)  # RPC success is not revocation proof.
        after = _utc(self._now_fn())
        return after >= before and not any(self._usable(record, after) for record in records)

    def _deadline(self, expires_at: datetime) -> tuple[datetime, datetime]:
        try:
            now = _utc(self._now_fn())
            deadline = _utc(expires_at).replace(microsecond=0)
            remaining = (expires_at - now).total_seconds()
            if 0 < remaining <= 300 and deadline > now:
                return now, deadline
        except BaseException:
            pass
        raise MintRefused("refused")

    def _mint(self, operation_id: str, expires_at: datetime) -> MintedKey:
        self._deadline(expires_at)  # Invalid lifetime makes ZERO admin calls.
        if type(self._user) is not str or not self._user or type(operation_id) is not str or not operation_id:
            raise MintRefused("refused")
        clean = False
        try:
            clean = self._cleanup(reserve_slot=True)
        except BaseException:
            pass
        if not clean:
            raise MintRefused("refused")
        now, deadline = self._deadline(expires_at)  # Cleanup may have consumed time.
        reserved = False
        try:
            reserved = self._records.reserve(operation_id, CreationWindow(now, deadline)) is True
        except BaseException:
            pass
        if not reserved:
            raise MintRefused("refused")
        # Store completion may consume time too; never start with expired input.
        _, deadline = self._deadline(expires_at)
        refused, failed = False, False
        try:
            record = self._admin.create_pre_auth_key(user=self._user, reusable=False,
                                                     ephemeral=False, expiration=deadline,
                                                     timeout_s=_TIMEOUT_S)
        except AdminRefused:
            refused = True
        except BaseException:
            failed = True
        if refused:
            raise MintRefused("refused")
        if not failed:
            try:
                record = self._record(record)
                if (not record.reusable and not record.ephemeral and not record.used and not record.acl_tags
                        and record.expiration <= deadline and record.expiration > _utc(self._now_fn())):
                    return MintedKey(record.key, record.expiration)
            except BaseException:
                pass
        raise MintUncertain()

    def mint(self, operation_id: str, expires_at: datetime) -> MintedKey:
        # Raise OUTSIDE handlers: `from None` alone retains secret __context__.
        # Pre-create failures guarantee refusal; all create/validation failures
        # are uncertain, except the transport's explicit pre-change guarantee.
        refused = False
        try:
            # Datetime/clock/config/store failures before _mint's create are
            # sanitized too. _mint marks only proven no-create cases refused.
            return self._mint(operation_id, expires_at)
        except MintRefused:
            refused = True
        except BaseException:
            pass
        del self, operation_id, expires_at
        if refused:
            raise MintRefused("refused") from None
        raise MintUncertain() from None

    def recover(self, operation_id: str) -> bool:
        """Expire all unused keys of the dedicated user, even without a journal.

        No window filter: a lost create response or journal read must not hide
        a key. Independent complete re-list proves absence, including by time.
        Never delete the reservation or mint here; repeated recovery is safe.
        """
        try:
            if type(self._user) is not str or not self._user:
                return False
            return self._cleanup()
        except BaseException:
            return False
