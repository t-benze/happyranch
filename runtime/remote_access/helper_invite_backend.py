"""Minimal fail-closed compatibility for retired home invite helper calls."""
from __future__ import annotations

import json
import math
import time
from typing import Callable

from runtime.remote_access.home_retirement import MESSAGE
from runtime.remote_access.installer import ManagedInstaller


class HelperInviteBackend:
    def __init__(self, io: ManagedInstaller) -> None:
        self.io = io

    def capture(self, uid: int) -> str:
        if type(uid) is not int or uid < 1000:
            raise ValueError('refused')
        return json.dumps(dict(uid=uid, retirement='home-mode-retired'),
                          sort_keys=True, separators=(',', ':'))

    def dispatch(self, method: str, uid: int, step: str, expected: str | None, *,
                 deadline: float, before_commit: Callable[[], None]) -> dict:
        if (method not in ('IssueInvite', 'CancelInvite', 'RecoverInvite')
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not time.monotonic() < deadline <= time.monotonic() + 120):
            raise ValueError('refused')
        snapshot = self.capture(uid)
        if step == 'capture' and expected is None:
            return {'snapshot': snapshot}
        if step != ('issue' if method == 'IssueInvite' else 'recover') or expected != snapshot:
            raise ValueError('refused')
        if method == 'IssueInvite':
            return {'result': 'refused', 'message': MESSAGE}
        return dict(outcome='refused', invite='unknown', cleanup='unknown', message=MESSAGE)
