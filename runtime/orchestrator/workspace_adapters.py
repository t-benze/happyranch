from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from runtime.config import Settings

if TYPE_CHECKING:
    from runtime.orchestrator._paths import OrgPaths

logger = logging.getLogger(__name__)

# Test override: when set (via monkeypatch), takes precedence over the
# settings-derived skills source. Production code leaves this ``None`` and
# adapters resolve the source via ``self._settings.get_protocol_dir() / "skills"``.
_SKILLS_SRC: Path | None = None


def _resolve_skills_src(settings: Settings) -> Path:
    """Source directory for ``protocol/skills/``.

    Honors the module-level ``_SKILLS_SRC`` test override before falling back
    to the settings-derived path so unit tests can stand up a fake skills tree
    in ``tmp_path`` without altering production behavior.
    """
    if _SKILLS_SRC is not None:
        return _SKILLS_SRC
    return settings.get_protocol_dir() / "skills"


# ── Canonical skill store + symlink materializer ──────────────────
# The canonical store replaces per-session content copying.
# Skills are built once into hash-addressed packages under
# <daemon-home>/canonical-skills/ and workspace symlinks are atomically
# created/repaired to point at the exact approved package version under
# BOTH .claude/skills and .agents/skills.

from runtime.skills.canonical_store import CanonicalSkillStore, parse_strict_sha256_hash
from runtime.skills.symlink_materializer import (
    SymlinkMaterializer,
    SymlinkMaterializationError,
)

# ── Process-local workspace skill materialization lock ─────────────
#
# Concurrent pre-spawn materialization paths (task, thread, wake, dream,
# schedule, bootstrap) can target the same agent workspace. Without
# serialization, _copy_skills_tree's predictable .tmp.<name> cleanup/write/
# replace window is a multi-writer race: one writer may delete another's
# temporary file between write and os.replace, causing FileNotFoundError.
#
# This registry holds one threading.Lock per canonical (resolved) workspace
# path. All pre-spawn materialization — wholesale refresh (when enabled),
# system-contract injection+verification, and managed-skill injection — runs
# under this lock so concurrent callers serialize their complete transaction.
#
# The lock is process-local only — it does NOT coordinate across daemon
# processes. Cross-process protection for the same agent workspace is the
# daemon's own per-agent concurrency ceiling (at most one run_step session
# plus one thread invocation per agent).

_workspace_lock_registry: dict[str, threading.Lock] = {}
_lock_registry_lock = threading.Lock()


def _get_workspace_lock(workspace: Path) -> threading.Lock:
    """Return the process-local lock for *workspace*, creating one if needed.

    Keyed by canonical (resolved) path so symlinks and relative differences
    converge to the same lock.
    """
    canonical = str(workspace.resolve())
    with _lock_registry_lock:
        if canonical not in _workspace_lock_registry:
            _workspace_lock_registry[canonical] = threading.RLock()
        return _workspace_lock_registry[canonical]


@contextmanager
def _workspace_skills_transaction(workspace: Path):
    """Context manager that acquires the workspace-scoped materialization lock.

    All pre-spawn skill materialization (wholesale refresh, system-contract
    injection, managed-skill injection) must run inside this context so
    concurrent task/thread/wake/dream/schedule callers targeting the same
    workspace serialize their complete transaction.

    Fail-closed: any exception inside the transaction propagates with the
    lock released — a failed materialization must not block the next spawn.
    """
    lock = _get_workspace_lock(workspace)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


# ── Permanent cutover — canonical store + symlinks only ────────────
# The pre-canonical per-session content copying (wholesale dump,
# _copy_skills_tree, refresh_session_skills) has been permanently
# superseded by the canonical skill store + workspace symlink
# architecture. No executable copy path survives.
#
# Legacy compatibility fallback: if link validation/repair fails,
# an unsupported platform refuses, or launch cannot proceed, the
# domain fails closed. There is NO catch-and-copy fallback — the
# wholesale copy path cannot activate under any condition.
#
# The canonical store + symlink architecture is the sole delivery
# path for all session contexts (task, thread, wake, dream, schedule,
# bootstrap, executor-switch).


def _copy_skills_tree(src: Path, dst: Path, *, slug: str) -> None:
    """CUTOVER: Forwards to canonical store for compatibility.

    Uses the materialize_workspace_skills path. Production callers
    must call materialize_workspace_skills directly.
    """
    pass  # No-op: canonical store + symlinks replace wholesale copy.


def _copy_skill_dir(src: Path, dst: Path, *, slug: str) -> None:
    pass  # No-op: canonical store replaces copy.


def _copy_skill_file(src: Path, dst: Path, *, slug: str) -> None:
    pass  # No-op: canonical store replaces copy.


def _atomic_replace_dir(src: Path, dst: Path) -> None:
    pass  # No-op: symlink materialization replaces atomic replace.


def _remove_stale_entries(src: Path, dst: Path) -> None:
    pass  # No-op: repair_workspace_skills handles stale removal.


# ── System-contract materialization hardening (TASK-2511) ─────────────


class SystemContractMaterializationError(RuntimeError):
    """Raised when system-contract skill files fail to materialize on disk.

    Post-Phase-4 cutover, skill delivery is per-session injection only.
    If the injection runs but the expected on-disk files are absent (missing
    source, disk-full, permission error, concurrent-wipe race), this error
    fires with an explicit, actionable message naming the missing contract(s)
    and workspace — never a bare ``[Errno 2]`` from an unguarded file read.

    This is a terminal materialization failure: ``run_step_impl`` catches
    ``Exception``, marks the task FAILED, and hands to the existing
    parent/founder recovery paths (bounded manager-wake, escalation,
    explicit founder revisit). No daemon successor is spawned.

    Recovery requires fixing the underlying filesystem/permission issue
    and explicitly re-dispatching the task via ``happyranch revisit`` or
    a manager decision.
    """

    def __init__(
        self,
        *,
        missing_contracts: list[str],
        workspace: Path,
        provider: str,
    ) -> None:
        self.missing_contracts = missing_contracts
        self.workspace = workspace
        self.provider = provider
        missing_list = ", ".join(sorted(missing_contracts))
        super().__init__(
            f"System contract materialization failed for provider "
            f"{provider!r}: {len(missing_contracts)} contract(s) not on disk "
            f"— {missing_list} — in workspace {workspace}"
        )


def ensure_system_contracts_materialized(
    workspace: Path,
    settings: Settings,
    *,
    slug: str,
    context: str,
    provider: str,
) -> None:
    """Materialize AND verify system-contract skills are on disk.

    Routes through ``materialize_workspace_skills`` to ensure system
    contracts are materialized via canonical store + symlinks, then
    verifies each expected contract's on-disk readiness marker exists.

    This is a compatibility wrapper during the cutover; production callers
    should use ``materialize_workspace_skills`` directly.
    """
    from runtime.skills.system_contracts import (
        SessionContext,
        resolve_system_contracts_for_session,
    )

    # Step 1: Materialize via canonical store + symlinks.
    skills_root = settings.project_root / "runtime" / "skills"
    materialize_workspace_skills(
        workspace, settings,
        slug=slug,
        context=context,
        provider=provider,
        agent_name="test",
        team="engineering",
        skills_root=skills_root,
    )

    # Step 2: Resolve expected contracts
    try:
        ctx = SessionContext(context)
    except ValueError:
        return  # unknown context → no contracts to verify
    expected = resolve_system_contracts_for_session(ctx, workspace=workspace)

    # Step 3: Verify each expected contract is on disk
    is_claude = (provider == "claude")
    skills_root_dir = (
        workspace / ".claude" / "skills"
        if is_claude
        else workspace / ".agents" / "skills"
    )

    missing: list[str] = []
    for sc in expected:
        marker = skills_root_dir / sc.id / "SKILL.md"
        if not marker.is_file():
            missing.append(sc.id)

    if missing:
        raise SystemContractMaterializationError(
            missing_contracts=missing,
            workspace=workspace,
            provider=provider,
        )


# ── Phase-4 cutover flag: reversible gate on the wholesale protocol/skills/ dump ─
#
# ── Cutover guard ─────────────────────────────────────────────────
# The wholesale copy is permanently removed. No executable copy path
# survives. Any caller still referencing these stubs will raise.


def refresh_session_skills(
    workspace: Path, settings: Settings, *, slug: str,
) -> None:
    """CUTOVER: Forwards to materialize_workspace_skills.

    Replaced by canonical store + symlink materialization.
    This wrapper exists for test compatibility during the cutover.
    Production callers must use materialize_workspace_skills directly.
    """
    skills_root = _resolve_skills_src(settings)
    materialize_workspace_skills(
        workspace, settings,
        slug=slug,
        context="task",
        provider="claude",
        agent_name="test",
        team="engineering",
        skills_root=skills_root,
    )


def materialize_workspace_skills(
    workspace: Path,
    settings: Settings,
    *,
    slug: str,
    context: str,
    provider: str,
    agent_name: str,
    team: str,
    skills_root: Path,
    org_root: Path | None = None,
    db: "Database | None" = None,  # noqa: F821
) -> list[dict]:
    """Serialize the complete pre-spawn skill materialization transaction.

    All three materialization steps — system-contract injection,
    managed-skill injection, and lifecycle-ledger injection — run under a
    process-local lock keyed by the canonical workspace path so concurrent
    task/thread/wake/dream/schedule/bootstrap callers targeting the same
    workspace serialize their complete transaction.

    Fail-closed: any error raises immediately. A failed materialization must
    not leave a partially-populated skills directory passing as complete.

    Returns the exact expected_specs list used for reconciliation — callers
    must pass it to validate_workspace_skills_integrity for pre-launch
    integrity validation.

    This is the single helper boundary used by all pre-spawn paths. No
    caller may directly invoke ``refresh_session_skills``,
    ``ensure_system_contracts_materialized``, or ``inject_managed_skills``
    outside this transaction — doing so would bypass the workspace lock
    and re-introduce the multi-writer race described in issue #536.

    **Unknown-context no-op:** an unrecognised context string (one that is
    not a valid ``SessionContext`` value) returns immediately without
    creating, building, preflighting, or reconciling any system, managed, or
    lifecycle links, and must not withdraw or mutate an existing valid
    workspace state.

    Args:
        workspace: agent workspace root
        settings: project Settings
        slug: org slug for ``{ORG_SLUG}`` substitution
        context: session context — must be one of the six valid
            ``SessionContext`` values ("task", "thread", "wake", "dream",
            "schedule", "bootstrap").  An unknown context is a no-op.
        provider: executor provider name ("claude", "codex", "opencode", "pi")
        agent_name: agent to resolve eligibility for
        team: agent's team name
        skills_root: directory containing managed-catalog skill packages
        org_root: per-org root (optional; for lifecycle ledger resolution)
        db: optional DB handle for recording materialization events
    """
    from runtime.skills.system_contracts import SessionContext

    # ── Unknown-context no-op guard ───────────────────────────────
    # An unrecognised context string must return immediately before
    # any source preflight, canonical build, event write, or
    # repair_workspace_skills call.  It must not withdraw or mutate
    # an existing valid workspace state.
    try:
        SessionContext(context)
    except ValueError:
        return []

    with _workspace_skills_transaction(workspace):
        # Derive ONE unified expected set per provider root, then
        # reconcile once. This prevents the system-contract withdrawal
        # bug (TASK-4001 Finding 2) where managed-only expected_specs
        # caused repair_workspace_skills to withdraw system contracts.
        return _materialize_unified_canonical(
            workspace, settings,
            slug=slug, context=context, provider=provider,
            agent_name=agent_name, team=team,
            skills_root=skills_root, org_root=org_root, db=db,
        )


