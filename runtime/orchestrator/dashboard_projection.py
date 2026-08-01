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


class DashboardProjection(BaseModel):
    """Versioned, wire-safe serialization envelope for a dashboard summary.

    Stored as a JSON file so the daemon can cold-start from persisted state.
    """
    version: int = 1
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
        """Load the persisted projection from disk. Returns None if missing
        or unparseable (corrupt file → log warning, return None)."""
        path = self.projection_path
        if not path.exists():
            return None
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            proj = DashboardProjection.model_validate(data)
            logger.debug(
                "dashboard projection loaded for org %s: generated_at=%s",
                self.org_slug, proj.generated_at.isoformat(),
            )
            return proj
        except Exception:
            logger.warning(
                "dashboard projection file for org %s is corrupt, discarding",
                self.org_slug, exc_info=True,
            )
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return None

    def persist(self, projection: DashboardProjection) -> None:
        """Atomically write the projection to disk (write-then-rename)."""
        path = self.projection_path
        tmp = path.with_suffix(path.suffix + ".tmp")
        raw = projection.model_dump_json(indent=2)
        tmp.write_text(raw, encoding="utf-8")
        tmp.rename(path)

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def warm(self, db, kb_store, teams) -> bool:
        """Perform ONE synchronous compose + persist. Called at cold-start
        and on every periodic tick. Returns True on success, False on failure.

        This is the single point where compose_dashboard_summary is called
        for the projection; the HTTP route never calls it directly.
        """
        try:
            now = datetime.now(timezone.utc)
            # Run the compose in a thread so we don't block the event loop
            # for the duration of the SQLite queries. The db._lock is a
            # threading.RLock so this is safe.
            response = await asyncio.to_thread(
                compose_dashboard_summary,
                db=db, kb_store=kb_store, teams=teams, now=now,
            )
            projection = DashboardProjection(
                org_slug=self.org_slug,
                generated_at=now,
                payload=response.model_dump(mode="json"),
            )
            self._projection = projection
            # Persist to disk (synchronous, fast — just JSON write)
            await asyncio.to_thread(self.persist, projection)
            logger.debug(
                "dashboard projection refreshed for org %s: generated_at=%s",
                self.org_slug, now.isoformat(),
            )
            return True
        except Exception:
            logger.warning(
                "dashboard projection refresh FAILED for org %s — "
                "keeping last-known-good snapshot",
                self.org_slug, exc_info=True,
            )
            return False

    async def _scheduler_loop(
        self, db, kb_store, teams, shutdown_event: asyncio.Event,
    ) -> None:
        """Coalesced periodic refresh: runs warm() every _REFRESH_INTERVAL_SECONDS.
        If a refresh is still in flight when the timer fires, skips that tick
        (no overlapping refreshes). Exits cleanly when shutdown_event is set."""
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

    async def stop_scheduler(self) -> None:
        """Signal the scheduler to stop and wait for it to finish."""
        if self._refresh_task is None:
            return
        self._shutdown_event.set()
        try:
            await self._refresh_task
        except asyncio.CancelledError:
            pass
        self._refresh_task = None

    # ── Read path (for the HTTP route) ───────────────────────────────────

    def get_projection(self) -> DashboardProjection | None:
        """Return the current in-memory projection, or None if never warmed.

        The HTTP route calls this; it never triggers a compose.
        """
        return self._projection
