from __future__ import annotations

import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.config import Settings
from src.orchestrator._paths import OrgPaths


@dataclass
class ExecutorResult:
    """Outcome of a subprocess execution. Completion data lives in the DB.

    ``returncode``/``stdout_tail``/``stderr_tail`` feed the enriched
    ``agent session failed`` note in ``run_step._session_failed_note`` so
    a subprocess that exits without calling back is self-diagnosing from
    the audit trail alone (the TASK-044/045/077 class of failure).
    Timeouts leave ``returncode=None`` because the process was killed
    before an exit code could be observed; in that case the enriched
    note renders ``rc=?`` and the ``error`` string carries the timeout.
    """

    success: bool
    duration_seconds: int
    session_id: str
    returncode: int | None = None
    stdout_tail: str = ""
    stderr_tail: str = ""
    error: str | None = None


_TAIL_BYTES = 2000


def _run_command(
    cmd: list[str],
    workspace: Path,
    session_id: str | None,
    timeout_seconds: int,
    input_text: str | None = None,
    on_started: Callable[[int], None] | None = None,
) -> ExecutorResult:
    sid = session_id or f"sess-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=True)
    start_time = time.monotonic()
    # Popen (not subprocess.run) because the daemon needs the pid handed to
    # SessionTracker BEFORE we block in communicate(), so /cancel can SIGTERM
    # the process mid-session. stdin=PIPE unconditionally — Codex reads its
    # prompt from stdin; Claude ignores it when nothing is written.
    proc = subprocess.Popen(
        cmd,
        cwd=str(workspace),
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if on_started is not None:
        on_started(proc.pid)
    try:
        stdout, stderr = proc.communicate(input=input_text, timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        proc.kill()
        # Drain pipes so we don't leak FDs on the retry-free path.
        try:
            proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        return ExecutorResult(
            success=False,
            duration_seconds=int(time.monotonic() - start_time),
            session_id=sid,
            error=f"Session timed out after {timeout_seconds} seconds",
        )
    stdout_tail = (stdout or "")[-_TAIL_BYTES:]
    stderr_tail = (stderr or "")[-_TAIL_BYTES:]
    if proc.returncode != 0:
        error_summary = (stderr or stdout or "").strip()
        if error_summary:
            error_summary = f": {error_summary}"
        return ExecutorResult(
            success=False,
            duration_seconds=int(time.monotonic() - start_time),
            session_id=sid,
            returncode=proc.returncode,
            stdout_tail=stdout_tail,
            stderr_tail=stderr_tail,
            error=f"Command exited with code {proc.returncode}{error_summary}",
        )
    return ExecutorResult(
        success=True,
        duration_seconds=int(time.monotonic() - start_time),
        session_id=sid,
        returncode=proc.returncode,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
    )


class ClaudeExecutor:
    def __init__(self, claude_cli_path: str, permission_mode: str, settings: Settings, paths: OrgPaths | None = None) -> None:
        self._cli_path = claude_cli_path
        self._permission_mode = permission_mode
        self._settings = settings
        self._paths = paths

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
    ) -> ExecutorResult:
        # The workspace's .claude/settings.json `permissions.allow` list is not
        # honoured in headless `-p` mode (observed empirically: Claude Code
        # 2.1.105 records `command_permissions.allowedTools: []` regardless of
        # what's in settings.json). Pass --allowedTools on the CLI instead so
        # agents can reliably call `opc ...` callbacks. Per-agent extras come
        # from the optional ``allow_rules:`` list in the agent's frontmatter
        # at ``<runtime>/org/agents/<name>.md``.
        from src.orchestrator.workspace_adapters import allow_rules_for_agent

        # Workspace layout is `<runtime>/workspaces/<agent_name>`, so the
        # directory name is the canonical agent identifier.
        allowed = " ".join(allow_rules_for_agent(self._paths, workspace.name, cli=True))
        cmd = [
            self._cli_path,
            "-p",
            prompt,
            "--permission-mode",
            self._permission_mode,
            "--allowedTools",
            allowed,
        ]
        return _run_command(
            cmd, workspace, session_id, timeout_seconds, on_started=on_started,
        )


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
        on_started: Callable[[int], None] | None = None,
    ) -> ExecutorResult:
        cmd = [
            self._cli_path,
            "exec",
            "--sandbox",
            self._sandbox_mode,
            # Codex's `workspace-write` sandbox blocks all outbound sockets by
            # default, including localhost. The `opc` CLI talks to the daemon
            # over 127.0.0.1 via httpx, so without this override the agent's
            # `opc report-completion` call dies with
            # `httpx.ConnectError: [Errno 1] Operation not permitted` and the
            # task auto-rejects with "no completion callback" (TASK-080 class
            # of failure). Enable network at the sandbox layer; agent-side
            # discipline still flows through the sanctioned `opc` channel.
            "-c",
            "sandbox_workspace_write.network_access=true",
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
            on_started=on_started,
        )


AgentExecutor = ClaudeExecutor
