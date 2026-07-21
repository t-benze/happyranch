from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from runtime.config import Settings
from runtime.models import TokenUsage
from runtime.orchestrator._paths import OrgPaths

if TYPE_CHECKING:
    from runtime.orchestrator.throttle import OnThrottleEvent

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
    # True when the subprocess output matched a known provider rate-limit
    # signature (issue #85). Set centrally in ``_run_command`` so every executor
    # exposes one normalized field; ``run_step._classify_failure_kind`` prefers
    # it over its legacy stdout/stderr string heuristic, and the per-provider
    # throttle uses it to drive 429 backoff.
    rate_limited: bool = False


_TAIL_BYTES = 2000

# Standard tool directories prepended to PATH at daemon startup so executor
# binaries resolve under Finder/launchd (which pass PATH=/usr/bin:/bin).
# Overridable in tests via monkeypatch of the module-level list.
_STANDARD_TOOL_DIRS: list[str] = [
    "/opt/homebrew/bin",
    "/usr/local/bin",
    os.path.expanduser("~/.local/bin"),
]


def _normalize_path() -> None:
    """Prepend standard tool directories to ``os.environ['PATH']`` if absent.

    Called once at daemon startup so executor binaries (claude, codex,
    opencode, pi) are findable even when the daemon was launched by
    Finder/launchd with PATH=/usr/bin:/bin (issue #254).

    When running as a PyInstaller-frozen bundle (bundled Mac app),
    prepends the bundled CLI directory (``os.path.dirname(sys.executable)``)
    at the very front so bare-name ``happyranch`` resolves to the bundled
    binary instead of a stale ``~/.local/bin/happyranch`` (THR-085).
    The ``sys.frozen`` gate is the canonical frozen-detection signal —
    the Swift-side ``PACKAGING_MODE=bundled`` env var is stripped by
    EnvironmentSanitizer before the daemon child launches.

    Idempotent: dirs already present are not duplicated.
    """
    current = os.environ.get("PATH", "")
    entries = current.split(":") if current else []

    # Build prepends in priority order: bundled CLI dir first (frozen only),
    # then standard tool dirs, then the original PATH entries.
    prepends: list[str] = []

    # When frozen (bundled Mac app), prepend the bundled CLI directory
    # FIRST so bare-name happyranch resolves to the bundled binary.
    # Dev/headless/CI daemons are NOT frozen, so PATH is unchanged.
    if getattr(sys, 'frozen', False):
        bundled_cli_dir = os.path.dirname(sys.executable)
        if bundled_cli_dir:
            # Remove ALL existing copies of the bundled dir from entries —
            # if it's already present later in PATH (e.g. behind ~/.local/bin),
            # a simple "if absent" guard would skip prepending and leave the
            # stale entry ahead of ours (THR-085 msg72). Strip duplicates, then
            # prepend exactly ONE copy at index 0 so bare-name happyranch
            # always resolves to the bundled binary.
            entries = [e for e in entries if e != bundled_cli_dir]
            prepends.append(bundled_cli_dir)

    # Standard tool dirs: prepend only those not already present.
    for d in _STANDARD_TOOL_DIRS:
        if d not in entries and d not in prepends:
            prepends.append(d)

    if prepends:
        os.environ["PATH"] = ":".join(prepends + entries)


class ExecutorBinaryBlocked(RuntimeError):
    """Raised when an executor binary cannot be resolved from the machine-local
    registry AND is not on PATH — or when a stored path is stale.

    The message is always actionable: it names the executor kind and tells the
    operator exactly how to fix it via ``happyranch executor-binaries register``.
    """


