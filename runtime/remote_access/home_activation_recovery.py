"""B7 fresh-auth recovery, privately mounted by Enable; no effect execution."""
from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from runtime.remote_access import home_admin
from runtime.remote_access.home_activation import (
    ACTIVATION_PATH, HomeActivationReceipt, validate_recovery_transition,
)
from runtime.remote_access.home_activation_coordinator import (
    ActivationOwner, ActivationRefused, HomeActivationCoordinator,
)
from runtime.remote_access.home_activation_txn import (
    CONFIG_MEMBERS, EFFECTS, TRANSACTION_PATH, Effect, HomeActivationTransaction,
)
from runtime.remote_access.home_minter import (
    HeadscaleJoinKeyMinter, PreAuthKey, _MAX_INVENTORY_KEYS,
)
from runtime.remote_access.home_observer import HomeObserver, ObservationOwner


class RecoveryAdmin(Protocol):
    """Complete bounded B3 lists only, with the existing fixed-user semantics."""

    def list_pre_auth_keys(self, *, user: str, timeout_s: float) -> tuple[PreAuthKey, ...]: ...

    def list_nodes(self, *, user: str, timeout_s: float) -> tuple[home_admin.HeadscaleNode, ...]: ...


class _RecoveryNodes:
    """Bind B2's independent fresh B3 read to the recovery singleton."""

    def __init__(self, admin: RecoveryAdmin, expected: tuple[home_admin.HeadscaleNode, ...]) -> None:
        self._admin, self._expected = admin, expected

    def list_nodes(self, *, user: str, timeout_s: float) -> tuple[home_admin.HeadscaleNode, ...]:
        current = self._admin.list_nodes(user=user, timeout_s=timeout_s)
        if current != self._expected:
            raise RecoveryRefused()
        return current


class RecoveryRefused(ValueError):
    def __init__(self, reason: str = "recovery_refused") -> None:
        super().__init__(reason if reason in ("pending_residue", "token_source_invalid", "retry_later") else "recovery_refused")


def validate_recovery_transaction(previous: HomeActivationTransaction,
                                  updated: HomeActivationTransaction) -> None:
    """A verified recovery alone may replace the terminal prefix with eight done effects."""
    if (previous.phase != "failed_uncertain" or previous.binding() != updated.binding()
            or updated.phase != "joined" or updated.recovery_observation is None
            or tuple(e.name for e in updated.effects) != EFFECTS
            or any(e.state != "done" for e in updated.effects)):
        raise RecoveryRefused()


