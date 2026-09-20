"""Canonical SKILL.md authoring-contract validation.

THR-262 / seq27 contract (supersedes the THR-210 PR-2 heading-first grammar for
NEW custom-skill writes):

* Frontmatter is required: a column-zero ``---`` fence, a YAML **mapping**, a
  closing ``---`` fence. The body-heading requirement is retired. A
  frontmatter-only document (empty body) is accepted; a wholly
  empty/whitespace document stays invalid (``skill_md_empty``).
* The **only** permitted top-level frontmatter keys are ``name``,
  ``description``, ``license``, ``compatibility`` and ``metadata``. Every other
  top-level key is admission-policy invalid **by presence**, independent of its
  value or type. This is a HappyRanch admission restriction, not a claim that
  the field is malformed in the standard.
* ``name`` must be a YAML string, 1-64 lower-case (Unicode) alphanumeric
  characters or hyphens, no leading/trailing hyphen, no ``--``, and must equal
  the logical workspace slug.
* ``description`` must be a non-empty (after strip) YAML string of at most 1024
  characters.
* Optional fields are validated only when present: ``license`` (string),
  ``compatibility`` (string, 1-500) and ``metadata`` (mapping with string keys
  and string values only).
* Duplicate top-level keys are detected by a duplicate-aware load and produce
  ``frontmatter_duplicate_key`` naming the first duplicated key; they are
  structural and short-circuit every later group.

Deterministic precedence (the first applicable finding in an earlier group is
the only finding of that group; groups 2-4 may coexist in order):

1. structural: empty -> no frontmatter -> unclosed -> malformed -> not mapping
   -> duplicate key
2. admission: one ``admission_field_not_allowed`` per disallowed top-level key,
   in document order
3. required: missing/invalid name, name/slug mismatch, missing/invalid
   description
4. optional: invalid license, compatibility, metadata

Historical reason codes and messages stay readable: ``skill_md_no_heading`` is
retained (some pre-THR-262 rows stored it) but is never emitted for new writes.
Stored ``validation_state`` remains authoritative at the resolver /
materialization seams -- this module never re-validates stored content, so
heading-first versions stored valid and legacy invalid evidence keep reading
exactly as persisted.
"""

from __future__ import annotations

import re
from typing import Sequence

import yaml

# Stable reason codes shared with the web reason-code mapper
# (web/src/features/skills/skills-validation.ts).
SKILL_MD_EMPTY = "skill_md_empty"
SKILL_MD_NO_FRONTMATTER = "skill_md_no_frontmatter"
SKILL_MD_UNCLOSED_FRONTMATTER = "skill_md_unclosed_frontmatter"
SKILL_MD_MALFORMED_FRONTMATTER = "skill_md_malformed_frontmatter"
SKILL_MD_FRONTMATTER_NOT_MAPPING = "skill_md_frontmatter_not_mapping"
# Retained for historical rows only; never emitted for new writes.
SKILL_MD_NO_HEADING = "skill_md_no_heading"

FRONTMATTER_DUPLICATE_KEY = "frontmatter_duplicate_key"
ADMISSION_FIELD_NOT_ALLOWED = "admission_field_not_allowed"
FRONTMATTER_MISSING_NAME = "frontmatter_missing_name"
FRONTMATTER_INVALID_NAME = "frontmatter_invalid_name"
FRONTMATTER_NAME_SLUG_MISMATCH = "frontmatter_name_slug_mismatch"
FRONTMATTER_MISSING_DESCRIPTION = "frontmatter_missing_description"
FRONTMATTER_INVALID_DESCRIPTION = "frontmatter_invalid_description"
FRONTMATTER_INVALID_LICENSE = "frontmatter_invalid_license"
FRONTMATTER_INVALID_COMPATIBILITY = "frontmatter_invalid_compatibility"
FRONTMATTER_INVALID_METADATA = "frontmatter_invalid_metadata"

