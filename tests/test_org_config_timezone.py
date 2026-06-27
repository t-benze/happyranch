"""Tests for the org/dreaming timezone resolvers added in TASK-976 (THR-039)."""
from __future__ import annotations

from datetime import timezone, tzinfo
from zoneinfo import ZoneInfo

import pytest

from runtime.orchestrator import org_config as oc
from runtime.orchestrator.org_config import (
    DreamingConfig,
    OrgConfig,
    OrgConfigError,
    resolve_dreaming_timezone,
    resolve_org_timezone,
    resolve_org_timezone_display,
    resolve_timezone_or_local,
)


# ---- top-level OrgConfig.timezone parsing -------------------------------------

def test_org_timezone_omitted_is_none() -> None:
    assert OrgConfig.load_from_text("").timezone is None


def test_org_timezone_explicit_parses() -> None:
    cfg = OrgConfig.load_from_text("timezone: Asia/Shanghai\n")
    assert cfg.timezone == "Asia/Shanghai"


def test_org_timezone_bad_value_rejected_at_load() -> None:
    with pytest.raises(OrgConfigError, match="unknown timezone"):
        OrgConfig.load_from_text("timezone: Mars/Olympus\n")


def test_org_timezone_non_string_rejected() -> None:
    with pytest.raises(OrgConfigError, match="timezone must be a string"):
        OrgConfig.load_from_text("timezone: 42\n")


# ---- dreaming.schedule.timezone now inherits (omitted -> None) ----------------

def test_dreaming_timezone_omitted_is_none() -> None:
    cfg = OrgConfig.load_from_text("dreaming:\n  enabled: true\n")
    assert cfg.dreaming.timezone is None


def test_dreaming_timezone_explicit_still_parses() -> None:
    cfg = OrgConfig.load_from_text(
        "dreaming:\n  enabled: true\n  schedule:\n    timezone: America/New_York\n"
    )
    assert cfg.dreaming.timezone == "America/New_York"


# ---- resolve_org_timezone[_display] ------------------------------------------

def test_resolve_org_timezone_explicit() -> None:
    tz, label = resolve_org_timezone_display(OrgConfig(timezone="Asia/Shanghai"))
    assert tz == ZoneInfo("Asia/Shanghai")
    assert label == "Asia/Shanghai"
    assert resolve_org_timezone(OrgConfig(timezone="Asia/Shanghai")) == ZoneInfo("Asia/Shanghai")


def test_resolve_org_timezone_none_is_machine_local_and_valid() -> None:
    tz, label = resolve_org_timezone_display(OrgConfig(timezone=None))
    assert isinstance(tz, tzinfo)
    assert label  # non-empty display
    # A valid zone yields a concrete offset (proves it is usable, not a crash).
    from datetime import datetime
    assert datetime(2026, 6, 27, tzinfo=timezone.utc).astimezone(tz).utcoffset() is not None


def test_resolve_bad_explicit_value_falls_through_gracefully() -> None:
    # A value that bypassed load-time validation must not crash the resolver.
    tz, label = oc._resolve_timezone("Not/AZone")
    assert isinstance(tz, tzinfo)
    assert label


def test_resolve_offset_fallback_when_no_iana(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oc, "_machine_local_iana", lambda: None)
    tz, label = oc._resolve_timezone(None)
    assert isinstance(tz, tzinfo)
    # Offset-only display is the UTC±HH:MM form (e.g. "UTC", "UTC+08:00").
    assert label.startswith("UTC")


def test_resolve_ultimate_utc_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(oc, "_machine_local_iana", lambda: None)

    def _boom() -> object:
        raise OSError("no clock")

    monkeypatch.setattr(oc, "datetime", type("D", (), {"now": staticmethod(_boom)}))
    tz, label = oc._resolve_timezone(None)
    assert tz == timezone.utc
    assert label == "UTC"


def test_resolve_timezone_or_local_explicit() -> None:
    assert resolve_timezone_or_local("Europe/Paris") == ZoneInfo("Europe/Paris")


# ---- resolve_dreaming_timezone precedence ------------------------------------

def test_dreaming_tz_explicit_wins_over_org() -> None:
    org = OrgConfig(timezone="Asia/Shanghai", dreaming=DreamingConfig(timezone="America/New_York"))
    assert resolve_dreaming_timezone(org) == ZoneInfo("America/New_York")


def test_dreaming_tz_inherits_org_when_omitted() -> None:
    org = OrgConfig(timezone="Asia/Shanghai", dreaming=DreamingConfig(timezone=None))
    assert resolve_dreaming_timezone(org) == ZoneInfo("Asia/Shanghai")


def test_dreaming_tz_machine_local_when_both_omitted() -> None:
    org = OrgConfig(timezone=None, dreaming=DreamingConfig(timezone=None))
    assert isinstance(resolve_dreaming_timezone(org), tzinfo)  # no crash, usable tz
