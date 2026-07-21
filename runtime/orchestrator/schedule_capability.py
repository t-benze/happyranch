"""Per-agent scheduling capability resolver (THR-105 Phase 4).

Reads the scheduling capability flag from ``org/config.yaml`` under a
``scheduling`` top-level key, without modifying OrgConfig (which is
founder-gated CRITICAL).  Unknown top-level keys in org/config.yaml are
carried through verbatim by OrgConfig's loader, so adding ``scheduling``
does not break existing parsing.

The expected YAML shape::

    scheduling:
      enabled_agents:
        - dev_agent
        - investment_advisor

Default-deny: an agent not in ``enabled_agents``, or the key absent,
returns ``False``.
"""
from __future__ import annotations

from pathlib import Path

import yaml


def is_scheduling_enabled(org_root: Path, agent_name: str) -> bool:
    """Return True iff *agent_name* is in the scheduling enabled_agents list.

    Reads ``<org_root>/org/config.yaml`` directly (raw YAML), bypassing
    OrgConfig.  Returns False on any error: missing file, malformed YAML,
    missing key, empty list — all default-deny.
    """
    config_path = org_root / "org" / "config.yaml"
    if not config_path.is_file():
        return False
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
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
