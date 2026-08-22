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

import asyncio
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
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
    canonical_v2_fingerprint,
    collect_source_files,
    compute_v2_fingerprint,
    deactivate_schedules,
    extract_archive,
    gather_legacy_skill_evidence,
    validate_b2_match,
    validate_legacy_evidence_match,
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

# Per-(runtime, slug) in-process import locks — the threadpool coordination half
# of the import claim. Mirrors ``_acquire_profile_lock`` (executors.py) and
# ``_invocation_lock`` (thread_runner.py): a per-key ``threading.Lock`` created
# lazily under a creation lock so two threads never race to insert it.
_import_claim_locks: dict[str, threading.Lock] = {}
_import_claim_locks_guard = threading.Lock()


def _import_claim_thread_lock(key: str) -> threading.Lock:
    """Return the shared per-key in-process lock, creating it on first use."""
    with _import_claim_locks_guard:
        lock = _import_claim_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _import_claim_locks[key] = lock
    return lock


class ImportClaim:
    """Exclusive, durable same-(runtime, slug) import claim.

    Serializes imports of one slug into one destination runtime — v1 REFUSES
    concurrent imports rather than supporting them. The claim is two halves:

    * **In-process** — a nonblocking per-key ``threading.Lock`` coordinates
      concurrent ``portability_import`` calls that FastAPI runs on its sync-route
      threadpool (the demonstrated read-modify-write race in the pending
      marker).
    * **Cross-process** — a nonblocking POSIX ``fcntl.flock`` (``LOCK_EX |
      LOCK_NB``) on a *stable* lock file inside the reserved receipt namespace
      (``<target>/orgs/_archive/.import-claim-<slug>.lock``), so a second
      daemon/process targeting the same runtime gets ``import_in_progress``
      instead of racing.

    ``acquire()`` returns ``False`` (without touching any import state) when
    either half is already held, which the route maps to HTTP 409
    ``import_in_progress``. The caller MUST hold the claim across check →
    prepare → pending identity → publish → receipt/recovery/finalize and call
    ``release()`` on every ordinary and error path (a ``try``/``finally`` in the
    route guarantees this). ``release()`` unlocks and closes the FD but NEVER
    unlinks the lock file: a future caller opens the same path, and a process
    crash releases ``flock`` implicitly. The persistent pending marker/receipt
    remains the sole single-owner recovery record.
    """

    def __init__(self, orgs_dir: Path, slug: str) -> None:
        key = f"{os.path.realpath(str(orgs_dir))}:{slug}"
        self._thread_lock = _import_claim_thread_lock(key)
        self._lock_path = orgs_dir / _IMPORT_RECEIPT_DIR / f".import-claim-{slug}.lock"
        self._fd: int | None = None

    def acquire(self) -> bool:
        # In-process half first: refuse immediately if another thread in this
        # process already holds the same (runtime, slug) claim.
        if not self._thread_lock.acquire(blocking=False):
            return False
        # Cross-process half: a nonblocking flock on a stable lock file. On
        # contention flock raises OSError (EWOULDBLOCK/EAGAIN) rather than
        # blocking, so a second process gets a refusal, not a race.
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._lock_path), os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            self._thread_lock.release()
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        # Never unlink the lock file: the open FD (not the path entry) is the
        # claim, and a future caller must open the same stable path. Unlocking
        # + closing releases flock; a process crash releases it implicitly.
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        self._thread_lock.release()


