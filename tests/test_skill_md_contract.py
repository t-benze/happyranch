"""Canonical SKILL.md authoring-contract tests (THR-262 / seq27).

The THR-210 PR-2 heading-first grammar is retired for NEW custom-skill writes.
A conforming document is frontmatter-first: a column-zero ``---`` fence, a YAML
mapping, a closing fence.  The only permitted top-level keys are
``name``/``description``/``license``/``compatibility``/``metadata``; every other
key is admission-policy invalid by presence.  ``name`` must equal the logical
slug; ``description`` must be non-empty.

The literal inputs and ordered findings below mirror case-design §4.3 row by
row.  Historical reason codes stay readable (``skill_md_no_heading``).
"""
from __future__ import annotations

import pytest

from runtime.skills.skill_md import (
    ADMISSION_FIELD_NOT_ALLOWED,
    FRONTMATTER_DUPLICATE_KEY,
    FRONTMATTER_INVALID_COMPATIBILITY,
    FRONTMATTER_INVALID_DESCRIPTION,
    FRONTMATTER_INVALID_LICENSE,
    FRONTMATTER_INVALID_METADATA,
    FRONTMATTER_INVALID_NAME,
    FRONTMATTER_MISSING_DESCRIPTION,
    FRONTMATTER_MISSING_NAME,
    FRONTMATTER_NAME_SLUG_MISMATCH,
    SKILL_MD_EMPTY,
    SKILL_MD_FRONTMATTER_NOT_MAPPING,
    SKILL_MD_MALFORMED_FRONTMATTER,
    SKILL_MD_NO_FRONTMATTER,
    SKILL_MD_NO_HEADING,
    SKILL_MD_UNCLOSED_FRONTMATTER,
    frontmatter_admission_violations,
    parse_skill_frontmatter,
    skill_md_contract_violations,
)

_BASELINE = "---\nname: my-workflow\ndescription: d\n---\n"


def _codes(skill_md: object, slug: str = "my-workflow") -> list[str]:
    return [code for code, _ in skill_md_contract_violations(skill_md, expected_slug=slug)]


# ── literal §4.3 matrix ─────────────────────────────────────────────────

