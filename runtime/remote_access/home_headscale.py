"""Unwired, pure home Headscale v0.25.1 rendering and LAN address selection.

These functions neither inspect the host nor establish custody or readiness.
The installer must supply a complete inventory and protect the rendered files.
"""
from __future__ import annotations

import re
import socket
from dataclasses import dataclass

import yaml

from runtime.remote_access import invite_v2


class HomeHeadscaleError(ValueError):
    """Closed categories: inventory, no_address, multiple_addresses, dial_ip, policy_path."""


@dataclass(frozen=True, repr=False)
class LANInterface:
    """Observed interface identity and local address literals (without CIDRs)."""

    index: int
    up: bool
    loopback: bool
    addresses: tuple[str, ...]


@dataclass(frozen=True, repr=False)
class LANDefaultRoute:
    """One default route, its selected local source and resolved Linux table ID.

    Include IPv6, every ECMP hop and unresolved sources (empty string). The
    four-byte RTA_TABLE overrides the header. Legacy callers default to IPv4
    and main (254), so they cannot silently ignore an unresolved IPv4 source.
    """

    interface_index: int
    source_ip: str
    table: int = 254
    family: int = int(socket.AF_INET)


def _dial_ip_valid(dial_ip: object) -> bool:
    return type(dial_ip) is str and invite_v2._canonical_dial_ip(dial_ip)


def select_lan_dial_ip(
    interfaces: tuple[LANInterface, ...], default_routes: tuple[LANDefaultRoute, ...],
) -> str:
    """Validate all defaults, then accept exactly one usable RFC1918 source.

    Down/loopback interfaces cannot supply a usable route. Missing/duplicate
    interface identities or a source not assigned to its interface refuse.
    IPv6 defaults cannot choose an IPv4 dial address. Among IPv4 defaults, only
    a non-main default with no preferred source is ignored, after record and
    interface validation. No table/metric/device ranking or address fallback.
    Multiple usable routes refuse even when their selected source is identical.
    """
    if type(interfaces) is not tuple or type(default_routes) is not tuple:
        raise HomeHeadscaleError("inventory")
    by_index: dict[int, LANInterface] = {}
    for interface in interfaces:
        if (type(interface) is not LANInterface or type(interface.index) is not int
                or interface.index <= 0 or interface.index in by_index
                or type(interface.up) is not bool or type(interface.loopback) is not bool
                or type(interface.addresses) is not tuple
                or any(type(address) is not str for address in interface.addresses)):
            raise HomeHeadscaleError("inventory")
        by_index[interface.index] = interface
    for route in default_routes:
        if (type(route) is not LANDefaultRoute or type(route.interface_index) is not int
                or type(route.source_ip) is not str or route.interface_index not in by_index
                or type(route.table) is not int or not 0 <= route.table <= 0xffffffff
                or type(route.family) is not int or route.family not in (socket.AF_INET, socket.AF_INET6)):
            raise HomeHeadscaleError("inventory")
        if (route.family == socket.AF_INET and (route.table == 254 or route.source_ip)
                and route.source_ip not in by_index[route.interface_index].addresses):
            raise HomeHeadscaleError("inventory")
    candidates: list[str] = []
    for route in default_routes:
        if route.family != socket.AF_INET:
            continue
        if route.table != 254 and not route.source_ip:
            continue
        interface = by_index[route.interface_index]
        if not interface.up or interface.loopback:
            continue
        if not _dial_ip_valid(route.source_ip):
            raise HomeHeadscaleError("dial_ip")
        candidates.append(route.source_ip)
    if not candidates:
        raise HomeHeadscaleError("no_address")
    if len(candidates) != 1:
        raise HomeHeadscaleError("multiple_addresses")
    return candidates[0]