def _read_json(path: Path) -> dict:
    """Read a JSON file, returning ``{}`` on any missing/corrupt read."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_pending_marker(
    pending_path: Path, *, slug: str, digest: str, operation_id: str,
) -> None:
    """Record the in-flight import identity BEFORE publish.

    Written atomically (and durably, immediately before the no-replace publish)
    so a crash at any point leaves a visible recovery marker: a same-digest
    retry can converge, and a differing digest conflicts, without overwriting.
    On a refused publish the caller removes the marker so a stale marker can
    never falsely claim a destination was published by this archive.
    """
    pending_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = pending_path.with_name(pending_path.name + ".tmp")
    tmp.write_text(json.dumps(
        {"slug": slug, "digest": digest, "operation_id": operation_id},
        sort_keys=True,
    ))
    os.replace(str(tmp), str(pending_path))


def _write_receipt(
    receipt_path: Path, *, slug: str, digest: str, archive_path: str,
    operation_id: str, deactivated: int | None,
    legacy_evidence: list, recovery: bool,
) -> None:
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "slug": slug,
        "digest": digest,
        "archive_path": archive_path,
        "result": "imported",
        "operation_id": operation_id,
        "schedules_deactivated": deactivated,
        "legacy_skills_quarantined": [
            e.model_dump() if hasattr(e, "model_dump") else e for e in legacy_evidence
        ],
        "recovery": recovery,
        "imported_at": datetime.now(timezone.utc).isoformat(),
    }
    tmp = receipt_path.with_name(receipt_path.name + ".tmp")
    tmp.write_text(json.dumps(receipt, sort_keys=True, indent=2))
    os.replace(str(tmp), str(receipt_path))


def _remove_matching_pending_after_receipt(
    *, pending_path: Path, receipt: dict,
) -> None:
    """Owner-held receipt recovery: remove a pending marker left by a fault
    that occurred after ``_write_receipt()`` but before ``pending_path.unlink()``.

    The idempotent receipt fast path converges an exact digest+slug retry to
    ``already_imported``. A crash in that finalize window persists the receipt
    but strands the pending marker. This helper removes the marker ONLY when
    its durable identity (slug + digest + operation_id) exactly matches the
    finalized receipt — never by digest alone, and never when the marker is
    malformed or carries a foreign identity. A malformed, missing, or
    nonmatching marker is left untouched (fail closed): this path never infers
    ownership of a marker it cannot positively match to the receipt.
    """
    pending = _read_json(pending_path)
    if not pending:
        return
    if (
        pending.get("slug") == receipt.get("slug")
        and pending.get("digest") == receipt.get("digest")
        and pending.get("operation_id") == receipt.get("operation_id")
    ):
        pending_path.unlink(missing_ok=True)


def _rename_noreplace(src: Path, dst: Path) -> bool:
    """Atomically rename ``src`` to ``dst`` only if ``dst`` does not exist.

    A platform-correct, genuine no-overwrite primitive: on Linux this is
    ``renameat2(..., RENAME_NOREPLACE)``; on macOS it is
    ``renamex_np(..., RENAME_EXCL)``. Both fail with ``EEXIST`` if the
    destination already exists (file, directory, or symlink — empty or not),
    leaving the competing destination intact. Returns ``True`` on success,
    ``False`` when ``dst`` already exists, and raises ``OSError`` on any other
    failure. On a platform without either primitive the operation fails closed
    (``ENOSYS``) — it never falls back to an overwriting rename.
    """
    if sys.platform == "linux":
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOSYS, "renameat2 unavailable", str(dst))
        renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p,
                              ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        renameat2.restype = ctypes.c_int
        rc = renameat2(-100, os.fsencode(src), -100, os.fsencode(dst), 1)  # RENAME_NOREPLACE
        if rc == 0:
            return True
        e = ctypes.get_errno()
        if e == errno.EEXIST:
            return False
        raise OSError(e, os.strerror(e), str(dst))
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        renamex_np = getattr(libc, "renamex_np", None)
        if renamex_np is None:
            raise OSError(errno.ENOSYS, "renamex_np unavailable", str(dst))
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        rc = renamex_np(os.fsencode(src), os.fsencode(dst), 0x00000004)  # RENAME_EXCL
        if rc == 0:
            return True
        e = ctypes.get_errno()
        if e == errno.EEXIST:
            return False
        raise OSError(e, os.strerror(e), str(dst))
    raise OSError(errno.ENOSYS, "no no-replace rename primitive on this platform", str(dst))


def _publish_no_replace(payload_dir: Path, dest: Path) -> None:
    """Atomically publish ``payload_dir`` to ``dest`` without replacing any
    existing destination (platform-correct same-filesystem no-replace).

    ``_rename_noreplace`` is a single atomic no-overwrite rename: exactly one
    caller can ever win the destination name, and a competitor that creates an
    *empty* (or any) destination after validation is left intact — the publish
    fails closed with ``destination_occupied`` rather than overwriting it.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        ok = _rename_noreplace(payload_dir, dest)
    except OSError:
        raise HTTPException(
            status_code=409, detail={"code": "destination_occupied", "slug": dest.name},
        )
    if not ok:
        raise HTTPException(
            status_code=409, detail={"code": "destination_occupied", "slug": dest.name},
        )


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

    # Acquire the per-org transfer fence (writer lease). ``acquire`` waits for
    # every in-flight admission to drain before returning, so the recheck →
    # backup → capture window is linearizable: an admission that started before
    # this call has committed and will be observed by the recheck; any admission
    # after this call raises TransferFenceHeld and lands nothing.
    if not await org.transfer_fence.acquire():
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

        # The transfer fence is held: no new admission can mutate captured
        # state. Run the blocking capture (SQLite backup → tree enumeration →
        # hashing → tar/gzip → full archive reread) on a worker thread so the
        # daemon event loop stays responsive. The Database RLock serializes the
        # backup against any in-flight writer, and the fence guarantees no
        # writer can start between recheck and capture (no capture window).
        return await asyncio.to_thread(
            _run_blocking_capture, org, inventory, staging, archive_path,
        )
    finally:
        # Release the fence on every path (success or failure) so admission can
        # resume; clean the private staging directory off the loop.
        await org.transfer_fence.release()
        if staging is not None:
            await asyncio.to_thread(shutil.rmtree, staging, True)


