"""Provider-independent reported-state normalization for Usage v1.

This module is deliberately pure. It transforms one stored usage row and the
built-in parser's declared semantics; it does not read the database or branch
on executor names itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from runtime.orchestrator.executors import (
    PARSER_USAGE_SEMANTICS,
    ParserUsageSemantics,
)


class StoredUsageRow(Protocol):
    """The string-indexed surface shared by dict and sqlite3.Row."""

    def __getitem__(self, key: str) -> object: ...


class ReportedState(StrEnum):
    REPORTED = "reported"
    NOT_REPORTED = "not_reported"


@dataclass(frozen=True)
class TokenClassState:
    state: ReportedState
    value: int | None = None
    partial_uncached_subtotal: int | None = None


@dataclass(frozen=True)
class NormalizedUsage:
    fresh_input: TokenClassState
    reread: TokenClassState
    output: TokenClassState
    parseable: bool


def _field(row: StoredUsageRow, key: str) -> object:
    try:
        return row[key]
    except (IndexError, KeyError):
        return None


def _reported_int(value: object) -> int | None:
    return value if type(value) is int else None


def _reported(value: int) -> TokenClassState:
    return TokenClassState(state=ReportedState.REPORTED, value=value)


def _not_reported(
    *, partial_uncached_subtotal: int | None = None,
) -> TokenClassState:
    return TokenClassState(
        state=ReportedState.NOT_REPORTED,
        partial_uncached_subtotal=partial_uncached_subtotal,
    )


def normalize_output_tokens(
    output_tokens: object,
    reasoning_tokens: object,
    semantics: ParserUsageSemantics | None,
) -> TokenClassState:
    """Normalize output while counting a reported reasoning subset once."""
    output = _reported_int(output_tokens)
    if output is None:
        return _not_reported()

    reasoning = _reported_int(reasoning_tokens)
    if semantics is None:
        return _not_reported() if reasoning is not None else _reported(output)
    if semantics.reasoning == "separate":
        return _reported(output + (reasoning or 0))
    if semantics.reasoning in ("in_output", "absent"):
        return _reported(output)
    return _not_reported()


def normalize_usage(row: StoredUsageRow) -> NormalizedUsage:
    """Normalize one stored usage row into the Usage v1 reported-state model.

    ``parseable`` is the coverage-numerator predicate: both stored input and
    stored output must be reported integers. Class-level completeness remains
    independent, so a parseable row may still have Fresh input ``not_reported``
    when cache-write reporting is absent.
    """
    input_tokens = _reported_int(_field(row, "input_tokens"))
    output_tokens = _reported_int(_field(row, "output_tokens"))
    cache_read_tokens = _reported_int(_field(row, "cache_read_tokens"))
    cache_creation_tokens = _reported_int(_field(row, "cache_creation_tokens"))
    reasoning_tokens = _reported_int(_field(row, "reasoning_tokens"))

    if input_tokens is None:
        fresh_input = _not_reported()
    elif cache_creation_tokens is None:
        fresh_input = _not_reported(partial_uncached_subtotal=input_tokens)
    else:
        fresh_input = _reported(input_tokens + cache_creation_tokens)

    reread = (
        _reported(cache_read_tokens)
        if cache_read_tokens is not None
        else _not_reported()
    )

    executor = _field(row, "executor")
    semantics = (
        PARSER_USAGE_SEMANTICS.get(executor)
        if isinstance(executor, str)
        else None
    )
    output = normalize_output_tokens(output_tokens, reasoning_tokens, semantics)

    return NormalizedUsage(
        fresh_input=fresh_input,
        reread=reread,
        output=output,
        parseable=input_tokens is not None and output_tokens is not None,
    )
