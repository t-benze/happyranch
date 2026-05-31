# Inline Delegation Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a team manager declare a multi-leg workflow in one `delegate` decision; the orchestrator auto-advances routine happy-path legs on verdict match without consuming the 50-step orchestration cap.

**Architecture:** New optional fields on `NextStep` (`expect_verdict`, `then: list[ChainLeg]`) and `CompletionReport` (`verdict`). A single JSON column `tasks.active_chain` stores the in-flight chain on the parent task. A new `src/orchestrator/chain.py` module owns the chain dataclass + pure-logic helpers; `_enqueue_parent_if_waiting` in `run_step.py` gains a branch that auto-spawns the next leg on verdict-match instead of waking the manager. Auto-advances write a `chain_auto_advance` audit row but do NOT bump `tasks.orchestration_step_count`.

**Tech Stack:** Python 3.11+, Pydantic v2, SQLite, FastAPI, React 18 + TypeScript. `uv` for package management, `pytest` for tests.

**Spec:** `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md`

---

## File Structure

**Create:**
- `src/orchestrator/chain.py` — `ChainState` dataclass, `serialize`/`deserialize`, `build_prior_leg_context`, `compute_advance_action`
- `tests/unit/test_chain.py` — chain.py helper tests
- `tests/unit/test_run_step_chain.py` — orchestrator-integration tests for chain branches
- `tests/integration/test_chain_e2e.py` — fake-claude harness E2E

**Modify:**
- `src/models.py` — `ChainLeg` (NEW class); `NextStep` (add `then`, `expect_verdict`); `CompletionReport` (add `verdict`); `TaskRecord` (add `active_chain`)
- `src/infrastructure/database.py` — schema migration; `update_task_active_chain` method; row→TaskRecord mapping for `active_chain`
- `src/infrastructure/audit_logger.py` — `log_chain_auto_advance` method
- `src/orchestrator/run_step.py` — `_validate_delegate` (validate every leg); `delegate` branch (persist chain); `_enqueue_parent_if_waiting` (chain advancement branch); `_build_prior_steps_from_db` (inject chain summary)
- `src/daemon/routes/tasks.py` — surface parsed `active_chain` in task detail response
- `src/cli.py` — render "Current workflow chain" block in `happyranch details`
- `web/src/lib/api/types.ts` — add `active_chain` to `TaskDetailResponse`
- `web/src/features/tasks/TaskDetailPane.tsx` — chain-strip rendering
- `protocol/00-completion-contract.md` — document `verdict` + `then`/`expect_verdict`
- `protocol/skills/start-task/SKILL.md` — add optional verdict bullet
- `tests/contract/openapi.json` — regenerated snapshot

---

### Task 1: Schema migration for `tasks.active_chain`

**Files:**
- Modify: `src/infrastructure/database.py` (migration block; `_row_to_task_record`)
- Test: `tests/unit/test_database.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_database.py`:

```python
def test_tasks_active_chain_column_exists_and_defaults_null(tmp_path):
    from src.infrastructure.database import Database
    db = Database(tmp_path / "test.db")
    db.insert_task(
        task_id="TASK-1",
        team="engineering",
        brief="x",
        parent_task_id=None,
    )
    task = db.get_task("TASK-1")
    assert task is not None
    assert task.active_chain is None  # column exists, NULL by default

    cursor = db._conn.execute("PRAGMA table_info(tasks)")
    cols = {row[1] for row in cursor.fetchall()}
    assert "active_chain" in cols
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_database.py::test_tasks_active_chain_column_exists_and_defaults_null -v`
Expected: FAIL — `active_chain` not in cols, and `TaskRecord` has no `active_chain` attribute.

- [ ] **Step 3: Add the migration + `TaskRecord` field**

In `src/infrastructure/database.py`, find the most recent migration block (the `try/except sqlite3.OperationalError` patterns near line 495+). Add:

```python
try:
    self._conn.execute("ALTER TABLE tasks ADD COLUMN active_chain TEXT")
except sqlite3.OperationalError:
    pass
```

In `src/models.py`, add to `TaskRecord` (after `blocked_on_job_ids`):

```python
# In-flight inline delegation chain (JSON-serialized ChainState). NULL when no
# chain is active on this parent. See docs/superpowers/specs/2026-05-30-inline-
# delegation-chain-design.md.
active_chain: str | None = None
```

In `src/infrastructure/database.py`, find `_row_to_task_record` (or wherever `TaskRecord` is constructed from a DB row) and add `active_chain=row["active_chain"]` to the constructor call. If the function uses `dict(row)`-style unpacking, no change needed.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_database.py::test_tasks_active_chain_column_exists_and_defaults_null -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/database.py src/models.py tests/unit/test_database.py
git commit -m "feat(db): add tasks.active_chain column for inline delegation chains"
```

---

### Task 2: Pydantic model additions (`ChainLeg`, `NextStep`, `CompletionReport.verdict`)

**Files:**
- Modify: `src/models.py`
- Test: `tests/unit/test_models.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_models.py`:

```python
def test_chain_leg_roundtrip():
    from src.models import ChainLeg
    leg = ChainLeg(agent="senior_dev", prompt="review", expect_verdict="APPROVE")
    assert leg.model_dump() == {
        "agent": "senior_dev",
        "prompt": "review",
        "expect_verdict": "APPROVE",
    }
    leg2 = ChainLeg(agent="qa_engineer", prompt="qa")
    assert leg2.expect_verdict is None


def test_next_step_accepts_then_and_expect_verdict():
    from src.models import NextStep
    ns = NextStep(
        action="delegate",
        agent="dev_agent",
        prompt="build",
        expect_verdict=None,
        then=[
            {"agent": "senior_dev", "prompt": "review", "expect_verdict": "APPROVE"},
            {"agent": "qa_engineer", "prompt": "qa", "expect_verdict": "PASS"},
        ],
    )
    assert len(ns.then) == 2
    assert ns.then[0].agent == "senior_dev"
    assert ns.then[0].expect_verdict == "APPROVE"
    assert ns.then[1].expect_verdict == "PASS"


def test_next_step_then_defaults_to_empty_list():
    from src.models import NextStep
    ns = NextStep(action="delegate", agent="dev", prompt="x")
    assert ns.then == []
    assert ns.expect_verdict is None


def test_completion_report_accepts_optional_verdict():
    from src.models import CompletionReport
    r = CompletionReport(
        task_id="TASK-1",
        agent="senior_dev",
        status="completed",
        confidence=92,
        output_summary="LGTM",
        verdict="APPROVE",
    )
    assert r.verdict == "APPROVE"
    r2 = CompletionReport(
        task_id="TASK-2",
        agent="dev_agent",
        status="completed",
        confidence=80,
        output_summary="built it",
    )
    assert r2.verdict is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_models.py -k "chain_leg or next_step or completion_report_accepts_optional_verdict" -v`
Expected: 4 FAIL — `ChainLeg` not defined, `NextStep` rejects `then`/`expect_verdict`, `CompletionReport` rejects `verdict`.

- [ ] **Step 3: Implement the model changes**

In `src/models.py`, add `ChainLeg` right above `NextStep`:

```python
class ChainLeg(BaseModel):
    """One leg of an inline delegation chain. The manager declares legs 2..N in
    NextStep.then; the first leg is the existing delegate payload (agent +
    prompt + optional expect_verdict).
    """
    agent: str
    prompt: str
    expect_verdict: str | None = None
```

Modify `NextStep` to add the two fields (preserve existing fields):

```python
class NextStep(BaseModel):
    """Decision returned by a team manager for what the orchestrator should do next."""
    action: Literal["delegate", "done", "escalate"]
    agent: str | None = None
    prompt: str | None = None
    expect_verdict: str | None = None
    then: list[ChainLeg] = Field(default_factory=list)
    summary: str | None = None
    reason: str | None = None
```

Modify `CompletionReport` to add `verdict` after `output_summary`:

```python
class CompletionReport(BaseModel):
    task_id: str
    agent: str
    status: str
    confidence: int = Field(ge=0, le=100)
    output_summary: str
    # Optional structured outcome for review/QA-type workers (APPROVE, PASS,
    # REQUEST_CHANGES, etc.). Free-string; per-team vocabulary lives in each
    # team's workflow KB entry. Used by inline delegation chains to gate
    # auto-advance.
    verdict: str | None = None
    decision: NextStep | None = None
    risks_flagged: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    suggested_reviewer_focus: list[str] = Field(default_factory=list)
    artifact_dir: str | None = None
    waiting_on_job_ids: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_models.py -k "chain_leg or next_step or completion_report_accepts_optional_verdict" -v`
Expected: 4 PASS

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/unit/test_models.py
git commit -m "feat(models): add ChainLeg + NextStep.then/expect_verdict + CompletionReport.verdict"
```

---

### Task 3: Chain helper module (`src/orchestrator/chain.py`)

**Files:**
- Create: `src/orchestrator/chain.py`
- Test: `tests/unit/test_chain.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_chain.py`:

