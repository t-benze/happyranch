from __future__ import annotations

import json
from pathlib import Path

from runtime.orchestrator.executors import _parse_claude_usage


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
    # Real Claude result envelopes no longer carry a top-level `model`; it is
    # read from the `modelUsage` object keyed by model id.
    assert u.model == "claude-sonnet-4-6"
    assert u.usage_raw_json is not None
    raw = json.loads(u.usage_raw_json)
    assert raw["input_tokens"] == 12345


def test_parse_claude_usage_model_from_modelusage_multiple_keys():
    """When `modelUsage` spans multiple models, pick the key with the most
    output_tokens (the 'canonical model this session ran on', mirroring the
    opencode last-model doctrine). Pins the deterministic choice."""
    payload = json.dumps({
        "type": "result",
        "usage": {"input_tokens": 10, "output_tokens": 20},
        "modelUsage": {
            "claude-haiku-4-5": {"inputTokens": 5, "outputTokens": 3},
            "claude-opus-4-8": {"inputTokens": 5, "outputTokens": 17},
            "claude-sonnet-4-6": {"inputTokens": 5, "outputTokens": 9},
        },
    })
    u = _parse_claude_usage(payload)
    assert u is not None
    assert u.model == "claude-opus-4-8"  # highest outputTokens wins


def test_parse_claude_usage_modelusage_preferred_over_legacy_top_level():
    """A legacy top-level `model` is only a fallback; `modelUsage` wins."""
    payload = json.dumps({
        "type": "result",
        "model": "legacy-top-level",
        "usage": {"input_tokens": 1, "output_tokens": 2},
        "modelUsage": {"claude-opus-4-8": {"outputTokens": 2}},
    })
    u = _parse_claude_usage(payload)
    assert u is not None
    assert u.model == "claude-opus-4-8"


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


from runtime.orchestrator.executors import _parse_codex_usage


def _codex_fixture() -> str:
    return (FIXTURES / "usage_codex.jsonl").read_text()


def test_parse_codex_usage_happy_path():
    u = _parse_codex_usage(_codex_fixture())
    assert u is not None
    assert u.input_tokens == 34887
    assert u.output_tokens == 9003
    assert u.cache_read_tokens == 15003  # mapped from `cached_input_tokens`
    assert u.cache_creation_tokens is None  # Codex doesn't separate creation
    assert u.reasoning_tokens == 1234  # mapped from `reasoning_output_tokens`
    # Codex `exec --json` v0.137.0 carries no model on any event (confirmed
    # against live output); model stays NULL until/unless Codex emits one.
    assert u.model is None


def test_parse_codex_usage_no_terminal_usage_event():
    # A real stream that never reaches `turn.completed` (e.g. killed mid-turn)
    # must still leave an auditable forensic row with NULL token fields.
    stream = (
        '{"type":"thread.started","thread_id":"t1"}\n'
        '{"type":"turn.started"}\n'
        '{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"hi"}}\n'
    )
    u = _parse_codex_usage(stream)
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is not None


def test_parse_codex_usage_skips_non_json_lines():
    stream = '\nWARNING: some stderr\n{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":2}}\n'
    u = _parse_codex_usage(stream)
    assert u is not None
    assert u.input_tokens == 1
    assert u.output_tokens == 2


def test_parse_codex_usage_takes_last_turn_completed():
    stream = (
        '{"type":"turn.completed","usage":{"input_tokens":1}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":99}}\n'
    )
    u = _parse_codex_usage(stream)
    assert u is not None
    assert u.input_tokens == 99


def test_parse_codex_usage_empty_stdout():
    assert _parse_codex_usage("") is None


from runtime.orchestrator.executors import _parse_opencode_usage


def _opencode_fixture() -> str:
    return (FIXTURES / "usage_opencode.json").read_text()


def test_parse_opencode_usage_sums_assistant_messages():
    u = _parse_opencode_usage(_opencode_fixture())
    assert u is not None
    assert u.input_tokens == 300       # 100 + 200
    assert u.output_tokens == 125      # 50 + 75
    assert u.cache_read_tokens == 100  # 0 + 100
    assert u.cache_creation_tokens == 100  # mapped from cache_write_tokens; 100 + 0
    assert u.model == "claude-sonnet-4-6"


def test_parse_opencode_usage_malformed_json():
    u = _parse_opencode_usage("not json")
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is not None


def test_parse_opencode_usage_no_assistant_messages():
    stream = '{"messages": [{"role": "user", "content": "hi"}]}'
    u = _parse_opencode_usage(stream)
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is not None


def test_parse_opencode_usage_empty_stdout():
    assert _parse_opencode_usage("") is None


def test_parse_opencode_usage_top_level_is_a_list():
    """Spec §8.1: parsers handle unexpected payload schema, never raise."""
    u = _parse_opencode_usage("[1, 2, 3]")
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is not None


def test_parse_opencode_usage_messages_is_not_a_list():
    """`messages` is a string instead of a list — must not raise."""
    u = _parse_opencode_usage('{"messages": "oops"}')
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is not None


def test_parse_opencode_usage_assistant_missing_usage_field():
    """One assistant turn lacks `usage`, another has it — only sum the one with data."""
    import json as _json
    payload = _json.dumps({
        "messages": [
            {"role": "assistant", "model": "x", "content": "tool call"},  # no usage
            {"role": "assistant", "model": "x", "content": "final",
             "usage": {"input_tokens": 50, "output_tokens": 25}},
        ]
    })
    u = _parse_opencode_usage(payload)
    assert u is not None
    assert u.input_tokens == 50
    assert u.output_tokens == 25


