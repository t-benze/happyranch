"""Pure historical Headscale config/DERP rendering for protected readback."""
from __future__ import annotations

import re

import yaml

from runtime.remote_access import invite_v2


class HomeHeadscaleError(ValueError):
    """Closed categories: inventory, no_address, multiple_addresses, dial_ip, policy_path."""






def _dial_ip_valid(dial_ip: object) -> bool:
    return type(dial_ip) is str and invite_v2._canonical_dial_ip(dial_ip)




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