```python
from __future__ import annotations

import pytest

from src.models import ChainLeg, CompletionReport
from src.orchestrator.chain import (
    ChainState,
    AdvanceAction,
    build_prior_leg_context,
    compute_advance_action,
)


def test_chain_state_serialize_roundtrip():
    cs = ChainState(
        step_index=1,
        first_leg_expect_verdict="APPROVE",
        legs=[
            ChainLeg(agent="qa_engineer", prompt="qa", expect_verdict="PASS"),
        ],
        step_audit_id=4521,
    )
    payload = cs.serialize()
    cs2 = ChainState.deserialize(payload)
    assert cs2.step_index == 1
    assert cs2.first_leg_expect_verdict == "APPROVE"
    assert len(cs2.legs) == 1
    assert cs2.legs[0].agent == "qa_engineer"
    assert cs2.step_audit_id == 4521


def test_chain_state_deserialize_handles_missing_optional_fields():
    cs = ChainState.deserialize('{"step_index": 0, "legs": [], "step_audit_id": 1}')
    assert cs.first_leg_expect_verdict is None
    assert cs.legs == []


def test_build_prior_leg_context_includes_all_fields():
    report = CompletionReport(
        task_id="TASK-579",
        agent="senior_dev",
        status="completed",
        confidence=92,
        output_summary="PR #180 looks good. All gates green.",
        verdict="APPROVE",
        artifact_dir="workspaces/senior_dev/artifacts/TASK-579/",
    )
    out = build_prior_leg_context(child_task_id="TASK-579", report=report)
    assert "Prior leg:    TASK-579" in out
    assert "agent: senior_dev" in out
    assert "Verdict:      APPROVE" in out
    assert "Confidence:   92" in out
    assert "PR #180 looks good. All gates green." in out
    assert "Artifact dir: workspaces/senior_dev/artifacts/TASK-579/" in out


def test_build_prior_leg_context_omits_artifact_dir_when_unset():
    report = CompletionReport(
        task_id="TASK-580",
        agent="dev_agent",
        status="completed",
        confidence=80,
        output_summary="built",
    )
    out = build_prior_leg_context(child_task_id="TASK-580", report=report)
    assert "Verdict:      -" in out  # no verdict emitted
    assert "Artifact dir:" not in out


def _legs_pair():
    return [
        ChainLeg(agent="senior_dev", prompt="review", expect_verdict="APPROVE"),
        ChainLeg(agent="qa_engineer", prompt="qa", expect_verdict="PASS"),
    ]


def _report(*, status="completed", verdict=None):
    return CompletionReport(
        task_id="TASK-X",
        agent="w",
        status=status,
        confidence=80,
        output_summary="...",
        verdict=verdict,
    )


def test_compute_advance_action_wake_on_blocked():
    cs = ChainState(step_index=0, first_leg_expect_verdict=None, legs=_legs_pair(), step_audit_id=1)
    action = compute_advance_action(chain=cs, report=_report(status="blocked"))
    assert action.kind == "wake"
    assert action.reason == "child_blocked"


def test_compute_advance_action_wake_on_verdict_mismatch_first_leg():
    cs = ChainState(step_index=0, first_leg_expect_verdict="APPROVE", legs=_legs_pair(), step_audit_id=1)
    action = compute_advance_action(chain=cs, report=_report(verdict="REQUEST_CHANGES"))
    assert action.kind == "wake"
    assert action.reason == "verdict_mismatch"
    assert action.expected == "APPROVE"
    assert action.actual == "REQUEST_CHANGES"


def test_compute_advance_action_wake_on_missing_verdict_when_gated():
    cs = ChainState(step_index=1, first_leg_expect_verdict=None, legs=_legs_pair(), step_audit_id=1)
    # step_index=1 means we just got the terminal of leg index 1 (= legs[0], senior_dev with expect=APPROVE)
    action = compute_advance_action(chain=cs, report=_report(verdict=None))
    assert action.kind == "wake"
    assert action.reason == "verdict_mismatch"
    assert action.expected == "APPROVE"
    assert action.actual is None


def test_compute_advance_action_advance_first_leg_ungated():
    cs = ChainState(step_index=0, first_leg_expect_verdict=None, legs=_legs_pair(), step_audit_id=1)
    action = compute_advance_action(chain=cs, report=_report())
    assert action.kind == "advance"
    assert action.next_leg.agent == "senior_dev"
    assert action.next_step_index == 1


def test_compute_advance_action_advance_first_leg_gated_match():
    cs = ChainState(step_index=0, first_leg_expect_verdict="APPROVE", legs=_legs_pair(), step_audit_id=1)
    action = compute_advance_action(chain=cs, report=_report(verdict="APPROVE"))
    assert action.kind == "advance"
    assert action.next_leg.agent == "senior_dev"


def test_compute_advance_action_wake_on_final_leg_match():
    cs = ChainState(step_index=2, first_leg_expect_verdict=None, legs=_legs_pair(), step_audit_id=1)
    # step_index=2 means terminal of leg index 2 (= legs[1], qa_engineer with expect=PASS)
    action = compute_advance_action(chain=cs, report=_report(verdict="PASS"))
    assert action.kind == "wake"
    assert action.reason == "chain_complete"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_chain.py -v`
Expected: All FAIL — `src.orchestrator.chain` doesn't exist.

- [ ] **Step 3: Implement `src/orchestrator/chain.py`**

Create `src/orchestrator/chain.py`:

```python
"""Inline delegation chain — state model + pure-logic helpers.

A chain is a manager-authored multi-leg workflow declared in one `delegate`
decision (NextStep.then). The orchestrator auto-advances routine happy-path
legs on verdict match without consuming the manager's 50-step cap. See
docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md.

This module is pure logic — no DB, no orchestrator, no I/O. Integration with
the orchestrator lives in src/orchestrator/run_step.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Literal

from src.models import ChainLeg, CompletionReport


@dataclass
class ChainState:
    """In-flight chain stored as JSON on tasks.active_chain.

    step_index = 0 when the first leg (the implicit decision.agent+prompt) is
    in flight; 1..N when a subsequent leg (from `legs`) is in flight.
    """
    step_index: int
    first_leg_expect_verdict: str | None
    legs: list[ChainLeg]
    step_audit_id: int

    def serialize(self) -> str:
        return json.dumps({
            "step_index": self.step_index,
            "first_leg_expect_verdict": self.first_leg_expect_verdict,
            "legs": [leg.model_dump() for leg in self.legs],
            "step_audit_id": self.step_audit_id,
        })

    @classmethod
    def deserialize(cls, payload: str) -> "ChainState":
        data = json.loads(payload)
        return cls(
            step_index=data["step_index"],
            first_leg_expect_verdict=data.get("first_leg_expect_verdict"),
            legs=[ChainLeg(**leg) for leg in data.get("legs", [])],
            step_audit_id=data["step_audit_id"],
        )

    def current_expect_verdict(self) -> str | None:
        """Expected verdict for the just-terminated child (the one at step_index)."""
        if self.step_index == 0:
            return self.first_leg_expect_verdict
        # step_index=1..N corresponds to legs[0..N-1].
        return self.legs[self.step_index - 1].expect_verdict


@dataclass
class AdvanceAction:
    """Outcome of compute_advance_action: either advance to the next leg or
    wake the manager (with a reason).
    """
    kind: Literal["advance", "wake"]
    # advance fields:
    next_leg: ChainLeg | None = None
    next_step_index: int | None = None
    # wake fields:
    reason: str | None = None    # "child_blocked" | "verdict_mismatch" | "chain_complete"
    expected: str | None = None
    actual: str | None = None


def compute_advance_action(*, chain: ChainState, report: CompletionReport) -> AdvanceAction:
    """Decide whether to auto-advance to the next leg or wake the manager.

    Caller has already confirmed the child task is in a terminal COMPLETED
    state (failed/cancelled children take a separate cascade path). This
    function only handles the COMPLETED branch.
    """
    if report.status == "blocked":
        return AdvanceAction(kind="wake", reason="child_blocked")

    expected = chain.current_expect_verdict()
    if expected is not None and report.verdict != expected:
        return AdvanceAction(
            kind="wake", reason="verdict_mismatch",
            expected=expected, actual=report.verdict,
        )

    next_index = chain.step_index + 1
    # Total legs = 1 (first leg) + len(chain.legs). Next-leg index space is
    # 1..len(chain.legs); next_index > len(chain.legs) means no more legs.
    if next_index > len(chain.legs):
        return AdvanceAction(kind="wake", reason="chain_complete")

    next_leg = chain.legs[next_index - 1]
    return AdvanceAction(
        kind="advance", next_leg=next_leg, next_step_index=next_index,
    )


def build_prior_leg_context(*, child_task_id: str, report: CompletionReport) -> str:
    """Render the orchestrator-appended Prior Leg Context block.

    Suffixed (not prepended) to every non-first leg's brief so the manager's
    authored brief remains the primary instruction surface.
    """
    verdict_line = f"Verdict:      {report.verdict}" if report.verdict else "Verdict:      -"
    lines = [
        "",
        "---",
        "## Prior leg context (auto-generated by orchestrator)",
        "",
        f"Prior leg:    {child_task_id}  (agent: {report.agent})",
        f"Status:       {report.status}",
        verdict_line,
        f"Confidence:   {report.confidence}",
        "Summary:",
    ]
    # Indent multi-line summary by two spaces for readability.
    for line in report.output_summary.splitlines() or [""]:
        lines.append(f"  {line}")
    if report.artifact_dir:
        lines.append("")
        lines.append(f"Artifact dir: {report.artifact_dir}")
    lines.append("---")
    return "\n".join(lines)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_chain.py -v`
