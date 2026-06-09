from __future__ import annotations

import pytest

from runtime.orchestrator.org_config import OrgConfig, OrgConfigError


def test_dreaming_missing_block_defaults_disabled() -> None:
    cfg = OrgConfig.load_from_text("")
    assert cfg.dreaming.enabled is False


def test_dreaming_full_block_parses() -> None:
    cfg = OrgConfig.load_from_text("""
dreaming:
  enabled: true
  schedule:
    time: "02:00"
    timezone: "Asia/Shanghai"
    catch_up_on_startup: true
  agents:
    mode: whitelist
    include: [dev_agent, qa_engineer]
    exclude: [qa_engineer]
""")
    assert cfg.dreaming.enabled is True
    assert cfg.dreaming.schedule_time == "02:00"
    assert cfg.dreaming.timezone == "Asia/Shanghai"
    assert cfg.dreaming.catch_up_on_startup is True
    assert cfg.dreaming.agent_mode == "whitelist"
    assert cfg.dreaming.include_agents == ["dev_agent", "qa_engineer"]
    assert cfg.dreaming.exclude_agents == ["qa_engineer"]


@pytest.mark.parametrize(
    "text,match",
    [
        ("dreaming: true\n", "dreaming must be a mapping"),
        ("dreaming:\n  enabled: nope\n", "dreaming.enabled must be a boolean"),
        ("dreaming:\n  enabled: true\n  schedule:\n    time: '2am'\n", "HH:MM"),
        ("dreaming:\n  enabled: true\n  schedule:\n    timezone: 42\n", "timezone must be a string"),
        ("dreaming:\n  enabled: true\n  agents:\n    mode: everyone\n", "mode must be one of"),
        ("dreaming:\n  enabled: true\n  agents:\n    include: dev_agent\n", "include must be a list"),
        ("dreaming:\n  enabled: true\n  agents:\n    exclude: [true]\n", "exclude entries must be strings"),
    ],
)
def test_dreaming_invalid_config_rejected(text: str, match: str) -> None:
    with pytest.raises(OrgConfigError, match=match):
        OrgConfig.load_from_text(text)
