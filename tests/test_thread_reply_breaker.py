from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from runtime.infrastructure.database import Database
from runtime.models import ThreadRecord, ThreadMessageKind
from runtime.orchestrator.executors import ExecutorResult
from runtime.daemon.thread_runner import _qualifying_breaker_failure
from runtime.config import Settings


NOW = datetime(2026, 9, 1, 8, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("category", [
    "provider_nonzero", "provider_timeout", "post_launch_contract",
])
def test_closed_post_launch_categories_are_qualifying(category):
    result = ExecutorResult(
        success=False, returncode=1, duration_seconds=1, session_id="sess-test",
        failure_category=category, provider_launched=True,
    )
    assert _qualifying_breaker_failure(result) == category


def test_prelaunch_and_unstructured_results_are_nonqualifying():
    prelaunch = ExecutorResult(
        success=False, returncode=1, duration_seconds=0, session_id="sess-test",
        failure_category="pre_launch", provider_launched=False,
    )
    legacy = ExecutorResult(
        success=False, returncode=1, stderr_tail="provider failed",
        duration_seconds=1, session_id="sess-test",
    )
    assert _qualifying_breaker_failure(prelaunch) is None
    assert _qualifying_breaker_failure(legacy) is None


def test_breaker_policy_is_validated_configuration():
    settings = Settings()
    assert settings.thread_reply_breaker_failure_threshold == 3
    assert settings.thread_reply_breaker_cooldown_seconds == 900
    with pytest.raises(ValueError):
        Settings(thread_reply_breaker_failure_threshold=0)
    with pytest.raises(ValueError):
        Settings(thread_reply_breaker_cooldown_seconds=0)


def _db(tmp_path):
    db = Database(tmp_path / "breaker.db")
    db.insert_thread(ThreadRecord(id="THR-1", subject="breaker"))
    db.add_thread_participant("THR-1", "dev_agent", added_by="founder")
    return db


def _failure(db, token, *, now=NOW):
    return db.record_thread_reply_breaker_failure(
        thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
        invocation_token=token, failure_category="provider_nonzero",
        threshold=3, cooldown_seconds=900, now=now,
    )


def test_absent_is_closed_and_threshold_opens_at_exactly_three(tmp_path):
    db = _db(tmp_path)
    assert db.get_thread_reply_breaker("THR-1", "dev_agent", "codex:gpt-5") is None
    assert _failure(db, "tok-1").state == "closed"
    assert _failure(db, "tok-2").state == "closed"
    opened = _failure(db, "tok-3")
    assert opened.state == "open"
    assert opened.consecutive_failures == 3
    assert opened.cooldown_until == (NOW + timedelta(minutes=15)).isoformat()


def test_terminal_receipt_is_idempotent(tmp_path):
    db = _db(tmp_path)
    first = _failure(db, "tok-1")
    duplicate = _failure(db, "tok-1", now=NOW + timedelta(hours=1))
    assert duplicate.episode_id == first.episode_id
    assert duplicate.consecutive_failures == 1
    assert db._conn.execute(
        "SELECT COUNT(*) FROM thread_reply_breaker_receipts"
    ).fetchone()[0] == 1


def test_nonnegative_constraint_is_database_enforced(tmp_path):
    db = _db(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        db._conn.execute(
            "INSERT INTO thread_reply_breaker_episodes VALUES "
            "('THR-1','dev_agent','codex','ep','closed',-1,NULL,NULL,NULL,NULL,'x')"
        )


def test_probe_fixed_clock_boundary_and_unique_lease(tmp_path):
    db = _db(tmp_path)
    _failure(db, "tok-1")
    _failure(db, "tok-2")
    opened = _failure(db, "tok-3")
    assert db.acquire_thread_reply_breaker_probe(
        thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
        lease_id="early", now=NOW + timedelta(minutes=15) - timedelta(microseconds=1),
    ) is None
    probe = db.acquire_thread_reply_breaker_probe(
        thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
        lease_id="lease-1", now=NOW + timedelta(minutes=15),
    )
    assert probe is not None and probe.state == "probe"
    assert probe.episode_id == opened.episode_id
    assert db.acquire_thread_reply_breaker_probe(
        thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
        lease_id="lease-2", now=NOW + timedelta(minutes=16),
    ) is None


def test_probe_n_way_concurrency_has_one_winner(tmp_path):
    path = tmp_path / "race.db"
    seed = Database(path)
    seed.insert_thread(ThreadRecord(id="THR-1", subject="breaker"))
    seed.add_thread_participant("THR-1", "dev_agent", added_by="founder")
    _failure(seed, "tok-1")
    _failure(seed, "tok-2")
    _failure(seed, "tok-3")
    seed.close()

    def acquire(index):
        db = Database(path)
        try:
            return db.acquire_thread_reply_breaker_probe(
                thread_id="THR-1", agent_name="dev_agent",
                executor_key="codex:gpt-5", lease_id=f"lease-{index}",
                now=NOW + timedelta(minutes=15),
            ) is not None
        finally:
            db.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(acquire, range(8))) == 1


def test_close_rearms_and_new_failure_starts_new_episode(tmp_path):
    db = _db(tmp_path)
    old = _failure(db, "tok-1")
    assert db.close_thread_reply_breaker(
        thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
        now=NOW + timedelta(minutes=1),
    )
    fresh = _failure(db, "tok-2", now=NOW + timedelta(minutes=2))
    assert fresh.episode_id != old.episode_id
    assert fresh.consecutive_failures == 1


def test_probe_success_is_idempotent_and_stale_safe(tmp_path):
    db = _db(tmp_path)
    _failure(db, "tok-1")
    _failure(db, "tok-2")
    opened = _failure(db, "tok-3")
    probe = db.acquire_thread_reply_breaker_probe(
        thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
        lease_id="lease-current", now=NOW + timedelta(minutes=15),
    )
    assert probe is not None
    assert not db.settle_thread_reply_breaker_success(
        thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
        invocation_token="success-stale", episode_id=opened.episode_id,
        probe_lease_id="lease-stale", now=NOW + timedelta(minutes=16),
    )
    assert db.get_thread_reply_breaker(
        "THR-1", "dev_agent", "codex:gpt-5",
    ).state == "probe"
    assert db.settle_thread_reply_breaker_success(
        thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
        invocation_token="success-current", episode_id=opened.episode_id,
        probe_lease_id="lease-current", now=NOW + timedelta(minutes=16),
    )
    assert db.settle_thread_reply_breaker_success(
        thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
        invocation_token="success-current", episode_id=opened.episode_id,
        probe_lease_id="lease-current", now=NOW + timedelta(hours=1),
    )
    closed = db.get_thread_reply_breaker("THR-1", "dev_agent", "codex:gpt-5")
    assert closed.state == "closed" and closed.consecutive_failures == 0
    assert db._conn.execute(
        "SELECT COUNT(*) FROM thread_reply_breaker_receipts WHERE outcome='success'"
    ).fetchone()[0] == 1


def test_old_episode_success_cannot_close_fresh_continuity(tmp_path):
    db = _db(tmp_path)
    old = _failure(db, "old-failure")
    db.close_thread_reply_breaker(
        thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
    )
    fresh = _failure(db, "fresh-failure", now=NOW + timedelta(minutes=1))
    assert fresh.episode_id != old.episode_id
    assert not db.settle_thread_reply_breaker_success(
        thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
        invocation_token="old-success", episode_id=old.episode_id,
    )
    current = db.get_thread_reply_breaker("THR-1", "dev_agent", "codex:gpt-5")
    assert current.episode_id == fresh.episode_id
    assert current.consecutive_failures == 1


def _seed_outstanding_delivery(db):
    db.append_thread_message(
        thread_id="THR-1", speaker="founder", kind=ThreadMessageKind.MESSAGE,
        body_markdown="recover me",
    )
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id,agent_name,acknowledged_through_seq,required_through_seq,updated_at) "
        "VALUES ('THR-1','dev_agent',0,1,?)",
        (NOW.isoformat(),),
    )
    db._conn.commit()


