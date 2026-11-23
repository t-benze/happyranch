"""Fixed bundled helper children for lifecycle and retained policy renewal."""
from __future__ import annotations

import json
import math
import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Callable

COMMAND = ('/opt/happyranch-desktop/python/bin/python3', '-I', '-m',
           'runtime.remote_access.home_activation')


class GenerationUnavailable(ValueError):
    def __init__(self) -> None:
        super().__init__('unavailable')


@dataclass(frozen=True, repr=False)
class GenerationSnapshot:
    generation: int
    operation: str
    release_uid: int
    phase: str = field(compare=False)  # Display progress is not a generation/operation change.

    def __repr__(self) -> str:
        return 'GenerationSnapshot(<redacted>)'


class HomeGenerationReader:
    def current_generation(self, remaining: float) -> GenerationSnapshot | None:
        """Immutable generation/operation token for B9; no caller-selected inputs."""
        if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining <= 0:
            raise GenerationUnavailable()
        deadline = time.monotonic() + remaining
        try:
            result = subprocess.run(COMMAND, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, timeout=remaining, check=False,
                                    env={}, cwd='/')
            if result.returncode or len(result.stdout) > 512 or time.monotonic() >= deadline:
                raise GenerationUnavailable()
            value = json.loads(result.stdout)
            if value is None:
                return None
            if (type(value) is not dict or set(value) != {'generation', 'operation', 'release_uid', 'phase'}
                    or type(value['generation']) is not int or value['generation'] < 0
                    or type(value['release_uid']) is not int or not 0 < value['release_uid'] < 2**32 - 1
                    or type(value['operation']) is not str
                    or re.fullmatch('[0-9a-f]{32}', value['operation']) is None
                    or value['phase'] not in ('prepared', 'activating', 'joined', 'ready', 'failed_uncertain')):
                raise GenerationUnavailable()
            return GenerationSnapshot(**value)
        except Exception:
            raise GenerationUnavailable() from None


LIFECYCLE_COMMAND = ('/opt/happyranch-desktop/python/bin/python3', '-I', '-m',
                     'runtime.remote_access.home_activation_backend')
POLICY_COMMAND = ('/opt/happyranch-desktop/python/bin/python3', '-I', '-m',
                  'runtime.remote_access.policy_renew_backend')


class HomeLifecycleClient:
    """Fixed child transport; IssueInvite alone carries a private terminal secret."""

    def __init__(self, method: str, uid: int) -> None:
        if method not in ('Enable', 'ReplaceFailed', 'Disable', 'IssueInvite', 'CancelInvite', 'RecoverInvite', 'RetryPolicyRenewal') or type(uid) is not int or uid < 1000:
            raise GenerationUnavailable()
        self.method, self.uid = method, uid

    def _call(self, step: str, expected: str | None, remaining: float, *, revoke_succeeded: bool = False) -> dict:
        if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining <= 0:
            raise GenerationUnavailable()
        deadline = time.monotonic() + remaining
        request = dict(method=self.method, uid=self.uid, step=step, expected=expected,
                       deadline=deadline)
        if self.method == 'Disable' and step == 'cleanup':
            request['revoke_succeeded'] = revoke_succeeded
        try:
            command = POLICY_COMMAND if self.method == 'RetryPolicyRenewal' else LIFECYCLE_COMMAND
            result = subprocess.run(command, input=json.dumps(request).encode(),
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=remaining,
                check=False, env={}, cwd='/')
            if result.returncode or len(result.stdout) > 4096 or time.monotonic() >= deadline:
                raise GenerationUnavailable()
            value = json.loads(result.stdout)
            if type(value) is not dict:
                raise GenerationUnavailable()
            return value
        except Exception:
            raise GenerationUnavailable() from None

    def current_generation(self, remaining: float) -> str:
        value = self._call('capture', None, remaining)
        if set(value) != {'snapshot'} or type(value['snapshot']) is not str or len(value['snapshot']) > 2048:
            raise GenerationUnavailable()
        return value['snapshot']  # Immutable CAS token, including protected absence.

    def effect(self, step: str, expected: str, remaining: float, *, revoke_succeeded: bool = False) -> dict:
        return self._call(step, expected, remaining, revoke_succeeded=revoke_succeeded)

    def retry_policy(self, expected: str, deadline: float, before_commit: Callable[[], None]) -> dict:
        if self.method != 'RetryPolicyRenewal' or not time.monotonic() < deadline <= time.monotonic() + 120:
            raise GenerationUnavailable()
        if __package__:
            from . import helper_invite
        else:
            import happyranch_fixed_helper_invite as helper_invite
        return helper_invite.effect(POLICY_COMMAND, dict(method=self.method, uid=self.uid,
            step='renew', expected=expected, deadline=deadline), before_commit)

    def policy_status(self, remaining: float) -> dict:
        if self.method != 'RetryPolicyRenewal':
            raise GenerationUnavailable()
        value = self._call('status', None, remaining)
        if set(value) != {'policy'} or value['policy'] not in ('current', 'renewing', 'expired', 'renewal_failed', 'uncertain'):
            raise GenerationUnavailable()
        return value

    def recover_invite(self, expected: str, deadline: float, before_commit: Callable[[], None]) -> dict:
        if not time.monotonic() < deadline <= time.monotonic() + 120:
            raise GenerationUnavailable()
        if __package__:
            from . import helper_invite
        else:
            import happyranch_fixed_helper_invite as helper_invite
        return helper_invite.effect(LIFECYCLE_COMMAND, dict(method=self.method, uid=self.uid,
            step='recover', expected=expected, deadline=deadline), before_commit)

    def issue_invite(self, expected: str, deadline: float, before_commit: Callable[[], None]) -> dict:
        if self.method != 'IssueInvite' or not time.monotonic() < deadline <= time.monotonic() + 120:
            raise GenerationUnavailable()
        if __package__:
            from . import helper_invite
        else:
            import happyranch_fixed_helper_invite as helper_invite
        return helper_invite.effect(LIFECYCLE_COMMAND, dict(method=self.method, uid=self.uid,
            step='issue', expected=expected, deadline=deadline), before_commit)
