"""Read-only UFW evidence for IPv4 home dial ports; never manages rules."""
from __future__ import annotations

import re
import subprocess

BLOCKED = frozenset({'host_firewall_blocks_home', 'host_firewall_blocks_tcp_8443',
                     'host_firewall_blocks_udp_3478'})
_PORTS = ((8443, 'tcp'), (3478, 'udp'))


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
    """Only positive, fully recognized active/default-deny evidence can veto.

    Any IPv4 allow for a port (including a source-scoped LAN rule) prevents a
    blocking claim for that port. Profiles/interfaces/unknown forms are not
    resolved here: they may allow traffic, so return unverified instead.
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
    if default[1] == 'allow':
        return 'clear'
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
            continue  # No allow proof; default already denies incoming.
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
    if len(missing) == 2:
        return 'host_firewall_blocks_home'
    return 'host_firewall_blocks_tcp_8443' if (8443, 'tcp') in missing else 'host_firewall_blocks_udp_3478'


def inspect_ufw(*, timeout: float = 2.0) -> str:
    """Fixed root-child command; no shell, caller inputs, rules or stderr output."""
    try:
        result = subprocess.run(('/usr/sbin/ufw', 'status', 'verbose'),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env={'LC_ALL': 'C', 'LANG': 'C'}, cwd='/', timeout=min(2.0, timeout), check=False)
        return parse_ufw(result.stdout) if result.returncode == 0 else 'unverified'
    except FileNotFoundError:
        return 'unknown'  # No UFW evidence; other firewall engines are not inferred.
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return 'unverified'
