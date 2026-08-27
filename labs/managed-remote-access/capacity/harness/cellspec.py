"""Headscale cell config generation for the lab (headscale v0.25 schema).

Each synthetic tenant cell is one headscale container with its own config,
database volume, and keyset. The generated config is deliberately minimal
and lab-safe: every cell runs its own embedded DERP server (region 999,
lab-only, auto-added to the DERP map — headscale v0.25.1 fatals on an empty
map) so the DERP relay is never disabled or bypassed and no external DERP
map/share is claimed; DNS is disabled (no third-party resolvers); and the
only URL anywhere is the internal docker-network server URL. No port of the
control plane is ever published to the host. Config generation fails closed:
``validate_headscale_config`` is applied before the text is returned.
"""

from __future__ import annotations

import io
from functools import lru_cache

import yaml

from models import derp_region_count

# Pinned by digest — resolved from Docker Hub on 2026-08-26:
#   headscale/headscale:0.25   linux/amd64
#   tailscale/tailscale:v1.80.0 linux/amd64 (era-matched with headscale 0.25)
HEADSCALE_IMAGE = (
    "headscale/headscale:0.25@sha256:ae91e47e0a8ab481e41bc83b72dc2bc9f7bca2b5dbe5448414c8ae9511f33541"
)
TAILSCALE_IMAGE = (
    "tailscale/tailscale:v1.80.0@sha256:5d36f58996def4b60e943ee6c15b4f3ad040299565f9971d8f541b250dd72f03"
)

# Per-cell host ports for the metrics endpoint only (bound to loopback).
# Control (8080) and gRPC (50443) stay on the internal lab network only.
_METRICS_HOST_PORT_BASE = 49000
_HTTP_HOST_PORT_BASE = 48000
_GRPC_HOST_PORT_BASE = 45000


def server_url(run_id: str, cell: int) -> str:
    return f"http://hs-{run_id}-c{cell}:8080"


def client_login_url(run_id: str, cell: int) -> str:
    return server_url(run_id, cell)


def lab_ports(cell: int) -> tuple[int, int, int]:
    """Return (http_host_port, grpc_host_port, metrics_host_port) for a cell."""
    return (
        _HTTP_HOST_PORT_BASE + cell,
        _GRPC_HOST_PORT_BASE + cell,
        _METRICS_HOST_PORT_BASE + cell,
    )


def headscale_config(run_id: str, cell: int, ephemeral_timeout: str = "75s") -> str:
    """Generate a deterministic headscale v0.25 config for one synthetic cell."""
    if ephemeral_timeout != "75s":
        raise ValueError("lab config pins ephemeral_node_inactivity_timeout=75s")
    url = server_url(run_id, cell)
    if not url.startswith("http://hs-") or ":8080" not in url:
        raise ValueError(f"refusing to generate config with non-lab server_url: {url}")
    cfg = {
        "server_url": url,
        "listen_addr": "0.0.0.0:8080",
        "grpc_listen_addr": "0.0.0.0:50443",
        "metrics_listen_addr": "0.0.0.0:9090",
        "noise": {"private_key_path": "/var/lib/headscale/noise_private.key"},
        "derp": {
            "server": {
                "enabled": True,
                "region_id": 999,
                "region_code": "lab",
                "region_name": "HappyRanch Lab DERP",
                # v0.25.1 fatal: "derp.server.stun_listen_addr must be set if
                # derp.server.enabled is true". Bound on the container's
                # internal network so synthetic clients can reach it.
                "stun_listen_addr": "0.0.0.0:3478",
                # Auto-generated on first boot (same pattern as the noise key).
                "private_key_path": "/var/lib/headscale/derp_server_private.key",
                # Auto-add the embedded region: headscale v0.25.1 refuses to
                # boot with an empty DERP map ("initial DERPMap is empty,
                # Headscale requires at least one entry"). The region node's
                # host/port come from server_url, so the embedded relay is the
                # cell's own control port on the internal docker network.
                "automatically_add_embedded_derp_region": True,
            },
            # Lab-only relay: no external DERP maps (no production DERP share,
            # no public-relay bypass) and no auto-update fetch.
            "urls": [],
            "paths": [],
            "auto_update_enabled": False,
        },
        "database": {
            "type": "sqlite",
            "sqlite": {
                "path": "/var/lib/headscale/db.sqlite",
                "write_ahead_log": True,
            },
        },
        # headscale v0.25 reads the map form prefixes.v4/v6; a list under
        # `prefixes` (or the legacy `ip_prefixes` key) is not recognized and
        # headscale refuses to start without a prefix.
        "prefixes": {"v4": "100.64.0.0/10", "v6": "fd7a:115c:a1e0::/48"},
        # MagicDNS off (v0.25 defaults it on and then requires base_domain);
        # no nameservers, so no third-party resolver is ever configured.
        "dns": {"magic_dns": False},
        "policy": {"path": ""},
        "log": {"format": "text", "level": "info"},
        "ephemeral_node_inactivity_timeout": ephemeral_timeout,
        "randomize_client_port": False,
    }
    buf = io.StringIO()
    yaml.safe_dump(cfg, buf, sort_keys=False)
    text = buf.getvalue()
    errs = validate_headscale_config(cfg)
    if errs:
        raise ValueError("invalid lab headscale config: " + "; ".join(errs))
    return text