def _run_blocking_capture(
    org, inventory, staging: Path, archive_path: Path,
) -> dict:
    """Blocking capture pipeline (runs on a worker thread, never the loop)."""
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
        "slug": org.slug,
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

    # Destination runtime must be schema-v2 (enforced above by RuntimeDir.load)
    # AND otherwise non-empty: at least one *other* valid org must already
    # exist. An empty v2 target is refused before any target mutation — this is
    # an enforced contract, not a fixture convention.
    other_orgs = [s for s, _ in target.iter_org_roots() if s != slug]
    if not other_orgs:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "empty_target_runtime",
                "message": (
                    "target runtime has no other org; relocation requires an "
                    "otherwise non-empty schema-v2 destination"
                ),
            },
        )

    # Acquire the exclusive per-(runtime, slug) import claim BEFORE any
    # state-dependent read (receipt idempotency / pending marker / destination
    # existence) or mutation. v1 serializes imports to one destination: a
    # competing same runtime+slug invocation is refused with
    # ``import_in_progress`` and MUST NOT read/infer ownership of, or write/
    # replace/unlink, the owner's marker, staging, target, or receipt. The
    # claim is held across check -> prepare -> pending identity -> publish ->
    # receipt/recovery/finalize and released on every ordinary and error path.
    claim = ImportClaim(target.orgs_dir, slug)
    if not claim.acquire():
        raise HTTPException(
            status_code=409,
            detail={"code": "import_in_progress", "slug": slug},
        )
    try:
        return _import_relocation(
            slug=slug, parsed=parsed, archive_path=archive_path, target=target,
        )
    finally:
        # Release the claim on every path (success, refusal, validation
        # failure, crash-fault). Never unlinks the stable lock file.
        claim.release()


