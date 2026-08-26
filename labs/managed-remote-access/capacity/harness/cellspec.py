"""Headscale cell config generation for the lab (headscale v0.25 schema).

Each synthetic tenant cell is one headscale container with its own config,
database volume, and keyset. The generated config is deliberately minimal
and lab-safe: DERP is disabled, DNS is disabled (no third-party resolvers),
and the only URL anywhere is the internal docker-network server URL. No
port of the control plane is ever published to the host.
"""

from __future__ import annotations

import io
from functools import lru_cache

import yaml

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
            "server": {"enabled": False, "region_id": 999, "region_code": "lab"},
            "urls": [],
            "auto_update_enabled": False,
        },
        "database": {
            "type": "sqlite",
            "sqlite": {
                "path": "/var/lib/headscale/db.sqlite",
                "write_ahead_log": True,
            },
        },
        "prefixes": ["100.64.0.0/10", "fd7a:115c:a1e0::/48"],
        "dns": {"override_local_dns": False},
        "policy": {"path": ""},
        "log": {"format": "text", "level": "info"},
        "ephemeral_node_inactivity_timeout": ephemeral_timeout,
        "randomize_client_port": False,
    }
    buf = io.StringIO()
    yaml.safe_dump(cfg, buf, sort_keys=False)
    return buf.getvalue()
