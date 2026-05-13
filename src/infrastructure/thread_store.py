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