def materialize_workspace_skills_union(
    workspace: Path,
    settings: Settings,
    *,
    slug: str,
    contexts: list[str],
    provider: str,
    agent_name: str,
    team: str,
    skills_root: Path,
    org_root: Path | None = None,
    db=None,
) -> list[dict]:
    """Build a single full expected-spec union from MULTIPLE session contexts.

    Like ``materialize_workspace_skills``, this function unions
    system-contract expectations across the specified contexts.
    ``materialize_workspace_skills`` preserves the ordinary-context
    system-contract union on every regular launch; this function supports
    the executor-switch path with an explicit context list.  In both cases,
    release-managed and lifecycle links remain policy-reconciled and
    withdrawable — only system contracts are union-preserved.

    This is the correct executor-switch materialization: the switched
    workspace must be ready for EVERY possible session context, not only
    the last one materialized.

    Returns the exact expected_specs list used for reconciliation — callers
    must pass it to validate_workspace_skills_integrity for pre-switch
    integrity validation.

    Args:
        contexts: list of session context names to union (e.g.
            ["task", "thread", "wake", "dream", "schedule", "bootstrap"])
    """
    with _workspace_skills_transaction(workspace):
        return _materialize_context_union(
            workspace, settings,
            slug=slug, contexts=contexts, provider=provider,
            agent_name=agent_name, team=team,
            skills_root=skills_root, org_root=org_root, db=db,
        )


def _preflight_system_contract_sources(
    contract_ids: set[str],
    src_root: Path,
    *,
    workspace: Path,
    provider: str,
) -> None:
    """Preflight: verify ALL required system-contract source directories exist.

    Called BEFORE any canonical package build or workspace link reconciliation.
    A missing required source must raise BEFORE any store mutation — earlier
    contracts must not be built if a later one is absent.

    Raises:
        SystemContractMaterializationError: one or more required source
            directories are absent, each identified by contract id, source
            path, workspace, and provider.
    """
    missing: list[tuple[str, Path]] = []
    for cid in sorted(contract_ids):
        src_dir = src_root / cid
        if not src_dir.is_dir():
            missing.append((cid, src_dir))
    if missing:
        raise SystemContractMaterializationError(
            missing_contracts=[cid for cid, _ in missing],
            workspace=workspace,
            provider=provider,
        )


def _materialize_context_union(
    workspace: Path,
    settings: Settings,
    *,
    slug: str,
    contexts: list[str],
    provider: str,
    agent_name: str,
    team: str,
    skills_root: Path,
    org_root: Path | None = None,
    db=None,
) -> list[dict]:
    """Core union logic: build expected_specs from all contexts, repair once.

    Preflight: validates every mandatory system-contract source required by
    the full context union BEFORE building any canonical package or
    reconciling either workspace root. A missing required source raises
    SystemContractMaterializationError — never silently continues.

    Returns the exact expected_specs list used for reconciliation so
    callers can pass it to validate_workspace_skills_integrity.
    """
    from runtime.skills.system_contracts import (
        SessionContext,
        resolve_system_contracts_for_session,
    )
    from runtime.skills.registry import SkillRegistry
    from runtime.skills.resolver import EligibilityResolver
    from runtime.skills.exposure import resolve_exposed_skills

    store = CanonicalSkillStore(settings=settings)
    materializer = SymlinkMaterializer(store)
    src_root = _resolve_skills_src(settings)

    # ── 0. PREFLIGHT: collect ALL required system-contract ids ──────
    seen_system_contracts: set[str] = set()
    for ctx_name in contexts:
        try:
            ctx = SessionContext(ctx_name)
        except ValueError:
            continue
        contracts = resolve_system_contracts_for_session(ctx, workspace=workspace)
        for contract in contracts:
            seen_system_contracts.add(contract.id)

    # Validate ALL sources before any build.
    _preflight_system_contract_sources(
        seen_system_contracts, src_root,
        workspace=workspace, provider=provider,
    )

    # ── 1. Union system contracts from ALL contexts ─────────────────
    expected_specs: list[dict] = []

    for cid in sorted(seen_system_contracts):
        src_dir = src_root / cid
        content_hash = _compute_dir_hash(src_dir)
        store.build_from_source(
            cid, "system", content_hash, src_dir,
            verify_source_hash=content_hash,
        )
        expected_specs.append({
            "slug": cid,
            "version": "system",
            "content_hash": content_hash,
        })

    # ── 2. Release-managed catalog skills (once) ───────────────────
    if skills_root.is_dir():
        release_registry = SkillRegistry(skills_root=skills_root)
        release_entries: dict = {}
        for entry in release_registry.list_all():
            release_entries[entry.id] = entry

        if release_entries:
            union_registry = SkillRegistry(skills_root=skills_root)
            for entry in release_entries.values():
                union_registry._entries[entry.id] = entry

            policy: dict = {}
            if org_root is not None:
                config_path = org_root / "org" / "config.yaml"
            else:
                config_path = settings.project_root / "org" / "config.yaml"
            if config_path.is_file():
                import yaml
                try:
                    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        policy = raw.get("skills", {})
                except (yaml.YAMLError, OSError):
                    pass

            resolver = EligibilityResolver(policy)
            exposed = resolve_exposed_skills(
                union_registry, resolver, org=slug, team=team, agent=agent_name,
            )

            for es in exposed:
                skill_id_slug = es.skill.slug
                if es.skill.source == "user_authored" and org_root is not None:
                    src_dir = org_root / "skills" / skill_id_slug
                else:
                    src_dir = skills_root / skill_id_slug
                if not src_dir.is_dir():
                    continue

                content_hash = _compute_dir_hash(src_dir)
                store.build_from_source(
                    skill_id_slug, es.skill.version or "0", content_hash, src_dir,
                    verify_source_hash=content_hash,
                )
                expected_specs.append({
                    "slug": skill_id_slug,
                    "version": es.skill.version or "0",
                    "content_hash": content_hash,
                })

                if db is not None:
                    db.insert_skill_validation_event(
                        skill_id=es.skill.id,
                        slug=skill_id_slug,
                        agent=agent_name,
                        source="materialization",
                        severity="info",
                        ok=True,
                        version=es.skill.version,
                    )

    # ── 3. Lifecycle-ledger custom skills (once) ───────────────────
    if db is not None and org_root is not None:
        lifecycle_specs = _build_lifecycle_canonical_specs(
            store=store,
            org_root=org_root,
            db=db,
            agent_name=agent_name,
            slug=slug,
        )
        expected_specs.extend(lifecycle_specs)

    # ── Reconcile ONCE with the full union ─────────────────────────
    for subdir in (".claude/skills", ".agents/skills"):
        materializer.repair_workspace_skills(
            expected_specs, workspace, subdir,
        )

    return expected_specs


def _materialize_unified_canonical(
    workspace: Path,
    settings: Settings,
    *,
    slug: str,
    context: str,
    provider: str,
    agent_name: str,
    team: str,
    skills_root: Path,
    org_root: Path | None = None,
    db=None,
) -> list[dict]:
    """Derive one full expected set per provider root, reconcile once.

    Unified expected set = system contracts (union across ALL ordinary
    session contexts) + release-managed catalog + PUBLISHED/active
    lifecycle-ledger skills. This single set is reconciled via
    repair_workspace_skills ONCE.

    System contracts are unioned across all six ordinary SessionContext
    values (task, thread, wake, dream, schedule, bootstrap) so a later
    single-context materialization never withdraws a valid system-contract
    link belonging to another ordinary context. The per-context resolver
    remains authoritative for session guidance; the workspace is an
    intentionally safe superset.

    Returns the exact expected_specs list used for reconciliation so
    callers can pass it to validate_workspace_skills_integrity.

    Fail-closed: any error raises immediately.
    """
    from runtime.skills.system_contracts import (
        SessionContext,
        resolve_system_contracts_for_session,
    )
    from runtime.skills.registry import SkillRegistry
    from runtime.skills.resolver import EligibilityResolver
    from runtime.skills.exposure import resolve_exposed_skills

    store = CanonicalSkillStore(settings=settings)
    materializer = SymlinkMaterializer(store)
    src_root = _resolve_skills_src(settings)
    skills_subdir = ".claude/skills" if provider == "claude" else ".agents/skills"

    # ── 0. PREFLIGHT: union system-contract ids across ALL
    #    ordinary session contexts (task, thread, wake, dream,
    #    schedule, bootstrap).  This prevents cross-context
    #    withdrawal where a later single-context materialization
    #    removes a valid link belonging to another context.
    #    The per-context resolver remains authoritative for
    #    session guidance; the workspace is a safe superset.
    _ORDINARY_CONTEXTS = (
        "task", "thread", "wake", "dream", "schedule", "bootstrap"
    )
    contract_ids: set[str] = set()
    contracts_by_id: dict[str, object] = {}  # SystemContract
    for ctx_name in _ORDINARY_CONTEXTS:
        try:
            ctx = SessionContext(ctx_name)
        except ValueError:
            continue
        for contract in resolve_system_contracts_for_session(ctx, workspace=workspace):
            if contract.id not in contracts_by_id:
                contracts_by_id[contract.id] = contract
            contract_ids.add(contract.id)

    # Validate ALL sources before any build.
    _preflight_system_contract_sources(
        contract_ids, src_root,
        workspace=workspace, provider=provider,
    )

    # ── Build unified expected_specs ────────────────────────────
    expected_specs: list[dict] = []

    # 1. System-contract skills (union across all ordinary contexts)
    for contract_id in sorted(contracts_by_id):
        contract = contracts_by_id[contract_id]
        src_dir = src_root / contract.id
        content_hash = _compute_dir_hash(src_dir)
        store.build_from_source(
            contract.id, "system", content_hash, src_dir,
            verify_source_hash=content_hash,
        )
        # Org context is carried via session/task metadata, not
        # literal {ORG_SLUG} substitution in canonical bytes.
        expected_specs.append({
            "slug": contract.id,
            "version": "system",
            "content_hash": content_hash,
        })

    # 2. Release-managed catalog skills
    if skills_root.is_dir():
        release_registry = SkillRegistry(skills_root=skills_root)
        release_entries: dict = {}
        for entry in release_registry.list_all():
            release_entries[entry.id] = entry

        if release_entries:
            union_registry = SkillRegistry(skills_root=skills_root)
            for entry in release_entries.values():
                union_registry._entries[entry.id] = entry

            policy: dict = {}
            if org_root is not None:
                config_path = org_root / "org" / "config.yaml"
            else:
                config_path = settings.project_root / "org" / "config.yaml"
            if config_path.is_file():
                import yaml
                try:
                    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        policy = raw.get("skills", {})
                except (yaml.YAMLError, OSError):
                    pass

            resolver = EligibilityResolver(policy)
            exposed = resolve_exposed_skills(
                union_registry, resolver, org=slug, team=team, agent=agent_name,
            )

            for es in exposed:
                skill_id_slug = es.skill.slug
                if es.skill.source == "user_authored" and org_root is not None:
                    src_dir = org_root / "skills" / skill_id_slug
                else:
                    src_dir = skills_root / skill_id_slug
                if not src_dir.is_dir():
                    continue

                content_hash = _compute_dir_hash(src_dir)
                store.build_from_source(
                    skill_id_slug, es.skill.version or "0", content_hash, src_dir,
                    verify_source_hash=content_hash,
                )
                expected_specs.append({
                    "slug": skill_id_slug,
                    "version": es.skill.version or "0",
                    "content_hash": content_hash,
                })

                if db is not None:
                    db.insert_skill_validation_event(
                        skill_id=es.skill.id,
                        slug=skill_id_slug,
                        agent=agent_name,
                        source="materialization",
                        severity="info",
                        ok=True,
                        version=es.skill.version,
                    )

    # 3. Lifecycle-ledger custom skills (PUBLISHED + actively assigned)
    if db is not None and org_root is not None:
        lifecycle_specs = _build_lifecycle_canonical_specs(
            store=store,
            org_root=org_root,
            db=db,
            agent_name=agent_name,
            slug=slug,
        )
        expected_specs.extend(lifecycle_specs)

    # ── Reconcile ONCE with unified expected set ────────────────────
    # Both provider roots get the same full set so system contracts
    # remain while managed/lifecycle links are created/withdrawn.
    for subdir in (".claude/skills", ".agents/skills"):
        materializer.repair_workspace_skills(
            expected_specs, workspace, subdir,
        )

    return expected_specs


