"""Headless assistant A-mode: TurnFrame vocabulary, adapter interface, per-workspace
conversation persistence, and a headless session manager.

PR-1 of the THR-056 approach-A dock rebuild.  The PTY path (AssistantPtySession,
AssistantSessionManager) is FROZEN — this module is a *parallel* implementation.

Architecture
------------
Each user message spawns a short-lived headless executor run (one-turn, not a
persistent subprocess).  Continuity is carried by the executor's own session-id,
NOT by a long-lived daemon process.  The dock receives a stream of normalized
``TurnFrame`` objects (JSON over WebSocket) instead of raw PTY chunks.

Adapter registry (design §2.2)
    * Registry keyed by ``config.selected_executor`` (claude/codex/opencode/pi).
    * Unknown executor → ``None`` → route returns "a-mode-unavailable".
    * PR-1 ships only the interface + registry + null/echo adapter.
      Real adapters land in PR-2 (opencode/pi) and PR-3/PR-4 (claude/codex).

Frame vocabulary (design §2.1)
    turn_start{role}     — signals the start of a new assistant turn.
    text_delta{text}     — streamed assistant output.
    tool_call{name,input}— optional tool-use transparency.
    tool_result{name,ok} — optional tool-result transparency.
    turn_end{role,usage} — signals the end of a turn.
    status{code,detail?} — daemon-level state (ready/working/session_closed/error).
    error{message}       — daemon-level error.

Persistence (design §4)
    Per-workspace JSON file, NOT a SQLite table.  Survives dock close/open AND
    daemon restart.  Stores the conversation log as structured turns.

Frozen symbols (design §8)
    AssistantPtySession, AssistantSessionManager, attach_assistant_session,
    the resize control string, and bearer-subprotocol auth are NOT edited.
"""

from __future__ import annotations

import asyncio
import contextlib
import json as _json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

# Shared helpers from the executor layer — used by ClaudeAdapter and
# CodexAdapter to parse terminal result events instead of manual
# event.get() that forks the parser.
from runtime.orchestrator.executors import (
    _parse_claude_usage as _executor_parse_claude_usage,
    _parse_claude_session_id as _executor_parse_claude_session_id,
    _parse_codex_usage as _executor_parse_codex_usage,
)

# ---------------------------------------------------------------------------
# TurnFrame vocabulary
# ---------------------------------------------------------------------------

class TurnFrame(BaseModel):
    """Normalized frame sent from backend to dock over WebSocket (JSON)."""
    model_config = ConfigDict(extra="forbid")

    type: str  # turn_start | text_delta | tool_call | tool_result | turn_end | status | error | history
    role: str | None = None           # "assistant" (turn_start, turn_end)
    text: str | None = None           # text_delta payload
    name: str | None = None           # tool_call / tool_result name
    input: dict[str, Any] | None = None  # tool_call input payload
    ok: bool | None = None            # tool_result outcome
    usage: dict[str, Any] | None = None  # turn_end token usage
    code: str | None = None           # status code (ready | working | session_closed | error)
    detail: str | None = None         # status detail (optional)
    message: str | None = None        # error message
    turns: list[dict[str, Any]] | None = None  # history frame: serialised turns

    # ---- factory helpers ----

    @classmethod
    def turn_start(cls, *, role: str = "assistant") -> TurnFrame:
        return cls(type="turn_start", role=role)

    @classmethod
    def text_delta(cls, *, text: str) -> TurnFrame:
        return cls(type="text_delta", text=text)

    @classmethod
    def tool_call(cls, *, name: str, input: dict[str, Any] | None = None) -> TurnFrame:
        return cls(type="tool_call", name=name, input=input)

    @classmethod
    def tool_result(cls, *, name: str, ok: bool) -> TurnFrame:
        return cls(type="tool_result", name=name, ok=ok)

    @classmethod
    def turn_end(cls, *, role: str = "assistant", usage: dict[str, Any] | None = None) -> TurnFrame:
        return cls(type="turn_end", role=role, usage=usage)

    @classmethod
    def status(cls, *, code: str, detail: str | None = None) -> TurnFrame:
        return cls(type="status", code=code, detail=detail)

    @classmethod
    def error(cls, *, message: str) -> TurnFrame:
        return cls(type="error", message=message)

    @classmethod
    def history(cls, *, turns: list[dict[str, Any]]) -> TurnFrame:
        """Replay envelope carrying the persisted structured conversation log.

        Emitted once at WS connect (before status{ready}) so the dock can
        render the full history from conversation.turns.
        """
        return cls(type="history", turns=turns)


# ---------------------------------------------------------------------------
# Permission posture (forward declaration for PR-3/PR-4)
# ---------------------------------------------------------------------------

