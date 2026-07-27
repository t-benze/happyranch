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


# ═══════════════════════════════════════════════════════════════════════════
# THR-055 seq 127 fix-forward: session context cleanup on clear/replacement
# ═══════════════════════════════════════════════════════════════════════════

class TestContextCleanup:
    """Regression: _context_by_session must be cleaned on clear() and
    on set_active() replacement, so revoked/completed/superseded opaque
    capabilities cannot create proposals.
    """

    def test_clear_removes_context_by_session(self) -> None:
        t = SessionTracker()
        t.set_active("TASK-001", "frontend_engineer", "sess-ctx", org_slug="alpha")
        assert t.get_context_by_session("sess-ctx") == ("alpha", "TASK-001", "frontend_engineer")
        t.clear("TASK-001", "frontend_engineer")
        # After clear, the context must be removed
        assert t.get_context_by_session("sess-ctx") is None, (
            "context_by_session must be cleaned on clear() — "
            "stale context leaks a revoked capability"
        )

    def test_clear_also_removes_active_entry(self) -> None:
        """clear() still removes the active mapping (existing behavior invariant)."""
        t = SessionTracker()
        t.set_active("TASK-001", "frontend_engineer", "sess-ctx", org_slug="alpha")
        t.clear("TASK-001", "frontend_engineer")
        assert t.get_active("TASK-001", "frontend_engineer") is None

    def test_replacement_removes_old_context_by_session(self) -> None:
        """When a (task_id, agent) gets a new session_id, the old session's
        context must be invalidated so superseded capabilities are denied."""
        t = SessionTracker()
        t.set_active("TASK-001", "frontend_engineer", "sess-old", org_slug="alpha")
        t.set_active("TASK-001", "frontend_engineer", "sess-new", org_slug="alpha")
        # Old session context must be removed
        assert t.get_context_by_session("sess-old") is None, (
            "Old session's context must be invalidated on replacement"
        )
        # New session context must be present
        assert t.get_context_by_session("sess-new") == ("alpha", "TASK-001", "frontend_engineer")

    def test_replacement_removes_old_get_by_session(self) -> None:
        """get_by_session must also not find the old session after replacement."""
        t = SessionTracker()
        t.set_active("TASK-001", "frontend_engineer", "sess-old", org_slug="alpha")
        t.set_active("TASK-001", "frontend_engineer", "sess-new", org_slug="alpha")
        assert t.get_by_session("sess-old") is None
        assert t.get_by_session("sess-new") == ("TASK-001", "frontend_engineer")

    def test_context_by_session_missing_org_fallback(self) -> None:
        """When org_slug is not provided (legacy, tests), get_context_by_session
        returns None because no context was stored."""
        t = SessionTracker()
        t.set_active("TASK-001", "dev_agent", "sess-noorg")
        assert t.get_context_by_session("sess-noorg") is None

    def test_context_independent_per_agent(self) -> None:
        """Clearing one agent's session does not affect another agent's context."""
        t = SessionTracker()
        t.set_active("TASK-001", "frontend_engineer", "sess-fe", org_slug="alpha")
        t.set_active("TASK-001", "product_lead", "sess-pm", org_slug="alpha")
        t.clear("TASK-001", "frontend_engineer")
        # frontend_engineer's context is gone
        assert t.get_context_by_session("sess-fe") is None
        # product_lead's context is still intact
        assert t.get_context_by_session("sess-pm") == ("alpha", "TASK-001", "product_lead")
