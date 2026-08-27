"""Canonical SKILL.md authoring-contract validation (founder-approved, THR-169).

The supported authoring contract (THR-210 PR 2 grammar): a newly authored
SKILL.md body is accepted when it is either (a) heading-first — its first
line at column zero is a Markdown ATX heading (1–6 ``#`` markers followed by
whitespace or end-of-line, CommonMark §4.2) — or (b) YAML-frontmatter-first
— a valid opening ``---`` fence at column zero, a YAML mapping, a closing
``---`` fence, then a Markdown body heading (the same ATX boundary). This
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

# A line opens a Markdown ATX heading (CommonMark §4.2) when it starts with
# 1–6 ``#`` markers followed by whitespace (space or tab) or end-of-line.
# ``#not-a-heading`` is therefore NOT a heading, and neither is a seven-or-more
# hash run; ``#``, ``######`` and ``###### text`` are.
_ATX_HEADING_OPENING_RE = re.compile(r"^#{1,6}(?:[ \t]|$)")


def _is_atx_heading_opening(line: str) -> bool:
    """True when ``line`` opens a Markdown ATX heading: 1–6 ``#`` markers
    followed by whitespace or end-of-line. The single canonical predicate for
    both accepted opening shapes (heading-first and post-frontmatter)."""
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


def skill_md_contract_violations(skill_md: object) -> list[tuple[str, str]]:
    """Return ``(reason_code, message)`` pairs for contract violations.

    Returns an empty list when the body satisfies the supported contract:
    either a column-zero Markdown ATX heading (heading-first; 1–6 ``#``
    markers followed by whitespace or end-of-line) or YAML frontmatter (valid
    opening ``---`` fence at column zero, a YAML mapping, a closing fence,
    then a Markdown body heading under the same ATX boundary).
    """
    if not isinstance(skill_md, str) or not skill_md.strip():
        return [(SKILL_MD_EMPTY, _MESSAGES[SKILL_MD_EMPTY])]
    if not skill_md.startswith(_OPEN_FENCE):
        # THR-210 PR 2: heading-first bodies whose FIRST line is a Markdown
        # ATX heading at column zero are accepted for new authoring. Leading
        # BOM/whitespace is not stripped — the heading must open the document
        # at column zero, mirroring the frontmatter fence rule (no silent
        # healing of documents that start with other content). A hash-prefixed
        # first line is only a heading when 1–6 ``#`` markers are followed by
        # whitespace or end-of-line (#not-a-heading and seven-or-more hashes
        # stay outside the grammar).
        if _is_atx_heading_opening(skill_md.split("\n", 1)[0]):
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
    # The required Markdown body heading uses the SAME ATX boundary as
    # heading-first. Blank lines between the closing fence and the heading
    # remain tolerated; the heading itself must be the first non-blank line
    # and open at column zero (1–6 hashes + whitespace/EOL).
    first_body_line = next((ln for ln in body.split("\n") if ln.strip()), None)
    if first_body_line is None or not _is_atx_heading_opening(first_body_line):
        return [(SKILL_MD_NO_HEADING, _MESSAGES[SKILL_MD_NO_HEADING])]
    return []