@dataclass
class PermissionPosture:
    """Permission configuration for headless executor invocations.

    PR-1 shipped this as an empty placeholder.  Claude fields land in PR-3
    (--allowedTools / --permission-mode); codex fields land in PR-4.
    The caller (route handler) pre-computes the posture; the adapter reads it.
    """
    # Claude-specific (PR-3).
    claude_allowed_tools: str | None = None
    claude_permission_mode: str = "auto"

    # Codex-specific (PR-4).  Defaults are the founder-ruled posture
    # per KB assistant-headless-permission-postures:
    #   sandbox = workspace-write, approval = never.
    codex_sandbox: str = "workspace-write"
    codex_ask_for_approval: str = "never"


# ---------------------------------------------------------------------------
# HeadlessAdapter interface (design §2.2)
# ---------------------------------------------------------------------------

class HeadlessAdapter(Protocol):
    """Contract every per-executor headless adapter must fulfil.

    Real adapters (PR-2/PR-3/PR-4) reuse the existing JSONL parsing helpers
    in ``runtime/orchestrator/executors.py`` (``_parse_claude_usage``,
    ``_parse_codex_usage``, ``_parse_opencode_usage``, ``_parse_pi_usage``,
    ``_parse_claude_session_id``) — they are imported and wired per-adapter,
    not forked.
    """

    def build_turn_argv(
        self,
        *,
        prompt: str,
        resume_id: str | None,
        permission_posture: PermissionPosture,
    ) -> list[str]:
        """Build the subprocess argv for one-turn headless execution."""
        ...

    def parse_event(self, raw_line: str) -> TurnFrame | None:
        """Parse one JSONL line into a TurnFrame.  Return None for non-interesting
        events (e.g. system messages the dock doesn't need)."""
        ...

    def extract_session_id(self, frame: TurnFrame) -> str | None:
        """Extract the executor's own session id from a TurnFrame for resume
        continuity."""
        ...

    def drain_pending_frames(self) -> list[TurnFrame]:
        """Drain any buffered frames that haven't been emitted yet.

        Called by run_headless_turn after EOF on stdout so multi-content-block
        messages (e.g. assistant with text + 2 tool_use blocks) don't leave
        frames stranded in the adapter's internal buffer.

        Default: no-op — adapters without buffering return an empty list.
        """
        return []


# ---------------------------------------------------------------------------
# Null / echo adapter (test-only, PR-1)
# ---------------------------------------------------------------------------

class NullAdapter:
    """Echo adapter for testing.  Echoes the prompt as a single text_delta turn.

    Used to exercise the TurnFrame vocabulary, registry lookup, and the
    HeadlessAssistantManager pipeline without requiring any real CLI.
    """

    def build_turn_argv(
        self,
        *,
        prompt: str,
        resume_id: str | None,
        permission_posture: PermissionPosture,
    ) -> list[str]:
        raise NotImplementedError(
            "NullAdapter.build_turn_argv must be overridden or patched in tests"
        )

    def parse_event(self, raw_line: str) -> TurnFrame | None:
        line = raw_line.strip()
        if not line:
            return None
        if line.startswith("TEXT_DELTA:"):
            text = line[len("TEXT_DELTA:"):].strip()
            return TurnFrame.text_delta(text=text)
        if line.startswith("TOOL_CALL:"):
            rest = line[len("TOOL_CALL:"):].strip()
            return TurnFrame.tool_call(name=rest, input=None)
        if line.startswith("TOOL_RESULT:"):
            rest = line[len("TOOL_RESULT:"):].strip()
            parts = rest.split(":", 1)
            name = parts[0].strip()
            ok = len(parts) > 1 and parts[1].strip().lower() == "ok"
            return TurnFrame.tool_result(name=name, ok=ok)
        if line.startswith("USAGE:"):
            import json
            rest = line[len("USAGE:"):].strip()
            try:
                usage = json.loads(rest) if rest else None
            except (json.JSONDecodeError, ValueError):
                usage = {"raw": rest}
            return TurnFrame.turn_end(usage=usage)
        if line.startswith("SESSION_ID:"):
            rest = line[len("SESSION_ID:"):].strip()
            return TurnFrame.turn_end(usage={"session_id": rest})
        if line.startswith("STATUS:"):
            rest = line[len("STATUS:"):].strip()
            return TurnFrame.status(code=rest)
        if line.startswith("ERROR:"):
            rest = line[len("ERROR:"):].strip()
            return TurnFrame.error(message=rest)
        return TurnFrame.text_delta(text=line)

    def extract_session_id(self, frame: TurnFrame) -> str | None:
        if frame.usage and isinstance(frame.usage, dict):
            sid = frame.usage.get("session_id")
            if isinstance(sid, str) and sid:
                return sid
        return None

    def drain_pending_frames(self) -> list[TurnFrame]:
        """No-op — NullAdapter has no buffering."""
        return []


# ---------------------------------------------------------------------------
# Adapter registry
# ---------------------------------------------------------------------------

_ADAPTERS: dict[str, HeadlessAdapter] = {}
_DEFAULT_NULL_ADAPTER = NullAdapter()


