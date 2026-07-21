"""THR-105 Phase 4: standalone per-agent scheduling capability resolver.

Reads ``scheduling.enabled_agents`` directly from ``org/config.yaml``
(default-deny) — intentionally does NOT route through OrgConfig/
_build_org_config/load_org_config to avoid touching CRITICAL existing
symbols. The founder enables agents individually by adding them to the
list in ``org/config.yaml``.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def resolve_scheduling_enabled(org_root: Path, agent_name: str) -> bool:
    """Return True if *agent_name* is permitted to create schedules.

    Default deny: missing ``scheduling.enabled_agents`` (or a missing
    ``org/config.yaml`` entirely) means no agent has scheduling
    capability.  The founder enables agents individually by adding them
    to ``scheduling.enabled_agents`` in ``org/config.yaml``.
    """
    config_path = org_root / "org" / "config.yaml"
    if not config_path.exists():
        return False

    try:
        raw = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError:
        return False

    if not isinstance(raw, dict):
        return False

    scheduling = raw.get("scheduling")
    if not isinstance(scheduling, dict):
        return False

    enabled = scheduling.get("enabled_agents")
    if not isinstance(enabled, list):
        return False

    return agent_name in enabled
