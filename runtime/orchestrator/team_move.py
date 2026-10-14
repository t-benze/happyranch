"""Read-only online move seam; producer/writer integration is NOT present in B.

One coordinator belongs to one org's DB owner. C1-C3 must bind the SAME gate to
all producers and writers before D/E enable operation admission. This projection
is advisory until that closure exists. It never claims work or changes a hold.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal

from runtime.infrastructure.team_move_store import MoveRequest, Phase, TeamMoveStore, _decode

CAPABILITY_VERSION = 1
APPLY_AVAILABLE = False
NOT_INTEGRATED = ("task_host", "thread_delivery", "other_producers_writers",
                  "canonical_context_publication_recovery", "startup_launcher_downgrade")

# Required D completion readback; labels are a contract, never Boolean proofs.
COMPLETION_REQUIREMENTS = (
    "three_exact_canonical_after_images_and_coherent_membership",
    "two_operation_scoped_native_reset_audits_or_validated_zero_row_steps",
    "two_original_workspace_native_context_and_all_context_skill_integrity_receipts",
    "same_owned_publisher_journal_cache_installed_and_matching_ready_pointer_file_cache_digest",
    "current_canonical_profile_dependency_skill_permission_snapshot",
    "settled_producer_callback_host_remote_cleanup_and_writer_ownership",
)
STARTUP_REQUIREMENTS = (
    "discover_validate_operation_before_generic_compatibility_writes_and_roster_validation",
    "recover_exact_owned_before_after_prefix_before_authority_publication",
    "install_durable_affected_hold_before_any_task_thread_job_schedule_recovery",
    "unknown_layout_record_owner_or_cleanup_keeps_org_diagnostic_only",
    "all_supported_runtime_attach_switch_launcher_paths_refuse_incompatible_reader",
)


@dataclass(frozen=True)
class PermanentAgentIdentity:
    """Resolved by the real producer store/AgentDef owner, never logical-ID parsing.

    A type is not authentication: C1 must carry the server-selected permanent ID
    from its task/session/workflow owner through fallback and provider retries.
    """
    org_slug: str
    agent_id: str


@dataclass(frozen=True)
class AdmissionProjection:
    org_slug: str
    agent_id: str
    held: bool
    operation_id: str | None
    state_revision: int | None
    phase: str | None
    committed: bool
    apply_available: Literal[False] = False


class TeamMoveCoordinator:
    """Inert, org-owned state/lock composition seam, with no production callers.

    Future short producer serialization: producer ownership/binding lease ->
    producer's root/Pause gate where applicable -> this synchronous gate -> DB
    RLock -> SQLite claim/intent transaction. Tracker's short leaf mutex, if
    required by its owner, stays innermost through commit. Terminal callbacks
    must settle under their existing ownership and must not acquire this gate
    beneath DB/tracker locks. Avoid a reverse callback dependency.

    Profile/publication async writer order stays profile -> workflow -> roster;
    acquire those leases outside the short gate, then revalidate under gate/DB
    for only the synchronous decision or mutation segment. Release gate and DB
    before await, filesystem scan/write, network, provider, bootstrap or acquiring
    any publication lease. Never nest two single-consumer writer intervals.

    C1 must register preparation before prompt/role/permission reads or work
    claims, reserve again at final launch, and retain that ownership through
    retries/fallback/terminal host cleanup. A durable hold is not a mutex lease:
    no TTL, timeout or missing PID releases it. Held agents stay outside host
    FIFO; unaffected traffic keeps normal admission. Captured running thread
    drain is C2-owned and gives no new claim entitlement. B enforces none of
    these producer conditions and must not be advertised as producer closure.
    """
    def __init__(self, store: TeamMoveStore) -> None:
        # Construction is DB-owned, before any producer uses the gate. This
        # is not a callback path or generic Database initialization hook.
        with store.db._lock:
            if getattr(store.db, "_team_move_coordinator", None) is not None:
                raise ValueError("team_move_duplicate_org_owner")
            self.store = store
            self.gate = threading.Lock()
            store.db._team_move_coordinator = self

    @classmethod
    def for_org(cls, store: TeamMoveStore) -> TeamMoveCoordinator:
        with store.db._lock:
            existing = getattr(store.db, "_team_move_coordinator", None)
            if existing is None:
                return cls(store)
            if not isinstance(existing, cls) or existing.store.org_slug != store.org_slug:
                raise ValueError("team_move_identity_owner_mismatch")
            return existing

    def project(self, identity: PermanentAgentIdentity) -> AdmissionProjection:
        if identity.org_slug != self.store.org_slug or not identity.agent_id:
            raise ValueError("team_move_identity_owner_mismatch")
        row = self.store.read()
        held = row is not None and identity.agent_id in _decode(MoveRequest, row["request_bytes"]).affected_agents
        return AdmissionProjection(
            org_slug=identity.org_slug, agent_id=identity.agent_id, held=held,
            operation_id=row["operation_id"] if held else None,
            state_revision=row["state_revision"] if held else None,
            phase=row["phase"] if held else None,
            committed=bool(held and row["decision_bytes"] is not None),
        )

    def startup_contract(self) -> dict:
        """Data only, not an implemented attach/downgrade admission guard."""
        row = self.store.read()
        return {
            "capability_version": CAPABILITY_VERSION,
            "apply_available": APPLY_AVAILABLE,
            "not_integrated": NOT_INTEGRATED,
            "required_reader": "online-team-move@1" if row is not None else None,
            "unresolved_phase": Phase(row["phase"]).value if row is not None else None,
            "startup_requirements": STARTUP_REQUIREMENTS,
            "completion_requirements": COMPLETION_REQUIREMENTS,
        }
