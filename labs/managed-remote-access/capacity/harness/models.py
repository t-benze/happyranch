"""Synthetic identity and resource-naming rules for the capacity lab.

Every rerun of the lab uses a fresh synthetic run id, and every disposable
resource (headscale cells, client nodes, the lab network, cell volumes,
state dirs) is namespaced by that run id. Deterministic cleanup therefore
can only ever touch resources created by the same run.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

# cap-YYYYMMDDTHHMMSSZ-<4 lowercase alnum>
RUN_ID_RE = re.compile(r"^cap-[0-9]{8}T[0-9]{6}Z-[a-z0-9]{4}$")


def make_run_id(now_utc: datetime, rand: str) -> str:
    """Build a synthetic run id from a UTC datetime and a 4-char random suffix."""
    if len(rand) != 4 or not re.fullmatch(r"[a-z0-9]{4}", rand):
        raise ValueError(f"rand suffix must be 4 lowercase alnum chars, got {rand!r}")
    ts = now_utc.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"cap-{ts}-{rand}"


def validate_run_id(run_id: str) -> None:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError(f"invalid lab run id: {run_id!r}")


def headscale_container_name(run_id: str, cell: int) -> str:
    validate_run_id(run_id)
    return f"hs-{run_id}-c{cell}"


def client_container_name(run_id: str, cell: int, node: int) -> str:
    validate_run_id(run_id)
    return f"cl-{run_id}-c{cell}-n{node}"


def network_name(run_id: str) -> str:
    validate_run_id(run_id)
    return f"net-{run_id}"


def volume_name(run_id: str, cell: int) -> str:
    validate_run_id(run_id)
    return f"vol-{run_id}-c{cell}"


def state_dir_name(run_id: str) -> str:
    validate_run_id(run_id)
    return f"st-{run_id}"


def node_online(record: dict, hostname: str) -> bool:
    """True when a parsed ``headscale nodes list --output json`` record shows
    the named synthetic node online.

    The headscale CLI's ``--output json`` serializes the protobuf-generated
    structs with standard ``encoding/json``, which emits the snake_case
    protobuf json tags (``given_name``, ``online``) — not protojson
    camelCase (``givenName``).
    """
    return bool(record.get("online")) and record.get("given_name") == hostname