def register_adapter(executor: str, adapter: HeadlessAdapter) -> None:
    """Register a headless adapter for an executor key (claude/codex/opencode/pi).

    Real adapters are registered in PR-2/PR-3/PR-4.  PR-1 registers only the
    null adapter for tests.
    """
    key = executor.strip().lower()
    if not key:
        raise ValueError("executor key must be non-empty")
    _ADAPTERS[key] = adapter


def get_adapter(executor: str) -> HeadlessAdapter | None:
    """Look up a headless adapter by executor key.  ``None`` means the executor
    has no A-mode adapter — the route should return 'a-mode-unavailable'."""
    key = executor.strip().lower()
    return _ADAPTERS.get(key)


# ---- init: register the null adapter for tests ----
register_adapter("_null", _DEFAULT_NULL_ADAPTER)


# ---------------------------------------------------------------------------
# OpenCodeAdapter (PR-2)
# ---------------------------------------------------------------------------

class OpenCodeAdapter:
    """Headless adapter for opencode (v1.14.31+, ``run --format json``).

    opencode emits NDJSON events: ``step_start``, ``text``, ``tool_use``,
    ``step_finish``, ``result``.  Session continuity via ``-s <session_id>``
    or ``-c`` (last session).

    Permission posture
    ------------------
    opencode has no workspace-level ``opencode.json`` in the bootstrapped
    assistant workspace (the bootstrap doesn't create one — see §3 verification
    in the THR-056 design).  The adapter therefore adds
    ``--dangerously-skip-permissions`` to every invocation so the headless run
    doesn't hang on interactive approval prompts.

    **This is flagged as gated** — it is a permission-posture change that
    requires founder approval (design §3).  Until that is resolved, the adapter
    is functional but the posture is noted in the PR.
    """

    def __init__(self) -> None:
        self._last_session_id: str | None = None

    # ---- HeadlessAdapter contract ----

    def build_turn_argv(
        self,
        *,
        prompt: str,
        resume_id: str | None,
        permission_posture: PermissionPosture,
    ) -> list[str]:
        argv = ["opencode", "run", "--format", "json", "--dangerously-skip-permissions"]
        if resume_id:
            argv.extend(["-s", resume_id])
        else:
            argv.append("-c")
        argv.append(prompt)
        return argv

    def parse_event(self, raw_line: str) -> TurnFrame | None:
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            return None
        try:
            event = _json.loads(line)
        except (_json.JSONDecodeError, ValueError):
            return None
        if not isinstance(event, dict):
            return None

        # Track session id from every event that carries it.
        sid = event.get("sessionID")
        if isinstance(sid, str) and sid:
            self._last_session_id = sid

        etype = event.get("type")
        if etype == "text":
            part = event.get("part")
            if isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str) and text:
                    return TurnFrame.text_delta(text=text)
        elif etype == "tool_use":
            part = event.get("part")
            if isinstance(part, dict):
                tool_name = part.get("tool")
                if isinstance(tool_name, str):
                    state = part.get("state")
                    return TurnFrame.tool_call(
                        name=tool_name,
                        input=state if isinstance(state, dict) else None,
                    )
        return None

    def extract_session_id(self, frame: TurnFrame) -> str | None:
        return self._last_session_id

    def drain_pending_frames(self) -> list[TurnFrame]:
        """No-op — OpenCodeAdapter has no buffering."""
        return []


# ---------------------------------------------------------------------------
# PiAdapter (PR-2)
# ---------------------------------------------------------------------------

class PiAdapter:
    """Headless adapter for pi (v0.80.2+, ``-p --mode json``).

    pi emits JSONL events: ``session``, ``agent_start``, ``turn_start``,
    ``message_start``, ``message_update`` (with ``text_delta`` sub-events),
    ``message_end``, ``turn_end``, ``agent_end``.
    Session continuity via ``--session-id <id>`` (exact) or ``-c`` (last).

    Containment posture
    -------------------
    pi is accepted **uncontained** per THR-056 design §3.  ``pi -p`` runs as
    the invoking user with full access — no sandbox, no permission flags, no
    approval gate.  This asymmetry is documented in the design and acknowledged
    in the PR notes.
    """

    def __init__(self) -> None:
        self._last_session_id: str | None = None

    # ---- HeadlessAdapter contract ----

    def build_turn_argv(
        self,
        *,
        prompt: str,
        resume_id: str | None,
        permission_posture: PermissionPosture,
    ) -> list[str]:
        argv = ["pi", "-p", "--mode", "json"]
        if resume_id:
            argv.extend(["--session-id", resume_id])
        else:
            argv.append("-c")
        argv.append(prompt)
        return argv

    def parse_event(self, raw_line: str) -> TurnFrame | None:
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            return None
        try:
            event = _json.loads(line)
        except (_json.JSONDecodeError, ValueError):
            return None
        if not isinstance(event, dict):
            return None

        etype = event.get("type")

        # Track session id from the initial 'session' event.
        if etype == "session":
            sid = event.get("id")
            if isinstance(sid, str) and sid:
                self._last_session_id = sid

        if etype == "message_update":
            ame = event.get("assistantMessageEvent")
            if isinstance(ame, dict) and ame.get("type") == "text_delta":
                delta = ame.get("delta")
                if isinstance(delta, str) and delta:
                    return TurnFrame.text_delta(text=delta)
        return None

    def extract_session_id(self, frame: TurnFrame) -> str | None:
        return self._last_session_id

    def drain_pending_frames(self) -> list[TurnFrame]:
        """No-op — PiAdapter has no buffering."""
        return []