#: The closed local admission allowlist for new custom-skill versions.
ALLOWED_FRONTMATTER_KEYS = frozenset(
    {"name", "description", "license", "compatibility", "metadata"}
)

_DESCRIPTION_MAX_LENGTH = 1024
_COMPATIBILITY_MAX_LENGTH = 500

_MESSAGES: dict[str, str] = {
    SKILL_MD_EMPTY: "SKILL.md content is empty or missing",
    SKILL_MD_NO_FRONTMATTER: (
        "SKILL.md must start with a YAML frontmatter '---' fence at column zero"
    ),
    SKILL_MD_UNCLOSED_FRONTMATTER: "SKILL.md YAML frontmatter is missing its closing fence",
    SKILL_MD_MALFORMED_FRONTMATTER: "SKILL.md YAML frontmatter is malformed",
    SKILL_MD_FRONTMATTER_NOT_MAPPING: "SKILL.md YAML frontmatter must be a mapping",
    SKILL_MD_NO_HEADING: "SKILL.md must start with a heading after the YAML frontmatter",
    FRONTMATTER_DUPLICATE_KEY: "SKILL.md frontmatter repeats the top-level key '{key}'",
    ADMISSION_FIELD_NOT_ALLOWED: (
        "HappyRanch accepts only name, description, license, compatibility and "
        "metadata in SKILL.md frontmatter; '{key}' is not permitted. Request "
        "tool permission through the jobs / manage-agent workflow."
    ),
    FRONTMATTER_MISSING_NAME: "SKILL.md frontmatter is missing the required 'name' field",
    FRONTMATTER_INVALID_NAME: (
        "SKILL.md frontmatter 'name' must be 1-64 lower-case letters, digits or "
        "hyphens, with no leading/trailing hyphen and no '--'"
    ),
    FRONTMATTER_NAME_SLUG_MISMATCH: (
        "SKILL.md frontmatter 'name' must equal the logical slug '{slug}'"
    ),
    FRONTMATTER_MISSING_DESCRIPTION: (
        "SKILL.md frontmatter is missing the required 'description' field"
    ),
    FRONTMATTER_INVALID_DESCRIPTION: (
        "SKILL.md frontmatter 'description' must be a non-empty string of at "
        "most 1024 characters"
    ),
    FRONTMATTER_INVALID_LICENSE: "SKILL.md frontmatter 'license' must be a string",
    FRONTMATTER_INVALID_COMPATIBILITY: (
        "SKILL.md frontmatter 'compatibility' must be a string of 1-500 characters"
    ),
    FRONTMATTER_INVALID_METADATA: (
        "SKILL.md frontmatter 'metadata' must be a mapping of string keys to "
        "string values"
    ),
}

_OPEN_FENCE = "---\n"

# A line opens a Markdown ATX heading (CommonMark §4.2) when it starts with
# 1-6 ``#`` markers followed by whitespace (space or tab) or end-of-line.
# Retained for historical readability only; the heading-first grammar is
# retired for new writes.
_ATX_HEADING_OPENING_RE = re.compile(r"^#{1,6}(?:[ \t]|$)")


def _is_atx_heading_opening(line: str) -> bool:
    """True when ``line`` opens a Markdown ATX heading (historical helper)."""
    return _ATX_HEADING_OPENING_RE.match(line) is not None


def _split_frontmatter(skill_md: str) -> tuple[str, str] | None:
    """Return ``(frontmatter_text, body)`` for a frontmatter-first document.

    Returns ``None`` when the opening fence is absent or the closing fence is
    missing (the caller distinguishes those cases via ``_OPEN_FENCE``).
    Fences must occupy their own line at column zero, matching the
    codebase-wide frontmatter convention (see runtime/orchestrator/agent_def.py).
    """
    lines = skill_md.split("\n")
    if not lines or lines[0] != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i] == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None


