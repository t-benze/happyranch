"""Disabled release catalog: fixed package bytes, no workflow store or effects."""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
from importlib import resources
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


CODING_DEFINITION_DIGEST = "6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8"
_RESOURCE_LIMIT = 16384
_DEFINITION_SIZE = 634
_EXPECTED_DEFINITION = {
    "format": "workflow-coding-definition@1",
    "identity": "release/happyranch/coding",
    "version": 1,
    "artifact_kind": "code-change",
    "roles": {"implementer": "agent", "code_reviewer": "agent",
              "qa_engineer": "agent", "agent_manager": "agent"},
    "stages": ["development", "code_review", "qa", "manager_closeout"],
    "revision_policy": {"max_revisions": 3, "invalidate": "all-dependent-approvals",
                        "return_to": "development"},
    "replacement_policy": {"same_principal_retries": 1, "uncertain": "hold",
                           "selector": "bound-agent-manager"},
    "closeout": {"checks": ["Source Boundary Lint", "Web Lint (Node 24)"],
                 "ci_conclusion": "success", "merge": "guarded-expected-head", "required": True},
}


class ReleaseTemplateError(ValueError):
    """Categorical catalog refusal; never expose a resource path or raw cause."""

    def __init__(self, code: Literal["resource_unavailable", "resource_corrupt"]) -> None:
        super().__init__(code)
        self.code = code


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError("nonfinite JSON")


def _exact_value(value: object, expected: object) -> bool:
    """Closed keys, list order/bounds and native scalar types, including bool/int."""
    if type(value) is not type(expected):
        return False
    if isinstance(expected, dict):
        return value.keys() == expected.keys() and all(
            _exact_value(value[key], item) for key, item in expected.items()
        )
    if isinstance(expected, list):
        return len(value) == len(expected) and all(
            _exact_value(left, right) for left, right in zip(value, expected)
        )
    return value == expected


def validate_coding_release(raw: bytes) -> bytes:
    """Admit only the canonical v1 preimage; caller data cannot select policy."""
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= _RESOURCE_LIMIT:
            raise ValueError("resource bounds")
        definition = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_object,
                                parse_constant=_reject_constant)
        if not _exact_value(definition, _EXPECTED_DEFINITION):
            raise ValueError("definition shape")
        canonical = json.dumps(definition, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=False, allow_nan=False).encode("utf-8")
        if (raw != canonical or len(raw) != _DEFINITION_SIZE
                or hashlib.sha256(raw).hexdigest() != CODING_DEFINITION_DIGEST):
            raise ValueError("definition pin")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise ReleaseTemplateError("resource_corrupt") from exc
    return raw


def read_coding_release() -> bytes:
    """Use one constant package resource in source, wheel and supported frozen loaders."""
    try:
        resource = resources.files("runtime").joinpath("workflows", "builtin_templates", "coding.v1.json")
        with resource.open("rb") as source:
            raw = source.read(_RESOURCE_LIMIT)
    except (OSError, ImportError, NotImplementedError, TypeError, ValueError) as exc:
        # Unsupported loaders hold the catalog; there is no filesystem fallback.
        raise ReleaseTemplateError("resource_unavailable") from exc
    return validate_coding_release(raw)


def coding_release_projection(raw: bytes) -> dict[str, object]:
    """Fixed receipt without imports, allocation or mutable readiness lookup."""
    raw = validate_coding_release(raw)
    return {
        "format": "workflow-release-template@1",
        "identity": "release/happyranch/coding",
        "version": 1,
        "definition_bytes_base64": base64.b64encode(raw).decode("ascii"),
        "definition_digest": CODING_DEFINITION_DIGEST,
        "compiler_pin": "workflow-coding-compiler@1",
        "validator_pin": "workflow-coding-validator@1",
        "source_pin": "release-input@1",
        "readiness": {"enabled": False, "reason": "release_catalog_only"},
    }


class ReleaseTemplateReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    enabled: Literal[False]
    reason: Literal["release_catalog_only"]

    @model_validator(mode="before")
    @classmethod
    def require_boolean(cls, value: object) -> object:
        if isinstance(value, dict) and type(value.get("enabled")) is not bool:
            raise ValueError("readiness requires a Boolean")
        return value


class ReleaseTemplateReceipt(BaseModel):
    """Shared closed served/CLI contract, including exact canonical byte validation."""
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    format: Literal["workflow-release-template@1"]
    identity: Literal["release/happyranch/coding"]
    version: Literal[1]
    definition_bytes_base64: str = Field(min_length=848, max_length=848)
    definition_digest: Literal["6ee02be98738d3c95fe584b165abe07d0dbef66e510133c117ee0d62c953cad8"]
    compiler_pin: Literal["workflow-coding-compiler@1"]
    validator_pin: Literal["workflow-coding-validator@1"]
    source_pin: Literal["release-input@1"]
    readiness: ReleaseTemplateReadiness

    @model_validator(mode="before")
    @classmethod
    def require_integer_version(cls, value: object) -> object:
        if isinstance(value, dict) and type(value.get("version")) is not int:
            raise ValueError("version requires an integer")
        return value

    @model_validator(mode="after")
    def validate_definition(self) -> Self:
        try:
            raw = base64.b64decode(self.definition_bytes_base64, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("invalid release bytes") from exc
        if self.model_dump() != coding_release_projection(raw):
            raise ValueError("release receipt mismatch")
        return self
