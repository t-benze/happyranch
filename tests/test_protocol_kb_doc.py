from __future__ import annotations

from pathlib import Path

DOC = Path(__file__).resolve().parent.parent / "protocol" / "06-knowledge-base.md"


def test_kb_guideline_doc_exists():
    assert DOC.exists(), f"missing {DOC}"


def test_kb_guideline_doc_covers_required_topics():
    body = DOC.read_text().lower()
    for token in (
        "what belongs",
        "what does not belong",
        "frontmatter",
        "collision",
        "update",
        "--force-new-sibling",
        "delete",
        "irreversible",
        "supersedes",
    ):
        assert token in body, f"missing section/token: {token!r}"
