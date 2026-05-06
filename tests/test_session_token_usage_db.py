from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.infrastructure.database import Database
from src.models import TokenUsage


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(Path(tmp) / "test.db")
        yield db


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
