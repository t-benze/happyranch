"""Build forwarded-context blocks for new threads."""
from __future__ import annotations

from src.models import ThreadMessage, ThreadMessageKind

_MAX_QUOTED_BYTES = 4096


def _truncate(s: str, *, limit: int = _MAX_QUOTED_BYTES) -> str:
    encoded = s.encode("utf-8")
    if len(encoded) <= limit:
        return s
    return encoded[:limit].decode("utf-8", errors="ignore") + "\n\n(... source truncated)"


def build_forward_body_from_talk(*, source_id: str, summary: str, agent_name: str) -> str:
    truncated = _truncate(summary).strip()
    body = (
        f"> **Forwarded from {source_id}** (talk with {agent_name})\n>\n"
        f"> {truncated.replace(chr(10), chr(10) + '> ')}\n\n"
        "---\n\n"
    )
    return body


def build_forward_body_from_thread(*, source_id: str, messages: list[ThreadMessage], subject: str) -> str:
    quoted_lines: list[str] = [
        f"> **Forwarded from {source_id}** (thread: {subject})",
        ">",
    ]
    rendered = []
    for m in messages:
        if m.kind is ThreadMessageKind.MESSAGE:
            rendered.append(f"> {m.speaker}: {(m.body_markdown or '').strip()}")
        elif m.kind is ThreadMessageKind.DECLINE:
            rendered.append(f"> ({m.speaker} declined: {m.decline_reason})")
        elif m.kind is ThreadMessageKind.SYSTEM:
            tag = (m.system_payload or {}).get("kind_tag", "system")
            rendered.append(f"> (system: {tag})")
    quoted = "\n".join(rendered)
    truncated = _truncate(quoted)
    return "\n".join(quoted_lines + [truncated, "", "---", ""])