def validate_headscale_config(cfg: dict) -> list[str]:
    """Fail-closed validation of a generated headscale config dict.

    Returns a list of violation strings (empty = valid). Checks mirror the
    headscale v0.25.1 startup fatals (verified against the v0.25.1 source and
    the config-example.yaml) plus the lab-only invariants: a non-empty DERP
    map, embedded-server prerequisites, no external DERP maps or auto-update,
    internal-only server URL, MagicDNS off, and map-form prefixes.
    """
    errs: list[str] = []
    url = cfg.get("server_url", "")
    if not url.startswith("http://hs-") or ":8080" not in url:
        errs.append(f"non-lab server_url: {url!r}")

    derp = cfg.get("derp")
    if not isinstance(derp, dict):
        return errs + ["derp block missing"]
    server = derp.get("server")
    if not isinstance(server, dict):
        return errs + ["derp.server block missing"]

    # headscale v0.25.1 app.go: len(DERPMap.Regions) == 0 -> fatal
    # "initial DERPMap is empty, Headscale requires at least one entry".
    if derp_region_count(cfg) == 0:
        errs.append("initial DERPMap is empty, Headscale requires at least one entry")

    if server.get("enabled"):
        if not server.get("stun_listen_addr"):
            # headscale v0.25.1 config.go fatal.
            errs.append("derp.server.stun_listen_addr must be set if derp.server.enabled is true")
        if not server.get("automatically_add_embedded_derp_region", True) and not (derp.get("paths") or []):
            # headscale v0.25.1 config.go fatal.
            errs.append(
                "Disabling derp.server.automatically_add_embedded_derp_region requires "
                "to configure the derp server in derp.paths"
            )
    if server.get("region_id") != 999 or server.get("region_code") != "lab":
        errs.append(f"lab DERP region must be 999/lab, got {server.get('region_id')}/{server.get('region_code')!r}")

    # Lab-only relay invariants: never claim/provision an external DERP share
    # and never fetch a DERP map from outside the lab network.
    if derp.get("urls"):
        errs.append(f"derp.urls must be empty in the lab (external DERP share would be claimed): {derp['urls']!r}")
    if derp.get("paths"):
        errs.append(f"derp.paths must be empty in the lab (no file-based external DERP maps): {derp['paths']!r}")
    if derp.get("auto_update_enabled"):
        errs.append("derp.auto_update_enabled must be false in the lab (no external DERP map fetch)")

    dns = cfg.get("dns")
    if not isinstance(dns, dict) or dns.get("magic_dns") is not False:
        errs.append("dns.magic_dns must be false (no base_domain, no resolvers)")

    prefixes = cfg.get("prefixes")
    if not isinstance(prefixes, dict) or not prefixes.get("v4") or not prefixes.get("v6"):
        errs.append("prefixes map form (v4/v6) required by headscale v0.25")

    return errs
