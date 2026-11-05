"""Pure, fail-closed consumers of the public startup observation.

No HTTP, authentication, admission or retry lives here. Shell script mode reads
one bounded health body and prints only the finite launcher status vocabulary.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import sys


@dataclass(frozen=True)
class StartupObservation:
    serving: str = 'unknown'
    recovery: str = 'unknown'

    @property
    def ready(self) -> bool:
        return self.serving == 'yes' and self.recovery == 'ready'

    def launcher_line(self) -> str:
        return f'startup serving={self.serving} recovery={self.recovery} launches={"admitted" if self.ready else "held"}'

    def description(self) -> str:
        if self.ready:
            return 'Serving; recovery ready. New work remains subject to normal admission controls.'
        if self.recovery == 'recovering':
            return 'Recovering; threads can be read, new work is held. Observe status; do not start another daemon.'
        if self.recovery == 'recovery_failed':
            return 'Recovery failed; new work is held. Send the startup error code to engineering_manager; preserve the original process.'
        if self.recovery == 'stopping':
            return 'Stopping; new work is held. Preserve the original shutdown owner.'
        return 'Recovery readiness unknown; new work is held. Inspect status and the original daemon log; do not restart automatically.'


def startup_observation(body: object) -> StartupObservation:
    if not isinstance(body, dict) or body.get('status') != 'ok':
        return StartupObservation()
    value = body.get('startup')
    if not isinstance(value, dict):
        return StartupObservation()
    if any(type(value.get(k)) is not bool for k in ('serving', 'recovery_ready', 'stopping')):
        return StartupObservation()
    state = value.get('state')
    if state not in ('ready', 'recovering', 'recovery_failed', 'stopping', 'stopped', 'unknown'):
        return StartupObservation()
    serving = 'yes' if value['serving'] else 'no'
    if value['stopping'] or state in ('stopping', 'stopped'):
        return StartupObservation(serving, 'stopping')
    if state == 'ready':
        return StartupObservation(serving, 'ready' if value['serving'] and value['recovery_ready'] else 'unknown')
    if value['recovery_ready']:
        return StartupObservation(serving, 'unknown')  # contradictory observation
    return StartupObservation(serving, state)


# Explicitly enumerate the complete wire vocabulary, including held unknown.
_LAUNCHER_LINES = {StartupObservation(s, r).launcher_line(): StartupObservation(s, r)
                   for s in ('yes', 'no')
                   for r in ('unknown', 'recovering', 'recovery_failed', 'stopping')}
_LAUNCHER_LINES[StartupObservation().launcher_line()] = StartupObservation()
_LAUNCHER_LINES[StartupObservation('yes', 'ready').launcher_line()] = StartupObservation('yes', 'ready')


def parse_launcher_output(text: str, *, started: bool) -> tuple[int, int, StartupObservation] | None:
    """Accept the original identity line PLUS exactly one finite observation."""
    label = 'daemon started' if started else 'running'
    match = re.fullmatch(re.escape(label) + r' \(pid ([1-9][0-9]{0,19}), port ([1-9][0-9]{0,4})\)\n([^\n]+)\n', text)
    if match is None or match[3] not in _LAUNCHER_LINES:
        return None
    pid, port = int(match[1]), int(match[2])
    if port > 65535:
        return None
    return pid, port, _LAUNCHER_LINES[match[3]]


if __name__ == '__main__':
    try:
        raw = sys.stdin.buffer.read(16385)
        body = json.loads(raw) if len(raw) <= 16384 else None
        observation = startup_observation(body)
    except (ValueError, TypeError, RecursionError):
        observation = StartupObservation()
    print(observation.launcher_line())
