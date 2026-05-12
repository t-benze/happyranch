"""Pure-function helpers to extract a decision from a Feishu inbound message.

The text content of a Feishu message lives inside a JSON envelope that varies
by `msg_type`. We support `text` and `post` envelopes; everything else is
considered unsupported and yields no text.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ParseResult:
    decision: Literal["approve", "reject", "revisit"]
    rationale: str


_NO_RATIONALE = "(no rationale provided)"


def extract_text_from_content(msg_type: str, content_json: str) -> str | None:
    """Convert a Feishu message envelope to plain text. Returns None for
    unsupported msg_types (image, file, interactive, ...)."""
    try:
        envelope = json.loads(content_json)
    except (TypeError, ValueError):
        return None

    if msg_type == "text":
        text = envelope.get("text")
        return text if isinstance(text, str) else None

    if msg_type == "post":
        # Feishu post envelope: {"zh_cn": {"title": "...", "content": [[seg, ...], ...]}}
        # Pick whichever locale block exists; usually zh_cn for our org.
        for locale_block in envelope.values():
            if not isinstance(locale_block, dict):
                continue
            content = locale_block.get("content")
            if not isinstance(content, list):
                continue
            lines: list[str] = []
            for line in content:
                if not isinstance(line, list):
                    continue
                segs = []
                for seg in line:
                    if isinstance(seg, dict) and seg.get("tag") == "text":
                        segs.append(seg.get("text", ""))
                lines.append("".join(segs))
            return "\n".join(lines)
        return None

    return None


def parse_reply(text: str) -> ParseResult | None:
    """Parse the founder's reply text into a decision + rationale.

    First non-empty line must be APPROVE or REJECT (case-insensitive).
    Subsequent lines (joined with \\n) form the rationale; empty rationale
    defaults to a placeholder so the resolve_escalation route accepts it.
    """
    if not text or not text.strip():
        return None

    lines = text.split("\n")
    first_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip():
            first_idx = idx
            break
    if first_idx is None:
        return None

    decision_word = lines[first_idx].strip().upper()
    if decision_word == "APPROVE":
        decision: Literal["approve", "reject", "revisit"] = "approve"
    elif decision_word == "REJECT":
        decision = "reject"
    elif decision_word == "REVISIT":
        decision = "revisit"
    else:
        return None

    rationale = "\n".join(lines[first_idx + 1:]).strip()
    if not rationale:
        rationale = _NO_RATIONALE
    return ParseResult(decision=decision, rationale=rationale)
