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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

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
    version: int = _SUPPORTED_VERSION
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
        - org_slug mismatch (foreign-org sidecar on a shared volume)
        - Unsupported version
        - Payload that fails ``DashboardSummaryResponse`` wire-model validation

        All failure paths are deterministic — they never serve a partial or
        malformed projection through the HTTP route.
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

        # Parse JSON
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning(
                "dashboard projection file for org %s is not valid JSON",
                self.org_slug,
            )
            return None

        # Validate envelope (DashboardProjection)
        try:
            proj = DashboardProjection.model_validate(data)
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

        # Validate payload as the wire response model
        try:
            DashboardSummaryResponse.model_validate(proj.payload)
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

    def persist(self, projection: DashboardProjection) -> None:
        """Atomically write the projection to disk (write-then-rename)."""
        path = self.projection_path
        tmp = path.with_suffix(path.suffix + ".tmp")
        raw = projection.model_dump_json(indent=2)
        tmp.write_text(raw, encoding="utf-8")
        tmp.rename(path)

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def warm(self, db, kb_store, teams) -> bool:
        """Perform ONE synchronous compose + persist + atomic publish.

        Called at cold-start and on every periodic tick. Returns True on
        success, False on failure.

        This is the single point where compose_dashboard_summary is called
        for the projection; the HTTP route never calls it directly.

        Atomic publish contract:
        1. Compose the dashboard summary.
        2. Construct + validate the projection envelope (DashboardProjection).
        3. Serialize to JSON (verify it round-trips via model_dump_json).
        4. Persist to disk atomically (write-then-rename).
        5. ONLY THEN publish to self._projection in memory.

        On ANY failure (compose, validation, serialization, or disk
        persistence), the exact former last-known-good in-memory projection
        AND the durable sidecar are preserved. Never update in-memory before
        disk persistence succeeds.
        """
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
            await asyncio.to_thread(self.persist, projection)
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
        self, db, kb_store, teams, loop: asyncio.AbstractEventLoop | None = None,
    ) -> asyncio.Task:
        """Create and return the scheduler background task.

        Must be called from within a running event loop.
        Once started, the task runs until cancelled.
        """
        if loop is None:
            loop = asyncio.get_running_loop()
        self._shutdown_event = asyncio.Event()
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
