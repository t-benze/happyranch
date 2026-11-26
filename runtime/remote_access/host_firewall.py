"""Read-only UFW evidence for IPv4 home dial ports; never manages rules."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from dataclasses import dataclass

_PORTS = ((8443, 'tcp'), (3478, 'udp'))
# Stock UFW IPv4 templates (scripts/fixtures/ufw), normalized only for comments
# and whitespace. Any customization, even one we could consider harmless,
# makes the probability observation unverified. Stock files never prove live rules.
_STOCK_RULES = (
    ('/etc/ufw/before.rules', '9bfb66e22065371713ecca5c5b5bb067724ccdf389e4eb2fb45e5d6abfd0fcce'),
    ('/etc/ufw/after.rules', 'f65aef81967a426cf4ae8ddfcc2da32ef0471fef1608d778a742891976002ea0'),
)


def _stock_rule_files() -> bool:
    """Bounded fixed-path reads in the existing root child; never expose data."""
    for path, expected in _STOCK_RULES:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > 65536:
                return False
            raw = stream.read(65537)
        if len(raw) > 65536:
            return False
        normalized = '\n'.join(' '.join(line.split()) for line in raw.decode('ascii').splitlines()
                               if line.strip() and not line.lstrip().startswith('#'))
        if hashlib.sha256(normalized.encode('ascii')).hexdigest() != expected:
            return False
    return True


def _covers(destination: str, port: int, protocol: str) -> bool | None:
    if destination == 'Anywhere':
        return True
    match = re.fullmatch(r'([0-9,:]+)(?:/(tcp|udp))?', destination)
    if match is None:
        return None
    if match[2] is not None and match[2] != protocol:
        return False
    covers = False
    for part in match[1].split(','):
        limits = part.split(':')
        if len(limits) > 2 or any(not v.isdecimal() for v in limits):
            return None
        low, high = int(limits[0]), int(limits[-1])
        if not 1 <= low <= high <= 65535:
            return None
        covers |= low <= port <= high
    return covers


def parse_ufw(raw: bytes) -> str:
    """Parse UFW observations; inspect_ufw also checks supplemental files.

    Any IPv4 allow for a port (including a source-scoped LAN rule) prevents a
    likely-blocked observation for that port. Unknown profiles/interfaces are
    not resolved here: they may allow traffic, so return unverified instead.
    """
    if type(raw) is not bytes or len(raw) > 65536:
        return 'unverified'
    try:
        lines = [line.strip() for line in raw.decode('ascii').splitlines() if line.strip()]
    except UnicodeError:
        return 'unverified'
    if lines == ['Status: inactive']:
        return 'clear'
    if lines.count('Status: active') != 1:
        return 'unverified'
    defaults = [line for line in lines if line.startswith('Default:')]
    if len(defaults) != 1:
        return 'unverified'
    default = re.fullmatch(r'Default: (deny|reject|allow) \(incoming\), (?:allow|deny|reject) \(outgoing\), (?:allow|deny|reject|disabled) \(routed\)', defaults[0])
    if default is None:
        return 'unverified'
    allowed: set[tuple[int, str]] = set()
    for line in lines:
        if (line == 'Status: active' or line == defaults[0]
                or re.fullmatch(r'Logging: (?:on(?: \((?:low|medium|high|full)\))?|off)', line)
                or re.fullmatch(r'New profiles: (?:skip|allow|deny|reject)', line)
                or re.fullmatch(r'To\s+Action\s+From', line)
                or re.fullmatch(r'-+\s+-+\s+-+', line)):
            continue
        rule = re.fullmatch(r'(.+?)\s{2,}(ALLOW|DENY|REJECT|LIMIT)(?: (IN|OUT))?\s{2,}(.+)', line)
        if rule is None:
            return 'unverified'
        destination, action, direction, _source = rule.groups()
        if direction == 'OUT' or destination.endswith(' (v6)'):
            continue
        if action == 'LIMIT':
            return 'unverified'  # Rate limits still admit some new connections.
        if action != 'ALLOW':
            continue  # A deny/reject is not a user allow for either port.
        for port, protocol in _PORTS:
            covers = _covers(destination, port, protocol)
            if covers is None:
                return 'unverified'
            if covers:
                # Source restrictions cannot justify a refusal: this may be
                # exactly the intended home LAN. Never publish its contents.
                allowed.add((port, protocol))
    missing = set(_PORTS) - allowed
    if not missing:
        return 'clear'
    if len(missing) == 2 and default[1] == 'deny':
        return 'likely_blocked'
    return 'unverified'


@dataclass(frozen=True)
class UFWObservation:
    advisory: str
    availability: str


def observe_ufw(*, timeout: float = 2.0) -> UFWObservation:
    """Fixed root-child command; no shell, caller inputs, rules or stderr output."""
    try:
        result = subprocess.run(('/usr/sbin/ufw', 'status', 'verbose'),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={'LC_ALL': 'C', 'LANG': 'C'}, cwd='/', timeout=min(2.0, timeout), check=False)
    except FileNotFoundError:
        return UFWObservation('unknown', 'absent')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return UFWObservation('unverified', 'unverified')
    state = parse_ufw(result.stdout) if result.returncode == 0 else 'unverified'
    availability = ('inactive' if result.returncode == 0 and result.stdout.strip() == b'Status: inactive'
                    else 'active' if result.returncode == 0 and result.stdout.startswith(b'Status: active\n')
                    else 'unverified')
    try:
        if result.stdout.strip() != b'Status: inactive' and not _stock_rule_files():
            return UFWObservation('unverified', availability)
    except (OSError, ValueError, UnicodeError):
        return UFWObservation('unverified', availability)
    return UFWObservation(state, availability)


def inspect_ufw(*, timeout: float = 2.0) -> str:
    """Original F2 advisory interface; F3 shares the same bounded observation."""
    return observe_ufw(timeout=timeout).advisory