Expected: All PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/chain.py tests/unit/test_chain.py
git commit -m "feat(orchestrator): chain.py module with state + advance helpers"
```

---

### Task 4: Audit logger + DB writer for chain auto-advance

**Files:**
- Modify: `src/infrastructure/audit_logger.py`
- Modify: `src/infrastructure/database.py` (add `update_task_active_chain`)
- Test: `tests/unit/test_audit_logger.py`, `tests/unit/test_database.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_audit_logger.py`:

```python
def test_log_chain_auto_advance_writes_expected_payload(tmp_path):
    from src.infrastructure.database import Database
    from src.infrastructure.audit_logger import AuditLogger
    db = Database(tmp_path / "x.db")
    AuditLogger(db).log_chain_auto_advance(
        parent_task_id="TASK-1",
        leg_index=2,
        spawned_child_id="TASK-3",
        triggering_child_id="TASK-2",
        triggering_verdict="APPROVE",
        chain_origin_step_audit_id=4521,
    )
    rows = db.get_audit_logs("TASK-1")
    assert len(rows) == 1
    assert rows[0]["action"] == "chain_auto_advance"
    assert rows[0]["agent"] == "orchestrator"
    payload = rows[0]["payload"]
    assert payload["leg_index"] == 2
    assert payload["spawned_child_id"] == "TASK-3"
    assert payload["triggering_child_id"] == "TASK-2"
    assert payload["triggering_verdict"] == "APPROVE"
    assert payload["chain_origin_step_audit_id"] == 4521
```

Add to `tests/unit/test_database.py`:

```python
def test_update_task_active_chain_sets_and_clears(tmp_path):
    from src.infrastructure.database import Database
    db = Database(tmp_path / "x.db")
    db.insert_task(task_id="TASK-1", team="engineering", brief="x", parent_task_id=None)

    db.update_task_active_chain("TASK-1", '{"step_index":0,"legs":[],"step_audit_id":1}')
    assert db.get_task("TASK-1").active_chain == '{"step_index":0,"legs":[],"step_audit_id":1}'

    db.update_task_active_chain("TASK-1", None)
    assert db.get_task("TASK-1").active_chain is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```
uv run pytest tests/unit/test_audit_logger.py::test_log_chain_auto_advance_writes_expected_payload tests/unit/test_database.py::test_update_task_active_chain_sets_and_clears -v
```
Expected: 2 FAIL — methods don't exist.

- [ ] **Step 3: Implement the methods**

In `src/infrastructure/audit_logger.py`, add (near other `log_thread_*` / `log_orchestration_step` methods):

```python
def log_chain_auto_advance(
    self,
    parent_task_id: str,
    *,
    leg_index: int,
    spawned_child_id: str,
    triggering_child_id: str,
    triggering_verdict: str | None,
    chain_origin_step_audit_id: int,
) -> None:
    """Audit row for an orchestrator-driven chain advance. Distinct from
    `orchestration_step` (which is manager-authored). Does NOT correspond to
    a tasks.orchestration_step_count bump — chains are one decision, multiple
    auto-advances.
    """
    self._db.insert_audit_log(
        task_id=parent_task_id,
        agent="orchestrator",
        action="chain_auto_advance",
        payload={
            "leg_index": leg_index,
            "spawned_child_id": spawned_child_id,
            "triggering_child_id": triggering_child_id,
            "triggering_verdict": triggering_verdict,
            "chain_origin_step_audit_id": chain_origin_step_audit_id,
        },
    )
```

In `src/infrastructure/database.py`, add (near other `update_task_*` helpers):

```python
def update_task_active_chain(self, task_id: str, active_chain: str | None) -> None:
    """Set or clear tasks.active_chain. Pass None to clear (chain finished,
    aborted, or never declared)."""
    with self._lock:
        self._conn.execute(
            "UPDATE tasks SET active_chain = ?, updated_at = ? WHERE id = ?",
            (active_chain, _utcnow_iso(), task_id),
        )
        self._conn.commit()
```