def _resolve_binary(cli_path: str) -> str:
    """Resolve an executor binary name to an absolute path.

    Stored-path-first resolution (THR-085):

    1. If ``cli_path`` is already absolute, trust it as-is (founder override).
    2. Consult the machine-local binary-path registry. If the kind is registered:
       a. Validate the stored path still exists and is executable.
       b. Valid → use it.
       c. Invalid → raise ``ExecutorBinaryBlocked`` naming the fix.
    3. If the kind is NOT registered, fall back to ``shutil.which`` over PATH.
       a. Found → NON-SILENT: log a warning that this binary was resolved from
          PATH and should be registered.
       b. Not found → raise ``ExecutorBinaryBlocked`` naming the fix.
    """
    if os.path.isabs(cli_path):
        # Founder-configured absolute path — trust it as-is.
        return cli_path

    # Check the machine-local registry first.
    from runtime.orchestrator.executor_binary_registry import (
        get_binary,
        is_binary_valid,
    )

    stored = get_binary(cli_path)
    if stored is not None:
        if is_binary_valid(stored):
            return stored
        # Stored path is stale — actionable block, NO silent PATH fallback.
        raise ExecutorBinaryBlocked(
            f"Executor binary '{cli_path}' is registered at {stored!r} "
            f"but the path does not exist or is not executable. "
            f"Re-register it: happyranch executor-binaries register {cli_path} --path <absolute-path>"
        )

    # Not registered — fall back to PATH (non-silent).
    resolved = shutil.which(cli_path)
    if resolved is None:
        raise ExecutorBinaryBlocked(
            f"Executor '{cli_path}' is not registered and not found on PATH. "
            f"Register it: happyranch executor-binaries register {cli_path} --path <absolute-path>"
        )
    logger.warning(
        "Executor '%s' has no stored binary path; resolved from PATH as %s. "
        "Register it for reliable resolution: "
        "happyranch executor-binaries register %s --path %s",
        cli_path, resolved, cli_path, resolved,
    )
    return resolved


def _callee_env() -> dict[str, str]:
    """Return a copy of ``os.environ`` suitable for passing as ``env=``
    to ``subprocess.Popen`` so the child inherits the daemon's normalized
    PATH instead of the stripped Finder/launchd PATH."""
    return dict(os.environ)


