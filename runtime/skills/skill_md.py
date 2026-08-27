"""Canonical SKILL.md authoring-contract validation (founder-approved, THR-169).

The supported authoring contract: a newly authored SKILL.md body is
YAML-frontmatter-first — a valid opening ``---`` fence, a YAML mapping, a
closing ``---`` fence, then a Markdown heading. This module is the single
authoritative shape check; every custom-skill write route (and any other
caller) reaches it through ``_validate_skill_package``.

Heading-first bodies are NOT accepted for new authoring. Pre-existing valid
legacy versions (validated heading-first under THR-055/1.0.0) remain
resolvable and materializable because their stored ``validation_state`` is
authoritative at the resolver/materialization seams — this module never
re-validates stored content and never broadens new-authoring acceptance.
"""

from __future__ import annotations

from typing import Sequence

import yaml

# Stable reason codes shared with the web reason-code mapper
# (web/src/features/skills/skills-validation.ts).
SKILL_MD_EMPTY = "skill_md_empty"
SKILL_MD_NO_FRONTMATTER = "skill_md_no_frontmatter"
SKILL_MD_UNCLOSED_FRONTMATTER = "skill_md_unclosed_frontmatter"
SKILL_MD_MALFORMED_FRONTMATTER = "skill_md_malformed_frontmatter"
SKILL_MD_FRONTMATTER_NOT_MAPPING = "skill_md_frontmatter_not_mapping"
SKILL_MD_NO_HEADING = "skill_md_no_heading"

_MESSAGES: dict[str, str] = {
    SKILL_MD_EMPTY: "SKILL.md content is empty or missing",
    SKILL_MD_NO_FRONTMATTER: "SKILL.md must start with a YAML frontmatter fence",
    SKILL_MD_UNCLOSED_FRONTMATTER: "SKILL.md YAML frontmatter is missing its closing fence",
    SKILL_MD_MALFORMED_FRONTMATTER: "SKILL.md YAML frontmatter is malformed",
    SKILL_MD_FRONTMATTER_NOT_MAPPING: "SKILL.md YAML frontmatter must be a mapping",
    SKILL_MD_NO_HEADING: "SKILL.md must start with a heading after the YAML frontmatter",
}

_OPEN_FENCE = "---\n"


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


def skill_md_contract_violations(skill_md: object) -> list[tuple[str, str]]:
    """Return ``(reason_code, message)`` pairs for contract violations.

    Returns an empty list when the body satisfies the supported contract.
    """
    if not isinstance(skill_md, str) or not skill_md.strip():
        return [(SKILL_MD_EMPTY, _MESSAGES[SKILL_MD_EMPTY])]
    if not skill_md.startswith(_OPEN_FENCE):
        return [(SKILL_MD_NO_FRONTMATTER, _MESSAGES[SKILL_MD_NO_FRONTMATTER])]
    split = _split_frontmatter(skill_md)
    if split is None:
        return [(SKILL_MD_UNCLOSED_FRONTMATTER, _MESSAGES[SKILL_MD_UNCLOSED_FRONTMATTER])]
    frontmatter_text, body = split
    try:
        parsed = yaml.safe_load(frontmatter_text)
    except yaml.YAMLError:
        return [(SKILL_MD_MALFORMED_FRONTMATTER, _MESSAGES[SKILL_MD_MALFORMED_FRONTMATTER])]
    if not isinstance(parsed, dict):
        return [(SKILL_MD_FRONTMATTER_NOT_MAPPING, _MESSAGES[SKILL_MD_FRONTMATTER_NOT_MAPPING])]
    if not body.strip() or not body.strip().startswith("#"):
        return [(SKILL_MD_NO_HEADING, _MESSAGES[SKILL_MD_NO_HEADING])]
    return []
