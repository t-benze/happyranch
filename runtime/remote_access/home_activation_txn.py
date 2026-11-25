"""Closed B4 journal and A5 store. No entry point or recovery authority."""
from __future__ import annotations

import fcntl
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Iterator, Mapping

from runtime.remote_access.home_activation import (
    ACTIVATION_PATH, BINDING_KEYS, TRANSACTION_FORMAT, HomeActivationError,
    HomeActivationReceipt, HomeActivationStore, _decode_record, _digest, _encode_record,
)
from runtime.remote_access.headscale_service import HeadscaleServiceError
from runtime.remote_access.installer import InstallerError

TRANSACTION_PATH = "/var/lib/happyranch-remote-access/home-activation-txn.json"
EFFECTS = ("preflight", "headscale_start_verify", "db_users", "local_node_mint",
           "credential_staging", "home_config", "managed_services", "joined")
# Secret credentials are verified in custody by the owner, never hashed/journaled (B1).
CONFIG_MEMBERS = ("/etc/happyranch/connector.json", "/etc/happyranch/sidecar.json")
_KEYS = frozenset(("format", *BINDING_KEYS, "operation_id", "phase", "effects"))


@dataclass(frozen=True, repr=False)
class Effect:
    name: str
    state: str
    member_digests: Mapping[str, str]

    def __post_init__(self) -> None:
        if (type(self.name) is not str or self.name not in EFFECTS
                or type(self.state) is not str or self.state not in ("intent", "done")
                or not isinstance(self.member_digests, Mapping)):
            raise HomeActivationError()
        required = set(CONFIG_MEMBERS) if self.name == "home_config" and self.state == "done" else set()
        if self.member_digests.keys() != required:
            raise HomeActivationError()
        for digest in self.member_digests.values():
            _digest(digest)
        object.__setattr__(self, "member_digests", MappingProxyType(dict(self.member_digests)))

    def __repr__(self) -> str:
        return "Effect(<redacted>)"

    def to_dict(self) -> dict:
        return dict(name=self.name, state=self.state, member_digests=dict(self.member_digests))


@dataclass(frozen=True, repr=False)
class HomeActivationTransaction:
    release_uid: int
    home_id: str
    control_plane_id: str
    connector_id: str
    activation_generation: int
    operation_id: str
    ca_der_sha256: str
    dial_ip: str
    phase: str
    effects: tuple[Effect, ...]
    recovery_observation: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        # Reuse all B1 identity predicates, without inventing another grammar.
        from runtime.remote_access.home_activation import MEMBER_PATHS
        HomeActivationReceipt(**self.binding(), member_digests={p: "0" * 64 for p in MEMBER_PATHS},
                              local_node_stable_id=None, home_endpoint=None, phase="prepared")
        if (type(self.phase) is not str or self.phase not in ("activating", "joined", "failed_uncertain")
                or type(self.effects) is not tuple or not 1 <= len(self.effects) <= len(EFFECTS)):
            raise HomeActivationError()
        for index, effect in enumerate(self.effects):
            if (type(effect) is not Effect or effect.name != EFFECTS[index]
                    or (index < len(self.effects) - 1 and effect.state != "done")):
                raise HomeActivationError()
        complete = len(self.effects) == len(EFFECTS) and self.effects[-1].state == "done"
        if self.phase == "joined" and not complete:
            raise HomeActivationError()
        if self.recovery_observation is not None:
            pair = self.recovery_observation
            if (not isinstance(pair, Mapping) or pair.keys() != {"local_node_stable_id", "home_endpoint"}
                    or self.phase not in ("joined", "failed_uncertain") or not complete):
                raise HomeActivationError()
            HomeActivationReceipt(**self.binding(), member_digests={p: "0" * 64 for p in MEMBER_PATHS},
                                  phase="ready", **pair)
            object.__setattr__(self, "recovery_observation", MappingProxyType(dict(pair)))

    def __repr__(self) -> str:
        return "HomeActivationTransaction(<redacted>)"

    def binding(self) -> dict:
        return {key: getattr(self, key) for key in (*BINDING_KEYS, "operation_id")}

    @classmethod
    def begin(cls, receipt: HomeActivationReceipt) -> HomeActivationTransaction:
        return cls(**{key: getattr(receipt, key) for key in (*BINDING_KEYS, "operation_id")},
                   phase="activating", effects=(Effect("preflight", "intent", {}),))

    def matches(self, receipt: HomeActivationReceipt) -> bool:
        return self.binding() == {key: getattr(receipt, key) for key in self.binding()}

    def to_bytes(self) -> bytes:
        record = dict(format=TRANSACTION_FORMAT, **self.binding(), phase=self.phase,
                      effects=[effect.to_dict() for effect in self.effects])
        if self.recovery_observation is not None:
            record["recovery_observation"] = dict(self.recovery_observation)
        return _encode_record(record)

    @classmethod
    def parse(cls, data: bytes) -> HomeActivationTransaction:
        # Old records retain their exact key set and byte encoding.
        try:
            record = _decode_record(data, _KEYS, TRANSACTION_FORMAT)
        except HomeActivationError:
            record = _decode_record(data, _KEYS | {"recovery_observation"}, TRANSACTION_FORMAT)
            if record["recovery_observation"] is None:
                raise HomeActivationError()
        record.pop("format")
        effects = record["effects"]
        if (type(effects) is not list or any(type(item) is not dict or item.keys() !=
                                           {"name", "state", "member_digests"} for item in effects)):
            raise HomeActivationError()
        record["effects"] = tuple(Effect(**item) for item in effects)
        return cls(**record)