@pytest.mark.parametrize("skill_md,expected", [
    # 1 baseline
    (_BASELINE, []),
    # 2 baseline without description
    ("---\nname: my-workflow\n---\n", [FRONTMATTER_MISSING_DESCRIPTION]),
    # 3 complete doc with description only
    ("---\ndescription: d\n---\n", [FRONTMATTER_MISSING_NAME]),
    # 4 no opening fence (heading-first is retired for new writes)
    ("# Heading-first body\n\nBody text.\n", [SKILL_MD_NO_FRONTMATTER]),
    ("plain text without frontmatter", [SKILL_MD_NO_FRONTMATTER]),
    # 5 opening fence, no closing fence
    ("---\nname: my-workflow\n", [SKILL_MD_UNCLOSED_FRONTMATTER]),
    # 6 malformed YAML
    ("---\nname: [a\ndescription: d\n---\n", [SKILL_MD_MALFORMED_FRONTMATTER]),
    # 7 malformed document that merely spells a disallowed key
    ("---\nname: my-workflow\ndescription: d\nallowed-tools: [Bash\n---\n",
     [SKILL_MD_MALFORMED_FRONTMATTER]),
    # 8 hooks: null
    ("---\nname: my-workflow\ndescription: d\nhooks: null\n---\n",
     [ADMISSION_FIELD_NOT_ALLOWED]),
    # 9 allowed-tools: []
    ("---\nname: my-workflow\ndescription: d\nallowed-tools: []\n---\n",
     [ADMISSION_FIELD_NOT_ALLOWED]),
    # 11 vendor key
    ("---\nname: my-workflow\ndescription: d\nvendor-x: 1\n---\n",
     [ADMISSION_FIELD_NOT_ALLOWED]),
    # 12 duplicate name
    ("---\nname: my-workflow\ndescription: d\nname: b\n---\n",
     [FRONTMATTER_DUPLICATE_KEY]),
    # 13 duplicate excluded key whose last value is empty
    ("---\nname: my-workflow\ndescription: d\nhooks: {}\nhooks: {}\n---\n",
     [FRONTMATTER_DUPLICATE_KEY]),
    # 15/16 YAML non-string name
    ("---\nname: 123\ndescription: d\n---\n", [FRONTMATTER_INVALID_NAME]),
    ("---\nname: true\ndescription: d\n---\n", [FRONTMATTER_INVALID_NAME]),
    # 18 uppercase name
    ("---\nname: My-Workflow\ndescription: d\n---\n", [FRONTMATTER_INVALID_NAME]),
    # 19 hyphen boundary
    ("---\nname: -a\ndescription: d\n---\n", [FRONTMATTER_INVALID_NAME]),
    ("---\nname: a-\ndescription: d\n---\n", [FRONTMATTER_INVALID_NAME]),
    ("---\nname: a--b\ndescription: d\n---\n", [FRONTMATTER_INVALID_NAME]),
    # 20 name/slug mismatch
    ("---\nname: other-workflow\ndescription: d\n---\n", [FRONTMATTER_NAME_SLUG_MISMATCH]),
    # 21-25 description type/length
    ("---\nname: my-workflow\ndescription: null\n---\n", [FRONTMATTER_INVALID_DESCRIPTION]),
    ("---\nname: my-workflow\ndescription: 5\n---\n", [FRONTMATTER_INVALID_DESCRIPTION]),
    ("---\nname: my-workflow\ndescription: \"\"\n---\n", [FRONTMATTER_INVALID_DESCRIPTION]),
    ("---\nname: my-workflow\ndescription: \"   \"\n---\n", [FRONTMATTER_INVALID_DESCRIPTION]),
    ("---\nname: my-workflow\ndescription: " + "x" * 1025 + "\n---\n",
     [FRONTMATTER_INVALID_DESCRIPTION]),
    # 26-29 optional scalar typing
    ("---\nname: my-workflow\ndescription: d\nlicense: 1\n---\n",
     [FRONTMATTER_INVALID_LICENSE]),
    ("---\nname: my-workflow\ndescription: d\ncompatibility: 0\n---\n",
     [FRONTMATTER_INVALID_COMPATIBILITY]),
    ("---\nname: my-workflow\ndescription: d\ncompatibility: \"\"\n---\n",
     [FRONTMATTER_INVALID_COMPATIBILITY]),
    ("---\nname: my-workflow\ndescription: d\ncompatibility: " + "x" * 501 + "\n---\n",
     [FRONTMATTER_INVALID_COMPATIBILITY]),
    # 30-35 metadata typing
    ("---\nname: my-workflow\ndescription: d\nmetadata: {a: 1}\n---\n",
     [FRONTMATTER_INVALID_METADATA]),
    ("---\nname: my-workflow\ndescription: d\nmetadata: {1: x}\n---\n",
     [FRONTMATTER_INVALID_METADATA]),
    ("---\nname: my-workflow\ndescription: d\nmetadata: {a: true}\n---\n",
     [FRONTMATTER_INVALID_METADATA]),
    ("---\nname: my-workflow\ndescription: d\nmetadata: {a: null}\n---\n",
     [FRONTMATTER_INVALID_METADATA]),
    ("---\nname: my-workflow\ndescription: d\nmetadata: {true: x}\n---\n",
     [FRONTMATTER_INVALID_METADATA]),
    ("---\nname: my-workflow\ndescription: d\nmetadata: {a: [x]}\n---\n",
     [FRONTMATTER_INVALID_METADATA]),
    # 37/38 standard-recognized and native-mechanism keys are admission-policy
    ("---\nname: my-workflow\ndescription: d\nallowed-tools: Bash(git status *)\n---\n",
     [ADMISSION_FIELD_NOT_ALLOWED]),
    ("---\nname: my-workflow\ndescription: d\nhooks: PreToolUse\n---\n",
     [ADMISSION_FIELD_NOT_ALLOWED]),
])
def test_contract_matrix_literal_findings(skill_md, expected):
    assert _codes(skill_md) == expected
    # A candidate is invalid if any finding exists.
    assert bool(expected) is (skill_md_contract_violations(skill_md, expected_slug="my-workflow") != [])


@pytest.mark.parametrize("skill_md", [
    # 1 baseline
    _BASELINE,
    # 10 metadata string-to-string with multiple keys
    "---\nname: my-workflow\ndescription: d\nmetadata: {a: \"b\", b: \"c\"}\n---\n",
    # 36 valid optional fields together
    "---\nname: my-workflow\ndescription: d\nlicense: MIT\ncompatibility: >-\n  Requires a POSIX shell\nmetadata: {owner: platform}\n---\n",
    # 39/40 prose/string metadata mentions are not top-level declarations
    "---\nname: my-workflow\ndescription: \"mentions allowed-tools and hooks\"\n---\n",
    "---\nname: my-workflow\ndescription: d\nmetadata: {note: \"allowed-tools\"}\n---\n",
    # frontmatter-only document (empty body) is accepted
    "---\nname: my-workflow\ndescription: d\n---\n",
])
def test_contract_accepts_conforming_documents(skill_md):
    assert _codes(skill_md) == []


def test_contract_matrix_slug_aware_rows():
    assert _codes("---\nname: \"123\"\ndescription: d\n---\n", slug="123") == []
    assert _codes("---\nname: café-workflow\ndescription: d\n---\n", slug="café-workflow") == []
    assert _codes("---\nname: My-Workflow\ndescription: d\n---\n", slug="My-Workflow") == [
        FRONTMATTER_INVALID_NAME
    ]
    assert _codes("---\nname: other-workflow\ndescription: d\n---\n", slug="my-workflow") == [
        FRONTMATTER_NAME_SLUG_MISMATCH
    ]


# ── admission ordering + grouping ───────────────────────────────────────

