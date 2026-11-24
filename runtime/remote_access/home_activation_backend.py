"""Minimal helper adapters for retired activation and retained owned Disable.

Enable and ReplaceFailed cannot provision, activate or resume a home. Native
sender authorization stays in the public helper; Disable keeps its original
protected readback, directory lock, revoke binding and selective cleanup.
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from typing import Callable

from runtime.remote_access.legacy_home_readback import (
    LegacyHomeReadback as HomeActivationStore, LegacyHomeTransactions,
)
from runtime.remote_access.legacy_home_receipt import ActivationRefused
from runtime.remote_access.home_retirement import MESSAGE
from runtime.remote_access.installer import ManagedInstaller, UNITS
from runtime.remote_access.helper_lifecycle import log_enable_refusal


class HelperLifecycleBackend:
    """Existing bundled root child, selected only by the fixed helper.

    No paths, identities or effect flags come from a D-Bus caller. The helper
    sends its own authenticated UID and previously captured protected snapshot.
    Each child revalidates the snapshot; B6 also checks operation under its flock.
    """

    def __init__(self, io: ManagedInstaller) -> None:
        self.io = io
        self.store = HomeActivationStore(root=io.root, root_owner=io.root_owner)

    def _policy_quiescence(self) -> list[str]:
        from runtime.remote_access.policy_renew_units import SERVICE_NAME, TIMER_NAME
        residue = []
        for name in (TIMER_NAME, SERVICE_NAME):
            keys = ('ActiveState', 'SubState', 'Job')
            if name == SERVICE_NAME:
                keys += ('MainPID', 'ControlPID', 'ControlGroup')
            try:
                props = self.io.manager.show_properties(name, keys)
                if (props.get('ActiveState') not in ('inactive', 'failed')
                        or props.get('SubState') not in ('dead', 'failed') or props.get('Job') != ''
                        or name == SERVICE_NAME and any(props.get(k) != v for k, v in
                            (('MainPID', '0'), ('ControlPID', '0'), ('ControlGroup', '')))):
                    raise ActivationRefused()
            except Exception:
                residue.append(name + ': quiescence unconfirmed')
        relative = 'etc/systemd/system/happyranch-managed.target.wants'
        try:
            with self.io._publication_parent(relative) as (parent, check):
                try:
                    os.stat(TIMER_NAME, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    residue.append('/' + relative + '/' + TIMER_NAME)
                check()
        except FileNotFoundError:
            pass
        except Exception:
            residue.append('/' + relative + '/' + TIMER_NAME + ': absence unconfirmed')
        return residue

    def _stop_policy(self) -> list[str]:
        from runtime.remote_access.policy_renew_units import SERVICE_NAME, TIMER_NAME
        residue = []
        timer_owned = False
        for name in (TIMER_NAME, SERVICE_NAME):
            try:
                props = self.io.manager.show_properties(name, ('LoadState', 'FragmentPath', 'DropInPaths'))
                loaded = props.get('LoadState') == 'loaded'
                if (props.get('LoadState') not in ('loaded', 'not-found') or props.get('DropInPaths') != ''
                        or props.get('FragmentPath') != (str(self.io.root / 'usr/lib/systemd/system' / name) if loaded else '')):
                    raise ActivationRefused()
                if name == TIMER_NAME:
                    timer_owned = True
                if loaded:
                    self.io.manager.stop(name, timeout=50.0)
            except Exception:
                residue.append(name + ': stop unconfirmed')
        if timer_owned:
            try:
                self.io.manager.disable(TIMER_NAME)
            except Exception:
                residue.append(TIMER_NAME + ': disable unconfirmed')
        return residue + self._policy_quiescence()

    def policy_status(self, uid: int) -> dict:
        from runtime.remote_access.policy_renew_backend import PolicyRenewalBackend
        return PolicyRenewalBackend(self.io).policy_status(uid)

    def capture(self, method: str, uid: int) -> str:
        if type(uid) is not int or uid < 1000:
            raise ActivationRefused()
        if method in ('Enable', 'ReplaceFailed'):
            return json.dumps(dict(uid=uid, retirement='home-mode-retired'),
                              sort_keys=True, separators=(',', ':'))
        if method == 'RetryPolicyRenewal':
            from runtime.remote_access.policy_renew_backend import PolicyRenewalBackend
            return PolicyRenewalBackend(self.io).capture(uid)
        if method in ('IssueInvite', 'CancelInvite', 'RecoverInvite'):
            from runtime.remote_access.helper_invite_backend import HelperInviteBackend
            return HelperInviteBackend(self.io).capture(uid)
        if method != 'Disable':
            raise ActivationRefused()
        receipt = self.store.current()
        if receipt is None or receipt.release_uid != uid:
            raise ActivationRefused()
        activation = dict(generation=receipt.activation_generation, operation=receipt.operation_id,
                          home_id=receipt.home_id, control_plane_id=receipt.control_plane_id,
                          connector_id=receipt.connector_id)
        return json.dumps(dict(uid=uid, activation=activation, generation=receipt.activation_generation,
                               replacement=None, configuration=None), sort_keys=True, separators=(',', ':'))

    def dispatch(self, method: str, uid: int, step: str, expected: str | None, *,
                 revoke_succeeded: bool = False, deadline: float | None = None,
                 before_commit: Callable[[], None] | None = None) -> dict:
        if method not in ('Enable', 'ReplaceFailed', 'Disable', 'RetryPolicyRenewal') or type(uid) is not int or uid < 1000:
            raise ActivationRefused()
        if type(revoke_succeeded) is not bool or (revoke_succeeded and (method, step) != ('Disable', 'cleanup')):
            raise ActivationRefused()
        if step == 'capture':
            if expected is not None:
                raise ActivationRefused()
            return {'snapshot': self.capture(method, uid)}
        if method == 'RetryPolicyRenewal':
            from runtime.remote_access.policy_renew_backend import PolicyRenewalBackend
            return PolicyRenewalBackend(self.io).dispatch(method, uid, step, expected,
                deadline=deadline, before_commit=before_commit)
        if method in ('Enable', 'ReplaceFailed'):
            if step != ('enable' if method == 'Enable' else 'replace') or expected != self.capture(method, uid):
                raise ActivationRefused()
            return {'outcome': 'refused', 'message': MESSAGE}
        if type(expected) is not str or self.capture(method, uid) != expected:
            raise ActivationRefused()
        snapshot = json.loads(expected)
        if method == 'Disable' and step == 'cleanup':
            residue = self._policy_quiescence()
            if residue:
                return {'outcome': 'partial_cleanup', 'residue': residue}
            receipt = self.store.current()
            if receipt is None:
                raise ActivationRefused()
            result = self.io.cleanup_headscale(expected_release_uid=uid,
                expected_home_id=snapshot['activation']['home_id'],
                expected_control_plane_id=snapshot['activation']['control_plane_id'],
                expected_connector_id=snapshot['activation']['connector_id'], expected_generation=snapshot['generation'],
                expected_operation_id=snapshot['activation']['operation'], revoke_succeeded=revoke_succeeded)
            if result.get('state') != 'removed':
                raise ActivationRefused()
            return {'outcome': 'off', 'residue': []}
        with LegacyHomeTransactions(root=self.io.root, root_owner=self.io.root_owner).locked():
            if self.capture(method, uid) != expected:
                raise ActivationRefused()
            if method == 'Disable' and step == 'stop':
                residue = self._stop_policy()
                try:
                    self.io._service_owner = self.io._account(create=False)
                    with self.io._publication_parent() as (parent, check):
                        self.io._headscale_no_pending(parent, 'install-transaction.json')
                        try:
                            transaction = self.io._read_file(parent, 'install-transaction.json', owner=self.io.root_owner, mode=0o600)
                        except FileNotFoundError:
                            transaction = None
                        check()
                    if transaction is not None:
                        self.io._recover_policy_renewal(transaction)
                except Exception:
                    residue.append('/etc/happyranch/install-transaction.json')
                # Renewal residue vetoes B6, never the independently owned
                # remote-unit safety stop (including after failed revocation).
                try:
                    self.io.stop_and_confirm()
                except Exception:
                    residue.append('owned remote units: stop unconfirmed')
                try:
                    from runtime.remote_access import headscale_service
                    from runtime.remote_access.home_unit_gate import HomeUnitGate
                    receipt = self.store.current()
                    gate = HomeUnitGate(expected_release_uid=uid, expected_home_id=receipt.home_id,
                        expected_control_plane_id=receipt.control_plane_id, expected_connector_id=receipt.connector_id,
                        expected_generation=receipt.activation_generation, root=self.io.root, root_owner=self.io.root_owner)
                    gate.inspect()
                    account = self.io._headscale_receipt()
                    self.io._headscale_cleanup_identity(account)
                    if self.io._headscale_owner is None:
                        raise ActivationRefused()
                    name = headscale_service.UNIT
                    props = self.io.manager.show_properties(name, ('LoadState', 'FragmentPath', 'DropInPaths'))
                    loaded = props.get('LoadState') == 'loaded'
                    if (props.get('LoadState') not in ('loaded', 'not-found') or props.get('DropInPaths') != ''
                            or props.get('FragmentPath') != (str(self.io.root / 'etc/systemd/system' / name) if loaded else '')):
                        raise ActivationRefused()
                    if loaded:
                        self.io.manager.stop(name, timeout=50.0)
                    self.io._headscale_stopped()
                except Exception:
                    residue.append('happyranch-headscale.service: stop unconfirmed')
                try:
                    # B6 owns its receipted Headscale link. Disable the other exact
                    # recognized remote units here, only after native stop proof.
                    for name in (UNITS[2], UNITS[0], UNITS[1], UNITS[3]):
                        if self.io.inventory().state not in ('owned', 'owned-activated'):
                            raise ActivationRefused()
                        self.io._confirm_quiescent()
                        if self.io.manager.show_properties(name, ('LoadState',)).get('LoadState') != 'not-found':
                            self.io.manager.disable(name)
                    self.io._confirm_quiescent()
                    self.io._headscale_stopped()
                except Exception:
                    residue.append('owned remote units: quiescence unconfirmed')
                residue.extend(self._policy_quiescence())
                if residue:
                    return {'outcome': 'partial_cleanup', 'residue': residue}
                return {'outcome': 'in_progress', 'residue': []}
        raise ActivationRefused()


def helper_main() -> int:
    """Closed private input from the fixed helper; never an operator CLI."""
    request = None
    try:
        if len(sys.argv) != 1 or os.geteuid() != 0:
            raise ActivationRefused()
        raw = sys.stdin.buffer.readline(4097)
        if len(raw) > 4096:
            raise ActivationRefused()
        request = json.loads(raw)
        if type(request) is not dict:
            raise ActivationRefused()
        fields = {'method', 'uid', 'step', 'expected', 'deadline'}
        if (request.get('method'), request.get('step')) == ('Disable', 'cleanup'):
            fields.add('revoke_succeeded')
        if set(request) != fields:
            raise ActivationRefused()
        deadline = request.pop('deadline')
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or deadline <= time.monotonic():
            raise ActivationRefused()
        # Existing command runner; every root/systemd step gets the ORIGINAL remainder.
        from runtime.remote_access.service_manager import SystemdServiceManager, _default_run
        def run(argv: list[str], timeout: float = 10.0, **kwargs: object) -> tuple[int, str]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ActivationRefused()
            # The fixed child has an empty environment: Python's default
            # executable search omits /usr/sbin on Debian/Ubuntu.
            if argv[0] in ('useradd', 'userdel', 'groupdel'):
                argv = ['/usr/sbin/' + argv[0], *argv[1:]]
            return _default_run(argv, timeout=min(timeout, remaining), **kwargs)
        manager = SystemdServiceManager(system=True, run=run)
        io = ManagedInstaller(layout='deb', manager=manager, run=run)
        if request['method'] in ('IssueInvite', 'CancelInvite', 'RecoverInvite'):
            from runtime.remote_access.helper_invite import confirm
            from runtime.remote_access.helper_invite_backend import HelperInviteBackend
            result = HelperInviteBackend(io).dispatch(**request, deadline=deadline,
                                                      before_commit=lambda: confirm(deadline))
        elif request['method'] == 'RetryPolicyRenewal':
            from runtime.remote_access.helper_invite import confirm
            result = HelperLifecycleBackend(io).dispatch(**request, deadline=deadline,
                before_commit=lambda: confirm(deadline))
        else:
            result = HelperLifecycleBackend(io).dispatch(**request, deadline=deadline)
        if time.monotonic() >= deadline:
            raise ActivationRefused()
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0
    except Exception as error:
        if (type(request) is dict and request.get('method') == 'Enable'
                and request.get('step') == 'capture'):
            log_enable_refusal(error, child=True)
        return 1  # No raw paths, keys, credentials or exception text leave this child.


if __name__ == '__main__':
    raise SystemExit(helper_main())
