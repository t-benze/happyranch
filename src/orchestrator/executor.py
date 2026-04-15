from __future__ import annotations

import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ExecutorResult:
    """Outcome of a subprocess execution. Completion data lives in the DB."""

    success: bool
    duration_seconds: int
    session_id: str
    error: str | None = None


class AgentExecutor:
    def __init__(self, claude_cli_path: str, permission_mode: str) -> None:
        self._cli_path = claude_cli_path
        self._permission_mode = permission_mode

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
    ) -> ExecutorResult:
        sid = session_id or f"sess-{uuid.uuid4().hex}"
        cmd = [
            self._cli_path,
            "-p", prompt,
            "--permission-mode", self._permission_mode,
        ]
        workspace.mkdir(parents=True, exist_ok=True)
        start_time = time.monotonic()
        try:
            subprocess.run(
                cmd,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return ExecutorResult(
                success=False,
                duration_seconds=int(time.monotonic() - start_time),
                session_id=sid,
                error=f"Session timed out after {timeout_seconds} seconds",
            )
        return ExecutorResult(
            success=True,
            duration_seconds=int(time.monotonic() - start_time),
            session_id=sid,
        )