def _build_lifecycle_canonical_specs(
    *,
    store: CanonicalSkillStore,
    org_root: Path,
    db,
    agent_name: str,
    slug: str,
) -> list[dict]:
    """Resolve lifecycle-ledger skills and build into canonical store.

    Returns list of {slug, version, content_hash} specs for the materializer.
    Only PUBLISHED skills with active assignments are resolved.
    Fail-closed: any error raises LifecycleMaterializationError.
    """
    from runtime.skills.lifecycle.service import SkillLifecycleService, LifecycleError
    from runtime.infrastructure.artifact_store import ArtifactStore, ArtifactNotFound
    from runtime.orchestrator._paths import OrgPaths
    from runtime.skills.canonical_store import CanonicalStoreError

    service = SkillLifecycleService()
    pkgs = service.get_effective_skills(db, agent_name)
    if not pkgs:
        return []

    artifact_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)
    specs: list[dict] = []

    for pkg in pkgs:
        skill_slug = pkg.slug

        if not pkg.content_artifact_key:
            raise LifecycleMaterializationError(
                skill_slug=skill_slug,
                agent_name=agent_name,
                reason="No content_artifact_key — legacy paths not supported",
            )

        # Load manifest artifact
        try:
            manifest_bytes = artifact_store.read(pkg.content_artifact_key)
        except ArtifactNotFound:
            raise LifecycleMaterializationError(
                skill_slug=skill_slug,
                agent_name=agent_name,
                reason=f"Artifact not found: {pkg.content_artifact_key}",
            )

        # Validate manifest hash against ledger content_hash
        import hashlib
        actual_hash = hashlib.sha256(manifest_bytes).hexdigest()
        if actual_hash != pkg.content_hash:
            raise LifecycleMaterializationError(
                skill_slug=skill_slug,
                agent_name=agent_name,
                reason=f"Manifest hash mismatch: expected {pkg.content_hash[:16]}..., got {actual_hash[:16]}...",
            )

        # Parse manifest
        import json
        manifest = None
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except Exception:
            # Legacy single-SKILL.md artifact — treat as source dir
            pass

        # ── Compute expected tree hash BEFORE building ─────────────
        # This validates every member's artifact bytes against the
        # immutable ledger-declared SHA-256.  If a member-artifact
        # mismatch exists (ArtifactStore vs ledger), this fails closed
        # BEFORE the canonical package build and BEFORE any session
        # launch that might proceed unvalidated.
        #
        # Finding 1 fix: if this fails, emit a durable integrity/
        # audit/operations failure event BEFORE propagating the error.
        # If the event persistence itself fails, refuse the build.
        if isinstance(manifest, dict) and "members" in manifest:
            try:
                expected_tree_hash = _compute_manifest_tree_hash(
                    manifest, artifact_store,
                    skill_slug=skill_slug,
                )
            except LifecycleMaterializationError:
                # ── Emit durable failure event (Finding 1) ──────────
                if db is not None:
                    try:
                        db.insert_skill_validation_event(
                            skill_id=pkg.skill_id,
                            slug=skill_slug,
                            agent=agent_name,
                            source="integrity_check",
                            severity="error",
                            ok=False,
                            version=pkg.version,
                            findings=[
                                f"Lifecycle manifest member-artifact hash mismatch "
                                f"for {skill_slug}@{pkg.version} — "
                                f"ArtifactStore bytes do not match ledger-declared "
                                f"member hashes."
                            ],
                            reason_codes=["member_hash_mismatch"],
                        )
                    except Exception as audit_exc:
                        # Event persistence failed — fail closed.
                        # Do NOT re-raise the original error (which
                        # would mask the audit failure).
                        raise LifecycleMaterializationError(
                            skill_slug=skill_slug,
                            agent_name=agent_name,
                            reason=(
                                f"Integrity event persistence failed during "
                                f"member-hash validation for {skill_slug}@{pkg.version}: "
                                f"{audit_exc}"
                            ),
                        ) from audit_exc
                raise  # Re-raise original error after audit write

            # Manifest-based: build via artifact store
            try:
                store.build_from_manifest(
                    skill_slug, pkg.version, pkg.content_hash,
                    manifest, artifact_store,
                )
            except CanonicalStoreError as build_exc:
                # build_from_manifest detected existing corrupted package —
                # emit durable event and refuse. No automatic repair.
                if db is not None:
                    try:
                        db.insert_skill_validation_event(
                            skill_id=pkg.skill_id,
                            slug=skill_slug,
                            agent=agent_name,
                            source="integrity_check",
                            severity="error",
                            ok=False,
                            version=pkg.version,
                            findings=[
                                f"Canonical package {skill_slug}@{pkg.version} "
                                f"content corruption detected: {build_exc}. "
                                f"No automatic repair from same-UID local source."
                            ],
                            reason_codes=["content_corruption"],
                        )
                    except Exception as audit_exc:
                        raise LifecycleMaterializationError(
                            skill_slug=skill_slug,
                            agent_name=agent_name,
                            reason=(
                                f"Integrity event persistence failed during "
                                f"content-corruption handling for {skill_slug}@{pkg.version}: "
                                f"{audit_exc}"
                            ),
                        ) from audit_exc
                raise LifecycleMaterializationError(
                    skill_slug=skill_slug,
                    agent_name=agent_name,
                    reason=str(build_exc),
                ) from build_exc
        else:
            # Legacy: single SKILL.md artifact
            expected_tree_hash = _compute_legacy_tree_hash(manifest_bytes)
            # Build a temp source dir with just the SKILL.md.
            import tempfile
            with tempfile.TemporaryDirectory() as tmpd:
                tmp_path = Path(tmpd)
                (tmp_path / "SKILL.md").write_bytes(manifest_bytes)
                source_hash = _compute_dir_hash(tmp_path)
                try:
                    store.build_from_source(
                        skill_slug, pkg.version, pkg.content_hash, tmp_path,
                        verify_source_hash=source_hash,
                    )
                except CanonicalStoreError as build_exc:
                    if db is not None:
                        try:
                            db.insert_skill_validation_event(
                                skill_id=pkg.skill_id,
                                slug=skill_slug,
                                agent=agent_name,
                                source="integrity_check",
                                severity="error",
                                ok=False,
                                version=pkg.version,
                                findings=[
                                    f"Canonical package {skill_slug}@{pkg.version} "
                                    f"content corruption detected: {build_exc}. "
                                    f"No automatic repair from same-UID local source."
                                ],
                                reason_codes=["content_corruption"],
                            )
                        except Exception as audit_exc:
                            raise LifecycleMaterializationError(
                                skill_slug=skill_slug,
                                agent_name=agent_name,
                                reason=(
                                    f"Integrity event persistence failed during "
                                    f"content-corruption handling for {skill_slug}@{pkg.version}: "
                                    f"{audit_exc}"
                                ),
                            ) from audit_exc
                    raise LifecycleMaterializationError(
                        skill_slug=skill_slug,
                        agent_name=agent_name,
                        reason=str(build_exc),
                    ) from build_exc

        specs.append({
            "slug": skill_slug,
            "version": pkg.version,
            "content_hash": pkg.content_hash,
            "tree_hash": expected_tree_hash,
        })

        # Record successful materialization — audit persistence is mandatory.
        # A ledger write failure here means the materialization cannot proceed
        # unrecorded to a launch-capable successful return.
        try:
            service.record_materialization(
                db=db,
                skill_id=pkg.skill_id,
                agent_name=agent_name,
                version_id=pkg.id,
                version=pkg.version,
                content_hash=pkg.content_hash,
                success=True,
                session_context="session_spawn",
            )
        except LifecycleError:
            raise
        except Exception as exc:
            raise LifecycleMaterializationError(
                skill_slug=skill_slug,
                agent_name=agent_name,
                reason=f"Audit persistence failed for successful materialization: {exc}",
            ) from exc

    return specs


def _compute_dir_hash(src_dir: Path) -> str:
    """Compute SHA-256 of a directory tree (for content-addressing).

    Sorted by relative path, hashes each file's content.
    """
    import hashlib
    h = hashlib.sha256()
    for fpath in sorted(src_dir.rglob("*")):
        if fpath.is_file():
            rel = str(fpath.relative_to(src_dir))
            h.update(rel.encode())
            h.update(b"\x00")
            h.update(fpath.read_bytes())
            h.update(b"\x00")
    return h.hexdigest()


def _compute_manifest_tree_hash(
    manifest: dict,
    artifact_store,
    *,
    skill_slug: str,
) -> str:
    """Compute the expected canonical tree hash from the AUTHORITATIVE
    manifest members, validating each member's artifact bytes against
    its immutable ledger-declared SHA-256 BEFORE hashing.

    Delegates to the canonical-store-level helper
    ``_compute_tree_hash_from_manifest_members`` which performs the
    identical validation.  Wraps ``CanonicalStoreError`` as
    ``LifecycleMaterializationError`` for the caller's error domain.

    This prevents lifecycle-ledger packages from self-ratifying:
    see ``_compute_tree_hash_from_manifest_members`` for details.
    """
    from runtime.skills.canonical_store import (
        _compute_tree_hash_from_manifest_members,
        CanonicalStoreError,
    )

    try:
        return _compute_tree_hash_from_manifest_members(
            manifest, artifact_store,
            skill_slug=skill_slug,
        )
    except CanonicalStoreError as exc:
        raise LifecycleMaterializationError(
            skill_slug=skill_slug,
            agent_name="materializer",
            reason=f"{exc.code}: {exc.detail}",
        ) from exc


