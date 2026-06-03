# Sub-tasks / Type-Driven Orchestration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let any agent that owns a top-level task spawn and orchestrate sub-tasks (woken on each sub-task terminal), by re-gating orchestration on `task_type` instead of the `manager` role.

**Architecture:** Add `TaskRecord.task_type ∈ {task, subtask}` (provenance: `subtask` = spawned-from-an-ongoing-task). Flip the decision-parse gate in `run_step` from `is_team_manager(agent)` to `task.task_type == "task"`. Non-manager owners may delegate to themselves only; managers keep own-team scope plus self. Sub-tasks are leaf-only (strict two-level topology → termination stays bounded by the existing per-task step budget). Escalation is unchanged (founder). Bundle removal of the dead legacy `type`-column compat machinery.

**Tech Stack:** Python 3.13, Pydantic v2, SQLite (WAL), FastAPI, pytest. Spec: `docs/superpowers/specs/2026-06-03-subtask-composite-task-design.md`.

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `src/models.py` | `TaskRecord` dataclass/model | Add `task_type` field |
| `src/infrastructure/database.py` | Schema + task persistence | Add `task_type` column (CREATE+ALTER); drop legacy `type`; remove legacy compat; carry `task_type` in insert/hydrate |
| `src/orchestrator/run_step.py` | The `run_step` algorithm | Gate flip; target-scope validation; lift self-delegation ban; revision-count exemption; child `task_type="subtask"`; prompt branch |
| `src/orchestrator/capabilities.py` | Orchestrator prompt text | `self_only` reduced variant |
| `src/daemon/routes/tasks.py` | Dispatch route | `SubmitTask.owner` + assignment |
| `src/cli.py` | `happyranch run` | `--owner` flag |
| `web/src/lib/api/tasks.ts` + `tests/contract/openapi.json` | Contract pin | `owner` field + snapshot regen |
| `protocol/00-completion-contract.md` | Agent-facing contract doc | Document type=task owners + self-delegation |
| `tests/test_database.py` | DB tests | Rewrite legacy-`type` tests; add `task_type` round-trip |
| `tests/test_run_step.py` | run_step unit tests | Gate, validation, child type, revision-count |
| `tests/test_capabilities.py` | Prompt tests | `self_only` variant |
| `tests/integration/test_subtask_self_decompose_e2e.py` | End-to-end | Founder→worker type=task → self sub-task → wake → done |

---

## Task 1: Add `TaskRecord.task_type` field

**Files:**
- Modify: `src/models.py:35-64` (TaskRecord)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_database.py`:

```python
def test_task_type_defaults_to_task():
    from src.models import TaskRecord
    t = TaskRecord(id="TASK-001", brief="x")
    assert t.task_type == "task"


def test_task_type_accepts_subtask():
    from src.models import TaskRecord
    t = TaskRecord(id="TASK-002", brief="x", task_type="subtask")
    assert t.task_type == "subtask"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_database.py::test_task_type_defaults_to_task -v`
Expected: FAIL — `TaskRecord` has no field `task_type` (Pydantic ignores the kwarg, so `t.task_type` raises `AttributeError`).

- [ ] **Step 3: Add the field**

In `src/models.py`, inside `class TaskRecord`, add right after the `team: str = "engineering"` line:

```python
    # Provenance, NOT a behavior label: "subtask" iff spawned from an ongoing
    # task; "task" otherwise (founder-dispatched root). The orchestration gate
    # in run_step keys on this — see
    # docs/superpowers/specs/2026-06-03-subtask-composite-task-design.md.
    task_type: Literal["task", "subtask"] = "task"
```

(`Literal` is already imported at `src/models.py:6`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_database.py::test_task_type_defaults_to_task tests/test_database.py::test_task_type_accepts_subtask -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_database.py
git commit -m "feat(models): add TaskRecord.task_type (task|subtask)"
```

---

## Task 2: Persist & hydrate `task_type` (DB column + insert + get_task)

**Files:**
- Modify: `src/infrastructure/database.py` — CREATE TABLE (`:201-214`), ALTER block (`:475-522`), `insert_task` (`:651-696`), `get_task` (`:699-727`)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

```python
def test_task_type_round_trips(tmp_path):
    from src.infrastructure.database import Database
    from src.models import TaskRecord
    db = Database(tmp_path / "rt.db")
    db.insert_task(TaskRecord(id="TASK-001", brief="root", task_type="task"))
    db.insert_task(TaskRecord(id="TASK-002", brief="child", task_type="subtask"))
    assert db.get_task("TASK-001").task_type == "task"
    assert db.get_task("TASK-002").task_type == "subtask"
    db.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_database.py::test_task_type_round_trips -v`
Expected: FAIL — `get_task` doesn't set `task_type` (defaults to `"task"`, so the `"subtask"` assertion fails), or `insert_task` errors on the missing column.

- [ ] **Step 3: Add the column to CREATE TABLE**

In `src/infrastructure/database.py`, in the `tasks` `CREATE TABLE IF NOT EXISTS` (around `:201-214`), add a line after `brief TEXT NOT NULL,`:

