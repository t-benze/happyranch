"""Filesystem writes for thread transcripts under <runtime>/orgs/<slug>/threads/."""
from __future__ import annotations

import os
import tempfile
from datetime import datetime
from pathlib import Path

import yaml


class ThreadStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def path_for(self, thread_id: str) -> Path:
        return self._root / f"{thread_id}.md"

    def write_transcript(
        self,
        *,
        thread_id: str,
        subject: str,
        started_at: datetime,
        archived_at: datetime,
        participants: list[str],
        turns_used: int,
        new_learnings_total: int,
        new_kb_slugs: list[str],
        forwarded_from_id: str | None,
        summary: str,
        rendered_transcript: str,
    ) -> Path:
        frontmatter = {
            "thread_id": thread_id,
            "subject": subject,
            "started_at": started_at.isoformat(),
            "archived_at": archived_at.isoformat(),
            "participants": participants,
            "forwarded_from_id": forwarded_from_id,
            "turns_used": turns_used,
            "new_learnings_total": new_learnings_total,
            "new_kb_slugs": new_kb_slugs,
        }
        fm_text = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        body = (
            "---\n"
            f"{fm_text}\n"
            "---\n\n"
            "# Summary\n\n"
            f"{summary}\n\n"
            f"{rendered_transcript}\n"
        )
        target = self.path_for(thread_id)
        fd, tmp_name = tempfile.mkstemp(dir=self._root, prefix=f".{thread_id}.", suffix=".md.tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(body.encode("utf-8"))
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return target


def render_transcript_body(messages: list) -> str:
    """Render a chronological list of ThreadMessage into markdown."""
    lines: list[str] = ["# Transcript", ""]
    for m in messages:
        ts = m.created_at.isoformat() if hasattr(m.created_at, "isoformat") else str(m.created_at)
        kind_name = m.kind.value if hasattr(m.kind, "value") else str(m.kind)
        if kind_name == "message":
            header = f"## Message {m.seq} — {m.speaker} · {ts}"
            lines.append(header)
            lines.append(m.body_markdown or "")
            lines.append("")
        elif kind_name == "decline":
            lines.append(f"## Message {m.seq} — {m.speaker} · {ts}")
            lines.append(f"> 👁 declined: {m.decline_reason or ''}")
            lines.append("")
        elif kind_name == "system":
            payload = m.system_payload or {}
            tag = payload.get("kind_tag", "system")
            if tag == "participant_added":
                rendered = f"founder added {payload.get('agent_name')} to the thread"
            elif tag == "task_dispatched":
                tgt = payload.get("target_agent")
                tid = payload.get("task_id")
                brief = payload.get("brief_preview", "")
                rendered = f"system: dispatched {tid} to {tgt}" + (
                    f" — {brief}" if brief else ""
                )
            elif tag == "task_completed":
                tid = payload.get("task_id")
                orig = payload.get("original_task_id")
                rendered = f"**Task {tid} completed**" + (
                    f" (chain root {orig})" if orig and orig != tid else ""
                )
                summary = (payload.get("final_output_summary") or "").strip()
                if summary:
                    rendered += f" · {summary[:240]}"
                artifact = payload.get("final_artifact_dir")
                if artifact:
                    rendered += f" · `{artifact}`"
            elif tag == "task_failed":
                tid = payload.get("task_id")
                orig = payload.get("original_task_id")
                rendered = f"**Task {tid} failed**" + (
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
                    rendered += " · " + "; ".join(annotations)
            elif tag == "turn_cap_extended":
                rendered = (
                    f"system: turn cap extended from {payload.get('prior_cap')} "
                    f"to {payload.get('new_cap')}"
                )
            elif tag == "archived":
                rendered = "system: thread archived"
            else:
                rendered = f"system: {tag}"
            lines.append(f"## Message {m.seq} — {m.speaker} · {ts}")
            lines.append(f"> {rendered}")
            lines.append("")
    return "\n".join(lines)