def render_headscale_config(*, dial_ip: str, policy_path: str) -> str:
    """Render deterministic YAML for protected config.yaml, without reading files.

    Policy is an existing, separately reviewed direct-child .hujson file under
    the fixed config directory. Its contents/ownership are the caller's gate.
    No CA signing key is passed to the service.
    """
    if not _dial_ip_valid(dial_ip):
        raise HomeHeadscaleError("dial_ip")
    if (type(policy_path) is not str or not re.fullmatch(
            r"/etc/happyranch/headscale/[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.hujson", policy_path)):
        raise HomeHeadscaleError("policy_path")
    config = {
        "server_url": "https://control.happyranch.invalid:8443",
        "listen_addr": f"{dial_ip}:8443",
        "metrics_listen_addr": "127.0.0.1:19090",
        "grpc_listen_addr": "127.0.0.1:50443",
        "grpc_allow_insecure": False,
        "tls_cert_path": "/etc/happyranch/headscale/server.crt",
        "tls_key_path": "/etc/happyranch/headscale/server.key",
        "tls_letsencrypt_hostname": "",
        "tls_letsencrypt_listen": "",
        "tls_letsencrypt_cache_dir": "/var/lib/happyranch-headscale/acme-unused",
        # v0.25.1 validates the enum even when ACME is disabled. No HTTP-01 path.
        "tls_letsencrypt_challenge_type": "TLS-ALPN-01",
        "acme_url": "",
        "acme_email": "",
        "noise": {"private_key_path": "/var/lib/happyranch-headscale/noise.key"},
        "derp": {
            "server": {
                "enabled": True,
                "region_id": 999,
                "region_code": "happyranch-home",
                "region_name": "HappyRanch Home",
                "stun_listen_addr": f"{dial_ip}:3478",
                "private_key_path": "/var/lib/happyranch-headscale/derp.key",
                "automatically_add_embedded_derp_region": False,
                "ipv4": dial_ip,
                "ipv6": "none",
            },
            "urls": [],
            "paths": ["/etc/happyranch/headscale/derp.yaml"],
            "auto_update_enabled": False,
            "update_frequency": "0s",
        },
        "disable_check_updates": True,
        "database": {
            "type": "sqlite",
            "debug": False,
            "gorm": {
                "prepare_stmt": False,
                "parameterized_queries": True,
                "skip_err_record_not_found": True,
                "slow_threshold": 0,
            },
            "sqlite": {
                "path": "/var/lib/happyranch-headscale/db.sqlite",
                "write_ahead_log": False,
                "wal_autocheckpoint": 1,
            },
        },
        "prefixes": {"v4": "100.64.0.0/10", "v6": "", "allocation": "random"},
        "dns": {
            "magic_dns": False,
            "base_domain": "",
            "nameservers": {"global": [], "split": {}},
            "search_domains": [],
            "extra_records": [],
        },
        "policy": {"mode": "file", "path": policy_path},
        "unix_socket": "/run/happyranch-headscale/headscale.sock",
        "unix_socket_permission": "0600",
        "oidc": {"issuer": "", "client_id": "", "client_secret": "", "client_secret_path": ""},
        "cli": {"address": "", "api_key": "", "insecure": False, "timeout": "5s"},
        "logtail": {"enabled": False},
        "log": {"format": "json", "level": "disabled"},
        "randomize_client_port": False,
        # Home nodes are persistent, not ephemeral; retain the validated 0.25.1
        # default for this otherwise unused collector (>65s is mandatory).
        "ephemeral_node_inactivity_timeout": "120s",
    }
    return yaml.safe_dump(config, sort_keys=True, allow_unicode=False)


def render_private_derp_map(*, dial_ip: str) -> str:
    """Render the one-region/one-node map consumed by v0.25.1 derp.paths.

    Go yaml.v3 uses lowercase field names, unlike the on-wire JSON DERP map.
    IPv6='none' disables AAAA resolution; an empty string would enable it.
    """
    if not _dial_ip_valid(dial_ip):
        raise HomeHeadscaleError("dial_ip")
    return yaml.safe_dump({
        "regions": {999: {
            "regionid": 999,
            "regioncode": "happyranch-home",
            "regionname": "HappyRanch Home",
            "nodes": [{
                "name": "999a",
                "regionid": 999,
                "hostname": "control.happyranch.invalid",
                "ipv4": dial_ip,
                "ipv6": "none",
                "derpport": 8443,
                "stunport": 3478,
                "stunonly": False,
                "canport80": False,
            }],
        }},
    }, sort_keys=True, allow_unicode=False)
