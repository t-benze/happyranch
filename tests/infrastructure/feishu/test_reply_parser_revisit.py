from __future__ import annotations

from runtime.infrastructure.feishu.reply_parser import parse_reply


def test_revisit_verb_uppercase():
    out = parse_reply("REVISIT\nadd the missing field")
    assert out is not None
    assert out.decision == "revisit"
    assert out.rationale == "add the missing field"


def test_revisit_verb_lowercase():
    out = parse_reply("revisit\nretry this")
    assert out is not None
    assert out.decision == "revisit"
    assert out.rationale == "retry this"


def test_revisit_with_multiline_body():
    out = parse_reply("REVISIT\nline one\nline two\n\nline three")
    assert out is not None
    assert out.decision == "revisit"
    assert out.rationale == "line one\nline two\n\nline three"


def test_revisit_without_body_defaults_rationale():
    out = parse_reply("REVISIT\n")
    assert out is not None
    assert out.decision == "revisit"
    # Match existing APPROVE/REJECT default-rationale behavior
    assert out.rationale == "(no rationale provided)"


def test_existing_approve_still_works():
    out = parse_reply("APPROVE\nrationale here")
    assert out is not None
    assert out.decision == "approve"
    assert out.rationale == "rationale here"
