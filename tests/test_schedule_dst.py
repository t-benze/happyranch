"""Independent DST-localization contract tests."""
from datetime import datetime, timedelta

from runtime.orchestrator.schedule_rules import _localize_or_skip


def test_new_york_spring_forward_gap_is_skipped():
    assert _localize_or_skip(datetime(2026, 3, 8, 2, 30), "America/New_York") is None


def test_london_spring_forward_gap_is_skipped():
    assert _localize_or_skip(datetime(2026, 3, 29, 1, 30), "Europe/London") is None


def test_new_york_fall_back_uses_first_instance():
    occurrence = _localize_or_skip(datetime(2026, 11, 1, 1, 30), "America/New_York")
    assert occurrence is not None
    assert occurrence.utcoffset() == timedelta(hours=-4)
    assert occurrence.fold == 0
