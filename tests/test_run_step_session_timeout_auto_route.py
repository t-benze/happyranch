"""Unit tests for the B.1 session-timeout auto-route framework change
(spec: docs/superpowers/specs/2026-05-25-session-timeout-auto-route-design.md).

Covers:
 - _classify_failure_kind table across the five canonical kinds + fallback.
 - Per-kind auto-revisit cap: same kind exhausts at 2, different kind has
   its own budget.
 - log_auto_revisit_of payload carries failure_kind at top-level.
 - Cascade-fail suppression: when a child failure spawns a root auto-revisit,
   the parent's cascade-fail Feishu notification is suppressed.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from runtime.config import Settings
from runtime.infrastructure.audit_logger import AuditLogger
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, TaskRecord, TaskStatus
from runtime.orchestrator._paths import OrgPaths
from runtime.orchestrator.teams import TeamsRegistry
from runtime.runtime import RuntimeDir


@pytest.fixture
def runtime(tmp_path: Path) -> OrgPaths:
    rt = RuntimeDir.init(tmp_path / "rt")
    paths = OrgPaths(root=rt.orgs_dir / "test")
    paths.teams_config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.teams_config_path.write_text(
        "teams:\n"
        "  engineering:\n"
        "    manager: engineering_head\n"
        "    workers: [dev_agent]\n"
    )
    return paths


@pytest.fixture
def db(runtime: OrgPaths) -> Database:
    return Database(runtime.db_path)


# ---------------------------------------------------------------------------
# _classify_failure_kind table
# ---------------------------------------------------------------------------


def _result(*, success=False, error=None, returncode=None,
            stdout_tail="", stderr_tail="", rate_limited=False):
    from runtime.orchestrator.executors import ExecutorResult
    return ExecutorResult(
        success=success, duration_seconds=1, session_id="sess",
        returncode=returncode, stdout_tail=stdout_tail,
        stderr_tail=stderr_tail, error=error, rate_limited=rate_limited,
    )


def test_classify_session_timeout():
    from runtime.orchestrator.run_step import _classify_failure_kind
    r = _result(error="Session timed out after 5400 seconds")
    assert _classify_failure_kind(r, None, mode="session_failure") == "session_timeout"


def test_classify_no_callback():
    """rc=0 (success=True) but report=None — the TASK-045 class."""
    from runtime.orchestrator.run_step import _classify_failure_kind
    r = _result(success=True, returncode=0, stdout_tail="wrote some files")
    assert _classify_failure_kind(r, None, mode="session_failure") == "no_callback"


def test_classify_rate_limit_in_stderr():
    from runtime.orchestrator.run_step import _classify_failure_kind
    r = _result(
        success=False,
        stderr_tail="Error: hit your limit · resets at 6:30pm Pacific.",
    )
    assert _classify_failure_kind(r, None, mode="session_failure") == "rate_limit"


def test_classify_rate_limit_phrase():
    """Generic 'rate limit' substring in stdout also classifies."""
    from runtime.orchestrator.run_step import _classify_failure_kind
    r = _result(
        success=False, returncode=1,
        stderr_tail="HTTP 429: rate limit exceeded for org_xyz",
    )
    assert _classify_failure_kind(r, None, mode="session_failure") == "rate_limit"


def test_classify_executor_error_nonzero_rc():
    from runtime.orchestrator.run_step import _classify_failure_kind
    r = _result(success=False, returncode=137,
                stderr_tail="killed (signal 9)")
    assert _classify_failure_kind(r, None, mode="session_failure") == "executor_error"


def test_classify_agent_exception_mode():
    from runtime.orchestrator.run_step import _classify_failure_kind
    assert _classify_failure_kind(None, None, mode="exception") == "agent_exception"


def test_classify_fallback_to_session_failed():
    """Defensive fallback when no signal matches — kind stays generic."""
    from runtime.orchestrator.run_step import _classify_failure_kind
    r = _result(success=False)   # error=None, rc=None, no diagnostics
    assert _classify_failure_kind(r, None, mode="session_failure") == "session_failed"


def test_classify_timeout_beats_rate_limit_string():
    """If both signals are present, session_timeout wins — the executor's
    own TimeoutExpired prefix is the authoritative signal."""
    from runtime.orchestrator.run_step import _classify_failure_kind
    r = _result(
        error="Session timed out after 5400 seconds",
        stdout_tail="(some hit your limit · resets text earlier in session)",
    )
    assert _classify_failure_kind(r, None, mode="session_failure") == "session_timeout"


def test_classify_prefers_rate_limited_field_without_string_signature():
    """issue #85: the normalized ExecutorResult.rate_limited field classifies as
    rate_limit even when no legacy string signature is present in the tails."""
    from runtime.orchestrator.run_step import _classify_failure_kind
    r = _result(success=False, returncode=1, stderr_tail="boom", rate_limited=True)
    assert _classify_failure_kind(r, None, mode="session_failure") == "rate_limit"


def test_classify_rate_limited_field_beats_executor_error():
    """A non-zero rc that would otherwise be 'executor_error' is classified as
    rate_limit when the normalized field is set — the field is preferred."""
    from runtime.orchestrator.run_step import _classify_failure_kind
    r = _result(success=False, returncode=137, stderr_tail="killed", rate_limited=True)
    assert _classify_failure_kind(r, None, mode="session_failure") == "rate_limit"


def test_classify_string_fallback_still_works_when_field_unset():
    """Back-compat: results that predate the field (rate_limited=False) still
    classify via the legacy stdout/stderr string heuristic."""
    from runtime.orchestrator.run_step import _classify_failure_kind
    r = _result(success=False, returncode=1,
                stderr_tail="HTTP 429: rate limit exceeded", rate_limited=False)
    assert _classify_failure_kind(r, None, mode="session_failure") == "rate_limit"


# ---------------------------------------------------------------------------
# Audit payload: failure_kind hoisted to top-level
# ---------------------------------------------------------------------------


def test_log_auto_revisit_of_includes_failure_kind(tmp_path: Path):
    db = Database(tmp_path / "g.db")
    audit = AuditLogger(db)
    audit.log_auto_revisit_of(
        task_id="T-NEW", predecessor_root="T-OLD",
        failed_task="T-CHD", failed_agent="dev_agent",
        cascade=["T-OLD", "T-CHD"],
        failure_kind="session_timeout",
        error_context={"mode": "session_failure", "rc": None},
        attempt=1,
    )
    rows = db.get_audit_logs("T-NEW")
    entry = next(r for r in rows if r["action"] == "auto_revisit_of")
    assert entry["payload"]["failure_kind"] == "session_timeout"
    # error_context still preserved alongside
    assert entry["payload"]["error_context"]["rc"] is None
    assert entry["payload"]["attempt"] == 1


# ---------------------------------------------------------------------------
# Per-kind cap counting
# ---------------------------------------------------------------------------


def test_count_prior_auto_revisits_by_kind_isolates_kinds(tmp_path: Path):
    """Two session_timeouts and one executor_error on the same chain →
    timeout count is 2, executor_error count is 1, no cross-contamination."""
    from runtime.orchestrator.run_step import _count_prior_auto_revisits_by_kind

    db = Database(tmp_path / "g.db")
    db.insert_task(TaskRecord(id="T-ROOT", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(id="T-R1", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED,
                              revisit_of_task_id="T-ROOT"))
    db.insert_task(TaskRecord(id="T-R2", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED,
                              revisit_of_task_id="T-R1"))
    db.insert_task(TaskRecord(id="T-R3", brief="b",
                              assigned_agent="engineering_head",
                              revisit_of_task_id="T-R2"))
    audit = AuditLogger(db)
    audit.log_auto_revisit_of(task_id="T-R1", predecessor_root="T-ROOT",
                              failed_task="T-ROOT", failed_agent="x",
                              cascade=["T-ROOT"], failure_kind="session_timeout",
                              error_context={}, attempt=1)
    audit.log_auto_revisit_of(task_id="T-R2", predecessor_root="T-R1",
                              failed_task="T-R1", failed_agent="x",
                              cascade=["T-R1"], failure_kind="executor_error",
                              error_context={}, attempt=1)
    audit.log_auto_revisit_of(task_id="T-R3", predecessor_root="T-R2",
                              failed_task="T-R2", failed_agent="x",
                              cascade=["T-R2"], failure_kind="session_timeout",
                              error_context={}, attempt=2)

    orch = MagicMock()
    orch._db = db
    # The chain should be walked from any leaf in the lineage; choose the
    # newest as the anchor (mirrors how _maybe_spawn_auto_revisit calls it).
    assert _count_prior_auto_revisits_by_kind(orch, "T-R3", "session_timeout") == 2
    assert _count_prior_auto_revisits_by_kind(orch, "T-R3", "executor_error") == 1
    assert _count_prior_auto_revisits_by_kind(orch, "T-R3", "rate_limit") == 0


def test_count_walks_past_old_20_hop_truncation_window(tmp_path: Path):
    """Codex P2 regression: the old code used walk_revisit_chain(truncate=True)
    which silently capped at 20 hops. Founder revisits don't count against
    the kind cap but DO consume hops, so on long-lived tasks older auto_revisit
    entries could fall out of the count window and the per-kind cap would be
    silently exceeded. After the fix the counter must see every same-kind
    auto-revisit, even when the chain exceeds 20 entries."""
    from runtime.orchestrator.run_step import _count_prior_auto_revisits_by_kind

    db = Database(tmp_path / "g.db")
    # Build a 25-entry chain (5 beyond the old 20-hop default).
    # The OLDEST entry (idx 24) carries a session_timeout auto-revisit;
    # the rest are founder revisits that consume hops without counting.
    prev_id = None
    for i in range(24, -1, -1):
        tid = f"T-{i:02d}"
        db.insert_task(TaskRecord(
            id=tid, brief="b", assigned_agent="engineering_head",
            status=TaskStatus.FAILED if i > 0 else TaskStatus.PENDING,
            revisit_of_task_id=prev_id,
        ))
        prev_id = tid
    # Now T-00 is the most recent; T-24 is the oldest. Hop count from T-00
    # back to T-24 is 25, well past the old default of 20.
    audit = AuditLogger(db)
    # Plant ONE same-kind auto_revisit at the very oldest entry — under the
    # old truncation it would never be seen.
    audit.log_auto_revisit_of(
        task_id="T-24", predecessor_root="(synthetic)",
        failed_task="(synthetic)", failed_agent="x",
        cascade=[], failure_kind="session_timeout",
        error_context={}, attempt=1,
    )
    # Plant a same-kind row at an intermediate entry (idx 22) for good measure.
    audit.log_auto_revisit_of(
        task_id="T-22", predecessor_root="T-24",
        failed_task="T-24", failed_agent="x",
        cascade=[], failure_kind="session_timeout",
        error_context={}, attempt=2,
    )

    orch = MagicMock()
    orch._db = db
    # The post-fix counter walks the full chain (max_hops=200) and sees BOTH.
    assert _count_prior_auto_revisits_by_kind(orch, "T-00", "session_timeout") == 2


def test_count_returns_cap_on_pathological_chain(tmp_path: Path):
    """If the revisit chain somehow exceeds _CHAIN_HOP_LIMIT_FOR_COUNTING
    (200), the counter must refuse to spawn (return == cap). Refusing is
    safer than silently undercounting — the cap is the contract."""
    from runtime.orchestrator.run_step import (
        _AUTO_REVISIT_CAP_PER_KIND,
        _count_prior_auto_revisits_by_kind,
    )

    db = MagicMock()
    # Simulate walk_revisit_chain raising LineageTooDeep at the call site.
    from runtime.infrastructure.database import LineageTooDeep
    db.walk_revisit_chain.side_effect = LineageTooDeep("chain too deep")

    orch = MagicMock()
    orch._db = db

    result = _count_prior_auto_revisits_by_kind(
        orch, "T-DEEP", "session_timeout",
    )
    assert result == _AUTO_REVISIT_CAP_PER_KIND
    # And the count was attempted with truncate=False (so the exception fired
    # instead of being swallowed by the chain walker).
    _, kwargs = db.walk_revisit_chain.call_args
    assert kwargs["truncate"] is False
    assert kwargs["max_hops"] >= 200


def test_count_ignores_pre_spec_audit_rows_without_failure_kind(tmp_path: Path):
    """Auto-revisit rows written by pre-B.1 code have no failure_kind in
    payload — they must count as 0 against every kind. Spec §10."""
    from runtime.orchestrator.run_step import _count_prior_auto_revisits_by_kind

    db = Database(tmp_path / "g.db")
    db.insert_task(TaskRecord(id="T-ROOT", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(id="T-R1", brief="b",
                              assigned_agent="engineering_head",
                              revisit_of_task_id="T-ROOT"))
    # Synthesize the pre-spec payload shape directly via insert_audit_log
    # since the modern log_auto_revisit_of requires failure_kind.
    db.insert_audit_log(
        task_id="T-R1", agent="orchestrator", action="auto_revisit_of",
        payload={
            "predecessor_root": "T-ROOT", "failed_task": "T-ROOT",
            "failed_agent": "x", "cascade": ["T-ROOT"],
            # No "failure_kind" key — legacy shape.
            "error_context": {"mode": "session_failure"},
            "attempt": 1,
        },
    )

    orch = MagicMock()
    orch._db = db
    assert _count_prior_auto_revisits_by_kind(orch, "T-R1", "session_timeout") == 0
    assert _count_prior_auto_revisits_by_kind(orch, "T-R1", "session_failed") == 0


# ---------------------------------------------------------------------------
# Per-kind cap: same kind exhausts at 2; different kind still has budget.
# Test fixtures inline a minimal Orchestrator (no team manager prompt loading).
# ---------------------------------------------------------------------------


class _SlugQueue:
    def __init__(self):
        import asyncio
        self._q = asyncio.Queue()

    def put_nowait(self, slug, task_id):
        self._q.put_nowait((slug, task_id))

    def qsize(self):
        return self._q.qsize()

    def get_nowait(self):
        return self._q.get_nowait()


def test_per_kind_cap_blocks_third_same_kind(runtime, db):
    """Two prior session_timeout auto-revisits → third session_timeout
    attempt is refused (returns False)."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _maybe_spawn_auto_revisit

    db.insert_task(TaskRecord(id="T-ROOT", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(id="T-R1", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED,
                              revisit_of_task_id="T-ROOT"))
    db.insert_task(TaskRecord(id="T-R2", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED,
                              revisit_of_task_id="T-R1"))
    audit = AuditLogger(db)
    audit.log_auto_revisit_of(task_id="T-R1", predecessor_root="T-ROOT",
                              failed_task="T-ROOT", failed_agent="x",
                              cascade=["T-ROOT"], failure_kind="session_timeout",
                              error_context={}, attempt=1)
    audit.log_auto_revisit_of(task_id="T-R2", predecessor_root="T-R1",
                              failed_task="T-R1", failed_agent="x",
                              cascade=["T-R1"], failure_kind="session_timeout",
                              error_context={}, attempt=2)

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    spawned = _maybe_spawn_auto_revisit(
        orch, "T-R2", "engineering_head",
        failure_kind="session_timeout",
        error_context={"mode": "session_failure",
                       "executor_error": "Session timed out after 5400 seconds"},
    )
    assert spawned is False
    assert orch._queue.qsize() == 0


def test_per_kind_cap_admits_different_kind_after_session_timeout(runtime, db):
    """Two prior session_timeouts do NOT exhaust the budget for executor_error
    — that kind has its own per-kind cap. Spec §5.1 second row."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _maybe_spawn_auto_revisit

    db.insert_task(TaskRecord(id="T-ROOT", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(id="T-R1", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED,
                              revisit_of_task_id="T-ROOT"))
    db.insert_task(TaskRecord(id="T-R2", brief="b",
                              assigned_agent="engineering_head",
                              status=TaskStatus.FAILED,
                              revisit_of_task_id="T-R1"))
    audit = AuditLogger(db)
    audit.log_auto_revisit_of(task_id="T-R1", predecessor_root="T-ROOT",
                              failed_task="T-ROOT", failed_agent="x",
                              cascade=["T-ROOT"], failure_kind="session_timeout",
                              error_context={}, attempt=1)
    audit.log_auto_revisit_of(task_id="T-R2", predecessor_root="T-R1",
                              failed_task="T-R1", failed_agent="x",
                              cascade=["T-R1"], failure_kind="session_timeout",
                              error_context={}, attempt=2)

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    # A NEW executor_error failure should still auto-revisit — it has its
    # own per-kind cap, unaffected by the exhausted session_timeout count.
    spawned = _maybe_spawn_auto_revisit(
        orch, "T-R2", "engineering_head",
        failure_kind="executor_error",
        error_context={"mode": "session_failure", "rc": 137},
    )
    assert spawned is True
    # The new auto-revisit is enqueued AND tagged executor_error.
    slug, new_root = orch._queue.get_nowait()
    assert slug == "test"
    rows = db.get_audit_logs(new_root)
    auto_entry = next(r for r in rows if r["action"] == "auto_revisit_of")
    assert auto_entry["payload"]["failure_kind"] == "executor_error"
    assert auto_entry["payload"]["attempt"] == 1


# ---------------------------------------------------------------------------
# Cascade-fail suppression: an auto-revisit at the root must silence
# cascade_fail Feishu notifications at every ancestor in the lineage.
# ---------------------------------------------------------------------------


def test_cascade_fail_suppressed_when_root_auto_revisit_spawned(runtime, db):
    """TASK-573 bounded failure-recovery: when a subtask fails and the root
    auto-revisit fires, the intermediate parent gets a bounded-wake decision
    step (in_progress+delegated, enqueued). The Feishu notification path is
    removed; no cascade-fail happens."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    db.insert_task(TaskRecord(id="T-ROOT", brief="root", team="engineering",
                              assigned_agent="engineering_head",
                              status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
                              task_type="task"))
    db.insert_task(TaskRecord(id="T-MID", brief="mid", team="engineering",
                              assigned_agent="engineering_head",
                              parent_task_id="T-ROOT",
                              status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
                              task_type="task"))
    db.insert_task(TaskRecord(id="T-CHD", brief="chd", team="engineering",
                              assigned_agent="dev_agent",
                              parent_task_id="T-MID",
                              status=TaskStatus.FAILED,
                              note="agent session failed (rc=?; Session timed out after 5400 seconds)",
                              task_type="subtask"))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    notify_calls: list[dict] = []

    def stub_notify_failed(**kw):
        notify_calls.append(kw)

    orch.notify_failed = stub_notify_failed   # type: ignore[assignment]

    # Simulate the run_step caller having spawned a root auto-revisit.
    _enqueue_parent_if_waiting(
        orch, "T-CHD", root_auto_revisit_spawned=True,
    )

    # T-MID stays in_progress(delegated) — bounded manager-wake (TASK-573).
    assert db.get_task("T-MID").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-MID").block_kind == BlockKind.DELEGATED
    # T-ROOT stays in_progress(delegated) — not reachable until T-MID advances.
    assert db.get_task("T-ROOT").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-ROOT").block_kind == BlockKind.DELEGATED
    # ZERO Feishu notifications.
    assert notify_calls == []


