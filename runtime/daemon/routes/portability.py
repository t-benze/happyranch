"""Organization-portability routes (THR-187 Slice A): read-only preflight and
founder-only audited reconciliation.

Both routes are CLI-private daemon surfaces (no TS client, no browser contract).
They are mounted under ``/api/v1/orgs/{slug}`` like every other per-org route.

* ``GET /portability-preflight`` — read-only. Exhaustively classifies every
  direct org-root child and computes quiescence. Reports blockers and possible
  zombies; creates no archive, staging, fence, cancellation, import, or other
  transfer side effect.
* ``POST /reconcile-portability`` — founder/master-bearer-only (reuses the
  existing ``_require_human`` dependency unchanged). Revalidates exactly one
  named candidate as a true zombie under the org DB lock and invokes the shared
  result/terminalization seam. Preflight never calls reconciliation, and
  reconciliation offers no export-cancellation path.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from runtime.daemon.auth import _require_human, require_token
from runtime.daemon.routes._org_dep import OrgDep
from runtime.daemon.state import DaemonState
from runtime.daemon.zombie_reaper import _consume_zombie_fingerprint, _pid_is_dead
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import TaskStatus
from runtime.orchestrator.run_step import _enqueue_parent_if_waiting
from runtime.portability.archive import (
    ARCHIVE_FORMAT_VERSION,
    ARCHIVE_POLICY_VERSION,
    ArchiveMember,
    ArchiveValidationError,
    Manifest,
    build_archive,
    read_archive,
    sha256_file,
)
from runtime.portability.capture import (
    CaptureError,
    collect_source_files,
    compute_v2_fingerprint,
    deactivate_schedules,
    extract_archive,
    gather_legacy_skill_evidence,
    verify_b2_custom_skills,
    verify_sqlite_integrity,
)
from runtime.portability.eligibility import (
    Eligibility,
    TaskLiveness,
    ZombieCandidate,
    compute_eligibility,
    is_true_zombie,
)
from runtime.portability.roots import classify_root_entries
from runtime.runtime import RuntimeDir

router = APIRouter(dependencies=[require_token()])

_ACTIVE_JOB_STATUSES = {"pending", "running"}
_ACTIVE_DREAM_STATUSES = {"pending", "running"}
_ACTIVE_WORK_HOUR_STATUSES = {"pending", "running"}
_ARMED_SCHEDULE_STATUSES = {"armed"}
_FIRING_SCHEDULE_STATUSES = {"firing"}


def _task_state_summary(task) -> dict:
    return {
        "task_id": task.id,
        "status": task.status.value,
        "block_kind": task.block_kind.value if task.block_kind else None,
        "last_heartbeat": task.last_heartbeat.isoformat() if task.last_heartbeat else None,
        "executor_pid": task.executor_pid,
        "assigned_agent": task.assigned_agent,
    }


def _gather_task_liveness(org) -> list[TaskLiveness]:
    out: list[TaskLiveness] = []
    for task_id in org.db.get_nonterminal_task_ids():
        t = org.db.get_task(task_id)
        if t is None:
            continue
        out.append(TaskLiveness(
            task_id=t.id,
            status=t.status.value,
            block_kind=t.block_kind.value if t.block_kind else None,
            last_heartbeat=t.last_heartbeat,
            executor_pid=t.executor_pid,
            assigned_agent=t.assigned_agent,
        ))
    return out


def _active_job_ids(org) -> list[str]:
    # Dedicated uncapped status-filtered id query — never the capped,
    # newest-first presentation list (list_jobs_db), which could hide an old
    # active row behind newer terminal rows.
    return org.db.list_job_ids_by_status(_ACTIVE_JOB_STATUSES)


def _active_dream_ids(org) -> list[str]:
    return org.db.list_dream_ids_by_status(_ACTIVE_DREAM_STATUSES)


def _active_work_hour_ids(org) -> list[str]:
    return org.db.work_hours.list_ids_by_status(_ACTIVE_WORK_HOUR_STATUSES)


def _armed_schedule_ids(org) -> list[str]:
    return org.db.schedules.list_ids_by_status(_ARMED_SCHEDULE_STATUSES)


def _firing_schedule_ids(org) -> list[str]:
    return org.db.schedules.list_ids_by_status(_FIRING_SCHEDULE_STATUSES)


def _active_schedule_ids(org) -> list[str]:
    return sorted(set(_armed_schedule_ids(org)) | set(_firing_schedule_ids(org)))


def _gather_eligibility(org, state: DaemonState) -> Eligibility:
    """Compute the Slice-A quiescence facts for an org (shared by preflight
    and the export fence recheck)."""
    now = datetime.now(timezone.utc)
    return compute_eligibility(
        tasks=_gather_task_liveness(org),
        active_session_count=org.sessions.count_active(),
        queued_for_org=1 if org.slug in state.queue.pending_slugs() else 0,
        pending_invocation_count=len(org.db.list_pending_thread_invocations()),
        active_job_ids=_active_job_ids(org),
        active_dream_ids=_active_dream_ids(org),
        active_work_hour_ids=_active_work_hour_ids(org),
        active_schedule_ids=_active_schedule_ids(org),
        now=now,
        pid_is_dead=_pid_is_dead,
    )


def _build_remedies(
    slug: str,
    *,
    tasks: list[str],
    jobs: list[str],
    dreams: list[str],
    work_hours: list[str],
    armed_schedules: list[str],
    firing_schedules: list[str],
    active_session_count: int,
    queued_for_org: int,
    pending_invocation_count: int,
    zombies: list[ZombieCandidate],
) -> list[dict]:
    """Report the exact actionable remedy for each blocker using only the
    existing founder controls (no relocation-specific disarm command, no
    export fence). For a state with no existing control (a firing schedule,
    live sessions/queue/invocations/dreams/work-hours), report the correct
    non-mutating wait/resolve condition instead."""
    remedies: list[dict] = []

    for sid in sorted(armed_schedules):
        remedies.append({
            "kind": "schedule",
            "target": sid,
            "status": "armed",
            "remedy": (
                f"happyranch todos pause --org {slug} {sid} "
                f"(or: happyranch todos cancel --org {slug} {sid})"
            ),
        })
    for sid in sorted(firing_schedules):
        remedies.append({
            "kind": "schedule",
            "target": sid,
            "status": "firing",
            "remedy": (
                f"{sid} is firing and cannot be paused or cancelled under the "
                f"existing schedule state machine; wait for it to reach a "
                f"terminal state, then re-run the preflight"
            ),
        })

    for tid in tasks:
        remedies.append({
            "kind": "task",
            "target": tid,
            "status": None,
            "remedy": f"happyranch cancel {tid} --org {slug}",
        })

    for jid in jobs:
        remedies.append({
            "kind": "job",
            "target": jid,
            "status": None,
            "remedy": f"happyranch jobs stop {jid} --org {slug}",
        })

    live_surfaces: list[str] = []
    if active_session_count:
        live_surfaces.append(f"{active_session_count} active session(s)")
    if queued_for_org:
        live_surfaces.append("a queued task")
    if pending_invocation_count:
        live_surfaces.append(f"{pending_invocation_count} pending thread invocation(s)")
    if dreams:
        live_surfaces.append(f"{len(dreams)} active dream(s)")
    if work_hours:
        live_surfaces.append(f"{len(work_hours)} active work-hour(s)")
    if live_surfaces:
        remedies.append({
            "kind": "live_work",
            "target": None,
            "status": None,
            "remedy": (
                "no founder cancel control exists for: "
                + "; ".join(live_surfaces)
                + ". Wait for these to complete, then re-run the preflight"
            ),
        })

    for z in zombies:
        remedies.append({
            "kind": "zombie",
            "target": z.task_id,
            "status": None,
            "remedy": (
                f"happyranch orgs reconcile-portability {slug} "
                f"--from-file <absolute-json-path>"
            ),
        })

    return remedies


@router.get("/portability-preflight")
def portability_preflight(slug: str, org: OrgDep, request: Request) -> dict:
    state: DaemonState = request.app.state.daemon
    inventory = classify_root_entries(org.root)
    armed_schedules = _armed_schedule_ids(org)
    firing_schedules = _firing_schedule_ids(org)
    eligibility = _gather_eligibility(org, state)
    return {
        "slug": slug,
        "root": str(org.root),
        "eligible": eligibility.eligible and not inventory.has_rejections,
        "classification": {
            "entries": [e.model_dump() for e in inventory.entries],
            "rejections": [e.model_dump() for e in inventory.rejected],
        },
        "eligibility": {
            "eligible": eligibility.eligible,
            "blockers": eligibility.blockers(),
            "possible_zombies": [z.__dict__ for z in eligibility.possible_zombies],
        },
        "remedies": _build_remedies(
            slug,
            tasks=eligibility.tasks,
            jobs=eligibility.active_jobs,
            dreams=eligibility.active_dreams,
            work_hours=eligibility.active_work_hours,
            armed_schedules=armed_schedules,
            firing_schedules=firing_schedules,
            active_session_count=eligibility.active_session_count,
            queued_for_org=eligibility.queued_for_org,
            pending_invocation_count=eligibility.pending_invocation_count,
            zombies=eligibility.possible_zombies,
        ),
    }


class ReconcilePortabilityBody(BaseModel):
    candidate_task_id: str = Field(min_length=1)
    evidence: dict = Field(default_factory=dict)
    disposition: str = "cancel"


@router.post("/reconcile-portability")
async def reconcile_portability(
    slug: str,
    body: ReconcilePortabilityBody,
    org: OrgDep,
    request: Request,
    _: None = Depends(_require_human),
) -> dict:
    if body.disposition not in ("cancel", "consume_result"):
        raise HTTPException(
            status_code=422,
            detail={"code": "bad_disposition", "disposition": body.disposition},
        )

    request_hash = hashlib.sha256(
        json.dumps(body.model_dump(), sort_keys=True).encode("utf-8")
    ).hexdigest()

    task_id = body.candidate_task_id
    now = datetime.now(timezone.utc)

    async with org.db_lock:
        task = org.db.get_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "unknown_task", "task_id": task_id},
            )
        before = _task_state_summary(task)

        is_zombie, reason = is_true_zombie(
            status=task.status.value,
            block_kind=task.block_kind.value if task.block_kind else None,
            last_heartbeat=task.last_heartbeat,
            executor_pid=task.executor_pid,
            now=now,
            pid_is_dead=_pid_is_dead,
        )
        if not is_zombie:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "not_a_zombie",
                    "task_id": task_id,
                    "reason": reason,
                },
            )

        if body.disposition == "consume_result":
            fingerprint = None
            if task.current_session_id is not None and task.assigned_agent is not None:
                fingerprint = org.db.get_latest_task_result(
                    task_id, task.assigned_agent, task.current_session_id,
                )
            if fingerprint is None:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "no_result_to_consume", "task_id": task_id},
                )
            # Shared result seam: the same orphaned-result consumption the
            # ongoing zombie reaper uses.
            _consume_zombie_fingerprint(org.db, task_id, fingerprint, task, org.orchestrator)
        else:  # cancel — the reaper's terminalization sequence
            now_iso = now.isoformat()
            org.db.update_task(
                task_id,
                status=TaskStatus.CANCELLED,
                cancelled_at=now_iso,
                completed_at=now_iso,
                block_kind=None,
                note="portability reconcile: founder cancelled confirmed zombie",
            )
            AuditLogger(org.db).log_zombie_cancelled(
                task_id, task.assigned_agent or "unknown",
            )
            _enqueue_parent_if_waiting(org.orchestrator, task_id)

        after = _task_state_summary(org.db.get_task(task_id))
        AuditLogger(org.db).log_portability_reconciled(
            task_id=task_id,
            actor="founder",
            request_hash=request_hash,
            evidence=body.evidence,
            disposition=body.disposition,
            before=before,
            after=after,
        )

    return {
        "task_id": task_id,
        "disposition": body.disposition,
        "request_hash": request_hash,
        "before": before,
        "after": after,
    }


# ── Slice B: archive export / inspection / import-relocation ────────────────
#
# All three are CLI-private daemon surfaces (no TS client, no browser
# contract). Export captures the active runtime's loaded org; inspection and
# import-relocation read a CLI-local archive path and (for import) a separate
# schema-v2 target runtime path — neither requires the source org to still be
# loaded in the active runtime, so they take the slug as a plain path string
# rather than an OrgDep. The router-level ``require_token()`` bearer is the
# only auth; mutating requests additionally require an affirmative plaintext /
# unsigned trust acknowledgement (never signing/encryption — checksums prove
# corruption, not sender identity).

_IMPORT_RECEIPT_DIR = "_archive"


def _inventory_sets(inventory) -> tuple[list[dict], list[dict]]:
    excluded = [{"path": e.path, "reason": e.reason} for e in inventory.excluded]
    rejected = [{"path": e.path, "reason": e.reason} for e in inventory.rejected]
    return excluded, rejected


def _included_root_names(inventory) -> list[str]:
    return sorted({e.path.split("/", 1)[0] for e in inventory.included})


def _build_manifest(
    org,
    inventory,
    payload: dict[str, Path],
    staging_db: Path,
    counts: dict[str, int],
) -> Manifest:
    conn = sqlite3.connect(str(staging_db))
    try:
        fingerprint = compute_v2_fingerprint(conn)
    finally:
        conn.close()
    members = [
        ArchiveMember(
            path=arcname,
            size=Path(src).stat().st_size,
            sha256=sha256_file(src),
        )
        for arcname, src in sorted(payload.items())
    ]
    excluded, rejected = _inventory_sets(inventory)
    skill_slugs = [
        e.path.split("/", 1)[1]
        for e in inventory.included
        if e.path.startswith("skills/")
    ]
    legacy_skills = gather_legacy_skill_evidence(org.root / "skills", skill_slugs)
    b2_checks = verify_b2_custom_skills(staging_db, org.root / "artifacts")
    counts_with_db = dict(counts)
    counts_with_db["happyranch.db"] = 1
    return Manifest(
        format_version=ARCHIVE_FORMAT_VERSION,
        policy_version=ARCHIVE_POLICY_VERSION,
        source_slug=org.slug,
        v2_fingerprint=fingerprint,
        members=members,
        source_root_inventory=_included_root_names(inventory),
        included_roots=counts_with_db,
        excluded_entries=excluded,
        rejected_entries=rejected,
        legacy_skills=legacy_skills,
        b2_custom_skill_checks=b2_checks,
    )


class ExportBody(BaseModel):
    archive_path: str = Field(min_length=1)
    trust_acknowledged: bool = False


@router.post("/portability-export")
async def portability_export(
    slug: str, body: ExportBody, org: OrgDep, request: Request,
) -> dict:
    if not body.trust_acknowledged:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "trust_not_acknowledged",
                "message": (
                    "archives are plaintext and unsigned; the mutating export "
                    "request must set trust_acknowledged: true to accept "
                    "local-only trust/handling of the resulting archive"
                ),
            },
        )
    archive_path = Path(body.archive_path).expanduser()
    state: DaemonState = request.app.state.daemon

    # Slice-A readiness (read-only): reject unknown/unsafe roots and any live
    # work — including armed/firing schedules — before touching anything.
    inventory = classify_root_entries(org.root)
    eligibility = _gather_eligibility(org, state)
    if inventory.has_rejections or not eligibility.eligible:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "source_not_ready",
                "rejections": [e.model_dump() for e in inventory.rejected],
                "blockers": eligibility.blockers(),
            },
        )

    # Acquire the per-org transfer fence. While held, dispatch/invocation/
    # scheduler admission is refused, so the recheck → backup → capture window
    # is consistent.
    if not org.transfer_fence.acquire():
        raise HTTPException(
            status_code=409, detail={"code": "transfer_in_progress", "slug": slug},
        )
    staging: Path | None = None
    try:
        async with org.db_lock:
            # Recheck quiescence under DB coordination immediately before the
            # backup. A failed second check conflicts and leaves the source
            # untouched (no archive is produced).
            eligibility = _gather_eligibility(org, state)
            if not eligibility.eligible:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "source_not_ready_on_recheck",
                        "blockers": eligibility.blockers(),
                    },
                )
            staging = Path(tempfile.mkdtemp(prefix="hr-port-export-"))
            staging_db = staging / "happyranch.db"
            org.db.backup_to(staging_db)

        included_paths = [
            e.path for e in inventory.included if e.path != "happyranch.db"
        ]
        payload, counts = collect_source_files(org.root, included_paths)
        payload["payload/happyranch.db"] = staging_db

        manifest = _build_manifest(org, inventory, payload, staging_db, counts)
        digest = build_archive(archive_path, manifest, payload)
        parsed = read_archive(archive_path)
        if parsed.digest != digest:
            raise HTTPException(
                status_code=500, detail={"code": "archive_digest_mismatch"},
            )

        return {
            "slug": slug,
            "archive_digest": digest,
            "archive_path": str(archive_path),
            "member_count": len(manifest.members),
            "source_root_inventory": manifest.source_root_inventory,
            "excluded_entries": manifest.excluded_entries,
            "legacy_skills_quarantined": [
                e.model_dump() for e in manifest.legacy_skills
            ],
            "b2_custom_skill_checks": [
                c.model_dump() for c in manifest.b2_custom_skill_checks
            ],
        }
    finally:
        # Release the fence on every path (success or failure) so admission can
        # resume; clean the private staging directory.
        org.transfer_fence.release()
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


class InspectBody(BaseModel):
    archive_path: str = Field(min_length=1)


@router.post("/portability-inspect")
def portability_inspect(slug: str, body: InspectBody, request: Request) -> dict:
    archive_path = Path(body.archive_path).expanduser()
    try:
        parsed = read_archive(archive_path)
    except ArchiveValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_archive", "message": str(exc)},
        )
    return {
        "slug": slug,
        "archive_digest": parsed.digest,
        "source_slug": parsed.manifest.source_slug,
        "format_version": parsed.manifest.format_version,
        "policy_version": parsed.manifest.policy_version,
        "v2_fingerprint": parsed.manifest.v2_fingerprint,
        "member_count": len(parsed.manifest.members),
        "source_root_inventory": parsed.manifest.source_root_inventory,
        "included_roots": parsed.manifest.included_roots,
        "excluded_entries": parsed.manifest.excluded_entries,
        "rejected_entries": parsed.manifest.rejected_entries,
        "legacy_skills_quarantined": [
            e.model_dump() for e in parsed.manifest.legacy_skills
        ],
        "b2_custom_skill_checks": [
            c.model_dump() for c in parsed.manifest.b2_custom_skill_checks
        ],
    }


class ImportBody(BaseModel):
    archive_path: str = Field(min_length=1)
    target_runtime: str = Field(min_length=1)
    trust_acknowledged: bool = False


@router.post("/portability-import")
def portability_import(slug: str, body: ImportBody, request: Request) -> dict:
    if not body.trust_acknowledged:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "trust_not_acknowledged",
                "message": (
                    "archives are plaintext and unsigned; the mutating import "
                    "request must set trust_acknowledged: true to accept "
                    "local-only trust/handling of the archive"
                ),
            },
        )
    archive_path = Path(body.archive_path).expanduser()

    try:
        parsed = read_archive(archive_path)
    except ArchiveValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_archive", "message": str(exc)},
        )

    if parsed.manifest.source_slug != slug:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "slug_mismatch",
                "manifest_slug": parsed.manifest.source_slug,
                "url_slug": slug,
            },
        )

    try:
        target = RuntimeDir.load(Path(body.target_runtime).expanduser())
    except ValueError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "unsupported_target_runtime", "message": str(exc)},
        )

    # Idempotency: exact digest + slug retry is a no-op; a different digest
    # for the same slug conflicts. Checked BEFORE the collision check so an
    # already-published import is idempotent (the published org dir occupies
    # the destination, which would otherwise be misread as a collision).
    receipt_dir = target.orgs_dir / _IMPORT_RECEIPT_DIR
    receipt_path = receipt_dir / f"import-{slug}.json"
    if receipt_path.exists():
        try:
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        existing_digest = existing.get("digest")
        if existing_digest == parsed.digest:
            return {
                "slug": slug,
                "archive_digest": parsed.digest,
                "result": "already_imported",
            }
        raise HTTPException(
            status_code=409,
            detail={
                "code": "digest_conflict",
                "slug": slug,
                "existing_digest": existing_digest,
                "new_digest": parsed.digest,
            },
        )

    # Collision: ANY on-disk occupancy of the destination slug refuses —
    # loaded, broken, partial, or data-bearing. Never reclaim/overwrite.
    dest = target.orgs_dir / slug
    if os.path.lexists(str(dest)):
        raise HTTPException(
            status_code=409,
            detail={"code": "destination_occupied", "slug": slug},
        )

    op_id = uuid.uuid4().hex
    staging = target.orgs_dir / "_pending" / op_id
    try:
        staging.mkdir(parents=True)
        extract_archive(parsed, archive_path, staging)

        payload_dir = staging / "payload"
        if not (payload_dir / "org" / "teams.yaml").is_file():
            raise HTTPException(
                status_code=422,
                detail={"code": "not_v2_org", "message": "payload lacks org/teams.yaml"},
            )
        db_path = payload_dir / "happyranch.db"
        if not db_path.is_file():
            raise HTTPException(
                status_code=422,
                detail={"code": "missing_db", "message": "payload lacks happyranch.db"},
            )

        # v2 compatibility fingerprint + old-shape rejection (v0 enrollment /
        # v1 flat / old DB shapes are refused — no import-time migration).
        conn = sqlite3.connect(str(db_path))
        try:
            actual_fp = compute_v2_fingerprint(conn)
            has_v0_enrollment = (
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='agent_enrollments'"
                ).fetchone()
                is not None
            )
        finally:
            conn.close()
        if actual_fp != parsed.manifest.v2_fingerprint:
            raise HTTPException(
                status_code=422,
                detail={"code": "fingerprint_mismatch", "slug": slug},
            )
        if has_v0_enrollment:
            raise HTTPException(
                status_code=422,
                detail={"code": "unsupported_v0_shape", "slug": slug},
            )

        try:
            verify_sqlite_integrity(db_path)
        except CaptureError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": "sqlite_invalid", "message": str(exc)},
            )

        b2_checks = verify_b2_custom_skills(db_path, payload_dir / "artifacts")
        invalid_b2 = [c for c in b2_checks if not c.valid]
        if invalid_b2:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "b2_cross_check_failed",
                    "checks": [c.model_dump() for c in invalid_b2],
                },
            )

        # Re-validate quarantined legacy skills against the extracted bytes.
        legacy_evidence = gather_legacy_skill_evidence(
            payload_dir / "skills",
            [e.slug for e in parsed.manifest.legacy_skills],
        )

        # Force every imported schedule active=0 before publish (Slice C alone
        # owns attach/rebind/rearm). Never alters schedule status semantics.
        deactivated = deactivate_schedules(db_path)

        for sidecar in ("happyranch.db-wal", "happyranch.db-shm"):
            if (payload_dir / sidecar).exists():
                raise HTTPException(
                    status_code=422,
                    detail={"code": "sqlite_sidecar_present", "name": sidecar},
                )

        # Publish: same-filesystem atomic rename of the validated payload. os.
        # rename never overwrites an existing destination.
        os.rename(str(payload_dir), str(dest))

        receipt_dir.mkdir(parents=True, exist_ok=True)
        receipt = {
            "slug": slug,
            "digest": parsed.digest,
            "archive_path": str(archive_path),
            "result": "imported",
            "operation_id": op_id,
            "schedules_deactivated": deactivated,
            "legacy_skills_quarantined": [
                e.model_dump() for e in legacy_evidence
            ],
            "imported_at": datetime.now(timezone.utc).isoformat(),
        }
        tmp_receipt = receipt_path.with_name(receipt_path.name + ".tmp")
        tmp_receipt.write_text(json.dumps(receipt, sort_keys=True, indent=2))
        os.replace(str(tmp_receipt), str(receipt_path))

        return {
            "slug": slug,
            "archive_digest": parsed.digest,
            "result": "imported",
            "schedules_deactivated": deactivated,
            "legacy_skills_quarantined": [
                e.model_dump() for e in legacy_evidence
            ],
        }
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=500,
            detail={"code": "import_failed", "message": str(exc)},
        )
    finally:
        # Clean only the private staging directory on any pre-publish fault.
        # The target org dir, loaded/broken registry, and queue are untouched.
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
