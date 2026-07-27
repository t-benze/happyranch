from __future__ import annotations

import pytest

from runtime.daemon.sessions import SessionTracker


def test_register_and_lookup() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    assert t.get_active("TASK-001", "dev_agent") == "sess-1"


def test_unknown_returns_none() -> None:
    t = SessionTracker()
    assert t.get_active("TASK-999", "dev_agent") is None


def test_overwrite_replaces_previous() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    t.set_active("TASK-001", "dev_agent", "sess-2")
    assert t.get_active("TASK-001", "dev_agent") == "sess-2"


def test_clear_removes_entry() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    t.clear("TASK-001", "dev_agent")
    assert t.get_active("TASK-001", "dev_agent") is None


def test_independent_per_agent() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    t.set_active("TASK-001", "engineering_head", "sess-2")
    assert t.get_active("TASK-001", "dev_agent") == "sess-1"
    assert t.get_active("TASK-001", "engineering_head") == "sess-2"


def test_count_active_empty() -> None:
    t = SessionTracker()
    assert t.count_active() == 0


def test_count_active_after_registration() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    assert t.count_active() == 1
    t.set_active("TASK-002", "dev_agent", "sess-2")
    assert t.count_active() == 2


def test_count_active_after_overwrite() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    t.set_active("TASK-001", "dev_agent", "sess-2")
    assert t.count_active() == 1  # same key, not a new entry


def test_count_active_after_clear() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    t.set_active("TASK-002", "dev_agent", "sess-2")
    assert t.count_active() == 2
    t.clear("TASK-001", "dev_agent")
    assert t.count_active() == 1


def test_get_by_session_returns_task_and_agent() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "frontend_engineer", "sess-abc")
    assert t.get_by_session("sess-abc") == ("TASK-001", "frontend_engineer")


def test_get_by_session_unknown_returns_none() -> None:
    t = SessionTracker()
    assert t.get_by_session("sess-nonexistent") is None


def test_get_by_session_after_clear_returns_none() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-xyz")
    t.clear("TASK-001", "dev_agent")
    assert t.get_by_session("sess-xyz") is None


def test_get_by_session_after_overwrite_still_finds() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-1")
    t.set_active("TASK-001", "dev_agent", "sess-2")
    # Old session is overwritten; only the new one resolves
    assert t.get_by_session("sess-1") is None
    assert t.get_by_session("sess-2") == ("TASK-001", "dev_agent")


def test_get_by_session_independent_per_agent() -> None:
    t = SessionTracker()
    t.set_active("TASK-001", "dev_agent", "sess-dev")
    t.set_active("TASK-002", "frontend_engineer", "sess-fe")
    assert t.get_by_session("sess-dev") == ("TASK-001", "dev_agent")
    assert t.get_by_session("sess-fe") == ("TASK-002", "frontend_engineer")
