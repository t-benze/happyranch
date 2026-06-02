from __future__ import annotations

from runtime.models import TokenUsage


def test_token_usage_all_fields_optional():
    u = TokenUsage()
    assert u.input_tokens is None
    assert u.output_tokens is None
    assert u.cache_read_tokens is None
    assert u.cache_creation_tokens is None
    assert u.reasoning_tokens is None
    assert u.model is None
    assert u.usage_raw_json is None


def test_token_usage_total_excludes_cache_reads():
    u = TokenUsage(
        input_tokens=100,
        output_tokens=50,
        cache_read_tokens=200,
        cache_creation_tokens=80,
        reasoning_tokens=30,
    )
    # Per spec §3.1: total = input + output + reasoning. Cache reads excluded.
    assert u.total == 100 + 50 + 30


def test_token_usage_total_treats_none_as_zero():
    u = TokenUsage(input_tokens=10)
    assert u.total == 10  # output=None and reasoning=None contribute 0


def test_token_usage_round_trip_via_model_dump():
    u = TokenUsage(
        input_tokens=1, output_tokens=2, cache_read_tokens=3,
        cache_creation_tokens=4, reasoning_tokens=5, model="claude-sonnet-4-6",
        usage_raw_json='{"raw":"x"}',
    )
    d = u.model_dump()
    u2 = TokenUsage(**d)
    assert u2 == u
