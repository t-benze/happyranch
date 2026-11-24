"""Standalone pure-renderer checks; synthetic LAN inventory, no host inspection."""
from __future__ import annotations


import pytest
import yaml

from runtime.remote_access import home_headscale as home
from runtime.remote_access import invite_v2
from scripts.test_invite_v2 import NOW, fixture, inputs, wire

IP = "192.168.1.40"
POLICY = "/etc/happyranch/headscale/policy.hujson"




def config() -> dict:
    return yaml.safe_load(home.render_headscale_config(dial_ip=IP, policy_path=POLICY))


def test_headscale_pinned_contract() -> None:
    value = config()
    for key, expected in {
        "server_url": "https://control.happyranch.invalid:8443",
        "listen_addr": "192.168.1.40:8443",
        "metrics_listen_addr": "127.0.0.1:19090",
        "grpc_listen_addr": "127.0.0.1:50443",
        "grpc_allow_insecure": False,
        "tls_letsencrypt_hostname": "",
        "tls_letsencrypt_listen": "",
        "tls_cert_path": "/etc/happyranch/headscale/server.crt",
        "tls_key_path": "/etc/happyranch/headscale/server.key",
        "noise": {"private_key_path": "/var/lib/happyranch-headscale/noise.key"},
        "unix_socket": "/run/happyranch-headscale/headscale.sock",
        "unix_socket_permission": "0600",
        "policy": {"mode": "file", "path": POLICY},
    }.items():
        assert value[key] == expected, key
    assert value["derp"] == {
        "server": {
            "enabled": True, "region_id": 999, "region_code": "happyranch-home",
            "region_name": "HappyRanch Home", "stun_listen_addr": "192.168.1.40:3478",
            "private_key_path": "/var/lib/happyranch-headscale/derp.key",
            "automatically_add_embedded_derp_region": False, "ipv4": IP, "ipv6": "none",
        },
        "urls": [], "paths": ["/etc/happyranch/headscale/derp.yaml"],
        "auto_update_enabled": False, "update_frequency": "0s",
    }


def test_restrictive_defaults_and_no_extra_keys() -> None:
    value = config()
    assert "extra_records_path" not in value["dns"]
    # These literals are the documented configuration contract, independent of
    # the renderer's data structure. Exact key sets catch added active surfaces.
    unpinned = {
        "acme_url": "", "acme_email": "",
        "tls_letsencrypt_challenge_type": "TLS-ALPN-01",
        "tls_letsencrypt_cache_dir": "/var/lib/happyranch-headscale/acme-unused",
        "disable_check_updates": True,
        "database": {
            "type": "sqlite", "debug": False,
            "gorm": {"prepare_stmt": False, "parameterized_queries": True,
                     "skip_err_record_not_found": True, "slow_threshold": 0},
            "sqlite": {"path": "/var/lib/happyranch-headscale/db.sqlite",
                       "write_ahead_log": False, "wal_autocheckpoint": 1},
        },
        "prefixes": {"v4": "100.64.0.0/10", "v6": "", "allocation": "random"},
        "dns": {"magic_dns": False, "base_domain": "", "nameservers": {"global": [], "split": {}},
                "search_domains": [], "extra_records": []},
        "oidc": {"issuer": "", "client_id": "", "client_secret": "", "client_secret_path": ""},
        "cli": {"address": "", "api_key": "", "insecure": False, "timeout": "5s"},
        "logtail": {"enabled": False}, "log": {"format": "json", "level": "disabled"},
        "randomize_client_port": False, "ephemeral_node_inactivity_timeout": "120s",
    }
    pinned_keys = {
        "server_url", "listen_addr", "metrics_listen_addr", "grpc_listen_addr", "grpc_allow_insecure",
        "tls_cert_path", "tls_key_path", "tls_letsencrypt_hostname", "tls_letsencrypt_listen",
        "noise", "derp", "policy", "unix_socket", "unix_socket_permission",
    }
    assert set(value) == pinned_keys | unpinned.keys()
    for key, expected in unpinned.items():
        assert value[key] == expected, key


def test_private_map_exact_tuple() -> None:
    assert yaml.safe_load(home.render_private_derp_map(dial_ip=IP)) == {
        "regions": {999: {
            "regionid": 999, "regioncode": "happyranch-home", "regionname": "HappyRanch Home",
            "nodes": [{"name": "999a", "regionid": 999,
                       "hostname": "control.happyranch.invalid", "ipv4": IP, "ipv6": "none",
                       "derpport": 8443, "stunport": 3478, "stunonly": False, "canport80": False}],
        }},
    }


