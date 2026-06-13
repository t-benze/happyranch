from __future__ import annotations

import json
import logging
import subprocess
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from runtime.config import Settings
from runtime.models import TokenUsage
from runtime.orchestrator._paths import OrgPaths

logger = logging.getLogger(__name__)


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
    token_usage: TokenUsage | None = None
    # The agent CLI's own session id, parsed from its structured output. Distinct
    # from `session_id` (the HappyRanch sess-<uuid> used for SessionTracker). Used
    # to resume thread sessions via `--resume` (issue #53). None for executors that
    # don't emit one and on parse failure.
    agent_session_id: str | None = None


_TAIL_BYTES = 2000


def _parse_claude_usage(stdout: str) -> TokenUsage | None:
    """Parse Claude Code's `--output-format json` stdout into TokenUsage.

    Best-effort: returns TokenUsage(usage_raw_json=...) on parse failure
    (token fields NULL) so the row still gets written for forensics.
    Returns None only when stdout is empty (no parse attempted).
    """
    if not stdout or not stdout.strip():
        return None
    try:
        obj = json.loads(stdout.strip())
    except json.JSONDecodeError:
        logger.warning("claude usage parser: stdout is not valid JSON")
        return TokenUsage(usage_raw_json=stdout[:_TAIL_BYTES])
    usage = obj.get("usage") if isinstance(obj, dict) else None
    if not isinstance(usage, dict):
        return TokenUsage(
            model=obj.get("model") if isinstance(obj, dict) else None,
            usage_raw_json=stdout[:_TAIL_BYTES],
        )
    return TokenUsage(
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cache_read_tokens=usage.get("cache_read_input_tokens"),
        cache_creation_tokens=usage.get("cache_creation_input_tokens"),
        reasoning_tokens=None,
        model=obj.get("model"),
        usage_raw_json=json.dumps(usage),
    )


def _parse_claude_session_id(stdout: str) -> str | None:
    """Extract `.session_id` from Claude Code's `--output-format json` stdout.

    Best-effort: returns None on empty/invalid/missing-field output. The session
    id is an optimization (resume), never a correctness dependency.
    """
    if not stdout or not stdout.strip():
        return None
    try:
        obj = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    sid = obj.get("session_id")
    return sid if isinstance(sid, str) and sid else None


def _parse_codex_usage(stdout: str) -> TokenUsage | None:
    """Parse Codex `exec --json` NDJSON event stream into TokenUsage.

    Walks events, picks the last `session_complete`. Returns None on empty
    stdout, TokenUsage with NULL token fields if no session_complete found
    (forensic preservation), populated TokenUsage on success.

    Note: the Codex event name "session_complete" is the documented terminal
    event. Verify against the running Codex CLI version during integration
    testing — if the schema changes, only this function needs updating.
    """
    if not stdout or not stdout.strip():
        return None
    last_complete: dict | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == "session_complete":
            last_complete = event
    if last_complete is None:
        return TokenUsage(usage_raw_json=stdout[:_TAIL_BYTES])
    tu = last_complete.get("token_usage") or {}
    if not isinstance(tu, dict):
        tu = {}
    return TokenUsage(
        input_tokens=tu.get("input_tokens"),
        output_tokens=tu.get("output_tokens"),
        cache_read_tokens=tu.get("cached_tokens"),
        cache_creation_tokens=None,
        reasoning_tokens=tu.get("reasoning_tokens"),
        model=last_complete.get("model"),
        usage_raw_json=json.dumps(last_complete),
    )


