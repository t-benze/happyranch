"""All-org observation of stale never-started pending jobs (THR-195).

Diagnosis (durable evidence, see audit trail for JOB-155/191/193/201 and
JOB-002/003/004): a job row is inserted ``pending`` with ``started_at IS
NULL``. On the auto-run path the dispatch handoff is synchronous — a row that
is still ``pending`` after the submit response has never been dispatched; when
the handoff fails validation (e.g. ``cwd_missing`` from a bad ``cwd_hint``)
the submit route re-raises to the caller and the row stays ``pending``
forever. No job queue scans ``pending`` rows, so nothing ever dispatches or
terminates them — they silently strand the owning task's bookkeeping.

This module implements the OBSERVATION half of the THR-195 repair: a read-only
scan, across every organization dynamically discovered via the supported
runtime/org registry (``RuntimeDir.iter_org_roots``), for
``status='pending' AND started_at IS NULL`` jobs older than a justified
threshold. Observation ONLY — this is deliberately NOT an automatic
reaper/retry/cancel mechanism and never mutates lifecycle state. Recurrence is
surfaced (daemon-startup diagnostic log) instead of silently stranding tasks.

Threshold justification: auto-run dispatch is synchronous (sub-second), so any
pending-never-started auto-run row is anomalous; the only legitimate
long-lived ``pending`` class is a review-gated job awaiting founder action, and
one that has waited longer than the threshold is exactly what the observation
consumer should see (it decides, this module does not). Seven days is far
beyond any dispatch window while surfacing recurrence within a week.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from runtime.infrastructure.database import Database
    from runtime.runtime import RuntimeDir

logger = logging.getLogger("happyranch.daemon.stale_pending_jobs")

# A pending job with started_at IS NULL that is older than this has either
# failed its synchronous dispatch handoff (auto-run path) or been abandoned
# awaiting founder review (review-gated path). Both are worth surfacing.
STALE_PENDING_JOB_MAX_AGE = timedelta(days=7)


def stale_pending_cutoff_iso(
    now: datetime,
    *,
    max_age: timedelta = STALE_PENDING_JOB_MAX_AGE,
) -> str:
    """ISO (Z-suffixed) cutoff for the stale-pending predicate.

    ``jobs.created_at`` is stored as Z-suffixed ISO-8601 by ``_now_iso()``, so
    lexicographic comparison against the same format is correct.
    """
    return (now - max_age).isoformat().replace("+00:00", "Z")


def scan_org_stale_pending(
    db: "Database",
    *,
    now: datetime | None = None,
    max_age: timedelta = STALE_PENDING_JOB_MAX_AGE,
) -> list[dict]:
    """Read-only scan of one org DB. Never mutates lifecycle state.

    Returns lightweight dicts (id/task_id/agent_name/title/review_required/
    created_at) for every stale never-started pending job.
    """
    now = now or datetime.now(timezone.utc)
    return db.list_stale_pending_jobs(stale_pending_cutoff_iso(now, max_age=max_age))


def scan_all_org_stale_pending(
    runtime: "RuntimeDir",
    *,
    now: datetime | None = None,
    max_age: timedelta = STALE_PENDING_JOB_MAX_AGE,
) -> dict[str, list[dict]]:
    """Scan every current organization via the supported runtime/org registry.

    Returns ``{org_slug: [stale_pending_rows]}`` — every org discovered by
    ``RuntimeDir.iter_org_roots`` appears in the result (orgs with no stale
    rows map to an empty list, giving the family-style zero-row control a
    voice). Org dirs without a DB yet map to an empty list.

    Startup-safe seam (founder ruling TASK-5544): each org root is observed
    INDEPENDENTLY. A per-org observation failure (malformed file, legacy/
    pre-migration schema without a ``jobs`` table, a locked DB, or any other
    SQLite read error) is logged with org/root/error context and that org
    maps to an empty list — it cannot abort daemon startup and cannot
    suppress the other org roots. Failures are never swallowed silently.
    """
    results: dict[str, list[dict]] = {}
    for slug, root in runtime.iter_org_roots():
        try:
            results[slug] = scan_org_root_stale_pending(
                root, now=now, max_age=max_age,
            )
        except Exception as exc:  # per-org isolation — never abort startup
            logger.warning(
                "stale-pending observation failed for org %s (root %s): %s: %s",
                slug, root, type(exc).__name__, exc,
            )
            results[slug] = []
    return results


def scan_org_root_stale_pending(
    org_root: "Path",
    *,
    now: datetime | None = None,
    max_age: timedelta = STALE_PENDING_JOB_MAX_AGE,
) -> list[dict]:
    """Scan one org root's ``happyranch.db`` via a GENUINE read-only connection
    (own handle, closed after).

    Observation must never durably mutate any store: an org root without a
    ``happyranch.db`` yet (``orgs init`` materializes ``org/teams.yaml``
    first) has nothing to observe — return ``[]`` without opening anything.
    For an EXISTING store this uses ``scan_stale_pending_jobs_readonly``: the
    source is never durably touched — a cleanly-closed store is read via an
    ``immutable=1`` SQLite connection, and an active-WAL store (``-wal``/``-shm``
    present) is read DIRECTLY via a genuine read-only WAL-aware connection
    (``mode=ro``) on the source itself. The FOUNDER CONTRACT (TASK-5544)
    protects the durable source ``happyranch.db`` and ``happyranch.db-wal``
    BYTES ONLY: both stay byte-identical before/after every observation,
    while the SQLite WAL-index ``happyranch.db-shm`` may be created,
    modified, or removed by read-side WAL access (explicitly permitted
    transient reader/lock/index behavior — no ``-shm`` identity is asserted),
    and no snapshot/temp directory is created anywhere. A legacy
    pre-migration DB stays byte-identical across scans, and a
    malformed/irrelevant store fails closed (raises) without mutation — the
    all-org coordinator isolates and logs that failure so startup continues.
    """
    from runtime.infrastructure.database import scan_stale_pending_jobs_readonly

    now = now or datetime.now(timezone.utc)
    db_path = org_root / "happyranch.db"
    return scan_stale_pending_jobs_readonly(
        db_path, stale_pending_cutoff_iso(now, max_age=max_age),
    )