def test_admission_findings_follow_document_order_and_precede_required():
    doc = (
        "---\nname: my-workflow\ndescription: d\n"
        "allowed-tools: null\nhooks: false\nvendor-x: 1\nfuture-field: \"\"\n---\n"
    )
    codes = _codes(doc)
    assert codes == [ADMISSION_FIELD_NOT_ALLOWED] * 4
    messages = [message for _, message in skill_md_contract_violations(doc, expected_slug="my-workflow")]
    assert "allowed-tools" in messages[0]
    assert "hooks" in messages[1]
    assert "vendor-x" in messages[2]
    assert "future-field" in messages[3]


def test_admission_precedes_required_and_optional_findings():
    missing = "---\nname: my-workflow\nallowed-tools: []\n---\n"
    assert _codes(missing) == [ADMISSION_FIELD_NOT_ALLOWED, FRONTMATTER_MISSING_DESCRIPTION]
    optional = (
        "---\nname: my-workflow\ndescription: d\nallowed-tools: []\nlicense: 1\n---\n"
    )
    assert _codes(optional) == [ADMISSION_FIELD_NOT_ALLOWED, FRONTMATTER_INVALID_LICENSE]


def test_structural_findings_short_circuit_field_groups():
    # Duplicate key cannot fall through to admission/required findings.
    assert _codes("---\nhooks: {}\nhooks: {}\n---\n") == [FRONTMATTER_DUPLICATE_KEY]
    # Non-mapping frontmatter is structural only.
    assert _codes("---\n- a\n- b\n---\n") == [SKILL_MD_FRONTMATTER_NOT_MAPPING]
    assert _codes("---\n---\n") == [SKILL_MD_FRONTMATTER_NOT_MAPPING]
    assert _codes("") == [SKILL_MD_EMPTY]
    assert _codes("   \n\n") == [SKILL_MD_EMPTY]
    assert _codes(123) == [SKILL_MD_EMPTY]


def test_duplicate_key_message_names_first_duplicated_key():
    messages = dict(skill_md_contract_violations(
        "---\nname: my-workflow\ndescription: d\nhooks: {}\nhooks: {}\n---\n",
        expected_slug="my-workflow",
    ))
    assert "hooks" in messages[FRONTMATTER_DUPLICATE_KEY]


def test_exact_boundaries_are_accepted():
    assert _codes("---\nname: " + "a" * 64 + "\ndescription: d\n---\n", slug="a" * 64) == []
    assert _codes("---\nname: my-workflow\ndescription: " + "x" * 1024 + "\n---\n") == []
    assert _codes(
        "---\nname: my-workflow\ndescription: d\ncompatibility: " + "x" * 500 + "\n---\n"
    ) == []
    assert _codes(
        "---\nname: my-workflow\ndescription: d\nmetadata: {}\n---\n"
    ) == []


def test_sixty_five_character_name_is_invalid():
    assert _codes("---\nname: " + "a" * 65 + "\ndescription: d\n---\n", slug="a" * 65) == [
        FRONTMATTER_INVALID_NAME
    ]


# ── parsed channel + bundled admission-only guard ───────────────────────

def test_parse_skill_frontmatter_returns_mapping_only_for_structurally_valid():
    assert parse_skill_frontmatter(_BASELINE) == {"name": "my-workflow", "description": "d"}
    assert parse_skill_frontmatter("# heading first\n") is None
    assert parse_skill_frontmatter("---\nname: [a\n---\n") is None
    assert parse_skill_frontmatter("---\nname: a\nname: b\n---\n") is None
    assert parse_skill_frontmatter("---\n- a\n---\n") is None


def test_frontmatter_admission_violations_passes_absent_frontmatter():
    # The bundled-source CI guard checks the allowed key set only: a heading /
    # no-frontmatter source has no top-level keys and passes.
    assert frontmatter_admission_violations("# Heading-first body\n\nBody\n") == []
    assert frontmatter_admission_violations("plain prose") == []
    assert frontmatter_admission_violations("---\nname: x\ndescription: y\n---\n") == []
    assert [
        code for code, _ in frontmatter_admission_violations(
            "---\nname: x\ndescription: y\nallowed-tools: []\n---\n"
        )
    ] == [ADMISSION_FIELD_NOT_ALLOWED]
    assert [
        code for code, _ in frontmatter_admission_violations("---\nname: x\nname: y\n---\n")
    ] == [FRONTMATTER_DUPLICATE_KEY]
    assert [
        code for code, _ in frontmatter_admission_violations("---\nname: [a\n---\n")
    ] == [SKILL_MD_MALFORMED_FRONTMATTER]


def test_historical_reason_code_stays_readable():
    """``skill_md_no_heading`` is retained for historical rows and never emitted
    for new writes; the retired heading-first shape now reports no frontmatter."""
    assert SKILL_MD_NO_HEADING == "skill_md_no_heading"
    assert _codes("---\nname: x\n---\nplain text without a heading\n") == [
        FRONTMATTER_NAME_SLUG_MISMATCH,
        FRONTMATTER_MISSING_DESCRIPTION,
    ]
