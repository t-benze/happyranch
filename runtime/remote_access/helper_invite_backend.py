"""Private B10/B11 composition in the existing fixed bundled-Python root child."""
from __future__ import annotations

import hashlib
import json
import ssl
import time
from datetime import datetime
from typing import Callable

from runtime.remote_access import home_admin, invite_recovery
from runtime.remote_access.helper_control import ControlClient
from runtime.remote_access.home_activation import (
    IDENTITY_KEYS, INVITE_CONFIG_PATH, HomeActivationStore,
)
from runtime.remote_access.installer import HOME_ISSUER, ManagedInstaller
from runtime.remote_access.invite_issuer import HomeInviteConfig, HomeInviteConfigRecord, HomeInviteIssuer, IssuerError
from runtime.remote_access.home_minter import HeadscaleJoinKeyMinter, PreAuthKey


class HelperInviteBackend:
    def __init__(self, io: ManagedInstaller) -> None:
        self.io = io
        self.store = HomeActivationStore(root=io.root, root_owner=io.root_owner)

    def _config(self, uid: int) -> tuple[dict, HomeInviteConfig]:
        receipt = self.store.current()
        if receipt is None or receipt.phase != "ready" or receipt.release_uid != uid:
            raise ValueError('refused')
        with self.store._record(INVITE_CONFIG_PATH) as raw:
            config = HomeInviteConfigRecord.parse(raw)
            if (any(getattr(config, key) != getattr(receipt, key) for key in IDENTITY_KEYS)
                    or hashlib.sha256(raw).hexdigest() != receipt.member_digests[INVITE_CONFIG_PATH]):
                raise ValueError('refused')
        # Reuse installer custody, never read the CA private key or discover a path.
        self.io._service_owner = self.io._account(create=False)
        if self.io._service_owner is None:
            raise ValueError('refused')
        path = '/etc/happyranch/control-ca.pem'
        owner, mode = self.io._headscale_members()[path]
        with self.io._publication_parent('etc/happyranch') as (parent, check):
            self.io._headscale_no_pending(parent, 'control-ca.pem')
            raw_ca = self.io._read_file(parent, 'control-ca.pem', owner=owner, mode=mode)
            der = ssl.PEM_cert_to_DER_cert(raw_ca.decode('ascii'))
            if (hashlib.sha256(raw_ca).hexdigest() != receipt.member_digests[path]
                    or hashlib.sha256(der).hexdigest() != receipt.ca_der_sha256):
                raise ValueError('refused')
            check()
        token = dict(uid=uid, generation=receipt.activation_generation, operation=receipt.operation_id,
                     configuration=receipt.member_digests[INVITE_CONFIG_PATH], ca=receipt.ca_der_sha256)
        return token, HomeInviteConfig(home_id=config.home_id, control_plane_id=config.control_plane_id,
            connector_id=config.connector_id, ca_der=der, dial_ip=config.dial_ip, home_endpoint=receipt.home_endpoint)

    def capture(self, uid: int) -> str:
        token, _ = self._config(uid)
        return json.dumps(token, sort_keys=True, separators=(',', ':'))

    def dispatch(self, method: str, uid: int, step: str, expected: str | None, *,
                 deadline: float, before_commit: Callable[[], None]) -> dict:
        if method not in ('IssueInvite', 'CancelInvite', 'RecoverInvite') or type(uid) is not int or uid < 1000:
            raise ValueError('refused')
        if step == 'capture' and expected is None:
            from runtime.remote_access.home_activation_backend import HelperLifecycleBackend
            return {'snapshot': HelperLifecycleBackend(self.io).capture(method, uid)}
        if method == 'IssueInvite' and step == 'status':
            if time.monotonic() >= deadline:
                raise ValueError('refused')
            token, config = self._config(uid)
            binding = json.dumps({k: token[k] for k in ('uid', 'generation', 'operation')},
                                 sort_keys=True, separators=(',', ':'))
            if type(expected) is not str or expected != binding:
                raise ValueError('refused')
            # observe() uses only its existing protected journal reader. No
            # admin/minter, serving pairing owner, lock, commit or repair.
            issuer = HomeInviteIssuer(config=config, minter=None, pairing=None,
                                      state_dir=self.io.root / HOME_ISSUER)
            observed = issuer.observe()
            if self._config(uid)[0] != token or time.monotonic() >= deadline:
                raise ValueError('refused')
            return dict(invite=observed.phase, expires_at=observed.expires_at)
        self.dispatched = False
        try:
            if method == 'IssueInvite':
                return self._issue(uid, step, expected, deadline, before_commit)
            return self._recover(method, uid, step, expected, deadline, before_commit)
        except Exception:
            # Preserve uncertainty if even lock release/result delivery failed
            # after B11 ran. Normal backend result tuples pass through verbatim.
            if method == 'IssueInvite':
                return {'result': 'uncertain' if self.dispatched else 'refused'}
            return (dict(outcome='uncertain', invite='uncertain', cleanup='uncertain') if self.dispatched
                    else dict(outcome='refused', invite='unknown', cleanup='unknown'))

    def _issue(self, uid: int, step: str, expected: str | None,
               deadline: float, before_commit: Callable[[], None]) -> dict:
        if step != 'issue' or type(expected) is not str or self.capture(uid) != expected:
            raise ValueError('refused')

        def revalidate() -> None:
            invite_recovery._check_deadline(deadline)
            before_commit()
            if self.capture(uid) != expected:
                raise ValueError('refused')
            invite_recovery._check_deadline(deadline)

        revalidate()
        _, config = self._config(uid)
        control = ControlClient()
        snapshot = control.status()  # Unavailable connector refuses BEFORE mint.
        if not snapshot.owned or snapshot.release_uid != uid:
            raise ValueError('refused')
        revalidate()

        def dispatched() -> None:
            revalidate()
            self.dispatched = True  # Commit may be sent, even if its reply is lost.

        pairing = _ServingInvites(control, snapshot.generation, deadline, dispatched)
        with home_admin._locked() as locked:
            revalidate()
            # Same existing minter, records and user lock; only clip its admin RPCs
            # to this call's original budget and carry the live-caller hook.
            minter = HeadscaleJoinKeyMinter(admin=_IssueAdmin(locked._admin, deadline, revalidate),
                invite_user=locked._user, now_fn=locked._now_fn, records=locked._records)
            issuer = HomeInviteIssuer(config=config, minter=minter, pairing=pairing,
                state_dir=self.io.root / HOME_ISSUER, now_fn=locked._now_fn)
            try:
                issued = issuer.issue()
            except IssuerError as error:
                code = error.args[0]
                if code == 'rate_limited' and not self.dispatched:
                    return {'result': 'rate_limited'}  # Before journal write, invalidation or mint.
                if code in ('refused', 'busy'):
                    return {'result': 'refused'}
                if self.dispatched:
                    # Keep the existing journal vocabulary/rate fence. B11 selects
                    # this same current entry, never a new operation or delivery.
                    with issuer._recovery_record() as (parent, current):
                        if current is not None:
                            current['outcome'] = 'uncertain'
                            issuer._write(parent, current)
                return {'result': 'uncertain' if self.dispatched or code == 'uncertain' else 'refused'}
            revalidate()
            return {'result': 'complete', 'invite': issued.invite_text, 'expires_at': issued.expires_at}

    def _recover(self, method: str, uid: int, step: str, expected: str | None,
                 deadline: float, before_commit: Callable[[], None]) -> dict:
        if step != 'recover' or type(expected) is not str or self.capture(uid) != expected:
            raise ValueError('refused')
        invite_recovery._check_deadline(deadline)
        _, config = self._config(uid)
        control = ControlClient()

        def revalidate() -> None:
            invite_recovery._check_deadline(deadline)
            before_commit()  # Actual live bus UID + B8 binding in the Gio parent.
            if self.capture(uid) != expected:
                raise ValueError('refused')
            invite_recovery._check_deadline(deadline)

        # The existing per-invite-user directory lock/store/transport only.
        # No local pairing manager is constructed: B10 uses the serving owner.
        revalidate()
        with home_admin._locked() as locked:
            invite_recovery._check_deadline(deadline)
            minter = invite_recovery.HeadscaleRecoveryMinter(admin=locked._admin,
                invite_user=locked._user, now_fn=locked._now_fn, records=locked._records)
            issuer = HomeInviteIssuer(config=config, minter=locked, pairing=None,
                state_dir=self.io.root / HOME_ISSUER, now_fn=locked._now_fn)
            # Safely absent current state needs no connector or key-cleanup
            # availability. Release this short read before the serving IPC.
            with issuer._recovery_record() as (_, current):
                absent = current is None
            generation = 0
            if not absent:
                snapshot = control.status()
                if not snapshot.owned or snapshot.release_uid != uid:
                    raise ValueError('refused')
                generation = snapshot.generation
            operation = (invite_recovery.cancel_invite if method == 'CancelInvite'
                         else invite_recovery.recover_invite)
            self.dispatched = True
            result = operation(issuer=issuer, control=control, minter=minter, generation=generation,
                               deadline=deadline, before_commit=revalidate)
        return dict(outcome=result.outcome.value, invite=result.invite.value, cleanup=result.cleanup.value)


