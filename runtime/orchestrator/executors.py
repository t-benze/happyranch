from __future__ import annotations

import json
import logging
import os
import platform
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from runtime.config import Settings
from runtime.models import TokenUsage
from runtime.orchestrator._paths import OrgPaths
from runtime.platform.isolation import (
    PlatformIsolationError,
    detect_platform_isolation,
)

if TYPE_CHECKING:
    from runtime.orchestrator.throttle import OnThrottleEvent
    from runtime.platform.session_backend import LaunchSpec, RunningHandle

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
    # exposes one normalized field; the opaque-failure completion path
    # prefers it over its legacy stdout/stderr string heuristic, and the
    # per-provider throttle uses it to drive 429 backoff.
    rate_limited: bool = False
    # Classified terminal failure reason extracted from structured executor
    # output (e.g. Claude's --output-format json result envelope).  None when
    # no structured terminal result is available — callers fall back to
    # ``error`` (THR-116).  Examples: ``session_limit``,
    # ``transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR``.
    terminal_error: str | None = None


_TAIL_BYTES = 2000

# ── THR-200: pre-spawn prompt-transport guard ────────────────────────────
# Large opaque prompt bodies do not belong in argv: the kernel caps a single
# argv element (Linux MAX_ARG_STRLEN = 128 KiB - 1, measured 131,071 bytes on
# this org's hosts), macOS enforces a combined argv+environment budget, and
# Windows command-line limits are materially smaller. When an argv transport
# is in use (input_text is None), the guard fails deterministically BEFORE
# Popen with a normalized, actionable category instead of letting execve raise
# E2BIG mid-launch. The prompt is NEVER truncated, and the guard is purely a
# transport check: encoded byte size is transport-only — it must never be
# interpreted as a cost or reset policy (see protocol/05b-agent-runtime.md;
# future cost policy should use turn count or cumulative session tokens).
_PROMPT_TRANSPORT_TOO_LARGE = "prompt_transport_too_large"


# Linux MAX_ARG_STRLEN measured on this org's hosts (128 KiB - 1).
_LINUX_MAX_ARGV_ELEMENT_BYTES = 131_071
# macOS does not export its per-argument ceiling; 256 KiB is below the
# smallest documented per-argument limit, so the guard never rejects an
# element the kernel would accept there.
_DARWIN_MAX_ARGV_ELEMENT_BYTES = 262_144
# Windows command-line limits are char-based and much smaller; the runtime
# refuses Windows before Popen anyway, this is belt-and-braces.
_WINDOWS_MAX_ARGV_ELEMENT_BYTES = 16_000


def _max_argv_element_bytes() -> int:
    """Platform-safe conservative per-argument transport limit (encoded bytes).

    Deliberately conservative: the guard converts a platform-dependent E2BIG
    into a deterministic normalized failure BEFORE spawn, so the limit must
    sit at or below the platform's actual per-argument cap. Known-good smaller
    argv executions are preserved because the limit is never below the
    kernel's documented floor for the running platform.
    """
    system = platform.system()
    if system == "Windows":
        return _WINDOWS_MAX_ARGV_ELEMENT_BYTES
    if system == "Darwin":
        return _DARWIN_MAX_ARGV_ELEMENT_BYTES
    return _LINUX_MAX_ARGV_ELEMENT_BYTES


def _prompt_transport_too_large_error(limit: int) -> str:
    return (
        f"prompt_transport_too_large: the invocation prompt cannot be "
        f"transported as a single argv element on this platform (safe "
        f"per-argument limit {limit} encoded bytes). The prompt was NOT "
        f"truncated. Encoded byte size is transport-only — it is not a cost "
        f"or reset policy. Use a stdin-capable transport (claude/pi/codex/"
        f"custom adapters) or reduce the delivered payload."
    )


