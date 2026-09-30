from __future__ import annotations

import sqlite3

from runtime.orchestrator.executors import (
    PARSER_USAGE_SEMANTICS,
    ParserUsageSemantics,
)
from runtime.orchestrator.usage_normalization import (
    ReportedState,
    normalize_output_tokens,
    normalize_usage,
)


def _row(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "executor": "codex",
        "input_tokens": 20,
        "output_tokens": 7,
        "cache_read_tokens": 3,
        "cache_creation_tokens": 2,
        "reasoning_tokens": 5,
    }
    row.update(overrides)
    return row


def test_built_in_parser_semantics_are_declared_once_as_data():
    assert {
        executor: semantics.reasoning
        for executor, semantics in PARSER_USAGE_SEMANTICS.items()
    } == {
        "claude": "in_output",
        "codex": "in_output",
        "opencode": "separate",
        "pi": "in_output",
    }
    assert "custom-adapter" not in PARSER_USAGE_SEMANTICS


def test_codex_reported_zero_cache_write_makes_fresh_input_complete():
    normalized = normalize_usage(_row(
        input_tokens=12,
        cache_creation_tokens=0,
    ))

    assert normalized.fresh_input.state is ReportedState.REPORTED
    assert normalized.fresh_input.value == 12
    assert normalized.fresh_input.partial_uncached_subtotal is None
    assert normalized.parseable is True


def test_missing_cache_write_is_not_reported_with_partial_uncached_subtotal():
    normalized = normalize_usage(_row(
        input_tokens=12,
        cache_creation_tokens=None,
    ))

    assert normalized.fresh_input.state is ReportedState.NOT_REPORTED
    assert normalized.fresh_input.value is None
    assert normalized.fresh_input.partial_uncached_subtotal == 12
    assert normalized.parseable is True


def test_missing_input_has_no_fresh_input_subtotal():
    normalized = normalize_usage(_row(input_tokens=None))

    assert normalized.fresh_input.state is ReportedState.NOT_REPORTED
    assert normalized.fresh_input.value is None
    assert normalized.fresh_input.partial_uncached_subtotal is None
    assert normalized.parseable is False


def test_output_reasoning_semantics_count_reasoning_exactly_once():
    codex = normalize_usage(_row(executor="codex", output_tokens=10, reasoning_tokens=4))
    opencode = normalize_usage(_row(executor="opencode", output_tokens=10, reasoning_tokens=4))
    absent = normalize_output_tokens(
        10, 4, ParserUsageSemantics(reasoning="absent")
    )
    undeclared = normalize_usage(_row(
        executor="custom-adapter", output_tokens=10, reasoning_tokens=4,
    ))

    assert codex.output.value == 10
    assert opencode.output.value == 14
    assert absent.value == 10
    assert undeclared.output.state is ReportedState.NOT_REPORTED
    assert undeclared.output.value is None


def test_separate_reasoning_absent_uses_reported_output():
    normalized = normalize_usage(_row(
        executor="opencode", output_tokens=10, reasoning_tokens=None,
    ))

    assert normalized.output.state is ReportedState.REPORTED
    assert normalized.output.value == 10


def test_cache_read_zero_is_reported_and_none_is_not_reported():
    zero = normalize_usage(_row(cache_read_tokens=0))
    missing = normalize_usage(_row(cache_read_tokens=None))

    assert zero.reread.state is ReportedState.REPORTED
    assert zero.reread.value == 0
    assert missing.reread.state is ReportedState.NOT_REPORTED
    assert missing.reread.value is None


def test_parse_failure_row_is_entirely_not_reported_and_not_parseable():
    normalized = normalize_usage(_row(
        input_tokens=None,
        output_tokens=None,
        cache_read_tokens=None,
        cache_creation_tokens=None,
        reasoning_tokens=None,
    ))

    assert normalized.fresh_input.state is ReportedState.NOT_REPORTED
    assert normalized.reread.state is ReportedState.NOT_REPORTED
    assert normalized.output.state is ReportedState.NOT_REPORTED
    assert normalized.parseable is False


def test_output_absent_is_not_reported_for_declared_executor():
    normalized = normalize_usage(_row(output_tokens=None))

    assert normalized.output.state is ReportedState.NOT_REPORTED
    assert normalized.parseable is False


def test_undeclared_executor_without_reasoning_can_use_reported_output():
    normalized = normalize_usage(_row(
        executor="custom-adapter", output_tokens=10, reasoning_tokens=None,
    ))

    assert normalized.output.state is ReportedState.REPORTED
    assert normalized.output.value == 10


def test_normalizer_accepts_a_stored_sqlite_row():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    row = connection.execute(
        """SELECT 'codex' AS executor, 4 AS input_tokens,
                  2 AS output_tokens, 0 AS cache_read_tokens,
                  0 AS cache_creation_tokens, 1 AS reasoning_tokens"""
    ).fetchone()
    assert row is not None

    normalized = normalize_usage(row)

    assert normalized.fresh_input.value == 4
    assert normalized.reread.value == 0
    assert normalized.output.value == 2
    assert normalized.parseable is True
    connection.close()