class HomeActivationRecovery(HomeActivationCoordinator):
    def __init__(self, *, owner: ActivationOwner, admin: RecoveryAdmin,
                 root: Path = Path("/"), root_owner: tuple[int, int] = (0, 0)) -> None:
        super().__init__(owner=owner, root=root, root_owner=root_owner)
        self._admin = admin

    def __repr__(self) -> str:
        return "HomeActivationRecovery(<redacted>)"

    def recover(self, *, expected_generation: int, expected_operation_id: str,
                fresh_authenticated: bool, observation_owner: ObservationOwner) -> HomeActivationReceipt:
        """Fresh auth is a precondition supplied by the private Enable owner.

        Every refusal retains current bytes. Only Ruling 7 terminal markers may
        mutate a matching nonterminal pair; that call never enters B7 recovery.
        A5 pending residue is retained and blocks all later attempts (12a).
        """
        try:
            if fresh_authenticated is not True:
                raise RecoveryRefused()
            with self._transactions.locked():
                self._pending()
                receipt = self._receipts.read(expected_generation=expected_generation)
                txn = self._transactions.read_optional()
                if (txn is None or not txn.matches(receipt)
                        or receipt.operation_id != expected_operation_id):
                    raise RecoveryRefused()
                half = (receipt.phase == "failed_uncertain" and txn.phase == "joined"
                        and txn.recovery_observation is not None)
                if receipt.phase != "failed_uncertain" or txn.phase != "failed_uncertain":
                    if not half:
                        # Existing intent is never resumed by this entry.
                        if receipt.phase != "ready":
                            self._fail(receipt)
                        raise RecoveryRefused()
                self._source(receipt)
                self._members(receipt, txn)
                mint_intended = any(effect.name == "local_node_mint" for effect in txn.effects)
                try:
                    window = home_admin.read_local_node_window(
                        receipt.operation_id, root=self._io.root, root_owner=self._io.root_owner)
                except home_admin.NodeWindowAbsent:
                    if mint_intended:
                        raise RecoveryRefused()
                    window = None
                keys = self._admin.list_pre_auth_keys(user="happyranch-node", timeout_s=5)
                nodes = self._admin.list_nodes(user="happyranch-node", timeout_s=5)
                if (type(keys) is not tuple or len(keys) > _MAX_INVENTORY_KEYS
                        or type(nodes) is not tuple or len(nodes) > 512
                        or any(type(n) is not home_admin.HeadscaleNode or n.user.name != "happyranch-node"
                               for n in nodes) or len({n.id for n in nodes}) != len(nodes)):
                    raise RecoveryRefused()
                # Reuse the minter's strict record predicates; validate inert history too.
                validator = HeadscaleJoinKeyMinter(admin=self._admin, invite_user="happyranch-node",
                                                   now_fn=lambda: datetime.now(timezone.utc), records=None)
                ids = set()
                for key in keys:
                    validator._record(key)
                    if key.id in ids:
                        raise RecoveryRefused()
                    ids.add(key.id)
                if window is None and (keys or nodes):
                    raise RecoveryRefused()
                if (window is not None and len(keys) == 1 and len(nodes) == 1 and keys[0].used
                        and not keys[0].reusable and not keys[0].ephemeral and not keys[0].acl_tags
                        and window.started_at <= keys[0].created_at <= window.expires_at):
                    observer = HomeObserver(owner=observation_owner, admin=_RecoveryNodes(self._admin, nodes),
                                            root=self._io.root)
                    observed = observer.observe(receipt, expected_generation=expected_generation)
                    pair = dict(local_node_stable_id=observed.stable_id, home_endpoint=observed.home_endpoint)
                    if half and txn.recovery_observation != pair:
                        raise RecoveryRefused()
                    for effect in EFFECTS:
                        self._verify(effect, receipt)
                    effects = tuple(Effect(name, "done", self._digests(CONFIG_MEMBERS)
                                           if name == "home_config" else {}) for name in EFFECTS)
                    target = replace(txn, phase="joined", effects=effects, recovery_observation=pair)
                    ready = replace(receipt, phase="ready", **pair)
                    validate_recovery_transition(receipt, ready)
                    self._unchanged(receipt, txn)
                    if not half:
                        validate_recovery_transaction(txn, target)
                        self._source(receipt)
                        self._transactions._publish_record(TRANSACTION_PATH, target.to_bytes(),
                                                           expected=txn, parse=HomeActivationTransaction.parse)
                    elif target != txn:
                        raise RecoveryRefused()
                    self._unchanged(receipt, target)
                    self._source(receipt)
                    self._receipts._publish_record(ACTIVATION_PATH, ready.to_bytes(), expected=receipt,
                                                   parse=HomeActivationReceipt.parse)
                    return ready
                now = datetime.now(timezone.utc)
                if any(not k.used and k.expiration > now for k in keys):
                    raise RecoveryRefused("retry_later")
                if half or nodes:
                    raise RecoveryRefused()
                prepared = replace(receipt, phase="prepared", activation_generation=expected_generation + 1,
                                   operation_id=secrets.token_hex(16), local_node_stable_id=None, home_endpoint=None)
                validate_recovery_transition(receipt, prepared)
                self._unchanged(receipt, txn)
                self._io._remove_enrollment()
                self._unchanged(receipt, txn)
                self._source(receipt)
                self._receipts._publish_record(ACTIVATION_PATH, prepared.to_bytes(), expected=receipt,
                                               parse=HomeActivationReceipt.parse)
                return prepared
        except (ActivationRefused, RecoveryRefused) as error:
            raise RecoveryRefused(str(error)) from None
        except Exception:
            raise RecoveryRefused() from None

    def _unchanged(self, receipt: HomeActivationReceipt, txn: HomeActivationTransaction) -> None:
        self._pending()
        if (self._receipts.read(expected_generation=receipt.activation_generation) != receipt
                or self._transactions.read_optional() != txn):
            raise RecoveryRefused()
        self._members(receipt, txn)
