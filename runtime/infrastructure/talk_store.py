"""Filesystem writes for talk transcripts under <runtime>/talks/."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import yaml


_MAX_TRANSCRIPT_BYTES = 512 * 1024  # 512 KiB — errs on generous, still bounded.


class InvalidTranscript(ValueError):
    """Raised for transcript-writer validation failures.

    ``code`` encodes the failure reason (currently only ``transcript_too_large``).
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class TalkStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)

    def path_for(self, talk_id: str) -> Path:
        return self._root / f"{talk_id}.md"

    def write_transcript(
        self,
        *,
        talk_id: str,
        agent_name: str,
        started_at: str,
        ended_at: str,
        topic_list: list[str],
        new_learnings_count: int,
        new_kb_slugs: list[str],
        summary: str,
        transcript_markdown: str,
    ) -> Path:
        """Write transcript file atomically (write temp → rename)."""
        body = self._format(
            talk_id=talk_id,
            agent_name=agent_name,
            started_at=started_at,
            ended_at=ended_at,
            topic_list=topic_list,
            new_learnings_count=new_learnings_count,
            new_kb_slugs=new_kb_slugs,
            summary=summary,
            transcript_markdown=transcript_markdown,
        )
        encoded = body.encode("utf-8")
        if len(encoded) > _MAX_TRANSCRIPT_BYTES:
            raise InvalidTranscript(
                code="transcript_too_large",
                message=f"transcript is {len(encoded)} bytes, max {_MAX_TRANSCRIPT_BYTES}",
            )
        target = self.path_for(talk_id)
        # Atomic rename into place.
        fd, tmp_name = tempfile.mkstemp(dir=self._root, prefix=f".{talk_id}.", suffix=".md.tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(encoded)
            os.replace(tmp_name, target)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise
        return target

    def read_transcript(self, talk_id: str) -> str:
        return self.path_for(talk_id).read_text(encoding="utf-8")

    def _format(
        self,
        *,
        talk_id: str,
        agent_name: str,
        started_at: str,
        ended_at: str,
        topic_list: list[str],
        new_learnings_count: int,
        new_kb_slugs: list[str],
        summary: str,
        transcript_markdown: str,
    ) -> str:
        frontmatter = {
            "talk_id": talk_id,
            "agent_name": agent_name,
            "started_at": started_at,
            "ended_at": ended_at,
            "topic_list": topic_list,
            "new_learnings_count": new_learnings_count,
            "new_kb_slugs": new_kb_slugs,
        }
        fm_text = yaml.safe_dump(frontmatter, sort_keys=False).strip()
        return (
            "---\n"
            f"{fm_text}\n"
            "---\n\n"
            "# Summary\n\n"
            f"{summary}\n\n"
            "# Transcript (agent's perspective)\n\n"
            f"{transcript_markdown}\n"
        )
