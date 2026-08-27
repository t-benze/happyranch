"""Canonical SKILL.md authoring-contract validation (founder-approved, THR-169).

The supported authoring contract (THR-210 PR 2 grammar): a newly authored
SKILL.md body is accepted when it is either (a) heading-first — its first
line at column zero is a Markdown heading (``#``…, any ATX level) — or
(b) YAML-frontmatter-first — a valid opening ``---`` fence at column zero, a
YAML mapping, a closing ``---`` fence, then a Markdown body heading. This
module is the single authoritative shape check; every custom-skill write
route (and any other caller) reaches it through ``_validate_skill_package``.

Leading BOM/whitespace before either opening shape is NOT tolerated: the
accepted shapes must start the document at column zero (the codebase-wide
frontmatter convention), so a BOM-prefixed or leading-blank-line document is
classified invalid rather than silently healed. Malformed YAML frontmatter,
unclosed frontmatter, non-mapping frontmatter, and frontmatter without a
Markdown body heading remain invalid under the stable reason codes below.
Stored ``validation_state`` remains authoritative at the resolver/
materialization seams — this module never re-validates stored content, so
pre-PR-2 records (including heading-first versions stored valid, and
PR-1-era heading-first candidates stored as invalid evidence) keep reading
exactly as persisted, without rewriting or silent healing.
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
    SKILL_MD_NO_FRONTMATTER: "SKILL.md must start with a YAML frontmatter fence or a Markdown heading",
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

    Returns an empty list when the body satisfies the supported contract:
    either a column-zero Markdown heading (heading-first) or YAML
    frontmatter (valid opening ``---`` fence at column zero, a YAML mapping,
    a closing fence, then a Markdown body heading).
    """
    if not isinstance(skill_md, str) or not skill_md.strip():
        return [(SKILL_MD_EMPTY, _MESSAGES[SKILL_MD_EMPTY])]
    if not skill_md.startswith(_OPEN_FENCE):
        # THR-210 PR 2: heading-first bodies whose FIRST line is a Markdown
        # heading at column zero are accepted for new authoring. Leading
        # BOM/whitespace is not stripped — the heading must open the document
        # at column zero, mirroring the frontmatter fence rule (no silent
        # healing of documents that start with other content).
        if skill_md.startswith("#"):
            return []
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