(If `_utcnow_iso` isn't the local helper name, match the convention used by surrounding `update_task_*` methods.)

- [ ] **Step 4: Run tests to verify they pass**

Run: same command as Step 2.
Expected: 2 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py src/infrastructure/database.py tests/unit/test_audit_logger.py tests/unit/test_database.py
git commit -m "feat(audit,db): log_chain_auto_advance + update_task_active_chain helpers"
```

---

### Task 5: Cross-team validation across all chain legs

**Files:**
- Modify: `src/orchestrator/run_step.py` (`_validate_delegate`)
- Test: `tests/unit/test_run_step_chain.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_run_step_chain.py`:

```python
from __future__ import annotations

from unittest.mock import MagicMock

from src.models import ChainLeg, NextStep
from src.orchestrator.run_step import _validate_delegate


def _orch_with_team(*, agents_by_team: dict[str, list[str]]):
    """Build a minimal mock Orchestrator-like object for _validate_delegate."""
    orch = MagicMock()
    workspace_root = MagicMock()
    workspace_root.exists.return_value = True
    orch.workspaces_dir = MagicMock()
    orch.workspaces_dir.__truediv__.return_value = workspace_root
    return orch


def test_validate_delegate_rejects_first_leg_with_no_agent():
    orch = _orch_with_team(agents_by_team={"eng": ["dev", "sr"]})
    err = _validate_delegate(orch, NextStep(action="delegate", agent=None, prompt="x"))
    assert err is not None
    assert "agent" in err.lower()


def test_validate_delegate_passes_when_all_legs_have_agents_and_workspaces():
    orch = _orch_with_team(agents_by_team={"eng": ["dev", "sr", "qa"]})
    decision = NextStep(
        action="delegate", agent="dev", prompt="build",
        then=[
            ChainLeg(agent="sr", prompt="review", expect_verdict="APPROVE"),
            ChainLeg(agent="qa", prompt="qa", expect_verdict="PASS"),
        ],
    )
    err = _validate_delegate(orch, decision)
    assert err is None


def test_validate_delegate_rejects_chain_leg_with_missing_workspace():
    orch = MagicMock()
    workspace_root = MagicMock()
    # First call (first leg) returns exists=True; second call (leg in `then`) returns False.
    workspace_root.exists.side_effect = [True, False]
    orch.workspaces_dir = MagicMock()
    orch.workspaces_dir.__truediv__.return_value = workspace_root
    decision = NextStep(
        action="delegate", agent="dev", prompt="build",
        then=[ChainLeg(agent="ghost_agent", prompt="x")],
    )
    err = _validate_delegate(orch, decision)
    assert err is not None
    assert "ghost_agent" in err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_run_step_chain.py -v -k validate`
Expected: 1 PASS (existing single-leg validation already rejects no-agent), 2 FAIL (chain leg validation not yet implemented).

- [ ] **Step 3: Extend `_validate_delegate` to validate every leg**

In `src/orchestrator/run_step.py`, find `_validate_delegate` (around line 453). Replace its body with a per-leg loop. Read the current function first to preserve any existing error messages, then update to iterate over `[decision] + decision.then`. The shape:

```python
def _validate_delegate(orch: "Orchestrator", decision) -> str | None:
    """Structural validation for a delegate decision, including every chain
    leg in `decision.then`. Returns None on success, an error string on the
    first failure encountered (legs evaluated in order: first leg, then
    then[0], then[1], ...).
    """
    # First leg
    err = _validate_one_leg(orch, agent=decision.agent, where="first leg")
    if err is not None:
        return err
    # Subsequent legs
    for i, leg in enumerate(decision.then or []):
        err = _validate_one_leg(orch, agent=leg.agent, where=f"chain leg {i + 2}")
        if err is not None:
            return err
    return None


def _validate_one_leg(orch: "Orchestrator", *, agent: str | None, where: str) -> str | None:
    if not agent:
        return f"{where}: missing target agent"
    workspace = orch.workspaces_dir / agent
    if not workspace.exists():
        return f"{where}: agent {agent!r} has no workspace at {workspace}"
    return None
```

(Adapt to preserve the existing function's exact error message format if it differs — read the surrounding code first.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_run_step_chain.py -v -k validate`
Expected: 3 PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/unit/test_run_step_chain.py
git commit -m "feat(run_step): validate every leg of a chain at decision-parse time"
```

---

### Task 6: Cross-team chain guard (manager-to-manager-team membership across all legs)

**Files:**
- Modify: `src/orchestrator/run_step.py` (the cross-team guard block in the delegate branch, currently around line 363)
- Test: `tests/unit/test_run_step_chain.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_run_step_chain.py`:

```python
def test_cross_team_chain_guard_rejects_off_team_leg():
    """If any leg targets an agent not on the manager's team, the whole
    chain is rejected via the existing feedback mechanism."""
    from src.orchestrator.run_step import _chain_legs_off_team

    teams = MagicMock()
    teams.team_for_manager.return_value = "engineering"
    teams.team_for_agent.side_effect = lambda name: {
        "dev": "engineering",
        "sr": "engineering",
        "outsider": "content",
    }.get(name)

    decision = NextStep(
        action="delegate", agent="dev", prompt="build",
        then=[
            ChainLeg(agent="sr", prompt="review"),
            ChainLeg(agent="outsider", prompt="other"),
        ],
    )
    off = _chain_legs_off_team(teams, manager="eh", decision=decision)
    assert off == [("outsider", "content")]


def test_cross_team_chain_guard_passes_when_all_legs_on_team():
    from src.orchestrator.run_step import _chain_legs_off_team
    teams = MagicMock()
    teams.team_for_manager.return_value = "engineering"
    teams.team_for_agent.return_value = "engineering"
    decision = NextStep(action="delegate", agent="dev", prompt="x", then=[
        ChainLeg(agent="sr", prompt="y"),
    ])
    assert _chain_legs_off_team(teams, manager="eh", decision=decision) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_run_step_chain.py -v -k cross_team`
Expected: 2 FAIL — `_chain_legs_off_team` doesn't exist.

- [ ] **Step 3: Extract the cross-team check into a helper and call it for every leg**

In `src/orchestrator/run_step.py`, add (near the existing cross-team guard):

```python
def _chain_legs_off_team(
    teams, manager: str, decision,
) -> list[tuple[str, str | None]]:
    """Return [(agent_name, agent_team)] for every leg in `decision` whose
    agent is NOT on `manager`'s team. Empty list = all on team.
    """
    caller_team = teams.team_for_manager(manager)
    if caller_team is None:
        # Manager itself isn't registered — surface every leg as off-team.
        all_legs = [decision.agent] + [leg.agent for leg in (decision.then or [])]
        return [(a, teams.team_for_agent(a)) for a in all_legs if a]

    off: list[tuple[str, str | None]] = []
    for agent_name in [decision.agent] + [leg.agent for leg in (decision.then or [])]:
        if not agent_name:
            continue
        agent_team = teams.team_for_agent(agent_name)
        if agent_team is None or agent_team != caller_team:
            off.append((agent_name, agent_team))
    return off
```

Then in the existing delegate branch (around line 363, where the current single-leg cross-team check lives), replace:

```python
caller_team = orch.teams.team_for_manager(agent)
target_team = orch.teams.team_for_agent(decision.agent)
if caller_team is None or target_team is None or caller_team != target_team:
    feedback = (
        f"Invalid delegation: you are on team {caller_team!r}, "
        f"but {decision.agent!r} is on team {target_team!r}. "
        "Pick a worker on your own team, or escalate."
    )
    # ... existing feedback insert ...
```

with:

```python
off_team_legs = _chain_legs_off_team(orch.teams, manager=agent, decision=decision)
if off_team_legs:
    caller_team = orch.teams.team_for_manager(agent)
    parts = [f"{name!r} is on team {team!r}" for name, team in off_team_legs]
    feedback = (
        f"Invalid delegation: you are on team {caller_team!r}, "
        f"but {'; '.join(parts)}. "
        "Pick workers on your own team, or escalate."
    )
    # ... existing feedback insert (unchanged) ...
```

Preserve the existing audit log + status reset + queue re-enqueue lines below the feedback assignment.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_run_step_chain.py -v -k cross_team`
Expected: 2 PASS.

Also run the existing run_step test suite to confirm no regressions on single-leg validation:

Run: `uv run pytest tests/unit/test_run_step.py -v` (if file exists; otherwise `tests/unit/ -v -k run_step`)
Expected: PASS unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/unit/test_run_step_chain.py
git commit -m "feat(run_step): cross-team guard runs against every chain leg"
```

---

### Task 7: Persist `active_chain` on chain declaration

**Files:**
- Modify: `src/orchestrator/run_step.py` (the delegate branch — after spawn-child, before return)
- Test: `tests/unit/test_run_step_chain.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_run_step_chain.py`:

```python
def test_chain_persistence_writes_active_chain_with_step_audit_id(tmp_path):
    """When a manager declares a delegate with `then` or `expect_verdict`,
    the orchestrator persists ChainState on the parent before/with the first
    leg spawn."""
    # Smoke-test via a fresh orchestrator + DB. This uses higher-level
    # plumbing because the persistence point sits inside the delegate branch
    # alongside the child-spawn transaction.
    from src.infrastructure.database import Database
    from src.orchestrator.chain import ChainState

    db = Database(tmp_path / "x.db")
    db.insert_task(task_id="TASK-1", team="engineering", brief="parent", parent_task_id=None)
    # Manually simulate what the delegate branch should do after the manager
    # decides {action:delegate, agent:'dev', prompt:'build', then:[{...}]}:
    chain = ChainState(
        step_index=0,
        first_leg_expect_verdict=None,
        legs=[ChainLeg(agent="sr", prompt="r", expect_verdict="APPROVE")],
        step_audit_id=42,
    )
    db.update_task_active_chain("TASK-1", chain.serialize())

    task = db.get_task("TASK-1")
    parsed = ChainState.deserialize(task.active_chain)
    assert parsed.step_index == 0
    assert parsed.step_audit_id == 42
    assert len(parsed.legs) == 1
```

(This test verifies the persistence mechanics; the integration test in Task 12 verifies the orchestrator actually calls the persistence at the right time.)

- [ ] **Step 2: Run to verify it passes**

Run: `uv run pytest tests/unit/test_run_step_chain.py::test_chain_persistence_writes_active_chain_with_step_audit_id -v`
Expected: PASS (Task 4 already shipped `update_task_active_chain`; this is a smoke test confirming the API shape is usable from the delegate branch).

- [ ] **Step 3: Wire chain persistence into the delegate branch**

In `src/orchestrator/run_step.py`, find the delegate branch (around line 343 — `if decision.action == "delegate":`). Locate the point AFTER `db.insert_task(...)` for the child, BEFORE the `return`. Add the chain persistence:

```python
# Persist the chain on the parent so child terminals can auto-advance.
# Skip if neither `then` nor `expect_verdict` is set — that's a plain
# single-leg delegate (existing behavior, no chain to track).
if decision.then or decision.expect_verdict is not None:
    from src.orchestrator.chain import ChainState
    chain = ChainState(
        step_index=0,
        first_leg_expect_verdict=decision.expect_verdict,
        legs=list(decision.then),
        step_audit_id=audit_row_id,  # see note below
    )
    db.update_task_active_chain(task_id, chain.serialize())
```

The `audit_row_id` is the id of the `orchestration_step` row written earlier in this branch by `orch._audit.log_orchestration_step(...)`. If that method does not currently return the row id, modify `Database.insert_audit_log` and `AuditLogger.log_orchestration_step` to return the inserted `lastrowid`:

```python
# In src/infrastructure/database.py:
def insert_audit_log(self, *, task_id, agent, action, payload) -> int:
    with self._lock:
        cur = self._conn.execute(
            "INSERT INTO audit_log (task_id, agent, action, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, agent, action, json.dumps(payload), _utcnow_iso()),
        )
        self._conn.commit()
        return cur.lastrowid
```

(Adapt to the actual current signature; the return-id addition is the load-bearing change.)

```python
# In src/infrastructure/audit_logger.py:
def log_orchestration_step(self, task_id, step_number, decision_payload) -> int:
    return self._db.insert_audit_log(
        task_id=task_id, agent="orchestrator",
        action="orchestration_step",
        payload={"step_number": step_number, "decision": decision_payload},
    )
```

(Match the existing signature exactly; only add the `return ...`.)

- [ ] **Step 4: Run the run_step chain tests + existing audit/db tests**

Run:
```
uv run pytest tests/unit/test_run_step_chain.py tests/unit/test_audit_logger.py tests/unit/test_database.py -v
```
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py src/infrastructure/database.py src/infrastructure/audit_logger.py tests/unit/test_run_step_chain.py
git commit -m "feat(run_step): persist active_chain on parent when manager declares a chain"
```

---

### Task 8: Chain advancement in `_enqueue_parent_if_waiting` (the core)

**Files:**
- Modify: `src/orchestrator/run_step.py` (`_enqueue_parent_if_waiting`)
- Test: `tests/unit/test_run_step_chain.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_run_step_chain.py`:

```python
def test_chain_branch_auto_advances_on_verdict_match(monkeypatch, tmp_path):
    """When a chain leg's worker reports a matching verdict and a next leg
    exists, the orchestrator spawns the next leg instead of waking the parent.
    Parent's orchestration_step_count is NOT bumped."""
    from src.infrastructure.database import Database
    from src.orchestrator.chain import ChainState
    from src.models import ChainLeg, CompletionReport, TaskStatus, BlockKind

    db = Database(tmp_path / "x.db")
    # Parent (manager task), blocked-delegated, with an active chain.
    db.insert_task(task_id="TASK-P", team="eng", brief="parent", parent_task_id=None)
    db.update_task("TASK-P", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    chain = ChainState(
        step_index=0,
        first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="sr", prompt="review brief", expect_verdict="APPROVE"),
            ChainLeg(agent="qa", prompt="qa brief", expect_verdict="PASS"),
        ],
        step_audit_id=1,
    )
    db.update_task_active_chain("TASK-P", chain.serialize())

    # Child (first leg, dev_agent) just completed.
    db.insert_task(task_id="TASK-C1", team="eng", brief="build", parent_task_id="TASK-P")
    db.update_task("TASK-C1", status=TaskStatus.COMPLETED, assigned_agent="dev")
    db.insert_task_result(
        task_id="TASK-C1", agent="dev", session_id="s",
        status="completed", confidence_score=80,
        output_summary="built PR #1", verdict=None,  # first leg ungated
    )

    # Call _advance_chain_or_continue (or whatever the helper is named).
    from src.orchestrator.run_step import _advance_chain_for_completed_child
    spawned = _advance_chain_for_completed_child(
        # Build a minimal orch test double — adapt args to the real signature.
        orch=_orch_with_db(db),
        parent_task_id="TASK-P",
        child_task_id="TASK-C1",
    )
    assert spawned == "advance"
    # New child task created for leg 2 (sr).
    children = db.get_children("TASK-P")
    assert len(children) == 2  # original + new
    new_child = db.get_task(children[1])
    assert new_child.assigned_agent == "sr"
    assert "review brief" in new_child.brief
    assert "Prior leg context" in new_child.brief  # auto-appended
    # Chain step_index advanced.
    cs2 = ChainState.deserialize(db.get_task("TASK-P").active_chain)
    assert cs2.step_index == 1
    # Parent NOT re-enqueued, orchestration_step_count NOT bumped.
    assert db.get_task("TASK-P").orchestration_step_count == 0


def test_chain_branch_wakes_parent_on_verdict_mismatch(tmp_path):
    """Mismatched verdict aborts the chain and falls through to the normal
    parent-wake path."""
    from src.infrastructure.database import Database
    from src.orchestrator.chain import ChainState
    from src.models import ChainLeg, TaskStatus, BlockKind

    db = Database(tmp_path / "x.db")
    db.insert_task(task_id="TASK-P", team="eng", brief="parent", parent_task_id=None)
    db.update_task("TASK-P", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    chain = ChainState(
        step_index=0,
        first_leg_expect_verdict="APPROVE",
        legs=[ChainLeg(agent="qa", prompt="q", expect_verdict="PASS")],
        step_audit_id=1,
    )
    db.update_task_active_chain("TASK-P", chain.serialize())

    db.insert_task(task_id="TASK-C1", team="eng", brief="b", parent_task_id="TASK-P")
    db.update_task("TASK-C1", status=TaskStatus.COMPLETED, assigned_agent="sr")
    db.insert_task_result(
        task_id="TASK-C1", agent="sr", session_id="s",
        status="completed", confidence_score=80,
        output_summary="needs changes", verdict="REQUEST_CHANGES",
    )

    from src.orchestrator.run_step import _advance_chain_for_completed_child
    spawned = _advance_chain_for_completed_child(
        orch=_orch_with_db(db),
        parent_task_id="TASK-P",
        child_task_id="TASK-C1",
    )
    assert spawned == "wake"
    # Chain cleared.
    assert db.get_task("TASK-P").active_chain is None


def _orch_with_db(db):
    """Test-double Orchestrator that satisfies _advance_chain_for_completed_child."""
    orch = MagicMock()
    orch._db = db
    orch._audit = MagicMock()
    orch._queue = None
    orch._slug = "test-org"
    return orch
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/unit/test_run_step_chain.py -v -k chain_branch`
Expected: FAIL — `_advance_chain_for_completed_child` doesn't exist.

- [ ] **Step 3: Implement the chain branch in `_enqueue_parent_if_waiting`**

In `src/orchestrator/run_step.py`, add the helper (above `_enqueue_parent_if_waiting`):

```python
def _advance_chain_for_completed_child(
    *,
    orch: "Orchestrator",
    parent_task_id: str,
    child_task_id: str,
) -> str:
    """Inspect the parent's active_chain against the just-completed child's
    report. Either spawn the next leg ("advance") or clear the chain so the
    caller falls through to the normal parent-wake path ("wake").

    Returns "advance" or "wake". When "advance" is returned, the parent is
    NOT re-enqueued and orchestration_step_count is NOT bumped.
    """
    from src.orchestrator.chain import (
        ChainState,
        build_prior_leg_context,
        compute_advance_action,
    )
    parent = orch._db.get_task(parent_task_id)
    if parent is None or parent.active_chain is None:
        return "wake"

    chain = ChainState.deserialize(parent.active_chain)

    # Fetch the child's terminal completion report. Workers write to
    # task_results; the latest row is the terminal one.
    report = orch._db.get_latest_completion_report(child_task_id)
    if report is None:
        # Defensive: completed status but no report (shouldn't happen).
        orch._db.update_task_active_chain(parent_task_id, None)
        return "wake"

    action = compute_advance_action(chain=chain, report=report)
    if action.kind == "wake":
        orch._db.update_task_active_chain(parent_task_id, None)
        return "wake"

    # Advance: spawn next leg.
    next_child_id = orch._db.next_task_id()  # match the existing id-mint helper
    full_brief = action.next_leg.prompt + "\n\n" + build_prior_leg_context(
        child_task_id=child_task_id, report=report,
    )
    orch._db.insert_task(
        task_id=next_child_id,
        team=parent.team,
        brief=full_brief,
        parent_task_id=parent_task_id,
        assigned_agent=action.next_leg.agent,
        session_timeout_seconds=parent.session_timeout_seconds,
    )
    # Persist updated chain (step_index advanced).
    chain.step_index = action.next_step_index
    orch._db.update_task_active_chain(parent_task_id, chain.serialize())
    # Audit row (does NOT bump orchestration_step_count).
    orch._audit.log_chain_auto_advance(
        parent_task_id=parent_task_id,
        leg_index=action.next_step_index,
        spawned_child_id=next_child_id,
        triggering_child_id=child_task_id,
        triggering_verdict=report.verdict,
        chain_origin_step_audit_id=chain.step_audit_id,
    )
    # Enqueue the new child for execution.
    if orch._queue is not None:
        orch._queue.put_nowait(orch._slug, next_child_id)
    return "advance"
```

Then in `_enqueue_parent_if_waiting` (around line 1018), insert a chain branch BEFORE the existing logic that checks `parent.block_kind == BlockKind.DELEGATED`. Locate the early returns; add right after `parent.status != TaskStatus.BLOCKED` returns:

```python
# Chain-advance branch: if the parent has an active chain and the just-
# completed child terminated cleanly, try to auto-advance to the next leg
# instead of waking the parent. Failed children skip this branch and fall
# through to the cascade-fail path below.
child = orch._db.get_task(task_id)
if (
    child is not None
    and child.status == TaskStatus.COMPLETED
    and parent.active_chain is not None
):
    outcome = _advance_chain_for_completed_child(
        orch=orch, parent_task_id=parent.id, child_task_id=task_id,
    )
    if outcome == "advance":
        return  # next leg spawned; parent stays blocked-delegated
    # outcome == "wake" → chain cleared; fall through to normal sibling-
    # check + parent-wake path below.
```

You may need to add a small DB helper `Database.get_latest_completion_report(task_id) -> CompletionReport | None` if one doesn't exist. Pattern:

```python
def get_latest_completion_report(self, task_id: str):
    from src.models import CompletionReport
    with self._lock:
        row = self._conn.execute(
            "SELECT * FROM task_results WHERE task_id = ? "
            "ORDER BY id DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    if row is None:
        return None
    return CompletionReport(
        task_id=task_id,
        agent=row["agent"],
        status=row["status"],
        confidence=row["confidence_score"],
        output_summary=row["output_summary"] or "",
        verdict=row["verdict"] if "verdict" in row.keys() else None,
        artifact_dir=row["artifact_dir"],
    )
```

If `task_results` doesn't yet have a `verdict` column, add it in this task:

```python
try:
    self._conn.execute("ALTER TABLE task_results ADD COLUMN verdict TEXT")
except sqlite3.OperationalError:
    pass
```

And update `insert_task_result` to persist `verdict` (read the current signature; add `verdict: str | None = None` and include it in the INSERT).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_run_step_chain.py -v -k chain_branch`
Expected: PASS.

Also run the full unit suite to catch regressions:

Run: `uv run pytest tests/ -v -m "not integration"`
Expected: PASS, no regressions.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py src/infrastructure/database.py tests/unit/test_run_step_chain.py
git commit -m "feat(run_step): auto-advance chain on verdict match instead of waking manager"
```

---

### Task 9: Regression test — `chain_complete` final-leg wakes manager + step-count accounting

**Files:**
- Test: `tests/unit/test_run_step_chain.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/unit/test_run_step_chain.py`:

```python
def test_chain_final_leg_wakes_manager_and_clears_chain(tmp_path):
    """When the LAST leg matches its expected verdict, parent wakes (not
    auto-done) and active_chain is cleared."""
    from src.infrastructure.database import Database
    from src.orchestrator.chain import ChainState
    from src.models import ChainLeg, TaskStatus, BlockKind

    db = Database(tmp_path / "x.db")
    db.insert_task(task_id="TASK-P", team="eng", brief="p", parent_task_id=None)
    db.update_task("TASK-P", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    chain = ChainState(
        step_index=1,  # final leg in flight (the only entry in `legs`)
        first_leg_expect_verdict=None,
        legs=[ChainLeg(agent="qa", prompt="q", expect_verdict="PASS")],
        step_audit_id=1,
    )
    db.update_task_active_chain("TASK-P", chain.serialize())

    db.insert_task(task_id="TASK-C2", team="eng", brief="qa", parent_task_id="TASK-P")
    db.update_task("TASK-C2", status=TaskStatus.COMPLETED, assigned_agent="qa")
    db.insert_task_result(
        task_id="TASK-C2", agent="qa", session_id="s",
        status="completed", confidence_score=90,
        output_summary="all green", verdict="PASS",
    )

    from src.orchestrator.run_step import _advance_chain_for_completed_child
    out = _advance_chain_for_completed_child(
        orch=_orch_with_db(db),
        parent_task_id="TASK-P",
        child_task_id="TASK-C2",
    )
    assert out == "wake"
    assert db.get_task("TASK-P").active_chain is None


def test_chain_step_count_not_bumped_on_auto_advance(tmp_path):
    """The whole point of chains: auto-advancing legs must not consume the
    parent's orchestration_step_count budget."""
    from src.infrastructure.database import Database
    from src.orchestrator.chain import ChainState
    from src.models import ChainLeg, TaskStatus, BlockKind

    db = Database(tmp_path / "x.db")
    db.insert_task(task_id="TASK-P", team="eng", brief="p", parent_task_id=None)
    db.update_task("TASK-P", status=TaskStatus.BLOCKED, block_kind=BlockKind.DELEGATED)
    initial_count = db.get_task("TASK-P").orchestration_step_count

    chain = ChainState(
        step_index=0, first_leg_expect_verdict=None,
        legs=[
            ChainLeg(agent="sr", prompt="r", expect_verdict="APPROVE"),
            ChainLeg(agent="qa", prompt="q", expect_verdict="PASS"),
        ],
        step_audit_id=1,
    )
    db.update_task_active_chain("TASK-P", chain.serialize())

    # Walk through two auto-advances.
    for child_idx, (cid, verdict) in enumerate([
        ("TASK-C1", None),         # ungated first leg
        ("TASK-C2", "APPROVE"),    # second leg expects APPROVE
    ]):
        db.insert_task(task_id=cid, team="eng", brief="x", parent_task_id="TASK-P")
        db.update_task(cid, status=TaskStatus.COMPLETED, assigned_agent="w")
        db.insert_task_result(
            task_id=cid, agent="w", session_id="s",
            status="completed", confidence_score=80, output_summary="ok",
            verdict=verdict,
        )
        from src.orchestrator.run_step import _advance_chain_for_completed_child
        _advance_chain_for_completed_child(
            orch=_orch_with_db(db),
            parent_task_id="TASK-P",
            child_task_id=cid,
        )

    final = db.get_task("TASK-P")
    assert final.orchestration_step_count == initial_count  # zero growth
```

- [ ] **Step 2: Run to verify they pass**

Run: `uv run pytest tests/unit/test_run_step_chain.py -v -k "final_leg or step_count_not_bumped"`
Expected: PASS (regression guards on already-implemented Task 8 behavior).

- [ ] **Step 3: No implementation needed — these are pure regression guards.**

- [ ] **Step 4: Re-run the chain test suite**

Run: `uv run pytest tests/unit/test_run_step_chain.py tests/unit/test_chain.py -v`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/test_run_step_chain.py
git commit -m "test(run_step): regression guards for final-leg wake + step-count invariant"
```

---

### Task 10: Chain summary injected into parent's wake history

**Files:**
- Modify: `src/orchestrator/run_step.py` (`_build_prior_steps_from_db`)
- Test: `tests/unit/test_run_step_chain.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/unit/test_run_step_chain.py`:

```python
def test_chain_summary_appended_to_prior_steps_when_chain_just_cleared(tmp_path):
    """After a chain clears (success or abort), the next time the manager
    wakes, its `prior_steps` history includes a synthetic summary row so the
    manager can read 'chain succeeded' or 'chain aborted at leg N' without
    re-deriving from raw child records."""
    from src.infrastructure.database import Database
    from src.orchestrator.run_step import _build_prior_steps_from_db
    from src.models import TaskStatus

    db = Database(tmp_path / "x.db")
    db.insert_task(task_id="TASK-P", team="eng", brief="p", parent_task_id=None)
    # Three children: two completed, one most-recent reported APPROVE then chain ended.
    for cid in ("TASK-C1", "TASK-C2", "TASK-C3"):
        db.insert_task(task_id=cid, team="eng", brief=cid, parent_task_id="TASK-P")
        db.update_task(cid, status=TaskStatus.COMPLETED, note="ok")

    # Write a chain_auto_advance audit row from C1→C2, simulating a chain that
    # ran. The chain_summary helper reads chain_auto_advance rows to build the
    # synthetic summary.
    db.insert_audit_log(
        task_id="TASK-P", agent="orchestrator",
        action="chain_auto_advance",
        payload={
            "leg_index": 1, "spawned_child_id": "TASK-C2",
            "triggering_child_id": "TASK-C1", "triggering_verdict": None,
            "chain_origin_step_audit_id": 1,
        },
    )

    # Build prior_steps; expect last entry to be the chain summary.
    from unittest.mock import MagicMock
    orch = MagicMock()
    orch._db = db
    steps = _build_prior_steps_from_db(orch, "TASK-P")
    # Last step should be the synthetic chain summary.
    assert any(
        "chain" in s.action.lower() or "chain" in s.result_summary.lower()
        for s in steps
    )
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/unit/test_run_step_chain.py::test_chain_summary_appended_to_prior_steps_when_chain_just_cleared -v`
Expected: FAIL — no chain summary in prior_steps.

- [ ] **Step 3: Inject chain summary into `_build_prior_steps_from_db`**

In `src/orchestrator/run_step.py`, after the existing loop in `_build_prior_steps_from_db` builds steps from children, append a chain summary if any `chain_auto_advance` rows exist for the task:

```python
def _build_prior_steps_from_db(orch: "Orchestrator", task_id: str):
    from src.models import StepRecord
    steps: list[StepRecord] = []
    for i, child_id in enumerate(orch._db.get_children(task_id), start=1):
        child = orch._db.get_task(child_id)
        if child is None:
            continue
        success = child.status == TaskStatus.COMPLETED
        steps.append(StepRecord(
            step_number=i,
            agent=child.assigned_agent or "unknown",
            action=f"delegate: {(child.brief or '')[:100]}",
            result_summary=child.note or "(no summary)",
            success=success,
        ))
    # Append chain summary if a chain ran since last manager wake.
    chain_summary = _summarize_recent_chain(orch, task_id)
    if chain_summary is not None:
        steps.append(StepRecord(
            step_number=len(steps) + 1,
            agent="orchestrator",
            action="chain summary",
            result_summary=chain_summary,
            success=True,
        ))
    return steps


def _summarize_recent_chain(orch: "Orchestrator", parent_task_id: str) -> str | None:
    """Return a one-line summary of the most-recent chain that ran under
    parent_task_id, or None if no chain_auto_advance audit rows exist.

    Reads chain_auto_advance rows; pairs them with the most recent
    chain-terminal child to produce e.g. 'Chain: 3 legs, all completed (TASK-1
    → TASK-2 → TASK-3), final verdict PASS' or 'Chain aborted at leg 2:
    TASK-2 returned REQUEST_CHANGES (expected APPROVE)'.
    """
    rows = [
        r for r in orch._db.get_audit_logs(parent_task_id)
        if r["action"] == "chain_auto_advance"
    ]
    if not rows:
        return None
    advances = rows  # already chronological per insertion order
    triggers = [a["payload"]["triggering_child_id"] for a in advances]
    spawned = [a["payload"]["spawned_child_id"] for a in advances]
    chain_children = triggers + ([spawned[-1]] if spawned else [])
    # Final child's terminal verdict (the chain's last word).
    last_child_id = chain_children[-1]
    last_report = orch._db.get_latest_completion_report(last_child_id)
    last_verdict = last_report.verdict if last_report else None
    arrow = " → ".join(chain_children)
    if last_report and last_report.status == "blocked":
        return f"Chain aborted at {last_child_id}: self-blocked"
    if last_verdict is not None:
        return f"Chain: {len(chain_children)} legs ({arrow}), final verdict {last_verdict}"
    return f"Chain: {len(chain_children)} legs ({arrow})"
```

(The summary text format is illustrative — adapt to the spec's documented strings: `Chain summary: …`, `Chain aborted at leg N: …`. Match the spec wording in section "Observability → Wake reason on parent".)

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/unit/test_run_step_chain.py::test_chain_summary_appended_to_prior_steps_when_chain_just_cleared -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/unit/test_run_step_chain.py
git commit -m "feat(run_step): inject chain summary into manager's wake history"
```

---

### Task 11: Surface `active_chain` in daemon task-detail route + regen OpenAPI + TS types

**Files:**
- Modify: `src/daemon/routes/tasks.py` (task detail response)
- Modify: `tests/contract/openapi.json` (regen via env var)
- Modify: `web/src/lib/api/types.ts`
- Test: `tests/unit/test_routes_tasks.py` if exists; otherwise `tests/integration/`

- [ ] **Step 1: Write the failing test**

Locate the task-detail route test (search: `grep -rn "blocked_on_jobs" tests/`). Add an assertion that `active_chain` appears in the response, parsed as a JSON object when set:

```python
def test_task_detail_includes_parsed_active_chain(client, fresh_org):
    # Create a task with active_chain set.
    org = fresh_org
    org.db.insert_task(task_id="TASK-1", team="eng", brief="x", parent_task_id=None)
    org.db.update_task_active_chain(
        "TASK-1",
        '{"step_index":0,"first_leg_expect_verdict":"APPROVE",'
        '"legs":[{"agent":"sr","prompt":"r","expect_verdict":"APPROVE"}],'
        '"step_audit_id":1}',
    )
    resp = client.get(f"/api/v1/orgs/{org.slug}/tasks/TASK-1")
    assert resp.status_code == 200
    body = resp.json()
    assert "active_chain" in body
    chain = body["active_chain"]
    assert chain["step_index"] == 0
    assert chain["first_leg_expect_verdict"] == "APPROVE"
    assert chain["legs"][0]["agent"] == "sr"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/ -v -k task_detail_includes_parsed_active_chain`
Expected: FAIL — `active_chain` not in response.

- [ ] **Step 3: Update the route + types**

In `src/daemon/routes/tasks.py`, find the task detail handler (around line 140-170 per the grep above). Add `active_chain` parsing alongside the existing `blocked_on_jobs` block:

```python
import json as _json

# ... in the handler, after blocked_on_jobs is built:
active_chain = None
if task.active_chain is not None:
    try:
        active_chain = _json.loads(task.active_chain)
    except _json.JSONDecodeError:
        active_chain = None  # defensive — never 500 on bad on-disk state

return {
    "task": _task_to_dict(task),
    "results": org.db.get_task_results(task_id),
    "audit_log": audit_log,
    "revisit_chain": chain,
    "direct_revisits": direct_revisits,
    "predecessor_prior_status": prior_status,
    "blocked_on_jobs": blocked_on_jobs,
    "active_chain": active_chain,
}
```

In `web/src/lib/api/types.ts`, find `TaskDetailResponse` (around line 79) and add:

```typescript
export interface ChainLegResponse {
  agent: string;
  prompt: string;
  expect_verdict: string | null;
}

export interface ActiveChainResponse {
  step_index: number;
  first_leg_expect_verdict: string | null;
  legs: ChainLegResponse[];
  step_audit_id: number;
}

export interface TaskDetailResponse {
  // ...existing fields...
  active_chain: ActiveChainResponse | null;
}
```

(Edit the existing interface in place; don't duplicate.)

- [ ] **Step 4: Run tests + regen OpenAPI snapshot**

Run:
```
uv run pytest tests/ -v -k task_detail_includes_parsed_active_chain
HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py
cd web && npm test -- --run openapi-coverage
```
Expected: PASS, openapi.json updated, TS coverage test still passes.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py web/src/lib/api/types.ts tests/contract/openapi.json tests/  # whichever route test file
git commit -m "feat(api): expose parsed active_chain in task detail response"
```

---

### Task 12: CLI render — `happyranch details` chain block

**Files:**
- Modify: `src/cli.py` (details command)
- Test: Manual smoke (no formal CLI test infra; verify by running against a seeded task)

- [ ] **Step 1: Locate the details command's rendering block**

Open `src/cli.py`, find the `details` command handler. Look for where `blocked_on_jobs` is rendered (per earlier grep: line 380). The chain block goes right above or below it. The render template:

```python
if body.get("active_chain"):
    chain = body["active_chain"]
    total_legs = 1 + len(chain.get("legs", []))
    current_idx = chain.get("step_index", 0)
    print(f"\nCurrent workflow chain (step {current_idx + 1} of {total_legs}):")
    # Leg 1 is the implicit first delegate; we don't have its agent/prompt
    # here (lives in the orchestration_step audit row referenced by
    # step_audit_id). Show first leg as "(see step audit)".
    print(f"  {'▶' if current_idx == 0 else '✓'} Leg 1  (first leg — see orchestration_step audit)")
    for i, leg in enumerate(chain.get("legs", []), start=2):
        if current_idx == i - 1:
            marker = "▶"
        elif current_idx >= i:
            marker = "✓"
        else:
            marker = "⋯"
        verdict_note = f" (expecting: {leg['expect_verdict']})" if leg.get("expect_verdict") else ""
        print(f"  {marker} Leg {i}  {leg['agent']:<14} {leg['prompt'][:40]}{verdict_note}")
```

- [ ] **Step 2: Add it under the existing render**

Insert the block above into `src/cli.py` near line 380 (where `blocked_on_jobs` rendering lives). Order: after revisit chain, before audit summary.

- [ ] **Step 3: Smoke-test manually**

```bash
# Start daemon if not running:
scripts/daemon.sh status || scripts/daemon.sh start

# Seed a task with an active chain via a quick Python REPL or the test
# helper, then:
uv run happyranch --org tourism-org details TASK-X
```

Expected: "Current workflow chain (step …)" block appears between the existing sections.

- [ ] **Step 4: Re-run unit suite (no regressions)**

Run: `uv run pytest tests/ -v -m "not integration"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): render Current workflow chain block in details"
```

---

### Task 13: Web UI chain-strip in `TaskDetailPane`

**Files:**
- Modify: `web/src/features/tasks/TaskDetailPane.tsx`
- Test: `web/src/features/tasks/TasksPage.test.tsx`

- [ ] **Step 1: Write the failing test**

Add to `web/src/features/tasks/TasksPage.test.tsx`:

```typescript
import { render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TaskDetailPane } from './TaskDetailPane';

describe('TaskDetailPane — workflow chain strip', () => {
  it('renders the chain strip when active_chain is set', async () => {
    // Mock getTask to return a task with active_chain.
    // (Reuse the test setup pattern from existing TaskDetailPane tests; if
    // none, adapt the pattern from TasksPage.test.tsx:94's blocked-on-jobs
    // suite which already mocks getTask.)
    // ...mock setup that returns:
    //   active_chain: {
    //     step_index: 1,
    //     first_leg_expect_verdict: null,
    //     legs: [
    //       {agent: 'senior_dev', prompt: 'review', expect_verdict: 'APPROVE'},
    //       {agent: 'qa_engineer', prompt: 'qa', expect_verdict: 'PASS'},
    //     ],
    //     step_audit_id: 14,
    //   }
    // ...

    render(/* QueryClientProvider + TaskDetailPane */);
    expect(await screen.findByText(/Workflow chain/i)).toBeInTheDocument();
    expect(screen.getByText(/senior_dev/)).toBeInTheDocument();
    expect(screen.getByText(/qa_engineer/)).toBeInTheDocument();
    expect(screen.getByText(/expecting: APPROVE/)).toBeInTheDocument();
  });

  it('does not render the chain strip when active_chain is null', async () => {
    // ...mock returns active_chain: null
    render(/* ... */);
    expect(screen.queryByText(/Workflow chain/i)).not.toBeInTheDocument();
  });
});
```

(Adapt the mock setup to mirror the existing `blocked-on-jobs` describe block in `TasksPage.test.tsx:94`.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd web && npm test -- --run TasksPage.test`
Expected: FAIL — no chain strip rendered.

- [ ] **Step 3: Add the chain-strip component to `TaskDetailPane.tsx`**

In `web/src/features/tasks/TaskDetailPane.tsx`, locate where blocked-on-jobs or similar metadata is rendered. Insert a chain strip:

```typescript
import type { ActiveChainResponse } from '../../lib/api/types';

function WorkflowChainStrip({ chain }: { chain: ActiveChainResponse }): JSX.Element {
  const totalLegs = 1 + chain.legs.length;
  const currentIdx = chain.step_index;

  return (
    <div className="rounded border border-zinc-700 bg-zinc-900/40 p-3 text-sm">
      <div className="mb-2 font-semibold text-zinc-200">
        Workflow chain — step {currentIdx + 1} of {totalLegs}
      </div>
      <ol className="space-y-1">
        <li className="flex gap-2">
          <span aria-hidden>{currentIdx === 0 ? '▶' : '✓'}</span>
          <span>Leg 1 (first leg)</span>
          {chain.first_leg_expect_verdict && (
            <span className="text-zinc-400">expecting: {chain.first_leg_expect_verdict}</span>
          )}
        </li>
        {chain.legs.map((leg, i) => {
          const legNum = i + 2;
          const marker = currentIdx === legNum - 1 ? '▶' : currentIdx >= legNum ? '✓' : '⋯';
          return (
            <li key={legNum} className="flex gap-2">
              <span aria-hidden>{marker}</span>
              <span className="font-mono">{leg.agent}</span>
              <span className="text-zinc-400 truncate">{leg.prompt}</span>
              {leg.expect_verdict && (
                <span className="text-zinc-500">expecting: {leg.expect_verdict}</span>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
```

Then in the `TaskDetailPane` body, render conditionally:

```typescript
{task.active_chain && <WorkflowChainStrip chain={task.active_chain} />}
```

Place it above the orchestration-steps timeline (find that section by searching for `audit_log` or `orchestration_step` rendering in the file).

- [ ] **Step 4: Run tests**

Run:
```
cd web && npm test -- --run TasksPage.test
cd web && npx tsc --noEmit
```
Expected: tests PASS, no type errors.

- [ ] **Step 5: Commit**

```bash
git add web/src/features/tasks/TaskDetailPane.tsx web/src/features/tasks/TasksPage.test.tsx
git commit -m "feat(web): render workflow chain strip in TaskDetailPane"
```

---

### Task 14: Integration test — full E2E chain via fake-claude harness

**Files:**
- Create: `tests/integration/test_chain_e2e.py`

- [ ] **Step 1: Write the test**

Create `tests/integration/test_chain_e2e.py`:

```python
"""End-to-end chain tests using the fake-claude harness.

Spawns a real daemon + fake CLIs. The manager declares a 3-leg chain; the
fake workers emit verdicts via $FAKE_CLAUDE_PLAN. Asserts:
- Auto-advance fires exactly len(legs)-1 times when all verdicts match
- Parent's orchestration_step_count is bumped exactly twice (declare + final)
- Mismatched verdict aborts the chain and wakes the manager
"""
from __future__ import annotations

import pytest


@pytest.mark.integration
def test_chain_happy_path_e2e(daemon_with_fake_claude, fake_claude_plan_env):
    """Manager declares 3-leg chain; all workers green; chain auto-advances
    twice; manager wakes once at the end."""
    # Adapt to the existing fake-claude fixture pattern. See
    # tests/integration/test_threads_e2e.py for the harness shape.
    # The plan should script:
    #   1. Manager session: emit decision with then=[<leg2>, <leg3>] and
    #      expect_verdict on each.
    #   2. Worker 1 (first leg): emit status=completed, no verdict.
    #   3. Worker 2 (leg 2): emit status=completed, verdict=APPROVE.
    #   4. Worker 3 (leg 3): emit status=completed, verdict=PASS.
    #   5. Manager session (final wake): emit decision action=done.
    # Then assert:
    #   - 2 chain_auto_advance audit rows on parent.
    #   - parent.orchestration_step_count == 2 (declare + final).
    #   - parent.status == completed.
    pass  # TODO: flesh out using daemon_with_fake_claude fixture pattern


@pytest.mark.integration
def test_chain_aborts_on_verdict_mismatch_e2e(daemon_with_fake_claude, fake_claude_plan_env):
    """Manager declares 3-leg chain; leg 2 worker emits REQUEST_CHANGES; chain
    aborts at leg 2, manager wakes with mismatch context, no leg 3 spawn."""
    pass  # TODO


@pytest.mark.integration
def test_chain_aborts_on_founder_cancel_e2e(daemon_with_fake_claude, fake_claude_plan_env):
    """Founder cancels parent mid-chain (during leg 2 in-flight). active_chain
    is cleared, no leg 3 spawn, parent ends in failed-cancelled."""
    pass  # TODO
```

- [ ] **Step 2: Implement the test bodies**

Open `tests/integration/test_threads_e2e.py` for the fixture/harness pattern. Replace the `pass # TODO` placeholders with concrete plan setup using the fixture. The plan format for `$FAKE_CLAUDE_PLAN` is sourced as bash from `tests/integration/fake_claude.sh`; check that script for the variable shape and write a plan that maps `(task_id, session_id, agent, org_slug)` tuples to emitted completion payloads.

Critical: the manager's completion payload's `decision` field must include `then` with the next two legs and `expect_verdict` on each. The fake workers' payloads must include a top-level `verdict` field.

- [ ] **Step 3: Run the integration tests**

Run: `uv run pytest tests/integration/test_chain_e2e.py -v -m integration`
Expected: 3 PASS.

- [ ] **Step 4: Run the full integration suite to check for regressions**

Run: `uv run pytest tests/ -v -m integration`
Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_chain_e2e.py
git commit -m "test(integration): E2E chain happy path, mismatch abort, cancel abort"
```

---

### Task 15: Protocol docs — completion-contract + start-task SKILL

**Files:**
- Modify: `protocol/00-completion-contract.md`
- Modify: `protocol/skills/start-task/SKILL.md`

- [ ] **Step 1: Update `protocol/00-completion-contract.md`**

In the "Task completion report" section, add `verdict` to the optional payload keys. After the existing example payload, add:

```markdown
For review/QA-type workers, optionally include a structured verdict:

```json
{
  "task_id": "...",
  "session_id": "...",
  "agent": "senior_dev",
  "status": "completed",
  "confidence": 92,
  "summary": "Code review complete. All 7 verification rows green...",
  "verdict": "APPROVE"
}
```

`verdict` is a free-string field. Each team's workflow KB entry documents the allowed values (e.g. engineering uses `APPROVE | REQUEST_CHANGES | BLOCK` for reviews; `PASS | REVISE | BLOCK` for QA). Omit when not applicable. Inline delegation chains (see `decision.then` below) use this field to gate auto-advance.
```

In the "Manager decision field" section, add documentation for chain shape after the existing examples:

```markdown
### Inline delegation chains

A manager can declare a multi-leg workflow in one decision via `decision.then` (additional legs) and per-leg `expect_verdict` gates:

```json
{
  "task_id": "...",
  "session_id": "...",
  "agent": "engineering_head",
  "status": "completed",
  "summary": "Dispatching Item 1a small-feature gate chain.",
  "decision": {
    "action": "delegate",
    "agent": "dev_agent",
    "prompt": "Build Item 1a Gallery uplift...",
    "then": [
      {"agent": "senior_dev",  "prompt": "Code-review the PR described in prior-leg context.", "expect_verdict": "APPROVE"},
      {"agent": "qa_engineer", "prompt": "QA the PR described in prior-leg context.",          "expect_verdict": "PASS"}
    ]
  }
}
```

The orchestrator spawns the first leg, then auto-advances to the next leg on each child terminal whose `verdict` matches the leg's `expect_verdict`. Any mismatch (or `status=blocked`) clears the chain and wakes the manager. The final leg's match wakes the manager too — chains do not auto-`done`. Each subsequent leg's brief is auto-suffixed with a "Prior leg context" block (the upstream worker's summary + verdict + artifact_dir).