def _claude_canonical_model(obj: dict) -> str | None:
    """Resolve the session's model id from a Claude result envelope.

    Claude Code's `--output-format json` result no longer carries a top-level
    ``model`` string (confirmed against Claude Code 2.1.x live output); the
    model id(s) live under ``modelUsage``, keyed by id. When a session spans
    multiple models, pick the one with the most output_tokens — the
    "canonical model this session ran on", mirroring the opencode last-model
    doctrine. Falls back to a legacy top-level ``model`` for older envelopes.
    """
    model_usage = obj.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        def _out(entry: object) -> int:
            return entry.get("outputTokens") or 0 if isinstance(entry, dict) else 0

        best_key = max(model_usage, key=lambda k: _out(model_usage[k]))
        if isinstance(best_key, str) and best_key:
            return best_key
    legacy = obj.get("model")
    return legacy if isinstance(legacy, str) and legacy else None


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
            model=_claude_canonical_model(obj) if isinstance(obj, dict) else None,
            usage_raw_json=stdout[:_TAIL_BYTES],
        )
    return TokenUsage(
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        cache_read_tokens=usage.get("cache_read_input_tokens"),
        cache_creation_tokens=usage.get("cache_creation_input_tokens"),
        reasoning_tokens=None,
        model=_claude_canonical_model(obj),
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

    Walks events, picks the last `turn.completed` — the terminal event that
    carries the cumulative ``usage`` object in Codex >= 0.137 (confirmed
    against codex-cli 0.137.0 and 0.139.0 live output). Returns None on empty
    stdout, TokenUsage with NULL token fields if no terminal usage event is
    found (forensic preservation), populated TokenUsage on success.

    Note: Codex `exec --json` v0.137.0 emits no model field on any event, so
    ``model`` stays NULL (read defensively in case a later version adds it).
    Verify the terminal event name/keys against the running Codex CLI version
    during integration testing — if the schema changes, only this function
    needs updating.

    **Codex ``input_tokens`` includes ``cached_input_tokens`` (issue #216
    CONFIRMED).** Live instrumentation (code_reviewer turn: input 4,412,984
    with cached 4,307,072) proves Codex follows the OpenAI convention where
    ``input_tokens`` is the inclusive total. This function normalizes on
    ingest: ``input_tokens`` = max(input - cached, 0), so the stored value is
    net-fresh input (consistent with Claude's semantics). ``cache_read_tokens``
    is preserved as-is. Normalization is forward-only; historical rows are NOT
    retro-corrected.
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
        if isinstance(event, dict) and event.get("type") == "turn.completed":
            last_complete = event
    if last_complete is None:
        return TokenUsage(usage_raw_json=stdout[:_TAIL_BYTES])
    usage = last_complete.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    raw_input = usage.get("input_tokens")
    cached = usage.get("cached_input_tokens")
    # Fix B (issue #216): Codex input_tokens is inclusive of cached_input_tokens.
    # Normalize to net-fresh so churn = input+output+reasoning is apples-to-apples
    # across executors and cache is never double-counted.
    if isinstance(raw_input, int) and isinstance(cached, int):
        net_input: int | None = max(raw_input - cached, 0)
    else:
        net_input = raw_input
    return TokenUsage(
        input_tokens=net_input,
        output_tokens=usage.get("output_tokens"),
        cache_read_tokens=usage.get("cached_input_tokens"),
        cache_creation_tokens=None,
        reasoning_tokens=usage.get("reasoning_output_tokens"),
        model=last_complete.get("model"),
        usage_raw_json=json.dumps(last_complete),
    )


def _parse_opencode_usage(stdout: str) -> TokenUsage | None:
    """Parse opencode `--format json` stdout into TokenUsage.

    Supports two output shapes:
    - **Old format** (opencode < 1.14): A single JSON object with
      ``messages[].usage`` per assistant turn. Sums assistant-role message
      usage; model from the last assistant message.
    - **New JSONL format** (opencode >= 1.14.31): NDJSON stream of events.
      Walks lines, picks the last ``step_finish`` event whose ``part`` carries
      ``tokens`` (``step_finish.part.tokens``). Falls back to the last
      assistant message event with ``usage`` if no step_finish tokens found.
    """
    if not stdout or not stdout.strip():
        return None
    stripped = stdout.strip()

    # --- Path A: Old single-JSON-object format ---
    # Try parsing as a single JSON object first (old format). If the stdout
    # starts with '{' but isn't a single JSON object (e.g., JSONL), fall
    # through to Path B instead of returning a raw-only TokenUsage.
    try:
        obj = json.loads(stripped)
    except json.JSONDecodeError:
        pass  # not a single JSON object; try JSONL below
    else:
        if isinstance(obj, dict):
            messages = obj.get("messages") or []
            assistant_msgs = [
                m for m in messages
                if isinstance(m, dict) and m.get("role") == "assistant"
                and isinstance(m.get("usage"), dict)
            ]
            if assistant_msgs:
                def _sum(field: str) -> int | None:
                    vals = [m["usage"].get(field) for m in assistant_msgs]
                    nums = [v for v in vals if isinstance(v, int) and not isinstance(v, bool)]
                    return sum(nums) if nums else None
                last_model = next(
                    (m.get("model") for m in reversed(assistant_msgs) if m.get("model")),
                    None,
                )
                return TokenUsage(
                    input_tokens=_sum("input_tokens"),
                    output_tokens=_sum("output_tokens"),
                    cache_read_tokens=_sum("cache_read_tokens"),
                    cache_creation_tokens=_sum("cache_write_tokens"),
                    reasoning_tokens=_sum("reasoning_tokens"),
                    model=last_model,
                    usage_raw_json=json.dumps([m["usage"] for m in assistant_msgs]),
                )
        # Single JSON but not the expected shape; fall through to JSONL.

    # --- Path B: New JSONL format (opencode >= 1.14.31) ---
    # Walk lines, collect step_finish tokens and assistant usage events.
    step_finish_tokens: dict | None = None
    assistant_usages: list[dict] = []
    last_model: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        # Track model from any event that carries it.
        if isinstance(event.get("model"), str):
            last_model = event["model"]
        etype = event.get("type")
        if etype == "step_finish":
            part = event.get("part")
            if isinstance(part, dict) and "tokens" in part:
                step_finish_tokens = part["tokens"]
        elif etype == "assistant" and isinstance(event.get("usage"), dict):
            assistant_usages.append(event["usage"])

    if isinstance(step_finish_tokens, dict):
        tokens = step_finish_tokens
        return TokenUsage(
            input_tokens=tokens.get("input_tokens"),
            output_tokens=tokens.get("output_tokens"),
            cache_read_tokens=tokens.get("cache_read_tokens"),
            cache_creation_tokens=tokens.get("cache_write_tokens"),
            reasoning_tokens=tokens.get("reasoning_tokens"),
            model=last_model,
            usage_raw_json=json.dumps(tokens),
        )
    if assistant_usages:
        # Fallback: sum assistant usage events from JSONL format.
        def _sum_field(field: str) -> int | None:
            vals = [u.get(field) for u in assistant_usages]
            nums = [v for v in vals if isinstance(v, int) and not isinstance(v, bool)]
            return sum(nums) if nums else None
        return TokenUsage(
            input_tokens=_sum_field("input_tokens"),
            output_tokens=_sum_field("output_tokens"),
            cache_read_tokens=_sum_field("cache_read_tokens"),
            cache_creation_tokens=_sum_field("cache_write_tokens"),
            reasoning_tokens=_sum_field("reasoning_tokens"),
            model=last_model,
            usage_raw_json=stdout[:_TAIL_BYTES],
        )
    return TokenUsage(usage_raw_json=stdout[:_TAIL_BYTES])


def _parse_pi_usage(stdout: str) -> TokenUsage | None:
    """Parse Pi `--mode json` stdout into TokenUsage.

    Pi 0.80.2+ emits JSONL events. The terminal events ``message_end`` and
    ``turn_end`` carry final usage at ``message.usage`` with keys:
    ``input``, ``output``, ``cacheRead``, ``cacheWrite``, ``totalTokens``.

    The LAST terminal event's usage wins when both are present.

    Falls back to raw-only preservation when the stdout cannot be parsed
    (original behavior), so successful Pi sessions still leave an auditable
    usage row for forensics.
    """
    if not stdout or not stdout.strip():
        return None
    # Walk JSONL lines for terminal events with usage in message.usage.
    last_usage: dict | None = None
    last_model: str | None = None
    for line in stdout.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") in ("message_end", "turn_end"):
            message = event.get("message")
            if isinstance(message, dict) and isinstance(message.get("usage"), dict):
                last_usage = event["message"]["usage"]
                last_model = event.get("model")
    if last_usage is not None:
        return TokenUsage(
            input_tokens=last_usage.get("input"),
            output_tokens=last_usage.get("output"),
            cache_read_tokens=last_usage.get("cacheRead"),
            cache_creation_tokens=last_usage.get("cacheWrite"),
            reasoning_tokens=last_usage.get("reasoning"),
            model=last_model,
            usage_raw_json=json.dumps(last_usage),
        )
    # Fall back to raw-only preservation (original behavior).
    return TokenUsage(usage_raw_json=stdout[:_TAIL_BYTES])

# ── Generic CLI result-envelope sentinels (THR-107) ────────────────────

_HR_ENVELOPE_BEGIN = "__HR_ENVELOPE_BEGIN__"
_HR_ENVELOPE_END = "__HR_ENVELOPE_END__"


def _parse_generic_cli_usage(stdout: str) -> TokenUsage | None:
    """Parse a custom CLI's stdout for a THR-107 result-envelope.

    Best-effort — mirrors the contract of every built-in parser:
    - Returns None when stdout is empty/whitespace (no parse attempted).
    - Returns TokenUsage with token fields NULL and raw JSON on parser failure
      (forensic preservation — same pattern as _parse_claude_usage:222).

    Algorithm:
    1. Empty stdout → None.
    2. Last occurrence of __HR_ENVELOPE_BEGIN__ via rfind → None if absent.
    3. __HR_ENVELOPE_END__ after begin → raw-only TokenUsage if absent.
    4. JSON parse the block → raw-only TokenUsage on JSONDecodeError.
    5. Validate envelope_version == 1 (int) → raw-only if absent/wrong.
    6. Map token_usage dict to TokenUsage fields with key-name parity.
    7. Top-level model backfills token_usage.model when absent.
    """
    if not stdout or not stdout.strip():
        return None

    # Last envelope wins (rfind).
    begin_pos = stdout.rfind(_HR_ENVELOPE_BEGIN)
    if begin_pos == -1:
        return None

    # Locate the closing sentinel after the begin marker.
    end_pos = stdout.find(_HR_ENVELOPE_END, begin_pos + len(_HR_ENVELOPE_BEGIN))
    if end_pos == -1:
        # Missing END — forensic tail preservation.
        tail = stdout[begin_pos:]
        logger.warning(
            "generic CLI usage parser: missing %s sentinel", _HR_ENVELOPE_END
        )
        return TokenUsage(usage_raw_json=tail[:_TAIL_BYTES])

    # Extract the JSON block between sentinels.
    block = stdout[begin_pos + len(_HR_ENVELOPE_BEGIN) : end_pos].strip()
    if not block:
        return None

    try:
        obj = json.loads(block)
    except json.JSONDecodeError:
        logger.warning("generic CLI usage parser: envelope is not valid JSON")
        return TokenUsage(usage_raw_json=block[:_TAIL_BYTES])

    if not isinstance(obj, dict):
        return TokenUsage(usage_raw_json=block[:_TAIL_BYTES])

    # Validate envelope_version — must be integer 1.
    version = obj.get("envelope_version")
    if version != 1 or not isinstance(version, int) or isinstance(version, bool):
        logger.warning(
            "generic CLI usage parser: envelope_version=%r, expected 1 (int)",
            version,
        )
        return TokenUsage(usage_raw_json=json.dumps(obj))

    # Map token_usage dict to TokenUsage fields.
    token_usage_raw = obj.get("token_usage")
    if not isinstance(token_usage_raw, dict):
        token_usage_raw = {}

    input_tokens = token_usage_raw.get("input_tokens")
    output_tokens = token_usage_raw.get("output_tokens")
    cache_read_tokens = token_usage_raw.get("cache_read_tokens")
    cache_creation_tokens = token_usage_raw.get("cache_creation_tokens")
    reasoning_tokens = token_usage_raw.get("reasoning_tokens")
    model = token_usage_raw.get("model")
    usage_raw_json_val = token_usage_raw.get("usage_raw_json")

    # Coerce int fields (tolerate float → int, reject non-numeric).
    def _to_int(value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value) if value == int(value) else None
        return None

    input_tokens = _to_int(input_tokens)
    output_tokens = _to_int(output_tokens)
    cache_read_tokens = _to_int(cache_read_tokens)
    cache_creation_tokens = _to_int(cache_creation_tokens)
    reasoning_tokens = _to_int(reasoning_tokens)

    # Model coercion: only str|None survives.
    if model is not None and not isinstance(model, str):
        model = None
    if usage_raw_json_val is not None and not isinstance(usage_raw_json_val, str):
        usage_raw_json_val = None

    # Top-level model backfills token_usage.model when absent.
    if model is None:
        top_level_model = obj.get("model")
        if isinstance(top_level_model, str):
            model = top_level_model

    return TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_creation_tokens=cache_creation_tokens,
        reasoning_tokens=reasoning_tokens,
        model=model,
        usage_raw_json=usage_raw_json_val,
    )


def is_rate_limit_signature(text: str) -> bool:
    """True when ``text`` matches a known provider rate-limit signature.

    The single source of truth for rate-limit detection (issue #85). Used by
    ``_run_command`` to set ``ExecutorResult.rate_limited`` across all executors
    and by ``run_step._classify_failure_kind`` as the back-compat string
    fallback — keeping both layers in lock-step. Intentionally matches the
    exact patterns the classifier has always used (Claude's
    "hit your limit · resets at HH:MM" and the generic "rate limit") so the
    normalized field and the legacy heuristic never disagree.
    """
    haystack = (text or "").lower()
    return ("hit your limit" in haystack and "reset" in haystack) or "rate limit" in haystack


def _run_command(
    cmd: list[str],
    workspace: Path,
    session_id: str | None,
    timeout_seconds: int,
    input_text: str | None = None,
    on_started: Callable[[int], None] | None = None,
    usage_parser: Callable[[str], "TokenUsage | None"] | None = None,
    session_id_parser: Callable[[str], "str | None"] | None = None,
    provider: str = "claude",
    on_throttle_event: "OnThrottleEvent | None" = None,
) -> ExecutorResult:
    """Run one agent subprocess under the per-provider throttle (issue #85).

    The Popen+communicate body is wrapped in ``_launch`` and handed to the
    process-wide ``ProviderThrottle``: it acquires a per-``provider`` slot,
    honors inter-launch spacing, and on a detected rate limit releases the slot,
    sleeps the backoff, and re-launches — ``_launch`` is idempotent because a
    rate-limited attempt did no useful work (never called ``report-completion``)
    and ``on_started`` simply re-stamps the new pid into SessionTracker.
    """
    sid = session_id or f"sess-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=True)

    def _launch() -> ExecutorResult:
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
            env=_callee_env(),
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
        # Normalize the rate-limit signal centrally so every provider sets the
        # same field (issue #85). Sniff both streams — providers vary on whether
        # the limit message lands on stdout (Claude, rc=0) or stderr.
        rate_limited = is_rate_limit_signature(full_stdout + "\n" + full_stderr)
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
                rate_limited=rate_limited,
            )
        token_usage: TokenUsage | None = None
        if usage_parser is not None:
            try:
                token_usage = usage_parser(full_stdout)
            except Exception as exc:  # parser must never break the task
                logger.warning("usage parser raised: %s", exc)
                token_usage = None
        # Fix A: Codex `exec --json` and some Pi runs emit no model field on
        # usage events. Record the executor/provider name (e.g. 'codex', 'pi')
        # so by-model rollups show a meaningful label instead of NULL/unknown.
        # The existing MODEL_FIX_CUTOVER_TS/null_codex_sessions scaffolding in
        # database.py handles HISTORICAL NULL rows; this is forward-only.
        if token_usage is not None and token_usage.model is None and provider:
            token_usage.model = provider
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
            rate_limited=rate_limited,
        )

    from runtime.orchestrator.throttle import get_throttle

    return get_throttle().run(provider, _launch, on_throttle_event)


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
    def __init__(self, claude_cli_path: str, permission_mode: str, settings: Settings, paths: OrgPaths | None = None, model_arg: list[str] | None = None) -> None:
        self._cli_path = claude_cli_path
        self._permission_mode = permission_mode
        self._settings = settings
        self._paths = paths
        self._model_arg = model_arg

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
        resume_session_id: str | None = None,
        on_throttle_event: "OnThrottleEvent | None" = None,
        model: str | None = None,
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
            _resolve_binary(self._cli_path),
        ]
        # Model select: inject after binary, before permission flags.
        if model and self._model_arg:
            for elem in self._model_arg:
                cmd.append(elem.replace("{model}", model))
        cmd += [
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
            provider="claude",
            on_throttle_event=on_throttle_event,
        )