def test_cascade_fail_no_auto_revisit(runtime, db):
    """TASK-573: when no auto-revisit was spawned (e.g., a deliberate
    self-block), the parent gets a bounded-wake decision step — stays
    in_progress(delegated) and is enqueued, NOT cascade-failed."""
    from runtime.orchestrator.orchestrator import Orchestrator
    from runtime.orchestrator.run_step import _enqueue_parent_if_waiting

    db.insert_task(TaskRecord(id="T-ROOT", brief="root", team="engineering",
                              assigned_agent="engineering_head",
                              status=TaskStatus.IN_PROGRESS, block_kind=BlockKind.DELEGATED,
                              task_type="task"))
    db.insert_task(TaskRecord(id="T-CHD", brief="chd", team="engineering",
                              assigned_agent="dev_agent",
                              parent_task_id="T-ROOT",
                              status=TaskStatus.FAILED,
                              note="self-blocked: missing API key",
                              task_type="subtask"))

    orch = Orchestrator(db=db, settings=Settings(), paths=runtime,
                        slug="test", teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    notify_calls: list[dict] = []
    orch.notify_failed = lambda **kw: notify_calls.append(kw)   # type: ignore[assignment]

    _enqueue_parent_if_waiting(
        orch, "T-CHD", root_auto_revisit_spawned=False,
    )

    # Parent stays in_progress(delegated) — bounded-wake (TASK-573), not cascade-fail.
    assert db.get_task("T-ROOT").status == TaskStatus.IN_PROGRESS
    assert db.get_task("T-ROOT").block_kind == BlockKind.DELEGATED
    # No notification fires — Feishu removed.
    assert len(notify_calls) == 0
