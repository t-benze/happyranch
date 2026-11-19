"""Protected TEST account invite composition for the fixed root helper only."""
from __future__ import annotations

import hashlib
import json
import math
import os
import ssl
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Callable
from types import MappingProxyType

from . import account_enrollment as account
from .helper_control import ControlClient, Refused, Snapshot
from .home_invite_records import (
    ACTIVATION_PATH, BINDING_KEYS, INVITE_CONFIG_PATH, HomeActivationReceipt,
    HomeInviteConfigRecord,
)
from .invite_issuer import HomeInviteConfig, HomeInviteIssuer, _pairs
from .invite_recovery import cancel_invite, recover_invite

if TYPE_CHECKING:
    from .helper_owner import Owner

MUTATIONS = {'IssueInvite': 'issue-invite', 'CancelInvite': 'cancel-invite', 'RecoverInvite': 'recover-invite'}


@dataclass(frozen=True, repr=False)
class Binding:
    config: bytes
    receipt: bytes
    invite_config: bytes
    services_ca: bytes
    credential_digest: str
    ca_der: bytes = field(repr=False)
    client: account.ServicesClient = field(compare=False, repr=False)
    home: HomeInviteConfig = field(compare=False, repr=False)


def binding(uid: int) -> Binding:
    # Every path is a fixed package literal, never request/env/argv input.
    raw = account.private_read(account.CONFIG, uid=0, limit=4096)
    config = json.loads(raw, object_pairs_hook=_pairs)
    keys = {'format', 'enabled', 'environment', 'services_origin', 'binding_id', *BINDING_KEYS}
    if (type(config) is not dict or config.keys() != keys or config['format'] != 'account-invite@1'
            or config['enabled'] is not True or config['environment'] not in ('TEST', 'development')
            or config['release_uid'] != uid):
        raise Refused('unavailable')
    account.opaque(config['binding_id'], 'bind')
    credential = account.secret(account.private_read(account.CREDENTIAL, uid=0, limit=43).decode('ascii'))
    services_ca = account.private_read(account.SERVICES_CA, uid=0, limit=16384)
    receipt_raw = account.private_read(Path(ACTIVATION_PATH), uid=0, limit=16384)
    config_raw = account.private_read(Path(INVITE_CONFIG_PATH), uid=0, limit=16384, private_parent=False)
    # Match the activation reader's pending-entry refusal; never repair.
    for path in (Path(ACTIVATION_PATH), Path(INVITE_CONFIG_PATH)):
        try:
            os.lstat(path.with_name('.' + path.name + '.pending'))
        except FileNotFoundError:
            pass
        else:
            raise Refused('unavailable')
    receipt, invite = HomeActivationReceipt.parse(receipt_raw), HomeInviteConfigRecord.parse(config_raw)
    if (receipt.phase != 'ready' or any(type(config[k]) is not type(getattr(receipt, k))
            or config[k] != getattr(receipt, k) for k in BINDING_KEYS)
            or any(getattr(invite, k) != getattr(receipt, k) for k in BINDING_KEYS)
            or invite.home_endpoint != receipt.home_endpoint
            or hashlib.sha256(config_raw).hexdigest() != receipt.member_digests[INVITE_CONFIG_PATH]):
        raise Refused('changed')
    ca_pem = account.private_read(Path('/etc/happyranch/control-ca.pem'), uid=0, limit=16384, private_parent=False)
    ca_der = ssl.PEM_cert_to_DER_cert(ca_pem.decode('ascii'))
    if (hashlib.sha256(ca_der).hexdigest() != invite.ca_der_sha256
            or hashlib.sha256(ca_pem).hexdigest() != receipt.member_digests['/etc/happyranch/control-ca.pem']):
        raise Refused('changed')
    client = account.ServicesClient(config['services_origin'], config['binding_id'], credential, services_ca.decode('ascii'))
    home = HomeInviteConfig(invite.home_id, invite.control_plane_id, invite.connector_id,
                            ca_der, invite.dial_ip, invite.home_endpoint)
    return Binding(raw, receipt_raw, config_raw, services_ca, hashlib.sha256(credential.encode()).hexdigest(),
                   ca_der, client, home)


