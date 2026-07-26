"""First-party adapter for generic CLI profiles (THR-107 Phase 2).

Private implementation detail of the adapter runtime boundary — not a
plugin surface. This adapter encapsulates generic-CLI template expansion
(argv construction from an argv_template) and result-envelope output
parsing behind :meth:`build_argv` and :meth:`parse_output`,
respectively.

This adapter is the **command adapter** for ALL custom profiles
registered in the machine-global runtime store. It is statically
imported and never discovered or loaded at runtime.
"""

from __future__ import annotations

import json
import logging

from runtime.models import TokenUsage

logger = logging.getLogger(__name__)

# ── Sentinel strings (mirrored from executors.py — canonical source) ────

_HR_ENVELOPE_BEGIN = "__HR_ENVELOPE_BEGIN__"
_HR_ENVELOPE_END = "__HR_ENVELOPE_END__"


class GenericCliAdapter:
    """First-party adapter for custom CLI profiles.

    Encapsulates:
    - :meth:`build_argv` — template-placeholder substitution identical to
      the current GenericCliExecutor.run() inline construction.
    - :meth:`parse_output` — result-envelope parsing identical to
      the current ``_parse_generic_cli_usage`` in executors.py.

    This adapter is the command-adapter for all custom profiles.
    Workspace preparation remains controlled by each profile's
    ``adapter_id`` (pi/claude/codex/opencode) — this adapter has
    no workspace-side effects.
    """

    @staticmethod
    def build_argv(
        argv_template: list[str],
        prompt: str,
        workspace: str,
        timeout_seconds: int,
        *,
        resolve_binary: str | None = None,
    ) -> list[str]:
        """Build the argv list from a template with placeholder substitution.

        Every template element is one argv element. Placeholders
        ``{prompt}``, ``{timeout_seconds}``, and ``{workspace}`` are
        replaced by their resolved values. No shell string is constructed;
        no splitting occurs. The first element (binary) may be optionally
        resolved to an absolute path by the caller.

        Args:
            argv_template: The list of argv template strings.
            prompt: Full prompt text including session-lifetime preamble.
            workspace: Absolute path to the agent workspace (as str).
            timeout_seconds: Timeout in seconds.
            resolve_binary: If set, the resolved absolute path for element
                [0]; if None, element [0] is used as-is. Callers should
                resolve via ``_resolve_binary`` in executors.py and pass
                the result.

        Returns:
            The fully-substituted argv list.
        """
        cmd: list[str] = []
        for i, elem in enumerate(argv_template):
            elem = elem.replace("{prompt}", prompt)
            elem = elem.replace("{timeout_seconds}", str(timeout_seconds))
            elem = elem.replace("{workspace}", workspace)
            if i == 0 and resolve_binary is not None:
                elem = resolve_binary
            cmd.append(elem)
        return cmd

    @staticmethod
    def parse_output(stdout: str) -> TokenUsage | None:
        """Parse a custom CLI's stdout for a THR-107 result-envelope.

        Best-effort — mirrors the contract of every built-in parser:
        - Returns None when stdout is empty/whitespace (no parse attempted).
        - Returns TokenUsage with token fields NULL and raw JSON on parser
          failure (forensic preservation — same pattern as
          _parse_claude_usage:222).

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
        end_pos = stdout.find(
            _HR_ENVELOPE_END, begin_pos + len(_HR_ENVELOPE_BEGIN)
        )
        if end_pos == -1:
            # Missing END — forensic tail preservation.
            tail = stdout[begin_pos:]
            logger.warning(
                "generic CLI usage parser: missing %s sentinel",
                _HR_ENVELOPE_END,
            )
            tail_bytes = tail.encode()
            safe_len = min(len(tail_bytes), 2000)
            return TokenUsage(usage_raw_json=tail_bytes[:safe_len].decode("utf-8", errors="replace"))

        # Extract the JSON block between sentinels.
        block = stdout[begin_pos + len(_HR_ENVELOPE_BEGIN) : end_pos].strip()
        if not block:
            return None

        try:
            obj = json.loads(block)
        except json.JSONDecodeError:
            logger.warning("generic CLI usage parser: envelope is not valid JSON")
            block_bytes = block.encode()
            safe_len = min(len(block_bytes), 2000)
            return TokenUsage(usage_raw_json=block_bytes[:safe_len].decode("utf-8", errors="replace"))

        if not isinstance(obj, dict):
            block_bytes = block.encode()
            safe_len = min(len(block_bytes), 2000)
            return TokenUsage(usage_raw_json=block_bytes[:safe_len].decode("utf-8", errors="replace"))

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
