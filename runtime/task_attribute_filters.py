"""Roots attribute query grammar and native-precision numeric comparisons."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
import math
import re
from collections.abc import Mapping, Sequence


class AttributeFilterError(ValueError):
    def __init__(self, code: str, field: str, reason: str) -> None:
        self.detail = {"code": code, "errors": {field: [reason]}}
        super().__init__(code)


@dataclass(frozen=True)
class AttributeFilter:
    key: str
    minimum: int | float | None = None
    maximum: int | float | None = None
    not_set: bool = False
    definition_id: str | None = None


def _invalid(field: str, reason: str) -> None:
    raise AttributeFilterError("attribute_filter_invalid", field, reason)


def parse_attribute_filters(pairs: Sequence[tuple[str, str]]) -> tuple[AttributeFilter, ...]:
    """Validate occurrences before maps, preserving duplicate detection."""
    if len(pairs) > 64:
        _invalid("attribute_filters", "too_many_predicates")
    predicates: dict[str, dict] = {}
    for operator, raw in pairs:
        key, separator, token = raw.partition("=") if operator in ("attr_min", "attr_max") else (raw, "", "")
        if operator in ("attr_min", "attr_max") and not separator:
            _invalid(operator, "expected_key_equals_number")
        field = f"{operator}.{key}"
        if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None:
            _invalid(operator, "invalid_key")
        value = None
        if operator in ("attr_min", "attr_max"):
            if len(token) > 128:
                _invalid(field, "number_too_long")
            if re.fullmatch(r"-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?", token) is None:
                _invalid(field, "expected_finite_json_number")
            value = json.loads(token)
            if type(value) is float and not math.isfinite(value):
                _invalid(field, "expected_finite_json_number")
        entry = predicates.setdefault(key, {})
        if operator in entry:
            _invalid(field, "duplicate_predicate")
        entry[operator] = value
    if len(predicates) > 16:
        _invalid("attribute_filters", "too_many_keys")
    result = []
    for key, entry in sorted(predicates.items()):
        if "attr_not_set" in entry and len(entry) > 1:
            _invalid(f"attr_not_set.{key}", "conflicting_predicates")
        result.append(AttributeFilter(key, entry.get("attr_min"), entry.get("attr_max"), "attr_not_set" in entry))
    return tuple(result)


def resolve_attribute_filters(filters: tuple[AttributeFilter, ...], definitions: Sequence[Mapping]) -> tuple[AttributeFilter, ...]:
    known = {d["key"]: d for d in definitions}
    resolved = []
    # All syntax is validated first; definition-dependent type precedes order.
    for item in filters:
        definition = known.get(item.key)
        if definition is None:
            raise AttributeFilterError("attribute_filter_unknown_key", item.key, "unknown_key")
        if (item.minimum is not None or item.maximum is not None) and definition["type"] != "numeric":
            raise AttributeFilterError("attribute_filter_not_numeric", item.key, "numeric_definition_required")
        resolved.append(replace(item, definition_id=definition["definition_id"]))
    for item in resolved:
        if item.minimum is not None and item.maximum is not None and item.minimum > item.maximum:
            raise AttributeFilterError("attribute_filter_inverted_range", item.key, "min_exceeds_max")
    return tuple(resolved)


def attribute_numeric_range(value_json: str, minimum_json: str | None, maximum_json: str | None) -> bool:
    """SQLite predicate: never round arbitrary JSON integers through REAL."""
    value = json.loads(value_json)
    if type(value) not in (int, float) or (type(value) is float and not math.isfinite(value)):
        return False
    return ((minimum_json is None or value >= json.loads(minimum_json))
            and (maximum_json is None or value <= json.loads(maximum_json)))
