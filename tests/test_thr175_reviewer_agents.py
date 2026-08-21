"""THR-175 — reviewer identity is the org-configured ``reviewer_agents`` setting.

These tests pin the authoring-time HARD REJECT and the execution-seam
fail-closed for configured reviewer legs that omit ``expect_verdict``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.config import Settings
from runtime.infrastructure.database import Database
from runtime.models import BlockKind, ChainLeg, NextStep, TaskRecord, TaskStatus
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
        "    workers: [product_manager, dev_agent, code_reviewer, qa_engineer, senior_dev]\n"
    )
    return paths


@pytest.fixture
def db(runtime: OrgPaths) -> Database:
    return Database(runtime.db_path)


def _make_orch(db: Database, runtime: OrgPaths):
    from runtime.orchestrator.orchestrator import Orchestrator
    orch = Orchestrator(
        db=db, settings=Settings(), paths=runtime, slug="test",
        teams=TeamsRegistry.load(runtime.root),
    )
    for a in ("dev_agent", "code_reviewer", "qa_engineer", "senior_dev"):
        (runtime.workspaces_dir / a).mkdir(parents=True, exist_ok=True)
    return orch


def _set_reviewer_agents(db: Database, names: list[str]) -> None:
    db.upsert_org_setting("reviewer_agents", json.dumps(names))


def test_validate_delegate_rejects_reviewer_then_leg_omitted_expectation(runtime, db):
    """A reviewer ``then`` leg with omitted expect_verdict is a HARD REJECT."""
    from runtime.orchestrator.run_step import _validate_delegate

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate",
        agent="dev_agent",
        prompt="build it",
        then=[ChainLeg(agent="code_reviewer", prompt="review", expect_verdict=None)],
    )
    err = _validate_delegate(orch, decision)
    assert err is not None
    assert "HARD REJECT" in err
    assert "expect_verdict" in err
    assert "code_reviewer" in err


def test_validate_delegate_rejects_reviewer_first_leg_omitted_expectation(runtime, db):
    """A reviewer FIRST leg (decision.agent is a reviewer) with omitted
    expect_verdict is a HARD REJECT."""
    from runtime.orchestrator.run_step import _validate_delegate

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate",
        agent="code_reviewer",
        prompt="review it",
        then=[ChainLeg(agent="qa_engineer", prompt="qa", expect_verdict="PASS")],
    )
    err = _validate_delegate(orch, decision)
    assert err is not None
    assert "HARD REJECT" in err


def test_validate_delegate_accepts_reviewer_leg_with_expectation(runtime, db):
    """A reviewer leg WITH expect_verdict=APPROVE passes validation."""
    from runtime.orchestrator.run_step import _validate_delegate

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate",
        agent="dev_agent",
        prompt="build it",
        then=[ChainLeg(agent="code_reviewer", prompt="review", expect_verdict="APPROVE")],
    )
    assert _validate_delegate(orch, decision) is None


def test_validate_delegate_accepts_verdictless_non_reviewer(runtime, db):
    """A NON-reviewer leg with omitted expect_verdict is accepted (ordinary
    verdict-less chain semantics preserved)."""
    from runtime.orchestrator.run_step import _validate_delegate

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate",
        agent="dev_agent",
        prompt="build it",
        then=[
            ChainLeg(agent="senior_dev", prompt="pair", expect_verdict=None),
            ChainLeg(agent="qa_engineer", prompt="qa", expect_verdict="PASS"),
        ],
    )
    assert _validate_delegate(orch, decision) is None


def test_validate_delegate_accepts_bare_single_leg_reviewer_delegate(runtime, db):
    """A bare single-leg reviewer delegate (no ``then``, no ``expect_verdict``)
    is NOT a chain and is NOT rejected — there is no downstream to gate."""
    from runtime.orchestrator.run_step import _validate_delegate

    orch = _make_orch(db, runtime)
    decision = NextStep(
        action="delegate",
        agent="code_reviewer",
        prompt="review this PR",
    )
    assert _validate_delegate(orch, decision) is None


def test_fanout_carrier_reviewer_omitted_expectation_rejected(runtime, db, monkeypatch):
    """A fan-out pipeline carrier whose ``then`` leg is a configured reviewer
    with omitted expect_verdict is rejected before any child spawns."""
    from runtime.orchestrator.orchestrator import Orchestrator

    orch = _make_orch(db, runtime)
    from unittest.mock import MagicMock

    db.insert_task(TaskRecord(
        id="T-FANOUT-REJ", brief="fanout reject",
        assigned_agent="engineering_head", task_type="task",
    ))

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        r = MagicMock()
        r.success = True
        r.error = None
        r.returncode = 0
        r.rate_limited = False
        r.token_usage = None
        r.session_id = "sess-1"
        from runtime.models import CompletionReport
        report = CompletionReport(
            task_id=task_id, agent=agent, status="completed", confidence=80,
            output_summary=json.dumps({
                "action": "fanout",
                "children": [
                    {
                        "agent": "dev_agent", "prompt": "child 1",
                        "then": [{"agent": "code_reviewer", "prompt": "review"}],
                    },
                    {"agent": "qa_engineer", "prompt": "child 2"},
                ],
                "width_cap_ack": 2,
            }),
        )
        return r, report

    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)
    orch.run_step("T-FANOUT-REJ")

    parent = db.get_task("T-FANOUT-REJ")
    assert parent.status == TaskStatus.FAILED
    assert "HARD REJECT" in (parent.note or "")
    assert db.get_children("T-FANOUT-REJ") == []
