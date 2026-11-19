"""Read-only adapter to Track A through the existing fixed bundled Python."""
from __future__ import annotations

import json
import math
import re
import subprocess
import time
from dataclasses import dataclass, field

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


class HomeLifecycleClient:
    """Fixed child transport; only protected snapshots cross the system-Python seam."""

    def __init__(self, method: str, uid: int) -> None:
        if method not in ('Enable', 'ReplaceFailed', 'Disable') or type(uid) is not int or uid < 1000:
            raise GenerationUnavailable()
        self.method, self.uid = method, uid

    def _call(self, step: str, expected: str | None, remaining: float) -> dict:
        if type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining <= 0:
            raise GenerationUnavailable()
        deadline = time.monotonic() + remaining
        request = dict(method=self.method, uid=self.uid, step=step, expected=expected,
                       deadline=deadline)
        try:
            result = subprocess.run(LIFECYCLE_COMMAND, input=json.dumps(request).encode(),
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

    def effect(self, step: str, expected: str, remaining: float) -> dict:
        return self._call(step, expected, remaining)
