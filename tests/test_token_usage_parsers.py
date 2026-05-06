from __future__ import annotations

import json
from pathlib import Path

from src.orchestrator.executors import _parse_claude_usage


FIXTURES = Path(__file__).parent / "fixtures"


def _claude_fixture() -> str:
    return (FIXTURES / "usage_claude.json").read_text()


def test_parse_claude_usage_happy_path():
    u = _parse_claude_usage(_claude_fixture())
    assert u is not None
    assert u.input_tokens == 12345
    assert u.output_tokens == 4201
    assert u.cache_read_tokens == 8402
    assert u.cache_creation_tokens == 8042
    assert u.reasoning_tokens is None  # Claude doesn't bill reasoning separately
    assert u.model == "claude-sonnet-4-6"
    assert u.usage_raw_json is not None
    raw = json.loads(u.usage_raw_json)
    assert raw["input_tokens"] == 12345


def test_parse_claude_usage_malformed_returns_raw_json_with_null_fields():
    u = _parse_claude_usage("not valid json {{{")
    # Per spec §4.3: parser never returns None on a non-empty stdout; instead
    # returns TokenUsage with token fields NULL and raw payload preserved.
    assert u is not None
    assert u.input_tokens is None
    assert u.output_tokens is None
    assert u.usage_raw_json is not None
    assert "not valid json" in u.usage_raw_json


def test_parse_claude_usage_missing_usage_block():
    payload = json.dumps({"type": "result", "result": "ok", "model": "claude"})
    u = _parse_claude_usage(payload)
    assert u is not None
    assert u.input_tokens is None
    assert u.output_tokens is None
    assert u.model == "claude"
    assert u.usage_raw_json == payload


def test_parse_claude_usage_empty_stdout():
    u = _parse_claude_usage("")
    assert u is None or (u.input_tokens is None and (u.usage_raw_json is None or u.usage_raw_json == ""))