def test_due_probe_mints_one_timer_wake_and_projects_testing(tmp_path):
    db = _db(tmp_path)
    _seed_outstanding_delivery(db)
    _failure(db, "tok-1")
    _failure(db, "tok-2")
    _failure(db, "tok-3")
    assert db.mint_due_thread_reply_breaker_probes(
        now=NOW + timedelta(minutes=15) - timedelta(microseconds=1),
    ) == []
    entries = db.mint_due_thread_reply_breaker_probes(
        now=NOW + timedelta(minutes=15),
    )
    assert len(entries) == 1 and entries[0].kind == "breaker_probe"
    assert db.mint_due_thread_reply_breaker_probes(
        now=NOW + timedelta(minutes=16),
    ) == []
    projection = db.list_reply_delivery_projections("THR-1")
    assert len(projection) == 1 and projection[0].state == "probe"


def test_due_probe_excludes_held_open_exchange(tmp_path):
    db = _db(tmp_path)
    _seed_outstanding_delivery(db)
    _failure(db, "tok-1")
    _failure(db, "tok-2")
    _failure(db, "tok-3")
    db._conn.execute(
        "INSERT INTO thread_reply_exchange "
        "(thread_id,exchange_id,state,open_seq,close_seq,opened_at,last_activity_at,deferred_count) "
        "VALUES ('THR-1',1,'open',1,1,?,?,1)",
        (NOW.isoformat(), NOW.isoformat()),
    )
    db._conn.execute(
        "INSERT INTO thread_exchange_deferrals "
        "(thread_id,exchange_id,agent_name,state,created_at) "
        "VALUES ('THR-1',1,'dev_agent','held',?)",
        (NOW.isoformat(),),
    )
    db._conn.commit()
    assert db.mint_due_thread_reply_breaker_probes(
        now=NOW + timedelta(minutes=15),
    ) == []
    assert db.list_reply_delivery_projections("THR-1")[0].state == "held"


