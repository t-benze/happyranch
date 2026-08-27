"""Canonical SKILL.md authoring-contract tests (founder-approved, THR-169).

THR-210 PR 2 authoring grammar: a newly authored SKILL.md body is accepted
when it is either (a) heading-first — it starts at column zero with a
Markdown heading (H1/H2/any ATX level) followed by the body — or
(b) YAML-frontmatter-first — a valid opening `---` fence at column zero, a
YAML mapping, a closing `---` fence, then a Markdown body heading. Leading
BOM/whitespace before either opening shape is NOT tolerated (the documented
column-zero contract; no silent healing), matching the pre-PR-2 behavior
that a leading blank line or BOM keeps the document outside the accepted
grammar. Malformed YAML frontmatter, unclosed frontmatter, non-mapping
frontmatter, and frontmatter without a Markdown body heading remain invalid
and are classified under the stable reason codes below (THR-210 PR 1 keeps
such candidates as immutable validation/provenance evidence).
"""
from __future__ import annotations

import pytest

from runtime.skills.skill_md import (
    SKILL_MD_EMPTY,
    SKILL_MD_FRONTMATTER_NOT_MAPPING,
    SKILL_MD_MALFORMED_FRONTMATTER,
    SKILL_MD_NO_FRONTMATTER,
    SKILL_MD_NO_HEADING,
    SKILL_MD_UNCLOSED_FRONTMATTER,
    skill_md_contract_violations,
)

_VALID = "---\nname: Example\ndescription: demo\n---\n\n# Example\n\nBody.\n"


# ── positive coverage ───────────────────────────────────────────────────

def test_valid_frontmatter_first_body_has_no_violations():
    assert skill_md_contract_violations(_VALID) == []


def test_frontmatter_then_heading_then_body_is_valid():
    body = (
        "---\nname: qa-b2-verify\ndescription: temporary verification fixture\n"
        "---\n\n# QA B2 Verify\n\nVerification skill for B2 custom-skill cutover.\n"
    )
    assert skill_md_contract_violations(body) == []


def test_frontmatter_with_level_two_heading_is_valid():
    assert skill_md_contract_violations("---\nname: x\n---\n## Sub\n\nBody\n") == []


def test_frontmatter_with_extra_yaml_keys_is_valid():
    assert skill_md_contract_violations(
        "---\nname: x\ndescription: y\ntags:\n  - a\n  - b\n---\n\n# H\n\nBody\n"
    ) == []


# ── THR-210 PR 2: heading-first acceptance ──────────────────────────────

@pytest.mark.parametrize("skill_md", [
    "# Heading-first body\n\nBody text.\n",            # H1 + normal body
    "## Heading-first level two\n\nBody text.\n",      # H2 + normal body
    "### Heading-first level three\n\nBody text.\n",   # any ATX level
    "# Heading without trailing newline",              # heading-only body
    "# Heading\nBody starts immediately below\n",      # no blank line after heading
    "#\n",                                              # ATX boundary: 1 hash + EOL
    "###### Level-six heading\n\nBody text.\n",        # ATX boundary: 6 hashes + space
    "######\n",                                         # ATX boundary: 6 hashes + EOL
    "#\tTab-separated heading\n\nBody text.\n",        # ATX: whitespace after the hashes
])
def test_heading_first_body_with_markdown_heading_is_valid(skill_md):
    assert skill_md_contract_violations(skill_md) == []


