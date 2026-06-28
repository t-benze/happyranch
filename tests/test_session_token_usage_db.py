from __future__ import annotations

import sqlite3

from runtime.infrastructure.database import Database
from runtime.models import TaskRecord, TaskStatus, TokenUsage


def _usage(input_tokens=100, output_tokens=50, **kw):
    return TokenUsage(input_tokens=input_tokens, output_tokens=output_tokens, **kw)


def test_insert_and_list_session_token_usage(db: Database):
    db.insert_session_token_usage(
        task_id="TASK-1", agent="dev_agent", session_id="sess-a",
        executor="claude", token_usage=_usage(input_tokens=10, output_tokens=20),
    )
    rows = db.list_session_token_usage()
    assert len(rows) == 1
    r = rows[0]
    assert r["task_id"] == "TASK-1"
    assert r["agent"] == "dev_agent"
    assert r["session_id"] == "sess-a"
    assert r["executor"] == "claude"
    assert r["input_tokens"] == 10
    assert r["output_tokens"] == 20


def test_legacy_session_token_usage_table_migrates_before_scope_indexes(tmp_path):
    db_path = tmp_path / "legacy-token-usage.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE session_token_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id    TEXT NOT NULL,
            agent      TEXT NOT NULL,
            session_id TEXT NOT NULL,
            executor   TEXT NOT NULL,
            model      TEXT,
            input_tokens          INTEGER,
            output_tokens         INTEGER,
            cache_read_tokens     INTEGER,
            cache_creation_tokens INTEGER,
            reasoning_tokens      INTEGER,
            usage_raw_json TEXT,
            created_at TEXT NOT NULL,
            UNIQUE (task_id, agent, session_id)
        );
    """)
    conn.execute(
        """INSERT INTO session_token_usage
           (task_id, agent, session_id, executor, input_tokens, created_at)
           VALUES ('TASK-1', 'dev_agent', 'sess-a', 'claude', 10, '2026-06-09T00:00:00Z')"""
    )
    conn.commit()
    conn.close()

    db = Database(db_path)

    rows = db.list_session_token_usage(task_id="TASK-1", scope_type="task")
    columns = {row["name"] for row in db._conn.execute(
        "PRAGMA table_info(session_token_usage)"
    ).fetchall()}
    indexes = {row["name"] for row in db._conn.execute(
        "PRAGMA index_list(session_token_usage)"
    ).fetchall()}

    assert rows[0]["scope_type"] == "task"
    assert rows[0]["scope_id"] == "TASK-1"
    assert {"scope_type", "scope_id", "thread_id"} <= columns
    assert "idx_session_token_usage_scope" in indexes


def test_insert_or_ignore_on_duplicate_unique_key(db: Database):
    args = dict(
        task_id="TASK-1", agent="dev_agent", session_id="sess-a", executor="claude",
    )
    db.insert_session_token_usage(**args, token_usage=_usage(input_tokens=10))
    db.insert_session_token_usage(**args, token_usage=_usage(input_tokens=999))
    rows = db.list_session_token_usage()
    assert len(rows) == 1
    assert rows[0]["input_tokens"] == 10  # first write wins (INSERT OR IGNORE)


def test_list_filters_by_task_id_and_agent(db: Database):
    db.insert_session_token_usage(
        task_id="TASK-1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10),
    )
    db.insert_session_token_usage(
        task_id="TASK-2", agent="dev", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20),
    )
    db.insert_session_token_usage(
        task_id="TASK-1", agent="qa", session_id="s3", executor="codex",
        token_usage=_usage(input_tokens=30),
    )
    assert {r["session_id"] for r in db.list_session_token_usage(task_id="TASK-1")} == {"s1", "s3"}
    assert {r["session_id"] for r in db.list_session_token_usage(agent="dev")} == {"s1", "s2"}


def test_aggregate_by_agent_sums_correctly(db: Database):
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10, output_tokens=5),
    )
    db.insert_session_token_usage(
        task_id="T2", agent="dev", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20, output_tokens=10, reasoning_tokens=3),
    )
    db.insert_session_token_usage(
        task_id="T3", agent="qa", session_id="s3", executor="codex",
        token_usage=_usage(input_tokens=100, output_tokens=50),
    )
    rollup = db.aggregate_session_token_usage_by_agent()
    by_agent = {r["agent"]: r for r in rollup}
    assert by_agent["dev"]["sessions"] == 2
    assert by_agent["dev"]["input_tokens"] == 30
    assert by_agent["dev"]["output_tokens"] == 15
    assert by_agent["dev"]["reasoning_tokens"] == 3
    assert by_agent["qa"]["sessions"] == 1
    assert by_agent["qa"]["input_tokens"] == 100


def test_aggregate_by_task_groups_per_task(db: Database):
    db.insert_session_token_usage(
        task_id="T1", agent="a", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10),
    )
    db.insert_session_token_usage(
        task_id="T1", agent="b", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20),
    )
    rollup = db.aggregate_session_token_usage_by_task()
    by_task = {r["task_id"]: r for r in rollup}
    assert by_task["T1"]["sessions"] == 2
    assert by_task["T1"]["input_tokens"] == 30


def test_round_trip_all_fields_populated(db: Database):
    u = TokenUsage(
        input_tokens=11,
        output_tokens=22,
        cache_read_tokens=33,
        cache_creation_tokens=44,
        reasoning_tokens=55,
        model="claude-sonnet-4-6",
        usage_raw_json='{"raw":"payload"}',
    )
    db.insert_session_token_usage(
        task_id="TASK-1", agent="dev_agent", session_id="sess-a",
        executor="claude", token_usage=u,
    )
    rows = db.list_session_token_usage()
    assert len(rows) == 1
    r = rows[0]
    assert r["input_tokens"] == 11
    assert r["output_tokens"] == 22
    assert r["cache_read_tokens"] == 33
    assert r["cache_creation_tokens"] == 44
    assert r["reasoning_tokens"] == 55
    assert r["model"] == "claude-sonnet-4-6"
    assert r["usage_raw_json"] == '{"raw":"payload"}'
    assert r["task_id"] == "TASK-1"
    assert r["agent"] == "dev_agent"
    assert r["session_id"] == "sess-a"
    assert r["executor"] == "claude"
    assert r["created_at"] is not None


def test_insert_thread_scoped_session_token_usage(db: Database):
    db.insert_session_token_usage(
        task_id=None,
        agent="alice",
        session_id="TOK-1",
        executor="claude",
        token_usage=_usage(input_tokens=12, output_tokens=3, model="sonnet"),
        scope_type="thread",
        scope_id="THR-001",
        thread_id="THR-001",
        invocation_purpose="reply",
    )

    rows = db.list_session_token_usage(scope_type="thread", thread_id="THR-001")

    assert len(rows) == 1
    assert rows[0]["task_id"] is None
    assert rows[0]["scope_type"] == "thread"
    assert rows[0]["scope_id"] == "THR-001"
    assert rows[0]["thread_id"] == "THR-001"
    assert rows[0]["invocation_purpose"] == "reply"
    assert rows[0]["total_tokens"] == 15


def test_existing_task_writes_default_to_task_scope(db: Database):
    db.insert_session_token_usage(
        task_id="TASK-1",
        agent="dev_agent",
        session_id="sess-a",
        executor="claude",
        token_usage=_usage(input_tokens=10, output_tokens=20),
    )

    rows = db.list_session_token_usage(task_id="TASK-1", scope_type="task")

    assert len(rows) == 1
    assert rows[0]["scope_type"] == "task"
    assert rows[0]["scope_id"] == "TASK-1"


def test_aggregate_by_thread(db: Database):
    db.insert_session_token_usage(
        task_id=None,
        agent="alice",
        session_id="thread-a",
        executor="claude",
        token_usage=_usage(input_tokens=10, output_tokens=2),
        scope_type="thread",
        scope_id="THR-001",
        thread_id="THR-001",
        invocation_purpose="bootstrap",
    )
    db.insert_session_token_usage(
        task_id=None,
        agent="bob",
        session_id="thread-b",
        executor="claude",
        token_usage=_usage(input_tokens=20, output_tokens=3),
        scope_type="thread",
        scope_id="THR-001",
        thread_id="THR-001",
        invocation_purpose="reply",
    )

    by_thread = db.aggregate_session_token_usage_by_thread()

    # Both threads here are claude with NULL model, so the null_claude_*
    # columns are real (non-deterministic) timestamps — pop and check presence,
    # then exact-compare the rest.
    [trow] = by_thread
    assert trow.pop("null_claude_min_created_at") is not None
    assert trow.pop("null_claude_max_created_at") is not None
    assert trow == {
        "thread_id": "THR-001",
        "sessions": 2,
        "input_tokens": 30,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 35,
        "churn_tokens": 35,
        "context_tokens": 35,
        "model_distinct": 0,
        "model_any": None,
        "non_null_sessions": 0,
        "null_codex_sessions": 0,
        "null_claude_sessions": 2,
    }


def test_aggregate_by_agent_filters_by_since(db: Database):
    """ISO timestamps compare lexicographically. since= filters out older rows."""
    import time
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10),
    )
    # Sleep a fraction so the second row's created_at timestamp is strictly later.
    time.sleep(0.01)
    cutoff_iso = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    time.sleep(0.01)
    db.insert_session_token_usage(
        task_id="T2", agent="dev", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=999),
    )
    rollup = db.aggregate_session_token_usage_by_agent(since=cutoff_iso)
    assert len(rollup) == 1
    assert rollup[0]["agent"] == "dev"
    assert rollup[0]["input_tokens"] == 999  # only the post-cutoff row counted


def test_aggregate_by_agent_filters_by_task_id(db: Database):
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10),
    )
    db.insert_session_token_usage(
        task_id="T2", agent="dev", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=99),
    )
    rollup = db.aggregate_session_token_usage_by_agent(task_id="T2")
    assert rollup[0]["input_tokens"] == 99


def test_aggregate_by_task_filters_by_agent(db: Database):
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10),
    )
    db.insert_session_token_usage(
        task_id="T1", agent="qa", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=99),
    )
    rollup = db.aggregate_session_token_usage_by_task(agent="qa")
    assert rollup[0]["input_tokens"] == 99


def test_aggregate_by_agent_filters_by_agent(db: Database):
    """`group_by=agent` with an `agent=` filter yields a one-row rollup for that agent."""
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10),
    )
    db.insert_session_token_usage(
        task_id="T2", agent="dev", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20),
    )
    db.insert_session_token_usage(
        task_id="T3", agent="qa", session_id="s3", executor="claude",
        token_usage=_usage(input_tokens=999),
    )
    rollup = db.aggregate_session_token_usage_by_agent(agent="dev")
    assert len(rollup) == 1
    assert rollup[0]["agent"] == "dev"
    assert rollup[0]["sessions"] == 2
    assert rollup[0]["input_tokens"] == 30


def test_aggregate_by_task_filters_by_task_id(db: Database):
    """`group_by=task` with a `task_id=` filter yields a one-row rollup for that task."""
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10),
    )
    db.insert_session_token_usage(
        task_id="T1", agent="qa", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20),
    )
    db.insert_session_token_usage(
        task_id="T2", agent="dev", session_id="s3", executor="claude",
        token_usage=_usage(input_tokens=999),
    )
    rollup = db.aggregate_session_token_usage_by_task(task_id="T1")
    assert len(rollup) == 1
    assert rollup[0]["task_id"] == "T1"
    assert rollup[0]["sessions"] == 2
    assert rollup[0]["input_tokens"] == 30


def test_insert_token_usage_supports_dream_scope(db: Database):
    db.insert_session_token_usage(
        task_id=None,
        agent="dev_agent",
        session_id="dream-session",
        executor="claude",
        token_usage=TokenUsage(input_tokens=10, output_tokens=5, model="test"),
        scope_type="dream",
        scope_id="DREAM-001",
    )

    rows = db.list_session_token_usage(scope_type="dream", scope_id="DREAM-001")
    assert len(rows) == 1
    assert rows[0]["scope_type"] == "dream"
    assert rows[0]["scope_id"] == "DREAM-001"


def test_aggregate_by_failed_task_groups_per_task_and_agent(db: Database):
    # FAILED tasks contribute; non-failed tasks (completed/blocked) are excluded.
    db.insert_task(TaskRecord(id="T-FAIL-1", brief="x", status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(id="T-FAIL-2", brief="x", status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(id="T-DONE", brief="x", status=TaskStatus.COMPLETED))
    db.insert_task(TaskRecord(id="T-BLOCKED", brief="x", status=TaskStatus.IN_PROGRESS))

    # T-FAIL-1: two agents -> two rollup rows.
    db.insert_session_token_usage(
        task_id="T-FAIL-1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10, output_tokens=5),
    )
    db.insert_session_token_usage(
        task_id="T-FAIL-1", agent="dev", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20, output_tokens=10, reasoning_tokens=3),
    )
    db.insert_session_token_usage(
        task_id="T-FAIL-1", agent="qa", session_id="s3", executor="codex",
        token_usage=_usage(input_tokens=100, output_tokens=40),
    )
    # T-FAIL-2: single agent.
    db.insert_session_token_usage(
        task_id="T-FAIL-2", agent="dev", session_id="s4", executor="claude",
        token_usage=_usage(input_tokens=7, output_tokens=3),
    )
    # Excluded: completed + blocked tasks must not appear.
    db.insert_session_token_usage(
        task_id="T-DONE", agent="dev", session_id="s5", executor="claude",
        token_usage=_usage(input_tokens=999, output_tokens=999),
    )
    db.insert_session_token_usage(
        task_id="T-BLOCKED", agent="dev", session_id="s6", executor="claude",
        token_usage=_usage(input_tokens=888, output_tokens=888),
    )

    rollup = db.aggregate_session_token_usage_by_failed_task()
    keyed = {(r["task_id"], r["agent"]): r for r in rollup}

    # Only the two failed tasks appear; non-failed excluded entirely.
    assert {r["task_id"] for r in rollup} == {"T-FAIL-1", "T-FAIL-2"}

    dev1 = keyed[("T-FAIL-1", "dev")]
    assert dev1["sessions"] == 2
    assert dev1["input_tokens"] == 30
    assert dev1["output_tokens"] == 15
    assert dev1["reasoning_tokens"] == 3
    # total = input + output + reasoning
    assert dev1["total_tokens"] == 48

    qa1 = keyed[("T-FAIL-1", "qa")]
    assert qa1["sessions"] == 1
    assert qa1["input_tokens"] == 100
    assert qa1["output_tokens"] == 40

    dev2 = keyed[("T-FAIL-2", "dev")]
    assert dev2["sessions"] == 1
    assert dev2["input_tokens"] == 7


def test_aggregate_by_failed_task_empty_when_no_failed_tasks(db: Database):
    db.insert_task(TaskRecord(id="T-DONE", brief="x", status=TaskStatus.COMPLETED))
    db.insert_session_token_usage(
        task_id="T-DONE", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10),
    )
    assert db.aggregate_session_token_usage_by_failed_task() == []


def test_aggregate_by_failed_task_filters_compose(db: Database):
    db.insert_task(TaskRecord(id="T-FAIL-1", brief="x", status=TaskStatus.FAILED))
    db.insert_task(TaskRecord(id="T-FAIL-2", brief="x", status=TaskStatus.FAILED))
    db.insert_session_token_usage(
        task_id="T-FAIL-1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10),
    )
    db.insert_session_token_usage(
        task_id="T-FAIL-2", agent="dev", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20),
    )
    # agent filter AND-composes with the failed-status JOIN.
    rollup = db.aggregate_session_token_usage_by_failed_task(task_id="T-FAIL-1")
    assert len(rollup) == 1
    assert rollup[0]["task_id"] == "T-FAIL-1"
    assert rollup[0]["agent"] == "dev"
    assert rollup[0]["input_tokens"] == 10


def test_aggregate_by_purpose_groups_and_excludes_null(db: Database):
    # Two sessions share purpose 'reply'; one 'bootstrap'; a NULL-purpose task
    # row must be EXCLUDED from the rollup.
    db.insert_session_token_usage(
        task_id=None, agent="alice", session_id="p1", executor="claude",
        token_usage=_usage(input_tokens=10, output_tokens=2),
        scope_type="thread", scope_id="THR-1", thread_id="THR-1",
        invocation_purpose="reply",
    )
    db.insert_session_token_usage(
        task_id=None, agent="bob", session_id="p2", executor="claude",
        token_usage=_usage(input_tokens=20, output_tokens=3),
        scope_type="thread", scope_id="THR-1", thread_id="THR-1",
        invocation_purpose="reply",
    )
    db.insert_session_token_usage(
        task_id=None, agent="alice", session_id="p3", executor="claude",
        token_usage=_usage(input_tokens=5, output_tokens=1),
        scope_type="thread", scope_id="THR-2", thread_id="THR-2",
        invocation_purpose="bootstrap",
    )
    # NULL invocation_purpose (a normal task row) must NOT appear.
    db.insert_session_token_usage(
        task_id="TASK-1", agent="alice", session_id="p4", executor="claude",
        token_usage=_usage(input_tokens=999, output_tokens=999),
    )

    rollup = db.aggregate_session_token_usage_by_purpose()
    keyed = {r["purpose"]: r for r in rollup}

    assert set(keyed) == {"reply", "bootstrap"}  # NULL purpose excluded
    assert keyed["reply"]["sessions"] == 2
    assert keyed["reply"]["input_tokens"] == 30
    assert keyed["reply"]["output_tokens"] == 5
    assert keyed["reply"]["total_tokens"] == 35
    assert keyed["bootstrap"]["sessions"] == 1
    assert keyed["bootstrap"]["input_tokens"] == 5
    # purpose carries NO model-classification columns (spec: purpose has no
    # Model column).
    assert "model_distinct" not in keyed["reply"]


def test_aggregate_by_purpose_filters_compose(db: Database):
    db.insert_session_token_usage(
        task_id=None, agent="alice", session_id="p1", executor="claude",
        token_usage=_usage(input_tokens=10),
        scope_type="thread", scope_id="THR-1", thread_id="THR-1",
        invocation_purpose="reply",
    )
    db.insert_session_token_usage(
        task_id=None, agent="bob", session_id="p2", executor="claude",
        token_usage=_usage(input_tokens=20),
        scope_type="thread", scope_id="THR-2", thread_id="THR-2",
        invocation_purpose="reply",
    )
    # thread_id filter AND-composes with the purpose grouping + NOT NULL.
    rollup = db.aggregate_session_token_usage_by_purpose(thread_id="THR-1")
    assert len(rollup) == 1
    assert rollup[0]["purpose"] == "reply"
    assert rollup[0]["input_tokens"] == 10


def test_aggregate_model_classification_primitives(db: Database):
    """The by-agent rollup carries the cutover-INDEPENDENT primitives a
    renderer needs to apply the spec-§2 model-name precedence. One agent per
    precedence case; we assert the PRIMITIVES, not the label (label = Leg B).
    """
    # Deterministic timestamps for the cutover-sensitive null-claude rows.
    # The cutover (MODEL_FIX_CUTOVER_TS = 2026-06-12T15:38:50Z) is a Leg-B
    # presentation constant; here PRE is before it and POST is after it.
    PRE = "2026-06-12T10:00:00+00:00"
    POST = "2026-06-12T20:00:00+00:00"

    # case 1: single non-NULL model -> resolved id
    db.insert_session_token_usage(
        task_id="T1", agent="single", session_id="c1", executor="claude",
        token_usage=_usage(model="claude-opus-4-8"),
    )
    # case 2: two distinct non-NULL models -> mixed
    db.insert_session_token_usage(
        task_id="T2", agent="two_distinct", session_id="c2a", executor="claude",
        token_usage=_usage(model="claude-opus-4-8"),
    )
    db.insert_session_token_usage(
        task_id="T2", agent="two_distinct", session_id="c2b", executor="claude",
        token_usage=_usage(model="claude-sonnet-4-6"),
    )
    # case 3: non-NULL + NULL mix -> mixed
    db.insert_session_token_usage(
        task_id="T3", agent="nonnull_plus_null", session_id="c3a", executor="codex",
        token_usage=_usage(model="gpt-5"),
    )
    db.insert_session_token_usage(
        task_id="T3", agent="nonnull_plus_null", session_id="c3b", executor="claude",
        token_usage=_usage(),  # model None
    )
    # case 4: all-NULL codex -> cli-unreported
    db.insert_session_token_usage(
        task_id="T4", agent="null_codex", session_id="c4", executor="codex",
        token_usage=_usage(),
    )
    # case 5: all-NULL claude pre-cutover -> unknown (pre-fix)
    db.insert_session_token_usage(
        task_id="T5", agent="null_claude_pre", session_id="c5", executor="claude",
        token_usage=_usage(),
    )
    # case 6: all-NULL claude post-cutover -> unknown (ANOMALY)
    db.insert_session_token_usage(
        task_id="T6", agent="null_claude_post", session_id="c6", executor="claude",
        token_usage=_usage(),
    )
    # case 7: all-NULL spanning codex+claude -> mixed
    db.insert_session_token_usage(
        task_id="T7", agent="null_mixed_exec", session_id="c7codex", executor="codex",
        token_usage=_usage(),
    )
    db.insert_session_token_usage(
        task_id="T7", agent="null_mixed_exec", session_id="c7claude", executor="claude",
        token_usage=_usage(),
    )

    # Pin created_at on the cutover-sensitive null-claude rows (the public
    # insert always stamps now(); a direct UPDATE is the test-local seam).
    db._conn.execute(
        "UPDATE session_token_usage SET created_at = ? WHERE session_id = ?",
        (PRE, "c5"),
    )
    db._conn.execute(
        "UPDATE session_token_usage SET created_at = ? WHERE session_id = ?",
        (POST, "c6"),
    )
    db._conn.commit()

    rollup = {r["agent"]: r for r in db.aggregate_session_token_usage_by_agent()}

    # case 1: distinct==1 -> renderer reads model_any as the id
    s = rollup["single"]
    assert s["model_distinct"] == 1
    assert s["model_any"] == "claude-opus-4-8"
    assert s["non_null_sessions"] == 1
    assert s["null_codex_sessions"] == 0
    assert s["null_claude_sessions"] == 0
    assert s["null_claude_min_created_at"] is None
    assert s["null_claude_max_created_at"] is None

    # case 2: >1 distinct -> mixed
    s = rollup["two_distinct"]
    assert s["model_distinct"] == 2
    assert s["non_null_sessions"] == 2
    assert s["null_codex_sessions"] == 0
    assert s["null_claude_sessions"] == 0

    # case 3: non-null present AND null present -> mixed
    s = rollup["nonnull_plus_null"]
    assert s["model_distinct"] == 1
    assert s["non_null_sessions"] == 1
    assert s["null_claude_sessions"] == 1
    assert s["null_codex_sessions"] == 0

    # case 4: all-NULL codex-only -> cli-unreported
    s = rollup["null_codex"]
    assert s["model_distinct"] == 0
    assert s["model_any"] is None
    assert s["non_null_sessions"] == 0
    assert s["null_codex_sessions"] == 1
    assert s["null_claude_sessions"] == 0
    assert s["null_claude_min_created_at"] is None
    assert s["null_claude_max_created_at"] is None

    # case 5: all-NULL claude-only, all created_at < cutover -> pre-fix
    s = rollup["null_claude_pre"]
    assert s["non_null_sessions"] == 0
    assert s["null_codex_sessions"] == 0
    assert s["null_claude_sessions"] == 1
    assert s["null_claude_min_created_at"] == PRE
    assert s["null_claude_max_created_at"] == PRE

    # case 6: all-NULL claude-only, any created_at >= cutover -> ANOMALY
    s = rollup["null_claude_post"]
    assert s["non_null_sessions"] == 0
    assert s["null_claude_sessions"] == 1
    assert s["null_claude_min_created_at"] == POST
    assert s["null_claude_max_created_at"] == POST

    # case 7: all-NULL spanning codex+claude -> mixed
    s = rollup["null_mixed_exec"]
    assert s["non_null_sessions"] == 0
    assert s["null_codex_sessions"] == 1
    assert s["null_claude_sessions"] == 1


def test_aggregate_by_model_groups_by_model_column(db: Database):
    """`aggregate_session_token_usage_by_model()` groups by the stored
    `model` column. NULL models render as a NULL-model row (honest — never
    blank / never a guessed correction)."""
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10, output_tokens=5, model="sonnet"),
    )
    db.insert_session_token_usage(
        task_id="T2", agent="dev", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20, output_tokens=10, model="sonnet"),
    )
    db.insert_session_token_usage(
        task_id="T3", agent="qa", session_id="s3", executor="codex",
        token_usage=_usage(input_tokens=100, output_tokens=50),
    )
    db.insert_session_token_usage(
        task_id="T4", agent="dev", session_id="s4", executor="claude",
        token_usage=_usage(input_tokens=40, output_tokens=20, model="opus"),
    )
    rollup = db.aggregate_session_token_usage_by_model()
    by_model = {r["model"]: r for r in rollup}
    # sonnet: 2 sessions, 30 input, 15 output
    assert by_model["sonnet"]["sessions"] == 2
    assert by_model["sonnet"]["input_tokens"] == 30
    assert by_model["sonnet"]["output_tokens"] == 15
    assert by_model["sonnet"]["total_tokens"] == 45
    # opus: 1 session
    assert by_model["opus"]["sessions"] == 1
    assert by_model["opus"]["total_tokens"] == 60
    # NULL model row: 1 session (codex, no model field ever)
    assert by_model[None]["sessions"] == 1
    assert by_model[None]["input_tokens"] == 100
    assert by_model[None]["total_tokens"] == 150


def test_aggregate_by_model_composes_with_since(db: Database):
    """Window filter AND-composes with model aggregation."""
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10, output_tokens=5, model="sonnet"),
    )
    db.insert_session_token_usage(
        task_id="T2", agent="qa", session_id="s2", executor="codex",
        token_usage=_usage(input_tokens=100, output_tokens=50),
    )
    # Filter to a future `since` — all rows created right now are before it
    from datetime import datetime, timedelta, timezone
    future = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    rollup = db.aggregate_session_token_usage_by_model(since=future)
    assert rollup == []


def test_aggregate_by_model_with_agent_filter(db: Database):
    """Agent filter AND-composes with model aggregation."""
    db.insert_session_token_usage(
        task_id="T1", agent="dev", session_id="s1", executor="claude",
        token_usage=_usage(input_tokens=10, output_tokens=5, model="sonnet"),
    )
    db.insert_session_token_usage(
        task_id="T2", agent="qa", session_id="s2", executor="claude",
        token_usage=_usage(input_tokens=20, output_tokens=10, model="sonnet"),
    )
    # Only dev_agent's sonnet session
    rollup = db.aggregate_session_token_usage_by_model(agent="dev")
    assert len(rollup) == 1
    assert rollup[0]["model"] == "sonnet"
    assert rollup[0]["sessions"] == 1
    assert rollup[0]["input_tokens"] == 10
