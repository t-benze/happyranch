"""Nonsecret optional F3 effect records in the existing activation journal."""
from __future__ import annotations

from dataclasses import dataclass
from ipaddress import ip_address, ip_network

from runtime.remote_access.home_activation import HomeActivationError

COMMENT = 'happyranch-home-headscale'
PORTS = ((8443, 'tcp'), (3478, 'udp'))
STATES = frozenset({'pending', 'add_intent', 'added', 'preexisting_not_owned',
    'blocked_by_existing', 'skipped_ufw_absent', 'skipped_ufw_inactive', 'skipped_ufw_lock_unavailable',
    'skipped_subnet_rejected', 'remove_intent', 'removed', 'unverified'})
_PRIVATE = tuple(ip_network(net) for net in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))


def valid_subnet(subnet: str, dial_ip: str) -> bool:
    try:
        net, address = ip_network(subnet), ip_address(dial_ip)
        return (str(net) == subnet and address in net and net.prefixlen >= 16
                and any(net.version == private.version and net.subnet_of(private) for private in _PRIVATE))
    except (TypeError, ValueError):
        return False


@dataclass(frozen=True)
class FirewallRecord:
    subnet: str | None
    states: tuple[str, str]

    def validate(self, dial_ip: str) -> None:
        if (type(self.states) is not tuple or len(self.states) != 2
                or any(type(state) is not str or state not in STATES for state in self.states)
                or (self.subnet is None and self.states != ('skipped_subnet_rejected',) * 2)
                or (self.subnet is not None and (type(self.subnet) is not str
                    or not valid_subnet(self.subnet, dial_ip)))):
            raise HomeActivationError()

    def to_dict(self) -> dict:
        return {'subnet': self.subnet, 'states': list(self.states)}

    @classmethod
    def parse(cls, value: object) -> FirewallRecord:
        if (type(value) is not dict or value.keys() != {'subnet', 'states'}
                or type(value['states']) is not list):
            raise HomeActivationError()
        return cls(value['subnet'], tuple(value['states']))


def validate_firewall_transition(before: FirewallRecord | None, after: FirewallRecord) -> None:
    if before is None or before.subnet is None or before.states == ('removed',) * 2:
        if after.states not in (('pending',) * 2, ('skipped_subnet_rejected',) * 2):
            raise HomeActivationError('transition')
        return
    if before.subnet != after.subnet:
        raise HomeActivationError('transition')
    changes = [(old, new) for old, new in zip(before.states, after.states) if old != new]
    if len(changes) != 1:
        raise HomeActivationError('transition')
    old, new = changes[0]
    if new == 'add_intent' and old not in ('remove_intent', 'skipped_subnet_rejected'):
        return
    if old == 'add_intent' and new in ('added', 'preexisting_not_owned', 'blocked_by_existing',
                                      'skipped_ufw_absent', 'skipped_ufw_inactive', 'skipped_ufw_lock_unavailable'):
        return
    if new == 'remove_intent' and old != 'skipped_subnet_rejected':
        return
    if new == 'removed' and old in ('remove_intent', 'pending', 'skipped_ufw_absent',
                                   'skipped_ufw_inactive', 'skipped_ufw_lock_unavailable'):
        return
    raise HomeActivationError('transition')