# ── adversarial shape coverage ──────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    # empty / missing body
    ("", SKILL_MD_EMPTY),
    ("   \n\n", SKILL_MD_EMPTY),
    (123, SKILL_MD_EMPTY),  # non-string
    # no frontmatter AND no heading -> still outside the grammar
    ("plain text without frontmatter", SKILL_MD_NO_FRONTMATTER),
    ("This is not a heading\n", SKILL_MD_NO_FRONTMATTER),
    # hash-prefixed lines are NOT ATX headings unless 1-6 hashes are followed
    # by whitespace or end-of-line (CommonMark §4.2) — a leading '#' alone
    # does not make the line a heading
    ("#not-a-heading\n", SKILL_MD_NO_FRONTMATTER),
    ("##not-a-heading either\n", SKILL_MD_NO_FRONTMATTER),
    ("####### Seven hashes then a space\n", SKILL_MD_NO_FRONTMATTER),
    ("#######\n", SKILL_MD_NO_FRONTMATTER),             # 7 hashes + EOL
    ("######## Too many\n", SKILL_MD_NO_FRONTMATTER),
    # leading BOM/whitespace before a heading is NOT accepted: the accepted
    # opening shapes must start the document at column zero (documented
    # contract), so these are classified invalid without silent healing.
    ("\n# Leading blank line\n", SKILL_MD_NO_FRONTMATTER),
    ("\ufeff# BOM-prefixed heading\n", SKILL_MD_NO_FRONTMATTER),
    ("  # Indented heading\n", SKILL_MD_NO_FRONTMATTER),
    # unclosed frontmatter fence
    ("---\nname: x\n# no closing fence\n", SKILL_MD_UNCLOSED_FRONTMATTER),
    ("---\nname: x", SKILL_MD_UNCLOSED_FRONTMATTER),
    # malformed YAML inside the fence
    ("---\nname: [unclosed\n---\n# Heading\n", SKILL_MD_MALFORMED_FRONTMATTER),
    ("---\n: : : bad\n---\n# Heading\n", SKILL_MD_MALFORMED_FRONTMATTER),
    # non-mapping frontmatter
    ("---\n- a\n- b\n---\n# Heading\n", SKILL_MD_FRONTMATTER_NOT_MAPPING),
    ("---\njust a string\n---\n# Heading\n", SKILL_MD_FRONTMATTER_NOT_MAPPING),
    ("---\n42\n---\n# Heading\n", SKILL_MD_FRONTMATTER_NOT_MAPPING),
    ("---\n---\n# Heading\n", SKILL_MD_FRONTMATTER_NOT_MAPPING),  # empty frontmatter
    # missing post-frontmatter heading
    ("---\nname: x\n---\nplain text without a heading\n", SKILL_MD_NO_HEADING),
    ("---\nname: x\n---\n\n", SKILL_MD_NO_HEADING),  # empty body after fence
    ("---\nname: x\n---\n", SKILL_MD_NO_HEADING),
    ("---\nname: x\n---\n   \n", SKILL_MD_NO_HEADING),
    # the post-frontmatter body heading uses the IDENTICAL ATX boundary
    ("---\nname: x\n---\n#not-a-heading\n", SKILL_MD_NO_HEADING),
    ("---\nname: x\n---\n####### Seven hashes\n", SKILL_MD_NO_HEADING),
    ("---\nname: x\n---\n  # Indented body heading\n", SKILL_MD_NO_HEADING),
])
def test_contract_violations_reject_invalid_bodies(body, expected):
    codes = [code for code, _ in skill_md_contract_violations(body)]
    assert codes == [expected]


@pytest.mark.parametrize("skill_md", [
    "---\nname: x\n---\n#\n",                            # 1 hash + EOL
    "---\nname: x\n---\n###### Level-six heading\n\nBody\n",  # 6 hashes + space
    "---\nname: x\n---\n######\n",                       # 6 hashes + EOL
])
def test_frontmatter_body_heading_accepts_atx_boundary_forms(skill_md):
    """The required Markdown heading after YAML frontmatter obeys the same
    ATX boundary as heading-first: 1-6 '#' markers followed by whitespace
    or end-of-line, on the body's first non-blank line (blank lines between
    the closing fence and the heading remain tolerated)."""
    assert skill_md_contract_violations(skill_md) == []


def test_contract_violations_include_human_readable_message():
    codes = dict(skill_md_contract_violations("no frontmatter here"))
    assert SKILL_MD_NO_FRONTMATTER in codes
    assert codes[SKILL_MD_NO_FRONTMATTER]


def test_no_frontmatter_message_names_both_accepted_shapes():
    """The stable skill_md_no_frontmatter code must keep its deterministic
    message truthful under the THR-210 PR 2 grammar (heading-first is now
    accepted too). New invalid evidence rows store this text; legacy rows
    keep their older wording and are never rewritten."""
    codes = dict(skill_md_contract_violations("plain prose"))
    assert "frontmatter fence or a Markdown heading" in codes[SKILL_MD_NO_FRONTMATTER]
