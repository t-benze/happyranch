"""Executor profile registry — the single source of truth for executor resolution.

THR-052 seq 6 founder ruling: HappyRanch should not maintain an explicit
supported-executor list. Executors are capability-registered, not name-listed.
Built-in executors (claude, codex, opencode, pi) are profiles like any other;
their factories, adapters, and readiness markers are registered, not hard-coded
in if/elif chains across every call site.

A profile resolves to:
  - an executor instance (factory) for subprocess launch
  - a workspace adapter id (which writes bootstrap files)
  - a readiness marker path (relative to workspace root)
  - for custom profiles: an argv_template with supported placeholders
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from runtime.config import Settings
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.executors import (
        ClaudeExecutor,
        CodexExecutor,
        OpencodeExecutor,
        PiExecutor,
        AgentExecutor,
    )

# ---------------------------------------------------------------------------
# Placeholders supported in custom-profile argv templates.
# Every placeholder must resolve to a single argv list element — no shell
# string templating, no concatenation with literal text.
# ---------------------------------------------------------------------------
VALID_PLACEHOLDERS: frozenset[str] = frozenset(
    {"{prompt}", "{timeout_seconds}", "{workspace}"}
)


def validate_argv_template(argv: list[str]) -> list[str]:
    """Reject unsafe argv templates for custom executor profiles.

    Returns a list of error strings (empty list = valid).
    """
    errors: list[str] = []
    if not isinstance(argv, list) or not argv:
        errors.append("argv_template must be a non-empty list of strings")
        return errors
    for i, elem in enumerate(argv):
        if not isinstance(elem, str) or not elem:
            errors.append(f"argv_template[{i}] must be a non-empty string")
            continue
        # Find all {placeholders} in the element
        import re
        placeholders = re.findall(r"\{[a-z_]+\}", elem)
        for ph in placeholders:
            if ph not in VALID_PLACEHOLDERS:
                errors.append(
                    f"argv_template[{i}]: unsupported placeholder {ph!r}; "
                    f"valid: {', '.join(sorted(VALID_PLACEHOLDERS))}"
                )
    return errors


# ---------------------------------------------------------------------------
# Profile definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutorProfile:
    """A registered executor profile.

    ``name`` is the string agents use in their frontmatter / agent.yaml
    ``executor:`` field. It must be lower-case and non-empty.

    ``kind`` is ``"builtin"`` for the four built-in adapters (claude, codex,
    opencode, pi) and ``"custom"`` for user-registered CLI profiles.

    ``adapter_id`` tells the workspace preparation layer which adapter to use
    when writing bootstrap files (CLAUDE.md / AGENTS.md / settings.json). It
    refers to a built-in adapter: ``"claude"``, ``"codex"``, ``"opencode"``,
    or ``"pi"``.

    ``readiness_marker_fragment`` is a relative path within the workspace
    that, when present, signals the workspace is ready. The orchestrator
    checks for it before launching.

    ``argv_template`` (custom profiles only) is the argv list the
    GenericCliExecutor expands from placeholders at launch time. Built-in
    profiles leave this ``None`` — their factories supply their own argv.

    ``command`` (custom profiles only) is the executable name checked via
    ``shutil.which`` at profile load time. Built-in profiles resolve their
    CLI paths from Settings.
    """

    name: str
    kind: str = "builtin"
    adapter_id: str = "claude"
    readiness_marker_fragment: str = ".claude/skills/start-task/SKILL.md"
    argv_template: list[str] | None = None
    command: str | None = None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class ExecutorRegistry:
    """Registry of all known executor profiles.

    Built-in profiles are loaded at import time and cannot be removed.
    Custom profiles are loaded from org config and workspace registrations.
    """

    def __init__(self) -> None:
        self._profiles: dict[str, ExecutorProfile] = {}
        self._register_builtins()

    def _register_builtins(self) -> None:
        """Register the four built-in executor profiles."""
        builtins = [
            ExecutorProfile(
                name="claude",
                kind="builtin",
                adapter_id="claude",
                readiness_marker_fragment=".claude/skills/start-task/SKILL.md",
            ),
            ExecutorProfile(
                name="codex",
                kind="builtin",
                adapter_id="codex",
                readiness_marker_fragment="AGENTS.md",
            ),
            ExecutorProfile(
                name="opencode",
                kind="builtin",
                adapter_id="opencode",
                readiness_marker_fragment="AGENTS.md",
            ),
            ExecutorProfile(
                name="pi",
                kind="builtin",
                adapter_id="pi",
                readiness_marker_fragment="AGENTS.md",
            ),
        ]
        for p in builtins:
            self._profiles[p.name] = p

    def get_profile(self, name: str) -> ExecutorProfile | None:
        """Return the profile for ``name``, or None if unregistered."""
        return self._profiles.get(name.lower())

    def is_registered(self, name: str) -> bool:
        """True when ``name`` resolves to a registered profile."""
        return name.lower() in self._profiles

    def list_profile_names(self) -> list[str]:
        """Return a sorted list of registered profile names."""
        return sorted(self._profiles.keys())

    def register_custom_profile(self, profile: ExecutorProfile) -> None:
        """Register a custom executor profile.

        Raises ValueError if the name collides with a built-in or if
        argv_template is invalid.
        """
        if profile.name.lower() in self._profiles:
            existing = self._profiles[profile.name.lower()]
            if existing.kind == "builtin":
                raise ValueError(
                    f"Cannot override built-in executor profile {profile.name!r}"
                )
        if profile.kind != "builtin":
            if profile.argv_template is None:
                raise ValueError(
                    f"Custom profile {profile.name!r} requires argv_template"
                )
            errors = validate_argv_template(profile.argv_template)
            if errors:
                raise ValueError(
                    f"Invalid argv_template for {profile.name!r}: {'; '.join(errors)}"
                )
        self._profiles[profile.name.lower()] = profile

    def register_custom_from_config(
        self, profiles: dict[str, dict]
    ) -> None:
        """Register custom profiles from an org-config executor_profiles block.

        Each entry: ``{name: {command, argv_template, adapter?}}``.
        ``command`` is the resolved executable (or None for skip).
        """
        for name, cfg in profiles.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"executor_profiles key must be a non-empty string")
            if not isinstance(cfg, dict):
                raise ValueError(f"executor_profiles.{name} must be a mapping")
            command = cfg.get("command")
            argv_template = cfg.get("argv_template")
            adapter = cfg.get("adapter", "pi")
            if not isinstance(argv_template, list) or not argv_template:
                raise ValueError(
                    f"executor_profiles.{name}.argv_template required"
                )
            if not isinstance(adapter, str) or adapter not in {
                "claude", "codex", "opencode", "pi",
            }:
                raise ValueError(
                    f"executor_profiles.{name}.adapter must be one of "
                    f"claude/codex/opencode/pi, got {adapter!r}"
                )
            # Resolve command — None means skip which (e.g., in tests)
            if command is not None and not isinstance(command, str):
                raise ValueError(
                    f"executor_profiles.{name}.command must be a string"
                )
            if command is not None:
                resolved = shutil.which(command)
                if resolved is None:
                    raise ValueError(
                        f"executor_profiles.{name}: command {command!r} "
                        f"not found on PATH"
                    )
            marker = "AGENTS.md" if adapter in {"codex", "opencode", "pi"} else ".claude/skills/start-task/SKILL.md"
            profile = ExecutorProfile(
                name=name,
                kind="custom",
                adapter_id=adapter,
                readiness_marker_fragment=marker,
                argv_template=[str(e) for e in argv_template],
                command=command,
            )
            self.register_custom_profile(profile)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_registry: ExecutorRegistry | None = None


def get_registry() -> ExecutorRegistry:
    """Return the process-wide executor registry singleton."""
    global _registry
    if _registry is None:
        _registry = ExecutorRegistry()
    return _registry


def reset_registry() -> None:
    """Reset the registry singleton (test seam)."""
    global _registry
    _registry = ExecutorRegistry()


# ---------------------------------------------------------------------------
# Executor factory — resolves a profile name to an executor instance
# ---------------------------------------------------------------------------


def build_executor(
    name: str,
    settings: Settings,
    paths: OrgPaths | None = None,
) -> "AgentExecutor":
    """Build an executor instance for a registered profile name.

    For built-in profiles, returns the specialized executor class
    (ClaudeExecutor, CodexExecutor, etc.). For custom profiles, returns a
    GenericCliExecutor configured from the profile's argv_template.

    Raises ValueError if the name is not registered.
    """
    from runtime.orchestrator.executors import (
        ClaudeExecutor,
        CodexExecutor,
        OpencodeExecutor,
        PiExecutor,
        GenericCliExecutor,
    )

    registry = get_registry()
    profile = registry.get_profile(name)
    if profile is None:
        raise ValueError(
            f"Unregistered executor {name!r}. "
            f"Registered: {', '.join(registry.list_profile_names())}"
        )

    if profile.name == "claude":
        return ClaudeExecutor(
            claude_cli_path=settings.claude_cli_path,
            permission_mode=settings.permission_mode,
            settings=settings,
            paths=paths,
        )
    if profile.name == "codex":
        return CodexExecutor(
            codex_cli_path=settings.codex_cli_path,
            sandbox_mode=settings.codex_sandbox_mode,
        )
    if profile.name == "opencode":
        return OpencodeExecutor(
            opencode_cli_path=settings.opencode_cli_path,
        )
    if profile.name == "pi":
        return PiExecutor(
            pi_cli_path=settings.pi_cli_path,
        )

    # Custom profile — GenericCliExecutor
    assert profile.argv_template is not None
    return GenericCliExecutor(
        profile_name=name,
        argv_template=profile.argv_template,
        provider=name,
    )
