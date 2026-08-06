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
    from runtime.infrastructure.direct_connect_store import DirectConnectStore

# ── THR-107 v9 Slice 1: module-level store reference for COMMITTED-only eligibility ──
# Set by the daemon on startup; tests inject via set_direct_connect_store_for_tests.
_direct_connect_store: "DirectConnectStore | None" = None


def set_direct_connect_store_for_tests(store: "DirectConnectStore | None") -> None:
    """Test seam: inject a DirectConnectStore for eligibility checks."""
    global _direct_connect_store
    _direct_connect_store = store

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

    ``workspace_adapter_id`` (D6 canonical) selects the workspace preparation
    adapter — which bootstrap files to write, which permission surface to
    configure. One of ``"claude"``, ``"codex"``, ``"opencode"``, ``"pi"``.
    This is the field consumers should read; ``adapter_id`` is a deprecated
    read-compatible alias that MUST match ``workspace_adapter_id``.

    ``command_adapter_id`` (D6 canonical) selects the command execution
    adapter — which executor builds argv and parses output. For built-in
    profiles this is the same as ``workspace_adapter_id`` (each built-in
    carries its own first-party command adapter). For custom profiles
    this may be ``"generic-cli"`` (template-based generic CLI) or
    ``"custom-adapter:<id>"`` (bound to a separately registered,
    founder-approved, hash-verified custom-adapter executable — D7B,
    subprocess-only, mandatory v1 AdapterInput/AdapterOutput, D5
    baseline-only posture, no permission expansion).
    ``command_adapter`` is a deprecated read-compatible alias.

    ``readiness_marker_fragment`` is a relative path within the workspace
    that, when present, signals the workspace is ready. The orchestrator
    checks for it before launching.

    ``argv_template`` (custom profiles only) is the argv list the
    GenericCliExecutor expands from placeholders at launch time. Built-in
    profiles leave this ``None`` — their factories supply their own argv.

    ``command`` (custom profiles only) is the declared executable name
    recorded in the profile definition. The machine-local binary registry
    (``executors.json``) keyed by the profile name is the sole source of
    truth for binary resolution at launch time (THR-107 seq155). Built-in
    profiles resolve through the same profile-name registry key (no
    Settings-path bypass).

    ``model_arg`` (optional) is an argv-TEMPLATE list containing a single
    ``{model}`` placeholder that each executor splices into its CLI argv
    when the agent has a model set. Unset (None) → CLI default model.
    Pre-seeded on the four built-in profiles with each CLI's verified flag.

    ``envelope_policy`` (D7A, custom profiles only) is the result-envelope
    enforcement posture. ``None`` (the default) is LEGACY COMPATIBILITY:
    the v1 envelope is optional and absence preserves pre-D7A behavior.
    ``"strict"`` is D7A mandatory enforcement: GenericCliExecutor fails
    closed on any missing, malformed, or invalid-version envelope with a
    deterministic error message guiding re-registration/verification.
    New registrations through either shipping route automatically receive
    ``"strict"``; existing stored profiles without this field are never
    auto-mutated. Built-in profiles always have ``None``.

    **D6 migration:** ``workspace_adapter_id`` and ``command_adapter_id`` are
    the canonical identity fields. ``adapter_id`` and ``command_adapter`` are
    deprecated read-compatible aliases that MUST match their canonical
    counterparts — conflicting values raise ``ValueError`` at construction
    time BEFORE any durable-store mutation, registry mutation, audit write,
    or token consumption.
    """

    name: str
    kind: str = "builtin"
    workspace_adapter_id: str = "claude"
    command_adapter_id: str | None = None
    readiness_marker_fragment: str = ".claude/skills/start-task/SKILL.md"
    argv_template: list[str] | None = None
    command: str | None = None
    model_arg: list[str] | None = None
    # ── Deprecated read-compatible aliases (D6) ────────────────────────
    # MUST match canonical fields; conflict → ValueError.
    adapter_id: str = "claude"
    command_adapter: str | None = None

    # ── D7A envelope enforcement (custom profiles only) ────────────────
    # None = legacy compatibility, "strict" = mandatory v1 enforcement
    envelope_policy: str | None = None

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

        # Command adapter resolution (both default to None)
        cmd = self.command_adapter_id
        cmd_legacy = self.command_adapter

        if cmd_legacy is not None and cmd is None:
            object.__setattr__(self, "command_adapter_id", cmd_legacy)
        elif cmd is not None and cmd_legacy is None:
            object.__setattr__(self, "command_adapter", cmd)
        elif cmd != cmd_legacy:
            raise ValueError(
                f"ExecutorProfile {self.name!r}: conflicting command adapter "
                f"identifiers — canonical command_adapter_id={cmd!r}, "
                f"deprecated command_adapter={cmd_legacy!r}. Use only "
                f"command_adapter_id; command_adapter is a deprecated alias."
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
            ValueError: if the name collides with a built-in or if
                argv_template is invalid.

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
        if profile.kind != "builtin":
            # D7B: custom-adapter profiles skip argv_template validation
            cmd_adapter = profile.command_adapter_id or ""
            if not cmd_adapter.startswith("custom-adapter:"):
                if profile.argv_template is None:
                    raise ValueError(
                        f"Custom profile {profile.name!r} requires argv_template"
                    )
                errors = validate_argv_template(profile.argv_template)
                if errors:
                    raise ValueError(
                        f"Invalid argv_template for {profile.name!r}: {'; '.join(errors)}"
                    )
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
            ValueError: if ``profile.name`` collides with a built-in or if
                ``argv_template`` is invalid.
        """
        key = profile.name.lower()
        existing = self._profiles.get(key)
        if existing is not None and existing.kind == "builtin":
            raise ValueError(
                f"Cannot replace built-in executor profile {profile.name!r}"
            )
        # Validate argv_template for custom profiles (same as register_custom_profile)
        if profile.kind != "builtin":
            cmd_adapter = profile.command_adapter_id or ""
            if not cmd_adapter.startswith("custom-adapter:"):
                if profile.argv_template is None:
                    raise ValueError(
                        f"Custom profile {profile.name!r} requires argv_template"
                    )
                errors = validate_argv_template(profile.argv_template)
                if errors:
                    raise ValueError(
                        f"Invalid argv_template for {profile.name!r}: {'; '.join(errors)}"
                    )
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

        THR-107 v9 Slice 1: COMMITTED-only launch fence. Only a durable
        matching COMMITTED direct_connect_operation grants eligibility.
        When the module-level ``_direct_connect_store`` is available,
        the adapter store check is gated behind a COMMITTED operation check;
        profiles without a COMMITTED record fail closed.
        """
        global _direct_connect_store
        cmd_adapter = profile.command_adapter_id or ""
        if not cmd_adapter.startswith("custom-adapter:"):
            return None
        adapter_id = cmd_adapter[len("custom-adapter:"):]

        # ── THR-107 v9 Slice 1: COMMITTED-only authority fence ────────────
        # A profile must have a durable COMMITTED direct-connect operation;
        # no other state (YAML, registry, approval, pending, bind, recovery,
        # token text) is accepted as launch authority.
        if _direct_connect_store is not None:
            op = _direct_connect_store.get_committed_operation(
                profile.name, adapter_id
            )
            if op is None:
                return None

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

        Raises ``ValueError`` for: invalid adapter, missing/bad argv_template,
        unsupported placeholder, declared-name mismatch, non-string command.
        """
        if not isinstance(name, str) or not name:
            raise ValueError(f"executor_profiles key must be a non-empty string")
        if not isinstance(cfg, dict):
            raise ValueError(f"executor_profiles.{name} must be a mapping")
        command = cfg.get("command")
        argv_template = cfg.get("argv_template")
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
        # ── D6: dual-read command adapter (canonical command_adapter_id
        #     wins over deprecated command_adapter; conflict → error) ─────
        command_adapter = cfg.get("command_adapter")
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
            elif command_adapter_id_from_cfg not in {"generic-cli"}:
                raise ValueError(
                    f"executor_profiles.{name}.command_adapter_id must be "
                    f"'generic-cli' or 'custom-adapter:<id>', got "
                    f"{command_adapter_id_from_cfg!r}"
                )
            if command_adapter is not None and command_adapter != command_adapter_id_from_cfg:
                raise ValueError(
                    f"executor_profiles.{name}: conflicting command "
                    f"adapter — canonical command_adapter_id="
                    f"{command_adapter_id_from_cfg!r}, deprecated "
                    f"command_adapter={command_adapter!r}. Use "
                    f"command_adapter_id; command_adapter is a "
                    f"deprecated alias."
                )
            command_adapter = command_adapter_id_from_cfg
        elif command_adapter is None:
            command_adapter = "generic-cli"
        elif command_adapter is not None:
            if not isinstance(command_adapter, str):
                raise ValueError(
                    f"executor_profiles.{name}.command_adapter must be a "
                    f"string, got {type(command_adapter).__name__}"
                )
            if command_adapter.startswith("custom-adapter:"):
                custom_adapter_id = command_adapter[len("custom-adapter:"):]
                if not custom_adapter_id:
                    raise ValueError(
                        f"executor_profiles.{name}.command_adapter: "
                        f"'custom-adapter:' requires a non-empty adapter id"
                    )
                # Defer adapter validation until after conflict check
            elif command_adapter not in {"generic-cli"}:
                raise ValueError(
                    f"executor_profiles.{name}.command_adapter must be "
                    f"'generic-cli' or 'custom-adapter:<id>', got "
                    f"{command_adapter!r}"
                )
        # ── end command adapter dual-read ────────────────────────────────
        # ── D7B: custom-adapter profiles skip argv_template/command validation ─
        # Validate the adapter binding AFTER conflict checks complete
        if custom_adapter_id is not None:
            cls._validate_custom_adapter_binding(custom_adapter_id)
        is_custom_adapter = custom_adapter_id is not None
        if not is_custom_adapter:
            # Validate argv_template placeholders (generic-cli only)
            if not isinstance(argv_template, list) or not argv_template:
                raise ValueError(
                    f"executor_profiles.{name}.argv_template required "
                    f"for generic-cli profiles"
                )
            argv_errors = validate_argv_template([str(e) for e in argv_template])
            if argv_errors:
                raise ValueError(
                    f"Invalid argv_template for {name!r}: {'; '.join(argv_errors)}"
                )
            # Resolve command — the declared executable name (informational only;
            # the machine-local binary registry is the sole resolution source).
            # None means skip validation (e.g., in tests).
            if command is not None and not isinstance(command, str):
                raise ValueError(
                    f"executor_profiles.{name}.command must be a string"
                )

            argv0 = [str(e) for e in argv_template][0]
            if command is not None and argv0:
                # Command and argv_template[0] must be the same declared name.
                # The machine-local binary registry (executors.json) keyed by
                # the profile name is the sole resolution source at launch
                # (THR-107 seq155) — neither shutil.which nor PATH discovery
                # is used here.
                if command != argv0:
                    raise ValueError(
                        f"executor_profiles.{name}: command {command!r} and "
                        f"argv_template[0] {argv0!r} must be the same executable "
                        f"name. The profile name is the binary registry key."
                    )
            elif command is not None and not argv0:
                raise ValueError(
                    f"executor_profiles.{name}: argv_template[0] is empty. "
                    f"The first element must be the executable name matching "
                    f"'command'."
                )
        # ── D7A: envelope policy validation ─────────────────────────
        envelope_policy = cfg.get("envelope_policy")
        if envelope_policy is not None:
            if not isinstance(envelope_policy, str):
                raise ValueError(
                    f"executor_profiles.{name}.envelope_policy must be a string, "
                    f"got {type(envelope_policy).__name__}"
                )
            if envelope_policy not in {"strict"}:
                raise ValueError(
                    f"executor_profiles.{name}.envelope_policy must be 'strict' "
                    f"(the only supported value), got {envelope_policy!r}"
                )
        # ── end envelope policy validation ──────────────────────────
        # ── D7B: custom-adapter profiles skip argv_template/command ────────
        if is_custom_adapter:
            marker = "AGENTS.md" if adapter in {"codex", "opencode", "pi"} else ".claude/skills/start-task/SKILL.md"
            return ExecutorProfile(
                name=name,
                kind="custom",
                workspace_adapter_id=adapter,
                command_adapter_id=command_adapter,
                readiness_marker_fragment=marker,
                argv_template=None,  # custom adapter uses AdapterInput, not argv_template
                command=None,  # adapter executable is resolved from adapter store, not PATH
                envelope_policy=None,  # custom adapters speak AdapterOutput contract natively
            )
        marker = "AGENTS.md" if adapter in {"codex", "opencode", "pi"} else ".claude/skills/start-task/SKILL.md"
        return ExecutorProfile(
            name=name,
            kind="custom",
            workspace_adapter_id=adapter,
            command_adapter_id=command_adapter,
            readiness_marker_fragment=marker,
            argv_template=[str(e) for e in argv_template],
            command=command,
            envelope_policy=envelope_policy,
        )


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
                f"launchable: no durable COMMITTED direct-connect operation found. "
                f"Complete the direct connect flow for this adapter."
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

    # Custom profile — GenericCliExecutor (unchanged, no adapter injected)
    assert profile.argv_template is not None
    return GenericCliExecutor(
        profile_name=name,
        argv_template=profile.argv_template,
        provider=name,
        envelope_policy=profile.envelope_policy,
    )