def is_prompt_transport_too_large(result) -> bool:
    """True when ``result`` is a pre-spawn prompt-transport failure.

    Normalized marker for consumers (thread_runner, wake/dream runners) so a
    deterministic oversized-argv rejection can be surfaced distinctly from
    other launch failures.
    """
    return _PROMPT_TRANSPORT_TOO_LARGE in str(getattr(result, "error", "") or "")

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
    registry — missing entry or stale stored path.

    The message is always actionable: it names the executor kind and tells the
    operator exactly how to fix it via ``happyranch executor-binaries register``.
    """


def _resolve_binary(executor_name: str) -> str:
    """Resolve an executor name to an absolute path via the machine-local
    binary-path registry.

    Registration-only resolution (THR-107 seq155 — hard no-PATH cutover):

    1. Consult the machine-local binary-path registry. If the name is registered:
       a. Validate the stored path still exists and is executable.
       b. Valid → use the stored absolute path.
       c. Invalid (stale) → raise ``ExecutorBinaryBlocked`` naming the fix.
    2. If the name is NOT registered → raise ``ExecutorBinaryBlocked``.

    Never discover, resolve, or auto-pin a PATH executable. An absolute
    filesystem path is never accepted — only executor/profile names
    that map to explicit ``executors.json`` entries.
    """
    from runtime.orchestrator.executor_binary_registry import (
        get_binary,
        is_binary_valid,
    )

    stored = get_binary(executor_name)
    if stored is not None:
        if is_binary_valid(stored):
            return stored
        # Stored path is stale — actionable block, NO silent PATH fallback.
        raise ExecutorBinaryBlocked(
            f"Executor binary '{executor_name}' is registered at {stored!r} "
            f"but the path does not exist or is not executable. "
            f"Re-register it: happyranch executor-binaries register {executor_name} --path <absolute-path>"
        )

    # Not registered — fail closed before subprocess creation.
    # Never discover, resolve, or auto-pin a PATH executable.
    raise ExecutorBinaryBlocked(
        f"Executor '{executor_name}' is not registered. "
        f"Register it: happyranch executor-binaries register {executor_name} --path <absolute-path>"
    )


# Environment variables inherited by the daemon that can steer package
# installation into the canonical shared venv when a child subprocess
# (agent executor, custom adapter, job script) runs an editable install
# or `uv sync`. Stripped from the child environment so the shared venv
# is never accidentally mutated from a disposable worktree.
# See protocol/05b-agent-runtime.md § "Spawn-Environment Invariant".
_ENV_VARS_TO_STRIP: frozenset[str] = frozenset({
    "VIRTUAL_ENV",             # standard venv activation; directs pip/uv
    "UV_PROJECT_ENVIRONMENT",   # uv project environment target override
    "UV_PYTHON",                # uv --python: interpreter into which packages install
    "UV_SYSTEM_PYTHON",         # uv --system: install into system Python environment
})


def _sanitize_child_env(env: dict[str, str]) -> dict[str, str]:
    """Remove inherited venv/uv target vars from *env* in-place.

    Returns *env* for chaining convenience.  Callers pass a fresh dict
    copy (e.g. ``dict(os.environ)``) so the daemon's own environment is
    never mutated.
    """
    for key in _ENV_VARS_TO_STRIP:
        env.pop(key, None)
    return env


def _callee_env(*, org_slug: str | None = None) -> dict[str, str]:
    """Return a copy of ``os.environ`` suitable for passing as ``env=``
    to ``subprocess.Popen`` so the child inherits the daemon's normalized
    PATH instead of the stripped Finder/launchd PATH.

    Strips inherited environment variables that can steer package
    installation into the canonical shared venv (VIRTUAL_ENV,
    UV_PROJECT_ENVIRONMENT, UV_PYTHON, UV_SYSTEM_PYTHON).  PATH and
    required HAPPYRANCH_* runtime variables are preserved.

    When *org_slug* is provided, ``HAPPYRANCH_ORG_SLUG`` is set so executor
    subprocesses can resolve org context without literal ``{ORG_SLUG}``
    substitution in canonical skill bodies.
    """
    env = _sanitize_child_env(dict(os.environ))
    if org_slug is not None:
        env["HAPPYRANCH_ORG_SLUG"] = org_slug
    return env


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


def _parse_codex_session_id(stdout: str) -> str | None:
    """Extract ``thread.started.thread_id`` from Codex's `--json` NDJSON stream.

    Verified live on codex-cli 0.148.0: a fresh ``codex exec --json -`` and a
    ``codex exec resume <id> --json -`` both emit exactly one
    ``{"type":"thread.started","thread_id":"<uuid>",...}`` event first; the
    resumed run re-emits the SAME thread_id. Best-effort: returns None on
    empty/malformed/no-match output so a parse failure degrades to a fresh
    invocation, never a wrong answer.
    """
    if not stdout:
        return None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "thread.started":
            continue
        tid = obj.get("thread_id")
        return tid if isinstance(tid, str) and tid else None
    return None


def _parse_pi_session_id(stdout: str) -> str | None:
    """Extract the session header ``id`` from Pi's `--mode json` event stream.

    Verified live on pi 0.84.2: the FIRST JSON line is always
    ``{"type":"session","version":3,"id":"<uuid>",...}`` on both a fresh
    ``pi -p --mode json`` and a ``pi -p --session <id> --mode json`` continue
    (same id re-emitted). Best-effort: returns None on empty/malformed/
    no-match output.
    """
    if not stdout:
        return None
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict) or obj.get("type") != "session":
            continue
        sid = obj.get("id")
        return sid if isinstance(sid, str) and sid else None
    return None


def _parse_claude_terminal_error(stdout: str, stderr: str) -> str | None:
    """Parse Claude Code ``--output-format json`` stdout for a structured
    terminal error reason on non-zero exit.

    THR-116: When Claude exits non-zero, its stdout may carry a structured
    JSON result envelope with a deterministic terminal error (e.g.
    session-limit or UNKNOWN_CERTIFICATE_VERIFICATION_ERROR), while stderr
    contains an unrelated workspace-trust warning.  This function extracts
    the structured reason so dream-runner failures carry a classified reason
    instead of incidental stderr noise.

    Only the single documented in-repo terminal failure envelope shape is
    validated: ``{"type": "result", "subtype": "error_during_execution",
    "is_error": true, ...}`` (tests/test_headless_assistant.py
    CLAUDE_RESULT_ERROR fixture).  Every other shape — ``subtype:
    success``, non-``result`` event types, ``error_max_turns``,
    ``error_lookalike``, ``error_unknown``, ``error/errors`` outside a
    terminal result envelope, arbitrary ``error_*`` subtypes, missing
    ``is_error: true``, malformed/non-dict JSON, and no-structured-output —
    returns None so the compatible stderr-first error fallback wins.  No
    generic ``claude_<suffix>`` or provider-taxonomy reasons are fabricated.

    Returns a classified reason string like ``session_limit`` or
    ``transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR``, or None
    when no usable structured terminal result is available.
    """
    if not stdout or not stdout.strip():
        return None
    try:
        obj = json.loads(stdout.strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None

    # Only parse type:result events — ignore progress, system, and other
    # non-terminal event types.
    if obj.get("type") != "result":
        return None

    # Only parse the single documented terminal failure subtype —
    # {type: result, subtype: success, ...} and every other error_*
    # subtype (error_max_turns, error_lookalike, error_unknown, ...)
    # must NOT produce a classified terminal error; they return None
    # so the existing stderr-first raw error fallback wins.
    subtype = obj.get("subtype")
    if not isinstance(subtype, str) or subtype != "error_during_execution":
        return None

    # Require is_error: true — the documented terminal failure envelope
    # (CLAUDE_RESULT_ERROR in test_headless_assistant.py) carries this
    # marker.  Envelopes without it are incomplete and fall back to the
    # raw error.
    if obj.get("is_error") is not True:
        return None

    # ── Inspect result / errors for known terminal classifications ──
    result = obj.get("result")
    if isinstance(result, str) and result:
        result_lower = result.lower()
        if "certificate" in result_lower:
            return "transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR"
        if "session" in result_lower and ("limit" in result_lower or "max" in result_lower):
            return "session_limit"

    errors = obj.get("errors")
    if isinstance(errors, list):
        for err in errors:
            msg = err.get("message") if isinstance(err, dict) else str(err)
            if isinstance(msg, str):
                msg_lower = msg.lower()
                if "certificate" in msg_lower:
                    return "transport_error: UNKNOWN_CERTIFICATE_VERIFICATION_ERROR"
                if "session" in msg_lower and "limit" in msg_lower:
                    return "session_limit"

    # Unrecognized / ambiguous error content → no classified reason;
    # the existing stderr-first error fallback wins.
    return None


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

    THR-107 Phase 2: Delegates to ``GenericCliAdapter.parse_output()``
    — the single authoritative implementation lives in
    ``runtime/adapters/generic_cli.py``. This function is preserved as
    the stable import surface for ``_run_command(usage_parser=...)``
    and for backward-compatible test imports.

    Best-effort — mirrors the contract of every built-in parser:
    - Returns None when stdout is empty/whitespace (no parse attempted).
    - Returns TokenUsage with token fields NULL and raw JSON on parser failure
      (forensic preservation — same pattern as _parse_claude_usage:222).
    """
    from runtime.adapters.generic_cli import GenericCliAdapter
    return GenericCliAdapter.parse_output(stdout)