# ---- init: register PR-2 adapters ----
register_adapter("opencode", OpenCodeAdapter())
register_adapter("pi", PiAdapter())


# ---------------------------------------------------------------------------
# ClaudeAdapter (PR-3)
# ---------------------------------------------------------------------------

class ClaudeAdapter:
    """Headless adapter for claude (v2.1.193+, ``-p --output-format stream-json``).

    claude emits JSONL events in stream-json mode:
    - ``system`` (subtype init/hook_started/hook_response) — session setup.
    - ``assistant`` — message with content blocks (text, tool_use).
    - ``user`` — tool_result content blocks.
    - ``result`` (subtype success/error_*) — terminal event with usage/session_id.
    - ``rate_limit_event`` — daemon-level, not surfaced to dock.

    Session continuity via ``--resume <session_id>``.

    Permission posture (THR-056 design §3, KB assistant-headless-permission-postures)
    -------------------------------------------------------------------------
    Mirrors the org-agent ``allow_rules`` machinery **exactly** as
    ClaudeExecutor does (executors.py:580-596).  The allowlist is built by
    the caller via ``allow_rules_for_agent(paths, workspace.name, cli=True)``
    and passed in ``PermissionPosture.claude_allowed_tools``.  The permission
    mode comes from ``PermissionPosture.claude_permission_mode``.

    This is NOT ``--dangerously-skip-permissions`` — the assistant's headless
    posture is the same as a worker's, per the founder ruling.
    """

    def __init__(self) -> None:
        self._last_session_id: str | None = None
        self._pending_frames: list[TurnFrame] = []
        self._terminal_usage: dict[str, Any] | None = None

    # ---- HeadlessAdapter contract ----

    def build_turn_argv(
        self,
        *,
        prompt: str,
        resume_id: str | None,
        permission_posture: PermissionPosture,
    ) -> list[str]:
        allowed = permission_posture.claude_allowed_tools or "Bash(happyranch *)"
        mode = permission_posture.claude_permission_mode or "auto"
        # Mirror the order in ClaudeExecutor (executors.py:588-596):
        # -p <prompt> immediately, then flags, then --resume at the end.
        argv = [
            "claude", "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--permission-mode", mode,
            "--allowedTools", allowed,
        ]
        if resume_id:
            argv.extend(["--resume", resume_id])
        return argv

    def parse_event(self, raw_line: str) -> TurnFrame | None:
        # If we have pending frames from a previous multi-content-block
        # message, emit one now.  Silently parse the current raw_line for
        # side effects (session_id tracking), pushing its frames to the
        # pending queue so they are emitted in the correct order after
        # the buffered frames.
        if self._pending_frames:
            self._parse_for_side_effects(raw_line)
            return self._pending_frames.pop(0)
        return self._parse_event_impl(raw_line)

    def _parse_for_side_effects(self, raw_line: str) -> None:
        """Parse *raw_line* silently for side effects (session_id /
        terminal-usage tracking).  Any resulting TurnFrame is pushed to
        ``_pending_frames`` so it is emitted in order after buffered frames."""
        frame = self._parse_event_impl(raw_line)
        if frame is not None:
            self._pending_frames.append(frame)

    def _parse_event_impl(self, raw_line: str) -> TurnFrame | None:
        """Core parsing logic: one JSONL line → at most one TurnFrame.

        Multi-content-block assistant messages return the *first* block and
        buffer the rest in ``_pending_frames`` so every content block is
        eventually emitted across successive calls.
        """
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            return None
        try:
            event = _json.loads(line)
        except (_json.JSONDecodeError, ValueError):
            return None
        if not isinstance(event, dict):
            return None

        # Track session_id from every event that carries it.
        sid = event.get("session_id")
        if isinstance(sid, str) and sid:
            self._last_session_id = sid

        etype = event.get("type")
        if etype == "system":
            subtype = event.get("subtype")
            if subtype == "init":
                # System init carries session_id / model — no dock frame,
                # but we already tracked session_id above.
                return None
            # hook_started, hook_response, etc. → no dock frame.
            return None

        if etype == "assistant":
            msg = event.get("message")
            if not isinstance(msg, dict):
                return None
            content = msg.get("content")
            if not isinstance(content, list):
                return None
            frames: list[TurnFrame] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text")
                    if isinstance(text, str) and text:
                        frames.append(TurnFrame.text_delta(text=text))
                elif btype == "tool_use":
                    name = block.get("name")
                    if isinstance(name, str):
                        frames.append(TurnFrame.tool_call(
                            name=name,
                            input=block.get("input") if isinstance(block.get("input"), dict) else None,
                        ))
            if not frames:
                return None
            if len(frames) == 1:
                return frames[0]
            # Multi-content-block message: return the first block, buffer
            # the rest so they are emitted across the next calls.
            result = frames[0]
            self._pending_frames.extend(frames[1:])
            return result

        if etype == "user":
            msg = event.get("message")
            if not isinstance(msg, dict):
                return None
            content = msg.get("content")
            if not isinstance(content, list):
                return None
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_result":
                    # Mark as ok=True — errors are surfaced via the
                    # assistant's next text block or the result event.
                    return TurnFrame.tool_result(
                        name=block.get("tool_use_id", "unknown"),
                        ok=True,
                    )
            return None

        if etype == "result":
            # Terminal event — carries session_id and usage.
            # Reuse the shared executor helpers (_parse_claude_usage,
            # _parse_claude_session_id) instead of manual event.get()
            # to maintain parser parity with the orchestrator path.
            #
            # Session id: use _parse_claude_session_id for consistency
            # (the generic tracking above also catches this, but the
            # shared helper normalises the extraction).
            sid = _executor_parse_claude_session_id(raw_line)
            if sid:
                self._last_session_id = sid
            # Usage: parse via _parse_claude_usage for canonical model +
            # normalised token fields.  Convert TokenUsage to a dict so
            # run_headless_turn can merge it into the turn_end frame.
            parsed = _executor_parse_claude_usage(raw_line)
            if parsed is not None:
                tu_dict: dict[str, Any] = {}
                if parsed.input_tokens is not None:
                    tu_dict["input_tokens"] = parsed.input_tokens
                if parsed.output_tokens is not None:
                    tu_dict["output_tokens"] = parsed.output_tokens
                if parsed.cache_read_tokens is not None:
                    tu_dict["cache_read_input_tokens"] = parsed.cache_read_tokens
                if parsed.cache_creation_tokens is not None:
                    tu_dict["cache_creation_input_tokens"] = parsed.cache_creation_tokens
                if parsed.model is not None:
                    tu_dict["model"] = parsed.model
                self._terminal_usage = tu_dict
            # No dock frame needed; run_headless_turn sends turn_end
            # independently.
            return None

        # rate_limit_event, unknown → no dock frame.
        return None

    def extract_session_id(self, frame: TurnFrame) -> str | None:
        return self._last_session_id

    def drain_pending_frames(self) -> list[TurnFrame]:
        """Drain all buffered frames from multi-content-block messages.

        Called at EOF by run_headless_turn so frames buffered by
        multi-content-block assistant messages are not lost.
        """
        frames = list(self._pending_frames)
        self._pending_frames.clear()
        return frames


