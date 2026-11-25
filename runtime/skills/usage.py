"""Lossy, bounded observations of skill use; never an execution/delivery gate."""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import sqlite3
import stat
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.infrastructure.database import Database
    from runtime.orchestrator.executors import ExecutorResult

logger = logging.getLogger(__name__)
MAX_BYTES = 4 * 1024 * 1024
# Production prompt/metadata lines measured up to 318,269 bytes (THR-203).
# Keep headroom within the unchanged total budget; never skip hidden boundaries.
MAX_LINE_BYTES = 512 * 1024
MAX_LINES = 20_000
MAX_SIGNALS = 256
MAX_SECONDS = 0.1
_SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")
_PROVIDER_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,127}\Z")
_WORKERS = threading.BoundedSemaphore(2)


@dataclass(frozen=True, repr=False)
class SkillUseCapture:
    provider: str
    workspace: Path
    prompt: str
    stdout: str


@dataclass(frozen=True)
class SkillUseSignal:
    slug: str
    kind: str
    source_ref: str


def capture_skill_use(
    provider: str, workspace: Path, prompt: str | None, stdout: str,
) -> SkillUseCapture | None:
    """Retain existing strings only within a constant bound; do no parsing/I/O."""
    if provider not in {"claude", "codex"}:
        return None
    if not prompt or len(prompt) > MAX_BYTES or len(stdout) > MAX_BYTES:
        logger.warning("skill use observation unavailable: capture bound")
        return None
    return SkillUseCapture(provider, workspace, prompt, stdout if provider == "codex" else "")


