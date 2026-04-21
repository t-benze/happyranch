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


def _run_command(
    cmd: list[str],
    workspace: Path,
    session_id: str | None,
    timeout_seconds: int,
    input_text: str | None = None,
) -> ExecutorResult:
    sid = session_id or f"sess-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=True)
    start_time = time.monotonic()
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(workspace),
            capture_output=True,
            text=True,
            input=input_text,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return ExecutorResult(
            success=False,
            duration_seconds=int(time.monotonic() - start_time),
            session_id=sid,
            error=f"Session timed out after {timeout_seconds} seconds",
        )
    if completed.returncode != 0:
        error_summary = (completed.stderr or completed.stdout or "").strip()
        if error_summary:
            error_summary = f": {error_summary}"
        return ExecutorResult(
            success=False,
            duration_seconds=int(time.monotonic() - start_time),
            session_id=sid,
            error=f"Command exited with code {completed.returncode}{error_summary}",
        )
    return ExecutorResult(
        success=True,
        duration_seconds=int(time.monotonic() - start_time),
        session_id=sid,
    )


class ClaudeExecutor:
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
        # The workspace's .claude/settings.json `permissions.allow` list is not
        # honoured in headless `-p` mode (observed empirically: Claude Code
        # 2.1.105 records `command_permissions.allowedTools: []` regardless of
        # what's in settings.json). Pass --allowedTools on the CLI instead so
        # agents can reliably call `opc ...` callbacks. Keep the rule
        # synchronised with context_builder._build_settings_json.
        cmd = [
            self._cli_path,
            "-p",
            prompt,
            "--permission-mode",
            self._permission_mode,
            "--allowedTools",
            "Bash(opc *)",
        ]
        return _run_command(cmd, workspace, session_id, timeout_seconds)


class CodexExecutor:
    def __init__(self, codex_cli_path: str, sandbox_mode: str) -> None:
        self._cli_path = codex_cli_path
        self._sandbox_mode = sandbox_mode

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
    ) -> ExecutorResult:
        cmd = [
            self._cli_path,
            "exec",
            "--sandbox",
            self._sandbox_mode,
            "--skip-git-repo-check",
            "--json",
            "-",
        ]
        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            input_text=prompt,
        )


AgentExecutor = ClaudeExecutor
