"""Tests for the generic CLI result-envelope parser (THR-107 Phase 1)."""
from __future__ import annotations

import json

from runtime.models import TokenUsage
from runtime.orchestrator.executors import _parse_generic_cli_usage


BEGIN = "__HR_ENVELOPE_BEGIN__"
END = "__HR_ENVELOPE_END__"


def _envelope(stdout_body: str, envelope_json: str | None = None) -> str:
    if envelope_json is None:
        return stdout_body
    return "Some normal output before...\n" + BEGIN + "\n" + envelope_json + "\n" + END + "\n...some normal output after"


# ── Absent envelope ─────────────────────────────────────────────────────


def test_no_envelope_returns_none():
    result = _parse_generic_cli_usage("Some CLI output with no envelope")
    assert result is None


def test_empty_stdout_returns_none():
    result = _parse_generic_cli_usage("")
    assert result is None


def test_whitespace_stdout_returns_none():
    result = _parse_generic_cli_usage("   \n  \t  ")
    assert result is None


def test_no_sentinel_in_full_stdout():
    result = _parse_generic_cli_usage("Agent response: completed task successfully.")
    assert result is None


# ── Valid envelope — field mapping ──────────────────────────────────────


def test_valid_envelope_full_mapping():
    env = json.dumps({
        "envelope_version": 1,
        "token_usage": {
            "input_tokens": 1500,
            "output_tokens": 420,
            "cache_read_tokens": 300,
            "cache_creation_tokens": 50,
            "reasoning_tokens": 200,
            "model": "my-cli-v2",
            "usage_raw_json": '{"raw": true}',
        },
        "model": "top-level-model",
    })
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens == 1500
    assert result.output_tokens == 420
    assert result.cache_read_tokens == 300
    assert result.cache_creation_tokens == 50
    assert result.reasoning_tokens == 200
    assert result.model == "my-cli-v2"
    assert result.usage_raw_json == '{"raw": true}'


def test_valid_envelope_partial_fields():
    env = json.dumps({
        "envelope_version": 1,
        "token_usage": {
            "input_tokens": 100,
            "output_tokens": 50,
        },
    })
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens == 100
    assert result.output_tokens == 50
    assert result.cache_read_tokens is None
    assert result.cache_creation_tokens is None
    assert result.reasoning_tokens is None
    assert result.model is None
    assert result.usage_raw_json is None


def test_token_usage_model_absent_top_level_model_present():
    env = json.dumps({
        "envelope_version": 1,
        "token_usage": {
            "input_tokens": 100,
            "output_tokens": 50,
        },
        "model": "global-model-v3",
    })
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.model == "global-model-v3"


def test_total_excludes_cache_reads():
    env = json.dumps({
        "envelope_version": 1,
        "token_usage": {
            "input_tokens": 100,
            "output_tokens": 200,
            "cache_read_tokens": 9999,
            "reasoning_tokens": 50,
        },
    })
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.total == 350  # 100 + 200 + 50


# ── Malformed / parse failures — forensic preservation ─────────────────


def test_missing_end_marker_forensic_preservation():
    stdout = "normal output\n" + BEGIN + "\n" + json.dumps({"envelope_version": 1}) + "\n...output continues"
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens is None
    assert result.usage_raw_json is not None
    assert "envelope_version" in result.usage_raw_json


def test_invalid_json_forensic_preservation():
    stdout = "normal output\n" + BEGIN + "\n{this is not valid json\n" + END + "\ntrailing"
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens is None
    assert result.usage_raw_json is not None
    assert "not valid json" in (result.usage_raw_json or "")


def test_missing_envelope_version_forensic_preservation():
    env = json.dumps({
        "token_usage": {"input_tokens": 100, "output_tokens": 50},
    })
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens is None
    assert result.usage_raw_json is not None
    assert "input_tokens" in (result.usage_raw_json or "")


def test_wrong_envelope_version_forensic_preservation():
    env = json.dumps({
        "envelope_version": 2,
        "token_usage": {"input_tokens": 100},
    })
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens is None
    assert result.usage_raw_json is not None
    assert "envelope_version" in (result.usage_raw_json or "")


def test_envelope_version_zero_rejected():
    env = json.dumps({
        "envelope_version": 0,
        "token_usage": {"input_tokens": 100},
    })
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens is None
    assert result.usage_raw_json is not None


def test_envelope_version_string_rejected():
    env = json.dumps({
        "envelope_version": "1",
        "token_usage": {"input_tokens": 100},
    })
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens is None
    assert result.usage_raw_json is not None


# ── Multiple envelopes — last wins ──────────────────────────────────────


def test_multiple_envelopes_last_wins():
    env1 = json.dumps({
        "envelope_version": 1,
        "token_usage": {"input_tokens": 100, "output_tokens": 50, "model": "first"},
    })
    env2 = json.dumps({
        "envelope_version": 1,
        "token_usage": {"input_tokens": 999, "output_tokens": 333, "model": "last"},
    })
    stdout = BEGIN + "\n" + env1 + "\n" + END + "\nintermediate output\n" + BEGIN + "\n" + env2 + "\n" + END
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens == 999
    assert result.output_tokens == 333
    assert result.model == "last"


def test_multiple_envelopes_malformed_first_valid_second():
    env_valid = json.dumps({
        "envelope_version": 1,
        "token_usage": {"input_tokens": 42},
    })
    stdout = BEGIN + "\nbroken\n" + BEGIN + "\n" + env_valid + "\n" + END
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens == 42


# ── result field ignored in Phase 1 ─────────────────────────────────────


def test_result_field_ignored():
    env = json.dumps({
        "envelope_version": 1,
        "result": "the agent's final answer",
        "token_usage": {"input_tokens": 50},
    })
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens == 50


# ── Edge cases ──────────────────────────────────────────────────────────


def test_envelope_with_no_token_usage():
    env = json.dumps({"envelope_version": 1})
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens is None
    assert result.output_tokens is None
    assert result.model is None


def test_envelope_with_null_token_usage():
    env = json.dumps({"envelope_version": 1, "token_usage": None})
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens is None


def test_sentinel_appears_in_normal_output_rfind_handles_it():
    env = json.dumps({
        "envelope_version": 1,
        "token_usage": {"input_tokens": 77},
    })
    stdout = "The string " + BEGIN + " appears in normal text.\n" + BEGIN + "\n" + env + "\n" + END
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.input_tokens == 77


def test_no_begin_but_has_end():
    stdout = "Just some output\n" + END + "\nmore output"
    result = _parse_generic_cli_usage(stdout)
    assert result is None


def test_cache_fields_separate():
    env = json.dumps({
        "envelope_version": 1,
        "token_usage": {
            "input_tokens": 1,
            "cache_read_tokens": 100,
            "cache_creation_tokens": 200,
        },
    })
    stdout = _envelope("stdout", env)
    result = _parse_generic_cli_usage(stdout)
    assert result is not None
    assert result.cache_read_tokens == 100
    assert result.cache_creation_tokens == 200
