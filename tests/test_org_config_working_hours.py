from __future__ import annotations

import pytest

from runtime.orchestrator.org_config import OrgConfig, OrgConfigError


_WINDOWED = """
working_hours:
  enabled: true
  default:
    mode: windowed
    window:
      start: "09:00"
      end: "18:00"
      timezone: "Asia/Shanghai"
    interval: "2h"
    days: [mon, tue, wed, thu, fri]
    catch_up_on_startup: true
  agents:
    mode: all
"""

_CONTINUOUS = """
working_hours:
  enabled: true
  default:
    mode: continuous
    interval: "30m"
    timezone: "Asia/Shanghai"
  agents:
    mode: all
"""

_THREE_TIER = """
working_hours:
  enabled: true
  default:
    mode: windowed
    window:
      start: "09:00"
      end: "18:00"
      timezone: "Asia/Shanghai"
    interval: "2h"
    days: [mon, tue, wed, thu, fri]
  teams:
    engineering:
      interval: "3h"
    customer_service:
      mode: continuous
      interval: "30m"
  overrides:
    triage_bot:
      mode: continuous
      interval: "1h"
"""


def test_missing_block_defaults_disabled() -> None:
    cfg = OrgConfig.load_from_text("")
    assert cfg.working_hours.enabled is False


def test_windowed_parses_and_resolves() -> None:
    cfg = OrgConfig.load_from_text(_WINDOWED)
    assert cfg.working_hours.enabled is True
    sched = cfg.working_hours.resolve_for("dev_agent", None)
    assert sched.mode == "windowed"
    assert sched.window_start == "09:00"
    assert sched.window_end == "18:00"
    assert sched.timezone == "Asia/Shanghai"
    assert sched.interval == "2h"
    assert sched.days == ("mon", "tue", "wed", "thu", "fri")
    assert sched.catch_up_on_startup is True


def test_continuous_parses_and_ignores_window_and_days() -> None:
    cfg = OrgConfig.load_from_text(_CONTINUOUS)
    sched = cfg.working_hours.resolve_for("triage_bot", None)
    assert sched.mode == "continuous"
    assert sched.interval == "30m"
    assert sched.timezone == "Asia/Shanghai"
    assert sched.window_start is None
    assert sched.window_end is None
    assert sched.days is None
    # catch_up defaults to True when no tier sets it.
    assert sched.catch_up_on_startup is True


def test_continuous_via_team_ignores_inherited_window_and_days() -> None:
    cfg = OrgConfig.load_from_text(_THREE_TIER)
    # default is windowed with window+days; the team flips mode to continuous.
    sched = cfg.working_hours.resolve_for("cs_agent", "customer_service")
    assert sched.mode == "continuous"
    assert sched.interval == "30m"
    # timezone inherited from default's window.timezone.
    assert sched.timezone == "Asia/Shanghai"
    assert sched.window_start is None
    assert sched.days is None


def test_three_tier_precedence_leaf_by_leaf() -> None:
    cfg = OrgConfig.load_from_text(_THREE_TIER)

    # Team overrides only the interval leaf; window/days/mode inherit from default.
    eng = cfg.working_hours.resolve_for("dev_agent", "engineering")
    assert eng.mode == "windowed"
    assert eng.interval == "3h"            # team default wins over org default 2h
    assert eng.window_start == "09:00"     # inherited from org default
    assert eng.days == ("mon", "tue", "wed", "thu", "fri")

    # Agent override wins over the team default for the interval leaf.
    triage = cfg.working_hours.resolve_for("triage_bot", "customer_service")
    assert triage.mode == "continuous"
    assert triage.interval == "1h"         # override beats team's 30m
    assert triage.timezone == "Asia/Shanghai"

    # An agent with no team layer and no override falls all the way to default.
    plain = cfg.working_hours.resolve_for("nobody", None)
    assert plain.interval == "2h"
    assert plain.mode == "windowed"


def test_partial_override_inherits_unset_leaves() -> None:
    text = """
working_hours:
  enabled: true
  default:
    mode: windowed
    window:
      start: "09:00"
      end: "18:00"
      timezone: "Asia/Shanghai"
    interval: "2h"
    days: [mon, tue, wed, thu, fri]
  overrides:
    dev_agent:
      interval: "4h"
"""
    cfg = OrgConfig.load_from_text(text)
    sched = cfg.working_hours.resolve_for("dev_agent", None)
    assert sched.interval == "4h"          # override leaf
    assert sched.mode == "windowed"        # inherited
    assert sched.window_end == "18:00"     # inherited
    assert sched.days == ("mon", "tue", "wed", "thu", "fri")  # inherited


@pytest.mark.parametrize(
    "text,match",
    [
        ("working_hours: true\n", "working_hours must be a mapping"),
        ("working_hours:\n  enabled: nope\n", "working_hours.enabled must be a boolean"),
        (
            "working_hours:\n  enabled: true\n  default:\n    mode: sideways\n",
            "mode must be one of",
        ),
        (
            "working_hours:\n  enabled: true\n  default:\n    mode: windowed\n"
            "    window: {start: '9am', end: '18:00', timezone: 'UTC'}\n"
            "    interval: '2h'\n    days: [mon]\n",
            "window.start must be HH:MM",
        ),
        (
            "working_hours:\n  enabled: true\n  default:\n    mode: windowed\n"
            "    window: {start: '18:00', end: '09:00', timezone: 'UTC'}\n"
            "    interval: '2h'\n    days: [mon]\n",
            "window.start must be before window.end",
        ),
        (
            "working_hours:\n  enabled: true\n  default:\n    mode: continuous\n"
            "    interval: '30m'\n    timezone: 'Mars/Phobos'\n",
            "unknown",
        ),
        (
            "working_hours:\n  enabled: true\n  default:\n    mode: continuous\n"
            "    interval: '2x'\n    timezone: 'UTC'\n",
            "must be Nh or Nm",
        ),
        (
            "working_hours:\n  enabled: true\n  default:\n    mode: windowed\n"
            "    window: {start: '09:00', end: '10:00', timezone: 'UTC'}\n"
            "    interval: '2h'\n    days: [mon]\n",
            "longer than the window length",
        ),
        (
            "working_hours:\n  enabled: true\n  default:\n    mode: continuous\n"
            "    interval: '5h'\n    timezone: 'UTC'\n",
            "must evenly divide 24h",
        ),
        (
            "working_hours:\n  enabled: true\n  default:\n    mode: windowed\n"
            "    window: {start: '09:00', end: '18:00', timezone: 'UTC'}\n"
            "    interval: '2h'\n    days: [funday]\n",
            "invalid days",
        ),
        (
            "working_hours:\n  enabled: true\n  agents:\n    mode: everyone\n",
            "agents.mode must be one of",
        ),
        (
            "working_hours:\n  enabled: true\n  agents:\n    include: dev_agent\n",
            "working_hours.agents.include must be a list",
        ),
    ],
)
def test_invalid_config_rejected(text: str, match: str) -> None:
    with pytest.raises(OrgConfigError, match=match):
        OrgConfig.load_from_text(text)
