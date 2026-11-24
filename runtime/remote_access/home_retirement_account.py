"""Dedicated historical account disposition under fresh retirement proof.

The protected original receipt survives every account effect. Current numeric
NSS mappings, locked system-account shape, static consumers and UID processes
are re-observed; a missing name never establishes successful deletion itself.
This owner neither stops services nor publishes final retirement.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Callable

from runtime.remote_access.home_retirement import ACCOUNT, RetirementUncertain

if TYPE_CHECKING:
    from runtime.remote_access.home_retirement_journal import OriginalInventory, RetirementJournal
    from runtime.remote_access.home_retirement_service import ServiceIdentity
    from runtime.remote_access.installer import ManagedInstaller


def account_status(io: ManagedInstaller, identity: ServiceIdentity) -> tuple[bool, bool]:
    """Fresh exact names AND numeric mappings, including partial user removal."""
    from runtime.remote_access.home_retirement_inventory import LegacyInventory
    try:
        user = io._lookup_user(ACCOUNT)
    except KeyError:
        user = None
    try:
        group = io._lookup_group(ACCOUNT)
    except KeyError:
        group = None
    if user is not None:
        if group is None or LegacyInventory(io)._account(dict(version=1, account=ACCOUNT,
                uid=identity.uid, gid=identity.gid, group_created_with_user=True)) != (identity.uid, identity.gid):
            raise RetirementUncertain()
    elif group is not None and ((group.gr_name, group.gr_gid) != (ACCOUNT, identity.gid) or group.gr_mem):
        raise RetirementUncertain()
    for database in ('passwd', 'group'):
        code, output = io._run(['getent', database], timeout=10.0)
        if code or len(output.encode()) > 65536:
            raise RetirementUncertain()
        uids, gids = [], []
        for line in output.splitlines():
            fields = line.split(':')
            if (len(fields) != (7 if database == 'passwd' else 4)
                    or not fields[2].isascii() or not fields[2].isdigit()
                    or database == 'passwd' and (not fields[3].isascii() or not fields[3].isdigit())):
                raise RetirementUncertain()
            if database == 'passwd' and int(fields[2]) == identity.uid:
                uids.append(fields[0])
            if int(fields[3 if database == 'passwd' else 2]) == identity.gid:
                gids.append(fields[0])
        expected = [ACCOUNT] if (user is not None if database == 'passwd' else group is not None) else []
        if gids != expected or database == 'passwd' and uids != expected:
            raise RetirementUncertain()
        numeric = identity.uid if database == 'passwd' else identity.gid
        code, output = io._run(['getent', database, str(numeric)], timeout=10.0)
        if expected:
            fields = output.rstrip('\n').split(':')
            if (code or len(output.encode()) > 65536 or len(fields) != (7 if database == 'passwd' else 4)
                    or fields[0] != ACCOUNT or fields[2] != str(numeric)):
                raise RetirementUncertain()
        elif code != 2 or output:
            raise RetirementUncertain()
    return user is not None, group is not None


def require_retained(io: ManagedInstaller, identity: ServiceIdentity) -> frozenset[str]:
    """Read-only final retention proof against the saved original identity."""
    from runtime.remote_access.home_retirement_service import OwnedService
    if account_status(io, identity) != (True, True):
        raise RetirementUncertain()
    consumers: set[str] = set()
    controller = OwnedService(io, identity)
    if controller._links(disabled=True, account_consumers=consumers) is not None or not consumers:
        raise RetirementUncertain()
    controller._process(0)
    if account_status(io, identity) != (True, True):
        raise RetirementUncertain()
    return frozenset(consumers)


class AccountDisposition:
    def __init__(self, journal: RetirementJournal, original: OriginalInventory, *,
                 before_effect: Callable[[], None]) -> None:
        self.journal, self.original, self.before_effect = journal, original, before_effect

    def _guard(self, *, receipt_cleanup: bool = False) -> tuple[tuple[bool, bool], set[str]]:
        from runtime.remote_access.home_retirement_inventory import LegacyInventory
        from runtime.remote_access.home_retirement_service import OwnedService
        io, original = self.journal.io, self.original
        self.journal.fence.require(io)
        if self.journal.load() != original:
            raise RetirementUncertain()
        self.before_effect()
        name = 'headscale-account.json'
        saved = LegacyInventory(io)._receipt(name, self.journal.fence, optional=receipt_cleanup)
        if saved is not None and (saved[1] != original.ownership.receipt_identities[name]
                or hashlib.sha256(saved[0]).hexdigest() != original.ownership.receipt_digests[name]):
            raise RetirementUncertain()
        current = account_status(io, original.ownership.service)
        consumers: set[str] = set()
        controller = OwnedService(io, original.ownership.service)
        controller._links(disabled=True, account_consumers=consumers)
        controller._process(0)
        if account_status(io, original.ownership.service) != current:
            raise RetirementUncertain()
        self.journal.fence.require(io)
        return current, consumers

    def require_retired(self) -> str:
        """Read current final disposition without repeating account effects.

        Final receipt cleanup may already have removed the original account
        receipt. Its protected journal and mandatory complete caller proof stay
        required. Partial user/group removal is not a final disposition.
        """
        try:
            current, consumers = self._guard(receipt_cleanup=True)
            if current == (False, False) and not consumers:
                return 'absent'
            if current == (True, True) and consumers:
                return 'retained-disabled: static consumer'
            raise RetirementUncertain()
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None

    def retire(self) -> str:
        """Delete only exclusive originals; preserve a locked static consumer.

        A nonzero command preserves uncertainty, including a possibly completed
        effect. Fresh explicit recovery observes absence and never repeats that
        deletion. No home directory or unrelated data is removed by userdel.
        """
        try:
            current, consumers = self._guard()
            if consumers:
                if current != (True, True):
                    raise RetirementUncertain()
                return 'retained-disabled: static consumer'
            if current[0]:
                if self._guard() != (current, set()):
                    raise RetirementUncertain()
                code, _ = self.journal.io._run(['userdel', ACCOUNT], timeout=30.0)
                if code:
                    raise RetirementUncertain()
            current, consumers = self._guard()
            if current[0] or consumers:
                raise RetirementUncertain()
            if current[1]:
                if self._guard() != (current, set()):
                    raise RetirementUncertain()
                code, _ = self.journal.io._run(['groupdel', ACCOUNT], timeout=30.0)
                if code:
                    raise RetirementUncertain()
            if self._guard() != ((False, False), set()):
                raise RetirementUncertain()
            return 'absent'
        except (OSError, ValueError, RuntimeError, TypeError, KeyError, AttributeError):
            raise RetirementUncertain() from None
