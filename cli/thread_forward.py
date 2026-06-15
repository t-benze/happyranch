"""Build forwarded-context blocks for new threads."""
from __future__ import annotations

from runtime.models import ThreadMessage, ThreadMessageKind

_MAX_QUOTED_BYTES = 4096


def _truncate(s: str, *, limit: int = _MAX_QUOTED_BYTES) -> str:
    encoded = s.encode("utf-8")
    if len(encoded) <= limit:
        return s
    return encoded[:limit].decode("utf-8", errors="ignore") + "\n\n(... source truncated)"


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
                rendered.append(f"> (system: {label})")
            elif tag == "task_failed":
                tid = payload.get("task_id")
                orig = payload.get("original_task_id")
                label = f"Task {tid} failed" + (
                    f" (chain root {orig})" if orig and orig != tid else ""
                )
                annotations: list[str] = []
                if payload.get("cancelled"):
                    annotations.append("founder-cancelled")
                chain_len = payload.get("revisit_chain_length", 1)
                if chain_len and chain_len > 1:
                    n = chain_len - 1
                    annotations.append(f"after {n} {'revisit' if n == 1 else 'revisits'}")
                if annotations:
                    label += "; " + "; ".join(annotations)
                rendered.append(f"> (system: {label})")
            elif tag == "task_escalated":
                tid = payload.get("task_id")
                orig = payload.get("original_task_id")
                label = f"Task {tid} escalated" + (
                    f" (chain root {orig})" if orig and orig != tid else ""
                )
                reason = (payload.get("reason") or "").strip()
                if reason:
                    label += f": {reason[:240]}"
                rendered.append(f"> (system: {label})")
            else:
                rendered.append(f"> (system: {tag})")
    quoted = "\n".join(rendered)
    truncated = _truncate(quoted)
    return "\n".join(quoted_lines + [truncated, "", "---", ""])