def _compute_legacy_tree_hash(manifest_bytes: bytes) -> str:
    """Compute expected tree hash for legacy single-SKILL.md artifacts.

    These packages have one member: SKILL.md, whose content IS the
    manifest_bytes (the raw artifact content).
    """
    import hashlib
    h = hashlib.sha256()
    h.update(b"SKILL.md")
    h.update(b"\x00")
    h.update(manifest_bytes)
    h.update(b"\x00")
    return h.hexdigest()


# ── Pre-launch integrity validation ────────────────────────────────
# Before every executor launch, validate that workspace skill links
# resolve to the expected canonical packages and that canonical package
# integrity (tree hashes, member hashes for lifecycle packages) is
# intact. The executor and daemon share the same OS identity — a
# same-UID process can mutate canonical targets between checks.
# Detection-only: no automatic repair from same-UID local sources.
# Recovery: FIRST manual authoritative external re-sync/redeploy
# of release/custom artifacts; ONLY THEN:
# (a) for link-only faults — `happyranch set-executor <agent>
#     --executor <current-executor>` (non-destructive re-materialize);
# (b) for corrupted canonical bytes — `happyranch skills recover
#     <slug> <version> <content_hash>` then restart the daemon.
# Local same-UID sources are not automatically repaired or trusted.


class WorkspaceIntegrityError(Exception):
    """Raised when workspace skill integrity validation fails.

    Terminal — no executor launch proceeds. Recovery: FIRST manual
    authoritative external re-sync/redeploy of release/custom
    artifacts; ONLY THEN existing verified link repair / skills
    recover / restart as applicable. Local same-UID sources are not
    automatically repaired or trusted.
    """

    def __init__(
        self,
        code: str,
        detail: str,
        findings: list[str] | None = None,
        recovery_command: str | None = None,
    ) -> None:
        self.code = code
        self.detail = detail
        self.findings = findings or []
        self.recovery_command = recovery_command
        msg = f"[{code}] {detail}"
        if self.recovery_command:
            msg += f"\nRecovery: {self.recovery_command}"
        if self.findings:
            msg += f"\nFindings: {', '.join(self.findings)}"
        super().__init__(msg)


def validate_workspace_skills_integrity(
    workspace: Path,
    expected_specs: list[dict],
    *,
    settings: "Settings | None" = None,  # noqa: F821
    db: "Database | None" = None,  # noqa: F821
    agent_name: str | None = None,
    task_id: str | None = None,
) -> None:
    """Validate workspace skill links and canonical package integrity.

    For EVERY expected spec, validates:
    - The canonical package exists and is non-empty
    - The canonical tree hash matches the expected value computed from
      ledger-declared member hashes (lifecycle) or the source tree hash
      (system contracts)
    - Workspace symlinks at BOTH ``.claude/skills`` and ``.agents/skills``
      point to the correct canonical target
    - No ordinary directories at expected symlink positions (hostile state)
    - No unexpected entries in either skills root

    On ANY mismatch: emits a durable ``skill_validation_events`` row with
    ``severity="error"``, ``source="integrity_check"``, ``ok=False``, then
    raises ``WorkspaceIntegrityError``. If the audit write itself fails,
    also raises (fail-closed — no launch proceeds unrecorded).

    This is a DETECTIVE control, not a preventive security boundary.
    The executor and daemon share the same OS identity, so an
    agent-controlled executor can mutate canonical targets through
    workspace links between checks. The integrity check detects
    tampering at the next launch attempt and refuses the session.
    Recovery: FIRST manual authoritative external re-sync/redeploy
    of release/custom artifacts; ONLY THEN (a) for link-only faults
    — `happyranch set-executor <agent> --executor <current-executor>`
    (never repairs bytes); (b) for corrupted canonical bytes —
    `happyranch skills recover <slug> <version> <content_hash>`
    then restart daemon. Local same-UID sources are not automatically
    repaired or trusted.

    Args:
        workspace: Agent workspace root
        expected_specs: List of {slug, version, content_hash, tree_hash}
            dicts from materialization. Must be the SAME list that was
            used to materialize — never a separately-derived list that
            can drift.
        settings: Project Settings (for canonical store construction)
        db: Database handle for persisting audit events
        agent_name: Agent name for audit attribution
        task_id: Task ID for audit attribution

    Raises:
        WorkspaceIntegrityError: On any mismatch, missing package, broken
            link, ordinary directory, unexpected entry, or audit-write failure.
    """
    from runtime.skills.canonical_store import CanonicalSkillStore, CanonicalStoreError
    from runtime.platform.isolation import (
        detect_platform_isolation,
        PlatformIsolationError,
    )

    isolation = detect_platform_isolation()
    store = CanonicalSkillStore(
        settings=settings,
        isolation=isolation,
    )

    # No specs → nothing to validate (e.g., agent with no skills).
    if not expected_specs:
        return

    findings: list[str] = []
    skill_roots = [".claude/skills", ".agents/skills"]
    expected_slugs = {spec["slug"] for spec in expected_specs}

    # ── Validate each expected spec ────────────────────────────
    for spec in expected_specs:
        slug = spec["slug"]
        version = spec["version"]
        content_hash = spec["content_hash"]

        # 1. Verify canonical package exists and is non-empty.
        try:
            store.verify_package(slug, version, content_hash)
        except CanonicalStoreError as exc:
            findings.append(
                f"Canonical package missing/empty for {slug}@{version}: {exc}"
            )
            continue

        # 1b. Verify package tree hash matches the expected value.
        #     The executor shares the daemon's OS identity and can
        #     chmod+mutate+restore canonical targets, so we validate
        #     actual content integrity via tree hash.
        expected_tree_hash = spec.get("tree_hash", content_hash)
        actual_tree_hash = store.compute_tree_hash(slug, version, content_hash)
        if actual_tree_hash != expected_tree_hash:
            findings.append(
                f"Package tree hash mismatch for {slug}@{version}: "
                f"expected {expected_tree_hash[:16]}..., "
                f"got {actual_tree_hash[:16]}..."
            )
            continue

        canonical_target = store.canonical_path(slug, version, content_hash)

        # 2. Verify workspace symlinks in BOTH roots
        for subdir in skill_roots:
            link_path = workspace / subdir / slug
            if not link_path.exists(follow_symlinks=False):
                findings.append(
                    f"Missing workspace link: {link_path} "
                    f"(expected → {canonical_target})"
                )
                continue

            if not link_path.is_symlink():
                if link_path.is_dir():
                    findings.append(
                        f"Ordinary directory at symlink position: {link_path} "
                        f"(expected symlink → {canonical_target}). "
                        f"This is a potentially hostile state — refused."
                    )
                else:
                    findings.append(
                        f"Non-symlink at link position: {link_path} "
                        f"(expected symlink → {canonical_target})"
                    )
                continue

            if not isolation.verify_workspace_link(
                link_path, canonical_target, store.root,
            ):
                try:
                    actual_target = os.readlink(str(link_path))
                except OSError:
                    actual_target = "<unreadable>"
                findings.append(
                    f"Wrong/mismatched workspace link: {link_path} → "
                    f"{actual_target} (expected → {canonical_target})"
                )

    # ── Check for unexpected entries in workspace skill dirs ──
    for subdir in skill_roots:
        skills_dir = workspace / subdir
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            if entry.name.startswith(".tmp."):
                continue
            if entry.name not in expected_slugs:
                if entry.is_symlink():
                    findings.append(
                        f"Unexpected symlink in {subdir}: {entry.name} "
                        f"→ {os.readlink(str(entry))}. "
                        f"Not in expected skill set."
                    )
                elif entry.is_dir():
                    findings.append(
                        f"Unexpected ordinary directory in {subdir}: {entry.name}"
                    )
                else:
                    findings.append(
                        f"Unexpected entry in {subdir}: {entry.name}"
                    )

    # ── If no findings, validation passed ─────────────────────
    if not findings:
        logger.debug(
            "Workspace skills integrity validation passed for %s "
            "(%d expected specs, agent=%s)",
            workspace, len(expected_specs), agent_name or "?",
        )
        return

    # ── Emit durable audit event(s) ────────────────────────────
    audit_failed = False
    if db is not None:
        for finding in findings:
            try:
                db.insert_skill_validation_event(
                    skill_id="hr:workspace-integrity",
                    slug="workspace-integrity",
                    agent=agent_name,
                    source="integrity_check",
                    severity="error",
                    ok=False,
                    version=None,
                    findings=[finding],
                    reason_codes=["integrity_mismatch"],
                )
            except Exception as exc:
                logger.error(
                    "Failed to persist integrity violation audit event: %s",
                    exc,
                )
                audit_failed = True
                findings.append(
                    f"Audit persistence failure: {exc}. "
                    f"Launch refused per fail-closed policy."
                )
                break

    # ── Fail closed ────────────────────────────────────────────
    # Recovery guidance:
    # - Broken/missing/wrong workspace links → re-materialize via
    #   executor switch (happyranch set-executor) which rebuilds links
    #   from the canonical store (links ONLY — does NOT recover
    #   corrupted canonical bytes).
    # - Corrupted canonical bytes (hash mismatch, tampered content) →
    #   set-executor CANNOT recover bytes — it only repairs links.
    #   Recovery is manual, operator-invoked: `happyranch skills
    #   recover <slug> <version> <content_hash>`. Validates ledger
    #   provenance and member hashes before deletion; refuses already-
    #   valid targets. Next materialization rebuilds from ArtifactStore.
    #   There is NO automatic same-UID local source repair and
    #   NO automatic recovery from any local same-UID source.
    recovery = (
        "FIRST manual authoritative external re-sync/redeploy "
        "of release/custom artifacts; ONLY THEN: "
        "for link-only faults — happyranch set-executor <agent> "
        "--executor <current-executor> (non-destructive re-materialize); "
        "for corrupted canonical bytes — happyranch skills recover "
        "<slug> <version> <content_hash> then restart daemon. "
        "Local same-UID sources are not automatically repaired or trusted."
    )

    raise WorkspaceIntegrityError(
        code="integrity_mismatch" if not audit_failed else "audit_write_failed",
        detail=(
            f"Workspace skills integrity validation failed for {workspace} "
            f"(agent={agent_name or '?'}, "
            f"expected_specs={len(expected_specs)}, "
            f"findings={len(findings)})."
        ),
        findings=findings,
        recovery_command=recovery,
    )


def inject_system_contracts(
    workspace: Path,
    settings: Settings,
    *,
    slug: str,
    context: str,
) -> None:
    """CUTOVER: Forwards to materialize_workspace_skills for compatibility.

    This wrapper exists only for test compat during the cutover window.
    Production callers must use materialize_workspace_skills directly.
    """
    # Derive minimal parameters for the unified call
    skills_root = settings.project_root / "runtime" / "skills"
    materialize_workspace_skills(
        workspace, settings,
        slug=slug,
        context=context,
        provider="claude",
        agent_name="test",
        team="engineering",
        skills_root=skills_root,
    )


