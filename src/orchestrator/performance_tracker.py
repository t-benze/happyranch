from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.config import Settings
from src.infrastructure.database import Database
from src.models import PerformanceTier


class PerformanceTracker:
    def __init__(self, db: Database, settings: Settings) -> None:
        self._db = db
        self._settings = settings

    def calculate_tier(self, agent: str) -> PerformanceTier:
        """Calculate performance tier based on review verdicts in the last 30 days."""
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        logs = self._db.get_audit_logs_by_action("review_verdict", since=since)
        verdicts = [
            log for log in logs
            if log.get("payload", {}).get("reviewed_agent") == agent
        ]
        if not verdicts:
            return PerformanceTier.GREEN

        approved = sum(
            1 for v in verdicts if v["payload"]["verdict"] == "approved"
        )
        total = len(verdicts)
        acceptance_rate = approved / total

        if acceptance_rate >= self._settings.tier_green_threshold:
            return PerformanceTier.GREEN
        elif acceptance_rate >= self._settings.tier_yellow_threshold:
            return PerformanceTier.YELLOW
        else:
            return PerformanceTier.RED

    def _compute_rates(self, agent: str) -> tuple[float, float, int]:
        """Return (acceptance_rate, revision_rate, error_count)."""
        since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        logs = self._db.get_audit_logs_by_action("review_verdict", since=since)
        verdicts = [
            log for log in logs
            if log.get("payload", {}).get("reviewed_agent") == agent
        ]
        if not verdicts:
            return 1.0, 0.0, 0

        total = len(verdicts)
        approved = sum(1 for v in verdicts if v["payload"]["verdict"] == "approved")
        revised = sum(1 for v in verdicts if v["payload"]["verdict"] == "revised")
        rejected = sum(1 for v in verdicts if v["payload"]["verdict"] == "rejected")

        acceptance_rate = approved / total if total else 1.0
        revision_rate = revised / total if total else 0.0
        return acceptance_rate, revision_rate, rejected

    def update_scorecard(self, agent: str) -> None:
        """Recalculate and persist scorecard for an agent."""
        acceptance_rate, revision_rate, error_count = self._compute_rates(agent)
        tier = self.calculate_tier(agent)
        now = datetime.now(timezone.utc)
        period_start = (now - timedelta(days=30)).isoformat()
        period_end = now.isoformat()
        self._db.upsert_scorecard(
            agent=agent,
            period_start=period_start,
            period_end=period_end,
            acceptance_rate=round(acceptance_rate, 4),
            revision_rate=round(revision_rate, 4),
            error_count=error_count,
            tier=tier.value,
        )

    def get_all_tiers(self, agent_names: list[str]) -> dict[str, PerformanceTier]:
        """Get current tier for a list of agents."""
        tiers: dict[str, PerformanceTier] = {}
        for agent in agent_names:
            scorecard = self._db.get_scorecard(agent)
            if scorecard:
                tiers[agent] = PerformanceTier(scorecard["tier"])
            else:
                tiers[agent] = PerformanceTier.GREEN
        return tiers