def read_permit(data_home: Path, uid: int) -> MappingProxyType:
    raw = account.private_read(data_home / 'account-invite/request.json', uid=uid, limit=1024)
    value = json.loads(raw, object_pairs_hook=_pairs)
    if type(value) is not dict or value.keys() != {'enrollment_id', 'permit', 'verifier'}:
        raise Refused('invalid-request')
    account.opaque(value['enrollment_id'], 'enr')
    account.secret(value['permit'])
    account.secret(value['verifier'])
    return MappingProxyType(value)


@dataclass(frozen=True, repr=False)
class Request:
    protected: Binding
    permit: MappingProxyType | None = field(repr=False)
    permit_digest: str | None


def target(protected: Binding) -> dict[str, str]:
    return {key: getattr(protected.home, key) for key in ('home_id', 'control_plane_id', 'connector_id')}


def capture(owner: Owner, snapshot: Snapshot, method: str, control: ControlClient) -> Request:
    protected = binding(owner.uid)
    if not snapshot.owned or (method == 'IssueInvite' and not snapshot.ready):
        raise Refused('unavailable')
    if control.invite_binding(snapshot.generation) != target(protected):
        raise Refused('changed')
    permit = read_permit(owner.data_home, owner.uid) if method == 'IssueInvite' else None
    digest = None if permit is None else hashlib.sha256(json.dumps(dict(permit), sort_keys=True).encode()).hexdigest()
    return Request(protected, permit, digest)


class PairingProxy:
    """Digest/deadline proxy only; the serving supervisor owns all ceremony state."""
    def __init__(self, control: ControlClient, generation: int, deadline: float,
                 before_commit: Callable[[], None], target: dict[str, str] | None = None) -> None:
        self.control, self.generation = control, generation
        self.deadline, self.before = deadline, before_commit
        self.target = target

    def issue_invite_record(self, digest: str, expires_at: datetime) -> int:
        self.generation = self.control.reserve_invite(self.generation, digest, math.floor(expires_at.timestamp()),
                                                      self.deadline, self.before, target=self.target)
        return self.generation

    def invalidate_invite(self, generation: int, deadline: float, before_commit: Callable[[], None]) -> int:
        self.generation = self.control.invalidate_invite(generation, deadline, before_commit, target=self.target)
        return self.generation


def execute(request: Request, *, method: str, uid: int, snapshot: Snapshot, control: ControlClient,
            deadline: float, before_commit: Callable[[], None]) -> str | dict:
    def revalidate() -> None:
        account.check(deadline)
        before_commit()
        if binding(uid) != request.protected:
            raise Refused('changed')
        account.check(deadline)

    revalidate()
    proxy = PairingProxy(control, snapshot.generation, deadline, revalidate, target(request.protected))

    def before_effect() -> None:
        revalidate()
        current = control.status()
        if (not current.owned or (method == 'IssueInvite' and not current.ready) or current.release_uid != uid
                or current.generation != proxy.generation):
            raise Refused('changed')
        if control.invite_binding(current.generation) != proxy.target:
            raise Refused('changed')
        account.check(deadline)

    minter = account.AccountEnrollmentMinter(client=request.protected.client, permit=request.permit,
        state_dir=account.STATE, deadline=deadline, before_effect=before_effect)
    issuer = HomeInviteIssuer(config=request.protected.home, minter=minter, pairing=proxy, state_dir=account.STATE)
    if method == 'IssueInvite':
        # A consumed delivery cannot prove the caller received the D-Bus reply.
        # Require explicit fresh-auth reconciliation before replacing it.
        with issuer._recovery_record() as (_, current):
            if current is not None and current['outcome'] not in ('recovered', 'refused'):
                raise Refused('unavailable')
        result = issuer.issue()
        before_effect()  # Final live caller/deadline check AFTER consumed delivery.
        return result.invite_text
    action = cancel_invite if method == 'CancelInvite' else recover_invite
    result = action(issuer=issuer, control=proxy, minter=minter, generation=snapshot.generation,
        deadline=deadline, before_commit=revalidate, after_cleanup=minter.settle_recovery)
    revalidate()
    return {'outcome': result.outcome.value, 'invite': result.invite.value, 'cleanup': result.cleanup.value}