```sql
                task_type TEXT NOT NULL DEFAULT 'task',
```

- [ ] **Step 4: Add the column to the idempotent ALTER block**

In the `for ddl in (...)` migration tuple at `:475-522`, add this entry (anywhere in the tuple):

```python
            "ALTER TABLE tasks ADD COLUMN task_type TEXT NOT NULL DEFAULT 'task'",
```

(The surrounding `try/except sqlite3.OperationalError: pass` makes it idempotent across restarts.)

- [ ] **Step 5: Carry `task_type` in `insert_task`**

In `insert_task` (`:651-670`), append `task.task_type` to the `params` tuple as the LAST element:

```python
            task.orchestration_step_count,
            task.session_timeout_seconds,
            task.task_type,
        )
```

Then in the non-legacy INSERT (`:687-694`) add `task_type` as the last column and one more `?`:

```python
            self._conn.execute(
                """INSERT INTO tasks (id, status, assigned_agent, team, brief,
                   revision_count, created_at, updated_at, completed_at, parent_task_id,
                   revisit_of_task_id, dispatched_from_talk_id, dispatched_from_thread_id,
                   block_kind, note,
                   orchestration_step_count, session_timeout_seconds, task_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                params,
            )
```

NOTE: the legacy `if self._tasks_has_legacy_type_column:` branch is removed entirely in Task 3 — for now, ALSO update that legacy branch's INSERT to keep the test green if you run Task 2 in isolation. Simplest: append `, task_type` + one `?` there too, and `(params[0], "general") + params[1:]` already carries the new last element. If executing Tasks 2 and 3 back-to-back, you may skip patching the legacy branch and let Task 3 delete it.

- [ ] **Step 6: Hydrate `task_type` in `get_task`**

In `get_task` (`:704-727`), add to the `TaskRecord(...)` kwargs:

```python
            task_type=row["task_type"],
```

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/test_database.py::test_task_type_round_trips -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): persist and hydrate tasks.task_type"
```

---

## Task 3: Remove dead legacy `type`-column machinery

**Files:**
- Modify: `src/infrastructure/database.py` — `__init__` (`:66,69`), `_detect_legacy_columns` (`:76-87`), `insert_task` legacy branch (`:671-685`), ALTER block (`:475-522`)
- Test: `tests/test_database.py` (rewrite 2 legacy tests)

Background: the legacy `type` column is never read; only the `"general"` sentinel is written. We drop the column and delete the detector. See spec §1 "Legacy `type`-column cleanup".

- [ ] **Step 1: Rewrite the two legacy tests to the new expectation**

Find `test_*legacy_type*` and `test_fresh_db_has_no_legacy_type_column` in `tests/test_database.py` (around `:790-832`). Replace them with:

```python
def test_legacy_type_column_is_dropped_on_open(tmp_path):
    """A pre-Task-4 DB with a legacy `type TEXT NOT NULL` column: opening it
    via Database() drops the column, and inserts still work."""
    import sqlite3
    from src.infrastructure.database import Database
    from src.models import TaskRecord

    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            assigned_agent TEXT,
            team TEXT NOT NULL DEFAULT 'engineering',
            brief TEXT NOT NULL,
            revision_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            parent_task_id TEXT
        )"""
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    cols = {r[1] for r in db._conn.execute("PRAGMA table_info(tasks)").fetchall()}
    assert "type" not in cols          # legacy column dropped
    assert "task_type" in cols         # new column present

    db.insert_task(TaskRecord(id="TASK-001", brief="legacy schema test"))
    got = db.get_task("TASK-001")
    assert got is not None and got.task_type == "task"
    db.close()
```

(Delete `test_fresh_db_has_no_legacy_type_column` — the concept it asserts no longer exists.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_database.py::test_legacy_type_column_is_dropped_on_open -v`
Expected: FAIL — `type` is still in `cols` (the DROP migration doesn't exist yet).

- [ ] **Step 3: Add the DROP migration**

In the same `for ddl in (...)` tuple at `:475-522`, add:

```python
            # Legacy cleanup: drop the dead `type` column (dropped from the
            # current schema in the Task-4 refactor; never read, only a
            # "general" sentinel was written). Idempotent via the try/except
            # below — DROP of an absent column raises OperationalError.
            "ALTER TABLE tasks DROP COLUMN type",
```

- [ ] **Step 4: Delete the detector + flag + legacy insert branch**

- In `__init__`, delete the line `self._tasks_has_legacy_type_column: bool = False` (`:66`) and the call `self._detect_legacy_columns()` (`:69`).
- Delete the entire `_detect_legacy_columns` method (`:76-87`).
- In `insert_task`, delete the `if self._tasks_has_legacy_type_column:` branch (`:671-685`) so only the `else` INSERT (now unconditional) remains. Remove the now-orphaned `else:` and dedent its body.

- [ ] **Step 5: Run the full DB test module**

Run: `uv run pytest tests/test_database.py -v`
Expected: PASS (including the rewritten legacy test; no references to `_tasks_has_legacy_type_column` remain).

- [ ] **Step 6: Verify no dangling references**

Run: `grep -rn "_tasks_has_legacy_type_column\|_detect_legacy_columns" src/ tests/`
Expected: no output.

- [ ] **Step 7: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "refactor(db): drop dead legacy tasks.type column + compat code"
```

---

## Task 4: Flip the orchestration gate to `task_type == "task"`

**Files:**
- Modify: `src/orchestrator/run_step.py:299` (gate)
- Test: `tests/test_run_step.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_run_step.py` (uses the existing `_make_result`, `_make_report`, `_SlugQueue`, `runtime`, `db` fixtures):

```python
def test_non_manager_owner_of_task_type_emits_decision(runtime, db, monkeypatch):
    """A type=task owned by a NON-manager parses its decision (done here)."""
    import json
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="T-1", brief="root", assigned_agent="dev_agent", task_type="task",
    ))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps({"action": "done", "summary": "did it"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")
    t = db.get_task("T-1")
    assert t.status == TaskStatus.COMPLETED
    assert t.note == "did it"


def test_subtask_owner_is_leaf_even_if_decision_present(runtime, db, monkeypatch):
    """A type=subtask owner does NOT orchestrate: a delegate decision in its
    report is ignored and the task simply completes (leaf path)."""
    import json
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="T-2", brief="leaf", assigned_agent="engineering_head",
        task_type="subtask",
    ))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        # Even though this is a manager AND emits a delegate, the subtask
        # gate forces leaf completion.
        return _make_result(), _make_report(
            output_summary=json.dumps(
                {"action": "delegate", "agent": "dev_agent", "prompt": "go"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-2")
    t = db.get_task("T-2")
    assert t.status == TaskStatus.COMPLETED          # leaf — no child spawned
    assert db.get_children("T-2") == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_run_step.py::test_non_manager_owner_of_task_type_emits_decision tests/test_run_step.py::test_subtask_owner_is_leaf_even_if_decision_present -v`
Expected: FAIL — currently `dev_agent` (non-manager) is forced to `done` regardless of decision (test 1 passes by luck since decision is "done") and the manager subtask (test 2) parses the delegate and spawns a child.

- [ ] **Step 3: Flip the gate**

In `src/orchestrator/run_step.py`, change the gate at `:299`:

```python
    # before:
    #   if orch.teams.is_team_manager(agent):
    # after — orchestration is driven by task TYPE, not manager role.
    # A type=task owner (any agent) speaks the NextStep protocol; a
    # type=subtask is leaf-only. `task` is the early-fetched record (line 33);
    # task_type is immutable provenance, safe to read post-claim.
    if task.task_type == "task":
        decision = orch._parse_next_step(report)
        _step_audit_id = orch._audit.log_orchestration_step(
            task_id, next_count, decision.model_dump(exclude_none=True),
        )
    else:
        from src.models import NextStep
        decision = NextStep(action="done", summary=report.output_summary)
        _step_audit_id = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_run_step.py::test_non_manager_owner_of_task_type_emits_decision tests/test_run_step.py::test_subtask_owner_is_leaf_even_if_decision_present -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Run the full run_step suite (regression guard)**

Run: `uv run pytest tests/test_run_step.py tests/test_run_step_chain.py -v`
Expected: PASS. NOTE: existing tests dispatch root tasks to `engineering_head` without setting `task_type` → defaults to `"task"`, so manager behavior is preserved. If any existing test created a *worker-owned* root expecting leaf behavior, it now orchestrates — fix by setting `task_type="subtask"` on that fixture (none expected; verify).

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/run_step.py tests/test_run_step.py
git commit -m "feat(orchestrator): gate decision-parse on task_type==task, not manager role"
```

---

## Task 5: Spawn child sub-tasks with `task_type="subtask"`

**Files:**
- Modify: `src/orchestrator/run_step.py:413-421` (child `TaskRecord` construction)
- Test: `tests/test_run_step.py`

- [ ] **Step 1: Write the failing test**

```python
def test_delegated_child_is_typed_subtask(runtime, db, monkeypatch):
    import json
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="T-1", brief="root", assigned_agent="engineering_head",
        task_type="task",
    ))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake_run_agent(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps(
                {"action": "delegate", "agent": "dev_agent", "prompt": "build"}),
        )
    monkeypatch.setattr(orch, "_run_agent", fake_run_agent)

    orch.run_step("T-1")
    children = db.get_children("T-1")
    assert len(children) == 1
    assert db.get_task(children[0]).task_type == "subtask"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_run_step.py::test_delegated_child_is_typed_subtask -v`
Expected: FAIL — child defaults to `task_type="task"`.

- [ ] **Step 3: Type the child as subtask**

In `src/orchestrator/run_step.py`, in the `child = TaskRecord(...)` construction at `:413-421`, add `task_type="subtask"`:

```python
        child = TaskRecord(
            id=child_id,
            team=task.team,
            brief=decision.prompt or "",
            assigned_agent=decision.agent,
            parent_task_id=task_id,
            status=TaskStatus.PENDING,
            session_timeout_seconds=task.session_timeout_seconds,
            task_type="subtask",
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_run_step.py::test_delegated_child_is_typed_subtask -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/test_run_step.py
git commit -m "feat(orchestrator): spawn delegated children as task_type=subtask"
```

---

## Task 6: Target-scope validation (self-only for non-managers; team∪self for managers)

**Files:**
- Modify: `src/orchestrator/run_step.py` — add `_legs_out_of_scope`, replace the `_chain_legs_off_team` call site (`:364-390`), delete `_chain_legs_off_team` (`:502-524`)
- Test: `tests/test_run_step.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_non_manager_self_delegation_is_allowed(runtime, db, monkeypatch):
    """dev_agent owns a type=task and delegates to ITSELF → child spawned."""
    import json
    from src.orchestrator.orchestrator import Orchestrator
    # dev_agent needs a workspace dir for _validate_one_leg to pass.
    (runtime.workspaces_dir / "dev_agent").mkdir(parents=True, exist_ok=True)
    db.insert_task(TaskRecord(id="T-1", brief="root",
                              assigned_agent="dev_agent", task_type="task"))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps(
                {"action": "delegate", "agent": "dev_agent", "prompt": "phase 2"}))
    monkeypatch.setattr(orch, "_run_agent", fake)

    orch.run_step("T-1")
    children = db.get_children("T-1")
    assert len(children) == 1
    assert db.get_task(children[0]).assigned_agent == "dev_agent"


def test_non_manager_cross_agent_delegation_is_rejected(runtime, db, monkeypatch):
    """dev_agent owning a type=task may NOT delegate to product_manager →
    feedback step, task re-enqueued PENDING, no child."""
    import json
    from src.orchestrator.orchestrator import Orchestrator
    (runtime.workspaces_dir / "product_manager").mkdir(parents=True, exist_ok=True)
    db.insert_task(TaskRecord(id="T-1", brief="root",
                              assigned_agent="dev_agent", task_type="task"))
    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps(
                {"action": "delegate", "agent": "product_manager", "prompt": "x"}))
    monkeypatch.setattr(orch, "_run_agent", fake)

    orch.run_step("T-1")
    assert db.get_children("T-1") == []
    assert db.get_task("T-1").status == TaskStatus.PENDING   # re-enqueued for re-decide
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_run_step.py::test_non_manager_self_delegation_is_allowed tests/test_run_step.py::test_non_manager_cross_agent_delegation_is_rejected -v`
Expected: FAIL — current `_chain_legs_off_team(manager=dev_agent)` returns `team_for_manager("dev_agent") is None` → treats EVERY leg (including self) as off-team → both reject.

- [ ] **Step 3: Add `_legs_out_of_scope` and delete `_chain_legs_off_team`**

In `src/orchestrator/run_step.py`, delete the `_chain_legs_off_team` function (`:502-524`) and add:

```python
def _legs_out_of_scope(orch: "Orchestrator", owner: str, decision) -> list[tuple[str, str]]:
    """Return [(agent_name, reason)] for delegation legs `owner` may not target.

    - Manager owner: may target agents on its own team, or itself.
    - Non-manager owner: may target ONLY itself (self-decomposition).

    Empty list = all legs in scope.
    """
    targets = [decision.agent] + [leg.agent for leg in (decision.then or [])]
    out: list[tuple[str, str]] = []
    if orch.teams.is_team_manager(owner):
        caller_team = orch.teams.team_for_manager(owner)
        for a in targets:
            if not a or a == owner:        # self always allowed
                continue
            t = orch.teams.team_for_agent(a)
            if caller_team is None or t != caller_team:
                out.append((a, f"on team {t!r}" if t else "not on a team"))
    else:
        for a in targets:
            if not a or a == owner:
                continue
            out.append((a, "non-manager owners may only delegate to themselves"))
    return out
```

- [ ] **Step 4: Replace the call site**

In the `delegate` branch, replace the block at `:364-390` (from `off_team_legs = _chain_legs_off_team(...)` through its `return`) with:

```python
        # Target-scope guard. Managers: own-team agents or self. Non-manager
        # owners: self only. Violations feed a feedback step back (not a hard
        # fail) so the owner can correct its decision next step.
        out_of_scope = _legs_out_of_scope(orch, owner=agent, decision=decision)
        if out_of_scope:
            parts = [f"{name!r} ({reason})" for name, reason in out_of_scope]
            if orch.teams.is_team_manager(agent):
                caller_team = orch.teams.team_for_manager(agent)
                feedback = (
                    f"Invalid delegation: you are on team {caller_team!r}, but "
                    f"{'; '.join(parts)}. Pick agents on your own team or "
                    "yourself, or escalate."
                )
            else:
                feedback = (
                    f"Invalid delegation: {'; '.join(parts)}. You may only "
                    f"delegate sub-tasks to yourself ({agent!r}), or escalate."
                )
            db.insert_task_result(
                task_id=task_id,
                agent=agent,
                session_id="",
                status="completed",
                confidence_score=0,
                output_summary=feedback,
                risks_flagged=[],
            )
            orch._audit.log_orchestration_step(
                task_id, next_count, {"action": "feedback", "reason": feedback},
            )
            db.update_task(task_id, status=TaskStatus.PENDING, block_kind=None)
            if orch._queue is not None:
                orch._queue.put_nowait(orch._slug, task_id)
            return
```

- [ ] **Step 5: Update/remove old `_chain_legs_off_team` tests**

Run: `grep -rn "_chain_legs_off_team" tests/`
For each hit, port the assertion to `_legs_out_of_scope(orch, owner=<manager>, decision=...)` semantics (same off-team behavior for managers) or delete if redundant with Step 1's tests.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_run_step.py tests/test_run_step_chain.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/orchestrator/run_step.py tests/test_run_step.py
git commit -m "feat(orchestrator): self-only delegation scope for non-manager owners"
```

---

## Task 7: Allow manager self-targeting (roster + revision-count exemption)

**Files:**
- Modify: `src/orchestrator/run_step.py` — `_list_candidate_agents` (`:592` discard line), revision-count bump (`:406-411`)
- Test: `tests/test_run_step.py`

- [ ] **Step 1: Write the failing test**

```python
def test_manager_self_target_does_not_bump_revision_count(runtime, db, monkeypatch):
    """A manager re-delegating to ITSELF is sequencing, not a revise loop —
    revision_count must stay 0 so escalate-after-2-rounds doesn't misfire."""
    import json
    from src.orchestrator.orchestrator import Orchestrator
    (runtime.workspaces_dir / "engineering_head").mkdir(parents=True, exist_ok=True)
    # One already-completed self-child makes engineering_head the worker-of-record.
    db.insert_task(TaskRecord(id="T-1", brief="root",
                              assigned_agent="engineering_head", task_type="task"))
    db.insert_task(TaskRecord(id="T-1-c1", brief="c1",
                              assigned_agent="engineering_head",
                              parent_task_id="T-1", task_type="subtask"))
    db.update_task("T-1-c1", status=TaskStatus.COMPLETED)

    orch = Orchestrator(db=db, settings=Settings(max_orchestration_steps=10),
                        paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    orch._queue = _SlugQueue()

    def fake(task_id, agent, prompt, on_session_started=None):
        return _make_result(), _make_report(
            output_summary=json.dumps(
                {"action": "delegate", "agent": "engineering_head",
                 "prompt": "phase 2"}))
    monkeypatch.setattr(orch, "_run_agent", fake)

    orch.run_step("T-1")
    assert db.get_task("T-1").revision_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_run_step.py::test_manager_self_target_does_not_bump_revision_count -v`
Expected: FAIL — `worker_of_record == decision.agent` is True (both `engineering_head`), so `revision_count` becomes 1.

- [ ] **Step 3: Exempt self-targets from the revision bump**

In `src/orchestrator/run_step.py:410`, change:

```python
            if worker_of_record == decision.agent:
                db.increment_revision_count(task_id)
```

to:

```python
            # Self-targeted delegation is a sequence step (self-decomposition),
            # NOT a revise cycle — only bump when re-delegating to a DIFFERENT
            # worker-of-record. `agent` is this task's owner.
            if worker_of_record == decision.agent and decision.agent != agent:
                db.increment_revision_count(task_id)
```

- [ ] **Step 4: Stop discarding self from the candidate roster**

In `_list_candidate_agents` (`:592`), delete the line:

```python
    team_members.discard(calling_manager)  # manager should not delegate to itself
```

(Managers may now self-target, so the roster should offer self. The validation in Task 6 already permits it.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_run_step.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/run_step.py tests/test_run_step.py
git commit -m "feat(orchestrator): allow manager self-targeting without revision bump"
```

---

## Task 8: Prompt surface — `self_only` reduced capabilities + `_build_agent_prompt` branch

**Files:**
- Modify: `src/orchestrator/capabilities.py:6-12` (signature) + body
- Modify: `src/orchestrator/run_step.py:531-578` (`_build_agent_prompt`)
- Test: `tests/test_capabilities.py`, `tests/test_run_step.py`

- [ ] **Step 1: Write the failing tests**

In `tests/test_capabilities.py`:

```python
def test_self_only_prompt_omits_roster_and_names_self():
    from src.orchestrator.capabilities import build_capabilities_prompt
    p = build_capabilities_prompt(
        agents=[], step_number=1, max_steps=10,
        manager_name="dev_agent", self_only=True,
    )
    assert "Available Agents" not in p          # no team roster
    assert "dev_agent" in p                       # delegate-to-self target named
    assert '"action": "delegate"' in p
    assert '"action": "done"' in p
    assert '"action": "escalate"' in p
```

In `tests/test_run_step.py`:

```python
def test_build_agent_prompt_leaf_subtask_is_empty(runtime, db):
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.run_step import _build_agent_prompt
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    t = TaskRecord(id="T-1", brief="x", assigned_agent="dev_agent",
                   task_type="subtask")
    assert _build_agent_prompt(orch, t, "dev_agent") == ""


def test_build_agent_prompt_non_manager_task_is_self_only(runtime, db):
    from src.orchestrator.orchestrator import Orchestrator
    from src.orchestrator.run_step import _build_agent_prompt
    orch = Orchestrator(db=db, settings=Settings(), paths=runtime, slug="test",
                        teams=TeamsRegistry.load(runtime.root))
    t = TaskRecord(id="T-1", brief="x", assigned_agent="dev_agent",
                   task_type="task")
    p = _build_agent_prompt(orch, t, "dev_agent")
    assert "Available Agents" not in p
    assert "dev_agent" in p
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_capabilities.py::test_self_only_prompt_omits_roster_and_names_self tests/test_run_step.py::test_build_agent_prompt_leaf_subtask_is_empty tests/test_run_step.py::test_build_agent_prompt_non_manager_task_is_self_only -v`
Expected: FAIL — `build_capabilities_prompt` has no `self_only` param; `_build_agent_prompt` still gates on `is_team_manager` (returns `""` for `dev_agent`).

- [ ] **Step 3: Add `self_only` to `build_capabilities_prompt`**

In `src/orchestrator/capabilities.py`, change the signature:

```python
def build_capabilities_prompt(
    agents: list[dict],
    step_number: int,
    max_steps: int,
    prior_steps: list[StepRecord] | None = None,
    manager_name: str = "team_manager",
    self_only: bool = False,
) -> str:
```

Immediately after the docstring and the `pretty = ...` line, add an early-return self-only branch:

```python
    if self_only:
        me = manager_name
        sections = [
            "## Your Orchestration Capabilities\n",
            f"You own this task ({me}). You can do the work yourself in this "
            "session, OR break it into a sequence of sub-tasks that YOU "
            "execute — each sub-task is a fresh session you'll be woken from "
            "when it finishes, so you can decide the next step with a clean "
            "context.\n",
            "### Response Format (MANDATORY)\n",
            "Your completion payload MUST include a top-level `decision` field "
            "(a single JSON object). If you omit it, the task escalates to the "
            "founder — the orchestrator will NOT infer intent from prose.\n",
            "Choose EXACTLY ONE shape:\n",
            "**delegate** -- spawn the next sub-task (assigned to YOURSELF):",
            "```json",
            f'{{"action": "delegate", "agent": "{me}", "prompt": "<instructions for the next sub-task>"}}',
            "```",
            "The `agent` MUST be yourself "
            f"(`{me}`) — you may only delegate sub-tasks to yourself.\n",
            "**done** -- the whole task is complete:",
            "```json",
            '{"action": "done", "summary": "<what was accomplished>"}',
            "```\n",
            "**escalate** -- needs founder attention:",
            "```json",
            '{"action": "escalate", "reason": "<why>"}',
            "```\n",
            "### Constraints\n",
            f"- This is step {step_number} of maximum {max_steps}",
            "- Org-specific authority limits come from your role_guidance / "
            "system prompt — escalate anything outside them.",
        ]
        if prior_steps:
            sections.append("\n### Prior Steps\n")
            for step in prior_steps:
                status = "OK" if step.success else "FAILED"
                sections.append(
                    f"**Step {step.step_number}** [{step.agent}] {step.action} -- "
                    f"{step.result_summary} ({status})"
                )
        return "\n".join(sections)