def _check_time(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ValueError("skill use time bound")


def _read_transcript(path: Path, deadline: float) -> bytes:
    """Open only a literal expected path, with no-follow on every component."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("skill use transcript path")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:-1]:
            _check_time(deadline)
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(leaf)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_BYTES:
                raise ValueError("skill use transcript type/size")
            chunks = []
            size = 0
            while True:
                _check_time(deadline)
                chunk = os.read(leaf, min(64 * 1024, MAX_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_BYTES:
                    raise ValueError("skill use transcript byte bound")
            after = os.fstat(leaf)
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size, after.st_mtime_ns, after.st_ctime_ns
            ):
                raise ValueError("skill use transcript changed")
            return b"".join(chunks)
        finally:
            os.close(leaf)
    finally:
        os.close(fd)


def _events(data: bytes, deadline: float) -> list[dict]:
    if len(data) > MAX_BYTES:
        raise ValueError("skill use byte bound")
    lines = data.split(b"\n", MAX_LINES)
    if lines and lines[-1] == b"":
        lines.pop()
    if len(lines) > MAX_LINES:
        raise ValueError("skill use line bound")
    events = []
    for line in lines:
        _check_time(deadline)
        if len(line) > MAX_LINE_BYTES:
            raise ValueError("skill use line byte bound")
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError("skill use event shape")
        events.append(value)
    _check_time(deadline)
    return events


def _claude_signals(
    events: list[dict], prompt: str, provider_id: str, deadline: float,
) -> list[SkillUseSignal]:
    # Exact submitted user text binds a resumed transcript to this invocation.
    # Tool-result user entries are lists and do not end the invocation window.
    matching = [i for i, event in enumerate(events)
                if event.get("type") == "user"
                and event.get("message", {}).get("content") == prompt]
    if len(matching) != 1 or events[matching[0]].get("sessionId") != provider_id:
        raise ValueError("skill use prompt boundary unavailable/ambiguous")
    signals = []
    for event in events[matching[0] + 1:]:
        _check_time(deadline)
        if event.get("type") not in {"user", "assistant"}:
            continue
        if event.get("sessionId") != provider_id:
            raise ValueError("skill use provider identity mismatch")
        message = event.get("message", {})
        if event.get("type") == "user" and isinstance(message.get("content"), str):
            break
        if event.get("type") != "assistant":
            continue
        for block in message.get("content", []):
            _check_time(deadline)
            if not isinstance(block, dict) or block.get("type") != "tool_use" or block.get("name") != "Skill":
                continue
            slug = block.get("input", {}).get("skill")
            tool_id = block.get("id")
            if not isinstance(slug, str) or not _SLUG.fullmatch(slug):
                continue
            if not isinstance(tool_id, str) or not _PROVIDER_ID.fullmatch(tool_id):
                raise ValueError("skill use tool identity unavailable")
            if len(signals) >= MAX_SIGNALS:
                raise ValueError("skill use signal bound")
            signals.append(SkillUseSignal(slug, "invoked", f"claude:{provider_id}:{tool_id}"))
    return signals


def _read_paths(command: str) -> list[str]:
    """Recognize only simple read commands, never arbitrary shell substrings."""
    if any(c in command for c in "`$\n#()"):
        return []

    def split(text: str) -> list[str]:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";|&<>")
        lexer.whitespace_split = True
        return list(lexer)

    words = split(command)
    if len(words) == 3 and Path(words[0]).name in {"bash", "sh", "zsh"} and words[1] in {"-c", "-lc"}:
        words = split(words[2])
    if not words:
        return []
    name = Path(words[0]).name
    args = words[1:]
    if name == "cat":
        paths = args
    elif name == "sed" and args[:1] == ["-n"]:
        args = args[1:]
        expressions = []
        if args[:1] == ["-e"]:
            while len(args) >= 2 and args[0] == "-e":
                expressions.append(args[1])
                args = args[2:]
        elif args:
            expressions.append(args.pop(0))
        if not expressions or not all(re.fullmatch(r"\d+(?:,\d+)?p(?:;\d+(?:,\d+)?p)*", e) for e in expressions):
            return []
        paths = args
    elif name in {"head", "tail"}:
        if args and args[0] in {"-n", "-c"}:
            if len(args) < 2 or not re.fullmatch(r"[+-]?\d+", args[1]) or int(args[1]) == 0:
                return []
            args = args[2:]
        elif args and args[0].startswith("-") and args[0] != "--":
            count = re.fullmatch(r"(?:-[nc]?|--(?:lines|bytes)=)([+-]?\d+)", args[0])
            if not count or int(count[1]) == 0:
                return []
            args = args[1:]
        paths = args
    elif name == "nl":
        if args[:1] == ["-ba"]:
            args = args[1:]
        elif args[:2] == ["-b", "a"]:
            args = args[2:]
        paths = args
    else:
        return []
    if paths[:1] == ["--"]:
        paths = paths[1:]
    return paths if all(not p.startswith("-") and not any(c in p for c in ";|&<>") for p in paths) else []


def _codex_signals(
    events: list[dict], workspace: Path, provider_id: str, deadline: float,
) -> list[SkillUseSignal]:
    started = [e.get("thread_id") for e in events if e.get("type") == "thread.started"]
    if started != [provider_id]:
        raise ValueError("skill use codex identity mismatch")
    signals = []
    for event in events:
        _check_time(deadline)
        item = event.get("item", {})
        if (event.get("type") != "item.completed" or item.get("type") != "command_execution"
                or item.get("status") != "completed" or type(item.get("exit_code")) is not int
                or item["exit_code"] != 0):
            continue
        item_id, command = item.get("id"), item.get("command")
        if not isinstance(item_id, str) or not _PROVIDER_ID.fullmatch(item_id) or not isinstance(command, str):
            raise ValueError("skill use command shape")
        # exec --json currently omits cwd. Honor explicit metadata if supplied
        # by a provider version; unknown/foreign cwd never gets a relative read.
        relative_cwd = all(
            isinstance(record[key], str) and Path(record[key]).is_absolute()
            and ".." not in Path(record[key]).parts and Path(record[key]) == workspace
            for record in (event, item) for key in ("cwd", "workdir") if key in record
        )
        seen = set()
        for raw_path in _read_paths(command):
            path = Path(raw_path)
            if ".." in path.parts:
                continue
            if not path.is_absolute():
                # Observational fallback: literal workspace skill paths only,
                # corroborated by an existing file; never resolve symlink roots.
                if not relative_cwd or not (workspace / path).is_file():
                    continue
                path = workspace / path
            for root in (".agents", ".claude"):
                try:
                    parts = path.relative_to(workspace / root / "skills").parts
                except ValueError:
                    continue
                if len(parts) == 2 and parts[1] == "SKILL.md" and _SLUG.fullmatch(parts[0]) and parts[0] not in seen:
                    if len(signals) >= MAX_SIGNALS:
                        raise ValueError("skill use signal bound")
                    signals.append(SkillUseSignal(parts[0], "opened_skill_file", f"codex:{item_id}"))
                    seen.add(parts[0])
    return signals


def parse_skill_use(capture: SkillUseCapture, provider_id: str) -> list[SkillUseSignal]:
    """All-or-nothing parse; callers discard every signal on any failure."""
    deadline = time.monotonic() + MAX_SECONDS
    if not _PROVIDER_ID.fullmatch(provider_id):
        raise ValueError("skill use provider id")
    if capture.provider == "claude":
        project = re.sub(r"[^a-zA-Z0-9]", "-", str(capture.workspace))
        path = Path.home() / ".claude" / "projects" / project / f"{provider_id}.jsonl"
        data = _read_transcript(path, deadline)
        signals = _claude_signals(_events(data, deadline), capture.prompt, provider_id, deadline)
    elif capture.provider == "codex":
        signals = _codex_signals(_events(capture.stdout.encode("utf-8"), deadline), capture.workspace, provider_id, deadline)
    else:
        return []
    if len(signals) > MAX_SIGNALS:
        raise ValueError("skill use signal bound")
    _check_time(deadline)
    return signals


def _record_skill_use(
    db_path: Path, capture: SkillUseCapture, *, org_slug: str, agent_name: str,
    task_id: str | None, session_context: str, session_id: str, executor: str,
    provider_id: str,
) -> None:
    # File I/O and parsing finish before any database ownership is acquired.
    signals = parse_skill_use(capture, provider_id)
    if not signals:
        return
    bundled = {signal.slug for signal in signals if any(
        (root / signal.slug / "SKILL.md").is_file()
        for root in (Path(__file__).parent / "bundled", Path(__file__).parent)
    )}
    deadline = time.monotonic() + MAX_SECONDS
    # Existing org database only, independent connection, zero busy waiting.
    # A busy/unavailable DB loses the observation, never the session outcome.
    conn = sqlite3.connect(db_path.as_uri() + "?mode=rw", uri=True, timeout=0)
    try:
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 100)
        conn.execute("BEGIN IMMEDIATE")
        observed_at = datetime.now(timezone.utc).isoformat()
        for signal in signals:
            _check_time(deadline)
            custom = conn.execute(
                "SELECT id FROM custom_skills WHERE org_slug=? AND slug=?", (org_slug, signal.slug),
            ).fetchone()
            source = "custom" if custom else "bundled" if signal.slug in bundled else "unknown"
            conn.execute(
                "INSERT INTO skill_use_events (org_slug, skill_slug, skill_source, custom_skill_id, "
                "agent_name, task_id, session_context, session_id, executor, signal_kind, "
                "provider_session_id, observed_at, source_ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(org_slug, session_id, executor, source_ref, skill_slug) DO NOTHING",
                (org_slug, signal.slug, source, custom[0] if custom else None, agent_name, task_id,
                 session_context, session_id, executor, signal.kind, provider_id, observed_at, signal.source_ref),
            )
        _check_time(deadline)
        conn.commit()
    finally:
        conn.close()  # Includes rollback of any incomplete observation batch.


def observe_skill_use(
    db: Database, result: ExecutorResult, *, org_slug: str, agent_name: str,
    task_id: str | None, session_context: str, executor: str,
) -> None:
    """Best effort off the outcome path; at most two process-wide workers."""
    acquired = False
    try:
        capture = getattr(result, "skill_use_capture", None)
        provider_id = getattr(result, "agent_session_id", None)
        if capture is None or not provider_id:
            return
        if not _WORKERS.acquire(blocking=False):
            logger.warning("skill use observation unavailable: worker bound")
            return
        acquired = True

        def run() -> None:
            try:
                _record_skill_use(
                    db.db_path, capture, org_slug=org_slug, agent_name=agent_name, task_id=task_id,
                    session_context=session_context, session_id=result.session_id,
                    executor=executor, provider_id=provider_id,
                )
            except Exception as exc:
                # Never log transcript bytes, commands, paths or prompts.
                logger.warning("skill use observation unavailable: %s", type(exc).__name__)
            finally:
                _WORKERS.release()

        threading.Thread(target=run, name="skill-use-observer", daemon=True).start()
    except Exception as exc:
        if acquired:
            _WORKERS.release()
        logger.warning("skill use observation unavailable: %s", type(exc).__name__)
