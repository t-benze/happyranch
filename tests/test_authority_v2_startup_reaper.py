"""THR-229 TASK-8766 startup refusal discovery and zombie CAS callers.

These cases drive the production startup/reaper entries over real SQLite state.
They intentionally exercise the caller ordering and transaction predicates, not
helper-only replicas.  Broad ``tests/integration`` remains skipped under
THR-243 seq42.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from runtime.daemon.__main__ import _sweep_on_startup
from runtime.daemon.queue import TaskQueue
from runtime.daemon.zombie_reaper import (
    FLAG_TTL_NO_FINGERPRINT_SECONDS,
    STALE_HEARTBEAT_SECONDS,
    _consume_zombie_fingerprint,
    _sweep_org_zombies,
)
from runtime.infrastructure.database import Database
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.models import TaskRecord, TaskStatus
from runtime.orchestrator.active_authority_policy import (
    SESSION_POLICY_BINDING_ACTION,
)
from runtime.orchestrator.authority_policy_store import AuthorityPolicyStore
from tests.test_authority_v2_attempt_admission import _seed_bound_task, _store
from tests.test_authority_v2_evaluation_stage import (
    _admitted,
    _audit_consumption,
    _audit_evaluation,
    _claim,
    _claim_audit,
    _consume,
    _evaluate,
)
from tests.test_authority_v2_hook import (
    _log_ordinary_completion,
    _orch,
)


class _CommitFailingConn:
    """Inject one transaction-boundary failure without changing production."""

    def __init__(self, real):
        self._real = real
        self._failed = False

    def commit(self):
        if not self._failed:
            self._failed = True
            raise RuntimeError("injected commit failure")
        return self._real.commit()

    def __getattr__(self, name):
        return getattr(self._real, name)


def _drive_stage(store, row, attempt, stage: str) -> None:
    if stage == "admitted":
        return
    assert _claim(store, row, attempt).status == "claimed"
    if stage == "claimed":
        return
    assert _claim_audit(store, row, attempt).status == "claim_audited"
    if stage == "claim_audited":
        return
    assert _evaluate(store, row, attempt).status == "evaluated"
    if stage == "evaluated":
        return
    assert _audit_evaluation(store, row, attempt).status == "evaluation_audited"
    if stage == "evaluation_audited":
        return
    assert _consume(store, row, attempt).status == "consumed"
    if stage == "consumed":
        return
    assert _audit_consumption(store, row, attempt).status == "consumed_audited"


@pytest.mark.parametrize(
    ("stage", "expected_code"),
    [
        ("admitted", "interrupted_pre_final"),
        ("claimed", "claim_audit_missing"),
        ("claim_audited", "evaluation_failed"),
        ("evaluated", "evaluation_audit_missing"),
        ("evaluation_audited", "consume_failed"),
        ("consumed", "consume_audit_missing"),
        ("consumed_audited", "final_commit_failed"),
    ],
)
def test_startup_refuses_every_old_boot_pre_final_stage_before_other_recovery(
    tmp_path, stage, expected_code,
):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    _drive_stage(store, row, attempt, stage)
    store.bind_v2_process_boot_id("boot-after-restart")
    store._db._v2_live_attempt_owners.clear()

    queue = TaskQueue()
    _sweep_on_startup(store._db, queue, "test")

    task = store._db.get_task(attempt.root_task_id)
    final = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert task.status is TaskStatus.ESCALATED
    assert final.finalization_state == "refused"
    assert final.stage == stage
    assert final.refusal_code == expected_code
    assert queue._queue.empty()


def test_startup_does_not_steal_same_boot_live_owner(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id(attempt.origin_boot_id)

    _sweep_on_startup(store._db, TaskQueue(), "test")

    task = store._db.get_task(attempt.root_task_id)
    final = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert task.status is TaskStatus.IN_PROGRESS
    assert final.finalization_state == "unfinalized"
    assert _claim(store, row, attempt).status == "claimed"


def test_startup_refusal_write_failure_preserves_residue_and_fences_later_branches(
    tmp_path, monkeypatch,
):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id("boot-after-restart")
    store._db._v2_live_attempt_owners.clear()

    def fail(*args, **kwargs):
        raise RuntimeError("refusal audit unavailable")

    monkeypatch.setattr(
        AuthorityPolicyStore, "finalize_v2_attempt_refusal", fail,
    )
    _sweep_on_startup(store._db, TaskQueue(), "test")

    task = store._db.get_task(attempt.root_task_id)
    final = store._db.get_authority_policy_v2_attempt_for_result(row["id"])
    assert task.status is TaskStatus.IN_PROGRESS
    assert final.finalization_state == "unfinalized"
    assert store.list_v2_unfinalized_attempts()[0].attempt_id == attempt.attempt_id


def test_startup_refusal_commit_failure_reopens_for_refusal_only_retry(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id("boot-after-restart")
    store._db._v2_live_attempt_owners.clear()
    real = store._db._conn
    store._db._conn = _CommitFailingConn(real)
    try:
        _sweep_on_startup(store._db, TaskQueue(), "test")
    finally:
        store._db._conn = real

    assert store._db.get_task(attempt.root_task_id).status is TaskStatus.IN_PROGRESS
    assert store._db.get_authority_policy_v2_attempt_for_result(
        row["id"]
    ).finalization_state == "unfinalized"

    reopened = AuthorityPolicyStore(Database(tmp_path / "c2.db"))
    reopened.bind_v2_process_boot_id("boot-another-restart")
    _sweep_on_startup(reopened._db, TaskQueue(), "test")
    assert reopened._db.get_task(attempt.root_task_id).status is TaskStatus.ESCALATED
    assert reopened._db.get_authority_policy_v2_attempt_for_result(
        row["id"]
    ).refusal_code == "interrupted_pre_final"


def _flag_v2_zombie(store, *, age: int) -> tuple[datetime, object]:
    task = store._db.get_task("TASK-C2")
    now = datetime.now(timezone.utc)
    flag = now - timedelta(seconds=age)
    store._db.update_task(
        task.id,
        last_heartbeat=(now - timedelta(seconds=STALE_HEARTBEAT_SECONDS + 10)).isoformat(),
        executor_pid=99999,
        zombie_flagged_at=flag.isoformat(),
    )
    return now, store._db.get_task(task.id)


def _reaper_orch(store):
    orch = _orch(store)
    orch._audit = AuditLogger(store._db)
    orch._parse_next_step = lambda report: report.decision
    return orch


def test_v2_result_present_uses_real_consumer_then_exact_marker_clear(tmp_path):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(store._db, row["id"])
    now, _ = _flag_v2_zombie(store, age=10)

    _sweep_org_zombies(
        store._db, now=now, uptime=999, warm_up_seconds=0,
        orchestrator=_reaper_orch(store),
    )

    task = store._db.get_task(attempt.root_task_id)
    assert task.status is TaskStatus.PENDING
    assert task.zombie_flagged_at is None
    assert [a["action"] for a in store._db.get_audit_logs(task.id)].count(
        "zombie_cleared"
    ) == 1

    # Replay/reopen cannot run a second evaluation, mint or enqueue: the root
    # is no longer an in_progress zombie and the exact clear receipt is single.
    before = {
        table: store._db._conn.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0]
        for table in (
            "authority_policy_v2_candidates",
            "authority_policy_v2_evaluations",
            "authority_policy_v2_continue_envelopes",
            "authority_policy_v2_recovery_notifications",
        )
    }
    _sweep_org_zombies(
        store._db, now=now, uptime=999, warm_up_seconds=0,
        orchestrator=_reaper_orch(store),
    )
    after = {
        table: store._db._conn.execute(
            f'SELECT COUNT(*) FROM "{table}"'
        ).fetchone()[0]
        for table in before
    }
    assert after == before
    assert [a["action"] for a in store._db.get_audit_logs(task.id)].count(
        "zombie_cleared"
    ) == 1


def test_v2_result_present_marker_race_preserves_winner(tmp_path, monkeypatch):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(store._db, row["id"])
    now, selected = _flag_v2_zombie(store, age=10)
    replacement_marker = (now - timedelta(seconds=3)).isoformat()

    import runtime.orchestrator.run_step as run_step
    real_consumer = run_step._consume_completion_report

    def consume_then_replace(*args, **kwargs):
        real_consumer(*args, **kwargs)
        store._db.update_task(attempt.root_task_id, zombie_flagged_at=replacement_marker)

    monkeypatch.setattr(run_step, "_consume_completion_report", consume_then_replace)
    _sweep_org_zombies(
        store._db, now=now, uptime=999, warm_up_seconds=0,
        orchestrator=_reaper_orch(store),
    )

    task = store._db.get_task(attempt.root_task_id)
    assert task.status is TaskStatus.PENDING
    assert task.zombie_flagged_at.isoformat() == replacement_marker
    assert not [
        a for a in store._db.get_audit_logs(task.id)
        if a["action"] == "zombie_cleared"
    ]


@pytest.mark.parametrize(
    "winner",
    ["session", "agent", "cancel", "status", "result"],
)
def test_v2_result_present_clear_cas_preserves_identity_race(
    tmp_path, winner,
):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(store._db, row["id"])
    _, selected = _flag_v2_zombie(store, age=10)
    _consume_zombie_fingerprint(
        store._db, attempt.root_task_id, row, selected, _reaper_orch(store),
    )
    marker = selected.zombie_flagged_at
    if winner == "session":
        store._db.update_task(attempt.root_task_id, current_session_id="sess-new")
    elif winner == "agent":
        store._db.update_task(attempt.root_task_id, assigned_agent="other-agent")
    elif winner == "cancel":
        store._db.update_task(
            attempt.root_task_id, cancelled_at="2026-01-01T00:00:00+00:00",
        )
    elif winner == "status":
        store._db.update_task(attempt.root_task_id, status=TaskStatus.COMPLETED)
    else:
        store._db._conn.execute(
            "UPDATE task_results SET session_id='sess-mutated' WHERE id=?",
            (row["id"],),
        )
        store._db._conn.commit()

    assert store._db.consume_v2_fingerprint_and_clear_zombie(
        task_id=attempt.root_task_id, expected_agent="engineering_manager",
        expected_session_id="sess-c2", result_id=row["id"],
        expected_zombie_flagged_at=marker,
    ) is False
    assert store._db.get_task(attempt.root_task_id).zombie_flagged_at == marker


def test_v2_result_present_clear_audit_failure_rolls_back_and_reopen_retries(
    tmp_path, monkeypatch,
):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(store._db, row["id"])
    _, selected = _flag_v2_zombie(store, age=10)
    _consume_zombie_fingerprint(
        store._db, attempt.root_task_id, row, selected, _reaper_orch(store),
    )
    marker = selected.zombie_flagged_at
    original = store._db.insert_audit_log_uncommitted

    def fail(*args, **kwargs):
        raise RuntimeError("clear audit unavailable")

    monkeypatch.setattr(store._db, "insert_audit_log_uncommitted", fail)
    with pytest.raises(RuntimeError):
        store._db.consume_v2_fingerprint_and_clear_zombie(
            task_id=attempt.root_task_id, expected_agent="engineering_manager",
            expected_session_id="sess-c2", result_id=row["id"],
            expected_zombie_flagged_at=marker,
        )
    monkeypatch.setattr(store._db, "insert_audit_log_uncommitted", original)
    assert store._db.get_task(attempt.root_task_id).zombie_flagged_at == marker

    reopened = Database(tmp_path / "c2.db")
    assert reopened.consume_v2_fingerprint_and_clear_zombie(
        task_id=attempt.root_task_id, expected_agent="engineering_manager",
        expected_session_id="sess-c2", result_id=row["id"],
        expected_zombie_flagged_at=marker,
    ) is True
    assert reopened.get_task(attempt.root_task_id).zombie_flagged_at is None


def test_v2_result_present_clear_commit_failure_rolls_back_and_reopen_retries(
    tmp_path,
):
    store, _, _, _, row, attempt = _admitted(tmp_path)
    store.bind_v2_process_boot_id(attempt.origin_boot_id)
    _log_ordinary_completion(store._db, row["id"])
    _, selected = _flag_v2_zombie(store, age=10)
    _consume_zombie_fingerprint(
        store._db, attempt.root_task_id, row, selected, _reaper_orch(store),
    )
    marker = selected.zombie_flagged_at
    real = store._db._conn
    store._db._conn = _CommitFailingConn(real)
    try:
        with pytest.raises(RuntimeError, match="commit failure"):
            store._db.consume_v2_fingerprint_and_clear_zombie(
                task_id=attempt.root_task_id,
                expected_agent="engineering_manager",
                expected_session_id="sess-c2", result_id=row["id"],
                expected_zombie_flagged_at=marker,
            )
    finally:
        store._db._conn = real
    assert store._db.get_task(attempt.root_task_id).zombie_flagged_at == marker

    reopened = Database(tmp_path / "c2.db")
    assert reopened.consume_v2_fingerprint_and_clear_zombie(
        task_id=attempt.root_task_id, expected_agent="engineering_manager",
        expected_session_id="sess-c2", result_id=row["id"],
        expected_zombie_flagged_at=marker,
    ) is True


def test_v2_result_absent_ttl_cas_denies_result_appearing_after_selection(
    tmp_path, monkeypatch,
):
    store = _store(tmp_path)
    _seed_bound_task(store)
    now, selected = _flag_v2_zombie(
        store, age=FLAG_TTL_NO_FINGERPRINT_SECONDS + 5,
    )
    second = Database(tmp_path / "c2.db")
    original = store._db.cancel_zombie_without_fingerprint

    def result_wins(**kwargs):
        second.insert_task_result(
            task_id="TASK-C2", agent="engineering_manager",
            session_id="sess-c2", status="completed", confidence_score=90,
            output_summary="late result",
        )
        return original(**kwargs)

    monkeypatch.setattr(store._db, "cancel_zombie_without_fingerprint", result_wins)
    _sweep_org_zombies(
        store._db, now=now, uptime=999, warm_up_seconds=0,
        orchestrator=_reaper_orch(store),
    )

    task = store._db.get_task("TASK-C2")
    assert task.status is TaskStatus.IN_PROGRESS
    assert task.zombie_flagged_at == selected.zombie_flagged_at
    assert not [
        a for a in store._db.get_audit_logs(task.id)
        if a["action"] == "zombie_cancelled"
    ]


def test_result_absent_malformed_session_binding_cannot_use_legacy_ttl_fallback(
    tmp_path,
):
    store = _store(tmp_path)
    db = store._db
    task_id = "TASK-MALFORMED-BINDING"
    session_id = "sess-malformed-binding"
    now = datetime.now(timezone.utc)
    marker = now - timedelta(seconds=FLAG_TTL_NO_FINGERPRINT_SECONDS + 5)
    db.insert_task(TaskRecord(
        id=task_id, brief="malformed binding", team="engineering",
        assigned_agent="engineering_manager", status=TaskStatus.IN_PROGRESS,
    ))
    db.update_task(
        task_id, current_session_id=session_id, executor_pid=99999,
        last_heartbeat=(
            now - timedelta(seconds=STALE_HEARTBEAT_SECONDS + 10)
        ).isoformat(),
        zombie_flagged_at=marker.isoformat(),
    )
    db.insert_audit_log(
        task_id, "engineering_manager", SESSION_POLICY_BINDING_ACTION,
        {"session_id": session_id, "mode": "unknown-family"},
    )

    _sweep_org_zombies(
        db, now=now, uptime=999, warm_up_seconds=0,
        orchestrator=_reaper_orch(store),
    )

    task = db.get_task(task_id)
    assert task.status is TaskStatus.IN_PROGRESS
    assert task.zombie_flagged_at == marker
    assert not [
        a for a in db.get_audit_logs(task_id)
        if a["action"] == "zombie_cancelled"
    ]


def test_v2_result_absent_ttl_cas_commits_before_single_parent_wake(
    tmp_path, monkeypatch,
):
    store = _store(tmp_path)
    _seed_bound_task(store)
    now, _ = _flag_v2_zombie(
        store, age=FLAG_TTL_NO_FINGERPRINT_SECONDS + 5,
    )
    wakes: list[str] = []
    monkeypatch.setattr(
        "runtime.orchestrator.run_step._enqueue_parent_if_waiting",
        lambda orch, task_id: wakes.append(task_id),
    )

    _sweep_org_zombies(
        store._db, now=now, uptime=999, warm_up_seconds=0,
        orchestrator=_reaper_orch(store),
    )
    _sweep_org_zombies(
        store._db, now=now, uptime=999, warm_up_seconds=0,
        orchestrator=_reaper_orch(store),
    )

    task = store._db.get_task("TASK-C2")
    assert task.status is TaskStatus.CANCELLED
    assert wakes == ["TASK-C2"]
    assert [a["action"] for a in store._db.get_audit_logs(task.id)].count(
        "zombie_cancelled"
    ) == 1


@pytest.mark.parametrize(
    "winner",
    ["marker", "session", "status", "block", "cancel"],
)
def test_v2_result_absent_ttl_cas_preserves_winning_task_race(tmp_path, winner):
    store = _store(tmp_path)
    _seed_bound_task(store)
    now, selected = _flag_v2_zombie(
        store, age=FLAG_TTL_NO_FINGERPRINT_SECONDS + 5,
    )
    if winner == "marker":
        store._db.update_task("TASK-C2", zombie_flagged_at=now.isoformat())
    elif winner == "session":
        store._db.update_task("TASK-C2", current_session_id="sess-new")
    elif winner == "status":
        store._db.update_task("TASK-C2", status=TaskStatus.PENDING)
    elif winner == "block":
        store._db.update_task("TASK-C2", block_kind="delegated")
    else:
        store._db.update_task(
            "TASK-C2", cancelled_at="2026-01-01T00:00:00+00:00",
        )

    assert store._db.cancel_zombie_without_fingerprint(
        task_id="TASK-C2", expected_agent="engineering_manager",
        expected_session_id="sess-c2",
        expected_zombie_flagged_at=selected.zombie_flagged_at,
        cancelled_at=now.isoformat(),
    ) is False
    assert store._db.get_task("TASK-C2").status is not TaskStatus.CANCELLED
    assert not [
        a for a in store._db.get_audit_logs("TASK-C2")
        if a["action"] == "zombie_cancelled"
    ]


def test_v2_result_absent_cancel_audit_failure_rolls_back_then_reopen_commits(
    tmp_path, monkeypatch,
):
    store = _store(tmp_path)
    _seed_bound_task(store)
    now, selected = _flag_v2_zombie(
        store, age=FLAG_TTL_NO_FINGERPRINT_SECONDS + 5,
    )
    original = store._db.insert_audit_log_uncommitted

    def fail(*args, **kwargs):
        raise RuntimeError("cancel audit unavailable")

    monkeypatch.setattr(store._db, "insert_audit_log_uncommitted", fail)
    with pytest.raises(RuntimeError):
        store._db.cancel_zombie_without_fingerprint(
            task_id="TASK-C2", expected_agent="engineering_manager",
            expected_session_id="sess-c2",
            expected_zombie_flagged_at=selected.zombie_flagged_at,
            cancelled_at=now.isoformat(),
        )
    monkeypatch.setattr(store._db, "insert_audit_log_uncommitted", original)
    assert store._db.get_task("TASK-C2").status is TaskStatus.IN_PROGRESS
    assert store._db.get_task("TASK-C2").zombie_flagged_at == selected.zombie_flagged_at

    reopened = Database(tmp_path / "c2.db")
    assert reopened.cancel_zombie_without_fingerprint(
        task_id="TASK-C2", expected_agent="engineering_manager",
        expected_session_id="sess-c2",
        expected_zombie_flagged_at=selected.zombie_flagged_at,
        cancelled_at=now.isoformat(),
    ) is True
    assert reopened.get_task("TASK-C2").status is TaskStatus.CANCELLED


def test_v2_result_absent_cancel_commit_failure_rolls_back_then_reopen_commits(
    tmp_path,
):
    store = _store(tmp_path)
    _seed_bound_task(store)
    now, selected = _flag_v2_zombie(
        store, age=FLAG_TTL_NO_FINGERPRINT_SECONDS + 5,
    )
    real = store._db._conn
    store._db._conn = _CommitFailingConn(real)
    try:
        with pytest.raises(RuntimeError, match="commit failure"):
            store._db.cancel_zombie_without_fingerprint(
                task_id="TASK-C2", expected_agent="engineering_manager",
                expected_session_id="sess-c2",
                expected_zombie_flagged_at=selected.zombie_flagged_at,
                cancelled_at=now.isoformat(),
            )
    finally:
        store._db._conn = real
    assert store._db.get_task("TASK-C2").status is TaskStatus.IN_PROGRESS
    assert store._db.get_task("TASK-C2").zombie_flagged_at == selected.zombie_flagged_at

    reopened = Database(tmp_path / "c2.db")
    assert reopened.cancel_zombie_without_fingerprint(
        task_id="TASK-C2", expected_agent="engineering_manager",
        expected_session_id="sess-c2",
        expected_zombie_flagged_at=selected.zombie_flagged_at,
        cancelled_at=now.isoformat(),
    ) is True