# ---- init: register PR-3 adapter ----
register_adapter("claude", ClaudeAdapter())


# ---------------------------------------------------------------------------
# CodexAdapter (PR-4)
# ---------------------------------------------------------------------------

class CodexAdapter:
    """Headless adapter for codex (0.139.0+, ``exec --json``).

    codex emits JSONL events in ``exec --json`` mode:
    - ``thread.started`` — carries ``thread_id`` (session id).
    - ``turn.started`` — turn initiation, no dock frame.
    - ``item.started`` — item start (``command_execution`` → tool_call).
    - ``item.completed`` — item completion (``agent_message`` → text_delta,
      ``command_execution`` → tool_result).
    - ``turn.completed`` — terminal event with ``usage``.

    Session continuity via ``codex exec resume <session_id>``.

    Permission posture (THR-056 design §3, KB assistant-headless-permission-postures)
    -------------------------------------------------------------------------
    Sandbox = workspace-write, approval = never.  Parity with the interactive
    assistant (``assistant_pty.py:131`` injects
    ``sandbox_workspace_write.network_access=true``) — minus the now-impossible
    interactive approval prompt.  The adapter mirrors the network-access config
    override for parity.

    NOT ``--dangerously-bypass-approvals-and-sandbox``.
    """

    def __init__(self) -> None:
        self._last_session_id: str | None = None
        self._terminal_usage: dict[str, Any] | None = None

    # ---- HeadlessAdapter contract ----

    def build_turn_argv(
        self,
        *,
        prompt: str,
        resume_id: str | None,
        permission_posture: PermissionPosture,
    ) -> list[str]:
        sandbox = permission_posture.codex_sandbox or "workspace-write"
        approval = permission_posture.codex_ask_for_approval or "never"

        # -a <approval> is a TOP-LEVEL flag on codex-cli 0.139.0 (appears
        # only in `codex --help`, not `codex exec --help`).  It must come
        # BEFORE the exec subcommand — clap parses global flags before the
        # subcommand name.  Confirmed empirically: `codex -a never exec -s
        # workspace-write --json <prompt>` parses and starts execution.
        #
        # -s <sandbox> is an exec-level flag (appears in `codex exec --help`).
        # -c sandbox_workspace_write.network_access=true mirrors the
        #   interactive assistant (assistant_pty.py:131) — without it, codex
        #   in workspace-write sandbox blocks all outbound sockets including
        #   localhost, so happyranch CLI calls fail.
        argv = [
            "codex",
            "-a", approval,
            "exec",
            "-s", sandbox,
            "-c", "sandbox_workspace_write.network_access=true",
            "--json",
        ]
        if resume_id:
            argv.extend(["resume", resume_id])
        argv.append(prompt)
        return argv

    def parse_event(self, raw_line: str) -> TurnFrame | None:
        line = raw_line.strip()
        if not line or not line.startswith("{"):
            return None
        try:
            event = _json.loads(line)
        except (_json.JSONDecodeError, ValueError):
            return None
        if not isinstance(event, dict):
            return None

        etype = event.get("type")

        if etype == "thread.started":
            tid = event.get("thread_id")
            if isinstance(tid, str) and tid:
                self._last_session_id = tid
            return None

        if etype == "turn.started":
            return None

        if etype == "item.started":
            item = event.get("item")
            if isinstance(item, dict) and item.get("type") == "command_execution":
                cmd = item.get("command")
                if isinstance(cmd, str):
                    return TurnFrame.tool_call(
                        name="bash",
                        input={"command": cmd},
                    )
            return None

        if etype == "item.completed":
            item = event.get("item")
            if not isinstance(item, dict):
                return None
            itype = item.get("type")
            if itype == "agent_message":
                text = item.get("text")
                if isinstance(text, str) and text:
                    return TurnFrame.text_delta(text=text)
            elif itype == "command_execution":
                cmd = item.get("command") or "unknown"
                exit_code = item.get("exit_code")
                ok = exit_code == 0
                return TurnFrame.tool_result(name=cmd, ok=ok)
            return None

        if etype == "turn.completed":
            # Reuse the shared executor helper (_parse_codex_usage) for
            # canonical usage parsing including the issue #216 normalization
            # (input_tokens = max(input - cached, 0)).
            parsed = _executor_parse_codex_usage(raw_line)
            if parsed is not None:
                tu_dict: dict[str, Any] = {}
                if parsed.input_tokens is not None:
                    tu_dict["input_tokens"] = parsed.input_tokens
                if parsed.output_tokens is not None:
                    tu_dict["output_tokens"] = parsed.output_tokens
                if parsed.cache_read_tokens is not None:
                    tu_dict["cache_read_input_tokens"] = parsed.cache_read_tokens
                if parsed.reasoning_tokens is not None:
                    tu_dict["reasoning_output_tokens"] = parsed.reasoning_tokens
                if parsed.model is not None:
                    tu_dict["model"] = parsed.model
                self._terminal_usage = tu_dict
            return None

        return None

    def extract_session_id(self, frame: TurnFrame) -> str | None:
        return self._last_session_id

    def drain_pending_frames(self) -> list[TurnFrame]:
        """No-op — CodexAdapter has no buffering.  codex emits one event
        per item; no multi-content-block messages."""
        return []


