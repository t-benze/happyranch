"""Unit tests for reply_parser — pure functions, no I/O."""
from __future__ import annotations

import json

import pytest

from runtime.infrastructure.feishu.reply_parser import (
    ParseResult,
    extract_text_from_content,
    parse_reply,
)


def _text_envelope(text: str) -> str:
    return json.dumps({"text": text})


def _post_envelope(lines: list[str]) -> str:
    return json.dumps({
        "zh_cn": {
            "title": "",
            "content": [
                [{"tag": "text", "text": line}] for line in lines
            ],
        }
    })


def test_extract_from_text_message():
    out = extract_text_from_content("text", _text_envelope("hello"))
    assert out == "hello"


def test_extract_from_post_message():
    out = extract_text_from_content("post", _post_envelope(["line one", "line two"]))
    assert out == "line one\nline two"


def test_extract_from_unsupported_type_returns_none():
    assert extract_text_from_content("interactive", "{}") is None
    assert extract_text_from_content("image", "{}") is None


def test_parse_reply_approve_clean():
    result = parse_reply("APPROVE\ngo for it")
    assert result == ParseResult(decision="approve", rationale="go for it")


def test_parse_reply_reject_clean():
    result = parse_reply("REJECT\nnot now")
    assert result == ParseResult(decision="reject", rationale="not now")


def test_parse_reply_lowercase_accepted():
    result = parse_reply("approve\nok")
    assert result.decision == "approve"


def test_parse_reply_mixed_case_accepted():
    assert parse_reply("Approve\nok").decision == "approve"
    assert parse_reply("Reject\nno").decision == "reject"


def test_parse_reply_multiline_rationale():
    result = parse_reply("APPROVE\nline1\nline2\nline3")
    assert result.rationale == "line1\nline2\nline3"


def test_parse_reply_decision_only_uses_default_rationale():
    result = parse_reply("APPROVE")
    assert result.decision == "approve"
    assert result.rationale == "(no rationale provided)"


def test_parse_reply_first_word_invalid_returns_none():
    assert parse_reply("MAYBE\nnot sure") is None


def test_parse_reply_empty_returns_none():
    assert parse_reply("") is None
    assert parse_reply("   \n   ") is None


def test_parse_reply_leading_blank_lines_skipped():
    result = parse_reply("\n\nAPPROVE\nfine")
    assert result.decision == "approve"
    assert result.rationale == "fine"