def _import_relocation(
    *,
    slug: str,
    parsed,
    archive_path: Path,
    target: RuntimeDir,
) -> dict:
    """Run the import-relocation critical section under the held exclusive
    per-(runtime, slug) claim: receipt idempotency, pending-marker
    reconciliation, destination-collision refusal, staged validation, no-replace
    publish, and receipt finalize/recovery."""

    # Idempotency: exact digest + slug retry is a no-op; a different digest
    # for the same slug conflicts. Checked BEFORE the collision check so an
    # already-published import is idempotent (the published org dir occupies
    # the destination, which would otherwise be misread as a collision).
    receipt_dir = target.orgs_dir / _IMPORT_RECEIPT_DIR
    receipt_path = receipt_dir / f"import-{slug}.json"
    pending_path = receipt_dir / f".pending-import-{slug}.json"
    if receipt_path.exists():
        try:
            existing = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            existing = {}
        existing_digest = existing.get("digest")
        if existing_digest == parsed.digest:
            # Owner-held receipt recovery: a fault after _write_receipt() but
            # before pending_path.unlink() strands the marker behind a durable
            # receipt. Remove it only when its identity exactly matches this
            # receipt (fail closed on a malformed/mismatched marker).
            _remove_matching_pending_after_receipt(
                pending_path=pending_path,
                receipt=existing,
            )
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

    # Reconcile the pending receipt identity BEFORE branching on destination
    # existence. A pending marker records the durable in-flight import identity
    # (slug + digest + operation) written before publish. A DIFFERENT digest for
    # the same slug must conflict whether the destination is absent (a crash
    # after identity preparation but before publish) or present (a crash after
    # publish but before finalize) — it must never overwrite/reuse the marker
    # or publish. Only an exact digest+slug may resume/converge.
    dest = target.orgs_dir / slug
    pending = _read_json(pending_path)
    pending_digest = pending.get("digest") if pending.get("slug") == slug else None
    if pending_digest is not None and pending_digest != parsed.digest:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "digest_conflict",
                "slug": slug,
                "existing_digest": pending_digest,
                "new_digest": parsed.digest,
            },
        )

    if os.path.lexists(str(dest)):
        if pending_digest is not None:
            # Same digest + slug, destination present (published but unfinalized
            # crash): converge by writing the missing receipt WITHOUT overwriting
            # the published org. Verify the destination is a published v2 org
            # (teams.yaml) so a stale marker can never claim a competitor's dest.
            if not (dest / "org" / "teams.yaml").is_file():
                raise HTTPException(
                    status_code=409,
                    detail={"code": "destination_occupied", "slug": slug},
                )
            _write_receipt(
                receipt_path,
                slug=slug,
                digest=parsed.digest,
                archive_path=str(archive_path),
                operation_id=pending.get("operation_id", ""),
                deactivated=None,
                legacy_evidence=parsed.manifest.legacy_skills,
                recovery=True,
            )
            pending_path.unlink(missing_ok=True)
            return {
                "slug": slug,
                "archive_digest": parsed.digest,
                "result": "imported",
                "recovered": True,
            }
        # Destination occupied without a matching pending marker → collision.
        raise HTTPException(
            status_code=409,
            detail={"code": "destination_occupied", "slug": slug},
        )

    # Destination absent: fresh import. If a pending marker carried the same
    # digest (a pre-publish crash left identity but no destination), the import
    # resumes here and rewrites the marker with a fresh operation id during
    # publish prep — same digest, so no conflict, and no destination was ever
    # created by the prior attempt.

    op_id = uuid.uuid4().hex
    staging = target.orgs_dir / "_pending" / op_id
    phase = "prepare"
    published = False
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

        # Independently validate the staged DB against the canonical current-v2
        # schema contract (derived from the runtime's own schema bootstrap in
        # ``runtime/infrastructure/database.py``), NOT merely against the
        # attacker-controlled manifest fingerprint. This refuses v0 enrollment,
        # v1 flat-single-org, and any other old/unsupported DB shape — no
        # import-time migration or RuntimeDir-loader broadening.
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
        canonical_fp = canonical_v2_fingerprint()
        if actual_fp != canonical_fp:
            raise HTTPException(
                status_code=422,
                detail={"code": "unsupported_db_shape", "slug": slug},
            )
        if parsed.manifest.v2_fingerprint != canonical_fp:
            raise HTTPException(
                status_code=422,
                detail={"code": "manifest_fingerprint_mismatch", "slug": slug},
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
        # Bind the manifest's declared B2 evidence to the recomputed checks
        # (artifact key + content hash must agree; recomputed must be valid).
        validate_b2_match(parsed.manifest.b2_custom_skill_checks, b2_checks)

        # Re-validate quarantined legacy skills against the extracted bytes,
        # then bind the manifest's declared evidence to those bytes (identity,
        # validation_result, member hashes, resolved local references).
        legacy_evidence = gather_legacy_skill_evidence(
            payload_dir / "skills",
            [e.slug for e in parsed.manifest.legacy_skills],
        )
        validate_legacy_evidence_match(parsed.manifest.legacy_skills, legacy_evidence)

        # Force every imported schedule active=0 before publish (Slice C alone
        # owns attach/rebind/rearm). Never alters schedule status semantics.
        deactivated = deactivate_schedules(db_path)

        for sidecar in ("happyranch.db-wal", "happyranch.db-shm"):
            if (payload_dir / sidecar).exists():
                raise HTTPException(
                    status_code=422,
                    detail={"code": "sqlite_sidecar_present", "name": sidecar},
                )

        # Publish: platform-correct same-filesystem no-replace. Durable import
        # identity (digest + slug + operation) is prepared BEFORE publish so a
        # crash at any boundary is recoverable: a crash after preparation but
        # before publish leaves no destination and no false success; a crash
        # after publish but before finalize converges on the same digest+slug
        # via the marker WITHOUT overwrite; a differing digest conflicts.
        phase = "publish"
        _write_pending_marker(
            pending_path, slug=slug, digest=parsed.digest, operation_id=op_id,
        )
        try:
            _publish_no_replace(payload_dir, dest)
        except HTTPException:
            # Publish refused (destination occupied): remove the marker so a
            # later retry can never falsely converge on a destination this
            # archive never published.
            pending_path.unlink(missing_ok=True)
            raise
        published = True
        phase = "finalize"

        _write_receipt(
            receipt_path,
            slug=slug,
            digest=parsed.digest,
            archive_path=str(archive_path),
            operation_id=op_id,
            deactivated=deactivated,
            legacy_evidence=legacy_evidence,
            recovery=False,
        )
        pending_path.unlink(missing_ok=True)

        return {
            "slug": slug,
            "archive_digest": parsed.digest,
            "result": "imported",
            "schedules_deactivated": deactivated,
            "legacy_skills_quarantined": [
                e.model_dump() for e in legacy_evidence
            ],
        }
    except ArchiveValidationError as exc:
        # Hostile/inconsistent archive content (legacy-skill or B2 evidence
        # binding, member-root/escapement failures) — fail closed as a client
        # error, never a 500, and never after any target/source mutation.
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_archive_content", "message": str(exc)},
        )
    except CaptureError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_archive_content", "message": str(exc)},
        )
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=500,
            detail={"code": "import_failed", "message": str(exc)},
        )
    finally:
        # Pre-publish validation faults clean the private staging directory. A
        # publish conflict leaves staging under _pending as visible recovery
        # state (the no-replace publish refused a competitor's occupancy). The
        # target org dir, loaded/broken registry, and queue are never touched.
        if phase == "prepare":
            shutil.rmtree(staging, ignore_errors=True)
        elif published:
            # Payload was renamed out; remove the now-empty staging dir.
            shutil.rmtree(staging, ignore_errors=True)