class _ServingInvites:
    """Issuer's digest-only facade over the ONE serving B10 owner.

    Preserve the core's undeliverable random reservation for invalidation, then
    reserve the encoded digest. Each acknowledgement supplies the next CAS.
    No local trust store or pairing manager is constructed here.
    """

    def __init__(self, control: ControlClient, generation: int, deadline: float,
                 before_commit: Callable[[], None]) -> None:
        self.control, self.generation = control, generation
        self.deadline, self.before_commit = deadline, before_commit

    def issue_invite_record(self, digest: str, expires_at: datetime) -> None:
        self.before_commit()
        self.generation = self.control.reserve_invite(self.generation, digest,
            int(expires_at.timestamp()), self.deadline, self.before_commit)
        invite_recovery._check_deadline(self.deadline)


class _IssueAdmin:
    """Bound existing local admin RPCs; no transport, store or minter owner."""

    def __init__(self, admin: object, deadline: float, revalidate: Callable[[], None]) -> None:
        self.admin, self.deadline, self.revalidate = admin, deadline, revalidate

    def _timeout(self, timeout_s: float) -> float:
        self.revalidate()
        return min(timeout_s, self.deadline - time.monotonic())

    def list_pre_auth_keys(self, *, user: str, timeout_s: float) -> tuple[PreAuthKey, ...]:
        result = self.admin.list_pre_auth_keys(user=user, timeout_s=self._timeout(timeout_s))
        self.revalidate()
        return result

    def expire_pre_auth_key(self, *, user: str, key: str, timeout_s: float) -> None:
        self.admin.expire_pre_auth_key(user=user, key=key, timeout_s=self._timeout(timeout_s))
        self.revalidate()

    def create_pre_auth_key(self, *, user: str, reusable: bool, ephemeral: bool,
                            expiration: datetime, timeout_s: float) -> PreAuthKey:
        result = self.admin.create_pre_auth_key(user=user, reusable=reusable, ephemeral=ephemeral,
            expiration=expiration, timeout_s=self._timeout(timeout_s))
        self.revalidate()
        return result