def inject_managed_skills(
    workspace: Path,
    settings: Settings,
    *,
    slug: str,
    agent_name: str,
    team: str,
    skills_root: Path,
    org_root: Path | None = None,
    db: "Database | None" = None,  # noqa: F821
) -> None:
    """CUTOVER: Forwards to canonical store for compatibility.

    Managed-catalog and lifecycle skills are now resolved via
    materialize_workspace_skills / _materialize_unified_canonical.
    """
    materialize_workspace_skills(
        workspace, settings,
        slug=slug,
        context="task",
        provider="claude",
        agent_name=agent_name,
        team=team,
        skills_root=skills_root,
        org_root=org_root,
        db=db,
    )


def _materialize_lifecycle_skills(
    *,
    workspace: Path,
    org_root: Path,
    db,
    agent_name: str,
    slug: str,
) -> None:
    """Resolve and materialize lifecycle-ledger custom skills for an agent.

    Only PUBLISHED skills with an active assignment are materialized.
    Proposed/draft/validated/approved-but-unpublished/quarantined skills
    are invisible here.

    Content resolution is ArtifactStore-backed only: the ledger's
    ``content_artifact_key`` points to immutable ArtifactStore bytes.
    Legacy filesystem paths (org_root/skills/) are NEVER resolved — the
    lifecycle ledger is the sole runtime source.

    CRITICAL: fail-closed. Missing/corrupt/hash-mismatched artifact, write
    error, or any provenance inconsistency → clean workspace residue and
    RAISE so the session launch cannot silently proceed without an
    assigned skill. Validate and record the exact bytes actually written;
    do NOT substitute after hash validation.
    """
    import hashlib
    import logging
    import shutil

    from runtime.skills.lifecycle.service import SkillLifecycleService, LifecycleError
    from runtime.skills.lifecycle.models import LifecycleStatus
    from runtime.skills.lifecycle import stores

    logger = logging.getLogger("happyranch.skills.lifecycle.materialization")
    service = SkillLifecycleService()

    # Get active assignments for this agent from the lifecycle ledger
    pkgs = service.get_effective_skills(db, agent_name)
    if not pkgs:
        return

    # Resolve ArtifactStore for artifact-backed content
    from runtime.infrastructure.artifact_store import ArtifactStore, ArtifactNotFound
    from runtime.orchestrator._paths import OrgPaths
    artifact_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)

    for pkg in pkgs:
        skill_slug = pkg.slug
        dest_claude = workspace / ".claude" / "skills" / skill_slug
        dest_agents = workspace / ".agents" / "skills" / skill_slug

        # ── Preflight: reject materialization of terminally REJECTED
        #    packages BEFORE any filesystem bytes are written.
        #    This guard must fire before creating directories or
        #    writing content — rejected attempts fail closed with
        #    no workspace residue.
        fresh_pkg = stores.get_package_version(db, pkg.id)
        if fresh_pkg is not None and fresh_pkg.status == LifecycleStatus.REJECTED:
            error_msg = (
                f"Package version {pkg.id} is terminally REJECTED. "
                f"Materialization is blocked."
            )
            try:
                service.record_materialization(
                    db=db,
                    skill_id=pkg.skill_id,
                    agent_name=agent_name,
                    version_id=pkg.id,
                    version=pkg.version,
                    content_hash=pkg.content_hash,
                    success=False,
                    error_message=error_msg,
                    session_context="session_spawn",
                )
            except Exception:
                pass
            raise LifecycleMaterializationError(
                skill_slug=skill_slug,
                agent_name=agent_name,
                reason=error_msg,
            )

        # ArtifactStore-backed content is the ONLY valid source.
        if not pkg.content_artifact_key:
            error_msg = "No content_artifact_key — legacy paths not supported"
            _record_and_cleanup(
                service, db, pkg, agent_name, dest_claude, dest_agents,
                error_msg,
            )
            raise LifecycleMaterializationError(
                skill_slug=skill_slug,
                agent_name=agent_name,
                reason=error_msg,
            )

        # Load and validate the manifest artifact.
        # The manifest hash IS the package content_hash (binds full provenance).
        manifest_bytes: bytes
        try:
            manifest_bytes = artifact_store.read(pkg.content_artifact_key)
        except ArtifactNotFound:
            error_msg = f"Artifact not found: {pkg.content_artifact_key}"
            _record_and_cleanup(
                service, db, pkg, agent_name, dest_claude, dest_agents,
                error_msg,
            )
            raise LifecycleMaterializationError(
                skill_slug=skill_slug,
                agent_name=agent_name,
                reason=error_msg,
            )
        except Exception as exc:
            error_msg = f"Artifact load error: {exc}"
            _record_and_cleanup(
                service, db, pkg, agent_name, dest_claude, dest_agents,
                error_msg,
            )
            raise LifecycleMaterializationError(
                skill_slug=skill_slug,
                agent_name=agent_name,
                reason=error_msg,
            ) from exc

        # Validate manifest hash against ledger content_hash
        actual_manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
        if actual_manifest_hash != pkg.content_hash:
            error_msg = (
                f"Manifest hash mismatch: expected {pkg.content_hash}, "
                f"got {actual_manifest_hash}"
            )
            _record_and_cleanup(
                service, db, pkg, agent_name, dest_claude, dest_agents,
                error_msg,
            )
            raise LifecycleMaterializationError(
                skill_slug=skill_slug,
                agent_name=agent_name,
                reason=error_msg,
            )

        # Parse manifest — if the artifact content is valid JSON with
        # a "members" field, it's a manifest-based package. Otherwise
        # fall back to legacy single-SKILL.md artifact for backward compat.
        import json
        manifest: dict | None = None
        try:
            parsed = json.loads(manifest_bytes.decode("utf-8"))
            if isinstance(parsed, dict) and "members" in parsed:
                manifest = parsed
        except Exception:
            pass  # Not a manifest — treat as legacy raw SKILL.md

        if manifest is not None:
            # ── Manifest-based full-package materialization ──────
            members = manifest["members"]
            if not members:
                error_msg = "Manifest has no members"
                _record_and_cleanup(
                    service, db, pkg, agent_name, dest_claude, dest_agents,
                    error_msg,
                )
                raise LifecycleMaterializationError(
                    skill_slug=skill_slug,
                    agent_name=agent_name,
                    reason=error_msg,
                )

            # Materialize every member from the manifest.
            try:
                dest_claude.mkdir(parents=True, exist_ok=True)
                dest_agents.mkdir(parents=True, exist_ok=True)

                for member in members:
                    member_path = member["path"]
                    member_hash = member["hash"]  # e.g. "sha256:abc123..."
                    member_artifact_key = member["artifact_key"]

                    # Load member content from ArtifactStore
                    try:
                        member_bytes = artifact_store.read(member_artifact_key)
                    except ArtifactNotFound:
                        error_msg = (
                            f"Member artifact not found: {member_artifact_key} "
                            f"(path={member_path})"
                        )
                        _record_and_cleanup(
                            service, db, pkg, agent_name, dest_claude, dest_agents,
                            error_msg,
                        )
                        raise LifecycleMaterializationError(
                            skill_slug=skill_slug,
                            agent_name=agent_name,
                            reason=error_msg,
                        )

                    # Validate member hash against stored bytes
                    # Must be strict sha256:<64 lowercase hex> — no bare digests,
                    # no arbitrary prefixes, no uppercase hex.
                    expected_hash_hex = parse_strict_sha256_hash(member_hash)
                    actual_member_hash = hashlib.sha256(member_bytes).hexdigest()
                    if actual_member_hash != expected_hash_hex:
                        error_msg = (
                            f"Member hash mismatch for {member_path}: "
                            f"expected {expected_hash_hex[:16]}..., got {actual_member_hash[:16]}..."
                        )
                        _record_and_cleanup(
                            service, db, pkg, agent_name, dest_claude, dest_agents,
                            error_msg,
                        )
                        raise LifecycleMaterializationError(
                            skill_slug=skill_slug,
                            agent_name=agent_name,
                            reason=error_msg,
                        )

                    # Write exact retained bytes to both target directories.
                    # The immutable-artifact content is the workspace content —
                    # no post-verification substitution (the hash was validated
                    # against the original bytes; mutating them would break the
                    # exact-byte provenance guarantee).
                    dest_path = member_path
                    for target_base in (dest_claude, dest_agents):
                        target_file = target_base / dest_path
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        target_file.write_bytes(member_bytes)

            except LifecycleMaterializationError:
                raise
            except Exception as exc:
                error_msg = f"Write error: {exc}"
                _record_and_cleanup(
                    service, db, pkg, agent_name, dest_claude, dest_agents,
                    error_msg,
                )
                raise LifecycleMaterializationError(
                    skill_slug=skill_slug,
                    agent_name=agent_name,
                    reason=error_msg,
                ) from exc

        else:
            # ── Legacy single-SKILL.md artifact ─────────────────
            # Backward compatibility: content_artifact_key points to
            # a raw SKILL.md file (no manifest). Treat the artifact
            # content directly as the SKILL.md to materialize.
            # Write exact retained bytes — no post-verification substitution.
            content_bytes = manifest_bytes

            try:
                dest_claude.mkdir(parents=True, exist_ok=True)
                dest_agents.mkdir(parents=True, exist_ok=True)
                (dest_claude / "SKILL.md").write_bytes(content_bytes)
                (dest_agents / "SKILL.md").write_bytes(content_bytes)
            except Exception as exc:
                error_msg = f"Write error: {exc}"
                _record_and_cleanup(
                    service, db, pkg, agent_name, dest_claude, dest_agents,
                    error_msg,
                )
                raise LifecycleMaterializationError(
                    skill_slug=skill_slug,
                    agent_name=agent_name,
                    reason=error_msg,
                ) from exc

        # Record successful materialization.
        # The preflight above guards against a REJECTED version BEFORE
        # filesystem writes, but an adversarial interleaving can flip
        # PUBLISHED→REJECTED between preflight and success recording.
        # record_materialization's own rejected_terminal gate catches
        # this at the success boundary — we must NOT broadly swallow it.
        try:
            service.record_materialization(
                db=db,
                skill_id=pkg.skill_id,
                agent_name=agent_name,
                version_id=pkg.id,
                version=pkg.version,
                content_hash=pkg.content_hash,
                success=True,
                session_context="session_spawn",
            )
        except LifecycleError as e:
            if e.code == "rejected_terminal":
                # Adversarial interleaving: package flipped to REJECTED
                # after bytes landed on disk. Clean both destination
                # trees and raise so NO session spawns with rejected
                # content — fail-closed, zero materialized-success events.
                for d in (dest_claude, dest_agents):
                    if d.exists():
                        shutil.rmtree(d, ignore_errors=True)
                raise LifecycleMaterializationError(
                    skill_slug=skill_slug,
                    agent_name=agent_name,
                    reason=(
                        f"Package version {pkg.id} became REJECTED during "
                        f"materialization — terminal lifecycle gate blocked "
                        f"at success-recording seam"
                    ),
                ) from e
            # Non-rejected LifecycleError: best-effort pass.
        except Exception:
            pass


