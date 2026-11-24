"""Guarded activation composition, mounted by the fixed root helper.

Public lifecycle calls use the fixed helper admission gate. Recovery executes only the
existing Ruling 12/12a verify/publication path, never activation effects.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import pwd
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

from runtime.remote_access.home_activation import (
    ACTIVATION_PATH, IDENTITY_KEYS, INVITE_CONFIG_PATH, HomeActivationReceipt, HomeActivationStore, _generation,
)
from runtime.remote_access.home_activation_coordinator import ActivationRefused, HomeActivationCoordinator
from runtime.remote_access.home_activation_owner import HomeActivationOwner
from runtime.remote_access.home_activation_prepare import HomeActivationPrepare
from runtime.remote_access.home_activation_recovery import HomeActivationRecovery
from runtime.remote_access.home_activation_txn import HomeActivationTransactionStore
from runtime.remote_access.home_live_config import HomeLiveConfigOwner
from runtime.remote_access.home_observer import HomeObserver
from runtime.remote_access.installer import ManagedInstaller, UNITS
from runtime.remote_access.helper_owner import bootstrap_owner
from runtime.remote_access.helper_lifecycle import log_enable_refusal


class _OperationTransactions(HomeActivationTransactionStore):
    """Borrow the SAME held flock for nested calls by this operation's thread.

    Other threads refuse nonblocking; independent objects/processes still hit the
    original directory flock. The unchanged coordinator keeps its lock contexts.
    """

    def __init__(self, *, io: ManagedInstaller) -> None:
        super().__init__(root=io.root, root_owner=io.root_owner)
        self._mutex = threading.RLock()
        self._depth = 0
        self._directory: int | None = None

    @contextmanager
    def locked(self) -> Iterator[int]:
        if not self._mutex.acquire(blocking=False):
            raise ActivationRefused()
        try:
            if self._depth:
                if self._directory is None:
                    raise ActivationRefused()
                yield self._directory
            else:
                with super().locked() as parent:
                    self._depth = 1
                    self._directory = parent
                    try:
                        yield parent
                    finally:
                        self._depth = 0
                        self._directory = None
        finally:
            self._mutex.release()


class HomeActivationBackend:
    def __init__(self, *, io: ManagedInstaller | None = None) -> None:
        self._io = io or ManagedInstaller()
        self._transactions = _OperationTransactions(io=self._io)
        self._prepare = HomeActivationPrepare(io=self._io)
        self._prepare._transactions = self._transactions

    def _compose(self, receipt: HomeActivationReceipt, *, defer_policy_expiry: bool = False) -> tuple[
            HomeActivationCoordinator, HomeActivationRecovery, HomeObserver]:
        binding = dict(expected_generation=receipt.activation_generation,
                       expected_operation_id=receipt.operation_id)
        config = HomeLiveConfigOwner(io=self._io, **binding)
        owner = HomeActivationOwner(io=self._io, config_owner=config,
                                    defer_policy_expiry=defer_policy_expiry, **binding)
        coordinator = HomeActivationCoordinator(owner=owner, root=self._io.root,
                                                root_owner=self._io.root_owner)
        recovery = HomeActivationRecovery(owner=owner, admin=owner._admin,
            root=self._io.root, root_owner=self._io.root_owner)
        coordinator._transactions = recovery._transactions = self._transactions
        observer = HomeObserver(owner=owner, admin=owner._admin, root=self._io.root)
        return coordinator, recovery, observer

    def enable(self, *, expected_generation: int, authenticated: bool,
               _defer_policy_expiry: bool = False) -> HomeActivationReceipt:
        try:
            _generation(expected_generation)
            if authenticated is not True or os.geteuid() != 0:
                raise ActivationRefused()
            with self._transactions.locked():
                receipts = self._prepare._receipts
                with receipts._parent(ACTIVATION_PATH) as (parent, check):
                    self._io._headscale_no_pending(parent, "home-activation.json")
                    try:
                        os.stat("home-activation.json", dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        exists = False
                    else:
                        exists = True
                    check()
                transaction = self._transactions.read_optional()
                if not exists:
                    if transaction is not None:
                        raise ActivationRefused()
                    self._prepare.prepare(expected_generation=expected_generation, authenticated=authenticated)
                receipt = receipts.read(expected_generation=expected_generation)
                if (receipt.phase not in ("prepared", "activating", "joined")
                        or receipt.local_node_stable_id is not None or receipt.home_endpoint is not None):
                    raise ActivationRefused()
                if transaction is not None:
                    if transaction.matches(receipt):
                        coordinator, _, _ = self._compose(receipt)
                        # Ruling 17: retain the actual protected binding.
                        coordinator._pending()
                        coordinator._fail(receipt)
                        raise ActivationRefused("failed_uncertain")
                    # B7's fresh prepared generation reaches the coordinator's
                    # existing first-intent replacement; all other pairs refuse.
                    if not (receipt.phase == "prepared"
                            and transaction.phase == "failed_uncertain"
                            and transaction.activation_generation < receipt.activation_generation
                            and all(getattr(transaction, key) == getattr(receipt, key) for key in IDENTITY_KEYS)
                            and transaction.operation_id != receipt.operation_id):
                        raise ActivationRefused()
                if receipt.phase != "prepared":
                    raise ActivationRefused()
                coordinator, _, observer = self._compose(receipt, defer_policy_expiry=_defer_policy_expiry)
                coordinator.run(receipt)
                return coordinator.publish_ready(expected_generation=expected_generation, observer=observer)
        except Exception:
            raise ActivationRefused() from None

    def recover(self, *, expected_generation: int, expected_operation_id: str,
                fresh_authenticated: bool) -> HomeActivationReceipt:
        try:
            if fresh_authenticated is not True or os.geteuid() != 0:
                raise ActivationRefused()
            with self._transactions.locked():
                receipt = self._prepare._receipts.read(expected_generation=expected_generation)
                if receipt.operation_id != expected_operation_id:
                    raise ActivationRefused()
                _, recovery, observer = self._compose(receipt)
                return recovery.recover(expected_generation=expected_generation,
                    expected_operation_id=expected_operation_id, fresh_authenticated=fresh_authenticated,
                    observation_owner=observer._owner)
        except Exception:
            raise ActivationRefused() from None


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
        """Read-only aggregate for the existing B15 policy dimension."""
        from datetime import datetime, timezone
        from runtime.remote_access import installer
        from runtime.remote_access.policy import PolicyEnvelope, PolicyError, RoutePolicyConsumer
        from runtime.remote_access.policy_renew_units import SERVICE_NAME, TIMER_NAME, fresh_auth_renewal_candidate
        self.capture('RetryPolicyRenewal', uid)
        self.io._service_owner = self.io._account(create=False)
        if self.io._service_owner is None:
            raise ActivationRefused()
        with self.io._publication_parent() as (parent, check):
            self.io._headscale_no_pending(parent, 'install-transaction.json')
            try:
                os.stat('install-transaction.json', dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                check()
                return {'policy': 'uncertain'}
            self.io._policy_renewal_pending(parent)
            raw = self.io._read_file(parent, 'policy.json', owner=(self.io.root_owner[0], self.io._service_owner[1]), mode=0o640)
            provision = self.io._read_file(parent, 'provision.json', owner=self.io.root_owner, mode=0o600)
            self.io._validate_install_record('provision.json', provision)
            if self.io._json(provision).get('digests', {}).get('policy.json') != hashlib.sha256(raw).hexdigest():
                raise ActivationRefused()
            check()
        envelope = PolicyEnvelope.model_validate(self.io._json(raw))
        consumer = RoutePolicyConsumer.from_envelope(envelope)
        source = Path(installer.__file__).with_name('route_policy.json')
        with self.io._input_parent(source) as parent:
            shipped = self.io._json(self.io._read_file(parent, source.name, owner=self.io.root_owner))
        now = datetime.now(timezone.utc)
        if fresh_auth_renewal_candidate(envelope, shipped, now=now) is None:
            return {'policy': 'renewal_failed'}
        try:
            consumer.require_current(now)
        except PolicyError:
            return {'policy': 'expired'}
        service = self.io.manager.show_properties(SERVICE_NAME, ('ActiveState', 'SubState', 'Result'))
        timer = self.io.manager.show_properties(TIMER_NAME, ('ActiveState', 'SubState', 'UnitFileState'))
        state = ('renewing' if service.get('ActiveState') in ('active', 'activating') else
                 'current' if service.get('Result') == 'success' and timer == dict(
                     ActiveState='active', SubState='waiting', UnitFileState='enabled') else 'renewal_failed')
        return {'policy': state}

    def capture(self, method: str, uid: int) -> str:
        if method in ('IssueInvite', 'CancelInvite', 'RecoverInvite'):
            from runtime.remote_access.helper_invite_backend import HelperInviteBackend
            return HelperInviteBackend(self.io).capture(uid)
        receipt = self.store.current()
        activation = None
        generation = None
        replacement = None
        configuration = None
        prestate = None
        inventory = None
        if receipt is not None:
            if receipt.release_uid != uid:
                raise ActivationRefused()
            if method == 'Enable' and receipt.phase == 'ready':
                # B6 can retain this receipt after partial cleanup. Reuse the
                # existing strict publication verifier before prompting again.
                preparing = self.io._headscale_preparing
                try:
                    self.io._service_owner = self.io._account(create=False)
                    self.io._headscale_owner = self.io._headscale_shape()
                    self.io._headscale_preparing = True
                    if self.io._headscale_publication() is None:
                        raise ActivationRefused()
                finally:
                    self.io._headscale_preparing = preparing
            activation = dict(generation=receipt.activation_generation, operation=receipt.operation_id,
                              home_id=receipt.home_id, control_plane_id=receipt.control_plane_id,
                              connector_id=receipt.connector_id)
            generation = receipt.activation_generation
        elif method == 'Enable':
            from runtime.remote_access.invite_issuer import HomeInviteConfigRecord
            try:
                with self.io._publication_parent() as (parent, check):
                    with self.store._record(INVITE_CONFIG_PATH, allow_absent=True) as data:
                        config = None if data is None else HomeInviteConfigRecord.parse(data)
                        configuration = None if data is None else hashlib.sha256(data).hexdigest()
                    check()
            except FileNotFoundError:
                # Prove the entire config directory is safely absent. Missing
                # parents inside a present generation never mean clean.
                with self.io._publication_parent('etc') as (parent, check):
                    self.io._refuse_provision_entry(parent, 'happyranch')
                    check()
                config = None
            if config is not None:
                if config.release_uid != uid:
                    raise ActivationRefused()
                prestate = 'prepared'
            else:
                candidate = self.io.inventory()
                if candidate.state == 'clean':
                    self.io._home_staging_residue(clean=True)
                    prestate = 'clean'
                else:
                    if candidate.state == 'known-failed-or-partial':
                        raise ActivationRefused()
                    with self.io._publication_parent() as (parent, check):
                        raw = self.io._json(self.io._read_file(parent, 'sidecar.json',
                            owner=(self.io.root_owner[0], self.io._account(create=False)[1]), mode=0o640))
                        check()
                    candidate = self.io._home_staged_inventory(post_disable='home_binding' in raw)
                    if self.io._check_release_record()['uid'] != uid:
                        raise ActivationRefused()
                    prestate = candidate.detail
                with self.io._home_preparation_custody():
                    inventory = hashlib.sha256(json.dumps(dict(
                        inventory=self.io._replacement_snapshot(),
                        headscale=self.io._headscale_publication(), issuer=self.io._issuer_receipt()),
                        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
            generation = 0
        if method == 'ReplaceFailed':
            candidate = self.io.replacement_inventory()
            replacement = candidate.journal_generation or candidate.inventory_generation
            if candidate.installation.state != 'known-failed-or-partial' or replacement is None:
                raise ActivationRefused()
        if method == 'Disable' and receipt is None:
            raise ActivationRefused()
        result = dict(uid=uid, activation=activation, generation=generation,
                      replacement=replacement, configuration=configuration)
        if method == 'Enable' and receipt is None:
            result.update(prestate=prestate, inventory=inventory)
        return json.dumps(result, sort_keys=True, separators=(',', ':'))

    def _enable_staging(self, uid: int, prestate: str, directory: int, deadline: float | None) -> None:
        def check() -> None:
            if deadline is None or not time.monotonic() < deadline:
                raise ActivationRefused()

        check()
        owner = bootstrap_owner(uid)
        rows = [row for row in pwd.getpwall() if row.pw_uid == owner.uid]
        if owner.uid != uid or len(rows) != 1:
            raise ActivationRefused()
        check()
        if prestate == 'clean':
            self.io.provision(release_user=rows[0].pw_name, data_home=owner.data_home)
        else:
            release = self.io._check_release_record()
            if (release['uid'], release['user'], Path(release['data_home'])) != (
                    uid, rows[0].pw_name, owner.data_home):
                raise ActivationRefused()
        check()
        self.io.prepare_headscale()
        check()
        self.io.publish_home_units(_activation_directory=directory)
        check()
        self.io.publish_invite_config(_activation_directory=directory)
        check()

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
        if method == 'RetryPolicyRenewal' and step == 'status' and expected is None:
            return self.policy_status(uid)
        if type(expected) is not str or self.capture(method, uid) != expected:
            raise ActivationRefused()
        if method == 'RetryPolicyRenewal' and step == 'renew':
            if deadline is None or before_commit is None or time.monotonic() >= deadline:
                raise ActivationRefused()
            before_commit()
            # Public entry holds the existing flock across P1 recovery/publication.
            def before_renew() -> None:
                if self.capture(method, uid) != expected:
                    raise ActivationRefused()
                before_commit()
            self.io.renew_policy(fresh_auth=True, _before_renew=before_renew)
            if self.capture(method, uid) != expected:
                raise ActivationRefused()
            before_commit()
            return {'outcome': 'complete'}
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
        backend = HomeActivationBackend(io=self.io)
        with backend._transactions.locked() as directory:
            if self.capture(method, uid) != expected:
                raise ActivationRefused()
            if method == 'Enable' and step == 'enable':
                current = self.store.current()
                if current is not None and current.phase == 'ready':
                    # Already-ready is inspect-only, never replays activation.
                    coordinator, _, _ = backend._compose(current)
                    if coordinator._owner.verify('joined', current) is not True:
                        raise ActivationRefused()
                elif current is not None and current.phase == 'failed_uncertain':
                    transaction = backend._transactions.read_optional()
                    if (transaction is None or not transaction.matches(current)
                            or not (transaction.phase == 'failed_uncertain'
                                    or (transaction.phase == 'joined'
                                        and transaction.recovery_observation is not None))):
                        raise ActivationRefused()
                    current = backend.recover(expected_generation=snapshot['generation'],
                        expected_operation_id=snapshot['activation']['operation'], fresh_authenticated=True)
                else:
                    if current is None and snapshot.get('prestate') != 'prepared':
                        self._enable_staging(uid, snapshot['prestate'], directory, deadline)
                    if deadline is not None and time.monotonic() >= deadline:
                        raise ActivationRefused()
                    current = backend.enable(expected_generation=snapshot['generation'], authenticated=True,
                        _defer_policy_expiry=current is None and snapshot.get('prestate') in ('staged-inert', 'post-disable'))
                if deadline is not None and time.monotonic() >= deadline:
                    raise ActivationRefused()
                return {'outcome': current.phase, 'residue': []}
            if method == 'ReplaceFailed' and step == 'replace':
                self.io.begin_replacement(expected_release_uid=uid,
                                          expected_generation=snapshot['replacement'])
                return {'outcome': 'off', 'residue': []}
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


def _log_activation_failure(error: Exception) -> None:
    """Keep only the class and innermost shipped source location in local syslog."""
    try:
        import re
        import syslog

        # Closed product wrappers retain the original failure as context even
        # when suppressing its text. Project one cause, without formatting it.
        seen = {id(error)}
        for _ in range(8):
            cause = error.__cause__
            if cause is None and error.__suppress_context__:
                cause = error.__context__
            if cause is None or id(cause) in seen:
                break
            seen.add(id(cause))
            error = cause
        root = Path(__file__).parent.parent
        source = None
        trace = error.__traceback__
        while trace is not None:
            try:
                relative = Path(trace.tb_frame.f_code.co_filename).relative_to(root)
            except ValueError:
                pass
            else:
                # Only source-relative identifiers; never an absolute/user path.
                path = f'runtime/{relative.as_posix()}'
                if (len(path) <= 160 and
                        re.fullmatch(r'(?:[a-z_][a-z0-9_]*/)*[a-z_][a-z0-9_]*\.py', relative.as_posix())):
                    source = f'{path}:{trace.tb_lineno}'
            trace = trace.tb_next
        name = type(error).__name__
        name = name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}', name) else 'Exception'
        if source is not None:
            syslog.syslog(syslog.LOG_WARNING,
                f'remote-access-activation-failure exception={name} source={source} step=enable')
    except Exception:
        pass  # Diagnostic loss preserves the original closed failure.


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
            from runtime.remote_access.helper_generation import host_state_category
            print(json.dumps({'refusal': host_state_category(error)}))
        elif (type(request) is dict and request.get('method') == 'Enable'
                and request.get('step') == 'enable'):
            _log_activation_failure(error)
        return 1  # No raw paths, keys, credentials or exception text leave this child.


if __name__ == '__main__':
    raise SystemExit(helper_main())
