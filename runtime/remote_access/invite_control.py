"""B10 adapter for the serving connector's ONE pinned pairing owner.

Supervisor._HelperOperations supplies this capability over its serving manager;
this module constructs no manager/store/listener and performs no root minting.
The caller holds that owner's mutation_lock across control ready/commit. Each
manager method independently checks CAS and the original authorization deadline
inside the actual file transaction, after any wait for readers.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from runtime.remote_access.helper_control import Refused
from runtime.remote_access.pairing import PairingError, PinnedPairingManager
from runtime.remote_access.revocation import RevocationIncomplete
from runtime.remote_access.state_store import StateStoreError


class InviteControl:
    def __init__(self, pairing: PinnedPairingManager) -> None:
        self._pairing = pairing

    @staticmethod
    def _invoke(operation: Callable[[], int]) -> int:
        try:
            return operation()
        except PairingError as error:
            category = {'trust_changed': 'changed', 'authorization_expired': 'expired'}.get(str(error), 'unavailable')
            raise Refused(category) from None
        except (StateStoreError, RevocationIncomplete, OSError, ValueError, OverflowError):
            # Publication/cleanup failure may follow a durable effect. Never
            # expose raw exceptions or interpret unavailable as no change.
            raise Refused('unavailable') from None

    def reserve_invite(self, generation: int, digest: str, expires_at: int, *, deadline: float | None = None) -> int:
        return self._invoke(lambda: self._pairing.issue_invite_record(
            digest, datetime.fromtimestamp(expires_at, timezone.utc),
            expected_generation=generation, deadline=deadline))

    def invalidate_invite(self, generation: int, *, deadline: float | None = None) -> int:
        return self._invoke(lambda: self._pairing.invalidate_invite(expected_generation=generation, deadline=deadline))

    def revoke_all(self, generation: int, *, deadline: float | None = None) -> int:
        return self._invoke(lambda: self._pairing.revoke_all(expected_generation=generation, deadline=deadline))