# ---- init: register PR-4 adapter ----
register_adapter("codex", CodexAdapter())


# ---------------------------------------------------------------------------
# AssistantConversation — per-workspace file persistence
# ---------------------------------------------------------------------------

@dataclass
class _TurnRecord:
    """A recorded turn (user prompt → assistant frames)."""
    id: str
    prompt: str
    frames: list[TurnFrame] = field(default_factory=list)
    started_at: str | None = None  # ISO-format
    finished_at: str | None = None  # ISO-format
    session_id: str | None = None  # executor's session id for resume


class AssistantConversation:
    """Per-workspace structured conversation log stored as a JSON file.

    NOT a SQLite table — this survives dock close/open AND daemon restart
    without a schema migration.  The file lives at
    ``<assistant-workspace>/conversation.json``.
    """

    _FILENAME = "conversation.json"

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self._path = workspace / self._FILENAME
        self.executor: str | None = None
        self.resume_session_id: str | None = None
        self.turns: list[_TurnRecord] = []

    # ---- I/O ----

    def load(self) -> bool:
        """Load from disk.  Returns False when the file doesn't exist (fresh start)."""
        if not self._path.exists():
            return False
        raw = self._path.read_text()
        data = _json.loads(raw)
        self.executor = data.get("executor")
        self.resume_session_id = data.get("resume_session_id")
        self.turns = []
        for t in data.get("turns", []):
            frames = [TurnFrame.model_validate(f) for f in t.get("frames", [])]
            self.turns.append(_TurnRecord(
                id=t.get("id", ""),
                prompt=t.get("prompt", ""),
                frames=frames,
                started_at=t.get("started_at"),
                finished_at=t.get("finished_at"),
                session_id=t.get("session_id"),
            ))
        return True

    def save(self) -> None:
        """Persist to disk atomically (write-then-rename)."""
        data: dict[str, Any] = {
            "executor": self.executor,
            "resume_session_id": self.resume_session_id,
            "workspace": str(self.workspace),
            "turns": [
                {
                    "id": t.id,
                    "prompt": t.prompt,
                    "frames": [f.model_dump(exclude_none=True) for f in t.frames],
                    "started_at": t.started_at,
                    "finished_at": t.finished_at,
                    "session_id": t.session_id,
                }
                for t in self.turns
            ],
        }
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(_json.dumps(data, indent=2) + "\n")
        tmp.replace(self._path)

    def exists(self) -> bool:
        return self._path.exists()

    # ---- turn management ----

    def begin_turn(self, *, turn_id: str, prompt: str) -> _TurnRecord:
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        turn = _TurnRecord(
            id=turn_id,
            prompt=prompt,
            started_at=now,
        )
        self.turns.append(turn)
        return turn

    def append_frame(self, turn: _TurnRecord, frame: TurnFrame) -> None:
        turn.frames.append(frame)

    def finish_turn(self, turn: _TurnRecord, *, session_id: str | None = None) -> None:
        turn.finished_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        if session_id is not None:
            turn.session_id = session_id
            self.resume_session_id = session_id

    def last_turn(self) -> _TurnRecord | None:
        return self.turns[-1] if self.turns else None