```

- [ ] **Step 4: Branch `_build_agent_prompt` on `task_type`**

In `src/orchestrator/run_step.py`, replace the head of `_build_agent_prompt` (`:545-565`) — from `from ... import build_capabilities_prompt` through the `base = build_capabilities_prompt(...)` call — with:

```python
    from src.orchestrator.capabilities import build_capabilities_prompt
    if task.task_type != "task":
        return ""   # leaf sub-task: per-task instruction is the brief
    from src.orchestrator import prompt_loader
    is_mgr = orch.teams.is_team_manager(agent)
    agents_for_prompt: list[dict] = []
    if is_mgr:
        for name in _list_candidate_agents(orch, agent):
            candidate = prompt_loader.load_agent(orch._paths, name)
            desc = (candidate.description if candidate is not None else None) or name
            agents_for_prompt.append({"name": name, "description": desc})
    prior_steps = _build_prior_steps_from_db(orch, task.id)
    base = build_capabilities_prompt(
        agents=agents_for_prompt,
        step_number=task.orchestration_step_count + 1,
        max_steps=orch._settings.max_orchestration_steps,
        prior_steps=prior_steps,
        manager_name=agent,
        self_only=not is_mgr,
    )
```

(The `headers`/`revisit`/`resume`/`resolved` tail below this point is unchanged.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_capabilities.py tests/test_run_step.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/capabilities.py src/orchestrator/run_step.py tests/test_capabilities.py tests/test_run_step.py
git commit -m "feat(orchestrator): type-gated prompt with self-only orchestration variant"
```

