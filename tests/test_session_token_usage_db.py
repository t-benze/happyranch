from __future__ import annotations

from runtime.infrastructure.database import Database
from runtime.models import TokenUsage


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
    assert rows[0]["talk_id"] is None
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


def test_aggregate_by_thread_and_talk(db: Database):
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
    db.insert_session_token_usage(
        task_id="TASK-9",
        agent="alice",
        session_id="talk-task",
        executor="codex",
        token_usage=_usage(input_tokens=7, output_tokens=4),
        scope_type="task",
        scope_id="TASK-9",
        talk_id="TALK-001",
    )

    by_thread = db.aggregate_session_token_usage_by_thread()
    by_talk = db.aggregate_session_token_usage_by_talk()

    assert by_thread == [{
        "thread_id": "THR-001",
        "sessions": 2,
        "input_tokens": 30,
        "output_tokens": 5,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 35,
    }]
    assert by_talk == [{
        "talk_id": "TALK-001",
        "sessions": 1,
        "input_tokens": 7,
        "output_tokens": 4,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_tokens": 0,
        "total_tokens": 11,
    }]


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
