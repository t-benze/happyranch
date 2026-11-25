"""Pure, native-type validation for optional org-defined task attributes."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import math
import re
from typing import Any


class AttributeValidationError(ValueError):
    """All field errors from one submitted definition or value set."""

    def __init__(self, errors: dict[str, list[str]]) -> None:
        self.errors = errors
        super().__init__(str(errors))


def _number(value: Any) -> bool:
    return type(value) is int or (type(value) is float and math.isfinite(value))


def validate_definition(*, key: Any, type: Any, name: Any,
                        description: Any = None, constraints: Any = None) -> dict:
    errors: dict[str, list[str]] = {}
    if not isinstance(key, str) or re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key) is None:
        errors["key"] = ["invalid_key"]
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 80:
        errors["name"] = ["length_1_80_trimmed"]
    if description is not None and (not isinstance(description, str) or len(description) > 4000):
        errors["description"] = ["length_0_4000"]
    if not isinstance(type, str) or type not in ("numeric", "string", "boolean"):
        errors["type"] = ["invalid_type"]
    rules = {} if constraints is None else constraints
    if not isinstance(rules, Mapping):
        errors["constraints"] = ["expected_object"]
        rules = {}
    allowed = {"numeric": {"min", "max", "integer_only"},
               "string": {"min_length", "max_length"}, "boolean": set()}
    for field, value in rules.items():
        path = f"constraints.{field}"
        if field not in allowed.get(type if isinstance(type, str) else "", set()):
            errors[path] = ["unsupported_constraint"]
        elif field == "integer_only":
            if value.__class__ is not bool:
                errors[path] = ["expected_boolean"]
        elif field in ("min", "max"):
            if not _number(value):
                errors[path] = ["expected_finite_number"]
        elif value.__class__ is not int or not 0 <= value <= 4000:
            errors[path] = ["length_bound_0_4000"]
    if not any(k.startswith("constraints") for k in errors):
        low, high = ("min", "max") if type == "numeric" else ("min_length", "max_length")
        if low in rules and high in rules and rules[low] > rules[high]:
            errors["constraints"] = ["inverted_bounds"]
        elif type == "numeric" and rules.get("integer_only", False) and low in rules and high in rules:
            if math.ceil(rules[low]) > math.floor(rules[high]):
                errors["constraints"] = ["no_permitted_integer"]
    if errors:
        raise AttributeValidationError(errors)
    return {"key": key, "type": type, "name": name.strip(),
            "description": description, "constraints": dict(rules)}


def value_errors(definition: Mapping, value: Any) -> list[str]:
    """Return errors without coercing false-like values or caller strings."""
    kind, rules = definition["type"], definition["constraints"]
    if kind == "numeric":
        if not _number(value):
            return ["expected_finite_number"]
        errors = []
        if rules.get("integer_only", False) and isinstance(value, float) and not value.is_integer():
            errors.append("expected_integer")
        if "min" in rules and value < rules["min"]:
            errors.append("below_min")
        if "max" in rules and value > rules["max"]:
            errors.append("above_max")
        return errors
    if kind == "string":
        if not isinstance(value, str):
            return ["expected_string"]
        if len(value) < rules.get("min_length", 0):
            return ["below_min_length"]
        if len(value) > rules.get("max_length", 4000):
            return ["above_max_length"]
        return []
    return [] if value.__class__ is bool else ["expected_boolean"]


def validate_values(values: Mapping | Iterable[tuple[str, Any]], definitions: Iterable[Mapping]) -> list[dict]:
    """Validate a whole map, or pairs before duplicate keys can be collapsed.

    Definitions are a caller-owned snapshot. This function has no DB effects;
    writers must obtain and validate their authoritative snapshot in a transaction.
    """
    known = {d["key"]: d for d in definitions}
    errors: dict[str, list[str]] = {}
    result, seen = [], set()
    pairs = values.items() if isinstance(values, Mapping) else values
    if not isinstance(pairs, Iterable) or isinstance(pairs, (str, bytes)):
        raise AttributeValidationError({"attributes": ["expected_map_or_pairs"]})
    for pair in pairs:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2 or not isinstance(pair[0], str):
            errors.setdefault("attributes", []).append("expected_key_value_pair")
            continue
        key, value = pair
        failures = errors.setdefault(key, [])
        if key in seen:
            failures.append("duplicate_key")
        seen.add(key)
        definition = known.get(key)
        if definition is None:
            failures.append("unknown_key")
        elif definition.get("archived_at") is not None:
            failures.append("archived_key")
        else:
            failures.extend(value_errors(definition, value))
            result.append({"key": key, "definition_id": definition["definition_id"],
                           "definition_version": definition["version"], "value": value})
    errors = {key: failures for key, failures in errors.items() if failures}
    if errors:
        raise AttributeValidationError(errors)
    return result
