"""Build forwarded-context blocks for new threads."""
from __future__ import annotations

import re

from runtime.models import ThreadMessage, ThreadMessageKind

_MAX_QUOTED_BYTES = 4096


def _quote_source(source: str) -> str:
    return "\n".join("> " + line if line else ">" for line in re.split(r"\r\n|\r|\n", source))


def _truncate(s: str, *, limit: int = _MAX_QUOTED_BYTES) -> str:
    """Longest source prefix within the fully serialized UTF8 excerpt budget."""
    source = re.sub(r"\r\n|\r", "\n", s)
    quoted = _quote_source(source)
    if len(quoted.encode("utf-8")) <= limit:
        return quoted
    marker = "\n\n(... source truncated)"
    low, high = 0, len(source)
    while low < high:
        mid = (low + high + 1) // 2
        if len(_quote_source(source[:mid] + marker).encode("utf-8")) <= limit:
            low = mid
        else:
            high = mid - 1
    return _quote_source(source[:low] + marker)


def build_forward_body_from_thread(*, source_id: str, messages: list[ThreadMessage], subject: str) -> str:
    quoted_lines: list[str] = [
        f"**Forwarded from {source_id}** (thread: {subject})",
        "",
    ]
    rendered = []
    for m in messages:
        if m.kind is ThreadMessageKind.MESSAGE:
            rendered.append(f"{m.speaker}: {m.body_markdown or ''}")
        elif m.kind is ThreadMessageKind.DECLINE:
            rendered.append(f"({m.speaker} declined: {m.decline_reason})")
        elif m.kind is ThreadMessageKind.SYSTEM:
            payload = m.system_payload or {}
            tag = payload.get("kind_tag", "system")
            if tag == "task_completed":
                tid = payload.get("task_id")
                orig = payload.get("original_task_id")
                label = f"Task {tid} completed" + (
                    f" (chain root {orig})" if orig and orig != tid else ""
                )
                summary = (payload.get("final_output_summary") or "").strip()
                if summary:
                    label += f": {summary[:240]}"
                rendered.append(f"(system: {label})")
            elif tag == "task_failed":
                tid = payload.get("task_id")
                orig = payload.get("original_task_id")
                label = f"Task {tid} failed" + (
                    f" (chain root {orig})" if orig and orig != tid else ""
                )
                annotations: list[str] = []
                if payload.get("cancelled"):
                    annotations.append("founder-cancelled")
                revisit_task_id = payload.get("revisit_task_id")
                if revisit_task_id:
                    annotations.append(f"revisiting as {revisit_task_id}")
                else:
                    chain_len = payload.get("revisit_chain_length", 1)
                    if chain_len and chain_len > 1:
                        annotations.append("no further revisits")
                if annotations:
                    label += "; " + "; ".join(annotations)
                rendered.append(f"(system: {label})")
            elif tag == "task_escalated":
                tid = payload.get("task_id")
                orig = payload.get("original_task_id")
                label = f"Task {tid} escalated" + (
                    f" (chain root {orig})" if orig and orig != tid else ""
                )
                reason = (payload.get("reason") or "").strip()
                if reason:
                    label += f": {reason[:240]}"
                rendered.append(f"(system: {label})")
            else:
                rendered.append(f"(system: {tag})")
    source = "\n".join(quoted_lines + rendered)
    return _truncate(source) + "\n\n---\n\n"
