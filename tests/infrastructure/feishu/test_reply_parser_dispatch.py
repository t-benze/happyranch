from __future__ import annotations

from runtime.infrastructure.feishu.reply_parser import (
    DispatchIntent,
    parse_top_level_message,
)


def test_dispatch_with_team_and_brief():
    out = parse_top_level_message("DISPATCH engineering\nfix the scraper")
    assert out == DispatchIntent(team="engineering", brief="fix the scraper")


def test_dispatch_without_team():
    out = parse_top_level_message("DISPATCH\nfix the scraper")
    assert out == DispatchIntent(team=None, brief="fix the scraper")


def test_dispatch_multiline_brief():
    out = parse_top_level_message("DISPATCH\nline 1\nline 2\n\nline 3")
    assert out == DispatchIntent(team=None, brief="line 1\nline 2\n\nline 3")


def test_dispatch_lowercase_verb():
    out = parse_top_level_message("dispatch engineering\nbrief")
    assert out == DispatchIntent(team="engineering", brief="brief")


def test_dispatch_empty_brief_returns_none():
    assert parse_top_level_message("DISPATCH engineering\n") is None
    assert parse_top_level_message("DISPATCH\n   \n") is None


def test_dispatch_unknown_verb_returns_none():
    assert parse_top_level_message("APPROVE\nbody") is None
    assert parse_top_level_message("hello world") is None


def test_dispatch_keeps_full_team_tail():
    out = parse_top_level_message("DISPATCH engineering ignored\nbrief")
    # Team is the rest of the verb line stripped — we don't truncate, so the
    # daemon can error with a helpful "unknown team" rather than silently
    # accept a typo.
    assert out is not None
    assert out.team == "engineering ignored"
    assert out.brief == "brief"