class CodexExecutor:
    def __init__(self, codex_cli_path: str, sandbox_mode: str, model_arg: list[str] | None = None) -> None:
        self._cli_path = codex_cli_path
        self._sandbox_mode = sandbox_mode
        self._model_arg = model_arg

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
        on_throttle_event: "OnThrottleEvent | None" = None,
        model: str | None = None,
    ) -> ExecutorResult:
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        cmd = [
            _resolve_binary(self._cli_path),
            "exec",
        ]
        # Model select: inject after binary+subcommand, before sandbox flags.
        if model and self._model_arg:
            for elem in self._model_arg:
                cmd.append(elem.replace("{model}", model))
        cmd += [
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
            provider="codex",
            on_throttle_event=on_throttle_event,
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

    def __init__(self, opencode_cli_path: str, model_arg: list[str] | None = None) -> None:
        self._cli_path = opencode_cli_path
        self._model_arg = model_arg

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
        on_throttle_event: "OnThrottleEvent | None" = None,
        model: str | None = None,
    ) -> ExecutorResult:
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        # opencode >= 1.14.0 rejects --prompt; use positional prompt (issue #216).
        cmd = [
            _resolve_binary(self._cli_path),
            "run",
        ]
        # Model select: inject after binary+subcommand, before --dir/prompt.
        if model and self._model_arg:
            for elem in self._model_arg:
                cmd.append(elem.replace("{model}", model))
        cmd += [
            "--dir",
            str(workspace),
            "--format",
            "json",
            prompt,
        ]
        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            on_started=on_started,
            usage_parser=_parse_opencode_usage,
            provider="opencode",
            on_throttle_event=on_throttle_event,
        )