# ---------------------------------------------------------------------------
# HeadlessAssistantManager — per-turn headless session lifecycle
# ---------------------------------------------------------------------------

@dataclass
class _InFlightTurn:
    """Bookkeeping for a turn currently executing."""
    turn_id: str
    prompt: str
    turn_record: _TurnRecord
    frames_sent: int = 0
    finished: bool = False
    # In-flight frames buffered in the conversation log (for finish-in-background).
    buffered_frames: list[TurnFrame] = field(default_factory=list)


class HeadlessAssistantManager:
    """Manages per-workspace headless assistant conversations.

    Parallel to ``AssistantSessionManager`` (frozen PTY path) — new field on
    ``DaemonState``.
    """

    def __init__(self) -> None:
        self._conversations: dict[str, AssistantConversation] = {}
        self._in_flight: dict[str, _InFlightTurn] = {}  # keyed by workspace str
        self._lock = asyncio.Lock()

    # ---- conversation access ----

    async def get_conversation(self, *, workspace: Path) -> AssistantConversation:
        key = str(workspace.resolve())
        async with self._lock:
            if key in self._conversations:
                conv = self._conversations[key]
            else:
                conv = AssistantConversation(workspace)
                conv.load()
                self._conversations[key] = conv
            return conv

    async def close_workspace(self, workspace: Path) -> None:
        """Buffer any in-flight turn then persist."""
        key = str(workspace.resolve())
        async with self._lock:
            inflight = self._in_flight.pop(key, None)
            if inflight is not None and not inflight.finished:
                self._flush_buffered(inflight)
            conv = self._conversations.pop(key, None)
            if conv is not None:
                conv.save()

    async def close_all(self) -> None:
        """Shutdown: buffer all in-flight turns and persist all conversations."""
        async with self._lock:
            for key, inflight in list(self._in_flight.items()):
                if not inflight.finished:
                    self._flush_buffered(inflight)
            self._in_flight.clear()
            for conv in self._conversations.values():
                conv.save()
            self._conversations.clear()

    # ---- in-flight turn tracking ----

    async def start_inflight(
        self,
        *,
        workspace: Path,
        turn_id: str,
        prompt: str,
        turn_record: _TurnRecord,
    ) -> None:
        key = str(workspace.resolve())
        async with self._lock:
            self._in_flight[key] = _InFlightTurn(
                turn_id=turn_id,
                prompt=prompt,
                turn_record=turn_record,
            )

    async def finish_inflight(
        self,
        *,
        workspace: Path,
        session_id: str | None = None,
    ) -> None:
        key = str(workspace.resolve())
        async with self._lock:
            inflight = self._in_flight.pop(key, None)
            if inflight is not None:
                inflight.finished = True
                # Flush buffered frames into the turn record before persisting.
                self._flush_buffered(inflight)
                conv = self._conversations.get(key)
                if conv is not None:
                    conv.finish_turn(inflight.turn_record, session_id=session_id)
                    conv.save()

    async def buffer_inflight_frame(
        self, *, workspace: Path, frame: TurnFrame
    ) -> None:
        key = str(workspace.resolve())
        async with self._lock:
            inflight = self._in_flight.get(key)
            if inflight is not None:
                inflight.buffered_frames.append(frame)
                inflight.frames_sent += 1

    def _flush_buffered(self, inflight: _InFlightTurn) -> None:
        """Append buffered frames to the turn record (non-async, lock held).

        Called both from finish_inflight (normal turn completion) and from
        close_workspace/close_all (dock-disconnect buffering).
        """
        for frame in inflight.buffered_frames:
            inflight.turn_record.frames.append(frame)


