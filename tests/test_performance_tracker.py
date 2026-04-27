from src.infrastructure.database import Database
from src.models import PerformanceTier, TaskRecord
from src.orchestrator.performance_tracker import PerformanceTracker


def _seed_task_results(db: Database, agent: str, outcomes: list[str]) -> None:
    """Seed task results. outcomes is a list of 'approved' or 'revised' or 'rejected'."""
    for i, outcome in enumerate(outcomes):
        task_id = f"TASK-{i+1:03d}"
        db.insert_task(TaskRecord(id=task_id, brief="test"))
        db.insert_task_result(
            task_id=task_id,
            agent=agent,
            session_id=f"sess-{i}",
            output_summary="test output",
            confidence_score=80,
            duration_seconds=60,
            token_count=1000,
            estimated_cost=0.05,
        )
        db.insert_audit_log(
            task_id=task_id,
            agent="engineering_head",
            action="review_verdict",
            payload={"verdict": outcome, "reviewed_agent": agent},
        )


def test_calculate_tier_green(db, test_settings):
    tracker = PerformanceTracker(db, test_settings)
    _seed_task_results(db, "dev_agent", ["approved"] * 9 + ["revised"])
    tier = tracker.calculate_tier("dev_agent")
    assert tier == PerformanceTier.GREEN


def test_calculate_tier_yellow(db, test_settings):
    tracker = PerformanceTracker(db, test_settings)
    _seed_task_results(db, "dev_agent", ["approved"] * 8 + ["revised"] * 2)
    tier = tracker.calculate_tier("dev_agent")
    assert tier == PerformanceTier.YELLOW


def test_calculate_tier_red(db, test_settings):
    tracker = PerformanceTracker(db, test_settings)
    _seed_task_results(db, "dev_agent", ["approved"] * 7 + ["revised"] * 3)
    tier = tracker.calculate_tier("dev_agent")
    assert tier == PerformanceTier.RED


def test_no_results_defaults_to_green(db, test_settings):
    tracker = PerformanceTracker(db, test_settings)
    tier = tracker.calculate_tier("dev_agent")
    assert tier == PerformanceTier.GREEN


def test_update_scorecard_writes_to_db(db, test_settings):
    tracker = PerformanceTracker(db, test_settings)
    _seed_task_results(db, "dev_agent", ["approved"] * 9 + ["revised"])
    tracker.update_scorecard("dev_agent")
    scorecard = db.get_scorecard("dev_agent")
    assert scorecard is not None
    assert scorecard["tier"] == "green"
    assert scorecard["acceptance_rate"] == 0.9


def test_get_all_tiers(db, test_settings):
    tracker = PerformanceTracker(db, test_settings)
    _seed_task_results(db, "dev_agent", ["approved"] * 9 + ["revised"])
    tracker.update_scorecard("dev_agent")
    tiers = tracker.get_all_tiers(["dev_agent", "product_manager"])
    assert tiers["dev_agent"] == PerformanceTier.GREEN
    assert tiers["product_manager"] == PerformanceTier.GREEN
