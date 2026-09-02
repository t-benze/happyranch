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
  - for custom profiles: a registered ``custom-adapter:<id>`` binding
"""

from __future__ import annotations

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

_CUSTOM_ADAPTER_GUIDANCE = (
    "Register and approve an adapter executable, then bind this profile with "
    "command_adapter_id='custom-adapter:<id>'. Legacy generic-cli, command, "
    "argv_template, and omitted command_adapter_id profiles are retired."
)


# ---------------------------------------------------------------------------
# Profile definition
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExecutorProfile:
    """A registered executor profile.

    ``name`` is the string agents use in their AgentDef frontmatter
    (``org/agents/<name>.md``) ``executor:`` field. It must be lower-case
    and non-empty.

    ``kind`` is ``"builtin"`` for the four built-in adapters (claude, codex,
    opencode, pi) and ``"custom"`` for user-registered CLI profiles.

    ``workspace_adapter_id`` (D6 canonical) selects the workspace preparation
    adapter — which bootstrap files to write, which permission surface to
    configure. One of ``"claude"``, ``"codex"``, ``"opencode"``, ``"pi"``.
    This is the field consumers should read; ``adapter_id`` is a deprecated
    read-compatible alias that MUST match ``workspace_adapter_id``.

    ``command_adapter_id`` (D6 canonical) selects the command execution
    adapter — which executor builds argv and parses output. For built-in
    profiles this is the same as ``workspace_adapter_id`` (each built-in
    carries its own first-party command adapter). For custom profiles
    this is ``"custom-adapter:<id>"`` (bound to a separately registered,
    founder-approved, hash-verified custom-adapter executable — D7B,
    subprocess-only, mandatory v1 AdapterInput/AdapterOutput, D5
    baseline-only posture, no permission expansion).

    ``readiness_marker_fragment`` is a relative path within the workspace
    that, when present, signals the workspace is ready. The orchestrator
    checks for it before launching.

    ``model_arg`` (optional) is an argv-TEMPLATE list containing a single
    ``{model}`` placeholder that each executor splices into its CLI argv
    when the agent has a model set. Unset (None) → CLI default model.
    Pre-seeded on the four built-in profiles with each CLI's verified flag.

    ``workspace_adapter_id`` and ``command_adapter_id`` are the canonical
    identity fields. Workspace aliases remain read-compatible; the retired
    generic command-adapter alias is not accepted.
    """

    name: str
    kind: str = "builtin"
    workspace_adapter_id: str = "claude"
    command_adapter_id: str | None = None
    readiness_marker_fragment: str = ".claude/skills/start-task/SKILL.md"
    model_arg: list[str] | None = None
    # ── Deprecated read-compatible aliases (D6) ────────────────────────
    # MUST match canonical fields; conflict → ValueError.
    adapter_id: str = "claude"

    # ── D7A envelope enforcement (custom profiles only) ────────────────
    # None = legacy compatibility, "strict" = mandatory v1 enforcement

    def __post_init__(self):
        """Enforce D6 canonical-alias consistency.

        Dual-read resolution (deterministic):
        1. If only the deprecated alias is set (canonical is default),
           the canonical field is updated to match.
        2. If only the canonical field is set (alias is default),
           the alias is updated to match.
        3. If both are set and disagree, raise ValueError before any
           durable-store mutation, registry mutation, audit write, or
           token consumption.
        """
        # Workspace adapter resolution
        ws = self.workspace_adapter_id
        ad_legacy = self.adapter_id
        _DEFAULT_WS = "claude"

        if ad_legacy != _DEFAULT_WS and ws == _DEFAULT_WS:
            # Legacy-only: use deprecated alias value
            object.__setattr__(self, "workspace_adapter_id", ad_legacy)
        elif ws != _DEFAULT_WS and ad_legacy == _DEFAULT_WS:
            # Canonical-only: sync deprecated alias
            object.__setattr__(self, "adapter_id", ws)
        elif ws != ad_legacy:
            raise ValueError(
                f"ExecutorProfile {self.name!r}: conflicting workspace adapter "
                f"identifiers — canonical workspace_adapter_id={ws!r}, "
                f"deprecated adapter_id={ad_legacy!r}. Use only "
                f"workspace_adapter_id; adapter_id is a deprecated alias."
            )



# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ExecutorProfileCollisionError(ValueError):
    """Raised when a custom profile name collides with a different existing
    custom profile. This is a hard semantic conflict — two orgs define the
    same profile name with incompatible definitions. The operator must
    rename one of the profiles."""


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
        """Register the four built-in executor profiles.

        D8: Built-in profile metadata is NOW authoritative from the
        private first-party adapter catalog (``runtime/adapters``).
        No literal parallel built-in list or table remains in this
        file — the catalog is the single source of truth.

        D6: Built-in profiles use the canonical ``workspace_adapter_id``
        field. Their ``command_adapter_id`` matches ``workspace_adapter_id``
        (each built-in carries its own first-party command adapter).

        **Immutability:** Catalog descriptors store ``model_arg`` as
        immutable tuples. Each ``ExecutorProfile`` receives its own
        independent list copy so that profile-local mutation cannot
        alias into the catalog or other registries.
        """
        from runtime.adapters import get_builtin_catalog

        for desc in get_builtin_catalog():
            self._profiles[desc.name] = ExecutorProfile(
                name=desc.name,
                kind=desc.kind,
                workspace_adapter_id=desc.workspace_adapter_id,
                command_adapter_id=desc.command_adapter_id,
                readiness_marker_fragment=desc.readiness_marker_fragment,
                model_arg=list(desc.model_arg) if desc.model_arg is not None else None,
            )

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

        Raises:
            ExecutorProfileCollisionError: if the name collides with a
                different custom profile already registered (hard semantic
                conflict — the operator must resolve it by renaming).
            ValueError: if the name collides with a built-in or does not bind
                a registered custom adapter identity.

        If a custom profile with the same name AND identical definition is
        already registered, the call is a no-op (idempotent re-registration).
        """
        key = profile.name.lower()
        if key in self._profiles:
            existing = self._profiles[key]
            if existing.kind == "builtin":
                raise ValueError(
                    f"Cannot override built-in executor profile {profile.name!r}"
                )
            # Custom profile with same name: if identical -> no-op;
            # if different -> hard collision that the operator must resolve.
            if existing == profile:
                return  # idempotent re-registration
            raise ExecutorProfileCollisionError(
                f"Custom executor profile {profile.name!r} is already "
                f"registered with a different definition. The existing "
                f"profile and the new definition conflict; rename one of "
                f"the profiles to resolve the collision."
            )
        if profile.kind != "builtin" and not (profile.command_adapter_id or "").startswith("custom-adapter:"):
            raise ValueError(_CUSTOM_ADAPTER_GUIDANCE)
        self._profiles[key] = profile

    def unregister_custom_profile(self, name: str) -> bool:
        """Remove a registered CUSTOM executor profile.

        The symmetric inverse of :meth:`register_custom_profile` for the
        runtime-level management surface (THR-107 S4a). The durable
        runtime store is the source of truth; callers remove the store
        entry first and then clear the transient in-process profile via
        this method so the removed profile does not linger until restart.

        Returns True when a custom profile was removed, False when
        ``name`` is not registered (no-op).

        Raises:
            ValueError: if ``name`` is a built-in profile — built-ins are
                registered at construction time and can never be removed
                (same invariant :meth:`register_custom_profile` protects).
        """
        key = name.lower()
        existing = self._profiles.get(key)
        if existing is None:
            return False
        if existing.kind == "builtin":
            raise ValueError(
                f"Cannot unregister built-in executor profile {name!r}"
            )
        del self._profiles[key]
        return True

    def replace_custom_profile(self, profile: ExecutorProfile) -> bool:
        """Atomically replace an existing custom profile with a new definition.

        D7A atomic-replacement seam (TASK-3558): the single dict assignment
        ``self._profiles[key] = profile`` ensures any concurrent reader
        (``get_profile`` / ``build_executor``) observes either the complete
        old profile or the complete new profile — never absent. This
        eliminates the unregister-pause-register gap that the per-profile-name
        registration lock did not cover (executor launches do not acquire
        that lock).

        Returns True when an existing custom profile was replaced, False when
        ``profile.name`` is not registered (the profile is registered fresh
        instead).

        Raises:
            ValueError: if ``profile.name`` collides with a built-in or does
                not bind a registered custom adapter identity.
        """
        key = profile.name.lower()
        existing = self._profiles.get(key)
        if existing is not None and existing.kind == "builtin":
            raise ValueError(
                f"Cannot replace built-in executor profile {profile.name!r}"
            )
        # Apply the same canonical custom-adapter identity gate as registration.
        if profile.kind != "builtin" and not (profile.command_adapter_id or "").startswith("custom-adapter:"):
            raise ValueError(_CUSTOM_ADAPTER_GUIDANCE)
        self._profiles[key] = profile
        return existing is not None

    @classmethod
    def _resolve_custom_adapter_eligibility(cls, profile: ExecutorProfile) -> dict | None:
        """Check whether a custom-adapter profile is launchable.

        Returns a dict with ``{executable, hash, version, contract_version}``
        when the adapter is APPROVED, hash-verified, and on-disk executable
        is intact. Returns ``None`` when the adapter is pending, tampered,
        missing, or otherwise not launchable.

        This is the SINGLE central eligibility predicate consumed by:
        - ``/health/prereqs`` (present flag)
        - runtime-profile list (availability)
        - Agent-page selection data (``useExecutorOptions``)
        - ``build_executor`` (resolve → validate → build)
        - ``CustomAdapterExecutor`` (pre-launch verification)

        It performs the same exact resolve_adapter → hash check as the
        launch path without side effects.
        """
        cmd_adapter = profile.command_adapter_id or ""
        if not cmd_adapter.startswith("custom-adapter:"):
            return None
        adapter_id = cmd_adapter[len("custom-adapter:"):]
        from runtime.orchestrator.custom_adapter_registry import resolve_adapter
        entry = resolve_adapter(adapter_id)
        if entry is None:
            return None
        return {
            "executable": entry.executable,
            "hash": entry.executable_hash,
            "version": entry.version,
            "contract_version": entry.contract_version,
            "dependency_manifest_version": entry.dependency_manifest_version,
            "dependencies": entry.dependencies,
        }

    @classmethod
    def _validate_custom_adapter_binding(cls, adapter_id: str) -> dict:
        """Validate that an adapter id refers to an APPROVED, hash-verified adapter.

        Returns a dict with adapter metadata (executable, hash, version,
        contract_version) on success.

        Raises ValueError when:
          - adapter is unknown
          - adapter is not APPROVED (PENDING or other status)
          - adapter executable no longer exists / not regular file / not executable
          - adapter hash doesn't match (tampered)
        """
        from runtime.orchestrator.custom_adapter_registry import resolve_adapter
        entry = resolve_adapter(adapter_id)
        if entry is None:
            # Check if it exists but isn't approved/hash-verified
            from runtime.orchestrator.adapter_store import get_adapter
            existing = get_adapter(adapter_id)
            if existing is None:
                raise ValueError(
                    f"Unknown adapter {adapter_id!r}. Register the adapter "
                    f"executable first, then approve it before binding."
                )
            if existing.status != "approved":
                raise ValueError(
                    f"Adapter {adapter_id!r} is status={existing.status!r}, "
                    f"not APPROVED. Founder approval is required before "
                    f"binding to a profile."
                )
            raise ValueError(
                f"Adapter {adapter_id!r} is approved but the on-disk "
                f"executable is missing, not a regular file, not executable, "
                f"or has a hash mismatch. Re-register the adapter."
            )
        return {
            "executable": entry.executable,
            "hash": entry.executable_hash,
            "version": entry.version,
            "contract_version": entry.contract_version,
        }

    @classmethod
    def validate_custom_profile_config(
        cls, name: str, cfg: dict
    ) -> ExecutorProfile:
        """Validate a custom profile config entry and return the built
        ExecutorProfile WITHOUT registering it.

        This is the CANONICAL validation path. The register routes and
        the runtime-store startup load (``DaemonState.from_runtime``)
        drive through this method so validation can never silently
        diverge.

        Raises ``ValueError`` for invalid workspace or command-adapter identity.
        """
        if not isinstance(name, str) or not name:
            raise ValueError(f"executor_profiles key must be a non-empty string")
        if not isinstance(cfg, dict):
            raise ValueError(f"executor_profiles.{name} must be a mapping")
        if "command" in cfg or "argv_template" in cfg or "envelope_policy" in cfg:
            raise ValueError(_CUSTOM_ADAPTER_GUIDANCE)
        # ── D6: dual-read workspace adapter (canonical workspace_adapter_id
        #     wins over deprecated adapter/adapter_id; conflict → error) ──
        #     Presence detection: if any two explicitly-supplied keys disagree,
        #     raise ValueError BEFORE any durable/registry/audit/token side effect.
        adapter = cfg.get("adapter", "pi")
        # Collect explicitly-supplied workspace adapter identifiers
        explicit_ws_keys: dict[str, str] = {}
        if "workspace_adapter_id" in cfg:
            val = cfg["workspace_adapter_id"]
            if isinstance(val, str):
                explicit_ws_keys["workspace_adapter_id"] = val
        if "adapter" in cfg:
            val = cfg["adapter"]
            if isinstance(val, str):
                explicit_ws_keys["adapter"] = val
        if "adapter_id" in cfg:
            val = cfg["adapter_id"]
            if isinstance(val, str):
                explicit_ws_keys["adapter_id"] = val
        # Detect conflicting explicitly-supplied values
        unique_values = list(set(explicit_ws_keys.values()))
        if len(unique_values) > 1:
            raise ValueError(
                f"executor_profiles.{name}: conflicting workspace adapter "
                f"identifiers — got {explicit_ws_keys!r}. Use only "
                f"workspace_adapter_id; adapter and adapter_id are "
                f"deprecated aliases."
            )
        # Resolve: canonical wins, then adapter, then adapter_id, then default
        if explicit_ws_keys:
            if "workspace_adapter_id" in explicit_ws_keys:
                adapter = explicit_ws_keys["workspace_adapter_id"]
            elif "adapter" in explicit_ws_keys:
                adapter = explicit_ws_keys["adapter"]
            elif "adapter_id" in explicit_ws_keys:
                adapter = explicit_ws_keys["adapter_id"]
        # Validate resolved value
        if not isinstance(adapter, str) or adapter not in {
            "claude", "codex", "opencode", "pi",
        }:
            raise ValueError(
                f"executor_profiles.{name}.workspace_adapter_id must be "
                f"one of claude/codex/opencode/pi, got "
                f"{adapter!r}"
            )
        # ── end workspace adapter dual-read ───────────────────────────────
        if "command_adapter" in cfg:
            raise ValueError(_CUSTOM_ADAPTER_GUIDANCE)
        command_adapter_id_from_cfg = cfg.get("command_adapter_id")
        # ── D7B: detect custom-adapter binding early ────────────────
        custom_adapter_id: str | None = None
        if command_adapter_id_from_cfg is not None:
            if not isinstance(command_adapter_id_from_cfg, str):
                raise ValueError(
                    f"executor_profiles.{name}.command_adapter_id must be "
                    f"a string, got {type(command_adapter_id_from_cfg).__name__}"
                )
            if command_adapter_id_from_cfg.startswith("custom-adapter:"):
                custom_adapter_id = command_adapter_id_from_cfg[len("custom-adapter:"):]
                if not custom_adapter_id:
                    raise ValueError(
                        f"executor_profiles.{name}.command_adapter_id: "
                        f"'custom-adapter:' requires a non-empty adapter id"
                    )
                # Defer adapter validation until after conflict check
            else:
                raise ValueError(
                    f"executor_profiles.{name}.command_adapter_id must be "
                    f"'custom-adapter:<id>', got "
                    f"{command_adapter_id_from_cfg!r}"
                )
        else:
            raise ValueError(_CUSTOM_ADAPTER_GUIDANCE)
        # Validate the adapter binding AFTER conflict checks complete
        if custom_adapter_id is not None:
            cls._validate_custom_adapter_binding(custom_adapter_id)
        if custom_adapter_id is not None:
            marker = "AGENTS.md" if adapter in {"codex", "opencode", "pi"} else ".claude/skills/start-task/SKILL.md"
            return ExecutorProfile(
                name=name,
                kind="custom",
                workspace_adapter_id=adapter,
                command_adapter_id=command_adapter_id_from_cfg,
                readiness_marker_fragment=marker,
            )
        raise ValueError(_CUSTOM_ADAPTER_GUIDANCE)


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

    For built-in profiles, returns the specialized executor class. Custom
    profiles resolve only through an approved registered custom adapter.

    Raises ValueError if the name is not registered.
    """
    from runtime.orchestrator.executors import (
        ClaudeExecutor,
        CodexExecutor,
        OpencodeExecutor,
        PiExecutor,
    )

    registry = get_registry()
    profile = registry.get_profile(name)
    if profile is None:
        raise ValueError(
            f"Unregistered executor {name!r}. "
            f"Registered: {', '.join(registry.list_profile_names())}"
        )

    # ── THR-107 D2/D10: first-party adapter resolution ────────────────────
    # Resolve the first-party adapter for this profile name (built-ins only;
    # custom profiles return None). When an adapter is available, inject it
    # into the executor so argv construction delegates through the adapter
    # boundary instead of the inline hard-coded construction.
    #
    # D10/D11 Phase-4 (this commit): the if/elif chain below was replaced by
    # a static data-driven factory dict derived from the D8 catalog. The
    # adapter is resolved exactly as before and passed to the factory.
    # ───────────────────────────────────────────────────────────────────────
    try:
        from runtime.adapters import get_first_party_adapter
        adapter_cls = get_first_party_adapter(profile.name)
    except ImportError:
        adapter_cls = None

    adapter_instance = adapter_cls() if adapter_cls is not None else None

    # ── D10/D11 Phase-4: Static data-driven factory (replaces D2 if/elif chain) ──
    #
    # Companion static mapping derived from the D8 authoritative built-in catalog
    # (runtime/adapters/__init__.py:_BUILTIN_CATALOG). Each entry maps a built-in
    # profile name to a factory callable that produces the specialised executor.
    # No imperative per-provider dispatch — the name-to-factory mapping is a
    # static data declaration, not a chain of conditionals.
    #
    # Rollback: revert this commit to restore the D2 if/elif chain.
    # ───────────────────────────────────────────────────────────────────────
    _BUILTIN_EXECUTOR_FACTORIES: dict[str, object] = {
        "claude": lambda s, p, pr, a, n=name: ClaudeExecutor(
            claude_cli_path=s.claude_cli_path,
            permission_mode=s.permission_mode,
            settings=s,
            paths=p,
            model_arg=pr.model_arg,
            profile_name=n,
            adapter=a,
        ),
        "codex": lambda s, p, pr, a, n=name: CodexExecutor(
            codex_cli_path=s.codex_cli_path,
            sandbox_mode=s.codex_sandbox_mode,
            model_arg=pr.model_arg,
            profile_name=n,
            adapter=a,
        ),
        "opencode": lambda s, p, pr, a, n=name: OpencodeExecutor(
            opencode_cli_path=s.opencode_cli_path,
            model_arg=pr.model_arg,
            profile_name=n,
            adapter=a,
        ),
        "pi": lambda s, p, pr, a, n=name: PiExecutor(
            pi_cli_path=s.pi_cli_path,
            model_arg=pr.model_arg,
            profile_name=n,
            adapter=a,
        ),
    }

    factory = _BUILTIN_EXECUTOR_FACTORIES.get(profile.name.lower())
    if factory is not None:
        return factory(settings, paths, profile, adapter_instance)

    # ── D7B: Custom-adapter profile routing ──────────────────────────────
    cmd_adapter = profile.command_adapter_id or ""
    if cmd_adapter.startswith("custom-adapter:"):
        # Central eligibility check — same predicate as health/prereqs,
        # runtime profiles, Agent-page, and CustomAdapterExecutor pre-launch.
        binding = ExecutorRegistry._resolve_custom_adapter_eligibility(profile)
        if binding is None:
            adapter_id = cmd_adapter[len("custom-adapter:"):]
            raise ValueError(
                f"Custom adapter {adapter_id!r} for profile {name!r} is not "
                f"launchable: adapter is pending, tampered, missing, or not "
                f"approved. Register, approve, and bind the adapter first."
            )
        from runtime.orchestrator.executors import CustomAdapterExecutor
        executor = CustomAdapterExecutor(
            profile_name=name,
            adapter_entry_id=cmd_adapter[len("custom-adapter:"):],
            adapter_executable=binding["executable"],
            adapter_hash=binding["hash"],
            adapter_version=binding["version"],
            adapter_contract_version=binding["contract_version"],
            provider=name,
        )
        # THR-107 seq244: set dependency manifest on the executor
        executor.set_dependency_manifest(
            binding.get("dependency_manifest_version"),
            binding.get("dependencies", []),
        )
        return executor

    raise ValueError(_CUSTOM_ADAPTER_GUIDANCE)