def test_failure_settlement_and_breaker_audit_roll_back_atomically(tmp_path, monkeypatch):
    db = _db(tmp_path)
    db.append_thread_message(
        thread_id="THR-1", speaker="founder", kind=ThreadMessageKind.MESSAGE,
        body_markdown="atomic",
    )
    token = db._mint_reply_invocation_uncommitted("THR-1", "dev_agent", 1)
    db._conn.execute(
        "INSERT INTO thread_reply_delivery_state "
        "(thread_id,agent_name,acknowledged_through_seq,required_through_seq,"
        "queued_invocation_token,updated_at) VALUES ('THR-1','dev_agent',0,1,?,?)",
        (token, NOW.isoformat()),
    )
    db._conn.commit()
    assert db.claim_conversational_reply(token) is not None
    original = db.insert_audit_log_uncommitted

    def fail_breaker_audit(*args, **kwargs):
        if kwargs.get("action") == "thread_reply_breaker_opened":
            raise RuntimeError("audit write failed")
        return original(*args, **kwargs)

    monkeypatch.setattr(db, "insert_audit_log_uncommitted", fail_breaker_audit)
    with pytest.raises(RuntimeError, match="audit write failed"):
        db.settle_conversational_reply_with_breaker_failure(
            token=token, outcome="failed", decline_reason="provider failed",
            thread_id="THR-1", agent_name="dev_agent", executor_key="codex:gpt-5",
            failure_category="provider_nonzero", threshold=1,
            cooldown_seconds=900, now=NOW,
        )
    assert db.get_invocation_any_status(token).status.value == "pending"
    state = db.get_reply_delivery_state("THR-1", "dev_agent")
    assert state.running_invocation_token == token
    assert db.get_thread_reply_breaker("THR-1", "dev_agent", "codex:gpt-5") is None


def test_additive_upgrade_from_pre_breaker_store(tmp_path):
    path = tmp_path / "legacy.db"
    db = Database(path)
    db._conn.execute("DROP TABLE thread_reply_breaker_receipts")
    db._conn.execute("DROP TABLE thread_reply_breaker_episodes")
    db._conn.commit()
    db.close()
    upgraded = Database(path)
    tables = {
        row[0] for row in upgraded._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"thread_reply_breaker_episodes", "thread_reply_breaker_receipts"} <= tables
    assert upgraded.get_thread_reply_breaker("missing", "agent", "executor") is None


def test_additive_upgrade_preserves_existing_v1_episode(tmp_path):
    db = _db(tmp_path)
    episode = _failure(db, "v1-token")
    db.close()
    reopened = Database(tmp_path / "breaker.db")
    preserved = reopened.get_thread_reply_breaker("THR-1", "dev_agent", "codex:gpt-5")
    assert preserved is not None
    assert preserved.episode_id == episode.episode_id
    assert preserved.consecutive_failures == 1