def _opencode_jsonl_fixture() -> str:
    return (FIXTURES / "usage_opencode_jsonl.json").read_text()


def test_parse_opencode_usage_jsonl_step_finish_tokens():
    """New opencode >= 1.14.31 JSONL format: step_finish.part.tokens."""
    u = _parse_opencode_usage(_opencode_jsonl_fixture())
    assert u is not None
    assert u.input_tokens == 5000
    assert u.output_tokens == 2000
    assert u.cache_read_tokens == 3000
    assert u.cache_creation_tokens == 1000
    assert u.model == "claude-sonnet-4-6"
    assert u.usage_raw_json is not None


def test_parse_opencode_usage_jsonl_no_step_finish_falls_back_to_assistant():
    """JSONL format without step_finish events falls back to summing
    assistant usage events."""
    stream = (
        '{"type":"assistant","model":"sonnet",'
        '"usage":{"input_tokens":300,"output_tokens":100}}\n'
        '{"type":"assistant","model":"sonnet",'
        '"usage":{"input_tokens":200,"output_tokens":50}}\n'
    )
    u = _parse_opencode_usage(stream)
    assert u is not None
    assert u.input_tokens == 500
    assert u.output_tokens == 150
    assert u.model == "sonnet"


def test_parse_opencode_usage_jsonl_empty():
    """JSONL format with no usable events."""
    stream = '{"type":"thread.started"}\n{"type":"step_started"}\n'
    u = _parse_opencode_usage(stream)
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is not None


def test_parse_opencode_usage_jsonl_skips_non_json_lines():
    """JSONL parser tolerates blank lines and non-JSON lines."""
    stream = (
        '\n'
        'WARNING: something\n'
        '{"type":"step_finish","part":{"tokens":{"input_tokens":100,"output_tokens":50}}}\n'
    )
    u = _parse_opencode_usage(stream)
    assert u is not None
    assert u.input_tokens == 100
    assert u.output_tokens == 50


# ---- Pi structured parsing (issue #216 addendum) ----

from runtime.orchestrator.executors import _parse_pi_usage


def _pi_fixture() -> str:
    return (FIXTURES / "usage_pi.jsonl").read_text()


def test_parse_pi_usage_structured_from_message_end():
    """Pi 0.80.2+ JSONL: message_end event carries usage at message.usage.
    The fixture includes a turn_end AFTER message_end; the parser must use the
    LAST terminal event's usage (turn_end wins)."""
    u = _parse_pi_usage(_pi_fixture())
    assert u is not None
    assert u.input_tokens == 999      # turn_end.message.usage.input (last terminal)
    assert u.output_tokens == 999     # turn_end.message.usage.output
    assert u.cache_read_tokens == 999  # turn_end.message.usage.cacheRead
    assert u.cache_creation_tokens == 999  # turn_end.message.usage.cacheWrite
    assert u.model == "pi-model-v1"
    assert u.usage_raw_json is not None


def test_parse_pi_usage_message_end_only_no_turn_end():
    """When only message_end is present (no turn_end), use its usage."""
    stdout = '{"type":"message_end","model":"pi-1","message":{"usage":{"input":10,"output":20,"cacheRead":3,"cacheWrite":4,"totalTokens":37}}}\n'
    u = _parse_pi_usage(stdout)
    assert u is not None
    assert u.input_tokens == 10
    assert u.output_tokens == 20
    assert u.cache_read_tokens == 3
    assert u.cache_creation_tokens == 4
    assert u.model == "pi-1"


def test_parse_pi_usage_turn_end_no_message_end():
    """When only turn_end is present (no message_end), use its usage."""
    stdout = '{"type":"turn_end","model":"pi-2","message":{"usage":{"input":99,"output":88,"totalTokens":187}}}\n'
    u = _parse_pi_usage(stdout)
    assert u is not None
    assert u.input_tokens == 99
    assert u.output_tokens == 88
    assert u.cache_read_tokens is None  # missing from event
    assert u.cache_creation_tokens is None  # missing from event
    assert u.model == "pi-2"


def test_parse_pi_usage_skips_non_terminal_assistant():
    """Assistant events with message.usage are not terminal events; parser
    must skip them and fall back to raw-only."""
    stdout = (
        '{"type":"assistant","model":"pi","message":{"usage":{"input":5,"output":5}}}\n'
    )
    u = _parse_pi_usage(stdout)
    assert u is not None
    assert u.input_tokens is None
    assert u.output_tokens is None
    assert u.usage_raw_json is not None


def test_parse_pi_usage_falls_back_to_raw_on_unrecognized():
    """Pi stdout without a parseable terminal event falls back to raw-only."""
    u = _parse_pi_usage('{"type":"result","model":"pi"}\n')
    assert u is not None
    assert u.input_tokens is None
    assert u.output_tokens is None
    assert "pi" in (u.usage_raw_json or "")


def test_parse_pi_usage_empty():
    assert _parse_pi_usage("") is None


def test_parse_pi_usage_raw_fallback_on_malformed():
    """Malformed Pi output preserves raw JSON for forensics."""
    u = _parse_pi_usage("not json")
    assert u is not None
    assert u.input_tokens is None
    assert u.usage_raw_json is not None


def test_parse_pi_usage_terminal_event_with_null_message_falls_back_to_raw():
    """A terminal turn_end event where message is null (not a dict)
    must not raise AttributeError and must fall back to raw-only TokenUsage."""
    u = _parse_pi_usage('{"type":"turn_end","message":null}\n')
    assert u is not None
    assert u.input_tokens is None
    assert u.output_tokens is None
    assert u.usage_raw_json is not None
