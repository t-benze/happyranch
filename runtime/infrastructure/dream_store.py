"""Filesystem writes for private dream transcripts."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml


_MAX_TRANSCRIPT_BYTES = 1024 * 1024


class InvalidDreamTranscript(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class DreamStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def path_for(self, dream_id: str) -> Path:
        return self._root / f"{dream_id}.md"

    def write_transcript(
        self,
        *,
        dream_id: str,
        agent_name: str,
        local_date: str,
        window_start: str | None,
        window_end: str,
        summary: str,
        transcript_markdown: str,
        new_learnings_count: int,
        kb_candidate_count: int,
        founder_thread_id: str | None,
    ) -> Path:
        body = self._format(
            dream_id=dream_id,
            agent_name=agent_name,
            local_date=local_date,
            window_start=window_start,
            window_end=window_end,
            summary=summary,
            transcript_markdown=transcript_markdown,
            new_learnings_count=new_learnings_count,
            kb_candidate_count=kb_candidate_count,
            founder_thread_id=founder_thread_id,
        )
        encoded = body.encode("utf-8")
        if len(encoded) > _MAX_TRANSCRIPT_BYTES:
            raise InvalidDreamTranscript(
                "transcript_too_large",
                f"transcript is {len(encoded)} bytes, max {_MAX_TRANSCRIPT_BYTES}",
            )
        target = self.path_for(dream_id)
        fd, tmp_name = tempfile.mkstemp(dir=self._root, prefix=f".{dream_id}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(encoded)
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return target

    def read_transcript(self, dream_id: str) -> str:
        return self.path_for(dream_id).read_text(encoding="utf-8")

    def _format(
        self,
        *,
        dream_id: str,
        agent_name: str,
        local_date: str,
        window_start: str | None,
        window_end: str,
        summary: str,
        transcript_markdown: str,
        new_learnings_count: int,
        kb_candidate_count: int,
        founder_thread_id: str | None,
    ) -> str:
        frontmatter = {
            "dream_id": dream_id,
            "agent_name": agent_name,
            "local_date": local_date,
            "window_start": window_start,
            "window_end": window_end,
            "new_learnings_count": new_learnings_count,
            "kb_candidate_count": kb_candidate_count,
            "founder_thread_id": founder_thread_id,
        }
        fm_text = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        return (
            "---\n"
            f"{fm_text}\n"
            "---\n\n"
            "# Summary\n\n"
            f"{summary}\n\n"
            "# Transcript\n\n"
            f"{transcript_markdown}\n"
        )