class _StrictLoader(yaml.SafeLoader):
    """SafeLoader that records the first duplicated top-level mapping key."""

    def __init__(self, stream) -> None:
        super().__init__(stream)
        self.top_level_duplicate: object | None = None
        self._mapping_depth = 0


def _strict_mapping(loader: _StrictLoader, node, deep: bool = False):
    if not isinstance(node, yaml.MappingNode):
        raise yaml.constructor.ConstructorError(
            None, None, "expected a mapping node", node.start_mark
        )
    is_root = loader._mapping_depth == 0
    loader._mapping_depth += 1
    try:
        loader.flatten_mapping(node)
        mapping: dict = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                hash(key)
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "found unhashable key",
                    key_node.start_mark,
                ) from exc
            if is_root and key in mapping and loader.top_level_duplicate is None:
                loader.top_level_duplicate = key
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping
    finally:
        loader._mapping_depth -= 1


_StrictLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_mapping
)


def _load_frontmatter(frontmatter_text: str) -> tuple[object, object | None, bool]:
    """Return ``(parsed, duplicate_top_level_key, malformed)``."""
    loader = _StrictLoader(frontmatter_text)
    try:
        parsed = loader.get_single_data()
    except yaml.YAMLError:
        return None, None, True
    finally:
        loader.dispose()
    return parsed, loader.top_level_duplicate, False


def parse_skill_frontmatter(skill_md: object) -> dict | None:
    """Return the parsed top-level frontmatter mapping, or ``None``.

    ``None`` covers every document this module classifies as structurally
    unusable (no frontmatter, unclosed/malformed/non-mapping, duplicate keys)
    and is never a second, more lenient validation authority.
    """
    if not isinstance(skill_md, str) or not skill_md.startswith(_OPEN_FENCE):
        return None
    split = _split_frontmatter(skill_md)
    if split is None:
        return None
    parsed, duplicate, malformed = _load_frontmatter(split[0])
    if malformed or duplicate is not None or not isinstance(parsed, dict):
        return None
    return parsed


def _structural_findings(
    skill_md: object,
) -> tuple[list[tuple[str, str]] | None, dict | None]:
    """Group 1 structural classification.

    Returns ``(None, None)`` when the document has no opening frontmatter fence
    at all (the caller decides whether that is a failure); otherwise a
    non-empty finding list OR ``([], parsed_mapping)``.
    """
    if not isinstance(skill_md, str) or not skill_md.strip():
        return [(SKILL_MD_EMPTY, _MESSAGES[SKILL_MD_EMPTY])], None
    if not skill_md.startswith(_OPEN_FENCE):
        return None, None
    split = _split_frontmatter(skill_md)
    if split is None:
        return [(SKILL_MD_UNCLOSED_FRONTMATTER, _MESSAGES[SKILL_MD_UNCLOSED_FRONTMATTER])], None
    parsed, duplicate, malformed = _load_frontmatter(split[0])
    if malformed:
        return [(SKILL_MD_MALFORMED_FRONTMATTER, _MESSAGES[SKILL_MD_MALFORMED_FRONTMATTER])], None
    if not isinstance(parsed, dict):
        return [
            (SKILL_MD_FRONTMATTER_NOT_MAPPING, _MESSAGES[SKILL_MD_FRONTMATTER_NOT_MAPPING])
        ], None
    if duplicate is not None:
        return [
            (
                FRONTMATTER_DUPLICATE_KEY,
                _MESSAGES[FRONTMATTER_DUPLICATE_KEY].format(key=duplicate),
            )
        ], None
    return [], parsed


def _admission_findings(parsed: dict) -> list[tuple[str, str]]:
    return [
        (
            ADMISSION_FIELD_NOT_ALLOWED,
            _MESSAGES[ADMISSION_FIELD_NOT_ALLOWED].format(key=key),
        )
        for key in parsed
        if key not in ALLOWED_FRONTMATTER_KEYS
    ]