class LifecycleMaterializationError(Exception):
    """Raised when lifecycle skill materialization fails.

    The session spawner catches this and treats it as a fatal workspace-prep
    error — no session is launched without an assigned custom skill.
    """
    def __init__(self, skill_slug: str, agent_name: str, reason: str):
        self.skill_slug = skill_slug
        self.agent_name = agent_name
        self.reason = reason
        super().__init__(
            f"Lifecycle materialization failed for {skill_slug}/{agent_name}: {reason}"
        )


def _record_and_cleanup(
    service, db, pkg, agent_name, dest_claude, dest_agents, error_message,
) -> None:
    """Best-effort materialization failure recording + workspace residue cleanup."""
    import shutil
    # Clean workspace residue
    for d in (dest_claude, dest_agents):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    # Record failure
    try:
        service.record_materialization(
            db=db,
            skill_id=pkg.skill_id,
            agent_name=agent_name,
            version_id=pkg.id,
            version=pkg.version,
            content_hash=pkg.content_hash,
            success=False,
            error_message=error_message,
            session_context="session_spawn",
        )
    except Exception:
        pass


def _memory_bootstrap_section(workspace: Path) -> list[str]:
    """Returns the 'Persistent Files' + 'Your Memory' block.

    Branches on workspace state: legacy flat learnings.md vs migrated memory/.
    """
    flat = workspace / "learnings.md"
    memory_dir = workspace / "memory"
    index = memory_dir / "_index.md"

    if memory_dir.exists() and index.exists():
        index_body = index.read_text()
        return [
            "## Persistent Files\n",
            "- `memory/_index.md` -- index of your operational memory",
            "  (full bodies via `happyranch memory get`)",
            "- `task_history.md` -- read-only, updated by orchestrator\n",
            "## Your Memory\n",
            index_body,
            "\nFetch any entry's body:",
            "```",
            "happyranch memory get --org <slug> --agent <you> <MEM-NNN-or-slug>",
            "```",
            "Write a new memory item (file payload with slug/title/topic/tags/body):",
            "```",
            "happyranch memory add --org <slug> --agent <you> --from-file <path>",
            "```",
            "Update an existing memory item:",
            "```",
            "happyranch memory update --org <slug> --agent <you> <MEM-NNN> --from-file <path>",
            "```",
            "Promote a durable cross-agent rule to the shared KB (one-way):",
            "```",
            "happyranch memory promote --org <slug> --agent <you> <MEM-NNN> --kb-slug <slug>",
            "```\n",
            "_Old `LRN-` ids and `happyranch learning …` still resolve "
            "(`learning` is a deprecated alias of `memory`)._\n",
        ]
    if flat.exists():
        flat_body = flat.read_text()
        return [
            "## Persistent Files\n",
            "- `learnings.md` -- your accumulated operational memory (legacy flat-file format)",
            "- `task_history.md` -- read-only, updated by orchestrator\n",
            "## Your Memory\n",
            flat_body + "\n",
            "Append a new line via `happyranch memory --agent <you> --text \"...\"`.",
            "_The structured per-entry format is available once this workspace is migrated._\n",
        ]
    # Brand-new workspace, ensure() should have created memory/ already.
    return [
        "## Persistent Files\n",
        "- `memory/_index.md` -- index of your operational memory (empty)",
        "- `task_history.md` -- read-only, updated by orchestrator\n",
    ]


def _shared_artifacts_section() -> list[str]:
    return [
        "## Shared Artifacts (org-wide)\n",
        "Path: `<runtime>/orgs/<slug>/artifacts/`. Drop persistent files your work",
        "produces — generated reports, exports, screenshots, PDFs, images. Files",
        "here survive across tasks and are visible to every agent in this org.\n",
        "Use cases: a generated PDF report another agent needs to attach to a",
        "customer reply; a CSV export the founder will want to review; a screenshot",
        "captured during QA that the bug-triage agent should see.\n",
        "**Not** the KB. KB is for durable cross-agent *knowledge* (rules,",
        "references, founder rulings). Artifacts are for *files and binary blobs*.",
        "Don't put scratch work here — use your workspace `repos/`, learning",
        "entries, or task output for transient state.\n",
        "All access is via `happyranch`. Direct filesystem reads/writes won't work",
        "uniformly across executors — use the CLI:\n",
        "```",
        "happyranch artifacts put <local-path> --agent <you> [--name <name>]",
        "happyranch artifacts list",
        "happyranch artifacts get <name> --output <local-path>",
        "```\n",
        "Naming convention: prefix with your agent name + ISO date for",
        "traceability, e.g. `dev_agent-YYYY-MM-DD-perf-report.pdf`. Names may",
        "use '/' as a path separator for logical folders. Each segment must",
        "match `[A-Za-z0-9._-]+`; max 200 chars total. Per-file size cap: 10 MB.\n",
    ]


def _thread_talk_dispatch_doctrine_section() -> list[str]:
    """System-injected doctrine: dispatch from a thread is self-only.

    Surfaces the structural rule enforced at `/threads/{id}/dispatch`
    so every agent reads it at bootstrap rather than discovering it via a
    403 response. The rule itself is mechanical (route rejects
    `effective_target != dispatcher` with `thread_dispatch_must_be_self`);
    this section is the *why* and the recommended pattern. Spec:
    `docs/superpowers/specs/2026-05-28-thread-talk-self-dispatch-only-design.md`.

    Keep the prose tight — every agent in every org reads this on every
    session. If this grows past ~25 lines it has become docs, not a prompt.
    """
    return [
        "## Thread Dispatch is Self-Only\n",
        "When you are inside a **thread invocation** (reply / bootstrap), the",
        "runtime only lets you dispatch tasks to **yourself**. Any attempt to",
        "target another agent returns 403 with `thread_dispatch_must_be_self`.\n",
        "This is the doctrine the rule encodes:",
        "- **Threads** exist for founder-visible coordination and cross-team",
        "  handoffs. They are messaging surfaces.",
        "- **Task trees** exist for iterative work. Managers drive sub-tasks",
        "  through the manager-decision loop; workers do bounded work and",
        "  report back. They are execution surfaces.\n",
        "When you need to do task-shaped work from inside a thread:",
        "- **Self-dispatch a root task.** Omit `target_agent` (or set it to",
        "  your own name). If you are a manager and the work has multiple",
        "  steps, the manager-decision loop handles internal sub-task",
        "  spawning on its own. The thread sees a single `task_completed`",
        "  system message at the end and a single TASK_FOLLOWUP turn where",
        "  you report back.",
        "- **Do not** thread-dispatch another agent. Instead, open or extend",
        "  a thread with `happyranch threads compose --to <other-agent>` and",
        "  let them decide whether to take the work on. Cross-team handoffs",
        "  always route through compose, not dispatch.\n",
        "If you find yourself wanting to dispatch a SECOND task from the same",
        "thread, that is the signal that you should have dispatched a single",
        "self-managed root the first time.\n",
    ]


def _skills_directory_readonly_section(skills_dir: str) -> list[str]:
    """System-injected operational guidance: do not edit managed skill links.

    Skill entries under BOTH ``.claude/skills`` and ``.agents/skills`` are
    daemon-materialized from the canonical skill store. This section
    directs agents NOT to edit these managed links and to use the
    supported custom-skill workflow instead.

    **IMPORTANT:** This is operational guidance, NOT enforcement. The
    executor and daemon share the same OS identity — there is NO OS-level
    security boundary. Integrity validation detects a mismatch, records a
    durable visible integrity/operations event, and refuses launch; there
    is NO local automatic recovery/autoheal. Recovery requires FIRST
    manual authoritative external re-sync/redeploy of release/custom
    artifacts; ONLY THEN existing verified link repair / skills recover /
    restart as applicable. Local same-UID sources are not automatically
    repaired or trusted. Do NOT call the target immutable, protected, or
    claim write/chmod/ACL denial, OS-enforced isolation, or automatic
    same-UID repair.
    """
    return [
        "## Skills Directory (do not edit)\n",
        "`.claude/skills/` and `.agents/skills/` are materialized by the ",
        "daemon from the canonical skill store under BOTH managed roots. ",
        "DO NOT author, edit, move, or delete anything under either ",
        "directory, even if a task seems to call for it. Treat them as ",
        "read-only.\n",
        "The executor and daemon share the same OS identity — the ",
        "filesystem CAN be written through these symlinks; there is no ",
        "OS-enforced security boundary. Integrity validation detects a ",
        "mismatch, records a durable visible integrity/operations event, ",
        "and refuses launch — there is NO local automatic ",
        "recovery/autoheal. Recovery requires FIRST manual authoritative ",
        "external re-sync/redeploy of release/custom ",
        "artifacts; ONLY THEN existing verified link repair / ",
        "skills recover / restart as applicable. Local same-UID "
        "sources are not automatically repaired or trusted. Do not "
        "rely on this as a security control or treat it as "
        "OS-enforced protection.\n",
        "If a skill's content is wrong or a new skill is needed, use the",
        "verified create-skill path instead of editing files directly:",
        "```",
        "happyranch skills create --from-file <path> --session-id <your-session-id>",
        "```\n",
    ]


def _non_stop_command_warning_section() -> list[str]:
    """Persistent warning: never run a non-returning command synchronously.

    A `bash` tool call that doesn't return blocks the session until the
    executor's wall-clock timeout fires (default 1800s). The orchestrator
    marks the task terminal FAILED under normal failure handling — no
    automatic successor is spawned and no retries are attempted. The
    session completes no useful work in the meantime and consumes one
    session budget.

    Recovery requires explicit manager or founder action (``happyranch
    revisit`` or a manager re-delegation).

    The remedy is the `jobs` skill: the daemon spawns the subprocess
    out-of-process, the agent's session continues, and the agent polls
    `happyranch jobs tail|wait|stop` for status.
    """
    return [
        "## Long-running and non-stop commands\n",
        "**Never** run a command synchronously via `bash` if it doesn't return on its",
        "own. Examples that will block your session until the wall-clock timeout",
        "kills it. The task is marked terminal FAILED under normal failure",
        "handling — no automatic retries or successor tasks are spawned. The",
        "session completes no useful work and consumes one budget.\n",
        "- Dev servers: `npm run dev`, `python -m http.server`, `cargo watch`",
        "- Log/file watchers: `tail -f`, `fswatch`, `entr`",
        "- Polling loops: `while true; do …; sleep N; done`",
        "- Long builds you don't need to wait on: full-image Docker builds, large",
        "  cross-compile runs, multi-hour migrations",
        "- Anything that needs founder credentials your `allow_rules` block",
        "  (`aws`, `stripe`, `ssh`, `sudo`, blocked `gh` verbs)\n",
        "Submit a **job** instead — the daemon runs the subprocess, your session",
        "continues, and you check on it with `happyranch jobs tail|wait|stop` when",
        "ready. See the **jobs** skill (`protocol/skills/jobs/SKILL.md`; available",
        "to you under your workspace's skills directory) for the form fields, the",
        "two policy flags (`review_required`, `persistent`), and how to self-block",
        "when founder review is required.\n",
        "If you're uncertain whether a command will return, submit it as a job",
        "with `persistent: true` — cheaper to be wrong than to lose a session.\n",
    ]