def validate_transaction_transition(previous: HomeActivationTransaction,
                                    updated: HomeActivationTransaction) -> None:
    if (previous.binding() != updated.binding() or previous.phase == "failed_uncertain"
            or previous.recovery_observation != updated.recovery_observation):
        raise HomeActivationError("transition")
    if updated.phase == "failed_uncertain" and updated.effects == previous.effects:
        return
    if previous.phase != "activating":
        raise HomeActivationError("transition")
    before, after = previous.effects, updated.effects
    append = (before[-1].state == "done" and len(after) == len(before) + 1
              and after[:-1] == before and after[-1].state == "intent")
    finish = (before[-1].state == "intent" and len(after) == len(before)
              and after[:-1] == before[:-1] and after[-1].name == before[-1].name
              and after[-1].state == "done")
    close = (before == after and len(before) == len(EFFECTS) and before[-1].state == "done"
             and updated.phase == "joined")
    if not (close or (updated.phase == "activating" and (append or finish))):
        raise HomeActivationError("transition")


class HomeActivationTransactionStore(HomeActivationStore):
    @contextmanager
    def _parent(self, path: str) -> Iterator[tuple[int, Callable[[], None]]]:
        if path != TRANSACTION_PATH:
            raise HomeActivationError("state")
        with super()._parent(ACTIVATION_PATH) as parent:
            yield parent

    @contextmanager
    def locked(self, *, wait: float = 0.0) -> Iterator[int]:
        """One directory lock spans both records and every effect.

        Nonblocking by default; only unattended renewal opts into bounded wait.
        """
        if type(wait) not in (int, float) or not 0 <= wait <= 20.0:
            raise HomeActivationError("state")
        try:
            with self._parent(TRANSACTION_PATH) as (parent, revalidate):
                deadline = time.monotonic() + wait
                while True:
                    try:
                        fcntl.flock(parent, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise
                        time.sleep(min(0.25, remaining))
                revalidate()
                yield parent
                revalidate()
        except (OSError, InstallerError, HeadscaleServiceError):
            raise HomeActivationError("state") from None

    def read_optional(self) -> HomeActivationTransaction | None:
        try:
            with self._parent(TRANSACTION_PATH) as (parent, revalidate):
                self._io._headscale_no_pending(parent, Path(TRANSACTION_PATH).name)
                try:
                    os.stat(Path(TRANSACTION_PATH).name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    revalidate()
                    return None
            with self._record(TRANSACTION_PATH) as data:
                return HomeActivationTransaction.parse(data)
        except (OSError, InstallerError, HeadscaleServiceError):
            raise HomeActivationError("state") from None

    def publish(self, transaction: HomeActivationTransaction, *,
                expected: HomeActivationTransaction | None = None) -> None:
        if type(transaction) is not HomeActivationTransaction:
            raise HomeActivationError()
        if expected is None:
            if transaction.phase != "activating" or transaction.effects != (Effect("preflight", "intent", {}),):
                raise HomeActivationError("transition")
        else:
            validate_transaction_transition(expected, transaction)
        self._publish_record(TRANSACTION_PATH, transaction.to_bytes(), expected=expected,
                             parse=HomeActivationTransaction.parse)
