# Thread Talk Token Usage Scope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist and report token usage for direct thread invocations while preserving existing task token reporting and making talk attribution explicit.

**Architecture:** Extend the existing `session_token_usage` fact table with nullable scope columns instead of creating a second table. Task execution continues to write task-scoped rows, and `thread_runner.py` writes thread-scoped rows when an executor result includes `TokenUsage`. The token route and database helpers add scope filters and rollups while treating legacy null scope values as task rows.

**Tech Stack:** Python 3.13, SQLite, FastAPI, pytest, existing HappyRanch `Database`, `ThreadRunner`, and token route helpers.

---

### Task 1: Scoped Token Storage

**Files:**
- Modify: `runtime/infrastructure/database.py`
- Test: `tests/test_session_token_usage_db.py`

- [ ] **Step 1: Write failing storage tests**

Add tests for a thread-scoped row, default task scope, and grouping by scope/thread/talk:

```python
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
```

- [ ] **Step 2: Verify storage tests fail**

Run: `uv run pytest tests/test_session_token_usage_db.py -v`

Expected: failures because `insert_session_token_usage` does not accept scope keyword arguments.

- [ ] **Step 3: Implement storage schema and helpers**

In `Database._init_schema`, add `ALTER TABLE ... ADD COLUMN` guards for the new nullable columns and indexes on `scope_type/scope_id`, `thread_id`, and `talk_id`.

Extend:

```python
def insert_session_token_usage(
    self,
    *,
    task_id: str | None,
    agent: str,
    session_id: str,
    executor: str,
    token_usage: TokenUsage,
    scope_type: str = "task",
    scope_id: str | None = None,
    thread_id: str | None = None,
    talk_id: str | None = None,
    invocation_purpose: str | None = None,
) -> None:
    ...
```

Default `scope_id` to `task_id` when `scope_type == "task"`.

Extend list and aggregate helpers with `scope_type`, `scope_id`, `thread_id`, `talk_id`, and `purpose` filters, plus `aggregate_session_token_usage_by_scope`, `aggregate_session_token_usage_by_thread`, and `aggregate_session_token_usage_by_talk`.

- [ ] **Step 4: Verify storage tests pass**

Run: `uv run pytest tests/test_session_token_usage_db.py -v`

Expected: all tests in that file pass.

### Task 2: Token Route Scope Support

**Files:**
- Modify: `runtime/daemon/routes/tokens.py`
- Test: `tests/daemon/test_tokens_route.py`

- [ ] **Step 1: Write failing route tests**

Add tests asserting `/tokens?scope_type=thread`, `/tokens?group_by=thread`, `/tokens?group_by=talk`, and invalid group values.

- [ ] **Step 2: Verify route tests fail**

Run: `uv run pytest tests/daemon/test_tokens_route.py -v`

Expected: failures because the route rejects new `group_by` values or ignores scope filters.

- [ ] **Step 3: Implement route filters and docs**

Update the module docstring and route docstring to explain task, thread, and talk scopes. Accept query parameters `scope_type`, `scope_id`, `thread_id`, `talk_id`, and `purpose`. Accept `group_by` values `agent`, `task`, `scope`, `thread`, and `talk`, dispatching to the matching database aggregate helper.

- [ ] **Step 4: Verify route tests pass**

Run: `uv run pytest tests/daemon/test_tokens_route.py -v`

Expected: all token route tests pass.

### Task 3: Thread Runner Persistence

**Files:**
- Modify: `runtime/daemon/thread_runner.py`
- Test: `tests/test_thread_runner.py`

- [ ] **Step 1: Write failing thread runner tests**

Extend `FakeExecutorResult` to accept `token_usage`. Add tests for:

1. successful no-callback thread invocation writes a thread-scoped token row when `token_usage` is present
2. failed no-callback thread invocation writes a thread-scoped token row when `token_usage` is present

- [ ] **Step 2: Run thread runner tests to verify failure**

Run: `uv run pytest tests/test_thread_runner.py -v`

Expected: new tests fail because `thread_runner.py` does not write token usage rows.

- [ ] **Step 3: Run GitNexus impact checks**

Run GitNexus impact analysis on `run_invocation` and any database helper symbols before editing them. If risk is HIGH or CRITICAL, report it before continuing.

- [ ] **Step 4: Implement thread token persistence**

After executor result creation and before terminal status inspection, call `org_state.db.insert_session_token_usage(...)` when `result.token_usage is not None` using:

```python
task_id=None,
agent=inv.agent_name,
session_id=getattr(result, "session_id", None) or invocation_token,
executor=executor_name,
token_usage=result.token_usage,
scope_type="thread",
scope_id=inv.thread_id,
thread_id=inv.thread_id,
invocation_purpose=inv.purpose.value,
```

Keep this best-effort in the same way event publishing is best-effort: token persistence must not break the invocation lifecycle.

- [ ] **Step 5: Verify thread runner tests pass**

Run: `uv run pytest tests/test_thread_runner.py -v`

Expected: all thread runner tests pass.

### Task 4: Task Row Compatibility And Attribution

**Files:**
- Modify: `runtime/orchestrator/run_step.py`
- Test: `tests/test_run_step_token_usage.py`

- [ ] **Step 1: Write compatibility tests**

Assert task rows now expose `scope_type="task"` and `scope_id=<task_id>` while existing `task_id` filtering still works.

- [ ] **Step 2: Verify compatibility tests fail**

Run: `uv run pytest tests/test_run_step_token_usage.py tests/test_session_token_usage_db.py -v`

Expected: failures until task-scope defaults are implemented.

- [ ] **Step 3: Run GitNexus impact check**

Run GitNexus impact analysis on `run_step_impl` before editing `runtime/orchestrator/run_step.py`.

- [ ] **Step 4: Keep task writes task-scoped**

Update the existing `insert_session_token_usage` call to pass `scope_type="task"` and `scope_id=task_id`. If the current default already does this, leave the call unchanged and rely on the helper defaults.

- [ ] **Step 5: Verify compatibility tests pass**

Run: `uv run pytest tests/test_run_step_token_usage.py tests/test_session_token_usage_db.py -v`

Expected: all selected tests pass.

### Task 5: Final Verification

**Files:**
- Modify only if failures identify missing contract updates.

- [ ] **Step 1: Run affected tests**

Run:

```bash
uv run pytest tests/test_session_token_usage_db.py tests/daemon/test_tokens_route.py tests/test_thread_runner.py tests/test_run_step_token_usage.py -v
```

Expected: all selected tests pass.

- [ ] **Step 2: Run full unit baseline**

Run: `uv run pytest tests/ -v`

Expected: full unit suite passes with integration tests deselected by default.

- [ ] **Step 3: Run GitNexus change detection**

Run `gitnexus_detect_changes(scope="all", repo="happyranch")` and verify changed symbols and processes match token storage, token route, thread runner, and docs.
