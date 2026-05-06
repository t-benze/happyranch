from __future__ import annotations

from src.infrastructure.audit_logger import AuditLogger
from src.infrastructure.database import Database
from src.models import TokenUsage


def _session_end_entries(db: Database, task_id: str) -> list[dict]:
    return [r for r in db.get_audit_logs(task_id) if r["action"] == "session_end"]


def test_log_session_end_without_token_usage_keeps_back_compat_shape(db: Database):
    a = AuditLogger(db)
    a.log_session_end(task_id="T1", agent="dev", duration_seconds=42)
    e = _session_end_entries(db, "T1")
    assert len(e) == 1
    payload = e[0]["payload"]
    assert payload["duration_seconds"] == 42
    assert payload["token_count"] is None
    assert "token_usage" not in payload


def test_log_session_end_with_token_usage_carries_dict_and_derived_total(db: Database):
    a = AuditLogger(db)
    u = TokenUsage(input_tokens=100, output_tokens=50, reasoning_tokens=10)
    a.log_session_end(task_id="T1", agent="dev", duration_seconds=42, token_usage=u)
    e = _session_end_entries(db, "T1")
    assert len(e) == 1
    payload = e[0]["payload"]
    assert payload["duration_seconds"] == 42
    # 100 + 50 + 10; cache reads excluded by TokenUsage.total
    assert payload["token_count"] == 160
    assert payload["token_usage"]["input_tokens"] == 100
    assert payload["token_usage"]["output_tokens"] == 50
    assert payload["token_usage"]["reasoning_tokens"] == 10


def test_log_session_end_with_partial_token_usage(db: Database):
    a = AuditLogger(db)
    # parse-failure case: all numeric fields NULL, raw JSON preserved
    u = TokenUsage(usage_raw_json='{"raw":"x"}')
    a.log_session_end(task_id="T1", agent="dev", duration_seconds=10, token_usage=u)
    e = _session_end_entries(db, "T1")
    payload = e[0]["payload"]
    assert payload["token_count"] == 0  # all None -> total returns 0
    assert payload["token_usage"]["usage_raw_json"] == '{"raw":"x"}'
    assert payload["token_usage"]["input_tokens"] is None
    assert payload["token_usage"]["output_tokens"] is None
    assert payload["token_usage"]["reasoning_tokens"] is None