Step-budget effect: declaring a chain consumes one orchestration step; auto-advances do NOT consume steps. A 4-leg chain that runs cleanly costs 2 steps (declare + final wake) vs. 4 today.

Cross-team validation runs on every leg at decision-parse time; any off-team agent rejects the whole decision via the feedback mechanism.

See `docs/superpowers/specs/2026-05-30-inline-delegation-chain-design.md`.
```

- [ ] **Step 2: Update `protocol/skills/start-task/SKILL.md`**

Find the completion-payload template section. Add one bullet:

```markdown
- If your role is to issue a verdict (code review, QA, design review, etc.), include `"verdict": "<value>"` in your payload. Free string; your team's workflow KB entry documents the vocabulary. Optional — workers without verdicts simply omit the field.
```

- [ ] **Step 3: Verify docs render**

Run: `grep -A2 "verdict" protocol/00-completion-contract.md protocol/skills/start-task/SKILL.md | head -40`
Expected: New mentions present.

- [ ] **Step 4: Re-run a sanity test suite to confirm nothing imports from these docs accidentally**

Run: `uv run pytest tests/ -v -m "not integration" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add protocol/00-completion-contract.md protocol/skills/start-task/SKILL.md
git commit -m "docs(protocol): document verdict field + inline delegation chains"
```

---

## Self-Review

**1. Spec coverage:**
- §Schema additions → Tasks 1, 2 ✓
- §Storage (active_chain shape, first_leg_expect_verdict mirror) → Task 1 (column), Task 3 (ChainState) ✓
- §Control flow on declare → Task 7 ✓
- §Control flow on child terminal → Task 8 ✓
- §Auto-appended Prior Leg Context → Task 3 (`build_prior_leg_context`), Task 8 (wire-up) ✓
- §Step-budget accounting (no-bump on auto-advance) → Task 8 implementation, Task 9 regression guard ✓
- §Observability (CLI, audit, web, wake-reason history) → Task 4 (audit), Task 10 (wake-reason), Task 11 (route), Task 12 (CLI), Task 13 (web) ✓
- §Failure modes (mismatch, blocked, no-verdict, founder cancel, cross-team) → Tasks 5, 6, 8, 9; cancel covered by integration Task 14 ✓
- §Migration → Task 1 (idempotent ALTER) ✓
- §Testing — unit + integration → Tasks 3, 5, 6, 7, 8, 9, 10 (unit); Task 14 (integration); Task 11 (contract) ✓
- §Load-bearing invariants → Tasks 8, 9 (no-bump, cross-team-all-legs); auto-revisit-doesn't-preserve covered by existing cascade behavior unchanged ✓
- §Protocol docs (start-task SKILL + completion-contract) → Task 15 ✓

No gaps.

**2. Placeholder scan:** Task 14 has `pass # TODO` placeholders inside the test bodies and instructs the engineer to flesh them out from the existing fixture pattern. This is acceptable — the integration-test harness is project-specific and the engineer must read `tests/integration/test_threads_e2e.py` to write authentic test bodies. The plan provides the assertion contract explicitly. Other tasks have full executable code.

**3. Type consistency:**
- `ChainLeg` fields: `agent`, `prompt`, `expect_verdict` — consistent in Tasks 2, 3, 5, 6, 7, 8, 11, 13.
- `ChainState` fields: `step_index`, `first_leg_expect_verdict`, `legs`, `step_audit_id` — consistent in Tasks 3, 4, 7, 8, 9, 10, 11.
- `compute_advance_action` return: `AdvanceAction.kind in {"advance", "wake"}` — consistent in Tasks 3, 8.
- `_advance_chain_for_completed_child` return: `"advance" | "wake"` strings — consistent in Tasks 8, 9.
- `log_chain_auto_advance` kwargs: `parent_task_id`, `leg_index`, `spawned_child_id`, `triggering_child_id`, `triggering_verdict`, `chain_origin_step_audit_id` — consistent in Tasks 4, 8, 10, 11.
- `update_task_active_chain(task_id, active_chain: str | None)` — consistent in Tasks 4, 7, 8.

No mismatches.
