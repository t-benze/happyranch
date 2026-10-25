from __future__ import annotations

import asyncio
import hashlib
import stat
import uuid
import json
import logging
import os
import re
import select
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar, copy_context
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from runtime.config import Settings

if TYPE_CHECKING:
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.producer_move import ProducerPreparation
    from runtime.orchestrator.task_pause import TaskInvocation
    from runtime.orchestrator.thread_move import ThreadPreparation

logger = logging.getLogger(__name__)

# Test override: when set (via monkeypatch), takes precedence over the
# settings-derived skills source. Production code leaves this ``None`` and
# adapters resolve the source via ``self._settings.get_bundled_skills_dir()``.
_SKILLS_SRC: Path | None = None


def _resolve_skills_src(settings: Settings) -> Path:
    """Source directory for ``runtime/skills/bundled/``.

    Honors the module-level ``_SKILLS_SRC`` test override before falling back
    to the settings-derived path so unit tests can stand up a fake skills tree
    in ``tmp_path`` without altering production behavior.
    """
    if _SKILLS_SRC is not None:
        return _SKILLS_SRC
    return settings.get_bundled_skills_dir()


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


# ── Retired copy entrypoints ──────────────────────────────────────
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
    task_id: str | None = None,
    session_id: str | None = None,
) -> list[dict]:
    """Serialize the complete pre-spawn skill materialization transaction.

    All three materialization steps — system-contract injection,
    managed-skill injection, and B2 custom-skill injection — run under a
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
    custom-skill links, and must not withdraw or mutate an existing valid
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
        org_root: per-org root (optional; for custom-skill resolution)
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
            task_id=task_id, session_id=session_id,
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
    release-managed and custom-skill links remain policy-reconciled and
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
        # Executor switch must publish through the same canonical function as
        # every ordinary launch.  That function already derives the complete
        # ordinary-context union and owns the single final publication barrier.
        return _materialize_unified_canonical(
            workspace, settings,
            slug=slug, context="bootstrap", provider=provider,
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

    # ── 3. B2 custom skills ─────────────────────────────────────────
    if db is not None and org_root is not None:
        custom_specs = _build_custom_skill_canonical_specs(
            store=store, org_root=org_root, db=db, slug=slug,
            agent_name=agent_name, team=team, task_id=None,
            session_id=None, session_context="bootstrap",
        )
        expected_specs.extend(custom_specs)

    return _publish_canonical_specs(
        materializer=materializer, expected_specs=expected_specs,
        workspace=workspace, slug=slug, org_root=org_root, db=db,
    )


def _publish_canonical_specs(
    *, materializer: SymlinkMaterializer, expected_specs: list[dict],
    workspace: Path, slug: str, org_root: Path | None, db,
) -> list[dict]:
    """Authoritatively filter and publish one canonical spec set."""
    # Lock order is workspace transaction -> org publication barrier -> DB
    # reads. Purge takes the same org barrier before its SQLite transaction.
    # Candidate building and session launch stay outside this narrow block;
    # the final authoritative read and both repairs are one lexical unit.
    if db is not None and org_root is not None:
        from runtime.skills.custom import service

        conn = getattr(db, "_conn", db)
        with service.canonical_publication_barrier(slug):
            view = (db.coherent_read_view() if hasattr(db, "coherent_read_view")
                    else nullcontext(conn))
            with view as reader:
                current_custom = {
                    row["slug"]: row
                    for row in reader.execute(
                        "SELECT slug, current_version_id, purged_at "
                        "FROM custom_skills WHERE org_slug=?",
                        (slug,),
                    ).fetchall()
                }
            expected_specs = [
                spec for spec in expected_specs
                if spec["slug"] not in current_custom
                or (
                    current_custom[spec["slug"]]["purged_at"] is None
                    and str(current_custom[spec["slug"]]["current_version_id"])
                    == str(spec["version"])
                )
            ]
            for subdir in (".claude/skills", ".agents/skills"):
                materializer.repair_workspace_skills(
                    expected_specs, workspace, subdir,
                )
    else:
        for subdir in (".claude/skills", ".agents/skills"):
            materializer.repair_workspace_skills(
                expected_specs, workspace, subdir,
            )
    return expected_specs


def select_system_contracts(workspace: Path):
    """Pure native all-context selection; does not build packages or links."""
    from runtime.skills.system_contracts import SessionContext, resolve_system_contracts_for_session
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

    return contracts_by_id


def select_release_skills(skills_root, settings, *, slug, team, agent_name, org_root):
    """Pure existing native registry/eligibility/exposure projection."""
    from runtime.skills.registry import SkillRegistry
    from runtime.skills.resolver import EligibilityResolver
    from runtime.skills.exposure import resolve_exposed_skills
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
            return resolve_exposed_skills(
                union_registry, resolver, org=slug, team=team, agent=agent_name,
            )

    return []


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
    task_id: str | None = None,
    session_id: str | None = None,
) -> list[dict]:
    """Derive one full expected set per provider root, reconcile once.

    Unified expected set = system contracts (union across ALL ordinary
    session contexts) + release-managed catalog + PUBLISHED/active
    B2 custom skills. This single set is reconciled via
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

    from runtime.orchestrator.task_pause import task_preparation_effects
    # Store initialization has its own native mkdir effects. A successful
    # initialization does not make a later read-only source refusal uncertain.
    with task_preparation_effects():
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
    contracts_by_id = select_system_contracts(workspace)
    contract_ids = set(contracts_by_id)

    # Validate ALL sources before any build.
    _preflight_system_contract_sources(
        contract_ids, src_root,
        workspace=workspace, provider=provider,
    )

    # The mandatory-source preflight above is affirmative pre-effect
    # validation. Package construction/publication below can be partial.
    with task_preparation_effects():
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
            exposed = select_release_skills(skills_root, settings, slug=slug,
                team=team, agent_name=agent_name, org_root=org_root)
            if exposed:
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

        # 3. B2 custom skills
        if db is not None and org_root is not None:
            custom_specs = _build_custom_skill_canonical_specs(
                store=store, org_root=org_root, db=db, slug=slug,
                agent_name=agent_name, team=team, task_id=task_id,
                session_id=session_id, session_context=context,
            )
            expected_specs.extend(custom_specs)

        return _publish_canonical_specs(
            materializer=materializer, expected_specs=expected_specs,
            workspace=workspace, slug=slug, org_root=org_root, db=db,
        )


def select_custom_skill_rows(db, *, slug, agent_name, team):
    """Capture current B2 eligibility before releasing shared DB ownership."""
    from runtime.skills.eligibility import EligibilityRecipient, EligibilityRule, SkillEligibilityState, resolve_custom_skill_eligibility
    conn = getattr(db, "_conn", db)
    view = (db.coherent_read_view() if hasattr(db, "coherent_read_view")
            else nullcontext(conn))
    selected = []
    with view as reader:
        rows = reader.execute(
            """SELECT s.id, s.slug, s.retired_at, s.purged_at, v.id AS version_id,
                  v.content_hash, v.content_artifact_key, v.validation_state
           FROM custom_skills s
           JOIN custom_skill_versions v ON v.id = s.current_version_id
           WHERE s.org_slug = ?""",
            (slug,),
        ).fetchall()
        recipient = EligibilityRecipient(agent_name, (team,))
        for row in rows:
            rules = reader.execute(
                """SELECT scope_type, scope_target, effect
               FROM custom_skill_eligibility_rules
               WHERE skill_id = ? AND superseded_at IS NULL""",
                (row["id"],),
            ).fetchall()
            decision = resolve_custom_skill_eligibility(
                SkillEligibilityState(
                    bool(row["retired_at"]), row["validation_state"], bool(row["purged_at"])
                ),
                [EligibilityRule(**dict(rule)) for rule in rules],
                recipient,
            )
            if decision.visible:
                selected.append(row)
    return selected


@contextmanager
def _custom_skill_materialization_writer(db: Any) -> Iterator[Any]:
    """Own only this outcome's SQL interval, never another caller's work.

    Raw connections retain their existing standalone use; production Database
    callers share its RLock. No package, publication-barrier acquisition or
    native cleanup work belongs inside this interval.
    """
    conn = getattr(db, "_conn", db)
    with getattr(db, "_lock", nullcontext()):
        if conn.in_transaction:
            raise ValueError("custom_skill_materialization_caller_transaction_not_allowed")
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except BaseException as exc:
            try:
                conn.rollback()
            except BaseException as rollback:
                exc.add_note(f"custom materialization rollback failed: {rollback!r}")
                raise exc from rollback
            raise


def _build_custom_skill_canonical_specs(
    *,
    store: CanonicalSkillStore,
    org_root: Path,
    db,
    slug: str,
    agent_name: str,
    team: str,
    task_id: str | None,
    session_id: str | None,
    session_context: str,
) -> list[dict]:
    """Build visible B2 custom skills through the canonical-store path.

    Each visible skill is independent: an artifact/build fault is persisted as
    a failed materialization for an identified task session and does not block
    system, managed, or sibling custom skills. Persistence failures propagate;
    no spec is admitted before its owned outcome transaction commits.
    """
    import hashlib
    import tempfile

    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    from runtime.skills.custom import service
    conn = getattr(db, "_conn", db)
    # Refuse before artifact/store effects rather than adopting pending caller
    # writes. Recheck ownership at every later SQL interval as well.
    with getattr(db, "_lock", nullcontext()):
        if conn.in_transaction:
            raise ValueError("custom_skill_materialization_caller_transaction_not_allowed")
        rows = select_custom_skill_rows(db, slug=slug, agent_name=agent_name, team=team)
    artifact_store = ArtifactStore(OrgPaths(org_root).artifacts_dir)
    specs: list[dict] = []
    recordable = (
        session_id is not None
        and session_context in {"task", "thread", "wake", "dream"}
        and (session_context != "task" or task_id is not None)
    )

    for row in rows:
        package_effects = False
        persistence_started = False
        try:
            artifact_key = row["content_artifact_key"]
            if not artifact_key:
                raise ValueError("No content_artifact_key")
            content = artifact_store.read(artifact_key)
            actual_hash = hashlib.sha256(content).hexdigest()
            if actual_hash != row["content_hash"]:
                raise ValueError(
                    "Artifact hash mismatch: expected "
                    f"{row['content_hash'][:16]}..., got {actual_hash[:16]}..."
                )
            # Artifact-key/read/hash refusals above are affirmative pre-effect
            # failures for the task owner. Temporary/package construction below
            # can leave effects even when this helper later omits the spec.
            package_effects = True
            with tempfile.TemporaryDirectory() as temporary_dir:
                source_dir = Path(temporary_dir)
                (source_dir / "SKILL.md").write_bytes(content)
                tree_hash = _compute_dir_hash(source_dir)
                store.build_from_source(
                    row["slug"], str(row["version_id"]), row["content_hash"],
                    source_dir, verify_source_hash=tree_hash,
                )
            # Selection and package construction may be expensive. Re-read the
            # authoritative row at the publication seam so a purge committed
            # during that window cannot publish a stale canonical spec or a
            # new link in either provider root. Existing canonical bytes are
            # retained by the logical-purge contract.
            persistence_started = True
            with _custom_skill_materialization_writer(db) as writer:
                current = writer.execute(
                    "SELECT current_version_id, purged_at FROM custom_skills "
                    "WHERE id=? AND org_slug=?",
                    (row["id"], slug),
                ).fetchone()
                if (
                    current is None
                    or current["purged_at"] is not None
                    or current["current_version_id"] != row["version_id"]
                ):
                    continue
                if recordable:
                    service.record_materialization(
                        writer, skill_id=row["id"], agent_name=agent_name,
                        task_id=task_id, session_context=session_context,
                        session_id=session_id, version_id=row["version_id"],
                        content_hash=row["content_hash"], success=True,
                    )
            specs.append({
                "slug": row["slug"],
                "version": str(row["version_id"]),
                "content_hash": row["content_hash"],
                "tree_hash": tree_hash,
            })
        except Exception as exc:
            # The native builder can leave canonical/package effects before
            # returning an omitted custom spec. Retain the actual native owner even
            # when this failure is caught and the outer materializer returns.
            from runtime.orchestrator.producer_move import current_producer_preparation
            from runtime.orchestrator.thread_move import current_thread_preparation
            from runtime.orchestrator.task_pause import current_invocation
            preparation = (current_thread_preparation() or current_producer_preparation()
                           or (current_invocation() if package_effects else None))
            if preparation is not None:
                try:
                    preparation.failed_cleanup()
                except BaseException as evidence:
                    exc.add_note(f"custom package preparation evidence failed: {evidence!r}")
                    raise exc from evidence
            if recordable:
                try:
                    with _custom_skill_materialization_writer(db) as writer:
                        service.record_materialization(
                            writer, skill_id=row["id"], agent_name=agent_name,
                            task_id=task_id, session_context=session_context,
                            session_id=session_id, version_id=row["version_id"],
                            content_hash=row["content_hash"], success=False,
                            error_message=str(exc),
                        )
                except BaseException as evidence:
                    exc.add_note(f"custom materialization failure recording failed: {evidence!r}")
                    raise exc from evidence
            if persistence_started:
                raise

    return specs


