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
    assert _parse_claude_usage("") is None
    assert _parse_claude_usage("   \n  ") is None  # whitespace-only is also empty


def test_parse_claude_usage_top_level_is_a_list():
    """Spec §8.1: parsers handle unexpected payload schema, never raise."""
    u = _parse_claude_usage("[1, 2, 3]")
    assert u is not None
    assert u.input_tokens is None
    assert u.model is None
    assert u.usage_raw_json is not None  # raw payload preserved


def test_parse_claude_usage_usage_field_is_not_a_dict():
    payload = json.dumps({"model": "claude", "usage": ["unexpected", "list"]})
    u = _parse_claude_usage(payload)
    assert u is not None
    assert u.input_tokens is None
    assert u.model == "claude"
    assert u.usage_raw_json is not None


from src.orchestrator.executors import _parse_codex_usage


def _codex_fixture() -> str:
    return (FIXTURES / "usage_codex.jsonl").read_text()


def test_parse_codex_usage_happy_path():
    u = _parse_codex_usage(_codex_fixture())
    assert u is not None
    assert u.input_tokens == 34887
    assert u.output_tokens == 9003
    assert u.cache_read_tokens == 15003
    assert u.cache_creation_tokens is None  # Codex doesn't separate creation
    assert u.reasoning_tokens == 1234
    assert u.model == "gpt-5"


def test_parse_codex_usage_no_session_complete_event():
    stream = '{"type":"agent_message","content":"hi"}\n{"type":"tool_call","name":"x"}\n'
    u = _parse_codex_usage(stream)
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is not None


def test_parse_codex_usage_skips_non_json_lines():
    stream = '\nWARNING: some stderr\n{"type":"session_complete","model":"gpt-5","token_usage":{"input_tokens":1,"output_tokens":2}}\n'
    u = _parse_codex_usage(stream)
    assert u is not None
    assert u.input_tokens == 1
    assert u.output_tokens == 2


def test_parse_codex_usage_takes_last_session_complete():
    stream = (
        '{"type":"session_complete","model":"gpt-5","token_usage":{"input_tokens":1}}\n'
        '{"type":"session_complete","model":"gpt-5","token_usage":{"input_tokens":99}}\n'
    )
    u = _parse_codex_usage(stream)
    assert u is not None
    assert u.input_tokens == 99


def test_parse_codex_usage_empty_stdout():
    assert _parse_codex_usage("") is None
