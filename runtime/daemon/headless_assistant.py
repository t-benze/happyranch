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

# ---------------------------------------------------------------------------
# TurnFrame vocabulary
# ---------------------------------------------------------------------------

class TurnFrame(BaseModel):
    """Normalized frame sent from backend to dock over WebSocket (JSON)."""
    model_config = ConfigDict(extra="forbid")

    type: str  # turn_start | text_delta | tool_call | tool_result | turn_end | status | error
    role: str | None = None           # "assistant" (turn_start, turn_end)
    text: str | None = None           # text_delta payload
    name: str | None = None           # tool_call / tool_result name
    input: dict[str, Any] | None = None  # tool_call input payload
    ok: bool | None = None            # tool_result outcome
    usage: dict[str, Any] | None = None  # turn_end token usage
    code: str | None = None           # status code (ready | working | session_closed | error)
    detail: str | None = None         # status detail (optional)
    message: str | None = None        # error message

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


# ---------------------------------------------------------------------------
# Permission posture (forward declaration for PR-3/PR-4)
# ---------------------------------------------------------------------------

@dataclass
class PermissionPosture:
    """Permission configuration for headless executor invocations.

    PR-1 ships this as an empty placeholder.  Real posture objects land in
    PR-3 (claude --allowedTools / --permission-mode) and PR-4 (codex --sandbox /
    approval policy).  The null adapter ignores it.
    """
    pass


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
                self._buffer_inflight_frames(inflight)
            conv = self._conversations.pop(key, None)
            if conv is not None:
                conv.save()

    async def close_all(self) -> None:
        """Shutdown: buffer all in-flight turns and persist all conversations."""
        async with self._lock:
            for key, inflight in list(self._in_flight.items()):
                if not inflight.finished:
                    self._buffer_inflight_frames(inflight)
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

    def _buffer_inflight_frames(self, inflight: _InFlightTurn) -> None:
        """Append buffered frames to the conversation log (non-async, lock held)."""
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
    usage: dict[str, Any] | None = None
    if session_id:
        usage = {"session_id": session_id}
    end_frame = TurnFrame.turn_end(usage=usage)
    await frame_sender(end_frame)
    await manager.buffer_inflight_frame(workspace=workspace, frame=end_frame)

    # Send ready status.
    ready_frame = TurnFrame.status(code="ready")
    await frame_sender(ready_frame)

    await manager.finish_inflight(workspace=workspace, session_id=session_id)
    return session_id
