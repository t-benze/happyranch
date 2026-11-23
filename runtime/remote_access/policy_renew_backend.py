"""Retained fixed-helper policy renewal, independent of home activation.

The helper owns fresh authorization and the live ready/commit exchange. This
private child binds its capture to protected non-home configuration and checks
it again under the installer's original directory flock before publication.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Callable

from runtime.remote_access.installer import ManagedInstaller


class PolicyRenewalRefused(ValueError):
    def __init__(self) -> None:
        super().__init__('unavailable')


class PolicyRenewalBackend:
    def __init__(self, io: ManagedInstaller) -> None:
        self.io = io

    def capture(self, uid: int) -> str:
        """No activation parser, directory creation or home-authority adoption."""
        if type(uid) is not int or not 1000 <= uid < 2**32 - 1:
            raise PolicyRenewalRefused()
        relative = 'var/lib/happyranch-remote-access'
        with self.io._publication_parent(relative) as (parent, check):
            self.io._check_directory(parent, relative, (0o700, self.io.root_owner))
            for name in ('home-activation.json', 'home-activation-txn.json'):
                self.io._refuse_provision_entry(parent, name)
            check()
        owner = self.io._account(create=False)
        if owner is None:
            raise PolicyRenewalRefused()
        self.io._service_owner = owner
        with self.io._publication_parent() as (parent, check):
            self.io._refuse_provision_entry(parent, 'invite-issuer.json')
            files = {}
            for name in ('release-daemon.json', 'connector.json', 'sidecar.json'):
                self.io._headscale_no_pending(parent, name)
                files[name] = self.io._read_file(parent, name,
                    owner=self.io.root_owner if name == 'release-daemon.json' else (self.io.root_owner[0], owner[1]),
                    mode=0o644 if name == 'release-daemon.json' else 0o640)
            if 'home_binding' in self.io._json(files['sidecar.json']):
                raise PolicyRenewalRefused()
            for name, data in files.items():
                self.io._headscale_no_pending(parent, name)
                if self.io._read_file(parent, name,
                        owner=self.io.root_owner if name == 'release-daemon.json' else (self.io.root_owner[0], owner[1]),
                        mode=0o644 if name == 'release-daemon.json' else 0o640) != data:
                    raise PolicyRenewalRefused()
            self.io._refuse_provision_entry(parent, 'invite-issuer.json')
            check()
        return json.dumps(dict(uid=uid, files={name: hashlib.sha256(data).hexdigest()
            for name, data in files.items()}), sort_keys=True, separators=(',', ':'))

    def policy_status(self, uid: int) -> dict:
        """The existing read-only policy dimension; never extends expiry."""
        from datetime import datetime, timezone
        from runtime.remote_access import installer
        from runtime.remote_access.policy import PolicyEnvelope, PolicyError, RoutePolicyConsumer
        from runtime.remote_access.policy_renew_units import SERVICE_NAME, TIMER_NAME, fresh_auth_renewal_candidate
        self.capture(uid)
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
                raise PolicyRenewalRefused()
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

    def dispatch(self, method: str, uid: int, step: str, expected: str | None, *,
                 deadline: float | None = None,
                 before_commit: Callable[[], None] | None = None) -> dict:
        if method != 'RetryPolicyRenewal':
            raise PolicyRenewalRefused()
        if step == 'capture' and expected is None:
            return {'snapshot': self.capture(uid)}
        if step == 'status' and expected is None:
            return self.policy_status(uid)
        if (step != 'renew' or type(expected) is not str or self.capture(uid) != expected
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or time.monotonic() >= deadline or before_commit is None):
            raise PolicyRenewalRefused()
        before_commit()

        def before_renew() -> None:
            if time.monotonic() >= deadline or self.capture(uid) != expected:
                raise PolicyRenewalRefused()
            before_commit()

        self.io.renew_policy(fresh_auth=True, _before_renew=before_renew)
        if time.monotonic() >= deadline or self.capture(uid) != expected:
            raise PolicyRenewalRefused()
        before_commit()
        return {'outcome': 'complete'}


def helper_main() -> int:
    """Fixed root child; closed bounded request and the original live exchange."""
    try:
        if len(sys.argv) != 1 or os.geteuid() != 0:
            raise PolicyRenewalRefused()
        raw = sys.stdin.buffer.readline(4097)
        if len(raw) > 4096:
            raise PolicyRenewalRefused()
        from runtime.remote_access.helper_invite import _pairs, confirm
        request = json.loads(raw, object_pairs_hook=_pairs)
        if type(request) is not dict or set(request) != {'method', 'uid', 'step', 'expected', 'deadline'}:
            raise PolicyRenewalRefused()
        deadline = request.pop('deadline')
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or deadline <= time.monotonic()):
            raise PolicyRenewalRefused()
        from runtime.remote_access.service_manager import SystemdServiceManager, _default_run

        def run(argv: list[str], timeout: float = 10.0, **kwargs: object) -> tuple[int, str]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PolicyRenewalRefused()
            return _default_run(argv, timeout=min(timeout, remaining), **kwargs)

        manager = SystemdServiceManager(system=True, run=run)
        io = ManagedInstaller(layout='deb', manager=manager, run=run)
        result = PolicyRenewalBackend(io).dispatch(**request, deadline=deadline,
            before_commit=lambda: confirm(deadline))
        if time.monotonic() >= deadline:
            raise PolicyRenewalRefused()
        print(json.dumps(result, sort_keys=True, separators=(',', ':')))
        return 0
    except Exception:
        return 1


if __name__ == '__main__':
    raise SystemExit(helper_main())
