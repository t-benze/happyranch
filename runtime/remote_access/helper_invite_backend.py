"""Private B11 composition in the existing fixed bundled-Python root child."""
from __future__ import annotations

import hashlib
import json
import ssl
from typing import Callable

from runtime.remote_access import home_admin, invite_recovery
from runtime.remote_access.helper_control import ControlClient
from runtime.remote_access.home_activation import (
    BINDING_KEYS, INVITE_CONFIG_PATH, HomeActivationStore,
)
from runtime.remote_access.installer import HOME_ISSUER, ManagedInstaller
from runtime.remote_access.invite_issuer import HomeInviteConfig, HomeInviteConfigRecord, HomeInviteIssuer


class HelperInviteBackend:
    def __init__(self, io: ManagedInstaller) -> None:
        self.io = io
        self.store = HomeActivationStore(root=io.root, root_owner=io.root_owner)

    def _config(self, uid: int) -> tuple[dict, HomeInviteConfig]:
        receipt = self.store.current()
        if receipt is None or receipt.release_uid != uid:
            raise ValueError('refused')
        with self.store._record(INVITE_CONFIG_PATH) as raw:
            config = HomeInviteConfigRecord.parse(raw)
            if (any(getattr(config, key) != getattr(receipt, key) for key in BINDING_KEYS)
                    or config.home_endpoint != receipt.home_endpoint
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
            connector_id=config.connector_id, ca_der=der, dial_ip=config.dial_ip, home_endpoint=config.home_endpoint)

    def capture(self, uid: int) -> str:
        token, _ = self._config(uid)
        return json.dumps(token, sort_keys=True, separators=(',', ':'))

    def dispatch(self, method: str, uid: int, step: str, expected: str | None, *,
                 deadline: float, before_commit: Callable[[], None]) -> dict:
        if method not in ('CancelInvite', 'RecoverInvite') or type(uid) is not int or uid < 1000:
            raise ValueError('refused')
        if step == 'capture' and expected is None:
            return {'snapshot': self.capture(uid)}
        self.dispatched = False
        try:
            return self._recover(method, uid, step, expected, deadline, before_commit)
        except Exception:
            # Preserve uncertainty if even lock release/result delivery failed
            # after B11 ran. Normal backend result tuples pass through verbatim.
            return (dict(outcome='uncertain', invite='uncertain', cleanup='uncertain') if self.dispatched
                    else dict(outcome='refused', invite='unknown', cleanup='unknown'))

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