def test_deterministic_independent_rendering() -> None:
    first_config = home.render_headscale_config(dial_ip=IP, policy_path=POLICY)
    first_map = home.render_private_derp_map(dial_ip=IP)
    other_ip = "10.1.2.3"
    other_config = home.render_headscale_config(dial_ip=other_ip, policy_path=POLICY)
    other_map = home.render_private_derp_map(dial_ip=other_ip)
    assert first_config != other_config and first_map != other_map
    assert home.render_headscale_config(dial_ip=IP, policy_path=POLICY).encode() == first_config.encode()
    assert home.render_private_derp_map(dial_ip=IP).encode() == first_map.encode()

    def ordered(value: object) -> None:
        if isinstance(value, dict):
            assert list(value) == sorted(value)
            for child in value.values():
                ordered(child)
        elif isinstance(value, list):
            for child in value:
                ordered(child)

    ordered(yaml.safe_load(first_config))
    ordered(yaml.safe_load(first_map))


@pytest.mark.parametrize("ip", ["10.0.0.0", "10.255.255.255", "172.16.0.0", "172.31.255.255",
                                  "192.168.0.0", "192.168.255.255", IP])
def test_renderer_accepts_shared_rfc1918_boundaries(ip: str) -> None:
    f = fixture(); f["control_tls"]["dial_ip"] = ip
    assert invite_v2.decode(wire(f), now=NOW).dial_ip == ip
    assert invite_v2.decode(invite_v2.encode(**(inputs() | {"dial_ip": ip})).invite_text, now=NOW).dial_ip == ip
    assert yaml.safe_load(home.render_headscale_config(dial_ip=ip, policy_path=POLICY))["listen_addr"] == ip + ":8443"
    assert yaml.safe_load(home.render_private_derp_map(dial_ip=ip))["regions"][999]["nodes"][0]["ipv4"] == ip


@pytest.mark.parametrize("ip", ["", "192.168.001.40", "192.168.1.40 ", " 10.1.2.3", "10.1.2.3/24",
                                  "10.1.2.3:8443", "10.1.2.999", "127.0.0.1", "169.254.1.2",
                                  "8.8.8.8", "100.64.0.1", "172.15.255.255", "172.32.0.0",
                                  "192.169.0.1", "0.0.0.0", "::", "::1", "fd00::1",
                                  "::ffff:192.168.1.40", "SYNTHETIC-INPUT-CANARY"])
def test_invalid_ip_refuses_at_every_boundary(ip: str) -> None:
    operations = (
        lambda: home.render_headscale_config(dial_ip=ip, policy_path=POLICY),
        lambda: home.render_private_derp_map(dial_ip=ip),
    )
    for operation in operations:
        with pytest.raises(home.HomeHeadscaleError) as error:
            operation()
        assert error.value.args == ("dial_ip",)
        assert error.value.__context__ is None and error.value.__cause__ is None
    f = fixture(); f["control_tls"]["dial_ip"] = ip
    for operation in (lambda: invite_v2.decode(wire(f), now=NOW),
                      lambda: invite_v2.encode(**(inputs() | {"dial_ip": ip}))):
        with pytest.raises(invite_v2.InviteError) as error:
            operation()
        assert error.value.args == ("url",)
        assert error.value.__context__ is None and error.value.__cause__ is None




@pytest.mark.parametrize("path", ["", "policy.hujson", "/tmp/policy.hujson", "/etc/happyranch/headscale/../policy.hujson",
                                    "/etc/happyranch/headscale/sub/policy.hujson", "/etc/happyranch/headscale/server.key",
                                    "/etc/happyranch/headscale/x.hujson\n", None, True])
def test_policy_path_refusals(path: object) -> None:
    with pytest.raises(home.HomeHeadscaleError) as error:
        home.render_headscale_config(dial_ip=IP, policy_path=path)
    assert error.value.args == ("policy_path",)


@pytest.mark.parametrize("ip", [None, True, 123, b"192.168.1.40"])
def test_renderer_type_refusals(ip: object) -> None:
    for operation in (lambda: home.render_headscale_config(dial_ip=ip, policy_path=POLICY),
                      lambda: home.render_private_derp_map(dial_ip=ip)):
        with pytest.raises(home.HomeHeadscaleError) as error:
            operation()
        assert error.value.args == ("dial_ip",)


def test_one_rule_is_used_by_codec_and_renderer(monkeypatch: pytest.MonkeyPatch) -> None:
    # Test-side interception of the shared existing rule. The observations are
    # real renderer/encode/decode refusals, not a helper-call count.
    assert config()["listen_addr"] == IP + ":8443"
    original = invite_v2.encode(**inputs()).invite_text
    monkeypatch.setattr(invite_v2, "_canonical_dial_ip", lambda _: False)
    with pytest.raises(home.HomeHeadscaleError, match="^dial_ip$"):
        home.render_headscale_config(dial_ip=IP, policy_path=POLICY)
    with pytest.raises(invite_v2.InviteError, match="^url$"):
        invite_v2.encode(**inputs())
    with pytest.raises(invite_v2.InviteError, match="^url$"):
        invite_v2.decode(original, now=NOW)