def _parse_opencode_usage(stdout: str) -> TokenUsage | None:
    """Parse opencode `--format json` stdout into TokenUsage.

    Sums assistant-role message usage. Model taken from last assistant
    message (sessions can span multiple models for tool use; last is the
    canonical 'this session ran on' answer).
    """
    if not stdout or not stdout.strip():
        return None
    try:
        obj = json.loads(stdout.strip())
    except json.JSONDecodeError:
        logger.warning("opencode usage parser: stdout is not valid JSON")
        return TokenUsage(usage_raw_json=stdout[:_TAIL_BYTES])
    if not isinstance(obj, dict):
        return TokenUsage(usage_raw_json=stdout[:_TAIL_BYTES])
    messages = obj.get("messages") or []
    assistant_msgs = [
        m
        for m in messages
        if isinstance(m, dict) and m.get("role") == "assistant" and isinstance(m.get("usage"), dict)
    ]
    if not assistant_msgs:
        return TokenUsage(usage_raw_json=stdout[:_TAIL_BYTES])

    def _sum(field: str) -> int | None:
        vals = [m["usage"].get(field) for m in assistant_msgs]
        nums = [v for v in vals if isinstance(v, int) and not isinstance(v, bool)]
        return sum(nums) if nums else None

    last_model = next((m.get("model") for m in reversed(assistant_msgs) if m.get("model")), None)
    return TokenUsage(
        input_tokens=_sum("input_tokens"),
        output_tokens=_sum("output_tokens"),
        cache_read_tokens=_sum("cache_read_tokens"),
        cache_creation_tokens=_sum("cache_write_tokens"),
        reasoning_tokens=_sum("reasoning_tokens"),
        model=last_model,
        usage_raw_json=json.dumps([m["usage"] for m in assistant_msgs]),
    )


def _parse_pi_usage(stdout: str) -> TokenUsage | None:
    """Preserve Pi JSON output for token-usage forensics.

    Pi's headless JSON schema is not pinned by this project yet. Store the
    raw JSON payload so successful sessions still leave an auditable usage row
    without pretending we know the token field mapping.
    """
    if not stdout or not stdout.strip():
        return None
    return TokenUsage(usage_raw_json=stdout[:_TAIL_BYTES])


def _run_command(
    cmd: list[str],
    workspace: Path,
    session_id: str | None,
    timeout_seconds: int,
    input_text: str | None = None,
    on_started: Callable[[int], None] | None = None,
    usage_parser: Callable[[str], "TokenUsage | None"] | None = None,
    session_id_parser: Callable[[str], "str | None"] | None = None,
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
    full_stdout = stdout or ""
    full_stderr = stderr or ""
    stdout_tail = full_stdout[-_TAIL_BYTES:]
    stderr_tail = full_stderr[-_TAIL_BYTES:]
    if proc.returncode != 0:
        # Subprocess failed → no token_usage row, per spec §4.3.
        error_summary = (full_stderr or full_stdout or "").strip()
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
    token_usage: TokenUsage | None = None
    if usage_parser is not None:
        try:
            token_usage = usage_parser(full_stdout)
        except Exception as exc:  # parser must never break the task
            logger.warning("usage parser raised: %s", exc)
            token_usage = None
    agent_session_id: str | None = None
    if session_id_parser is not None:
        try:
            agent_session_id = session_id_parser(full_stdout)
        except Exception as exc:  # parser must never break the task
            logger.warning("session-id parser raised: %s", exc)
            agent_session_id = None
    return ExecutorResult(
        success=True,
        duration_seconds=int(time.monotonic() - start_time),
        session_id=sid,
        returncode=proc.returncode,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
        token_usage=token_usage,
        agent_session_id=agent_session_id,
    )


# Prepended to every executor prompt, regardless of session type. A
# daemon-spawned session is a single non-interactive `... -p`/headless process:
# when the model yields its turn, the subprocess exits. Agents otherwise treat
# the session like an interactive loop and defer their callback to a "next
# turn" via ScheduleWakeup or a backgrounded command — neither of which
# survives process exit — so the session ends with no completion callback and
# the task auto-rejects (TASK-295 class of failure). The invariant is
# session-type agnostic (task `report-completion`, thread reply, etc.) because
# every session kind funnels through this shared executor layer.
_SESSION_LIFETIME_PREAMBLE = (
    "<session-lifetime>\n"
    "This is a single non-interactive turn. When you end your turn this "
    "process exits immediately — there is NO later turn, no scheduled "
    "wake-up, and any backgrounded command is killed on exit. Complete every "
    "callback this session requires (e.g. `happyranch report-completion`, a "
    "thread reply) as the FINAL action of THIS turn, before you yield. Never "
    "use ScheduleWakeup or a `run_in_background` command to defer it. If you "
    "are waiting on something external (CI, a deploy, a long build), do NOT "
    "wait for it to finish: report your terminal-or-in-flight status now, and "
    "use a `job` or `thread` for genuine async work.\n"
    "</session-lifetime>\n\n"
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
        resume_session_id: str | None = None,
    ) -> ExecutorResult:
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        # The workspace's .claude/settings.json `permissions.allow` list is not
        # honoured in headless `-p` mode (observed empirically: Claude Code
        # 2.1.105 records `command_permissions.allowedTools: []` regardless of
        # what's in settings.json). Pass --allowedTools on the CLI instead so
        # agents can reliably call `happyranch ...` callbacks. Per-agent extras come
        # from the optional ``allow_rules:`` list in the agent's frontmatter
        # at ``<runtime>/org/agents/<name>.md``.
        from runtime.orchestrator.workspace_adapters import allow_rules_for_agent

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
            "--output-format",
            "json",
        ]
        # Resume an existing session (issue #53) for thread turn 2+: the system
        # prompt + transcript stay in session memory and only the delta is shipped.
        # Resume may fork a new id; the caller reads ExecutorResult.agent_session_id.
        if resume_session_id:
            cmd += ["--resume", resume_session_id]
        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            on_started=on_started,
            usage_parser=_parse_claude_usage,
            session_id_parser=_parse_claude_session_id,
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
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        cmd = [
            self._cli_path,
            "exec",
            "--sandbox",
            self._sandbox_mode,
            # Codex's `workspace-write` sandbox blocks all outbound sockets by
            # default, including localhost. The `happyranch` CLI talks to the daemon
            # over 127.0.0.1 via httpx, so without this override the agent's
            # `happyranch report-completion` call dies with
            # `httpx.ConnectError: [Errno 1] Operation not permitted` and the
            # task auto-rejects with "no completion callback" (TASK-080 class
            # of failure). Enable network at the sandbox layer; agent-side
            # discipline still flows through the sanctioned `happyranch` channel.
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
            usage_parser=_parse_codex_usage,
        )


