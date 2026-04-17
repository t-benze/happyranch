from __future__ import annotations

import pytest

from src.daemon.sessions import SessionTracker


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
