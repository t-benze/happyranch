"""Unit tests for headscale cell config generation (v0.25 schema).

The lab generates one headscale config per cell. It must be valid v0.25
config, use only internal lab hostnames, carry a valid lab-only embedded
DERP region (headscale v0.25.1 fatals on an empty DERP map), disable DNS,
and be fully deterministic per (run_id, cell).
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
    validate_headscale_config,
)
from models import derp_region_count


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


def test_config_enables_embedded_lab_derp_and_disables_dns():
    run_id = "cap-20260826T120000Z-ab12"
    cfg = yaml.safe_load(headscale_config(run_id, 1))
    # headscale v0.25.1 fatals at startup with an empty DERP map ("initial
    # DERPMap is empty, Headscale requires at least one entry"), so every
    # lab cell runs its own embedded DERP server (region 999, auto-added).
    server = cfg["derp"]["server"]
    assert server["enabled"] is True
    assert server["region_id"] == 999
    assert server["region_code"] == "lab"
    assert server["region_name"] == "HappyRanch Lab DERP"
    assert server["stun_listen_addr"] == "0.0.0.0:3478"
    assert server["private_key_path"] == "/var/lib/headscale/derp_server_private.key"
    assert server["automatically_add_embedded_derp_region"] is True
    # Lab-only relay: no external DERP maps, no auto-update (no production
    # DERP share, no third-party fetch).
    assert cfg["derp"]["urls"] == []
    assert cfg["derp"]["paths"] == []
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


def test_derp_map_is_nonempty():
    """Green proof of the final-SHA blocker fix.

    Mirrors headscale v0.25.1 startup: the initial DERPMap (embedded region
    auto-added when the server is enabled) must have >= 1 region, otherwise
    headscale exits with "initial DERPMap is empty, Headscale requires at
    least one entry" (observed in lab run 32989032467 idle.failure.log).
    """
    run_id = "cap-20260826T120000Z-ab12"
    cfg = yaml.safe_load(headscale_config(run_id, 1))
    assert derp_region_count(cfg) >= 1


def test_validate_rejects_empty_derp_map():
    """Fail-closed: a config that yields an empty DERPMap is rejected with
    the exact headscale v0.25.1 startup fatal message."""
    run_id = "cap-20260826T120000Z-ab12"
    cfg = yaml.safe_load(headscale_config(run_id, 1))
    # Simulate the pre-fix shape: embedded server disabled, no urls/paths.
    cfg["derp"]["server"]["enabled"] = False
    cfg["derp"].pop("paths", None)
    errs = validate_headscale_config(cfg)
    assert any("DERPMap is empty" in e and "at least one entry" in e for e in errs), errs


def test_validate_rejects_missing_stun_addr():
    run_id = "cap-20260826T120000Z-ab12"
    cfg = yaml.safe_load(headscale_config(run_id, 1))
    del cfg["derp"]["server"]["stun_listen_addr"]
    errs = validate_headscale_config(cfg)
    assert any("stun_listen_addr" in e for e in errs), errs


def test_validate_rejects_external_derp_urls():
    """Fail-closed: no external DERP map may ever be configured — the lab
    relay is the cell's own embedded server; a production DERP share is not
    claimed and public relays must never be bypassed/used."""
    run_id = "cap-20260826T120000Z-ab12"
    cfg = yaml.safe_load(headscale_config(run_id, 1))
    cfg["derp"]["urls"] = ["https://derp.tailscale.com"]
    errs = validate_headscale_config(cfg)
    assert any("derp.urls" in e for e in errs), errs


def test_validate_rejects_derp_auto_update():
    run_id = "cap-20260826T120000Z-ab12"
    cfg = yaml.safe_load(headscale_config(run_id, 1))
    cfg["derp"]["auto_update_enabled"] = True
    errs = validate_headscale_config(cfg)
    assert any("auto_update_enabled" in e for e in errs), errs


def test_validate_accepts_lab_config():
    run_id = "cap-20260826T120000Z-ab12"
    cfg = yaml.safe_load(headscale_config(run_id, 1))
    assert validate_headscale_config(cfg) == []


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