class OpencodeExecutor:
    """Headless opencode invocation.

    opencode has no `--allowedTools`-style flag; permissions are configured
    via the workspace's ``opencode.json`` (written by
    ``OpencodeWorkspaceAdapter``). Headless runs honor that file directly,
    so the sanctioned-channel discipline (allow ``happyranch`` + agent-specific
    extras, deny everything else) lives in a single surface — cleaner than
    Claude's two-surface settings.json + ``--allowedTools`` workaround.

    We deliberately do NOT pass ``--dangerously-skip-permissions``: the
    permission file is the enforcement surface, and bypassing it would
    erase the per-prefix discipline that CLAUDE.md mandates.
    """

    def __init__(self, opencode_cli_path: str) -> None:
        self._cli_path = opencode_cli_path

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
    ) -> ExecutorResult:
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        cmd = [
            self._cli_path,
            "run",
            "--dir",
            str(workspace),
            "--format",
            "json",
            "--prompt",
            prompt,
        ]
        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            on_started=on_started,
            usage_parser=_parse_opencode_usage,
        )


class PiExecutor:
    """Headless Pi invocation.

    Pi reads ``AGENTS.md`` from the workspace and supports print mode via
    ``-p``. It does not currently provide a HappyRanch-managed permission
    surface like Codex sandbox flags or opencode.json, so process containment
    must be supplied outside this executor if required.
    """

    def __init__(self, pi_cli_path: str) -> None:
        self._cli_path = pi_cli_path

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
    ) -> ExecutorResult:
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        cmd = [
            self._cli_path,
            "-p",
            prompt,
            "--mode",
            "json",
        ]
        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            on_started=on_started,
            usage_parser=_parse_pi_usage,
        )


AgentExecutor = ClaudeExecutor