# ---------------------------------------------------------------------------
# Headless turn runner
# ---------------------------------------------------------------------------

async def run_headless_turn(
    *,
    manager: HeadlessAssistantManager,
    adapter: HeadlessAdapter,
    workspace: Path,
    prompt: str,
    conversation: AssistantConversation,
    permission_posture: PermissionPosture,
    frame_sender: Callable[[TurnFrame], asyncio.awaitable],
) -> str | None:
    """Execute one headless turn: spawn subprocess, stream TurnFrames, persist.

    Returns the executor's session-id (for resume continuity) or None.
    """
    turn_id = f"turn-{int(time.time() * 1000)}"
    turn_record = conversation.begin_turn(turn_id=turn_id, prompt=prompt)
    await manager.start_inflight(
        workspace=workspace,
        turn_id=turn_id,
        prompt=prompt,
        turn_record=turn_record,
    )

    try:
        argv = adapter.build_turn_argv(
            prompt=prompt,
            resume_id=conversation.resume_session_id,
            permission_posture=permission_posture,
        )
    except Exception as exc:
        error_frame = TurnFrame.error(message=f"Failed to build argv: {exc}")
        await frame_sender(error_frame)
        await manager.buffer_inflight_frame(workspace=workspace, frame=error_frame)
        await manager.finish_inflight(workspace=workspace)
        return None

    # Send turn_start frame.
    start_frame = TurnFrame.turn_start()
    await frame_sender(start_frame)
    await manager.buffer_inflight_frame(workspace=workspace, frame=start_frame)

    # Send working status.
    working_frame = TurnFrame.status(code="working")
    await frame_sender(working_frame)

    session_id: str | None = None

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(workspace),
        )
    except OSError as exc:
        error_frame = TurnFrame.error(message=f"Failed to spawn executor: {exc}")
        await frame_sender(error_frame)
        await manager.buffer_inflight_frame(workspace=workspace, frame=error_frame)
        await manager.finish_inflight(workspace=workspace)
        return None

    try:
        async for line in proc.stdout:  # type: ignore
            line_str = line.decode(errors="replace")
            frame = adapter.parse_event(line_str)
            if frame is not None:
                await frame_sender(frame)
                await manager.buffer_inflight_frame(workspace=workspace, frame=frame)
                extracted = adapter.extract_session_id(frame)
                if extracted is not None:
                    session_id = extracted

        await proc.wait()

        # Drain any buffered frames the adapter accumulated from
        # multi-content-block messages (e.g. assistant with text + 2
        # tool_use blocks).  parse_event emits one frame per call;
        # remainder sits in the adapter's internal buffer and must be
        # flushed at EOF.
        for drained in adapter.drain_pending_frames():
            await frame_sender(drained)
            await manager.buffer_inflight_frame(workspace=workspace, frame=drained)
            extracted = adapter.extract_session_id(drained)
            if extracted is not None:
                session_id = extracted

        # Capture session_id from adapters that track it internally from
        # terminal events (e.g., ClaudeAdapter result event).  These events
        # return None from parse_event, so extract_session_id is never
        # called during the stdout loop.
        extra_sid = adapter.extract_session_id(TurnFrame.turn_end())
        if extra_sid is not None:
            session_id = extra_sid

        # Drain stderr for diagnostics only — no frames emitted from it.
        stderr_data = await proc.stderr.read()
        if stderr_data and proc.returncode != 0:
            stderr_str = stderr_data.decode(errors="replace")[-2000:]
            error_frame = TurnFrame.error(
                message=f"Executor exited with code {proc.returncode}: {stderr_str}"
            )
            await frame_sender(error_frame)
            await manager.buffer_inflight_frame(workspace=workspace, frame=error_frame)

    except Exception as exc:
        error_frame = TurnFrame.error(message=f"Turn execution failed: {exc}")
        await frame_sender(error_frame)
        await manager.buffer_inflight_frame(workspace=workspace, frame=error_frame)
    finally:
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()

    # Send turn_end frame.
    usage: dict[str, Any] = {}
    if session_id:
        usage["session_id"] = session_id
    # Include terminal usage from adapters that capture it (e.g.,
    # ClaudeAdapter._terminal_usage from the result event).
    terminal_usage = getattr(adapter, '_terminal_usage', None)
    if isinstance(terminal_usage, dict):
        for k, v in terminal_usage.items():
            if k not in usage:
                usage[k] = v
    end_frame = TurnFrame.turn_end(usage=usage if usage else None)
    await frame_sender(end_frame)
    await manager.buffer_inflight_frame(workspace=workspace, frame=end_frame)

    # Send ready status.
    ready_frame = TurnFrame.status(code="ready")
    await frame_sender(ready_frame)

    await manager.finish_inflight(workspace=workspace, session_id=session_id)
    return session_id
