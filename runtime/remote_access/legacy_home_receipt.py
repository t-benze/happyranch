"""Historical home receipt schema retained solely to recognize retirement lineage.

The closed member set and validators are the original home-activation@1
contract. Parsing this record never grants admission, provisioning, cleanup or
service authority. The retiring activation owner temporarily re-exports these
same types while its callers are adapted; the retirement reader owns no writer.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

from runtime.remote_access import invite_v2
from runtime.remote_access.state_store import CorruptTrustStateError, _require_identity_text

MAX_RECORD_BYTES = 16 * 1024
ACTIVATION_PATH = "/var/lib/happyranch-remote-access/home-activation.json"
INVITE_CONFIG_PATH = "/etc/happyranch/invite-issuer.json"
# B4 fixes the format only. G2-C owns the transaction's closed member set and I/O.
TRANSACTION_FORMAT = "home-activation-txn@1"
MEMBER_PATHS = (
    "/etc/systemd/system/happyranch-headscale.service",
    "/etc/happyranch/headscale/config.yaml",
    "/etc/happyranch/headscale/derp.yaml",
    "/etc/happyranch/headscale/policy.hujson",
    "/etc/happyranch/control-ca.pem",
    INVITE_CONFIG_PATH,
)
LIVE_MEMBERS = (
    "/var/lib/happyranch-headscale/db.sqlite",
    "/var/lib/happyranch-headscale/db.sqlite-wal",
    "/var/lib/happyranch-headscale/db.sqlite-shm",
    "/var/lib/happyranch-headscale/noise.key",
    "/var/lib/happyranch-headscale/derp.key",
    "/run/happyranch-headscale/headscale.sock",
    "/run/happyranch-tsnet-sidecar/observe.sock",
    "/var/lib/happyranch-headscale/acme-unused",
)
OWNED_LINK = "/etc/systemd/system/happyranch-managed.target.wants/happyranch-headscale.service"
OWNED_LINK_TARGET = MEMBER_PATHS[0]
CONTROL_URL = "https://control.happyranch.invalid:8443"
BINDING_KEYS = (
    "release_uid", "home_id", "control_plane_id", "connector_id",
    "activation_generation", "ca_der_sha256", "dial_ip",
)
IDENTITY_KEYS = tuple(key for key in BINDING_KEYS if key != "activation_generation")
_RECEIPT_KEYS = frozenset((
    "format", *BINDING_KEYS, "operation_id", "member_digests",
    "local_node_stable_id", "home_endpoint", "phase", "ownership",
))
_PHASES = ("prepared", "activating", "joined", "ready", "failed_uncertain")


class HomeActivationError(ValueError):
    """Closed, input-free diagnostics, including underlying filesystem errors."""

    def __init__(self, category: str = "record") -> None:
        super().__init__(category if type(category) is str and category in
                         {"record", "state", "transition", "binding"} else "record")


def _unique_pairs(items: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in items:
        if key in result:
            raise HomeActivationError()
        result[key] = value
    return result


def _not_integer(token: str) -> int:
    raise HomeActivationError()


def _decode_record(data: bytes, keys: frozenset[str], format_name: str) -> dict:
    if type(data) is not bytes or not 0 < len(data) <= MAX_RECORD_BYTES:
        raise HomeActivationError()
    try:
        record = json.loads(data.decode("utf-8"), object_pairs_hook=_unique_pairs,
                            parse_float=_not_integer, parse_constant=_not_integer)
        if type(record) is not dict or record.keys() != keys or record["format"] != format_name:
            raise HomeActivationError()
        return record
    except (ValueError, UnicodeError, RecursionError):
        raise HomeActivationError() from None


def _encode_record(record: dict) -> bytes:
    try:
        data = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError):
        raise HomeActivationError() from None
    if not 0 < len(data) <= MAX_RECORD_BYTES:
        raise HomeActivationError()
    return data


def _generation(value: object) -> None:
    if type(value) is not int or value < 0:
        raise HomeActivationError()


def _digest(value: object) -> None:
    if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise HomeActivationError()


def _validate_identity(record: Any) -> None:
    if type(record.release_uid) is not int or not 0 < record.release_uid < 2**32 - 1:
        raise HomeActivationError()
    for name in ("home_id", "control_plane_id", "connector_id"):
        value = getattr(record, name)
        if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value) is None:
            raise HomeActivationError()
    _digest(record.ca_der_sha256)
    if type(record.dial_ip) is not str or not invite_v2._canonical_dial_ip(record.dial_ip):
        raise HomeActivationError()


def _validate_binding(record: Any) -> None:
    _validate_identity(record)
    _generation(record.activation_generation)


def _endpoint(value: object) -> None:
    if type(value) is not str or not invite_v2._endpoint_valid(value):
        raise HomeActivationError()


@dataclass(frozen=True, repr=False)
class OwnershipInventory:
    """The B6 list and B5 link are literals, never caller-defined paths."""

    def __repr__(self) -> str:
        return "OwnershipInventory(<fixed>)"

    def to_dict(self) -> dict:
        return {"members": list(LIVE_MEMBERS), "links": {OWNED_LINK: OWNED_LINK_TARGET}}

    @classmethod
    def parse(cls, value: object) -> OwnershipInventory:
        if (type(value) is not dict or value.keys() != {"members", "links"}
                or type(value["members"]) is not list
                or any(type(item) is not str for item in value["members"])
                or len(value["members"]) != len(LIVE_MEMBERS)
                or set(value["members"]) != set(LIVE_MEMBERS)
                or type(value["links"]) is not dict
                or value["links"] != {OWNED_LINK: OWNED_LINK_TARGET}):
            raise HomeActivationError()
        return cls()


@dataclass(frozen=True, repr=False)
class HomeActivationReceipt:
    release_uid: int
    home_id: str
    control_plane_id: str
    connector_id: str
    activation_generation: int
    operation_id: str
    ca_der_sha256: str
    dial_ip: str
    member_digests: Mapping[str, str]
    local_node_stable_id: str | None
    home_endpoint: str | None
    phase: str
    ownership: OwnershipInventory = field(default_factory=OwnershipInventory)

    def __post_init__(self) -> None:
        _validate_binding(self)
        if (type(self.operation_id) is not str or re.fullmatch(r"[0-9a-f]{32}", self.operation_id) is None
                or type(self.phase) is not str or self.phase not in _PHASES
                or type(self.ownership) is not OwnershipInventory
                or not isinstance(self.member_digests, Mapping)
                or self.member_digests.keys() != set(MEMBER_PATHS)):
            raise HomeActivationError()
        for value in self.member_digests.values():
            _digest(value)
        object.__setattr__(self, "member_digests", MappingProxyType(dict(self.member_digests)))
        observed = self.local_node_stable_id is not None
        if (observed != (self.home_endpoint is not None)
                or (self.phase in ("prepared", "activating") and observed)
                or (self.phase == "ready" and not observed)):
            raise HomeActivationError()
        if observed:
            try:
                _require_identity_text(self.local_node_stable_id)
            except CorruptTrustStateError:
                raise HomeActivationError() from None
            _endpoint(self.home_endpoint)

    def __repr__(self) -> str:
        return "HomeActivationReceipt(<redacted>)"

    def to_bytes(self) -> bytes:
        record = {key: getattr(self, key) for key in (*BINDING_KEYS, "operation_id",
                  "local_node_stable_id", "home_endpoint", "phase")}
        record.update(format="home-activation@1", member_digests=dict(self.member_digests),
                      ownership=self.ownership.to_dict())
        return _encode_record(record)

    @classmethod
    def parse(cls, data: bytes) -> HomeActivationReceipt:
        record = _decode_record(data, _RECEIPT_KEYS, "home-activation@1")
        record.pop("format")
        record["ownership"] = OwnershipInventory.parse(record["ownership"])
        if type(record["member_digests"]) is not dict:
            raise HomeActivationError()
        return cls(**record)


EFFECTS = ("preflight", "headscale_start_verify", "db_users", "local_node_mint",
           "credential_staging", "home_config", "managed_services", "joined")
# Secret credentials are verified in custody by the owner, never hashed/journaled (B1).
CONFIG_MEMBERS = ("/etc/happyranch/connector.json", "/etc/happyranch/sidecar.json")
_TRANSACTION_KEYS = frozenset(("format", *BINDING_KEYS, "operation_id", "phase", "effects"))


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
            record = _decode_record(data, _TRANSACTION_KEYS, TRANSACTION_FORMAT)
        except HomeActivationError:
            record = _decode_record(data, _TRANSACTION_KEYS | {"recovery_observation"}, TRANSACTION_FORMAT)
            if record["recovery_observation"] is None:
                raise HomeActivationError()
        record.pop("format")
        effects = record["effects"]
        if (type(effects) is not list or any(type(item) is not dict or item.keys() !=
                                           {"name", "state", "member_digests"} for item in effects)):
            raise HomeActivationError()
        record["effects"] = tuple(Effect(**item) for item in effects)
        return cls(**record)


@dataclass(frozen=True, repr=False)
class HomeInviteConfigRecord:
    """Historical B1 invite binding, decoded without importing an issuer."""

    release_uid: int
    home_id: str
    control_plane_id: str
    connector_id: str
    ca_der_sha256: str
    dial_ip: str
    control_url: str

    def __post_init__(self) -> None:
        _validate_identity(self)
        if type(self.control_url) is not str or self.control_url != CONTROL_URL:
            raise HomeActivationError()

    @classmethod
    def parse(cls, data: bytes) -> HomeInviteConfigRecord:
        record = _decode_record(data, frozenset(("format", *IDENTITY_KEYS, "control_url")),
                                "home-invite-config@2")
        record.pop("format")
        return cls(**record)
