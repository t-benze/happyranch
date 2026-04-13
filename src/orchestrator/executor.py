from __future__ import annotations

import json
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from src.models import CompletionReport


@dataclass
class ExecutorResult:
    success: bool
    report: CompletionReport | None
    duration_seconds: int
    session_id: str
    error: str | None = None


class AgentExecutor:
    def __init__(self, claude_cli_path: str, permission_mode: str) -> None:
        self._cli_path = claude_cli_path
        self._permission_mode = permission_mode

    def read_completion_report(self, workspace: Path) -> CompletionReport | None:
        """Read and parse completion_report.json from a workspace."""
        report_path = workspace / "completion_report.json"
        if not report_path.exists():
            return None
        try:
            data = json.loads(report_path.read_text())
            return CompletionReport(**data)
        except (json.JSONDecodeError, Exception):
            return None

    def run(
        self,
        workspace: Path,
        prompt: str,
        timeout_seconds: int = 1800,
    ) -> ExecutorResult:
        """Spawn a claude -p session and return the result."""
        session_id = f"sess-{uuid.uuid4().hex[:8]}"

        # Clean old completion report
        report_path = workspace / "completion_report.json"
        if report_path.exists():
            report_path.unlink()

        cmd = [
            self._cli_path,
            "-p", prompt,
            "--permission-mode", self._permission_mode,
        ]

        start_time = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                cwd=str(workspace),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            duration = int(time.monotonic() - start_time)
            return ExecutorResult(
                success=False,
                report=None,
                duration_seconds=duration,
                session_id=session_id,
                error=f"Session timed out after {timeout_seconds} seconds",
            )

        duration = int(time.monotonic() - start_time)

        # Read completion report
        report = self.read_completion_report(workspace)
        if report is None:
            return ExecutorResult(
                success=False,
                report=None,
                duration_seconds=duration,
                session_id=session_id,
                error="No completion_report.json found after session completed",
            )

        return ExecutorResult(
            success=True,
            report=report,
            duration_seconds=duration,
            session_id=session_id,
        )