def _compute_dir_hash(src_dir: Path) -> str:
    """Compute a deterministic SHA-256 for a source tree."""
    import hashlib

    digest = hashlib.sha256()
    for path in sorted(src_dir.rglob("*")):
        if path.is_file():
            digest.update(str(path.relative_to(src_dir)).encode())
            digest.update(b"\x00")
            digest.update(path.read_bytes())
            digest.update(b"\x00")
    return digest.hexdigest()


# ── Pre-launch integrity validation ────────────────────────────────
# Before every executor launch, validate that workspace skill links
# resolve to the expected canonical packages and that canonical package
# integrity (tree hashes and B2 custom-skill artifact hashes) is
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
      B2-declared member hashes or the source tree hash
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
    #   recover <slug> <version> <content_hash>`. Validates B2 provenance
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

    Managed-catalog and B2 custom skills are now resolved via
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


def _skills_directory_readonly_section(skills_dir: str, agent_name: str) -> list[str]:
    """System-injected operational guidance: do not edit managed skill links.

    Skill entries under BOTH ``.claude/skills`` and ``.agents/skills`` are
    daemon-materialized from the canonical skill store. This section
    directs agents NOT to edit these managed links and to use the B2
    custom-skill workflow instead.

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
    lines = [
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
    ]
    lines.extend([
        "If a new custom skill is needed, every verified agent can use the B2 ",
        "create-skill workflow directly; there is no pilot roster or slug restriction. ",
        "It remains bound to this session. The response includes the editable custom ",
        "skill, immutable version, default-hidden reason, and verified agent/task/session ",
        "provenance:\n",
        "```",
        "happyranch skills create --from-file <path> --session-id <your-session-id>",
        "```\n",
    ])
    return lines


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
        "ready. See the **jobs** skill (available",
        "to you under your workspace's skills directory) for the form fields, the",
        "persistence flag and genuine job-result waits. Obtain required content",
        "authorization in a thread BEFORE submission from an ordinary task session.",
        "Omit review_required or set false; true is refused before allocation.",
        "Admission is not script success; inspect the actual terminal receipt.\n",
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
    "[RESERVED] Active Team Escalation Policy",
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
    try:
        from runtime.orchestrator.active_authority_policy import assert_no_reserved_team_policy_header
        assert_no_reserved_team_policy_header(body, source=f"agent body {agent_name}")
    except Exception as exc:
        from runtime.orchestrator.active_authority_policy import ActiveAuthorityPolicyError
        if isinstance(exc, ActiveAuthorityPolicyError):
            raise ReservedHeaderInAgentBody(str(exc)) from exc
        raise


def _task_completion_format_section() -> list[str]:
    """System-injected reminder of the completion contract.

    Replaces the per-agent ``## Task Completion Format`` stubs that lived in
    agent ``.md`` files. Request models and transition handlers own the
    completion contract. The ``start-task`` skill's *Report completion* step
    explains the payload, including the owner-only ``decision`` block.
    This section points at that skill and lists the prose-``summary`` items
    that apply regardless of role, so individual agent files don't have to
    restate (and slowly drift from) the contract.
    """
    return [
        "## Task Completion Format\n",
        "Every task ends with a `happyranch report-completion --from-file <path>`",
        "callback driven by the **start-task** skill. The skill's *Report",
        "completion* step carries the canonical JSON payload shape — fields,",
        "the task-owner `decision` scope, and the blocked-path variant.",
        "Worker-owned roots use only server-authorized self decisions;",
        "manager-only operations remain restricted. Consult the canonical skill.",
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


# One budget for the complete batch, not a fresh allowance per probe/repo.
_REPO_REFRESH_SECONDS = 30.0


# Preparation state follows the original owner through the joined offload.
_repo_preparation: ContextVar[Any] = ContextVar("repository_preparation", default=None)


def _repo_directory(path: Path) -> int:
    path = Path(path)
    if not path.is_absolute():
        raise OSError("repository_path_not_absolute")
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        info = os.fstat(fd)
        if info.st_uid != os.getuid():
            raise OSError("repository_directory_owner")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _repo_refresh_repository(repo_dir: Path) -> dict[str, Any]:
    """Observe literal physical identity before ordered lease admission."""

    def literal_file(parent, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise OSError("repository_pointer_unsafe")
            raw = os.read(fd, 4097)
            if len(raw) > 4096 or os.read(fd, 1):
                raise OSError("repository_pointer_bound")
            return raw
        finally:
            os.close(fd)

    def locate():
        literal_root = Path(repo_dir).absolute()
        checked = _repo_directory(literal_root)
        os.close(checked)
        root = Path(os.path.abspath(literal_root))
        fd = _repo_directory(root)
        try:
            info = os.stat(".git", dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                gitdir = root / ".git"
            else:
                raw = literal_file(fd, ".git")
                if not raw.startswith(b"gitdir: "):
                    raise OSError("repository_pointer_invalid")
                value = _repo_refresh_line(raw[8:])
                gitdir = root / value
        finally:
            os.close(fd)
        fd = _repo_directory(gitdir)
        try:
            try:
                raw = literal_file(fd, "commondir")
            except FileNotFoundError:
                common = Path(os.path.abspath(gitdir))
            else:
                literal_common = gitdir / _repo_refresh_line(raw)
                checked = _repo_directory(literal_common)
                os.close(checked)
                common = Path(os.path.abspath(literal_common))
        finally:
            os.close(fd)
        return common

    common = locate()
    descriptor = _repo_directory(common)
    try:
        pin = os.fstat(descriptor)
        return {"path": str(common), "device": pin.st_dev, "inode": pin.st_ino}
    finally:
        os.close(descriptor)


@contextmanager
def _repo_refresh_lease(repo_dir: Path, *, deadline: float, cancel_requested: threading.Event | None,
        expected_repository: dict | None = None, blocking: bool = True):
    """Stable descriptor custody, physically ordered by the caller; never unlink."""
    import fcntl

    repository = _repo_refresh_repository(repo_dir)
    if expected_repository is not None and repository != expected_repository:
        raise OSError("repository_common_directory_changed")
    common = Path(repository["path"])
    parent = _repo_directory(common)
    lock = None
    try:
        pin = os.fstat(parent)
        if (pin.st_dev, pin.st_ino) != (repository["device"], repository["inode"]):
            raise OSError("repository_common_directory_changed")
        lock = os.open("happyranch-repository-refresh.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                       0o600, dir_fd=parent)
        lock_pin = os.fstat(lock)
        if (not stat.S_ISREG(lock_pin.st_mode) or lock_pin.st_uid != os.getuid()
                or lock_pin.st_nlink != 1 or lock_pin.st_mode & 0o022):
            raise OSError("repository_lease_unsafe")
        while True:
            if time.monotonic() >= deadline or (cancel_requested is not None and cancel_requested.is_set()):
                raise TimeoutError("repository_lease_unavailable")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if not blocking:
                    raise TimeoutError("repository_lease_unavailable") from None
                if cancel_requested is not None:
                    cancel_requested.wait(min(.05, max(0.0, deadline-time.monotonic())))
                else:
                    time.sleep(min(.05, max(0.0, deadline-time.monotonic())))

        def validate(*, finishing: bool = False):
            if not finishing and (time.monotonic() >= deadline or (cancel_requested is not None and cancel_requested.is_set())):
                raise TimeoutError("repository_lease_unavailable")
            if _repo_refresh_repository(repo_dir) != repository:
                raise OSError("repository_common_directory_changed")
            check = _repo_directory(common)
            try:
                current = os.fstat(check)
                entry = os.stat("happyranch-repository-refresh.lock", dir_fd=check, follow_symlinks=False)
                if ((current.st_dev, current.st_ino) != (pin.st_dev, pin.st_ino)
                        or (entry.st_dev, entry.st_ino, entry.st_uid, entry.st_nlink, entry.st_mode) !=
                        (lock_pin.st_dev, lock_pin.st_ino, lock_pin.st_uid, lock_pin.st_nlink, lock_pin.st_mode)):
                    raise OSError("repository_lease_identity_changed")
            finally:
                os.close(check)
        validate()
        yield {"path": str(common), "device": pin.st_dev, "inode": pin.st_ino}, validate
    finally:
        # Closing the original locked descriptor releases it. Never unlink:
        # waiting owners must keep contending on this SAME inode.
        if lock is not None:
            os.close(lock)
        os.close(parent)


def _repo_owner_binding(owner: Any) -> tuple[dict[str, Any], Any]:
    """Read the actual native owner; IDs never manufacture a replacement."""
    if hasattr(owner, "orch"):
        db, org, kind = owner.orch._db, owner.orch._slug, "task"
        scope, agent, invocation, attempt, native = (owner.task.id, owner.agent,
            owner.identity, owner.refusal_attempt_id, owner.task.id)
    else:
        db, org = owner.admission.db, owner.state.org_slug
        state = owner.state
        kind = "thread" if hasattr(owner, "inv") else state.kind
        scope = state.thread_id if kind == "thread" else state.source_id
        agent, invocation = state.agent_id, state.preparation_id
        native = owner.inv.id if kind == "thread" else state.occurrence_id
        # No artificial task/executor identity. The existing native phase and
        # ordinal bind the repository attempt on this actual preparation.
        attempt = hashlib.sha256((invocation+":"+getattr(state, "phase", "initial")+":"
            +state.boundary+":"+str(state.ordinal)).encode()).hexdigest()[:32]
    context = getattr(db, "_memory_collection_context", None)
    observer = getattr(context, "memory_collection", None)
    if observer is None or observer.db is not db or observer.org != org:
        raise RuntimeError("repository_preparation_boot_unavailable")
    return {"org": org, "kind": kind, "scope": scope, "agent": agent,
        "session_id": owner.session_id, "invocation_id": invocation, "attempt_id": attempt,
        "native_id": native, "boot_id": observer.boot_id, "source": _repo_preparation_source(owner)}, db


def _repo_preparation_source(owner: Any) -> str:
    """Bounded selected loaded/source agreement, not an executing-commit claim.

    Compilation is retained only on this same live owner. Every use rechecks
    actual function/code bindings, finite globals and every bounded source file;
    no historical proof or mutable DB observation is cached.
    """
    import inspect
    import sys
    from types import FunctionType
    from runtime.infrastructure.memory_collection import _code_projection, _find_code
    db = owner.orch._db if hasattr(owner, "orch") else owner.admission.db
    functions = [inspect.unwrap(v) for k, v in tuple(globals().items())
        if k.startswith("_repo_") and type(v) is FunctionType]
    functions += [getattr(_RepoPreparationEvidence, name) for name in
        ("__init__", "emit", "committed", "active", "revalidate", "returned", "settle")]
    functions += [getattr(type(owner), name) for name in ("record_repository_preparation",
        "settle_repository_preparation", "repository_preparation_revalidate", "failed_cleanup")]
    functions += [getattr(db, name).__func__ for name in ("repository_preparation_history_uncommitted",
        "append_repository_preparation_uncommitted", "repository_preparation_snapshot_uncommitted")]
    identities = tuple((v, v.__code__) for v in functions)
    constants = (_REPO_REFRESH_SECONDS, _REPO_PULL_CAPTURE_BYTES, _REPO_PULL_POLL_SECONDS)
    if constants != (30.0, 4096, 0.05) or tuple(type(v) for v in constants) != (float, int, float):
        raise RuntimeError("repository_preparation_globals_changed")
    cache = getattr(owner, "_repository_source_cache", None)
    sources = {}
    for function in functions:
        path = Path(function.__code__.co_filename)
        if str(path) not in sources:
            with path.open("rb") as stream:
                raw = stream.read(1024*1024+1)
            if len(raw) > 1024*1024:
                raise RuntimeError("repository_preparation_source_bound")
            sources[str(path)] = (raw, hashlib.sha256(raw).hexdigest())
    digests = tuple((path, digest) for path, (_, digest) in sources.items())
    if cache is not None:
        if cache[:2] != (identities, digests):
            raise RuntimeError("repository_preparation_loaded_source_changed")
        return cache[2]
    compiled, rows = {}, []
    for function in functions:
        code = function.__code__
        path = code.co_filename
        if path not in compiled:
            compiled[path] = compile(sources[path][0], path, "exec", dont_inherit=True, optimize=sys.flags.optimize)
        loaded = _code_projection(code)
        if loaded != _code_projection(_find_code(compiled[path], code.co_qualname)):
            raise RuntimeError("repository_preparation_loaded_source_changed")
        rows.append([function.__module__, code.co_qualname, sources[path][1], loaded])
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    owner._repository_source_cache = (identities, digests, digest)
    return digest


def _repo_retain_materialization(owner: Any, workspace: Path, settings: Settings, specs: list[dict]) -> None:
    from runtime.skills.canonical_store import _get_canonical_store_root
    if type(specs) is not list or len(specs) > 128:
        raise RuntimeError("repository_preparation_materialization_bound")
    raw = json.dumps(specs, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(raw) > 65536:
        raise RuntimeError("repository_preparation_materialization_bound")
    receipt = hashlib.sha256(raw).hexdigest()
    paths = (workspace, _get_canonical_store_root(settings))
    for path in paths:
        descriptor = _repo_directory(Path(path).absolute())
        os.close(descriptor)
    owner._repository_retained_effects = [{"path": str(path), "disposition": "retained", "receipt": receipt}
        for path in paths]


def _repo_effect_inventory(owner: Any) -> dict:
    """Retain completed original intervals and native scratch observations.

    Retention is not deletion or proof a writer remains alive. An unknown
    interval or unsupported inventory blocks only positive settlement.
    """
    intervals = getattr(owner, "_repository_effect_intervals", [])
    if len(intervals) > 128 or any(not v["closed"] or v["failed"] for v in intervals):
        raise RuntimeError("repository_preparation_earlier_effects_unresolved")
    retained = list(getattr(owner, "_repository_retained_effects", []))
    if hasattr(owner, "orch"):
        from runtime.orchestrator.task_scratch import validate_task_scratch_manifest
        workspace = owner.orch._paths.workspaces_dir / owner.agent
        path = workspace / ".happyranch" / "task-scratch-manifests" / (owner.task.id+".json")
        parent_descriptor = None
        try:
            parent_descriptor = _repo_directory(path.parent)
            descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_descriptor)
        except FileNotFoundError:
            # First refresh precedes native task scratch creation. A missing
            # manifest cannot close an actually admitted scratch interval.
            if any(v.get("kind") == "scratch" for v in intervals):
                raise RuntimeError("repository_preparation_scratch_unavailable") from None
        else:
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid() or metadata.st_nlink != 1:
                    raise RuntimeError("repository_preparation_scratch_unsafe")
                raw = os.read(descriptor, 65537)
                if len(raw) > 65536 or os.read(descriptor, 1):
                    raise RuntimeError("repository_preparation_scratch_bound")
                manifest = json.loads(raw)
                root = workspace / ".happyranch" / "task-tmp" / owner.task.id
                producers = validate_task_scratch_manifest(manifest, expected_task_id=owner.task.id, expected_root=root)
                matching = [v for v in producers if v["producer_kind"] == "agent" and v["producer_id"] == owner.session_id]
                if any(v.get("kind") == "scratch" for v in intervals) and len(matching) != 1:
                    raise RuntimeError("repository_preparation_scratch_owner_unavailable")
                if matching:
                    retained.append({"path": str(root), "disposition": "retained",
                        "receipt": hashlib.sha256(raw).hexdigest()})
            finally:
                os.close(descriptor)
        finally:
            if parent_descriptor is not None:
                os.close(parent_descriptor)
    for evidence in getattr(owner, "_repository_preparations", {}).values():
        raw = json.dumps(evidence.repository, sort_keys=True, separators=(",", ":")).encode()
        retained.append({"path": evidence.repository["path"], "disposition": "retained",
            "receipt": hashlib.sha256(raw).hexdigest()})
    if len(retained) > 128:
        raise RuntimeError("repository_preparation_inventory_bound")
    return {"intervals": [dict(v) for v in intervals], "retained": retained, "outer_closed": True}


@contextmanager
def _repo_effect_control(owner: Any) -> Iterator[None]:
    """Short original-owner arbitration; never keep gates across effect work."""
    if hasattr(owner, "orch"):
        with owner.store.writer(owner.task.id):
            yield
    else:
        with owner.admission.move.gate, owner.admission.db._lock:
            yield


@contextmanager
def _repo_effect_interval(owner: Any) -> Iterator[None]:
    intervals = getattr(owner, "_repository_effect_intervals", None)
    if intervals is None:
        owner._repository_effect_intervals = intervals = []
    if len(intervals) >= 128:
        owner.failed_cleanup()
        raise RuntimeError("repository_preparation_effect_bound")
    import sys
    from runtime.orchestrator.task_scratch import prepare_task_scratch
    frame = sys._getframe()
    kind = "native"
    for _ in range(24):
        if frame is None:
            break
        if frame.f_code is prepare_task_scratch.__code__:
            kind = "scratch"
            break
        frame = frame.f_back
    del frame
    with _repo_effect_control(owner):
        if len(intervals) >= 128:
            raise RuntimeError("repository_preparation_effect_bound")
        interval = {"id": len(intervals), "kind": kind, "closed": False, "failed": False}
        intervals.append(interval)
    try:
        yield
    except BaseException:
        with _repo_effect_control(owner):
            interval["failed"] = True
        raise
    finally:
        with _repo_effect_control(owner):
            interval["closed"] = True


@contextmanager
def _repo_preparation_gates(owner: Any, evidence: Any = None, *, deadline: float | None = None,
        finishing: bool = False) -> Iterator[str | None]:
    """All preparation-local application admission, including gate registries.

    The caller already owns repository custody. Registry mutexes are released
    before waiting for their native gate; the plain binding lock is taken once.
    Ordinary FIFO, Pause and tracker callers keep their existing semantics.
    """
    from contextlib import ExitStack
    db = owner.orch._db if hasattr(owner, "orch") else owner.admission.db
    if evidence is not None:
        if evidence.owner is not owner or evidence.db is not db:
            raise RuntimeError("repository_preparation_wrong_owner")
        deadline, finishing = evidence.deadline, evidence.finishing or finishing
    if deadline is None:
        raise RuntimeError("repository_preparation_deadline_unavailable")

    def acquire(lock: Any) -> None:
        remaining = deadline-time.monotonic()
        admitted = (lock.acquire(timeout=remaining) if remaining > 0 else
            finishing and lock.acquire(blocking=False))
        if not admitted:
            raise TimeoutError("repository_preparation_control_deadline")

    root_id = None
    if hasattr(owner, "orch"):
        acquire(db._lock)
        try:
            with db._memory_collection_read_budget(publication=True):
                root_id = owner.store.root_uncommitted(owner.task.id)["id"]
        finally:
            db._lock.release()
    with ExitStack() as stack:
        if root_id is not None:
            sessions = owner.orch._sessions
            if sessions is None:
                raise RuntimeError("repository_preparation_binding_unavailable")
            # These are the SAME persistent native maps. Calling their public
            # lookup while holding the plain map mutex would acquire it twice.
            acquire(sessions._lock)
            try:
                key = (owner.task.id, owner.agent)
                binding = sessions._binding_leases.setdefault(key, threading.Lock())
            finally:
                sessions._lock.release()
            acquire(binding)
            stack.callback(binding.release)
            acquire(owner.store._gates_lock)
            try:
                root_gate = owner.store._gates.setdefault(root_id, threading.RLock())
            finally:
                owner.store._gates_lock.release()
            acquire(root_gate)
            stack.callback(root_gate.release)
        locks = ([owner.orch._team_moves.gate] if root_id is not None
            else [owner.admission.move.gate]) + [db._lock]
        for lock in locks:
            acquire(lock)
            stack.callback(lock.release)
        yield root_id


@contextmanager
def _repo_preparation_writer(owner: Any, evidence: Any, *,
        committed: Callable[[], None] | None = None) -> Iterator[None]:
    """One original-owner transaction; publish COMMIT facts before gate release."""
    db = evidence.db
    with _repo_preparation_gates(owner, evidence) as root_id:
        if not evidence.active() and not evidence.finishing:
            raise TimeoutError("repository_preparation_control_deadline")
        with db._memory_collection_read_budget(publication=True):
            if db._conn.in_transaction:
                raise RuntimeError("repository_preparation_sql_owner")
            db._conn.execute("BEGIN IMMEDIATE")
            try:
                if root_id is not None:
                    # Same root/row validation as the native Pause writer,
                    # without another unbounded registry lookup or binding lock.
                    if owner.store.root_uncommitted(owner.task.id)["id"] != root_id:
                        raise RuntimeError("repository_preparation_root_changed")
                    owner.store.row_uncommitted(root_id)
                else:
                    owner.admission.revalidate_uncommitted(owner)
                yield
                db._conn.commit()
            except BaseException:
                db._conn.rollback()
                raise
            # No deadline/readback/reacquisition follows successful COMMIT.
            # Even a later context restoration error cannot erase these facts.
            if committed is not None:
                committed()


@contextmanager
def _repo_preparation_cleanup(owner: Any, deadline: float) -> Iterator[None]:
    """Bound failure attribution even when evidence construction itself failed.

    This is the original batch deadline, not a replacement evidence identity.
    The caller MUST latch uncertainty before this fallible admission.
    """
    db = owner.orch._db if hasattr(owner, "orch") else owner.admission.db
    with _repo_preparation_gates(owner, deadline=deadline, finishing=True) as root_id:
        with db._memory_collection_read_budget(publication=True):
            if root_id is None:
                # Native thread/producer leaf writers own their transaction.
                yield
                return
            if db._conn.in_transaction:
                raise RuntimeError("repository_preparation_sql_owner")
            db._conn.execute("BEGIN IMMEDIATE")
            try:
                if owner.store.root_uncommitted(owner.task.id)["id"] != root_id:
                    raise RuntimeError("repository_preparation_root_changed")
                owner.store.row_uncommitted(root_id)
                yield
                db._conn.commit()
            except BaseException:
                db._conn.rollback()
                raise


def _repo_closure_digest_uncommitted(owner: Any, evidence: Any) -> str:
    db = evidence.db
    if hasattr(owner, "orch"):
        return db.repository_preparation_snapshot_uncommitted(owner.task.id,
            owner.identity, owner.refusal_attempt_id)["state_digest"]
    state = {"native": owner.state.model_dump(mode="json"),
        "history": db.repository_preparation_history_uncommitted(evidence.binding["scope"])}
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _repo_closure_digest(owner: Any, evidence: Any) -> str:
    # Digest admission is the preparation writer's admission, never a new
    # census lock-wait allowance. The existing finite SQL/work budget remains.
    if not evidence.db._lock._is_owned() or not evidence.db._conn.in_transaction:
        raise RuntimeError("repository_preparation_sql_owner")
    return _repo_closure_digest_uncommitted(owner, evidence)


def _repo_check_closure_uncommitted(owner: Any, evidence: Any, body: dict) -> None:
    if body["phase"] != "closed":
        return
    if (getattr(owner, "_repository_worker_active", True) or not evidence.worker_returned
            or evidence.pending or evidence.uncertain or evidence.closed
            or body["state_digest"] != _repo_closure_digest_uncommitted(owner, evidence)):
        raise RuntimeError("repository_preparation_stale_closure")
    history = evidence.db.repository_preparation_history_uncommitted(evidence.binding["scope"])
    selected = [v["event"] for v in history if v["event"]["obligation"] == evidence.obligation]
    if (not selected or selected[-1]["phase"] != "outer_closed"
            or selected[-1]["inventory"]["intervals"] != owner._repository_effect_intervals):
        raise RuntimeError("repository_preparation_outer_changed")


def _repo_record_uncommitted(owner: Any, evidence: Any, body: dict) -> int:
    if hasattr(owner, "orch"):
        from runtime.orchestrator.launch_refusals import _current
        owner.store.held_uncommitted(owner.task.id)
        owner.orch._team_moves.revalidate_uncommitted(owner.move_reservation)
        if (owner.store._live.get(owner.identity) is not owner or not _current(owner)
                or owner.launch_observed or owner.attempt_committed):
            raise RuntimeError("repository_preparation_owner_changed")
    if body["phase"] == "opened":
        owner._repository_preparations[body["obligation"]] = evidence
        if hasattr(owner, "orch"):
            owner.move_reservation.repository_obligations[body["obligation"]] = False
            owner.move_reservation.repository_evidence[body["obligation"]] = evidence
    elif owner._repository_preparations.get(body["obligation"]) is not evidence:
        raise RuntimeError("repository_preparation_wrong_obligation")
    if body["phase"] == "closed" and not hasattr(owner, "orch") and (
            owner.state.failed_cleanup or not owner.owner_live or owner.attempt_open):
        raise RuntimeError("repository_preparation_owner_changed")
    _repo_check_closure_uncommitted(owner, evidence, body)
    return evidence.db.append_repository_preparation_uncommitted(body)


def _repo_native_record(owner: Any, evidence: Any, body: dict) -> int:
    try:
        if evidence.owner is not owner or body["owner"] != owner.repository_preparation_binding()[0]:
            raise RuntimeError("repository_preparation_wrong_owner")
        with _repo_preparation_writer(owner, evidence,
                committed=lambda: evidence.committed(body, receipt)):
            receipt = _repo_record_uncommitted(owner, evidence, body)
        return receipt
    except BaseException as exc:
        try:
            owner.failed_cleanup(repository_deadline=evidence.deadline)
        except BaseException:
            exc.add_note("repository preparation failure attribution unavailable; original error retained")
        raise


def _repo_close_preparation(owner: Any, evidence: Any) -> bool:
    """Construct, validate and append closure on ONE admitted native snapshot."""
    from runtime.infrastructure.db.tasks import repository_preparation_event
    try:
        if evidence.owner is not owner or owner.repository_preparation_binding()[0] != evidence.binding:
            raise RuntimeError("repository_preparation_wrong_owner")
        with _repo_preparation_writer(owner, evidence,
                committed=lambda: evidence.committed(body, receipt)):
            body = repository_preparation_event(evidence.binding, evidence.repository, evidence.obligation,
                evidence.sequence, "closed", probe=evidence.probe, complete=True,
                state_digest=_repo_closure_digest(owner, evidence))
            receipt = _repo_record_uncommitted(owner, evidence, body)
        return evidence.closed
    except BaseException as exc:
        evidence.uncertain = True
        try:
            owner.failed_cleanup(repository_deadline=evidence.deadline)
        except BaseException:
            exc.add_note("repository preparation failure attribution unavailable; original error retained")
        raise


def _repo_native_settle(owner: Any, evidence: Any) -> bool:
    if (evidence.owner is not owner or not evidence.worker_returned or evidence.pending
            or evidence.uncertain or owner.state.failed_cleanup or not owner.owner_live or owner.attempt_open):
        return False
    inventory = _repo_effect_inventory(owner)
    evidence.emit("outer_closed", complete=True, inventory=inventory)
    # The closure writer revalidates the actual native owner. Ordinary FIFO
    # admission has no place in this deadline-bound finishing tail.
    return _repo_close_preparation(owner, evidence)


class _RepoPreparationEvidence:
    """A single native obligation, never an old-owner reconstruction."""

    def __init__(self, owner, supervisor, repository, validate, deadline, cancel_requested):
        from runtime.orchestrator.host_supervisor import CancellationToken
        self.owner, self.supervisor, self.repository = owner, supervisor, repository
        self.validate_lease, self.deadline, self.cancel_requested = validate, deadline, cancel_requested
        self.binding, self.db = owner.repository_preparation_binding()
        self.obligation = uuid.uuid4().hex
        self.sequence, self.uncertain = 0, False
        self.probe = None
        self.cancellation = CancellationToken()
        self.enqueued_at = deadline - _REPO_REFRESH_SECONDS
        self.worker_returned = False
        self.closed = False
        self.last_receipt = None
        self.pending = set()
        self.children = {}  # Original opaque handles/Popen objects stay private and owner-bound.
        self.opening_attempted = False
        self.finishing = False

    def active(self) -> bool:
        return (time.monotonic() < self.deadline and not self.cancellation.cancelled
            and not (self.cancel_requested is not None and self.cancel_requested.is_set()))

    def revalidate(self) -> None:
        self.validate_lease()
        self.owner.repository_preparation_revalidate(self)
        binding, db = self.owner.repository_preparation_binding()
        if binding != self.binding or db is not self.db:
            raise RuntimeError("repository_preparation_owner_changed")

    def emit(self, phase: str, **facts: Any) -> int:
        from runtime.infrastructure.db.tasks import repository_preparation_event
        if phase in {"consumed", "finished", "not_admitted", "worker_returned", "outer_closed", "closed"}:
            # Cleanup/evidence tails may use only immediately available native
            # gates after expiry. They never extend Git/queue admission time.
            self.finishing = True
        if phase != "opened" and self.sequence == 0:
            self.opening_attempted = True
            self.emit("opened")
        if self.sequence >= 512:
            self.uncertain = True
            raise RuntimeError("repository_preparation_overflow")
        body = repository_preparation_event(self.binding, self.repository, self.obligation,
            self.sequence, phase, probe=self.probe, **facts)
        try:
            receipt = self.owner.record_repository_preparation(self, body)
        except BaseException:
            self.uncertain = True
            raise
        return receipt

    def committed(self, body: dict, receipt: int) -> None:
        # Called only AFTER the actual COMMIT, still under original gates.
        # These fixed local publications require no further application lock.
        self.sequence += 1
        phase = body["phase"]
        if phase == "intent":
            self.pending.add(body["request_ref"])
        if phase in {"finished", "not_admitted"}:
            self.pending.discard(body["request_ref"])
            self.uncertain |= body["complete"] is not True
        self.last_receipt = receipt
        if phase == "closed":
            self.closed = True
            if hasattr(self.owner, "orch"):
                self.owner.move_reservation.repository_obligations[self.obligation] = True

    def returned(self) -> None:
        # Called by the ORIGINAL caller after actual worker return/join. There
        # is no process-absence or timeout inference here.
        self.worker_returned = True
        if not self.opening_attempted and self.sequence == 0:
            # Affirmative no child/audit admission, not missing child evidence.
            # There is no new repository obligation to settle or clear.
            self.closed = True
            return
        self.emit("worker_returned", complete=not self.pending and not self.uncertain)

    def settle(self) -> bool:
        self.validate_lease(finishing=True)
        if self.closed:
            return True
        self.closed = self.owner.settle_repository_preparation(self)
        return self.closed


def _repo_refresh_git(
    repo_dir: Path, arguments: list[str], *, deadline: float,
    cancel_requested: threading.Event | None,
) -> subprocess.CompletedProcess[bytes] | None:
    """Bounded, complete private control output; never authorize from a prefix.

    Reuse the direct-child supervisor, with stdout in place of diagnostic stderr.
    No lazy object fetch is allowed in a read-only availability/ancestry probe.
    """
    result = _repo_refresh_child(
        repo_dir, ["--no-lazy-fetch", *arguments], deadline=deadline,
        cancel_requested=cancel_requested, control_output=True,
    )
    if result is None:
        return None
    try:
        if (result.failure is not None or not result.settled
                or not result.tree_quiescent or not result.capture_complete or result.capture_truncated):
            if result.failure is not None:
                raise result.failure from None
            raise subprocess.SubprocessError("repository_refresh_probe_unavailable")
        return subprocess.CompletedProcess(arguments, result.returncode, bytes(result.stderr), b"")
    finally:
        result.stderr.clear()


_REPO_PULL_CAPTURE_BYTES = 4096
_REPO_PULL_POLL_SECONDS = 0.05


@dataclass
class _RepoPullOutcome:
    """Private capture only; neither bytes nor an original exception leave here."""

    refresh_phase: str = "fetch"
    probe: str | None = None
    tree_quiescent: bool = False
    receipt_ref: int | None = None
    started: bool = False
    returncode: int | None = None
    settled: bool = False
    stderr: bytearray = field(default_factory=bytearray, repr=False)
    capture_complete: bool = False
    capture_truncated: bool = False
    timed_out: bool = False
    failure: BaseException | None = field(default=None, repr=False)

    def fail(self, exc: BaseException) -> None:
        if self.failure is None:
            self.failure = _repo_pull_safe_error(exc)
        self.timed_out = self.timed_out or isinstance(exc, subprocess.TimeoutExpired)
        self.capture_complete = False


def _repo_pull_safe_error(exc: BaseException) -> BaseException:
    # Never retain text, notes, command, output, traceback or exception chains.
    if isinstance(exc, asyncio.CancelledError):
        return asyncio.CancelledError("repository_pull_interrupted")
    if isinstance(exc, KeyboardInterrupt):
        return KeyboardInterrupt("repository_pull_interrupted")
    if isinstance(exc, SystemExit):
        return SystemExit("repository_pull_interrupted")
    if isinstance(exc, subprocess.TimeoutExpired):
        return subprocess.TimeoutExpired("git_pull_ff_only", _REPO_REFRESH_SECONDS)
    if isinstance(exc, OSError):
        return OSError("repository_pull_io_failed")
    if isinstance(exc, subprocess.SubprocessError):
        return subprocess.SubprocessError("repository_pull_failed")
    return RuntimeError("repository_pull_failed")


def _repo_refresh_child(
    repo_dir: Path, arguments: list[str], *, deadline: float,
    cancel_requested: threading.Event | None, control_output: bool = False,
) -> _RepoPullOutcome | None:
    """One original contained request; its Popen alone owns consumed status.

    No backend token is reconstructed and no uncontained fallback exists.
    The supervisor finishes/abandons every admitted handle, including probes.
    """
    evidence = _repo_preparation.get()
    if evidence is None or not evidence.active():
        return None
    from runtime.orchestrator.host_supervisor import AdmissionRequest, LaunchResult
    from runtime.platform.session_backend import LaunchSpec
    outcome = _RepoPullOutcome(probe=evidence.probe)
    command = arguments[1] if arguments[:1] == ["--no-lazy-fetch"] else arguments[0]
    kind = command if command in {"fetch", "merge", "submodule"} else "probe"
    request_id = uuid.uuid4().hex
    from runtime.orchestrator.host_supervisor import CancellationToken
    evidence.cancellation = CancellationToken()
    request = AdmissionRequest(org=evidence.binding["org"], agent_id=evidence.binding["agent"],
        invocation_kind="repository_preparation", logical_id=request_id,
        executor_profile="repository_preparation", cancellation=evidence.cancellation,
        enqueued_at=evidence.enqueued_at)
    evidence.revalidate()
    evidence.emit("intent", command=kind, request_ref=request_id)
    capture_available = os.name == "posix" and hasattr(os, "set_blocking")

    def body(running):
        process, pipe = running.process, None
        try:
            if process is None or running.request_id != request_id or running.root_pid != process.pid:
                raise RuntimeError("repository_preparation_handle_unavailable")
            evidence.children[request_id] = running
            outcome.started = True
            evidence.emit("started", command=kind, request_ref=request_id,
                handle_ref=hashlib.sha256(running.token.encode()).hexdigest(),
                pid=running.root_pid, start_pin=(hashlib.sha256(running.start_identity.encode()).hexdigest()
                    if running.start_identity else None), attempted=True, started=True)
            pipe = process.stdout if control_output else process.stderr
            if not capture_available or pipe is None:
                raise OSError("repository_preparation_capture_unavailable")
            descriptor = pipe.fileno()
            os.set_blocking(descriptor, False)

            def read_once():
                try:
                    chunk = os.read(descriptor, _REPO_PULL_CAPTURE_BYTES)
                except BlockingIOError:
                    return False
                if not chunk:
                    return True
                capacity = _REPO_PULL_CAPTURE_BYTES - len(outcome.stderr)
                outcome.stderr.extend(chunk[:capacity])
                outcome.capture_truncated |= len(chunk) > capacity
                return False

            eof = False
            while process.poll() is None:
                if not evidence.active():
                    outcome.timed_out = time.monotonic() >= deadline
                    # The opaque control performs whole-tree cleanup. Return to
                    # the SAME Popen afterward for its consuming wait.
                    evidence.cancellation.cancel()
                    raise subprocess.TimeoutExpired("repository_preparation", _REPO_REFRESH_SECONDS)
                remaining = max(0.0, deadline - time.monotonic())
                if eof:
                    try:
                        process.wait(timeout=min(remaining, _REPO_PULL_POLL_SECONDS))
                    except subprocess.TimeoutExpired:
                        pass
                elif select.select([descriptor], [], [], min(remaining, _REPO_PULL_POLL_SECONDS))[0]:
                    eof = read_once()
            for _ in range(2):
                if eof:
                    break
                eof = read_once()
            outcome.capture_complete = eof
        except BaseException as exc:
            outcome.fail(exc)
        finally:
            if pipe is not None:
                try:
                    pipe.close()
                except BaseException as exc:
                    outcome.fail(exc)
        # Do not detach a possible child after an evidence/capture error. This
        # request's original opaque control owns teardown; no direct PID kill.
        if process is not None:
            try:
                if process.poll() is None:
                    evidence.cancellation.cancel()
                outcome.returncode = process.wait(timeout=max(0.0, deadline-time.monotonic()))
                outcome.settled = True
            except BaseException as exc:
                outcome.fail(exc)
        cause = None
        if outcome.failure is not None or kind != "probe" and outcome.returncode is not None and outcome.returncode != 0:
            # Expected probe rc1 (missing optional config/non-ancestor) is an
            # observation, never fabricated as the first failure.
            # Fixed fields only: preserve the original observed failure before
            # backend finish/export can fail. No stderr or exception text.
            cause = {"stage": "repository_pull"}
            if outcome.failure is not None:
                cause["exception_class"] = type(outcome.failure).__name__
            if outcome.returncode is not None:
                cause["returncode"] = outcome.returncode
            if evidence.probe is not None:
                cause["refresh_probe"] = evidence.probe
        try:
            if outcome.settled:
                evidence.emit("consumed", command=kind, request_ref=request_id,
                    attempted=True, started=outcome.started, consumed=True,
                    returncode=outcome.returncode, cause=cause,
                    capture_complete=outcome.capture_complete, capture_truncated=outcome.capture_truncated)
        except BaseException as exc:
            outcome.fail(exc)
        return LaunchResult(success=outcome.failure is None and outcome.returncode == 0,
            duration_seconds=0.0, returncode=outcome.returncode, timed_out=outcome.timed_out)

    attempted = False
    def validate():
        if not evidence.active():
            raise RuntimeError("repository_preparation_deadline")
        evidence.revalidate()

    def admitted():
        nonlocal attempted
        validate()
        evidence.emit("attempted", command=kind, request_ref=request_id, attempted=True)
        attempted = True

    def not_admitted():
        evidence.emit("not_admitted", command=kind, request_ref=request_id, complete=True)

    try:
        # The first validator is AFTER host FIFO admission and BEFORE backend
        # prepare. A queued timeout/cancel has no invented creation attempt.
        terminal = evidence.supervisor.run(request,
            launch_spec=LaunchSpec(argv=("git", "--no-optional-locks", *arguments), cwd=str(repo_dir),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE if control_output else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL if control_output else subprocess.PIPE, text=False),
            launch_body=body, timeout=max(0.0, deadline-time.monotonic()),
            grace_seconds=max(0.0, min(1.0, deadline-time.monotonic())),
            pre_launch_validator=admitted, final_prelaunch_validator=validate, allow_retries=False)
        if not attempted:
            not_admitted()
            return None
        receipt = terminal.receipt
        outcome.tree_quiescent = bool(outcome.settled and receipt is not None
            and receipt.backend != "passthrough" and receipt.quiescent is True
            and not receipt.survivors and receipt.cleanup_status.value != "incomplete"
            and terminal.cleanup_error is None and terminal.request is request)
        outcome.receipt_ref = evidence.emit("finished", command=kind, request_ref=request_id,
            attempted=True, started=outcome.started, consumed=outcome.settled,
            returncode=outcome.returncode, complete=outcome.tree_quiescent,
            receipt={"quiescent": receipt.quiescent if receipt is not None else None,
                "survivors": len(receipt.survivors) if receipt is not None else None,
                "cleanup_error": terminal.cleanup_error is not None,
                "cleanup_status": receipt.cleanup_status.value if receipt is not None else None},
            capture_complete=outcome.capture_complete, capture_truncated=outcome.capture_truncated)
    except BaseException as exc:
        from runtime.orchestrator.host_supervisor import AdmissionTimeout
        if isinstance(exc, AdmissionTimeout) and not attempted:
            not_admitted()
            return None
        outcome.fail(exc)
        evidence.uncertain = True
    if not outcome.tree_quiescent:
        evidence.uncertain = True
        if outcome.failure is None:
            outcome.fail(subprocess.SubprocessError("repository_preparation_unresolved"))
    return outcome


def _repo_pull_diagnostics(outcome: _RepoPullOutcome) -> dict[str, str | bool]:
    """Whole bounded stream, opaque operands, fixed advisory wording pairs only."""
    facts: dict[str, str | bool] = {
        "diagnostic_category": "unknown", "diagnostic_rule": "pull-unknown-v1",
        "capture_complete": outcome.capture_complete,
        "capture_truncated": outcome.capture_truncated, "timed_out": outcome.timed_out,
    }
    if (not outcome.capture_complete or outcome.capture_truncated
            or outcome.timed_out or outcome.failure is not None):
        return facts
    try:
        message = outcome.stderr.decode("utf8", errors="strict").replace("\r\n", "\n")
        if not message.strip() or any(c != "\n" and not c.isprintable() for c in message):
            return facts
        # Graphic ASCII tokens exclude quote/backslash; lock also permits spaces.
        remote = r"[\x21-\x26\x28-\x5b\x5d-\x7e]{1,2048}"
        lock = r"[\x20-\x26\x28-\x5b\x5d-\x7e]{1,2048}"
        host = r"(?=[A-Za-z0-9.-]{1,253}$)[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
        patterns = (
            ("lock", "pull-lock-exists-v1", rf"fatal: Unable to create '({lock})': File exists\."),
            ("network", "pull-resolve-host-v1", rf"fatal: unable to access '{remote}': Could not resolve host: {host}"),
            ("auth", "pull-auth-failed-v1", rf"fatal: Authentication failed for '{remote}'"),
            ("other", "pull-non-ff-v1", r"fatal: Not possible to fast-forward, aborting\."),
        )
        matched: set[tuple[str, str]] = set()
        unaccounted = False
        for line in message.split("\n"):
            if not line or re.fullmatch(r"remote: Enumerating objects: [0-9]{1,10}, done\.", line):
                continue
            for category, rule, pattern in patterns:
                match = re.fullmatch(pattern, line)
                if match is not None and (category != "lock" or match[1].endswith(".lock")):
                    matched.add((category, rule))
                    break
            else:
                # Unsafe quoting/escapes cannot be reinterpreted as multiple lines.
                if "'" in line or "\\" in line:
                    return facts
                unaccounted = True
        if len(matched) == 1 and not unaccounted:
            facts["diagnostic_category"], facts["diagnostic_rule"] = next(iter(matched))
        elif not matched:
            facts["diagnostic_category"], facts["diagnostic_rule"] = "other", "pull-unmatched-v1"
    except Exception:
        # No classifier defect may bypass native uncertainty or escape with bytes.
        facts["diagnostic_category"], facts["diagnostic_rule"] = "unknown", "pull-unknown-v1"
        facts["capture_complete"] = False
    return facts


def _repo_pull_failed(
    preparation: TaskInvocation | ThreadPreparation | ProducerPreparation | None,
    name: str, outcome: _RepoPullOutcome, *, preparation_closed: bool = False,
    repository_deadline: float | None = None,
) -> None:
    """Latch uncertainty BEFORE any fallible attribution, including classifier work."""
    evidence_failure: BaseException | None = None
    if preparation is not None:
        try:
            if not preparation_closed:
                preparation.failed_cleanup(repository_deadline=repository_deadline)
        except BaseException as exc:
            evidence_failure = _repo_pull_safe_error(exc)
        from runtime.orchestrator.task_pause import current_invocation
        if preparation is current_invocation():
            try:
                facts = _repo_pull_diagnostics(outcome) if outcome.started else {}
                preparation.record_preparation_cause(
                    "repository_pull", outcome.failure, repo=name,
                    returncode=outcome.returncode, operation="refresh_ff_only",
                    refresh_phase=outcome.refresh_phase, refresh_probe=outcome.probe,
                    preparation_receipt=outcome.receipt_ref, **facts,
                )
            except BaseException as exc:
                if evidence_failure is None or isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                    evidence_failure = _repo_pull_safe_error(exc)
    outcome.stderr.clear()
    if isinstance(evidence_failure, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
        raise evidence_failure from None
    if isinstance(outcome.failure, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
        # An ordinary evidence fault must not turn control into refusal accounting.
        raise outcome.failure from None
    if evidence_failure is not None:
        # Never chain/format evidence exceptions (which can contain private data).
        raise RuntimeError("repository_preparation_evidence_failed") from None
    if outcome.failure is not None and not isinstance(
        outcome.failure, (subprocess.SubprocessError, OSError),
    ):
        raise outcome.failure from None
    if outcome.started and not outcome.settled:
        raise RuntimeError("repository_pull_settlement_unavailable") from None


@dataclass(frozen=True, repr=False)
class _RepoRefreshIdentity:
    # Transient control data only: never format config, transport, refs or paths.
    branch_ref: str
    head: str
    upstream: str
    object_format: str
    config: bytes  # Complete ordered selected scope/origin/key-first-LF-value triples.
    transport: bytes


def _repo_refresh_observe(
    repo_dir: Path, arguments: list[str], *, deadline: float,
    cancel_requested: threading.Event | None, allowed: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[bytes]:
    result = _repo_refresh_git(repo_dir, arguments, deadline=deadline,
                               cancel_requested=cancel_requested)
    if result is None or result.returncode not in allowed:
        raise subprocess.SubprocessError("repository_refresh_probe_unavailable")
    return result


def _repo_refresh_line(data: bytes) -> str:
    value = data.removesuffix(b"\n").decode("utf8", errors="strict")
    if not value or any(c in value for c in "\r\n\0"):
        raise ValueError("repository_refresh_invalid_control")
    return value


def _repo_refresh_oid(value: str, object_format: str) -> str:
    length = {"sha1": 40, "sha256": 64}.get(object_format)
    if length is None or re.fullmatch(r"[0-9a-f]{%d}" % length, value) is None:
        raise ValueError("repository_refresh_invalid_oid")
    return value


# Finite command-dependency inventory, not a universal Git config interpreter.
# Keep entire families, including shadowed/inactive members, in raw order.
_REPO_REFRESH_CONFIG_FAMILIES = (
    "remote", "remotes", "url", "http", "ssh", "credential", "protocol", "promisor",
    "core", "extensions", "safe", "feature", "index", "checkout", "filter", "diff", "fsck",
    "attr", "splitindex", "reftable",
    "fetch", "transfer", "pack", "repack", "gc", "maintenance", "commitgraph", "bundle", "clone",
    "pull", "merge", "rebase", "submodule", "rerere", "apply", "stash", "hook",
    "user", "author", "committer", "commit", "gpg", "i18n",
)
_REPO_REFRESH_BRANCH_KEYS = ("remote", "merge", "rebase", "mergeoptions")


def _repo_refresh_config_selector(branch: str) -> str:
    # POSIX ERE metacharacters only: branch subsection case and bytes are data.
    # re.escape also escapes non-ERE punctuation; do not pass those escapes to Git.
    escaped = "".join("\\" + c if c in r"\.^$*+?{}[]|()" else c for c in branch)
    return (r"^(" + "|".join(_REPO_REFRESH_CONFIG_FAMILIES) + r")\."
            + r"|^branch\." + escaped + r"\.(" + "|".join(_REPO_REFRESH_BRANCH_KEYS) + r")$")


def _repo_refresh_config_values(config: bytes, key: str, *, branch: str) -> list[bytes | None]:
    """Validate the entire selected scope/origin/key-value stream before use."""
    if not config.endswith(b"\0") or len(config) > _REPO_PULL_CAPTURE_BYTES:
        raise subprocess.SubprocessError("repository_refresh_invalid_config")
    fields = config[:-1].split(b"\0")
    if len(fields) % 3:
        raise subprocess.SubprocessError("repository_refresh_invalid_config")
    selector = _repo_refresh_config_selector(branch).encode("utf8")
    values: list[bytes | None] = []
    for offset in range(0, len(fields), 3):
        scope, origin, entry = fields[offset:offset + 3]
        name, separator, value = entry.partition(b"\n")
        if (scope not in {b"system", b"global", b"local", b"worktree", b"command"}
                or not origin
                or re.fullmatch(rb"[a-z0-9-]+(?:\.[^\n]+)?\.[a-z][a-z0-9-]*", name) is None
                or re.match(selector, name) is None):
            raise subprocess.SubprocessError("repository_refresh_invalid_config")
        if name == key.encode("utf8"):
            values.append(value if separator else None)
    return values


def _repo_refresh_identity(
    repo_dir: Path, *, deadline: float, cancel_requested: threading.Event | None,
) -> _RepoRefreshIdentity:
    def observe(arguments: list[str]) -> bytes:
        return _repo_refresh_observe(repo_dir, arguments, deadline=deadline,
                                     cancel_requested=cancel_requested).stdout

    branch_ref = _repo_refresh_line(observe(["symbolic-ref", "--quiet", "HEAD"]))
    if not branch_ref.startswith("refs/heads/"):
        raise ValueError("unsupported_refresh_configuration")
    branch = branch_ref[len("refs/heads/"):]
    object_format = _repo_refresh_line(observe(["rev-parse", "--show-object-format"]))
    head = _repo_refresh_oid(_repo_refresh_line(observe(
        ["rev-parse", "--verify", "--quiet", "HEAD"])), object_format)
    if observe(["cat-file", "-t", head]) != b"commit\n":
        raise ValueError("repository_refresh_commit_unavailable")
    # Git selects BEFORE bounded capture. Keep complete ordered scope/origin/raw
    # values for equality; unrelated branches/custom bloat cannot consume the cap.
    # One observation includes all applicable scopes/includes; no value regexp.
    config = observe(["config", "--includes", "--null", "--show-scope", "--show-origin",
                      "--get-regexp", _repo_refresh_config_selector(branch)])
    for key, expected in (
        ("remote.origin.fetch", b"+refs/heads/*:refs/remotes/origin/*"),
        (f"branch.{branch}.remote", b"origin"),
        (f"branch.{branch}.merge", branch_ref.encode("utf8")),
    ):
        if _repo_refresh_config_values(config, key, branch=branch) != [expected]:
            raise subprocess.SubprocessError("unsupported_refresh_configuration")
    upstream = _repo_refresh_line(observe(
        ["rev-parse", "--symbolic-full-name", "--verify", "@{upstream}"]))
    if upstream != f"refs/remotes/origin/{branch}":
        raise ValueError("unsupported_refresh_configuration")
    _repo_refresh_oid(_repo_refresh_line(observe(
        ["rev-parse", "--verify", "--quiet", "@{upstream}^{commit}"])), object_format)
    transport = observe(["remote", "get-url", "--all", "origin"])
    if not transport.strip():
        raise ValueError("repository_refresh_transport_unavailable")
    return _RepoRefreshIdentity(branch_ref, head, upstream, object_format, config, transport)


def _repo_refresh_advertised(
    repo_dir: Path, identity: _RepoRefreshIdentity, *, deadline: float,
    cancel_requested: threading.Event | None,
) -> str:
    result = _repo_refresh_observe(
        repo_dir, ["ls-remote", "--exit-code", "--refs", "--", "origin", identity.branch_ref],
        deadline=deadline, cancel_requested=cancel_requested,
    )
    line = _repo_refresh_line(result.stdout)
    oid, separator, source = line.partition("\t")
    if not separator or source != identity.branch_ref:
        raise ValueError("repository_refresh_source_unavailable")
    return _repo_refresh_oid(oid, identity.object_format)


def _repo_refresh_admit(
    repo_dir: Path, *, deadline: float, cancel_requested: threading.Event | None,
) -> _RepoRefreshIdentity | None:
    try:
        identity = _repo_refresh_identity(repo_dir, deadline=deadline,
                                           cancel_requested=cancel_requested)
        # U0 is existence only. It must NEVER become the later merge operand.
        _repo_refresh_advertised(repo_dir, identity, deadline=deadline,
                                 cancel_requested=cancel_requested)
        return identity
    except (subprocess.SubprocessError, OSError, UnicodeError, ValueError):
        # No raw control values, exception text, repo paths or remote URLs.
        evidence = _repo_preparation.get()
        if evidence is not None and evidence.uncertain:
            raise RuntimeError("repository_admission_probe_unresolved") from None
        logger.debug("repo refresh skipped: unsupported_or_unavailable_refresh_configuration")
        return None


def _repo_refresh_precheck(
    repo_dir: Path, identity: _RepoRefreshIdentity, *, deadline: float,
    cancel_requested: threading.Event | None,
) -> tuple[list[str], bool]:
    """Pull's early state checks and pull-specific autostash precedence.

    This is admitted-state failure, not a new unsupported-config skip. Only
    Git owns stash/apply and merge options; no rebase or compensation is added.
    """
    def observe(arguments: list[str], allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[bytes]:
        return _repo_refresh_observe(repo_dir, arguments, deadline=deadline,
                                     cancel_requested=cancel_requested, allowed=allowed)

    def boolean(key: str) -> bool | None:
        result = observe(["config", "--null", "--type=bool", "--get-all", key], (0, 1))
        if result.returncode == 1:
            if result.stdout:
                raise subprocess.SubprocessError("repository_refresh_invalid_setting")
            return None
        values = result.stdout.split(b"\0")
        if (len(values) < 2 or values[-1] != b""
                or any(value not in {b"true", b"false"} for value in values[:-1])):
            raise subprocess.SubprocessError("repository_refresh_invalid_setting")
        return values[-2] == b"true"

    branch = identity.branch_ref[len("refs/heads/"):]
    key = f"branch.{branch}.rebase"
    values = _repo_refresh_config_values(identity.config, key, branch=branch)
    if not values:
        key = "pull.rebase"
        values = _repo_refresh_config_values(identity.config, key, branch=branch)
    rebase = False
    if values:
        if values[-1] in {b"merges", b"m", b"interactive", b"i"}:
            rebase = True
        else:
            # Use Git's boolean parser rather than approximate numeric/case rules.
            result = observe(["config", "--type=bool", "--get", key])
            if result.stdout not in {b"true\n", b"false\n"}:
                raise subprocess.SubprocessError("repository_refresh_invalid_setting")
            rebase = result.stdout == b"true\n"
    # pull validates these settings even when a later override makes one unused.
    rebase_autostash = boolean("rebase.autostash") or False
    pull_autostash = boolean("pull.autostash")
    recursive = boolean("submodule.recurse") or False
    autostash = pull_autostash if pull_autostash is not None else (rebase_autostash if rebase else None)
    if observe(["ls-files", "--unmerged", "-z"]).stdout:
        raise subprocess.SubprocessError("repository_refresh_precheck_failed")
    # Pull rejects presence, including an empty/malformed MERGE_HEAD, not merely
    # a successfully parsed object name. --git-path also handles linked worktrees.
    merge_head = Path(_repo_refresh_line(observe(["rev-parse", "--git-path", "MERGE_HEAD"]).stdout))
    if not merge_head.is_absolute():
        merge_head = repo_dir / merge_head
    try:
        merge_head.lstat()
    except FileNotFoundError:
        pass
    else:
        raise subprocess.SubprocessError("repository_refresh_precheck_failed")
    if rebase and not autostash:
        # Pull ignores submodule-only dirtiness here, and does not blanket-reject
        # untracked files. A later merge/update still applies Git's own checks.
        for arguments in (["diff", "--quiet", "--ignore-submodules", "--"],
                          ["diff", "--cached", "--quiet", "--ignore-submodules", "HEAD", "--"]):
            if observe(arguments, (0, 1)).returncode != 0:
                raise subprocess.SubprocessError("repository_refresh_precheck_failed")
    merge_options = [] if autostash is None else ["--autostash" if autostash else "--no-autostash"]
    return merge_options, recursive


def _repo_refresh_pull(
    repo_dir: Path, identity: _RepoRefreshIdentity, *, deadline: float,
    cancel_requested: threading.Event | None,
) -> _RepoPullOutcome | None:
    """Compose supervised children; later no-start cannot erase earlier effects."""
    def active() -> bool:
        return time.monotonic() < deadline and not (cancel_requested is not None and cancel_requested.is_set())

    if not active():
        return None
    phase = "precheck"
    evidence = _repo_preparation.get()
    result: _RepoPullOutcome | None = None
    try:
        merge_options, recursive = _repo_refresh_precheck(
            repo_dir, identity, deadline=deadline, cancel_requested=cancel_requested,
        )
        phase = "fetch"
        result = _repo_refresh_child(repo_dir, ["fetch", "--", "origin"],
                                     deadline=deadline, cancel_requested=cancel_requested)
        if result is None:
            return None  # No mutating creation attempted; ordinary stale skip.
        result.refresh_phase = phase
        if result.failure is not None or result.returncode != 0 or not result.settled:
            return result
        result.stderr.clear()
        phase = "between_stages"
        if evidence is not None:
            evidence.probe = "identity_after_fetch"
        if not active() or _repo_refresh_identity(
            repo_dir, deadline=deadline, cancel_requested=cancel_requested,
        ) != identity:
            raise subprocess.SubprocessError("repository_refresh_changed_or_interrupted")
        # Select U once, AFTER the successful fetch, independently of FETCH_HEAD
        # and tracking contents. No fetch/chase/fallback on unavailable objects.
        if evidence is not None:
            evidence.probe = "source_after_fetch"
        target = _repo_refresh_advertised(repo_dir, identity, deadline=deadline,
                                          cancel_requested=cancel_requested)

        def observe(arguments: list[str], allowed: tuple[int, ...] = (0,)) -> subprocess.CompletedProcess[bytes]:
            return _repo_refresh_observe(repo_dir, arguments, deadline=deadline,
                                         cancel_requested=cancel_requested, allowed=allowed)

        if evidence is not None:
            evidence.probe = "target_type"
        if observe(["cat-file", "-t", target]).stdout != b"commit\n":
            raise subprocess.SubprocessError("repository_refresh_commit_unavailable")
        if evidence is not None:
            evidence.probe = "ancestry_forward"
        behind = observe(["merge-base", "--is-ancestor", identity.head, target], (0, 1)).returncode == 0
        if evidence is not None:
            evidence.probe = "ancestry_reverse"
        ahead = observe(["merge-base", "--is-ancestor", target, identity.head], (0, 1)).returncode == 0
        expected = target if behind else identity.head
        if evidence is not None:
            evidence.probe = None
        if _repo_refresh_identity(repo_dir, deadline=deadline, cancel_requested=cancel_requested) != identity:
            raise subprocess.SubprocessError("repository_refresh_changed")
        phase = "merge"
        result = _repo_refresh_child(repo_dir, ["merge", "--ff-only", *merge_options, target],
                                     deadline=deadline, cancel_requested=cancel_requested)
        if result is None:
            phase = "between_stages"
            raise subprocess.SubprocessError("repository_refresh_incomplete")
        result.refresh_phase = phase
        if result.failure is not None or result.returncode != 0 or not result.settled:
            return result
        result.stderr.clear()
        phase = "between_stages"
        if evidence is not None:
            evidence.probe = "identity_after_fetch"
        current_identity = _repo_refresh_identity(repo_dir, deadline=deadline, cancel_requested=cancel_requested)
        from dataclasses import replace
        if current_identity != replace(identity, head=expected):
            raise subprocess.SubprocessError("repository_refresh_changed")
        if recursive:
            if evidence is not None:
                evidence.probe = None
            phase = "submodule_update"
            result = _repo_refresh_child(repo_dir, ["submodule", "update", "--recursive", "--checkout"],
                                         deadline=deadline, cancel_requested=cancel_requested)
            if result is None:
                phase = "between_stages"
                raise subprocess.SubprocessError("repository_refresh_incomplete")
            result.refresh_phase = phase
            if result.failure is not None or result.returncode != 0 or not result.settled:
                return result
            result.stderr.clear()
        phase = "between_stages"
        if evidence is not None:
            evidence.probe = "identity_after_fetch"
        if _repo_refresh_identity(repo_dir, deadline=deadline, cancel_requested=cancel_requested) != replace(identity, head=expected):
            raise subprocess.SubprocessError("repository_refresh_changed")
        if evidence is not None:
            evidence.probe = "final_branch"
        final_branch = _repo_refresh_line(observe(["symbolic-ref", "--quiet", "HEAD"]).stdout)
        if evidence is not None:
            evidence.probe = "final_head"
        final_head = _repo_refresh_line(observe(["rev-parse", "--verify", "--quiet", "HEAD"]).stdout)
        if (not active() or not (behind or ahead) or final_branch != identity.branch_ref
                or final_head != expected):
            raise subprocess.SubprocessError("repository_refresh_final_state_unavailable")
        return result
    except BaseException as exc:
        # Discard successful preceding-child diagnostics and its rc. No fabricated
        # mutating-child exit for a precheck, probe or between-stage control gap.
        if result is not None:
            result.stderr.clear()
        failure = _repo_pull_safe_error(exc)
    return _RepoPullOutcome(refresh_phase=phase, failure=failure,
        probe=evidence.probe if evidence is not None else None,
        receipt_ref=evidence.last_receipt if evidence is not None else None)


def _repo_refresh_batch(workspace: Path, preparation: Any, supervisor: Any, deadline: float,
        cancel_requested: threading.Event, work: list[dict]) -> dict[str, bool]:
    from runtime.platform.session_backend import Capability, CapabilityLevel
    try:
        names = sorted(PersistentWorkspaceSetup.detect_repo_names(workspace))
    except OSError:
        return {}
    results = dict.fromkeys(names, False)
    # Never substitute another supervisor or an uncontained Popen. Missing
    # positive host capability is an affirmative pre-child stale refusal.
    if preparation is None or supervisor is None:
        return results
    report = supervisor.probe()
    if not report.healthy or report.level(Capability.KILLS_TREE_GUARANTEED) != CapabilityLevel.GUARANTEED:
        logger.debug("repo refresh skipped: containment_unavailable")
        return results
    # Discover without holding another repository, then acquire by physical
    # identity in every batch (names can enumerate the same resources in reverse).
    # A duplicate remains false for its OWN name; it has no child/obligation.
    candidates, physical = [], set()
    for name in names:
        if time.monotonic() >= deadline or cancel_requested.is_set():
            break
        repo_dir = workspace / "repos" / name
        try:
            repository = _repo_refresh_repository(repo_dir)
        except (OSError, ValueError):
            continue
        key = (repository["device"], repository["inode"])
        if key in physical:
            logger.debug("repo refresh skipped: duplicate_physical_repository")
            continue
        physical.add(key)
        candidates.append((key, name, repo_dir, repository))
    for _, name, repo_dir, repository in sorted(candidates):
        if time.monotonic() >= deadline or cancel_requested.is_set():
            break
        lease = _repo_refresh_lease(repo_dir, deadline=deadline, cancel_requested=cancel_requested,
            expected_repository=repository)
        try:
            repository, validate = lease.__enter__()
        except (OSError, ValueError, TimeoutError):
            continue
        record = {"name": name, "lease": lease, "evidence": None, "outcome": None, "deadline": deadline}
        work.append(record)
        try:
            evidence = _RepoPreparationEvidence(preparation, supervisor, repository, validate, deadline, cancel_requested)
            record["evidence"] = evidence
            evidence.repo_dir = repo_dir
            token = _repo_preparation.set(evidence)
            try:
                evidence.revalidate()
                identity = _repo_refresh_admit(repo_dir, deadline=deadline, cancel_requested=cancel_requested)
                if identity is not None:
                    record["outcome"] = _repo_refresh_pull(repo_dir, identity, deadline=deadline,
                        cancel_requested=cancel_requested)
            finally:
                _repo_preparation.reset(token)
            if record["outcome"] is not None and isinstance(record["outcome"].failure,
                    (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                cancel_requested.set()
        except BaseException as exc:
            if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                cancel_requested.set()
            evidence = record["evidence"]
            if (not isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit))
                    and (evidence is None or not evidence.opening_attempted)):
                record["outcome"] = None
                logger.debug("repo refresh skipped: preparation_capability_unavailable")
            else:
                record["outcome"] = _RepoPullOutcome(failure=_repo_pull_safe_error(exc))
                if evidence is not None:
                    evidence.uncertain = True
    return results


def _repo_refresh_complete(preparation: Any, results: dict[str, bool], work: list[dict]) -> dict[str, bool]:
    """The original caller owns this tail AFTER actual worker return/join."""
    primary = None
    for record in work:
        evidence, outcome = record["evidence"], record["outcome"]
        try:
            if evidence is not None:
                evidence.returned()
                if not evidence.settle():
                    if outcome is None:
                        outcome = _RepoPullOutcome(failure=RuntimeError("repository_preparation_unresolved"))
                    evidence.uncertain = True
                else:
                    results[record["name"]] = bool(outcome is not None and outcome.returncode == 0
                        and outcome.failure is None and outcome.tree_quiescent)
            if outcome is not None and not results[record["name"]]:
                _repo_pull_failed(preparation, record["name"], outcome,
                    preparation_closed=evidence is not None and evidence.closed,
                    repository_deadline=record["deadline"])
            elif outcome is not None:
                outcome.stderr.clear()
        except BaseException as exc:
            # Failed closure/export cannot turn a completed Popen into a
            # settled preparation; latch before preserving the first safe cause.
            safe_error = _repo_pull_safe_error(exc)
            try:
                if evidence is not None:
                    evidence.uncertain = True
                preparation.failed_cleanup(repository_deadline=record["deadline"])
            except BaseException:
                safe_error.add_note("repository preparation failure attribution unavailable; ownership remains unknown")
            if outcome is not None:
                try:
                    _repo_pull_failed(preparation, record["name"], outcome,
                        preparation_closed=evidence is not None and evidence.closed,
                        repository_deadline=record["deadline"])
                except BaseException:
                    safe_error.add_note("repository preparation cause attribution failed; original failure retained")
            if primary is None:
                primary = safe_error
        finally:
            record["lease"].__exit__(None, None, None)
    if primary is not None:
        raise primary from None
    return results


def refresh_workspace_repos(
    workspace: Path, *, preparation=None, host_supervisor=None,
    materialization=None,
    _deadline: float | None = None, _cancel_requested: threading.Event | None = None,
) -> dict[str, bool]:
    """One original30s cooperative batch before outer executor admission.

    All Git children/probes use configured native host containment. New
    preparation-specific closure never resets a prior unknown owner. The
    original caller closes evidence only after the worker function returned.
    Manual Git/hooks/other hosts and arbitrary same-UID writes do not cooperate.
    """
    deadline = time.monotonic()+_REPO_REFRESH_SECONDS if _deadline is None else _deadline
    cancel = _cancel_requested if _cancel_requested is not None else threading.Event()
    work, results = [], {}
    if preparation is not None and materialization is not None:
        _repo_retain_materialization(preparation, workspace, *materialization)
    if preparation is not None:
        preparation._repository_worker_active = True
        preparation._repository_completion_active = True
    try:
        results = _repo_refresh_batch(workspace, preparation, host_supervisor, deadline, cancel, work)
    finally:
        if preparation is not None:
            preparation._repository_worker_active = False
        try:
            results = _repo_refresh_complete(preparation, results, work)
        finally:
            if preparation is not None:
                preparation._repository_completion_active = False
    return results


async def refresh_workspace_repos_async(workspace: Path, *, preparation=None, host_supervisor=None, materialization=None) -> dict[str, bool]:
    """Keep the real native owner/OrgState alive through shielded worker join."""
    cancel = threading.Event()
    deadline = time.monotonic()+_REPO_REFRESH_SECONDS
    context = copy_context()
    work = []
    if preparation is not None and materialization is not None:
        _repo_retain_materialization(preparation, workspace, *materialization)
    if preparation is not None:
        preparation._repository_worker_active = True
        preparation._repository_completion_active = True
    def refresh():
        return _repo_refresh_batch(workspace, preparation, host_supervisor, deadline, cancel, work)
    worker = asyncio.get_running_loop().run_in_executor(None, context.run, refresh)
    cancellation, failure, results = None, None, {}
    while True:
        try:
            results = await asyncio.shield(worker)
            break
        except asyncio.CancelledError as exc:
            cancellation = cancellation or exc
            cancel.set()
            if worker.done():
                break
        except BaseException as exc:
            failure = _repo_pull_safe_error(exc)
            break
    if preparation is not None:
        preparation._repository_worker_active = False
    # The batch has genuinely returned; keep its leases and the caller's org
    # lifetime through a joined off-loop completion tail. Binding/root/DB waits
    # use the SAME original deadline, never the serving event loop.
    tail = asyncio.get_running_loop().run_in_executor(None, context.run,
        _repo_refresh_complete, preparation, results, work)
    try:
        while True:
            try:
                results = await asyncio.shield(tail)
                break
            except asyncio.CancelledError as exc:
                cancellation = cancellation or exc
                cancel.set()
                if tail.done():
                    # Retrieve a completed result/error despite waiter cancellation.
                    results = tail.result()
                    break
    except BaseException as exc:
        failure = failure or _repo_pull_safe_error(exc)
    finally:
        if preparation is not None:
            preparation._repository_completion_active = False
    if cancellation is not None:
        # Escaping an outer effects interval retains its sticky uncertainty;
        # actual worker return is not proof that interval completed normally.
        if preparation is not None:
            preparation.failed_cleanup()
        raise cancellation from None
    if failure is not None:
        raise failure from None
    return results


def format_repo_refresh_note(results: dict[str, bool]) -> str:
    """Render the session-visible status for a fail-open repo refresh."""
    if not results:
        return "Repository freshness at session start: no cloned repositories were refreshed."
    stale_repos = sorted(name for name, fresh in results.items() if not fresh)
    if stale_repos:
        return (
            "Repository freshness at session start: "
            f"{', '.join(stale_repos)} did not fast-forward cleanly; their code may be stale."
        )
    return "Repository freshness at session start: all cloned repositories fast-forwarded cleanly."


# ── Canonical instruction pair (THR-262 Slice B / founder seq59) ──────
# Every generated ordinary agent workspace converges
# non-destructively on one regular ``AGENTS.md`` plus a raw relative
# ``CLAUDE.md -> AGENTS.md`` symlink. This module owns the shared
# classifier/writer used by the built-in workspace adapters and the
# read-only startup readiness gate. It never writes through a symlink and
# never mutates an external target.

CANONICAL_AGENTS_NAME = "AGENTS.md"
CANONICAL_CLAUDE_NAME = "CLAUDE.md"
CANONICAL_CLAUDE_LINK_TARGET = "AGENTS.md"


class InstructionPairOperation(StrEnum):
    """Closed caller-visible classifications for canonical-pair failures."""

    INPUT_INSPECTION = "input inspection failed"
    PRESERVATION_COPY = "preservation copy failed"
    CANONICAL_WRITE = "canonical write failed"
    LINK_CREATION = "link creation failed"


class InstructionPairCompensationOperation(StrEnum):
    """Closed caller-visible classifications for pair compensation failures."""

    RESTORE_PATH = "Failed to restore instruction path"


@dataclass(frozen=True)
class InstructionPairCompensationFailure:
    """Structured pair rollback failure with raw detail reserved for logs."""

    operation: InstructionPairCompensationOperation
    owned_relative_path: str
    cause: Exception

    def caller_diagnostic(self) -> str:
        """Return only stable classification and an owned relative name."""
        return f"{self.operation.value} {self.owned_relative_path}"

    def raw_diagnostic(self) -> str:
        """Return full operator detail, including the original exception."""
        return f"{self.caller_diagnostic()}: {self.cause}"


class InstructionPairConflict(RuntimeError):
    """Raised when the canonical instruction pair cannot be converged safely.

    The pair-wide preservation barrier guarantees that when this is raised
    after preflight, both instruction paths are byte/type/raw-link identical
    to their pre-state (any completed preservation copy is retained and only
    an owned incomplete copy is removed).
    """

    def __init__(
        self,
        path: Path,
        reason: str,
        *,
        operation: InstructionPairOperation,
        compensation_failures: list[InstructionPairCompensationFailure] | None = None,
    ) -> None:
        self.path = Path(path)
        self.reason = reason
        self.operation = operation
        self.compensation_failures = tuple(compensation_failures or ())
        super().__init__(self.caller_diagnostic())

    def caller_diagnostic(self, *, max_compensation_failures: int = 4) -> str:
        """Return bounded caller-safe context without paths or exception text."""
        diagnostic = (
            f"instruction pair conflict at {self.path.name}: "
            f"{self.operation.value}"
        )
        if not self.compensation_failures:
            return diagnostic

        visible = self.compensation_failures[:max_compensation_failures]
        if len(self.compensation_failures) > max_compensation_failures:
            visible = self.compensation_failures[:max_compensation_failures - 1]
        nested = [failure.caller_diagnostic() for failure in visible]
        omitted = len(self.compensation_failures) - len(visible)
        if omitted > 0:
            nested.append(f"... and {omitted} more compensation failure(s)")
        return f"{diagnostic}; nested compensation incomplete: {'; '.join(nested)}"

    def raw_diagnostic(self) -> str:
        """Return complete operator detail for daemon logs only."""
        diagnostic = (
            f"instruction pair conflict at {self.path}: "
            f"{self.operation.value}: {self.reason}"
        )
        if self.compensation_failures:
            nested = "; ".join(
                failure.raw_diagnostic()
                for failure in self.compensation_failures
            )
            diagnostic = f"{diagnostic}; pair rollback failed: {nested}"
        return diagnostic


@dataclass(frozen=True)
class _InstructionPathState:
    kind: str  # absent | regular | symlink | unsupported
    data: bytes | None = None
    mode: int | None = None
    uid: int | None = None
    raw_link: str | None = None
    detail: str | None = None


def _classify_instruction_path(path: Path) -> _InstructionPathState:
    """Classify one instruction path with ``lstat`` and never follow links."""
    import stat as _stat

    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return _InstructionPathState(kind="absent")
    except OSError as exc:
        return _InstructionPathState(kind="unsupported", detail=f"lstat failed: {exc}")
    if _stat.S_ISLNK(st.st_mode):
        try:
            raw = os.readlink(path)
        except OSError as exc:
            return _InstructionPathState(
                kind="unsupported", detail=f"readlink failed: {exc}"
            )
        return _InstructionPathState(
            kind="symlink",
            mode=st.st_mode & 0o7777,
            uid=st.st_uid,
            raw_link=raw,
        )
    if _stat.S_ISREG(st.st_mode):
        try:
            data = path.read_bytes()
        except OSError as exc:
            return _InstructionPathState(kind="unsupported", detail=f"read failed: {exc}")
        return _InstructionPathState(
            kind="regular",
            data=data,
            mode=st.st_mode & 0o7777,
            uid=st.st_uid,
        )
    return _InstructionPathState(kind="unsupported", detail="not a regular file")


def _timestamp_suffix() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _reserve_sibling(directory: Path, stem: str) -> Path:
    """Reserve a collision-safe owned name in *directory* via ``O_EXCL``."""
    candidate = directory / stem
    counter = 0
    while True:
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            counter += 1
            candidate = directory / f"{stem}-{counter}"
            continue
        os.close(fd)
        return candidate


def _write_preservation_copy(path: Path, state: _InstructionPathState) -> Path:
    """Write and verify one collision-safe preservation copy.

    Returns the retained backup path. On failure the incomplete owned copy is
    removed and ``OSError`` is raised; a pre-existing collided/foreign path is
    never removed because reservation used ``O_EXCL``.
    """
    backup = _reserve_sibling(
        path.parent, f"{path.name}.happyranch-{_timestamp_suffix()}.bak"
    )
    assert state.data is not None
    try:
        with open(backup, "wb") as fh:
            fh.write(state.data)
        os.chmod(backup, state.mode or 0o644)
        if backup.read_bytes() != state.data:
            raise OSError("preservation copy byte verification failed")
        if (backup.stat().st_mode & 0o7777) != ((state.mode or 0o644) & 0o7777):
            raise OSError("preservation copy mode verification failed")
    except OSError:
        try:
            backup.unlink()
        except OSError:
            pass
        raise
    return backup


def _atomic_write_regular(path: Path, data: bytes, mode: int = 0o644) -> None:
    """Write *data* to a same-directory temp then atomically ``os.replace``."""
    temp = _reserve_sibling(
        path.parent, f"{path.name}.happyranch-{_timestamp_suffix()}.tmp"
    )
    try:
        with open(temp, "wb") as fh:
            fh.write(data)
        os.chmod(temp, mode)
        os.replace(temp, path)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        raise


def _restore_instruction_path(path: Path, state: _InstructionPathState) -> None:
    """Restore one snapshotted instruction path without following links.

    Restoration uses a same-directory owned sibling plus ``os.replace`` for
    regular files and symlinks. An absent pre-state removes only the live path
    written by this attempt. Any unexpected non-regular live object fails
    closed; external link targets are never opened or mutated.
    """
    current = _classify_instruction_path(path)
    if current == state:
        return

    if state.kind == "absent":
        if current.kind == "absent":
            return
        if current.kind not in {"regular", "symlink"}:
            raise OSError(
                f"refusing to remove unexpected {current.kind} at {path}"
            )
        path.unlink()
    elif state.kind == "regular":
        assert state.data is not None
        staged = _reserve_sibling(
            path.parent, f"{path.name}.happyranch-{_timestamp_suffix()}.restore.tmp"
        )
        try:
            with open(staged, "wb") as fh:
                fh.write(state.data)
            os.chmod(staged, state.mode if state.mode is not None else 0o644)
            if state.uid is not None and state.uid != os.getuid():
                os.chown(staged, state.uid, -1)
            os.replace(staged, path)
        except Exception:
            try:
                staged.unlink()
            except OSError:
                pass
            raise
    elif state.kind == "symlink":
        assert state.raw_link is not None
        staged: Path | None = None
        for _ in range(64):
            candidate = path.parent / (
                f"{path.name}.happyranch-{_timestamp_suffix()}.restore.lnk"
            )
            try:
                os.symlink(state.raw_link, candidate)
            except FileExistsError:
                continue
            staged = candidate
            break
        if staged is None:
            raise OSError("could not reserve an owned instruction-link restore name")
        try:
            if state.uid is not None and state.uid != os.getuid():
                os.lchown(staged, state.uid, -1)
            os.replace(staged, path)
        except Exception:
            try:
                staged.unlink()
            except OSError:
                pass
            raise
    else:  # unsupported input is rejected before the preservation barrier
        raise OSError(f"cannot restore unsupported instruction state at {path}")

    restored = _classify_instruction_path(path)
    if restored != state:
        raise OSError(
            f"instruction-path restore verification failed at {path}: "
            f"expected {state!r}, got {restored!r}"
        )


def _restore_instruction_pair(
    agents: Path,
    agents_state: _InstructionPathState,
    claude: Path,
    claude_state: _InstructionPathState,
) -> list[InstructionPairCompensationFailure]:
    """Best-effort exact compensation for a caught post-barrier failure."""
    errors: list[InstructionPairCompensationFailure] = []
    # Restore CLAUDE.md first so a captured reverse AGENTS.md -> CLAUDE.md
    # never temporarily resolves through the new canonical link.
    for path, state in ((claude, claude_state), (agents, agents_state)):
        try:
            _restore_instruction_path(path, state)
        except Exception as exc:
            errors.append(
                InstructionPairCompensationFailure(
                    InstructionPairCompensationOperation.RESTORE_PATH,
                    path.name,
                    exc,
                )
            )
    return errors


def _claude_link_is_canonical(workspace: Path, state: _InstructionPathState) -> bool:
    """True when *state* is exactly raw relative ``CLAUDE.md -> AGENTS.md``.

    The raw link text must be exactly ``AGENTS.md`` and its resolution must
    equal a regular same-directory ``AGENTS.md``.
    """
    import stat as _stat

    if state.kind != "symlink" or state.raw_link != CANONICAL_CLAUDE_LINK_TARGET:
        return False
    agents = workspace / CANONICAL_AGENTS_NAME
    try:
        if not _stat.S_ISREG(os.lstat(agents).st_mode):
            return False
        return os.path.realpath(workspace / CANONICAL_CLAUDE_NAME) == os.path.realpath(
            agents
        )
    except OSError:
        return False


def instruction_pair_refusal(workspace: Path) -> str | None:
    """Return the refusal reason for a non-canonical pair, else ``None``.

    Read-only: classifies both paths with ``lstat`` and never follows,
    creates, repairs or mutates anything. Startup uses this before launch.
    """
    import stat as _stat

    agents = workspace / CANONICAL_AGENTS_NAME
    claude = workspace / CANONICAL_CLAUDE_NAME
    try:
        agents_st = os.lstat(agents)
    except FileNotFoundError:
        return f"{CANONICAL_AGENTS_NAME} is missing"
    except OSError as exc:
        return f"{CANONICAL_AGENTS_NAME} cannot be inspected: {exc}"
    if not _stat.S_ISREG(agents_st.st_mode):
        return f"{CANONICAL_AGENTS_NAME} is not a regular file"
    try:
        claude_st = os.lstat(claude)
    except FileNotFoundError:
        return f"{CANONICAL_CLAUDE_NAME} is missing"
    except OSError as exc:
        return f"{CANONICAL_CLAUDE_NAME} cannot be inspected: {exc}"
    if not _stat.S_ISLNK(claude_st.st_mode):
        return f"{CANONICAL_CLAUDE_NAME} is not a symlink to {CANONICAL_AGENTS_NAME}"
    try:
        raw = os.readlink(claude)
    except OSError as exc:
        return f"{CANONICAL_CLAUDE_NAME} link text cannot be read: {exc}"
    if raw != CANONICAL_CLAUDE_LINK_TARGET:
        return (
            f"{CANONICAL_CLAUDE_NAME} link text is {raw!r}, expected "
            f"{CANONICAL_CLAUDE_LINK_TARGET!r}"
        )
    try:
        if os.path.realpath(claude) != os.path.realpath(agents):
            return f"{CANONICAL_CLAUDE_NAME} does not resolve to {CANONICAL_AGENTS_NAME}"
    except OSError as exc:
        return f"{CANONICAL_CLAUDE_NAME} cannot be resolved: {exc}"
    return None


def canonical_instruction_pair_ok(workspace: Path) -> bool:
    """True when the workspace holds the accepted canonical instruction pair."""
    return instruction_pair_refusal(workspace) is None


def write_canonical_instruction_pair(workspace: Path, content: str) -> None:
    """Converge *workspace* on the canonical instruction pair (non-destructive).

    Preflight classifies both paths without following links. Every required
    preservation copy for both paths is written and verified before either
    path is mutated. ``AGENTS.md`` becomes the regular canonical file holding
    *content*; ``CLAUDE.md`` becomes the raw relative link ``AGENTS.md``.
    Raises :class:`InstructionPairConflict` on unreadable/unsupported input or
    a preservation-copy failure, leaving both paths unchanged.
    """
    workspace.mkdir(parents=True, exist_ok=True)
    data = content.encode("utf-8")
    agents = workspace / CANONICAL_AGENTS_NAME
    claude = workspace / CANONICAL_CLAUDE_NAME
    agents_state = _classify_instruction_path(agents)
    claude_state = _classify_instruction_path(claude)

    if agents_state.kind == "unsupported":
        raise InstructionPairConflict(
            agents,
            agents_state.detail or "unsupported input",
            operation=InstructionPairOperation.INPUT_INSPECTION,
        )
    if claude_state.kind == "unsupported":
        raise InstructionPairConflict(
            claude,
            claude_state.detail or "unsupported input",
            operation=InstructionPairOperation.INPUT_INSPECTION,
        )

    # ── Preservation barrier: ALL required copies for BOTH paths first ──
    if agents_state.kind == "regular" and agents_state.data != data:
        try:
            _write_preservation_copy(agents, agents_state)
        except OSError as exc:
            raise InstructionPairConflict(
                agents,
                str(exc),
                operation=InstructionPairOperation.PRESERVATION_COPY,
            )
    if claude_state.kind == "regular" and claude_state.data != data:
        try:
            _write_preservation_copy(claude, claude_state)
        except OSError as exc:
            # A completed AGENTS.md copy (if any) is retained and identified;
            # only an owned incomplete CLAUDE.md copy is removed.
            raise InstructionPairConflict(
                claude,
                str(exc),
                operation=InstructionPairOperation.PRESERVATION_COPY,
            )

    # ── Live pair transaction: compensate BOTH paths on caught failure ──
    failure_path = agents
    failure_operation = InstructionPairOperation.CANONICAL_WRITE
    try:
        # AGENTS.md: regular canonical file.
        if agents_state.kind == "absent":
            _atomic_write_regular(agents, data)
        elif agents_state.kind == "regular":
            if agents_state.data != data:
                _atomic_write_regular(agents, data, agents_state.mode or 0o644)
        else:  # symlink — atomic replace, never write through it
            _atomic_write_regular(agents, data)

        # CLAUDE.md: raw relative link to AGENTS.md.
        failure_path = claude
        failure_operation = InstructionPairOperation.LINK_CREATION
        if not _claude_link_is_canonical(workspace, claude_state):
            _replace_with_canonical_claude_link(claude)
    except Exception as exc:
        rollback_errors = _restore_instruction_pair(
            agents, agents_state, claude, claude_state,
        )
        raise InstructionPairConflict(
            failure_path,
            str(exc),
            operation=failure_operation,
            compensation_failures=rollback_errors,
        ) from exc


def _stage_canonical_claude_link(workspace: Path) -> Path:
    """Create an owned sibling raw relative ``CLAUDE.md -> AGENTS.md`` link.

    Returns the staging path. The staging name is reserved collision-safely via
    ``os.symlink`` (``FileExistsError`` retries); the caller owns and must
    clean up the returned sibling if it is not moved into place.
    """
    for _ in range(64):
        candidate = workspace / (
            f"{CANONICAL_CLAUDE_NAME}.happyranch-{_timestamp_suffix()}.lnk"
        )
        try:
            os.symlink(CANONICAL_CLAUDE_LINK_TARGET, candidate)
        except FileExistsError:
            continue
        return candidate
    raise OSError("could not reserve an owned instruction-link staging name")


def _replace_with_canonical_claude_link(claude: Path) -> None:
    """Atomically replace *claude* with the canonical raw relative link.

    The new link is fully created under an owned sibling first and only then
    ``os.replace``d into place (``rename(2)``), so no unlink-before-success
    window exists: a failed creation or replacement leaves the pre-existing
    path — regular file, foreign link or absent — exactly as it was. On any
    ``OSError`` the owned staging sibling is removed and the error propagates.
    """
    staged: Path | None = None
    try:
        staged = _stage_canonical_claude_link(claude.parent)
        os.replace(staged, claude)
    except OSError:
        if staged is not None:
            try:
                staged.unlink()
            except OSError:
                pass
        raise


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

    def write_claude_md(self, workspace: Path, agent_name: str, system_prompt: str,
                     repo_names: list[str] | None = None) -> None:
        write_canonical_instruction_pair(workspace, self.render_claude_md(
            workspace, agent_name, system_prompt, repo_names=repo_names,
        ))

    def render_claude_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        repo_names: list[str] | None = None,
    ) -> str:
        """Write the canonical instruction pair with system prompt and pointers.

        The active-executor content is written to the regular canonical
        ``AGENTS.md``; ``CLAUDE.md`` becomes the raw relative link
        ``AGENTS.md``. ``repo_names`` is accepted for API compatibility but is
        not listed inline — the canonical file points at the agent's
        authoritative ``org/agents/<name>.md`` frontmatter (``AgentDef.repos``)
        so the repo list doesn't drift between the two files.
        """
        _assert_no_reserved_headers_in_body(agent_name, system_prompt)
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
        return "\n".join(sections)

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
            f"See the `repos` list in your authoritative `org/agents/{agent_name}.md` "
            "frontmatter (`AgentDef.repos`) — bootstrap paths (enrollment/init/"
            "manage-repo) provision missing clones, while session spawn only "
            "fast-forward-pulls existing",
            repo_refresh_note + "\n",
            *_memory_bootstrap_section(workspace),
            "## Knowledge Base (shared across agents)\n",
            "Path: `<runtime>/kb/`. Read: everyone. Write: any agent (via `--from-file`).",
            "Delete: any team manager (audited); founder via `--as-founder`.",
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
            *_skills_directory_readonly_section(skills_dir, agent_name),
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

    def write_agents_md(self, workspace: Path, agent_name: str, system_prompt: str,
                     repo_names: list[str] | None = None) -> None:
        write_canonical_instruction_pair(workspace, self.render_agents_md(
            workspace, agent_name, system_prompt, repo_names=repo_names,
        ))

    def render_agents_md(
        self,
        workspace: Path,
        agent_name: str,
        system_prompt: str,
        repo_names: list[str] | None = None,
    ) -> str:
        """Write AGENTS.md to workspace with system prompt and context pointers.

        Codex CLI ≥0.125 discovers skills by walking ``.agents/skills/`` from
        the working directory up to the repo root. Canonical materialization
        publishes relative skill links under BOTH ``.claude/skills`` and
        ``.agents/skills``. AGENTS.md points at the **start-task** skill for
        usage instructions; request models and transition handlers enforce
        the completion contract.
        """
        _assert_no_reserved_headers_in_body(agent_name, system_prompt)
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
        return "\n".join(sections)

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


def plan_workspace_skills(workspace, settings, *, slug, provider, agent_name, team, skills_root, org_root, db):
    """Read-only manifest using the actual native all-context selectors.

    Does not instantiate a store/materializer, build a package, emit an event,
    repair a link, create scratch or modify memory. Artifact failure refuses
    the preview; the mutating builder retains its ordinary caught-error policy.
    """
    import hashlib
    from runtime.infrastructure.artifact_store import ArtifactStore
    from runtime.orchestrator._paths import OrgPaths
    specs = []
    src_root = _resolve_skills_src(settings)
    contracts = select_system_contracts(workspace)
    _preflight_system_contract_sources(set(contracts), src_root, workspace=workspace, provider=provider)
    for contract_id in sorted(contracts):
        digest = _compute_dir_hash(src_root / contract_id)
        specs.append({"slug": contract_id, "version": "system", "content_hash": digest})
    for exposed in select_release_skills(skills_root, settings, slug=slug,
            team=team, agent_name=agent_name, org_root=org_root):
        skill = exposed.skill
        source = (org_root / "skills" if skill.source == "user_authored" else skills_root) / skill.slug
        if not source.is_dir():
            raise ValueError("team_move_selected_skill_source_missing")
        specs.append({"slug": skill.slug, "version": skill.version or "0", "content_hash": _compute_dir_hash(source)})
    with db._lock:
        selected = [dict(row) for row in select_custom_skill_rows(db, slug=slug, agent_name=agent_name, team=team)]
    artifacts = ArtifactStore.open_existing(OrgPaths(org_root).artifacts_dir) if selected else None
    for row in selected:
        if not row["content_artifact_key"]:
            raise ValueError("team_move_custom_skill_artifact_missing")
        content = artifacts.read(row["content_artifact_key"])
        if hashlib.sha256(content).hexdigest() != row["content_hash"]:
            raise ValueError("team_move_custom_skill_artifact_changed")
        tree_hash = hashlib.sha256(b"SKILL.md\x00" + content + b"\x00").hexdigest()
        specs.append({"slug": row["slug"], "version": str(row["version_id"]),
            "content_hash": row["content_hash"], "tree_hash": tree_hash})
    if len({s["slug"] for s in specs}) != len(specs):
        raise ValueError("team_move_skill_slug_collision")
    return specs
