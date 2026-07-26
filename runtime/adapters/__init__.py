"""Private first-party adapter catalog (THR-107 Phase 1 / D2, D8).

This is a **compatibility-only** private implementation detail — NOT a
runtime-writable manifest and NOT a custom-adapter execution or plugin-loader
mechanism. The catalog maps the four built-in executor profile names to their
corresponding first-party adapter classes and profile metadata.

Custom profiles and ``GenericCliExecutor`` are explicitly excluded — this
catalog is solely for the four built-ins: Claude, Codex, OpenCode, and Pi.

**D2:** The adapter-class catalog provides data-driven adapter injection
into ``build_executor``, above the preserved hard-coded if/elif chain.

**D8:** ``BuiltinAdapterDescriptor`` is now the authoritative single source
for built-in profile metadata. ``ExecutorRegistry._register_builtins()``
constructs ``ExecutorProfile`` instances exclusively from this catalog;
no literal parallel built-in list remains in ``executor_registry.py``.

**Rollback:** The current hard-coded `if/elif` chain in ``build_executor``
is preserved as the compatibility fallback/rollback path (D10 is not
approved in this PR).
"""

from __future__ import annotations

from dataclasses import dataclass

from runtime.adapters.claude import ClaudeAdapter
from runtime.adapters.codex import CodexAdapter
from runtime.adapters.opencode import OpencodeAdapter
from runtime.adapters.pi import PiAdapter

# ---------------------------------------------------------------------------
# Built-in adapter descriptor
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuiltinAdapterDescriptor:
    """Immutable declaration of a built-in first-party adapter profile.

    Each field maps to an ``ExecutorProfile`` constructor argument.
    ``adapter_cls`` is the D2 first-party adapter class used for argv
    construction in ``build_executor``.

    **D8 authoritative source:** ``ExecutorRegistry._register_builtins()``
    constructs its built-in ``ExecutorProfile`` instances exclusively from
    the catalog of these descriptors. No literal parallel built-in list or
    table may remain in ``executor_registry.py``.

    This is a **private code-native declaration**, not a runtime-writable
    manifest, dynamic discovery, plugin loader, or external configuration
    format.

    **Immutability:** ``model_arg`` is stored as an immutable tuple to
    prevent alias mutation through the public catalog accessor. Registry
    consumers that need a mutable list must copy it independently.
    """

    name: str
    kind: str
    adapter_id: str
    readiness_marker_fragment: str
    model_arg: tuple[str, ...] | None
    adapter_cls: type


# ---------------------------------------------------------------------------
# Authoritative built-in catalog (D8)
# ---------------------------------------------------------------------------
# This tuple is the SINGLE source of truth for all four built-in profiles.
# It replaces the literal ExecutorProfile(…) list in _register_builtins().
# Order: claude, codex, opencode, pi (stable, matches legacy registration).
# Immutable — no runtime modification path exists.
# ---------------------------------------------------------------------------

_BUILTIN_CATALOG: tuple[BuiltinAdapterDescriptor, ...] = (
    BuiltinAdapterDescriptor(
        name="claude",
        kind="builtin",
        adapter_id="claude",
        readiness_marker_fragment=".claude/skills/start-task/SKILL.md",
        model_arg=("--model", "{model}"),
        adapter_cls=ClaudeAdapter,
    ),
    BuiltinAdapterDescriptor(
        name="codex",
        kind="builtin",
        adapter_id="codex",
        readiness_marker_fragment="AGENTS.md",
        model_arg=("-m", "{model}"),
        adapter_cls=CodexAdapter,
    ),
    BuiltinAdapterDescriptor(
        name="opencode",
        kind="builtin",
        adapter_id="opencode",
        readiness_marker_fragment="AGENTS.md",
        model_arg=("-m", "{model}"),
        adapter_cls=OpencodeAdapter,
    ),
    BuiltinAdapterDescriptor(
        name="pi",
        kind="builtin",
        adapter_id="pi",
        readiness_marker_fragment="AGENTS.md",
        model_arg=("--model", "{model}"),
        adapter_cls=PiAdapter,
    ),
)


# ---------------------------------------------------------------------------
# Derived adapter-class lookup (D2 — preserved, derived from D8 catalog)
# ---------------------------------------------------------------------------
# Maps built-in executor profile name → adapter class.
# Excludes custom profiles ("generic", "generic-cli") — those continue to
# use GenericCliExecutor through the existing hard-coded build_executor chain.
# Derived from _BUILTIN_CATALOG so there is no parallel truth.
# ---------------------------------------------------------------------------

_FIRST_PARTY_ADAPTER_CATALOG: dict[str, type] = {
    desc.name: desc.adapter_cls for desc in _BUILTIN_CATALOG
}


# ---------------------------------------------------------------------------
# Public accessors (D2 + D8)
# ---------------------------------------------------------------------------


def get_builtin_catalog() -> tuple[BuiltinAdapterDescriptor, ...]:
    """Return the authoritative immutable built-in adapter catalog (D8).

    ``ExecutorRegistry._register_builtins()`` consumes this to construct
    built-in ``ExecutorProfile`` instances. The catalog is the single source
    of truth — no literal parallel built-in list may remain elsewhere.
    """
    return _BUILTIN_CATALOG


def get_first_party_adapter(name: str) -> type | None:
    """Return the adapter class for a built-in profile name, or None.

    This is the **single import surface** for the first-party adapter
    catalog — the daemon's ``build_executor`` and the executor classes
    consume it, never the raw catalog dict.

    Derived from the D8 authoritative catalog — no parallel truth.
    """
    return _FIRST_PARTY_ADAPTER_CATALOG.get(name.lower())


# Re-export for convenience — tests and type-checkers reference the concrete
# adapter classes through the runtime.adapters package.
__all__ = [
    "BuiltinAdapterDescriptor",
    "ClaudeAdapter",
    "CodexAdapter",
    "OpencodeAdapter",
    "PiAdapter",
    "get_builtin_catalog",
    "get_first_party_adapter",
]