def is_rate_limit_signature(text: str) -> bool:
    """True when ``text`` matches a known provider rate-limit signature.

    The single source of truth for rate-limit detection (issue #85). Used by
    ``_run_command`` to set ``ExecutorResult.rate_limited`` across all executors
    and by the opaque-failure completion path as the back-compat string
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
    error_parser: Callable[[str, str], "str | None"] | None = None,
    strict_envelope_validator: Callable[[str], "str | None"] | None = None,
    pre_launch_validator: Callable[[], None] | None = None,
    org_slug: str | None = None,
    running: "RunningHandle | None" = None,
    throttle_backoff_seconds: Sequence[float] | None = None,
) -> ExecutorResult:
    """Run one agent subprocess under the per-provider throttle (issue #85).

    The Popen+communicate body is wrapped in ``_launch`` and handed to the
    process-wide ``ProviderThrottle``: it acquires a per-``provider`` slot,
    honors inter-launch spacing, and on a detected rate limit releases the slot,
    sleeps the backoff, and re-launches — ``_launch`` is idempotent because a
    rate-limited attempt did no useful work (never called ``report-completion``)
    and ``on_started`` simply re-stamps the new pid into SessionTracker.

    THR-207 contained wiring: when ``running`` is a backend-created
    ``RunningHandle`` the subprocess was already launched INTO containment by
    the ``HostSessionSupervisor`` (``backend.launch``); this body only
    communicates and parses. It does NOT self-launch, does NOT call
    ``on_started`` (the supervisor's ``AdmissionRequest.on_started`` exposes
    the diagnostic PID after bind), does NOT re-run the pre-launch validators
    (the supervisor ran them before ``backend.launch``), and enters the
    throttle with NO internal 429 retry so the rate-limited result flows to
    the supervisor, which fully finishes/releases the attempt, sleeps without
    capacity, and reacquires with the original enqueue age and a fresh
    backend handle. ``throttle_backoff_seconds`` overrides the configured 429
    schedule for the uncontained path (empty tuple = no internal retry).
    """
    sid = session_id or f"sess-{uuid.uuid4().hex}"
    workspace.mkdir(parents=True, exist_ok=True)

    def _launch() -> ExecutorResult:
        start_time = time.monotonic()
        if running is not None:
            # ── THR-207 contained launch ──
            # The backend already launched the subprocess into containment
            # (backend.launch); this body owns communicate + parse only. The
            # pre-launch validators ran in the supervisor before launch, and
            # on_started(pid) is the supervisor's AdmissionRequest hook bound
            # after the RunningHandle was bound — never re-stamped here.
            if running.process is None:
                # Honest passthrough fallback has no process: the caller must
                # use the uncontained path (running=None) so the executor
                # self-launches. Never synthesize a fake PID.
                return ExecutorResult(
                    success=False,
                    duration_seconds=0,
                    session_id=sid,
                    error=(
                        "contained launch requires a backend-created process; "
                        "the selected backend provides no containment "
                        "(passthrough) — retry on the uncontained path"
                    ),
                )
            proc = running.process
        else:
            # ── THR-200 pre-spawn prompt-transport guard ───────────────────
            # When the prompt travels via argv (input_text is None), any element
            # over the platform-safe per-argument byte limit would raise E2BIG at
            # execve. Fail deterministically BEFORE Popen with a normalized
            # category. stdin-capable transports (input_text set) are unbounded.
            if input_text is None:
                limit = _max_argv_element_bytes()
                for elem in cmd:
                    if len(elem.encode("utf-8")) > limit:
                        logger.warning(
                            "%s: argv element of %d encoded bytes exceeds "
                            "platform-safe per-argument limit %d",
                            _PROMPT_TRANSPORT_TOO_LARGE,
                            len(elem.encode("utf-8")), limit,
                        )
                        return ExecutorResult(
                            success=False,
                            duration_seconds=0,
                            session_id=sid,
                            error=_prompt_transport_too_large_error(limit),
                        )
            # ── Pre-launch integrity validation ──────────────────────────
            # Caller-provided validator runs BEFORE every Popen attempt,
            # including throttle retries after rate-limited responses.
            # Any exception here prevents the executor subprocess from
            # launching — fail-closed.
            if pre_launch_validator is not None:
                pre_launch_validator()
            # Popen (not subprocess.run) because the daemon needs the pid handed to
            # SessionTracker BEFORE we block in communicate(), so /cancel can SIGTERM
            # the process mid-session. stdin=PIPE unconditionally — Codex reads its
            # prompt from stdin; Claude ignores it when nothing is written.
            # Launch directly — the executor and daemon share the same OS
            # identity. Raises PlatformIsolationError on unsupported platform
            # — fail-closed before any subprocess.
            isolation = detect_platform_isolation()
            try:
                proc = isolation.launch_executor(
                    cmd,
                    cwd=workspace,
                    env=_callee_env(org_slug=org_slug),
                    stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except PlatformIsolationError as exc:
                return ExecutorResult(
                    success=False,
                    duration_seconds=0,
                    session_id=sid,
                    error=f"Platform isolation failure: {exc}",
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
            # THR-116: extract a classified terminal failure reason from
            # structured executor output (e.g. Claude's JSON result envelope)
            # so callers like dream_runner can persist a deterministic reason
            # instead of incidental stderr noise.
            terminal_error = None
            if error_parser is not None:
                try:
                    terminal_error = error_parser(full_stdout, full_stderr)
                except Exception as exc:
                    logger.warning("error parser raised: %s", exc)
                    terminal_error = None
            return ExecutorResult(
                success=False,
                duration_seconds=int(time.monotonic() - start_time),
                session_id=sid,
                returncode=proc.returncode,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
                error=f"Command exited with code {proc.returncode}{error_summary}",
                rate_limited=rate_limited,
                terminal_error=terminal_error,
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

        # ── D7A strict envelope enforcement (post-parse gate) ─────────
        # Runs AFTER the usage parser so forensic token data is already
        # captured. A non-None return value is the failure reason string;
        # None means the envelope is valid (or this validator isn't active).
        if strict_envelope_validator is not None:
            try:
                violation = strict_envelope_validator(full_stdout)
            except Exception as exc:
                logger.warning("strict-envelope validator raised: %s", exc)
                violation = f"Strict envelope validation error: {exc}"
            if violation is not None:
                # Envelope violation — fail closed with preserved tails
                # and actionable remediation in the error message.
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    returncode=proc.returncode,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error=violation,
                    rate_limited=rate_limited,
                )
        # ── end D7A strict enforcement ───────────────────────────────

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

    # Contained sessions defer the 429 retry to the supervisor (the throttle
    # must not re-communicate a backend-owned process); the uncontained path
    # honors ``throttle_backoff_seconds`` when the caller supplies it.
    backoff = () if running is not None else throttle_backoff_seconds
    return get_throttle().run(provider, _launch, on_throttle_event, backoff_seconds=backoff)


def build_command_launch_spec(
    *,
    cmd: Sequence[str],
    workspace: Path,
    input_text: str | None,
    org_slug: str | None = None,
) -> "LaunchSpec":
    """Assemble the supervisor ``LaunchSpec`` for a ``_run_command``-style
    executor (THR-207 task-producer wiring).

    Mirrors exactly what the uncontained ``_launch`` passes to
    ``isolation.launch_executor``: argv/cwd/normalized env/stdio/text. The
    argv-too-large gate is applied when the prompt travels via argv (the
    transport that can raise E2BIG at execve); a violation raises
    :class:`PromptTransportTooLargeError` with the actionable message the
    uncontained path returns as its normalized category.
    """
    if input_text is None:
        limit = _max_argv_element_bytes()
        for elem in cmd:
            if len(elem.encode("utf-8")) > limit:
                raise PromptTransportTooLargeError(_prompt_transport_too_large_error(limit))
    from runtime.platform.session_backend import LaunchSpec

    return LaunchSpec(
        argv=tuple(cmd),
        cwd=str(workspace),
        env=_callee_env(org_slug=org_slug),
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


class PromptTransportTooLargeError(RuntimeError):
    """The executor's argv cannot be delivered safely (E2BIG at execve).

    Raised by :func:`build_command_launch_spec` when the prompt travels via
    argv (``input_text`` is None); the contained caller maps it to the same
    normalized failure category as the uncontained path.
    """


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


def _allowed_tools_for_launch(
    paths: "OrgPaths", workspace: Path, turn_allow_set: object | None,
) -> str:
    """Build the Claude ``--allowedTools`` value for one launch.

    Ordinary launches keep the per-agent allow rules (baseline generation,
    unchanged). A continued manager turn (``turn_allow_set`` present) uses
    the mechanically narrowed turn-scoped allow set instead — the continued
    turn never inherits the ordinary manager executor permissions.
    """
    from runtime.orchestrator.workspace_adapters import allow_rules_for_agent
    if turn_allow_set is not None:
        return " ".join(turn_allow_set.allowed_tools_cli)
    # Workspace layout is `<runtime>/workspaces/<agent_name>`, so the
    # directory name is the canonical agent identifier.
    return " ".join(allow_rules_for_agent(paths, workspace.name, cli=True))


class ClaudeExecutor:
    def __init__(
        self,
        claude_cli_path: str,
        permission_mode: str,
        settings: Settings,
        paths: OrgPaths | None = None,
        model_arg: list[str] | None = None,
        *,
        profile_name: str = "claude",
        adapter: object | None = None,
    ) -> None:
        self._profile_name = profile_name
        self._permission_mode = permission_mode
        self._settings = settings
        self._paths = paths
        self._model_arg = model_arg
        self._adapter = adapter  # THR-107 D2: first-party adapter delegate

    def _build_argv(
        self,
        prompt: str,
        allowed_tools: str,
        model: str | None = None,
        resume_session_id: str | None = None,
    ) -> list[str]:
        """Build the argv list for a Claude Code subprocess launch.

        Delegates to the first-party adapter when available (D2); otherwise
        falls back to the D2-inline compatibility construction (producing
        bit-identical argv).
        """
        if self._adapter is not None:
            return self._adapter.build_argv(
                cli_path=_resolve_binary(self._profile_name),
                prompt=prompt,
                permission_mode=self._permission_mode,
                allowed_tools=allowed_tools,
                model=model,
                model_arg=self._model_arg,
                resume_session_id=resume_session_id,
            )
        # Compatibility fallback — bit-identical to the adapter path.
        # THR-200: the prompt is delivered via stdin (input_text), never argv.
        cmd = [
            _resolve_binary(self._profile_name),
        ]
        if model and self._model_arg:
            for elem in self._model_arg:
                cmd.append(elem.replace("{model}", model))
        cmd += [
            "-p",
            "--permission-mode",
            self._permission_mode,
            "--allowedTools",
            allowed_tools,
            "--output-format",
            "json",
        ]
        if resume_session_id:
            cmd += ["--resume", resume_session_id]
        return cmd

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
        pre_launch_validator: Callable[[], None] | None = None,
        org_slug: str | None = None,
        running: "RunningHandle | None" = None,
        throttle_backoff_seconds: Sequence[float] | None = None,
        turn_allow_set: object | None = None,
    ) -> ExecutorResult:
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        # The workspace's .claude/settings.json `permissions.allow` list is not
        # honoured in headless `-p` mode (observed empirically: Claude Code
        # 2.1.105 records `command_permissions.allowedTools: []` regardless of
        # what's in settings.json). Pass --allowedTools on the CLI instead so
        # agents can reliably call `happyranch ...` callbacks. Per-agent extras come
        # from the optional ``allow_rules:`` list in the agent's frontmatter
        # at ``<runtime>/org/agents/<name>.md``.
        # THR-181 Track A (founder option B) Unit 2: a continued manager turn
        # carries ``turn_allow_set`` — the mechanically narrowed allow set is
        # substituted for the ordinary per-agent rules (baseline generation is
        # untouched; the narrowing is turn-scoped only).
        allowed = _allowed_tools_for_launch(self._paths, workspace, turn_allow_set)
        cmd = self._build_argv(
            prompt=prompt,
            allowed_tools=allowed,
            model=model,
            resume_session_id=resume_session_id,
        )
        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            input_text=prompt,
            on_started=on_started,
            usage_parser=_parse_claude_usage,
            session_id_parser=_parse_claude_session_id,
            provider="claude",
            on_throttle_event=on_throttle_event,
            error_parser=_parse_claude_terminal_error,
            pre_launch_validator=pre_launch_validator,
            org_slug=org_slug,
            running=running,
            throttle_backoff_seconds=throttle_backoff_seconds,
        )

    def build_launch_spec(
        self,
        *,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
        resume_session_id: str | None = None,
        org_slug: str | None = None,
        timeout_seconds: int = 1800,
        turn_allow_set: object | None = None,
    ) -> "LaunchSpec":
        """The supervisor ``LaunchSpec`` for a contained Claude launch
        (THR-207 task-producer wiring): argv via the same ``_build_argv`` the
        uncontained path uses, stdin carries the prompt, stdio PIPE/text.
        ``timeout_seconds`` is accepted for seam uniformity (the session
        timeout is applied at communicate-time in contained mode; only the
        generic-CLI argv template substitutes it into argv).
        """
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        allowed = _allowed_tools_for_launch(self._paths, workspace, turn_allow_set)
        cmd = self._build_argv(
            prompt=prompt,
            allowed_tools=allowed,
            model=model,
            resume_session_id=resume_session_id,
        )
        return build_command_launch_spec(
            cmd=cmd, workspace=workspace, input_text=prompt, org_slug=org_slug,
        )



class CodexExecutor:
    def __init__(
        self,
        codex_cli_path: str,
        sandbox_mode: str,
        model_arg: list[str] | None = None,
        *,
        profile_name: str = "codex",
        adapter: object | None = None,
    ) -> None:
        self._profile_name = profile_name
        self._sandbox_mode = sandbox_mode
        self._model_arg = model_arg
        self._adapter = adapter  # THR-107 D2: first-party adapter delegate

    def _build_argv(
        self,
        model: str | None = None,
        resume_session_id: str | None = None,
    ) -> list[str]:
        """Build the argv list for a Codex subprocess launch.

        Delegates to the first-party adapter when available (D2); otherwise
        falls back to the D2-inline compatibility construction (producing
        bit-identical argv).
        """
        if self._adapter is not None:
            return self._adapter.build_argv(
                cli_path=_resolve_binary(self._profile_name),
                sandbox_mode=self._sandbox_mode,
                model=model,
                model_arg=self._model_arg,
                resume_session_id=resume_session_id,
            )
        # Compatibility fallback — bit-identical to the adapter path.
        if resume_session_id:
            # TASK-5977: resume routes through `codex exec resume <id>`. The
            # resume subcommand has NO --sandbox flag (verified in its help on
            # codex-cli 0.148.0), so the same workspace-write sandbox + network
            # posture is carried as `-c` config overrides; stdin `-` keeps the
            # prompt off argv.
            cmd = [
                _resolve_binary(self._profile_name),
                "exec",
                "resume",
                resume_session_id,
            ]
            if model and self._model_arg:
                for elem in self._model_arg:
                    cmd.append(elem.replace("{model}", model))
            cmd += [
                "-c",
                f'sandbox_mode="{self._sandbox_mode}"',
                "-c",
                "sandbox_workspace_write.network_access=true",
                "--skip-git-repo-check",
                "--json",
                "-",
            ]
            return cmd
        cmd = [
            _resolve_binary(self._profile_name),
            "exec",
        ]
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
        return cmd

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
        on_throttle_event: "OnThrottleEvent | None" = None,
        model: str | None = None,
        resume_session_id: str | None = None,
        pre_launch_validator: Callable[[], None] | None = None,
        org_slug: str | None = None,
        running: "RunningHandle | None" = None,
        throttle_backoff_seconds: Sequence[float] | None = None,
        turn_allow_set: object | None = None,
    ) -> ExecutorResult:
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        cmd = self._build_argv(model=model, resume_session_id=resume_session_id)
        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            input_text=prompt,
            on_started=on_started,
            usage_parser=_parse_codex_usage,
            session_id_parser=_parse_codex_session_id,
            provider="codex",
            on_throttle_event=on_throttle_event,
            pre_launch_validator=pre_launch_validator,
            org_slug=org_slug,
            running=running,
            throttle_backoff_seconds=throttle_backoff_seconds,
        )

    def build_launch_spec(
        self,
        *,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
        resume_session_id: str | None = None,
        org_slug: str | None = None,
        timeout_seconds: int = 1800,
        turn_allow_set: object | None = None,
    ) -> "LaunchSpec":
        """The supervisor ``LaunchSpec`` for a contained Codex launch
        (THR-207 task-producer wiring); stdin carries the prompt.
        ``timeout_seconds`` is accepted for seam uniformity (the session
        timeout is applied at communicate-time in contained mode).
        """
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        cmd = self._build_argv(model=model, resume_session_id=resume_session_id)
        return build_command_launch_spec(
            cmd=cmd, workspace=workspace, input_text=prompt, org_slug=org_slug,
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

    def __init__(
        self,
        opencode_cli_path: str,
        model_arg: list[str] | None = None,
        *,
        profile_name: str = "opencode",
        adapter: object | None = None,
    ) -> None:
        self._profile_name = profile_name
        self._model_arg = model_arg
        self._adapter = adapter  # THR-107 D2: first-party adapter delegate

    def _build_argv(
        self,
        workspace: str,
        prompt: str,
        model: str | None = None,
    ) -> list[str]:
        """Build the argv list for an opencode subprocess launch.

        Delegates to the first-party adapter when available (D2); otherwise
        falls back to the D2-inline compatibility construction (producing
        bit-identical argv).
        """
        if self._adapter is not None:
            return self._adapter.build_argv(
                cli_path=_resolve_binary(self._profile_name),
                workspace=workspace,
                prompt=prompt,
                model=model,
                model_arg=self._model_arg,
            )
        # Compatibility fallback — bit-identical to the adapter path.
        cmd = [
            _resolve_binary(self._profile_name),
            "run",
        ]
        if model and self._model_arg:
            for elem in self._model_arg:
                cmd.append(elem.replace("{model}", model))
        cmd += [
            "--dir",
            workspace,
            "--format",
            "json",
            prompt,
        ]
        return cmd

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
        on_throttle_event: "OnThrottleEvent | None" = None,
        model: str | None = None,
        pre_launch_validator: Callable[[], None] | None = None,
        org_slug: str | None = None,
        running: "RunningHandle | None" = None,
        throttle_backoff_seconds: Sequence[float] | None = None,
        turn_allow_set: object | None = None,
    ) -> ExecutorResult:
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        # opencode >= 1.14.0 rejects --prompt; use positional prompt (issue #216).
        cmd = self._build_argv(
            workspace=str(workspace),
            prompt=prompt,
            model=model,
        )
        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            on_started=on_started,
            usage_parser=_parse_opencode_usage,
            provider="opencode",
            on_throttle_event=on_throttle_event,
            pre_launch_validator=pre_launch_validator,
            org_slug=org_slug,
            running=running,
            throttle_backoff_seconds=throttle_backoff_seconds,
        )

    def build_launch_spec(
        self,
        *,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
        resume_session_id: str | None = None,
        org_slug: str | None = None,
        timeout_seconds: int = 1800,
        turn_allow_set: object | None = None,
    ) -> "LaunchSpec":
        """The supervisor ``LaunchSpec`` for a contained opencode launch
        (THR-207 task-producer wiring); the prompt travels via argv (opencode
        rejects ``--prompt``), so the argv-too-large gate applies.
        ``timeout_seconds`` is accepted for seam uniformity (applied at
        communicate-time in contained mode).
        """
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        cmd = self._build_argv(
            workspace=str(workspace),
            prompt=prompt,
            model=model,
        )
        return build_command_launch_spec(
            cmd=cmd, workspace=workspace, input_text=None, org_slug=org_slug,
        )



class PiExecutor:
    """Headless Pi invocation.

    Pi reads ``AGENTS.md`` from the workspace and supports print mode via
    ``-p``. It does not currently provide a HappyRanch-managed permission
    surface like Codex sandbox flags or opencode.json, so process containment
    must be supplied outside this executor if required.
    """

    def __init__(
        self,
        pi_cli_path: str,
        model_arg: list[str] | None = None,
        *,
        profile_name: str = "pi",
        adapter: object | None = None,
    ) -> None:
        self._profile_name = profile_name
        self._model_arg = model_arg
        self._adapter = adapter  # THR-107 D2: first-party adapter delegate

    def _build_argv(
        self,
        prompt: str,
        model: str | None = None,
        resume_session_id: str | None = None,
    ) -> list[str]:
        """Build the argv list for a Pi subprocess launch.

        Delegates to the first-party adapter when available (D2); otherwise
        falls back to the D2-inline compatibility construction (producing
        bit-identical argv).
        """
        if self._adapter is not None:
            return self._adapter.build_argv(
                cli_path=_resolve_binary(self._profile_name),
                prompt=prompt,
                model=model,
                model_arg=self._model_arg,
                resume_session_id=resume_session_id,
            )
        # Compatibility fallback — bit-identical to the adapter path.
        # THR-200: the prompt is delivered via stdin (input_text), never argv.
        cmd = [
            _resolve_binary(self._profile_name),
        ]
        if model and self._model_arg:
            for elem in self._model_arg:
                cmd.append(elem.replace("{model}", model))
        cmd += [
            "-p",
            "--mode",
            "json",
        ]
        if resume_session_id:
            # TASK-5977: `--session <id>` FAILS when the id is missing (the
            # eviction signature the runner detects). Never `--session-id`,
            # which would silently create a fresh session.
            cmd += ["--session", resume_session_id]
        return cmd

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
        on_throttle_event: "OnThrottleEvent | None" = None,
        model: str | None = None,
        resume_session_id: str | None = None,
        pre_launch_validator: Callable[[], None] | None = None,
        org_slug: str | None = None,
        running: "RunningHandle | None" = None,
        throttle_backoff_seconds: Sequence[float] | None = None,
        turn_allow_set: object | None = None,
    ) -> ExecutorResult:
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        cmd = self._build_argv(prompt=prompt, model=model,
                               resume_session_id=resume_session_id)
        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            input_text=prompt,
            on_started=on_started,
            usage_parser=_parse_pi_usage,
            session_id_parser=_parse_pi_session_id,
            provider="pi",
            on_throttle_event=on_throttle_event,
            pre_launch_validator=pre_launch_validator,
            org_slug=org_slug,
            running=running,
            throttle_backoff_seconds=throttle_backoff_seconds,
        )

    def build_launch_spec(
        self,
        *,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
        resume_session_id: str | None = None,
        org_slug: str | None = None,
        timeout_seconds: int = 1800,
        turn_allow_set: object | None = None,
    ) -> "LaunchSpec":
        """The supervisor ``LaunchSpec`` for a contained Pi launch
        (THR-207 task-producer wiring); stdin carries the prompt.
        ``timeout_seconds`` is accepted for seam uniformity (the session
        timeout is applied at communicate-time in contained mode).
        """
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        cmd = self._build_argv(prompt=prompt, model=model,
                               resume_session_id=resume_session_id)
        return build_command_launch_spec(
            cmd=cmd, workspace=workspace, input_text=prompt, org_slug=org_slug,
        )



class GenericCliExecutor:
    """Executor for registered custom CLI profiles (THR-052, THR-107 Phase 2).

    THR-107 Phase 2: GenericCliExecutor is now a **compatibility shell**
    around the first-party ``GenericCliAdapter`` in
    ``runtime/adapters/generic_cli.py``. The adapter owns the template
    expansion / argv construction and result-envelope parsing logic.
    This class delegates to it for bit-for-bit compatibility while
    preserving the existing public factory contract in ``build_executor``.

    Custom profiles with ``command_adapter_id`` set to ``"generic-cli"``
    (template-based) use this executor through the (unchanged)
    custom branch of ``build_executor``. Custom profiles bound to
    ``"custom-adapter:<id>"`` (separately registered, founder-approved,
    hash-verified executable — D7B) route through ``CustomAdapterExecutor``
    instead. Each profile's ``adapter`` field (claude/codex/opencode/pi)
    still controls workspace preparation only. No model selection is
    performed — custom profile model_arg
    is out of scope per founder gate (THR-067).

    The session-lifetime preamble is prepended to the prompt before
    substitution, same as every other executor.

    ``envelope_policy`` (D7A) controls result-envelope enforcement:
    - ``None`` (default): legacy compatibility — the v1 envelope is
      optional and absence preserves pre-D7A behavior.
    - ``"strict"``: mandatory v1 enforcement — a missing, malformed,
      invalid-version, or absent envelope fails closed with a
      deterministic error message including re-registration/verification
      guidance.
    """

    def __init__(
        self,
        *,
        profile_name: str,
        argv_template: list[str],
        provider: str,
        envelope_policy: str | None = None,
    ) -> None:
        self._profile_name = profile_name
        self._argv_template = list(argv_template)
        self._provider = provider
        self._envelope_policy = envelope_policy

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
        on_throttle_event: "OnThrottleEvent | None" = None,
        model: str | None = None,
        pre_launch_validator: Callable[[], None] | None = None,
        org_slug: str | None = None,
        running: "RunningHandle | None" = None,
        throttle_backoff_seconds: Sequence[float] | None = None,
        turn_allow_set: object | None = None,
    ) -> ExecutorResult:
        # model is accepted for signature parity but not used — custom
        # profile model_arg is out of scope per founder gate (THR-067).
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        from runtime.adapters.generic_cli import GenericCliAdapter
        cmd = GenericCliAdapter.build_argv(
            argv_template=self._argv_template,
            prompt=prompt,
            workspace=str(workspace),
            timeout_seconds=timeout_seconds,
            resolve_binary=_resolve_binary(self._profile_name),
        )

        # ── D7A strict envelope enforcement ─────────────────────────
        # When envelope_policy is "strict", add a post-launch validator
        # that checks stdout for a valid v1 envelope. The validator runs
        # inside _run_command with access to the full stdout (not just
        # the tail), so it can detect all failure modes: missing markers,
        # malformed JSON, missing/incorrect envelope_version, non-dict
        # content, etc.
        strict_validator = None
        if self._envelope_policy == "strict":
            strict_validator = GenericCliAdapter.validate_strict

        return _run_command(
            cmd,
            workspace,
            session_id,
            timeout_seconds,
            on_started=on_started,
            usage_parser=_parse_generic_cli_usage,
            provider=self._provider,
            on_throttle_event=on_throttle_event,
            strict_envelope_validator=strict_validator,
            pre_launch_validator=pre_launch_validator,
            org_slug=org_slug,
            running=running,
            throttle_backoff_seconds=throttle_backoff_seconds,
        )

    def build_launch_spec(
        self,
        *,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
        resume_session_id: str | None = None,
        org_slug: str | None = None,
        timeout_seconds: int = 1800,
        turn_allow_set: object | None = None,
    ) -> "LaunchSpec":
        """The supervisor ``LaunchSpec`` for a contained generic-CLI launch
        (THR-207 task-producer wiring); the prompt travels via the argv
        template (with ``{timeout_seconds}`` substitution), so the argv-too-
        large gate applies (input_text is None)."""
        prompt = _SESSION_LIFETIME_PREAMBLE + prompt
        from runtime.adapters.generic_cli import GenericCliAdapter
        cmd = GenericCliAdapter.build_argv(
            argv_template=self._argv_template,
            prompt=prompt,
            workspace=str(workspace),
            timeout_seconds=timeout_seconds,
            resolve_binary=_resolve_binary(self._profile_name),
        )
        return build_command_launch_spec(
            cmd=cmd, workspace=workspace, input_text=None, org_slug=org_slug,
        )



class CustomAdapterExecutor:
    """Executor for custom adapter profiles (THR-107 D7B).

    Launches only the resolved, approved, hash-verified absolute adapter
    executable as a subprocess. Passes the exact version-1 ``AdapterInput``
    JSON on stdin and accepts exactly one valid version-1 ``AdapterOutput``
    JSON on stdout, mapping it conservatively into the existing
    ``ExecutorResult`` lifecycle.

    The adapter must be durably APPROVED and its on-disk executable must
    pass the full path/regular-file/executable/SHA-256 verification at
    construction time and at every launch. A tampered, removed, or
    unexecutable artifact fails closed with a deterministic error.

    Never imports or discovers third-party Python. Never alters stored
    hashes, profile definitions, or adapter approval state.
    """

    def __init__(
        self,
        *,
        profile_name: str,
        adapter_entry_id: str,
        adapter_executable: str,
        adapter_hash: str,
        adapter_version: str,
        adapter_contract_version: int,
        provider: str,
        invocation_context: dict | None = None,
    ) -> None:
        self._profile_name = profile_name
        self._adapter_entry_id = adapter_entry_id
        self._adapter_executable = adapter_executable
        self._adapter_hash = adapter_hash
        self._adapter_version = adapter_version
        self._adapter_contract_version = adapter_contract_version
        self._provider = provider
        # invocation_context is a dict with truthful invocation fields
        # set by the caller (orchestrator, thread_runner, etc.).
        # Must contain at minimum: agent, org, invocation_kind.
        self._invocation_context = invocation_context or {}
        # THR-107 seq244: declared dependency manifest (None for legacy)
        self._dependency_manifest_version: int | None = None
        self._dependencies: list[dict] = []

    def set_dependency_manifest(
        self,
        dependency_manifest_version: int | None,
        dependencies: list[dict],
    ) -> None:
        """Set the dependency manifest BEFORE run().

        Called by build_executor after resolving the adapter entry.
        Must be called before run() for new-manifest adapters; legacy
        adapters omit this call and get the legacy behavior.
        """
        self._dependency_manifest_version = dependency_manifest_version
        self._dependencies = dependencies or []

    def set_invocation_context(
        self,
        *,
        agent: str,
        org: str,
        invocation_kind: str,
        task_id: str | None = None,
    ) -> None:
        """Set truthful invocation context BEFORE run().

        Called by the runner (orchestrator, thread_runner, wake_runner,
        dream_runner, schedule_runner) after build_executor returns.
        Must supply all required fields; the CustomAdapterExecutor fails
        closed if any field is missing at run() time.
        """
        self._invocation_context = {
            "agent": agent,
            "org": org,
            "invocation_kind": invocation_kind,
            "task_id": task_id,
        }

    def verify_launch_ready(self) -> str | None:
        """Verify the approved adapter artifact + every declared dependency
        immediately before an actual launch attempt.

        Returns an actionable error string when the artifact is missing,
        not a regular file, not executable, hash-mismatched, or a declared
        dependency fails any of those checks; ``None`` when everything
        verifies. Runs on BOTH launch paths: inside the uncontained
        ``_launch`` (before Popen, per THR-107) and via the supervisor's
        per-attempt pre-launch validator for contained sessions (before the
        backend creates the scope), and defensively again inside the
        contained ``_launch``.
        """
        from runtime.orchestrator.adapter_store import compute_sha256

        adapter_launch_path = Path(self._adapter_executable)
        if not adapter_launch_path.exists():
            return (
                f"Custom adapter {self._adapter_entry_id!r} executable "
                f"{self._adapter_executable!r} no longer exists. "
                f"Re-register the adapter."
            )
        if not adapter_launch_path.is_file():
            return (
                f"Custom adapter {self._adapter_entry_id!r} path "
                f"{self._adapter_executable!r} is not a regular file. "
                f"Re-register the adapter."
            )
        if not os.access(adapter_launch_path, os.X_OK):
            return (
                f"Custom adapter {self._adapter_entry_id!r} executable "
                f"{self._adapter_executable!r} is not executable. "
                f"Re-register the adapter."
            )
        current_launch_hash = compute_sha256(self._adapter_executable)
        if current_launch_hash != self._adapter_hash:
            return (
                f"Custom adapter {self._adapter_entry_id!r} hash mismatch: "
                f"expected {self._adapter_hash[:12]}..., "
                f"got {current_launch_hash[:12]}... "
                f"Re-register and re-approve the adapter."
            )
        # THR-107 seq244: revalidate every declared dependency (path type /
        # executability / hash) before EVERY launch attempt.
        if self._dependency_manifest_version is not None and self._dependencies:
            for dep in self._dependencies:
                dep_exe = dep.get("executable", "")
                dep_hash = dep.get("sha256", "")
                dep_path = Path(dep_exe)
                if not dep_path.exists():
                    return (
                        f"Declared dependency {dep_exe!r} no longer exists. "
                        f"Re-submit the adapter with a valid dependency and "
                        f"obtain founder approval."
                    )
                if not dep_path.is_file():
                    return (
                        f"Declared dependency {dep_exe!r} is not a regular file. "
                        f"Re-submit the adapter with a valid dependency and "
                        f"obtain founder approval."
                    )
                if not os.access(dep_path, os.X_OK):
                    return (
                        f"Declared dependency {dep_exe!r} is not executable. "
                        f"Re-submit the adapter with a valid dependency and "
                        f"obtain founder approval."
                    )
                current_dep_hash = compute_sha256(dep_exe)
                if current_dep_hash != dep_hash:
                    return (
                        f"Declared dependency {dep_exe!r} hash mismatch: "
                        f"expected {dep_hash[:12]}..., got {current_dep_hash[:12]}... "
                        f"The dependency has been modified since approval. "
                        f"Re-submit the adapter with the updated dependency "
                        f"and obtain founder approval."
                    )
        return None

    def build_launch_spec(
        self,
        *,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        model: str | None = None,
        resume_session_id: str | None = None,
        org_slug: str | None = None,
        timeout_seconds: int = 1800,
        turn_allow_set: object | None = None,
    ) -> "LaunchSpec":
        """The supervisor ``LaunchSpec`` for a contained custom-adapter launch
        (THR-207 task-producer wiring): the absolute, hash-pinned adapter
        executable with the inherited normalized env; AdapterInput travels on
        stdin via communicate. ``timeout_seconds`` is accepted for seam
        uniformity (applied at communicate-time in contained mode).
        """
        from runtime.platform.session_backend import LaunchSpec

        return LaunchSpec(
            argv=(self._adapter_executable,),
            cwd=str(workspace),
            env=_callee_env(org_slug=org_slug),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def run(
        self,
        workspace: Path,
        prompt: str,
        session_id: str | None = None,
        timeout_seconds: int = 1800,
        on_started: Callable[[int], None] | None = None,
        on_throttle_event: "OnThrottleEvent | None" = None,
        model: str | None = None,
        pre_launch_validator: Callable[[], None] | None = None,
        org_slug: str | None = None,
        running: "RunningHandle | None" = None,
        throttle_backoff_seconds: Sequence[float] | None = None,
        turn_allow_set: object | None = None,
    ) -> ExecutorResult:
        """Launch the custom adapter subprocess with AdapterInput on stdin.

        Builds an AdapterInput v1 contract with truthful invocation
        context, passes it as JSON on stdin, and parses the AdapterOutput
        v1 contract from stdout. Maps the output conservatively into
        ExecutorResult.

        Rejects: missing/malformed/unknown-version/non-object output,
        identity/version/contract mismatch, success/returncode
        inconsistency, and oversized output.

        THR-207 contained wiring: when ``running`` is a backend-created
        ``RunningHandle`` the adapter was already launched into containment
        by the ``HostSessionSupervisor``; this body communicates and parses
        only (no raw ``Popen``, no ``on_started`` — the supervisor exposes
        the diagnostic PID — and no internal 429 retry so the rate-limited
        result flows to the supervisor's finish/release/sleep/reacquire).
        """
        from runtime.orchestrator.adapter_contract import (
            AdapterInput,
            AdapterOutput,
            ExecutorContext,
            InvocationInfo,
            TimeoutInfo,
        )
        from runtime.orchestrator.adapter_store import compute_sha256

        prompt = _SESSION_LIFETIME_PREAMBLE + prompt

        # ── D7B: Fail closed if invocation context is missing/incomplete ──
        ctx = self._invocation_context
        missing = []
        if not ctx.get("agent"):
            missing.append("agent")
        if not ctx.get("org"):
            missing.append("org")
        if not ctx.get("invocation_kind"):
            missing.append("invocation_kind")
        if missing:
            return ExecutorResult(
                success=False,
                duration_seconds=0,
                session_id=session_id or "",
                error=(
                    f"Custom adapter {self._adapter_entry_id!r} requires "
                    f"truthful invocation context but the caller did not "
                    f"supply: {', '.join(missing)}. "
                    f"The caller must call set_invocation_context() before run()."
                ),
            )

        # ── Build AdapterInput with truthful invocation context ────
        sid = session_id or f"sess-{uuid.uuid4().hex}"
        ctx = self._invocation_context
        invocation_info = InvocationInfo(
            invocation_id=sid,
            task_id=ctx.get("task_id"),
            agent=ctx.get("agent", "unknown"),
            org=ctx.get("org", "unknown"),
            invocation_kind=ctx.get("invocation_kind", "task"),
        )
        adapter_input = AdapterInput(
            contract_version=1,
            invocation=invocation_info,
            prompt=prompt,
            workspace=str(workspace),
            timeout=TimeoutInfo(
                deadline_seconds=timeout_seconds,
                max_runtime_seconds=timeout_seconds,
            ),
            executor_context=ExecutorContext(
                provider=self._provider,
                adapter_id=self._adapter_entry_id,
                adapter_version=self._adapter_version,
                permission_mode=None,
            ),
        )
        input_json = adapter_input.model_dump_json()

        # ── D7B: route through per-provider throttle (Fix 3) ──────
        # The Popen+communicate+parse+validate body is wrapped in _launch
        # and handed to ProviderThrottle — same pattern as _run_command.
        # Acquires a per-provider slot, honors inter-launch spacing, and
        # on a detected rate limit releases the slot, sleeps the backoff,
        # and re-launches.
        workspace.mkdir(parents=True, exist_ok=True)

        def _launch() -> ExecutorResult:
            start_time = time.monotonic()

            # ── Pre-launch integrity validation ────────────────────────
            # Caller-provided validator runs BEFORE every launch attempt,
            # including supervisor-level retries after rate-limited responses.
            if pre_launch_validator is not None:
                pre_launch_validator()

            # ── D7B: Verify executable integrity at EVERY actual launch attempt ──
            # This MUST be inside _launch, not pre-throttle: ProviderThrottle
            # (and the supervisor retry loop) can re-launch, and each attempt
            # MUST re-verify the exact approved artifact (path type,
            # executable bit, SHA-256) immediately before its Popen.
            verify_error = self.verify_launch_ready()
            if verify_error is not None:
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    error=verify_error,
                )

            if running is not None:
                # ── THR-207 contained launch ──
                # The backend already launched the adapter into containment;
                # this body communicates + parses only. on_started(pid) is the
                # supervisor's AdmissionRequest hook bound after the
                # RunningHandle was bound — never re-stamped here.
                if running.process is None:
                    return ExecutorResult(
                        success=False,
                        duration_seconds=int(time.monotonic() - start_time),
                        session_id=sid,
                        error=(
                            "contained launch requires a backend-created "
                            "process; the selected backend provides no "
                            "containment (passthrough) — retry on the "
                            "uncontained path"
                        ),
                    )
                proc = running.process
            else:
                # ── THR-107 seq268 (seq315 correction): inherited normalized PATH ──
                # The adapter-launched process receives the same inherited
                # normalized environment as normal launches (_callee_env()).
                # The adapter wrapper and every declared child dependency remain
                # explicitly absolute and hash-pinned/revalidated — the runtime
                # never selects an agentic CLI from ambient PATH.  Pre-Popen
                # wrapper/dependency validation is retained exactly.
                launch_env = _callee_env()

                try:
                    proc = subprocess.Popen(
                        [self._adapter_executable],
                        cwd=str(workspace),
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        env=launch_env,
                    )
                except (FileNotFoundError, OSError, PermissionError) as exc:
                    return ExecutorResult(
                        success=False,
                        duration_seconds=int(time.monotonic() - start_time),
                        session_id=sid,
                        error=(
                            f"Failed to launch custom adapter "
                            f"{self._adapter_executable!r}: {exc}"
                        ),
                    )

                if on_started is not None:
                    on_started(proc.pid)

            try:
                stdout, stderr = proc.communicate(
                    input=input_json, timeout=timeout_seconds
                )
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    error=f"Custom adapter session timed out after {timeout_seconds}s",
                )

            full_stdout = stdout or ""
            full_stderr = stderr or ""
            stdout_tail = full_stdout[-_TAIL_BYTES:]
            stderr_tail = full_stderr[-_TAIL_BYTES:]

            if proc.returncode != 0:
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    returncode=proc.returncode,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error=(
                        f"Custom adapter exited with code {proc.returncode}"
                        + (": " + stderr_tail[:500] if stderr_tail else "")
                    ),
                )

            # ── Parse AdapterOutput ────────────────────────────────
            # Reject oversized output (same 1MB limit as conformance probe)
            MAX_OUTPUT_BYTES = 1_048_576
            stdout_bytes = full_stdout.encode("utf-8")
            if len(stdout_bytes) > MAX_OUTPUT_BYTES:
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error=(
                        f"Custom adapter stdout exceeds {MAX_OUTPUT_BYTES} byte limit "
                        f"({len(stdout_bytes)} bytes)"
                    ),
                )

            # Parse JSON
            try:
                output_dict = json.loads(full_stdout)
            except json.JSONDecodeError:
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error="Custom adapter stdout is not valid JSON",
                )

            if not isinstance(output_dict, dict):
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error=(
                        f"Custom adapter stdout is not a JSON object; "
                        f"got {type(output_dict).__name__}"
                    ),
                )

            # Validate AdapterOutput schema
            try:
                output = AdapterOutput.model_validate(output_dict)
            except Exception as exc:
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error=f"Custom adapter output does not match AdapterOutput contract: {exc}",
                )

            # ── D7B: Verify adapter provenance (Fix 1) ─────────────
            # Reject before mapping/accounting when the adapter's output
            # metadata disagrees with the approved bound adapter.

            # Verify contract version
            if output.adapter_metadata.contract_version != 1:
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error=(
                        f"Custom adapter contract version "
                        f"{output.adapter_metadata.contract_version} "
                        f"not supported; expected 1"
                    ),
                )

            # Verify adapter identity
            if output.adapter_metadata.adapter != self._adapter_entry_id:
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error=(
                        f"Custom adapter identity mismatch: expected "
                        f"{self._adapter_entry_id!r}, got "
                        f"{output.adapter_metadata.adapter!r}"
                    ),
                )

            # Verify adapter version matches approved binding
            if output.adapter_metadata.adapter_version != self._adapter_version:
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error=(
                        f"Custom adapter version mismatch: approved "
                        f"{self._adapter_version!r}, adapter returned "
                        f"{output.adapter_metadata.adapter_version!r}"
                    ),
                )

            # Verify session_id echo (invocation integrity)
            if output.session_id != sid:
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error=(
                        f"Custom adapter session_id mismatch: expected "
                        f"{sid!r}, adapter returned "
                        f"{output.session_id!r}"
                    ),
                )

            # Verify success/returncode consistency
            if output.success and output.returncode is not None and output.returncode != 0:
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    returncode=output.returncode,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error=(
                        f"Adapter reported success=true but agent subprocess "
                        f"exit code was {output.returncode}"
                    ),
                )
            if not output.success and (output.returncode is None or output.returncode == 0):
                return ExecutorResult(
                    success=False,
                    duration_seconds=int(time.monotonic() - start_time),
                    session_id=sid,
                    returncode=proc.returncode,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    error=(
                        f"Adapter reported success=false but subprocess "
                        f"exit code was 0"
                    ),
                )

            # ── Map to ExecutorResult ──────────────────────────────
            token_usage: "TokenUsage | None" = None
            if output.token_usage is not None:
                from runtime.models import TokenUsage
                token_usage = TokenUsage(
                    input_tokens=output.token_usage.input_tokens,
                    output_tokens=output.token_usage.output_tokens,
                    cache_read_tokens=output.token_usage.cache_read_tokens,
                    cache_creation_tokens=output.token_usage.cache_creation_tokens,
                    reasoning_tokens=output.token_usage.reasoning_tokens,
                    model=output.token_usage.model or self._provider,
                    usage_raw_json=output.token_usage.usage_raw_json,
                )

            return ExecutorResult(
                success=output.success,
                duration_seconds=output.duration_seconds,
                session_id=sid,
                returncode=output.returncode,
                stdout_tail=output.stdout_tail or stdout_tail,
                stderr_tail=output.stderr_tail or stderr_tail,
                error=output.error,
                token_usage=token_usage,
                agent_session_id=output.agent_session_id,
                rate_limited=output.rate_limited,
            )

        # Contained sessions defer the 429 retry to the supervisor (the throttle
        # must not re-communicate a backend-owned process); the uncontained path
        # honors ``throttle_backoff_seconds`` when the caller supplies it.
        backoff = () if running is not None else throttle_backoff_seconds
        from runtime.orchestrator.throttle import get_throttle
        return get_throttle().run(
            self._provider, _launch, on_throttle_event, backoff_seconds=backoff
        )


AgentExecutor = ClaudeExecutor