def _valid_name_charset(name: str) -> bool:
    if not (1 <= len(name) <= 64):
        return False
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False
    for ch in name:
        if ch == "-" or ch.isdigit():
            continue
        if ch.isalpha() and ch.islower():
            continue
        return False
    return True


def _required_findings(
    parsed: dict, expected_slug: str | None
) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    if "name" not in parsed:
        findings.append((FRONTMATTER_MISSING_NAME, _MESSAGES[FRONTMATTER_MISSING_NAME]))
    else:
        name = parsed["name"]
        if not isinstance(name, str) or not _valid_name_charset(name):
            findings.append((FRONTMATTER_INVALID_NAME, _MESSAGES[FRONTMATTER_INVALID_NAME]))
        elif expected_slug is not None and name != expected_slug:
            findings.append(
                (
                    FRONTMATTER_NAME_SLUG_MISMATCH,
                    _MESSAGES[FRONTMATTER_NAME_SLUG_MISMATCH].format(slug=expected_slug),
                )
            )
    if "description" not in parsed:
        findings.append(
            (FRONTMATTER_MISSING_DESCRIPTION, _MESSAGES[FRONTMATTER_MISSING_DESCRIPTION])
        )
    else:
        description = parsed["description"]
        if (
            not isinstance(description, str)
            or not description.strip()
            or len(description) > _DESCRIPTION_MAX_LENGTH
        ):
            findings.append(
                (
                    FRONTMATTER_INVALID_DESCRIPTION,
                    _MESSAGES[FRONTMATTER_INVALID_DESCRIPTION],
                )
            )
    return findings


def _optional_findings(parsed: dict) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    if "license" in parsed and not isinstance(parsed["license"], str):
        findings.append((FRONTMATTER_INVALID_LICENSE, _MESSAGES[FRONTMATTER_INVALID_LICENSE]))
    if "compatibility" in parsed:
        compatibility = parsed["compatibility"]
        if (
            not isinstance(compatibility, str)
            or not (1 <= len(compatibility) <= _COMPATIBILITY_MAX_LENGTH)
        ):
            findings.append(
                (
                    FRONTMATTER_INVALID_COMPATIBILITY,
                    _MESSAGES[FRONTMATTER_INVALID_COMPATIBILITY],
                )
            )
    if "metadata" in parsed:
        metadata = parsed["metadata"]
        valid_metadata = isinstance(metadata, dict) and all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in metadata.items()
        )
        if not valid_metadata:
            findings.append(
                (FRONTMATTER_INVALID_METADATA, _MESSAGES[FRONTMATTER_INVALID_METADATA])
            )
    return findings


def frontmatter_admission_violations(skill_md: object) -> list[tuple[str, str]]:
    """Structural + admission findings (groups 1-2) only.

    Used by the bundled-source CI guard over release-owned sources: a document
    with no frontmatter at all has no top-level keys and passes this
    allowed-set-only check. Malformed frontmatter and duplicate top-level keys
    fail with structural precedence.
    """
    findings, parsed = _structural_findings(skill_md)
    if findings is None:
        return []
    if findings:
        return findings
    return _admission_findings(parsed)


def skill_md_contract_violations(
    skill_md: object, *, expected_slug: str | None = None
) -> list[tuple[str, str]]:
    """Return ``(reason_code, message)`` pairs for contract violations.

    Returns an empty list only for a frontmatter-first document that satisfies
    the closed allowlist, the required name/description rules and the optional
    field types. Heading-first documents are no longer accepted for new writes
    and return ``skill_md_no_frontmatter``.
    """
    findings, parsed = _structural_findings(skill_md)
    if findings is None:
        return [(SKILL_MD_NO_FRONTMATTER, _MESSAGES[SKILL_MD_NO_FRONTMATTER])]
    if findings:
        return findings
    violations = _admission_findings(parsed)
    violations.extend(_required_findings(parsed, expected_slug))
    violations.extend(_optional_findings(parsed))
    return violations