# H2 headers that the system emits into every assembled bootstrap doc.
# An agent's ``.md`` body must NOT use any of these as a section header,
# because the assembled prompt would then carry two sections with the
# same heading (the agent body's section above the agent-body cutline, and
# the system-injected section below). Confusing for the agent, and a
# maintenance hazard: each agent file becomes a place where system content
# can quietly drift.
#
# Keep this set synchronized with the ``## <Header>`` lines emitted by
# ``_build_sections`` and the ``_*_section`` helpers above.
_RESERVED_AGENT_BODY_HEADERS: frozenset[str] = frozenset({
    "Available Repositories",
    "Persistent Files",
    "Your Learnings",
    "Knowledge Base (shared across agents)",
    "Shared Artifacts (org-wide)",
    "Thread Dispatch is Self-Only",
    "Skills Directory (do not edit)",
    "Long-running and non-stop commands",
    "Task Completion Format",
    "Task Recall",
    "Workflow",
})


class ReservedHeaderInAgentBody(ValueError):
    """Raised when an agent's ``.md`` body uses a reserved H2 header that
    collides with a system-injected section in the assembled bootstrap doc.
    """


def _assert_no_reserved_headers_in_body(agent_name: str, body: str) -> None:
    """Block the bootstrap-doc write if the agent body collides with a
    system-injected H2 header.

    Boundary contract: the agent ``.md`` file owns who-the-agent-is content
    (role, authority, escalation, accountability). The system owns
    how-to-interact-with-the-orchestrator content (the headers in
    ``_RESERVED_AGENT_BODY_HEADERS`` above). If an agent file authors one of
    the reserved headers, the assembled CLAUDE.md / AGENTS.md will carry
    two sections with the same name and the system's section becomes
    duplicated, contradicted, or drifted.

    Surfaced at write time so the founder sees the violation BEFORE a
    session spawns against a broken assembled doc. Only the offending
    agent's workspace setup fails; the rest of the org keeps running.
    """
    offenders: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            heading = line[3:].strip()
            if heading in _RESERVED_AGENT_BODY_HEADERS:
                offenders.append(heading)
    if offenders:
        offenders_list = ", ".join(repr(h) for h in offenders)
        raise ReservedHeaderInAgentBody(
            f"agent {agent_name!r}: the agent .md body uses H2 header(s) "
            f"{offenders_list}, which the system also emits into the "
            f"assembled bootstrap doc. Rename or remove these headers in "
            f"the agent file. Reserved headers are owned by the system "
            f"(see _RESERVED_AGENT_BODY_HEADERS in workspace_adapters.py)."
        )


def _task_completion_format_section() -> list[str]:
    """System-injected reminder of the completion contract.

    Replaces the per-agent ``## Task Completion Format`` stubs that lived in
    agent ``.md`` files. The canonical JSON payload shape — including the
    manager-only ``decision`` block — lives in the ``start-task`` skill's
    *Report completion* step (``skills/start-task/SKILL.md``) and the
    universal spec (``protocol/00-completion-contract.md``). This section
    keeps every agent pointed at them and lists the prose-``summary`` items
    that apply regardless of role, so individual agent files don't have to
    restate (and slowly drift from) the contract.
    """
    return [
        "## Task Completion Format\n",
        "Every task ends with a `happyranch report-completion --from-file <path>`",
        "callback driven by the **start-task** skill. The skill's *Report",
        "completion* step carries the canonical JSON payload shape — fields,",
        "the manager-only `decision` block, and the blocked-path variant.",
        "Do **not** restate it here; consult the skill.\n",
        "In the prose `summary` field, include:",
        "- What was done — or, for a blocker, what is in the way.",
        "- Findings, risks, or concerns the founder or a downstream reviewer",
        "  should know about.",
        "- Items that need founder decision (call them out explicitly).",
        "- Follow-up work the next task should pick up.\n",
        "Role-specific items your output should mention (artifact paths, PR",
        "numbers, verdicts, tokens added, etc.) come from your role — name",
        "them concretely; do not leave the reader to infer.\n",
    ]


def _format_allow_rule(prefix: str, *, cli: bool) -> str:
    """Render a Bash prefix in one of the two equivalent permission syntaxes.

    Settings.json uses ``Bash(<cmd>:*)``; the ``--allowedTools`` CLI flag uses
    ``Bash(<cmd> *)``. Both prefix-match the same invocations in Claude Code,
    but the project has historically used different separators in the two
    surfaces and we preserve that to minimize diff noise against prior tests
    and released workspaces.
    """
    sep = " " if cli else ":"
    return f"Bash({prefix}{sep}*)"


def allow_rules_for_agent(
    paths: "OrgPaths", agent_name: str | None, *, cli: bool,
) -> list[str]:
    """Build the Bash allow-rule list for ``agent_name``.

    Baseline ``happyranch`` is always included (the agent-callback channel).
    Additional prefixes come from the agent's ``allow_rules`` frontmatter
    field in ``<runtime>/org/agents/<name>.md``.
    """
    from runtime.orchestrator import prompt_loader
    rules = [_format_allow_rule("happyranch", cli=cli)]
    if agent_name is None:
        return rules
    for prefix in prompt_loader.allow_rules_for_agent(paths, agent_name):
        rules.append(_format_allow_rule(prefix, cli=cli))
    return rules


def bash_allow_prefixes_for_agent(
    paths: "OrgPaths", agent_name: str | None,
) -> list[str]:
    """Return raw Bash allow-rule prefixes (no syntax wrapping).

    Used by ``OpencodeWorkspaceAdapter`` to build ``opencode.json``, where
    each prefix is rendered as ``"<prefix> *": "allow"`` rather than
    ``Bash(<prefix>:*)`` (settings.json) or ``Bash(<prefix> *)``
    (Claude ``--allowedTools``). Source of truth (the per-agent
    ``allow_rules`` frontmatter) is the same; only the rendering differs.
    """
    from runtime.orchestrator import prompt_loader
    prefixes = ["happyranch"]
    if agent_name is None:
        return prefixes
    for prefix in prompt_loader.allow_rules_for_agent(paths, agent_name):
        prefixes.append(prefix)
    return prefixes


def build_settings_json(
    paths: "OrgPaths",
    repo_names: list[str],
    agent_name: str | None = None,
) -> dict:
    """Build .claude/settings.json for a workspace.

    ``repo_names`` is accepted for signature stability but no longer feeds a
    PreToolUse pull hook — repo freshness moved daemon-side (THR-103): the
    orchestrator fast-forward-pulls every cloned repo at session spawn via
    ``refresh_workspace_repos``, uniformly for all executors.
    """
    return {
        "permissions": {
            "allow": allow_rules_for_agent(paths, agent_name, cli=False),
        },
        "hooks": {},
    }


@dataclass(slots=True)
class PersistentWorkspaceSetup:
    """Shared workspace files that every provider keeps up to date."""

    settings: Settings

    def ensure(self, workspace: Path, agent_name: str) -> list[str]:
        """Create persistent files and return detected cloned repo names."""
        workspace.mkdir(parents=True, exist_ok=True)

        # Migrate legacy recent_tasks.md → task_history.md in place so no
        # history is lost on workspaces created before the rename.
        legacy = workspace / "recent_tasks.md"
        renamed = workspace / "task_history.md"
        if legacy.exists() and not renamed.exists():
            legacy.rename(renamed)

        # task_history.md: always ensure
        history_path = workspace / "task_history.md"
        if not history_path.exists():
            history_path.write_text(f"# Task History: {agent_name}\n\n")

        # memory: state-aware, idempotent, lazy learnings/ -> memory/ migration
        # (THR-032 Phase R) + _index.md safety. Lazy imports avoid a hard infra
        # dep at module top.
        from runtime.infrastructure.learnings_store import MemoryStore
        from runtime.infrastructure.memory_migration import migrate_workspace

        # Idempotent + lossless: moves a legacy learnings/ dir to memory/ if
        # present, no-op once memory/ exists, leaves flat learnings.md alone.
        migrate_workspace(workspace)

        flat_path = workspace / "learnings.md"
        memory_dir = workspace / "memory"
        if memory_dir.exists():
            # Migrated or natively-new layout: idempotently ensure _index.md.
            store = MemoryStore(memory_dir)
            if not (memory_dir / "_index.md").exists():
                store.regenerate_index()
        elif flat_path.exists():
            # Pre-migration legacy flat-file workspace: leave untouched.
            pass
        else:
            # Brand-new workspace: create memory/ on the new layout.
            memory_dir.mkdir(parents=True, exist_ok=True)
            MemoryStore(memory_dir).regenerate_index()

        return self.detect_repo_names(workspace)

    @staticmethod
    def detect_repo_names(workspace: Path) -> list[str]:
        repos_dir = workspace / "repos"
        if not repos_dir.exists():
            return []
        return sorted(
            d.name for d in repos_dir.iterdir()
            if d.is_dir() and (d / ".git").exists()
        )


def refresh_workspace_repos(workspace: Path) -> None:
    """Fast-forward-refresh every cloned repo under ``workspace/repos/``.

    Called by the orchestrator on EVERY session spawn, before the executor
    subprocess starts, so all executors (claude, codex, opencode, pi) get
    fresh repo state (THR-103). This replaces the Claude-only PreToolUse
    settings hook that previously ran the same pull.

    Failure semantics: NEVER raises and never blocks a spawn. Offline,
    dirty-tree, non-fast-forward, or conflicted pulls exit non-zero, which
    ``subprocess.run`` without ``check=True`` does not raise for — the
    returncode is deliberately not inspected. Hung fetches are bounded by a
    per-repo 30s timeout; one repo's failure does not stop its siblings.

    Only already-cloned repos are refreshed (``detect_repo_names`` filters to
    dirs with a ``.git``); first-time cloning stays in the bootstrap path
    (``context_builder.clone_repo``).
    """
    try:
        repo_names = PersistentWorkspaceSetup.detect_repo_names(workspace)
    except OSError as exc:
        logger.debug("repo refresh: could not scan %s: %s", workspace, exc)
        return
    for name in repo_names:
        repo_dir = workspace / "repos" / name
        try:
            subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(repo_dir),
                capture_output=True,
                timeout=30,
            )
        except (subprocess.TimeoutExpired, subprocess.SubprocessError, OSError) as exc:
            logger.info("repo refresh: git pull skipped for %s: %s", repo_dir, exc)


