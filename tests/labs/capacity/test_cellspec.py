"""Unit tests for headscale cell config generation (v0.25 schema).

The lab generates one headscale config per cell. It must be valid v0.25
config, use only internal lab hostnames, disable DERP/DNS, and be fully
deterministic per (run_id, cell).
"""

from __future__ import annotations

import re

import pytest
import yaml

from cellspec import (
    HEADSCALE_IMAGE,
    TAILSCALE_IMAGE,
    headscale_config,
    lab_ports,
    client_login_url,
    server_url,
)


def test_images_pinned_by_digest():
    assert "sha256:" in HEADSCALE_IMAGE
    assert HEADSCALE_IMAGE.startswith("headscale/headscale:0.25@")
    assert "sha256:" in TAILSCALE_IMAGE
    assert TAILSCALE_IMAGE.startswith("tailscale/tailscale:v1.80.0@")


def test_server_url_is_internal_lab_hostname():
    run_id = "cap-20260826T120000Z-ab12"
    url = server_url(run_id, 1)
    assert url == "http://hs-cap-20260826T120000Z-ab12-c1:8080"
    assert client_login_url(run_id, 1) == url


def test_config_is_valid_yaml_and_canonical_keys():
    run_id = "cap-20260826T120000Z-ab12"
    text = headscale_config(run_id, 1)
    cfg = yaml.safe_load(text)
    assert cfg["server_url"] == server_url(run_id, 1)
    assert cfg["listen_addr"] == "0.0.0.0:8080"
    assert cfg["grpc_listen_addr"] == "0.0.0.0:50443"
    assert cfg["metrics_listen_addr"] == "0.0.0.0:9090"
    assert cfg["noise"]["private_key_path"] == "/var/lib/headscale/noise_private.key"
    assert cfg["database"]["type"] == "sqlite"
    assert cfg["database"]["sqlite"]["path"] == "/var/lib/headscale/db.sqlite"
    assert cfg["database"]["sqlite"]["write_ahead_log"] is True
    # headscale v0.25 reads `prefixes.v4` / `prefixes.v6` (map form). A list
    # under `prefixes` (or the legacy `ip_prefixes` key) is not recognized and
    # headscale refuses to start: "no IPv4 or IPv6 prefix configured, minimum
    # one prefix is required".
    assert cfg["prefixes"] == {"v4": "100.64.0.0/10", "v6": "fd7a:115c:a1e0::/48"}
    assert cfg["ephemeral_node_inactivity_timeout"] == "75s"
    assert cfg["randomize_client_port"] is False


def test_config_disables_derp_and_dns():
    run_id = "cap-20260826T120000Z-ab12"
    cfg = yaml.safe_load(headscale_config(run_id, 1))
    assert cfg["derp"]["server"]["enabled"] is False
    assert cfg["derp"]["urls"] == []
    assert cfg["derp"]["auto_update_enabled"] is False
    # MagicDNS is disabled: headscale v0.25 defaults `dns.magic_dns` to true
    # and then fatals without `dns.base_domain`; the lab sets it false so no
    # base_domain is required and no resolver is ever configured.
    assert cfg["dns"]["magic_dns"] is False
    # No external DNS resolvers may be configured (lab cannot egress to
    # third-party resolvers; "cannot target non-lab endpoints").
    assert "nameservers" not in cfg["dns"]
    assert "base_domain" not in cfg["dns"]
    # v0.25 deprecates `dns_config.override_local_dns`; it must not be emitted.
    assert "override_local_dns" not in cfg["dns"]


def test_config_contains_no_external_urls():
    run_id = "cap-20260826T120000Z-ab12"
    text = headscale_config(run_id, 1)
    # Only the internal server_url host may appear; no public hostnames.
    for token in ("tailscale.com", "1.1.1.1", "8.8.8.8", "https://"):
        assert token not in text, f"external token {token!r} leaked into lab config"


def test_config_deterministic_per_cell():
    run_id = "cap-20260826T120000Z-ab12"
    assert headscale_config(run_id, 1) == headscale_config(run_id, 1)
    assert headscale_config(run_id, 1) != headscale_config(run_id, 2)


def test_lab_ports_unique_and_bounded():
    seen = []
    for cell in range(1, 5):
        http, grpc, metrics = lab_ports(cell)
        assert 40000 <= http <= 50000
        assert 40000 <= grpc <= 50000
        assert 40000 <= metrics <= 50000
        seen.extend([http, grpc, metrics])
    assert len(seen) == len(set(seen)), "ports must be unique across cells"
    # Control-plane (8080) and gRPC (50443) ports stay on the internal lab
    # network; only metrics (9090) and the HTTP API (8080) are published to
    # the host, bound to 127.0.0.1 (loopback) so the harness can scrape and
    # poll them. No lab endpoint is ever reachable off-host.
    assert all(port != 8080 for port in seen)