class PiExecutor:
    """Headless Pi invocation.

    Pi reads ``AGENTS.md`` from the workspace and supports print mode via
    ``-p``. It does not currently provide a HappyRanch-managed permission
    surface like Codex sandbox flags or opencode.json, so process containment
    must be supplied outside this executor if required.
    """

    def __init__(self, pi_cli_path: str, model_arg: list[str] | None = None) -> None:
        self._cli_path = pi_cli_path
        self._model_arg = model_arg

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
        on_throttle_event: "OnThrottleEvent | None" = None,
        model: str | None = None,
    ) -> ExecutorResult:
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        cmd = [
            _resolve_binary(self._cli_path),
        ]
        # Model select: inject after binary, before -p/prompt.
        if model and self._model_arg:
            for elem in self._model_arg:
                cmd.append(elem.replace("{model}", model))
        cmd += [
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
            provider="pi",
            on_throttle_event=on_throttle_event,
        )


class GenericCliExecutor:
    """Executor for registered custom CLI profiles (THR-052).

    Unlike the built-in executors, a GenericCliExecutor does not know the
    CLI's semantics — it builds a subprocess argv from a template with
    supported placeholders and delegates to ``_run_command`` like every
    other executor. No shell string, no concatenation beyond placeholder
    substitution.

    The template argv is a list of strings. Each element may contain
    ``{placeholders}`` which are replaced at launch time:
    - ``{prompt}`` → the full prompt text (passed as a single argv element;
      the underlying CLI is responsible for any shell-safe embedding)
    - ``{timeout_seconds}`` → the timeout in seconds
    - ``{workspace}`` → absolute path to the agent workspace

    The session-lifetime preamble is prepended to the prompt before
    substitution, same as every other executor.
    """

    def __init__(
        self,
        *,
        profile_name: str,
        argv_template: list[str],
        provider: str,
    ) -> None:
        self._profile_name = profile_name
        self._argv_template = list(argv_template)
        self._provider = provider

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
        on_throttle_event: "OnThrottleEvent | None" = None,
        model: str | None = None,
    ) -> ExecutorResult:
        # model is accepted for signature parity but not used — custom
        # profile model_arg is out of scope per founder gate (THR-067).
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        cmd: list[str] = []
        for i, elem in enumerate(self._argv_template):
            elem = elem.replace("{prompt}", prompt)
            elem = elem.replace("{timeout_seconds}", str(timeout_seconds))
            elem = elem.replace("{workspace}", str(workspace))
            # All placeholders resolve to a single string — no splitting.
            # Resolve the first element (the CLI binary) to an absolute path.
            if i == 0:
                elem = _resolve_binary(elem)
            cmd.append(elem)
        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            on_started=on_started,
            usage_parser=_parse_generic_cli_usage,
            provider=self._provider,
            on_throttle_event=on_throttle_event,
        )


AgentExecutor = ClaudeExecutor