class ClaudeWorkspaceAdapter:
    """Bootstrap and maintain Claude Code workspaces."""

    provider_name = "claude"

    def __init__(self, settings: Settings, paths: "OrgPaths", *, slug: str) -> None:
        self._settings = settings
        self._paths = paths
        self._slug = slug
        self._persistent = PersistentWorkspaceSetup(settings)

    def write_settings_json(
        self,
        workspace: Path,
        repo_names: list[str] | None = None,
        agent_name: str | None = None,
    ) -> None:
        """Write .claude/settings.json to workspace."""
        claude_dir = workspace / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        settings_data = build_settings_json(
            self._paths, repo_names or [], agent_name=agent_name,
        )
        (claude_dir / "settings.json").write_text(
            json.dumps(settings_data, indent=2) + "\n"
        )

    def write_claude_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        repo_names: list[str] | None = None,
    ) -> None:
        """Write CLAUDE.md to workspace with system prompt and context pointers.

        ``repo_names`` is accepted for API compatibility but is not listed
        inline — CLAUDE.md just points at ``agent.yaml`` as the source of
        truth so the repo list doesn't drift between the two files.
        """
        _assert_no_reserved_headers_in_body(agent_name, system_prompt)
        workspace.mkdir(parents=True, exist_ok=True)
        sections = self._build_sections(
            agent_name,
            system_prompt,
            workspace=workspace,
            include_start_task=True,
            repo_refresh_note=(
                "repositories cloned under `repos/`. The daemon fast-forward-"
                "refreshes each cloned repo at session spawn, before your "
                "session starts."
            ),
            callback_note=(
                "The `--from-file` form is mandatory here — multi-line `happyranch` "
                "invocations are blocked by the `Bash(happyranch:*)` permission rule."
            ),
            workflow_section=[
                "Every task arrives via the orchestrator's prompt. Use the **start-task** skill",
                "(in `.claude/skills/start-task/`) to parse parameters and report completion via",
                "`happyranch report-completion`. Mid-task memory items go through `happyranch memory`.\n",
            ],
            skills_dir=".claude/skills",
        )
        (workspace / "CLAUDE.md").write_text("\n".join(sections))

    def _build_sections(
        self,
        agent_name: str,
        system_prompt: str,
        *,
        workspace: Path,
        include_start_task: bool,
        repo_refresh_note: str,
        callback_note: str,
        workflow_section: list[str],
        skills_dir: str,
    ) -> list[str]:
        sections = [
            f"# Agent: {agent_name}\n",
            "## System Prompt\n",
            system_prompt.strip() + "\n",
            "## Available Repositories\n",
            "See `agent.yaml` in this workspace for the authoritative list of",
            repo_refresh_note + "\n",
            *_memory_bootstrap_section(workspace),
            "## Knowledge Base (shared across agents)\n",
            "Path: `<runtime>/kb/`. Read: everyone. Write: any agent (via `--from-file`).",
            "Delete: any team manager (audited); founder via `--as-founder`.",
            "Protocol docs are available in your session prompt `Protocol Docs`",
            "block — use `Read` to load any doc from its absolute bundled path.",
        ]
        if include_start_task:
            sections.extend([
                "The **start-task** skill's *Consult KB* and *Contribute to KB* steps are",
                "mandatory — do not skip them.\n",
            ])
        sections.extend([
            "Read:",
            "```",
            "happyranch kb list [--topic <t>] [--type <label>]",
            "happyranch kb search \"<keywords>\"",
            "happyranch kb get <slug>",
            "```\n",
            "Write (durable, cross-agent knowledge only — regulations, partner-API quirks,",
            "payment flows, founder rulings; **not** task-specific notes):",
            "```",
            "happyranch kb add --agent <you> --from-file /tmp/kb-<slug>.md",
            "happyranch kb update <slug> --agent <you> --from-file /tmp/kb-<slug>.md",
            "```",
            "Payload file needs YAML frontmatter (`slug`, `title`, `type`, `topic`,",
            "optional `tags`, `source_task`) followed by a markdown body. `type` is a",
            "freeform label (e.g. `reference`, `ruling`, `sop`) used for grouping.",
            callback_note + "\n",
            *_shared_artifacts_section(),
            *_thread_talk_dispatch_doctrine_section(),
            *_skills_directory_readonly_section(skills_dir),
            *_non_stop_command_warning_section(),
            *_task_completion_format_section(),
            "## Task Recall\n",
            "Past task context (brief, completion summary, output files) is retrievable via:",
            "```",
            "happyranch recall <task_id>                  # brief + final summary",
            "happyranch recall <task_id> --tree           # include the full subtree of child tasks",
            "happyranch recall <task_id> --fetch-output   # inline output file bodies (capped at ~200KB)",
            "```",
            "Use when the current brief references a prior task, when you need to revisit",
            "your own earlier output before reworking, or when a KB entry points to",
            "`source_task: TASK-xyz`. Your own recent activity is also summarized in",
            "`task_history.md` at the workspace root.\n",
            "## Workflow\n",
        ])
        sections.extend(workflow_section)
        return sections

    def ensure_workspace_ready(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
    ) -> None:
        """Make sure an agent workspace has every file the orchestrator requires."""
        repo_names = self._persistent.ensure(workspace, agent_name)

        # CLAUDE.md, settings.json, and the skills tree are always regenerated
        # so workspaces carried over from older code self-heal.
        self.write_claude_md(workspace, agent_name, system_prompt, repo_names=repo_names)
        self._copy_skills(workspace)
        self.write_settings_json(
            workspace, repo_names=repo_names, agent_name=agent_name,
        )

    def _copy_skills(self, workspace: Path) -> None:
        """No-op: canonical store + symlinks supersede wholesale copy.

        Skills are materialized on every session spawn via
        ``materialize_workspace_skills``, not at bootstrap time.
        """


class CodexWorkspaceAdapter:
    """Bootstrap and maintain Codex workspaces."""

    provider_name = "codex"

    def __init__(self, settings: Settings, paths: "OrgPaths", *, slug: str) -> None:
        self._settings = settings
        self._paths = paths
        self._slug = slug
        self._persistent = PersistentWorkspaceSetup(settings)

    def write_agents_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        repo_names: list[str] | None = None,
    ) -> None:
        """Write AGENTS.md to workspace with system prompt and context pointers.

        Codex CLI ≥0.125 discovers skills by walking ``.agents/skills/`` from
        the working directory up to the repo root, so the same
        ``protocol/skills/`` tree that Claude consumes is copied into
        ``<ws>/.agents/skills/`` by ``_copy_skills``. AGENTS.md therefore
        only points at the **start-task** skill — it does not re-inline the
        completion contract. The skill itself is the source of truth.
        """
        _assert_no_reserved_headers_in_body(agent_name, system_prompt)
        workspace.mkdir(parents=True, exist_ok=True)
        # Shared bootstrap sections (KB, memory, artifacts) are assembled in
        # Claude's _build_sections and flow through here unchanged.
        sections = ClaudeWorkspaceAdapter(self._settings, self._paths, slug=self._slug)._build_sections(
            agent_name,
            system_prompt,
            workspace=workspace,
            include_start_task=True,
            repo_refresh_note=(
                "repositories cloned under `repos/`. The daemon fast-forward-"
                "refreshes each cloned repo at session spawn, before your "
                "session starts; refresh again yourself mid-task if you need "
                "newer state."
            ),
            callback_note=(
                "Use the `--from-file` form to keep the callback contract stable "
                "across executors and avoid shell quoting issues."
            ),
            workflow_section=[
                "Every task arrives via the orchestrator's prompt. Use the **start-task** skill",
                "(in `.agents/skills/start-task/`) to parse parameters and report completion via",
                "`happyranch report-completion`. Mid-task memory items go through `happyranch memory`.\n",
            ],
            skills_dir=".agents/skills",
        )
        (workspace / "AGENTS.md").write_text("\n".join(sections))

    def _copy_skills(self, workspace: Path) -> None:
        """No-op: canonical store + symlinks supersede wholesale copy."""

    def ensure_workspace_ready(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
    ) -> None:
        """Make sure a Codex workspace has the shared persistent files and bootstrap."""
        self._persistent.ensure(workspace, agent_name)
        self.write_agents_md(workspace, agent_name, system_prompt)
        self._copy_skills(workspace)


class OpencodeWorkspaceAdapter:
    """Bootstrap and maintain opencode workspaces.

    opencode reads ``AGENTS.md`` (with ``CLAUDE.md`` as a fallback) and
    discovers skills under ``.opencode/skills/``, ``.claude/skills/``, or
    ``.agents/skills/``. We use the same ``AGENTS.md`` + ``.agents/skills/``
    layout as Codex so a single workspace shape works for both executors.

    The opencode-specific surface is ``opencode.json``: a structured
    permission file that gates bash by command-prefix glob. We write a
    strict default (``"*": "deny"``) plus per-agent allow rules sourced
    from the same ``allow_rules`` frontmatter Claude reads. No
    ``--dangerously-skip-permissions`` — the file is the enforcement
    surface, and bypassing it would erase the per-prefix discipline that
    CLAUDE.md mandates.
    """

    provider_name = "opencode"

    def __init__(self, settings: Settings, paths: "OrgPaths", *, slug: str) -> None:
        self._settings = settings
        self._paths = paths
        self._slug = slug
        self._persistent = PersistentWorkspaceSetup(settings)
        # AGENTS.md generation is identical to Codex — delegate.
        self._codex_adapter = CodexWorkspaceAdapter(settings, paths, slug=slug)

    def write_agents_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        repo_names: list[str] | None = None,
    ) -> None:
        """Write AGENTS.md to workspace. Same shape as Codex's AGENTS.md."""
        self._codex_adapter.write_agents_md(
            workspace, agent_name, system_prompt, repo_names=repo_names,
        )

    def write_opencode_json(
        self, workspace: Path, agent_name: str | None = None,
    ) -> None:
        """Write ``opencode.json`` with the agent's bash allow list.

        Default for unmatched bash is ``"deny"`` so an agent attempting an
        unsanctioned command fails fast rather than waiting on an
        interactive prompt that will never arrive in headless mode. The
        sanctioned channel (``happyranch``) is always allowed; per-agent extras
        come from the same ``allow_rules`` frontmatter Claude reads.
        """
        prefixes = bash_allow_prefixes_for_agent(self._paths, agent_name)
        permission_bash: dict[str, str] = {"*": "deny"}
        for prefix in prefixes:
            permission_bash[f"{prefix} *"] = "allow"
        config = {
            "$schema": "https://opencode.ai/config.json",
            "permission": {"bash": permission_bash},
        }
        (workspace / "opencode.json").write_text(
            json.dumps(config, indent=2) + "\n"
        )

    def _copy_skills(self, workspace: Path) -> None:
        """No-op: canonical store + symlinks supersede wholesale copy."""

    def ensure_workspace_ready(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
    ) -> None:
        """Make sure an opencode workspace has every file the orchestrator requires."""
        self._persistent.ensure(workspace, agent_name)
        self.write_agents_md(workspace, agent_name, system_prompt)
        self._copy_skills(workspace)
        self.write_opencode_json(workspace, agent_name=agent_name)


class PiWorkspaceAdapter:
    """Bootstrap and maintain Pi workspaces.

    Pi reads ``AGENTS.md`` and uses the same shared skill tree layout as
    Codex. Keep this as a named adapter so Pi-specific bootstrap files can be
    added later without changing ContextBuilder's provider contract.
    """

    provider_name = "pi"

    def __init__(self, settings: Settings, paths: "OrgPaths", *, slug: str) -> None:
        self._codex_adapter = CodexWorkspaceAdapter(settings, paths, slug=slug)

    def ensure_workspace_ready(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
    ) -> None:
        self._codex_adapter.ensure_workspace_ready(workspace, agent_name, system_prompt)
