"""Private first-party adapter catalog (THR-107 Phase 1 / D2).

This is a **compatibility-only** private implementation detail — NOT a
runtime-writable manifest and NOT a custom-adapter execution or plugin-loader
mechanism. The catalog maps the four built-in executor profile names to their
corresponding first-party adapter classes.

Custom profiles and ``GenericCliExecutor`` are explicitly excluded — this
catalog is solely for the four built-ins: Claude, Codex, OpenCode, and Pi.

**Rollback:** The current hard-coded `if/elif` chain in ``build_executor``
is preserved as the compatibility fallback/rollback path. This catalog is
an additive data-driven path above it.
"""

from __future__ import annotations

from runtime.adapters.claude import ClaudeAdapter
from runtime.adapters.codex import CodexAdapter
from runtime.adapters.opencode import OpencodeAdapter
from runtime.adapters.pi import PiAdapter

# ---------------------------------------------------------------------------
# Private first-party adapter catalog
# ---------------------------------------------------------------------------
# Maps built-in executor profile name → adapter class.
# Excludes custom profiles ("generic", "generic-cli") — those continue to
# use GenericCliExecutor through the existing hard-coded build_executor chain.
# ---------------------------------------------------------------------------

_FIRST_PARTY_ADAPTER_CATALOG: dict[str, type] = {
    "claude": ClaudeAdapter,
    "codex": CodexAdapter,
    "opencode": OpencodeAdapter,
    "pi": PiAdapter,
}


def get_first_party_adapter(name: str) -> type | None:
    """Return the adapter class for a built-in profile name, or None.

    This is the **single import surface** for the first-party adapter
    catalog — the daemon's ``build_executor`` and the executor classes
    consume it, never the raw catalog dict.
    """
    return _FIRST_PARTY_ADAPTER_CATALOG.get(name.lower())


# Re-export for convenience — tests and type-checkers reference the concrete
# adapter classes through the runtime.adapters package.
__all__ = [
    "ClaudeAdapter",
    "CodexAdapter",
    "OpencodeAdapter",
    "PiAdapter",
    "get_first_party_adapter",
]