---

## Task 9: Founder dispatch `--owner` (route)

**Files:**
- Modify: `src/daemon/routes/tasks.py:76-105` (`SubmitTask` + `submit_task`)
- Test: `tests/daemon/test_routes_tasks_owner.py` (uses the `tmp_home`, `app`, `auth_headers` fixtures from `tests/daemon/conftest.py`; org `alpha` seeds team `engineering` with manager `engineering_head` and worker `dev_agent`)

Decision (resolves the spec's open item): `--owner` must name a registered agent (`org.teams.all_agents()`); `--team` is still supplied (default `engineering`) and provides routing/escalation context. The owner is assigned instead of the team manager; `task_type` stays `"task"`.

- [ ] **Step 1: Write the failing test**

Create `tests/daemon/test_routes_tasks_owner.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient


def test_dispatch_with_owner_assigns_owner_not_manager(tmp_home, app, auth_headers):
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        headers=auth_headers,
        json={"brief": "do it", "team": "engineering", "owner": "dev_agent"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["assigned_agent"] == "dev_agent"


def test_dispatch_without_owner_defaults_to_manager(tmp_home, app, auth_headers):
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        headers=auth_headers,
        json={"brief": "do it", "team": "engineering"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["assigned_agent"] == "engineering_head"


def test_dispatch_with_unknown_owner_is_400(tmp_home, app, auth_headers):
    r = TestClient(app).post(
        "/api/v1/orgs/alpha/tasks",
        headers=auth_headers,
        json={"brief": "x", "team": "engineering", "owner": "ghost"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "unknown_owner"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/daemon/test_routes_tasks_owner.py -v`
Expected: FAIL — `test_dispatch_with_owner_assigns_owner_not_manager` returns `engineering_head` (route ignores `owner`), and `test_dispatch_with_unknown_owner_is_400` returns 200 instead of 400.

- [ ] **Step 3: Add `owner` to the request model + assignment**

In `src/daemon/routes/tasks.py`, extend `SubmitTask`:

```python
class SubmitTask(BaseModel):
    team: str | None = None
    brief: str
    owner: str | None = None   # assign a specific agent (default: team manager)
```

In `submit_task`, after the team-validation block and before `manager = registry.manager_for_team(team)`, resolve the assignee:

```python
    if body.owner is not None:
        if body.owner not in registry.all_agents():
            raise HTTPException(
                status_code=400,
                detail={"code": "unknown_owner", "owner": body.owner,
                        "valid": registry.all_agents()},
            )
        assigned = body.owner
    else:
        assigned = registry.manager_for_team(team).name
```

Then use `assigned` in the `TaskRecord(..., assigned_agent=assigned)` and the return dict (`"assigned_agent": assigned`). Remove the now-unused `manager = registry.manager_for_team(team)` line if `assigned` covers both branches.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/test_routes_tasks_owner.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py tests/daemon/test_routes_tasks_owner.py
git commit -m "feat(daemon): dispatch --owner assigns a specific agent to a root task"
```

---

## Task 10: CLI `--owner` for `happyranch run`

**Files:**
- Modify: `src/cli.py:208-243` (`cmd_run`), `:2563-2575` (`p_run` parser)

- [ ] **Step 1: Add the flag to the parser**

In `src/cli.py`, in the `# happyranch run` block (`:2563`), after the `--team` argument:

```python
    p_run.add_argument(
        "--owner", default=None,
        help="Assign the task to a specific agent (default: the team manager)",
    )
```

- [ ] **Step 2: Thread it into the payload**

In `cmd_run` (`:237-239`), after `if args.team: payload["team"] = args.team`:

```python
    if args.owner:
        payload["owner"] = args.owner
```

- [ ] **Step 3: Smoke-test the CLI parses it**

Run: `uv run happyranch run --help`
Expected: output lists `--owner`.

- [ ] **Step 4: Commit**

```bash
git add src/cli.py
git commit -m "feat(cli): happyranch run --owner"
```

---

## Task 11: Contract pin — OpenAPI snapshot + TS mirror

**Files:**
- Modify: `web/src/lib/api/tasks.ts` (the `submitTask` function + its body type)
- Regenerate: `tests/contract/openapi.json`
- Test: `tests/contract/test_openapi_snapshot.py`, `web/src/test/openapi-coverage.test.ts`

- [ ] **Step 1: Run the contract test to confirm it fails (drift detected)**

Run: `uv run pytest tests/contract/test_openapi_snapshot.py -v`
Expected: FAIL — the live OpenAPI now includes `owner` on `SubmitTask`, diverging from the pinned snapshot.

- [ ] **Step 2: Regenerate the OpenAPI snapshot**

Run: `HAPPYRANCH_REGEN_OPENAPI=1 uv run pytest tests/contract/test_openapi_snapshot.py`
Expected: snapshot rewritten; test passes on re-run.

- [ ] **Step 3: Mirror `owner` in the TS client**

In `web/src/lib/api/tasks.ts`, add `owner?: string` to the `submitTask` request body type/params and include it in the POST body (match the existing optional-field pattern for `team`).

- [ ] **Step 4: Run the TS coverage test**

Run: `cd web && npm run test -- openapi-coverage`
Expected: PASS (the `/tasks` path stays in `INCLUDED_PATHS`; the new field is reflected).

- [ ] **Step 5: Commit**

```bash
git add web/src/lib/api/tasks.ts tests/contract/openapi.json
git commit -m "chore(contract): mirror dispatch owner field in OpenAPI + TS client"
```

---

## Task 12: Update the completion-contract protocol doc

**Files:**
- Modify: `protocol/00-completion-contract.md`

- [ ] **Step 1: Document the generalization**

In the "Manager decision field" section of `protocol/00-completion-contract.md`, add a subsection (no code test — prose doc):

> **Who emits a `decision`:** Any agent that owns a **`task_type=task`** task — not only `role: manager` agents. A `task_type=subtask` owner is a leaf: it reports `status` + `output_summary` and never emits a `decision`.
>
> **Self-delegation (self-decomposition):** A non-manager owner may `delegate` only to **itself** — spawning the next sub-task in a sequence it runs, getting woken on each terminal. Managers may delegate to own-team agents or to themselves. Cross-agent delegation by a non-manager is rejected with feedback.
>
> **Escalation** routes to the founder (unchanged).

- [ ] **Step 2: Commit**

```bash
git add protocol/00-completion-contract.md
git commit -m "docs(protocol): task_type owners emit decisions; self-delegation"
```

---

## Task 13: End-to-end integration test (self-decomposition)

**Files:**
- Create: `tests/integration/test_subtask_self_decompose_e2e.py`
- Reference pattern: `tests/integration/test_chain_e2e.py`, `tests/integration/fake_claude.sh`, `tests/integration/conftest.py`

- [ ] **Step 1: Study the existing e2e harness**

Run: `sed -n '1,80p' tests/integration/test_chain_e2e.py` and read `tests/integration/conftest.py` for `fake_claude_plan_env`. The fake CLI sources `$FAKE_CLAUDE_PLAN` with `(task_id, session_id, agent, org_slug)` and writes a completion JSON via `happyranch report-completion --from-file`.

- [ ] **Step 2: Write the e2e test**

Create `tests/integration/test_subtask_self_decompose_e2e.py`. The flow to assert:
1. Founder dispatches a root with `owner=dev_agent` (a non-manager worker), `team=engineering`.
2. `FAKE_CLAUDE_PLAN`: on the **root** session (`task_type=task`), emit `decision={"action":"delegate","agent":"dev_agent","prompt":"phase 2"}`.
3. On the **sub-task** session (`task_type=subtask`, a leaf), emit `status=completed` with a plain summary (no decision).
4. On the **root wake** (after sub-task terminal), emit `decision={"action":"done","summary":"all phases complete"}`.

Concrete plan script (bash, sourced by `fake_claude.sh` — branch on `agent`/brief; mirror `test_chain_e2e.py`'s plan style):

```bash
# $1=task_id $2=session_id $3=agent $4=org_slug ; brief available to the harness
# Root (TASK-NNN, no parent) first wake → delegate to self
# Sub-task (TASK-NNN-… child) → leaf complete
# Root second wake → done
```

Assertions via the CLI/daemon:
- The child task exists with `task_type == "subtask"` and `assigned_agent == "dev_agent"`.
- The root task reaches `COMPLETED` with note "all phases complete".
- The root was woken exactly twice (two `orchestration_step` audit rows).

NOTE: branching the fake plan on "is this the root or the child" is done by inspecting `task_id` shape / a marker in the brief, exactly as `test_chain_e2e.py` distinguishes legs. Copy that mechanism rather than inventing a new one.

- [ ] **Step 3: Run the integration test**

Run: `uv run pytest tests/integration/test_subtask_self_decompose_e2e.py -v -m integration`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_subtask_self_decompose_e2e.py
git commit -m "test(integration): e2e self-decomposition (task spawns self sub-task)"
```

---

## Final verification

- [ ] **Run the full unit suite**

Run: `uv run pytest tests/ -v`
Expected: all PASS.

- [ ] **Run the integration suite** (daemon-lifecycle surface — required per CLAUDE.md)

Run: `uv run pytest tests/ -v -m integration`
Expected: all PASS.

- [ ] **Spec coverage self-check** — confirm each spec section maps to a task:
  - §1 representation + migration + legacy cleanup → Tasks 1, 2, 3
  - §2 gate → Task 4
  - §3 target scope + self-target un-ban + revision exemption → Tasks 6, 7
  - §4 topology (child=subtask, leaf can't spawn) → Tasks 4, 5
  - §6 escalation (no change) → covered by Task 4 regression (escalate still → founder)
  - §7 prompt surface → Task 8
  - §5 entry points (founder `--owner`) → Tasks 9, 10, 11
  - protocol doc → Task 12; e2e → Task 13
