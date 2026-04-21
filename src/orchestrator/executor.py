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
    returncode: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str | None = None


_TAIL_BYTES = 2000


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
        # The workspace's .claude/settings.json `permissions.allow` list is not
        # honoured in headless `-p` mode (observed empirically: Claude Code
        # 2.1.105 records `command_permissions.allowedTools: []` regardless of
        # what's in settings.json). Pass --allowedTools on the CLI instead so
        # agents can reliably call `opc ...` callbacks. Keep the rule
        # synchronised with context_builder._build_settings_json.
        cmd = [
            self._cli_path,
            "-p", prompt,
            "--permission-mode", self._permission_mode,
            "--allowedTools", "Bash(opc *)",
        ]
        workspace.mkdir(parents=True, exist_ok=True)
        start_time = time.monotonic()
        try:
            proc = subprocess.run(
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
                returncode=None,
                stdout_tail="",
                stderr_tail="",
                error=f"Session timed out after {timeout_seconds} seconds",
            )
        duration = int(time.monotonic() - start_time)
        rc = proc.returncode
        return ExecutorResult(
            # `success` previously meant "no TimeoutExpired" — every non-zero
            # rc still looked successful, which is what let TASK-044/045
            # silently fail with no trace. Tie success to rc==0 so the
            # run_step classifier treats a crashed subprocess as a failure.
            success=(rc == 0),
            duration_seconds=duration,
            session_id=sid,
            returncode=rc,
            stdout_tail=(proc.stdout or "")[-_TAIL_BYTES:],
            stderr_tail=(proc.stderr or "")[-_TAIL_BYTES:],
        )
