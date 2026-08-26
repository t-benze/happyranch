"""Deterministic cleanup and residue detection for the capacity lab.

After every scenario (and on abort), the harness tears down every resource
created under the run id and then asserts no residue remains: containers,
networks, volumes, host processes, and state-dir entries. Parsers are pure
functions so the residue logic is unit-testable without docker.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


def parse_docker_ps_json(text: str) -> list[dict]:
    """Parse ``docker ps -a --format '{{json .}}'`` output (one JSON object/line)."""
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def parse_docker_network_ls(text: str) -> list[str]:
    """Parse ``docker network ls --format '{{.Name}}'`` output."""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def parse_docker_volume_ls(text: str) -> list[str]:
    """Parse ``docker volume ls --format '{{.Name}}'`` output."""
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


_PID_RE = re.compile(r"\d+")


def parse_pgrep(text: str) -> list[int]:
    """Parse ``pgrep -f <pattern>`` output into pid ints (non-numeric lines ignored)."""
    pids: list[int] = []
    for ln in text.splitlines():
        m = _PID_RE.fullmatch(ln.strip())
        if m:
            pids.append(int(m.group(0)))
    return pids


def residue_report(
    containers: list[str],
    networks: list[str],
    volumes: list[str],
    pids: list[int],
    state_entries: list[str],
    run_id: str | None = None,
) -> dict:
    """Report leftover lab resources after teardown.

    When ``run_id`` is given, only resources whose name embeds the run id
    are considered (defense in depth against a mismatched docker label).
    """
    if run_id:
        containers = [n for n in containers if run_id in n]
        networks = [n for n in networks if run_id in n]
        volumes = [n for n in volumes if run_id in n]
        state_entries = [n for n in state_entries if run_id in n]
    leftovers = {
        "containers": sorted(containers),
        "networks": sorted(networks),
        "volumes": sorted(volumes),
        "pids": sorted(pids),
        "state_entries": sorted(state_entries),
    }
    ok = not any(leftovers.values())
    return {"ok": ok, **leftovers}
