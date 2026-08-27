"""Canonical SKILL.md authoring-contract tests (founder-approved, THR-169).

The supported authoring contract is YAML-frontmatter-first: a valid opening
`---` fence, a YAML mapping, a closing `---` fence, then a Markdown heading.
Heading-first bodies are NOT accepted for new authoring — they remain valid
only as pre-existing legacy versions whose stored validation_state is
authoritative at the resolver/materialization seams.
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


# ── positive frontmatter-first coverage ─────────────────────────────────

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


# ── adversarial shape coverage ──────────────────────────────────────────

@pytest.mark.parametrize("body,expected", [
    # empty / missing body
    ("", SKILL_MD_EMPTY),
    ("   \n\n", SKILL_MD_EMPTY),
    (123, SKILL_MD_EMPTY),  # non-string
    # heading-first legacy shape is NOT accepted for new authoring
    ("# Heading-first body\n", SKILL_MD_NO_FRONTMATTER),
    ("plain text without frontmatter", SKILL_MD_NO_FRONTMATTER),
    ("\n# Leading blank line\n", SKILL_MD_NO_FRONTMATTER),
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
])
def test_contract_violations_reject_invalid_bodies(body, expected):
    codes = [code for code, _ in skill_md_contract_violations(body)]
    assert codes == [expected]


def test_contract_violations_include_human_readable_message():
    codes = dict(skill_md_contract_violations("no frontmatter here"))
    assert SKILL_MD_NO_FRONTMATTER in codes
    assert codes[SKILL_MD_NO_FRONTMATTER]
