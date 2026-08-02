"""Per-org durable last-known-good dashboard-summary projection.

A sidecar JSON file under <org_root>/dashboard_projection.json holds the most
recently successful compose_dashboard_summary output. An asyncio scheduler
refreshes it every 10 seconds with coalescing (no overlapping refreshes).
When a refresh fails, the prior good snapshot is preserved (last-known-good).

The HTTP route reads ONLY the in-memory projection; it never calls
compose_dashboard_summary synchronously. Cold-start / no-projection-yet
returns a deterministic 503 with a standard error envelope.

Spec: protocol/05e-dashboard.md (THR-129 cache-only behavior)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from runtime.orchestrator.dashboard_summary import (
    DashboardSummaryResponse,
    compose_dashboard_summary,
)

logger = logging.getLogger(__name__)

# ── Projection model ──────────────────────────────────────────────────────

# Only version 1 is supported. A persisted sidecar with any other version
# is treated as cache-unavailable (cold-start, returns None from load).
_SUPPORTED_VERSION = 1


class DashboardProjection(BaseModel):
    """Versioned, wire-safe serialization envelope for a dashboard summary.

    Stored as a JSON file so the daemon can cold-start from persisted state.
    """
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=_SUPPORTED_VERSION, strict=True)
    org_slug: str
    generated_at: datetime
    payload: dict[str, Any]  # DashboardSummaryResponse.model_dump(mode='json')


# ── Projection manager ────────────────────────────────────────────────────

# Refresh every 10 seconds per the THR-129 brief.
_REFRESH_INTERVAL_SECONDS = 10.0


@dataclass
class DashboardProjectionManager:
    """Per-org projection lifecycle.

    Owns the in-memory cached payload, the persisted sidecar file, and the
    coalesced periodic refresh scheduler task.
    """
    org_slug: str
    org_root: Path
    _projection: DashboardProjection | None = field(default=None, init=False)
    _refresh_task: asyncio.Task | None = field(default=None, init=False)
    _refresh_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)
    _refresh_in_flight: bool = field(default=False, init=False)

    @property
    def projection_path(self) -> Path:
        return self.org_root / "dashboard_projection.json"

    # ── Persistence ──────────────────────────────────────────────────────

    def load_from_disk(self) -> DashboardProjection | None:
        """Load the persisted projection from disk with strict validation.

        Returns None (cache unavailable) for:
        - Missing file
        - Corrupt / non-JSON content
        - Valid JSON that fails ``DashboardProjection`` envelope validation
          (strict mode: rejects numeric/boolean coercions, unknown fields,
          malformed types — only canonical ISO datetime JSON form accepted)
        - org_slug mismatch (foreign-org sidecar on a shared volume)
        - Unsupported version
        - Payload that fails ``DashboardSummaryResponse`` wire-model validation
          (strict mode: rejects string-ified numbers, boolean lists, etc.)

        The returned projection carries the VALIDATED (re-serialized) payload
        from ``DashboardSummaryResponse.model_dump(mode='json')``, never the
        raw on-disk dict. All failure paths are deterministic — they never
        serve a partial or malformed projection through the HTTP route.
        """
        path = self.projection_path
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception:
            logger.warning(
                "dashboard projection file for org %s is unreadable",
                self.org_slug, exc_info=True,
            )
            return None

        # Strict JSON validation of the ENTIRE DashboardProjection envelope.
        # model_validate_json(strict=True) rejects:
        # - Non-integer version (boolean true, string "1")
        # - Non-string generated_at (numeric, boolean)
        # - Unknown envelope fields (extra='forbid')
        # - Any type coercion in the envelope or payload dict
        try:
            proj = DashboardProjection.model_validate_json(raw, strict=True)
        except Exception:
            logger.warning(
                "dashboard projection file for org %s fails envelope validation",
                self.org_slug, exc_info=True,
            )
            return None

        # Reject foreign-org sidecars
        if proj.org_slug != self.org_slug:
            logger.warning(
                "dashboard projection file for org %s has foreign org_slug=%r — "
                "rejecting (shared volume / misrouted sidecar)",
                self.org_slug, proj.org_slug,
            )
            return None

        # Reject unsupported versions
        if proj.version != _SUPPORTED_VERSION:
            logger.warning(
                "dashboard projection file for org %s has unsupported version %d "
                "(supported: %d)",
                self.org_slug, proj.version, _SUPPORTED_VERSION,
            )
            return None

        # Reject payload containing unknown/extra fields BEFORE Pydantic
        # validation. DashboardSummaryResponse uses the BaseModel default
        # extra='ignore', so model_validate_json(strict=True) alone silently
        # drops unknown fields instead of rejecting them. Explicit pre-check
        # guarantees every on-disk key maps to a canonical response field.
        valid_payload_keys = set(DashboardSummaryResponse.model_fields.keys())
        payload_keys = set(proj.payload.keys())
        extra_keys = payload_keys - valid_payload_keys
        if extra_keys:
            _extra = sorted(extra_keys)
            logger.warning(
                "dashboard projection file for org %s has payload with "
                "%d unknown field(s): %s",
                self.org_slug, len(_extra), _extra,
            )
            return None

        # Validate payload against DashboardSummaryResponse with strict type
        # checking AND retain the validated output. model_validate_json with
        # strict=True rejects coercible payload types (string numeric fields,
        # boolean lists, etc.) while accepting canonical ISO datetime strings.
        # The validated model is re-serialized to dict via model_dump(mode='json')
        # so the returned projection never carries raw/untrusted dict data.
        try:
            validated = DashboardSummaryResponse.model_validate_json(
                json.dumps(proj.payload), strict=True,
            )
            proj.payload = validated.model_dump(mode="json")
        except Exception:
            logger.warning(
                "dashboard projection file for org %s has payload that fails "
                "DashboardSummaryResponse wire-model validation",
                self.org_slug, exc_info=True,
            )
            return None

        logger.debug(
            "dashboard projection loaded for org %s: generated_at=%s",
            self.org_slug, proj.generated_at.isoformat(),
        )
        return proj

    def _atomic_persist(self, projection: DashboardProjection) -> None:
        """Atomically write the projection to disk.

        Uses os.replace() which is atomic on POSIX — the canonical file is
        never unlinked before the replacement exists. If os.replace fails,
        the canonical sidecar is untouched (the tmp file becomes debris that
        cannot affect the canonical cache).

        Internal seam for warm().
        """
        path = self.projection_path
        tmp = path.with_suffix(path.suffix + ".tmp")
        raw = projection.model_dump_json(indent=2)
        tmp.write_text(raw, encoding="utf-8")
        os.replace(tmp, path)

    def persist(self, projection: DashboardProjection) -> None:
        """Atomically write the projection to disk (delegates to _atomic_persist).

        Public surface preserved for tests and cold-start seeding.
        """
        self._atomic_persist(projection)

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def warm(self, db, kb_store, teams) -> bool:
        """Perform ONE synchronous compose + persist + atomic publish.

        Called at cold-start and on every periodic tick. Returns True on
        success, False on failure.

        This is the single point where compose_dashboard_summary is called
        for the projection; the HTTP route never calls it directly.

        Atomic publish contract:
        1. Capture old in-memory projection.
        2. Compose the dashboard summary.
        3. Construct + validate the projection envelope (DashboardProjection).
        4. Serialize to JSON (verify it round-trips via model_dump_json).
        5. Persist to disk atomically via os.replace (tmp write, then atomic
           replace — canonical file is never unlinked first).
        6. ONLY THEN publish to self._projection in memory.

        On ANY failure at ANY seam (compose, envelope validation,
        serialization, tmp write, or os.replace), the exact former
        last-known-good in-memory projection AND the exact former durable
        sidecar bytes are preserved. os.replace guarantees the canonical
        path is never damaged — no non-atomic recovery rewrite is needed.
        Never update in-memory before disk persistence succeeds; never leave
        a half-written or truncated sidecar on disk.
        """
        # Capture old in-memory state BEFORE any mutation.
        old_projection = self._projection

        try:
            now = datetime.now(timezone.utc)
            # 1. Compose in a thread (db._lock is threading.RLock, safe).
            response = await asyncio.to_thread(
                compose_dashboard_summary,
                db=db, kb_store=kb_store, teams=teams, now=now,
            )
            # 2. Construct + validate the envelope.
            projection = DashboardProjection(
                org_slug=self.org_slug,
                generated_at=now,
                payload=response.model_dump(mode="json"),
            )
            # 3. Verify serialization (model_dump_json catches serialization
            #    bugs early — we never publish something that can't be persisted).
            _ = projection.model_dump_json()
            # 4. Persist to disk BEFORE publishing in memory.
            #    os.replace(tmp, path) is atomic on POSIX — the canonical file
            #    is never unlinked before the replacement exists. If replace
            #    fails, the canonical sidecar is untouched. No non-atomic
            #    recovery rewrite is needed.
            await asyncio.to_thread(self._atomic_persist, projection)
            # 5. Atomic publish: only after disk persistence succeeds.
            self._projection = projection
            logger.debug(
                "dashboard projection refreshed for org %s: generated_at=%s",
                self.org_slug, now.isoformat(),
            )
            return True
        except Exception:
            logger.warning(
                "dashboard projection refresh FAILED for org %s — "
                "keeping last-known-good snapshot (in-memory + sidecar)",
                self.org_slug, exc_info=True,
            )
            # Restore exact old in-memory projection.
            # The sidecar is preserved automatically by os.replace —
            # the canonical file was never unlinked or overwritten.
            self._projection = old_projection
            return False

    async def _scheduler_loop(
        self, db, kb_store, teams, shutdown_event: asyncio.Event,
    ) -> None:
        """Coalesced periodic refresh: runs warm() every _REFRESH_INTERVAL_SECONDS.
        If a refresh is still in flight when the timer fires, skips that tick
        (no overlapping refreshes). Exits cleanly when shutdown_event is set.

        Cancellation safety: a CancelledError thrown into the await path
        (including during a warm that is stuck in to_thread) exits cleanly —
        the in-flight flag is not reset (no new tick starts after cancel),
        the existing _projection is preserved, and the task yields without
        an unowned exception."""
        try:
            while not shutdown_event.is_set():
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(), timeout=_REFRESH_INTERVAL_SECONDS,
                    )
                    # shutdown_event was set → exit
                    return
                except asyncio.TimeoutError:
                    pass  # normal tick

                # Coalescing: skip if a refresh is already in-flight
                if self._refresh_in_flight:
                    continue

                self._refresh_in_flight = True
                try:
                    await self.warm(db, kb_store, teams)
                finally:
                    self._refresh_in_flight = False
        except asyncio.CancelledError:
            # Clean exit on cancellation: the in-flight flag may still be
            # True if a warm is stuck in to_thread, but that's safe — no
            # new tick will start and the running warm finishes (or is
            # abandoned on process exit). The prior _projection is preserved.
            # Re-raise so the gather/reap path sees the cancellation.
            raise

    # ── Scheduler management ─────────────────────────────────────────────

    def start_scheduler(
        self,
        db,
        kb_store,
        teams,
        loop: asyncio.AbstractEventLoop | None = None,
        *,
        initial_warm: bool = False,
    ) -> asyncio.Task:
        """Create and return the scheduler background task.

        Must be called from within a running event loop.
        Once started, the task runs until cancelled.

        When initial_warm=True (daemon cold-start), the task performs one
        immediate warm() before entering the periodic scheduler loop. If
        initial_warm=False, the periodic loop starts immediately (the first
        tick fires after _REFRESH_INTERVAL_SECONDS).
        """
        if loop is None:
            loop = asyncio.get_running_loop()
        self._shutdown_event = asyncio.Event()

        if initial_warm:
            async def _bootstrap_then_schedule():
                # Immediate warm — if it fails, the periodic tick will retry
                ok = await self.warm(db, kb_store, teams)
                if ok:
                    logger.info(
                        "dashboard projection initial warm succeeded for org %s",
                        self.org_slug,
                    )
                else:
                    logger.warning(
                        "dashboard projection initial warm FAILED for org %s "
                        "— will retry on periodic scheduler tick",
                        self.org_slug,
                    )
                # Then enter the periodic loop
                await self._scheduler_loop(
                    db, kb_store, teams, self._shutdown_event,
                )
            self._refresh_task = loop.create_task(_bootstrap_then_schedule())
        else:
            self._refresh_task = loop.create_task(
                self._scheduler_loop(db, kb_store, teams, self._shutdown_event),
            )
        return self._refresh_task

    def cancel_scheduler(self) -> None:
        """Issue cancellation for the scheduler task.

        Must be called BEFORE awaiting (so a refresh stuck in the to_thread
        warm path doesn't make daemon shutdown wait for cooperative stop
        observation). Idempotent."""
        if self._refresh_task is None:
            return
        self._shutdown_event.set()
        self._refresh_task.cancel()

    async def reap_scheduler(self) -> None:
        """Await the scheduler task, collecting its outcome without raising.

        Must be called AFTER cancel_scheduler(). Returns after the task
        is fully done (or cancelled). Any exception (CancelledError or
        otherwise) is caught — never leaves an unowned task exception."""
        if self._refresh_task is None:
            return
        try:
            await self._refresh_task
        except (asyncio.CancelledError, Exception):
            pass
        self._refresh_task = None

    # ── Read path (for the HTTP route) ───────────────────────────────────

    def get_projection(self) -> DashboardProjection | None:
        """Return the current in-memory projection, or None if never warmed.

        The HTTP route calls this; it never triggers a compose.
        """
        return self._projection
