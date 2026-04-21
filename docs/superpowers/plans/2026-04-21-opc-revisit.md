# `opc revisit` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `opc revisit <task-id>` — a founder-initiated recovery primitive that spawns a fresh root task inheriting the brief of a terminal predecessor and hands the EH a prompt-header pointer to the frozen lineage.

**Architecture:** Walk `parent_task_id` from any task id to its root under `state.db_lock`; if the root is in a terminal-ish state (`failed`, `failed-cancelled`, `blocked(escalated)`, `completed`), atomically insert a new root row with the predecessor's brief + `task_type`, two audit entries (`revisit_of` on new root, `revisit_spawned` on predecessor), then enqueue. The new root is identical to an `opc run` submission except `_build_agent_prompt` injects a 5-6 line context header on its first orchestration step (detected by presence of `revisit_of` and absence of `orchestration_step` in audit log).

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, SQLite (WAL), httpx, argparse. TTY gate via `sys.stdin.isatty()` / `sys.stdout.isatty()`. No schema migration, no new columns — predecessor ↔ new-root link lives entirely in `audit_log`.

**Spec:** `docs/superpowers/specs/2026-04-21-opc-revisit-design.md`

---

## File Structure

| File | Responsibility |
| ---- | -------------- |
| `src/infrastructure/database.py` | `walk_ancestors(task_id, max_hops=20) -> list[TaskRecord]` helper — follows `parent_task_id` to root. Raises `LineageTooDeep` on overrun. |
| `src/infrastructure/audit_logger.py` | Two new methods: `log_revisit_of(...)` and `log_revisit_spawned(...)`. Payload shapes match the `revisit_of` / `revisit_spawned` action types referenced by the prompt builder. |
| `src/daemon/routes/tasks.py` | New `POST /tasks/{task_id}/revisit` endpoint. Walks ancestors → validates predecessor state → under `db_lock` inserts new `TaskRecord` + two audit entries → `enqueue_task`. |
| `src/orchestrator/run_step.py` | Extend `_build_agent_prompt` (and/or a `_maybe_revisit_header` helper) to prepend a 5-6 line context header when the task has a `revisit_of` audit entry AND no prior `orchestration_step` audit entry. |
| `src/cli.py` | Add `cmd_revisit` with TTY gate + confirmation prompt + argparse subparser. POSTs to the new endpoint, then streams events via the existing `_stream_task_events` helper. |
| `tests/test_database.py` | 2-3 unit tests for `walk_ancestors` (leaf → root, root-is-self, over-limit). |
| `tests/test_audit_logger.py` | Unit tests for the two new methods — payload shape, action value. |
| `tests/daemon/test_routes_tasks.py` | 11 unit tests for the revisit endpoint (happy paths, rejections, edge cases). |
| `tests/test_run_step.py` | 3 unit tests for first-step header injection / second-step absence / founder-note round-trip. |
| `tests/test_cli.py` | 2 unit tests — TTY rejection + confirmation prompt accept/reject. |
| `tests/integration/test_end_to_end.py` | 1 integration test — roundtrip through the real daemon with the fake Claude binary. |
| `CLAUDE.md` | Mention `opc revisit` in the CLI list + link predecessor-via-audit-log. |
| `skills/opc/SKILL.md` | Add revisit to Tasks section; note TTY gate; list under "Confirm with user first". |
| `README.md` | One line in the user-facing command reference. |

---

## Task 1: `walk_ancestors` database helper + unit tests

**Files:**
- Modify: `src/infrastructure/database.py` (add helper + `LineageTooDeep` exception near top of file)
- Modify: `tests/test_database.py` (add 3 tests at bottom)

- [ ] **Step 1: Write failing tests for `walk_ancestors`**

Append to `tests/test_database.py`:

```python
import pytest

from src.infrastructure.database import LineageTooDeep


def test_walk_ancestors_leaf_to_root_returns_chain(db):
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="root"))
    db.insert_task(TaskRecord(
        id="TASK-002", type=TaskType.GENERAL, brief="mid", parent_task_id="TASK-001",
    ))
    db.insert_task(TaskRecord(
        id="TASK-003", type=TaskType.GENERAL, brief="leaf", parent_task_id="TASK-002",
    ))
    chain = db.walk_ancestors("TASK-003")
    assert [t.id for t in chain] == ["TASK-003", "TASK-002", "TASK-001"]


def test_walk_ancestors_root_returns_single_element(db):
    db.insert_task(TaskRecord(id="TASK-001", type=TaskType.GENERAL, brief="root"))
    chain = db.walk_ancestors("TASK-001")
    assert [t.id for t in chain] == ["TASK-001"]


def test_walk_ancestors_raises_when_over_limit(db):
    db.insert_task(TaskRecord(id="TASK-000", type=TaskType.GENERAL, brief="root"))
    prev = "TASK-000"
    for i in range(1, 25):  # 24 descendants + root = 25 hops
        tid = f"TASK-{i:03d}"
        db.insert_task(TaskRecord(
            id=tid, type=TaskType.GENERAL, brief=f"t{i}", parent_task_id=prev,
        ))
        prev = tid
    with pytest.raises(LineageTooDeep):
        db.walk_ancestors(prev, max_hops=20)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_database.py::test_walk_ancestors_leaf_to_root_returns_chain tests/test_database.py::test_walk_ancestors_root_returns_single_element tests/test_database.py::test_walk_ancestors_raises_when_over_limit -v`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'walk_ancestors'` (and `ImportError` on `LineageTooDeep`).

- [ ] **Step 3: Add `LineageTooDeep` exception and `walk_ancestors` method**

At the top of `src/infrastructure/database.py`, after the imports (around line 8):

```python
class LineageTooDeep(Exception):
    """Ancestor walk exceeded the safety bound; indicates data corruption."""
```

Inside the `Database` class, near `get_children` (around line 244):

```python
def walk_ancestors(self, task_id: str, max_hops: int = 20) -> list[TaskRecord]:
    """Return [task, parent, ..., root] by following parent_task_id.

    Raises LineageTooDeep if the walk exceeds max_hops (defensive bound;
    real lineages are 2-4 deep). A missing intermediate task truncates the
    walk silently — callers see the chain they could reconstruct.
    """
    chain: list[TaskRecord] = []
    current_id: str | None = task_id
    for _ in range(max_hops):
        if current_id is None:
            return chain
        task = self.get_task(current_id)
        if task is None:
            return chain
        chain.append(task)
        current_id = task.parent_task_id
    if current_id is not None:
        raise LineageTooDeep(f"walk from {task_id} exceeded {max_hops} hops")
    return chain
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_database.py::test_walk_ancestors_leaf_to_root_returns_chain tests/test_database.py::test_walk_ancestors_root_returns_single_element tests/test_database.py::test_walk_ancestors_raises_when_over_limit -v`
Expected: 3 passed.

- [ ] **Step 5: Run full DB test module to confirm no regression**

Run: `uv run pytest tests/test_database.py -v`
Expected: all existing tests still pass alongside the 3 new ones.

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/database.py tests/test_database.py
git commit -m "feat(db): add walk_ancestors helper for revisit lineage walk"
```

---

## Task 2: Audit logger — `log_revisit_of` + `log_revisit_spawned`

**Files:**
- Modify: `src/infrastructure/audit_logger.py` (append 2 methods)
- Modify: `tests/test_audit_logger.py` (append 2 tests)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_audit_logger.py`:

```python
def test_log_revisit_of_records_predecessor_chain(db):
    from src.infrastructure.audit_logger import AuditLogger
    audit = AuditLogger(db)
    audit.log_revisit_of(
        task_id="TASK-072",
        predecessor_root="TASK-052",
        flagged="TASK-058",
        cascade=["TASK-052", "TASK-053", "TASK-058"],
        prior_status="failed",
        founder_note="PR #103 already merged",
    )
    logs = db.get_audit_logs("TASK-072")
    entry = next(e for e in logs if e["action"] == "revisit_of")
    assert entry["agent"] == "founder"
    assert entry["payload"]["predecessor_root"] == "TASK-052"
    assert entry["payload"]["flagged"] == "TASK-058"
    assert entry["payload"]["cascade"] == ["TASK-052", "TASK-053", "TASK-058"]
    assert entry["payload"]["prior_status"] == "failed"
    assert entry["payload"]["founder_note"] == "PR #103 already merged"


def test_log_revisit_spawned_records_new_root(db):
    from src.infrastructure.audit_logger import AuditLogger
    audit = AuditLogger(db)
    audit.log_revisit_spawned(predecessor_task_id="TASK-052", new_root="TASK-072")
    logs = db.get_audit_logs("TASK-052")
    entry = next(e for e in logs if e["action"] == "revisit_spawned")
    assert entry["agent"] == "founder"
    assert entry["payload"]["new_root"] == "TASK-072"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_audit_logger.py::test_log_revisit_of_records_predecessor_chain tests/test_audit_logger.py::test_log_revisit_spawned_records_new_root -v`
Expected: FAIL with `AttributeError: 'AuditLogger' object has no attribute 'log_revisit_of'`.

- [ ] **Step 3: Implement the two methods**

Append inside `AuditLogger` in `src/infrastructure/audit_logger.py`:

```python
def log_revisit_of(
    self,
    task_id: str,
    predecessor_root: str,
    flagged: str,
    cascade: list[str],
    prior_status: str,
    founder_note: str | None,
) -> None:
    """Record on the NEW root that it is a revisit of `predecessor_root`.

    `cascade` is [predecessor_root, …, flagged] — the chain the founder
    walked from the flagged task back up to the predecessor root. The
    prompt-injection step in run_step reads this entry to build the
    first-step context header.
    """
    self._db.insert_audit_log(
        task_id=task_id,
        agent="founder",
        action="revisit_of",
        payload={
            "predecessor_root": predecessor_root,
            "flagged": flagged,
            "cascade": cascade,
            "prior_status": prior_status,
            "founder_note": founder_note,
        },
    )

def log_revisit_spawned(
    self, predecessor_task_id: str, new_root: str,
) -> None:
    """Record on the predecessor that it spawned a revisit (observational)."""
    self._db.insert_audit_log(
        task_id=predecessor_task_id,
        agent="founder",
        action="revisit_spawned",
        payload={"new_root": new_root},
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_audit_logger.py -v`
Expected: all pass (including the 2 new ones).

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/audit_logger.py tests/test_audit_logger.py
git commit -m "feat(audit): add revisit_of and revisit_spawned log methods"
```

---

## Task 3: `POST /tasks/{id}/revisit` endpoint — happy path (failed predecessor)

**Files:**
- Modify: `src/daemon/routes/tasks.py` (add `RevisitBody` model + endpoint after `cancel_task`)
- Modify: `tests/daemon/test_routes_tasks.py` (add 1 test at bottom)

- [ ] **Step 1: Write failing test for the happy path**

Append to `tests/daemon/test_routes_tasks.py`:

```python
def test_revisit_creates_new_root_from_failed_predecessor(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """Revisit a failed root: new root inherits brief/task_type, both audit
    entries are written, predecessor row stays exactly as it was."""
    from src.models import TaskRecord, TaskStatus, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(
        id="TASK-052", type=TaskType.IMPLEMENT_FEATURE, brief="Add Alipay support",
    ))
    db.update_task(
        "TASK-052",
        status=TaskStatus.FAILED,
        note="delegated child TASK-058 failed: rc=1",
        completed_at="2026-04-21T00:00:00+00:00",
    )
    pre_snapshot = db.get_task("TASK-052")

    r = TestClient(app).post(
        "/api/v1/tasks/TASK-052/revisit",
        json={"founder_note": "PR #103 already merged"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    new_id = body["new_root_task_id"]
    assert new_id.startswith("TASK-")
    assert body["predecessor_root_task_id"] == "TASK-052"
    assert body["flagged_task_id"] == "TASK-052"
    assert body["cascade"] == ["TASK-052"]
    assert body["predecessor_status"] == "failed"

    # New root row
    new_root = db.get_task(new_id)
    assert new_root is not None
    assert new_root.parent_task_id is None
    assert new_root.status == TaskStatus.PENDING
    assert new_root.brief == "Add Alipay support"
    assert new_root.type == TaskType.IMPLEMENT_FEATURE
    assert new_root.orchestration_step_count == 0
    assert new_root.cancelled_at is None

    # revisit_of on new root
    new_logs = db.get_audit_logs(new_id)
    revisit_of = next(e for e in new_logs if e["action"] == "revisit_of")
    assert revisit_of["payload"]["predecessor_root"] == "TASK-052"
    assert revisit_of["payload"]["prior_status"] == "failed"
    assert revisit_of["payload"]["founder_note"] == "PR #103 already merged"

    # revisit_spawned on predecessor
    pre_logs = db.get_audit_logs("TASK-052")
    spawned = next(e for e in pre_logs if e["action"] == "revisit_spawned")
    assert spawned["payload"]["new_root"] == new_id

    # Predecessor otherwise untouched
    post_snapshot = db.get_task("TASK-052")
    assert post_snapshot.status == pre_snapshot.status
    assert post_snapshot.note == pre_snapshot.note
    assert post_snapshot.completed_at == pre_snapshot.completed_at
    assert post_snapshot.cancelled_at == pre_snapshot.cancelled_at
    assert post_snapshot.orchestration_step_count == pre_snapshot.orchestration_step_count
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/daemon/test_routes_tasks.py::test_revisit_creates_new_root_from_failed_predecessor -v`
Expected: FAIL with 404 (no `/revisit` route yet).

- [ ] **Step 3: Add `RevisitBody` model and endpoint**

Add to `src/daemon/routes/tasks.py`, after the `CancelBody` class (around line 281):

```python
class RevisitBody(BaseModel):
    founder_note: str | None = None


# Predecessor-root states that revisit accepts. Everything else is 409.
# `failed-cancelled` is not a DB value — it's the normalized label for
# (status=failed, cancelled_at!=NULL) that the response body returns and
# the EH prompt header surfaces.
_REVISIT_ELIGIBLE_STATUSES = frozenset({
    TaskStatus.FAILED, TaskStatus.COMPLETED,
})


def _classify_predecessor_status(task: TaskRecord) -> str | None:
    """Return the normalized prior_status label, or None if ineligible.

    Maps DB shape → the 4-valued spec vocabulary:
      failed + cancelled_at != NULL  → 'failed-cancelled'
      failed + cancelled_at == NULL  → 'failed'
      blocked(escalated)             → 'blocked-escalated'
      completed                      → 'completed'
    """
    from src.models import BlockKind
    if task.status == TaskStatus.FAILED:
        return "failed-cancelled" if task.cancelled_at is not None else "failed"
    if task.status == TaskStatus.COMPLETED:
        return "completed"
    if task.status == TaskStatus.BLOCKED and task.block_kind == BlockKind.ESCALATED:
        return "blocked-escalated"
    return None


@router.post("/tasks/{task_id}/revisit")
async def revisit_task(
    task_id: str, body: RevisitBody, request: Request,
) -> dict:
    """Founder-initiated: spawn a fresh root that inherits the predecessor's
    brief and references it via audit-log entries.

    The predecessor root (the ancestor we walk up to) MUST be in a terminal-ish
    state — see `_classify_predecessor_status`. The flagged task (the id the
    founder gave us) can be in any state; only the root's status is validated.
    """
    from src.infrastructure.audit_logger import AuditLogger
    from src.infrastructure.database import LineageTooDeep

    state: DaemonState = request.app.state.daemon
    _require_active(state)

    flagged = state.db.get_task(task_id)
    if flagged is None:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")

    # Walk to the predecessor root. Defensive bound guards against corrupt cycles.
    try:
        chain = state.db.walk_ancestors(task_id, max_hops=20)
    except LineageTooDeep as exc:
        raise HTTPException(
            status_code=500,
            detail={"code": "lineage_too_deep", "reason": str(exc)},
        )
    if not chain:
        raise HTTPException(status_code=404, detail=f"task {task_id} not found")
    predecessor = chain[-1]  # root is last; chain is [flagged, ..., root]

    prior_status = _classify_predecessor_status(predecessor)
    if prior_status is None:
        from src.models import BlockKind as _BK
        raise HTTPException(
            status_code=409,
            detail={
                "code": "cannot_revisit",
                "reason": f"predecessor {predecessor.id} is {predecessor.status.value}",
                "predecessor_root_task_id": predecessor.id,
                "predecessor_status": predecessor.status.value,
                "block_kind": (
                    predecessor.block_kind.value
                    if isinstance(predecessor.block_kind, _BK) else None
                ),
            },
        )

    # cascade: [predecessor_root, ..., flagged]. chain is [flagged, ..., root],
    # so reverse it. When flagged == root, this is a single-element list.
    cascade = [t.id for t in reversed(chain)]

    async with state.db_lock:
        new_id = state.db.next_task_id()
        state.db.insert_task(TaskRecord(
            id=new_id,
            type=predecessor.type,
            brief=predecessor.brief,
            status=TaskStatus.PENDING,
            parent_task_id=None,
        ))
        audit = AuditLogger(state.db)
        audit.log_revisit_of(
            task_id=new_id,
            predecessor_root=predecessor.id,
            flagged=task_id,
            cascade=cascade,
            prior_status=prior_status,
            founder_note=body.founder_note,
        )
        audit.log_revisit_spawned(
            predecessor_task_id=predecessor.id, new_root=new_id,
        )

    enqueue_task(state, new_id)

    return {
        "new_root_task_id": new_id,
        "predecessor_root_task_id": predecessor.id,
        "flagged_task_id": task_id,
        "cascade": cascade,
        "predecessor_status": prior_status,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/daemon/test_routes_tasks.py::test_revisit_creates_new_root_from_failed_predecessor -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py tests/daemon/test_routes_tasks.py
git commit -m "feat(daemon): add POST /tasks/{id}/revisit endpoint"
```

---

## Task 4: Revisit endpoint — cascade walk, alternate prior statuses, 404

**Files:**
- Modify: `tests/daemon/test_routes_tasks.py` (append 5 tests)

- [ ] **Step 1: Write failing tests for cascade walk + 4 prior-status flavours + 404**

Append to `tests/daemon/test_routes_tasks.py`:

```python
def test_revisit_walks_cascade_to_root(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """Flag a leaf; endpoint walks parent_task_id to the predecessor root."""
    from src.models import TaskRecord, TaskStatus, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(id="TASK-052", type=TaskType.GENERAL, brief="root"))
    db.insert_task(TaskRecord(
        id="TASK-053", type=TaskType.GENERAL, brief="mid", parent_task_id="TASK-052",
    ))
    db.insert_task(TaskRecord(
        id="TASK-058", type=TaskType.GENERAL, brief="leaf", parent_task_id="TASK-053",
    ))
    db.update_task("TASK-052", status=TaskStatus.FAILED, note="cascade")
    db.update_task("TASK-053", status=TaskStatus.FAILED, note="child failed")
    db.update_task("TASK-058", status=TaskStatus.FAILED, note="rc=1")

    r = TestClient(app).post(
        "/api/v1/tasks/TASK-058/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["predecessor_root_task_id"] == "TASK-052"
    assert body["flagged_task_id"] == "TASK-058"
    assert body["cascade"] == ["TASK-052", "TASK-053", "TASK-058"]


def test_revisit_handles_cancelled_predecessor(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    from src.models import TaskRecord, TaskStatus, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(id="TASK-052", type=TaskType.GENERAL, brief="x"))
    db.update_task(
        "TASK-052",
        status=TaskStatus.FAILED,
        note="cancelled by founder: stuck",
        cancelled_at="2026-04-21T00:00:00+00:00",
    )
    r = TestClient(app).post(
        "/api/v1/tasks/TASK-052/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["predecessor_status"] == "failed-cancelled"


def test_revisit_handles_escalated_predecessor(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    from src.models import BlockKind, TaskRecord, TaskStatus, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(id="TASK-052", type=TaskType.GENERAL, brief="x"))
    db.update_task(
        "TASK-052",
        status=TaskStatus.BLOCKED,
        block_kind=BlockKind.ESCALATED,
        note="halted",
    )
    r = TestClient(app).post(
        "/api/v1/tasks/TASK-052/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["predecessor_status"] == "blocked-escalated"
    # Predecessor stays blocked(escalated) — revisit is not resolve-escalation.
    pre = db.get_task("TASK-052")
    assert pre.status == TaskStatus.BLOCKED
    assert pre.block_kind == BlockKind.ESCALATED


def test_revisit_handles_completed_predecessor(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    from src.models import TaskRecord, TaskStatus, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(id="TASK-052", type=TaskType.GENERAL, brief="x"))
    db.update_task("TASK-052", status=TaskStatus.COMPLETED, note="ok")
    r = TestClient(app).post(
        "/api/v1/tasks/TASK-052/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["predecessor_status"] == "completed"


def test_revisit_missing_task_returns_404(
    tmp_home, app, auth_headers,
) -> None:
    r = TestClient(app).post(
        "/api/v1/tasks/TASK-NOPE/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 404
```

- [ ] **Step 2: Run tests to verify they pass**

These should pass without further code changes because Task 3 already covers every branch. If any fail, fix the endpoint before proceeding.

Run: `uv run pytest tests/daemon/test_routes_tasks.py -k revisit -v`
Expected: 5 new tests PASS + the one from Task 3.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_routes_tasks.py
git commit -m "test(daemon): cover revisit cascade walk and prior-status variants"
```

---

## Task 5: Revisit endpoint — rejection of ineligible predecessor states

**Files:**
- Modify: `tests/daemon/test_routes_tasks.py` (append parameterized test)

- [ ] **Step 1: Write failing test for 409 ineligibility**

Append to `tests/daemon/test_routes_tasks.py`:

```python
import pytest


@pytest.mark.parametrize(
    "status,block_kind,note",
    [
        ("in_progress", None, "working"),
        ("pending", None, None),
        ("blocked", "delegated", "Delegated to dev_agent (child=TASK-053)"),
    ],
)
def test_revisit_rejects_ineligible_predecessor(
    tmp_home, app, daemon_state, auth_headers, status, block_kind, note,
) -> None:
    """Revisit must reject predecessors whose history isn't final yet."""
    from src.models import BlockKind, TaskRecord, TaskStatus, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(id="TASK-052", type=TaskType.GENERAL, brief="x"))
    bk = BlockKind(block_kind) if block_kind else None
    db.update_task(
        "TASK-052",
        status=TaskStatus(status),
        block_kind=bk,
        note=note,
    )
    r = TestClient(app).post(
        "/api/v1/tasks/TASK-052/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "cannot_revisit"
    assert detail["predecessor_root_task_id"] == "TASK-052"
    assert detail["predecessor_status"] == status
    # No new task row was created.
    assert len(db.list_tasks()) == 1
```

- [ ] **Step 2: Run test to verify it passes**

Run: `uv run pytest tests/daemon/test_routes_tasks.py::test_revisit_rejects_ineligible_predecessor -v`
Expected: 3 parameterizations PASS (already handled by `_classify_predecessor_status` returning None).

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_routes_tasks.py
git commit -m "test(daemon): reject revisit on ineligible predecessor states"
```

---

## Task 6: Revisit endpoint — lineage-too-deep, concurrent revisits, chain of chains

**Files:**
- Modify: `tests/daemon/test_routes_tasks.py` (append 3 tests)

- [ ] **Step 1: Write failing tests for lineage safety bound and revisit-chains**

Append to `tests/daemon/test_routes_tasks.py`:

```python
def test_revisit_lineage_too_deep_returns_500(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """A 21-hop ancestor chain is pathological; the endpoint guards with 500."""
    from src.models import TaskRecord, TaskStatus, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(id="TASK-000", type=TaskType.GENERAL, brief="root"))
    db.update_task("TASK-000", status=TaskStatus.FAILED)
    prev = "TASK-000"
    for i in range(1, 25):
        tid = f"TASK-{i:03d}"
        db.insert_task(TaskRecord(
            id=tid, type=TaskType.GENERAL, brief=f"t{i}", parent_task_id=prev,
        ))
        db.update_task(tid, status=TaskStatus.FAILED)
        prev = tid
    r = TestClient(app).post(
        f"/api/v1/tasks/{prev}/revisit", json={}, headers=auth_headers,
    )
    assert r.status_code == 500
    assert r.json()["detail"]["code"] == "lineage_too_deep"


def test_revisit_concurrent_on_same_predecessor_both_succeed(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """Two sequential POSTs against the same failed predecessor both succeed;
    predecessor ends with two revisit_spawned audit entries."""
    from src.models import TaskRecord, TaskStatus, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(id="TASK-052", type=TaskType.GENERAL, brief="x"))
    db.update_task("TASK-052", status=TaskStatus.FAILED)

    client = TestClient(app)
    r1 = client.post("/api/v1/tasks/TASK-052/revisit", json={}, headers=auth_headers)
    r2 = client.post("/api/v1/tasks/TASK-052/revisit", json={}, headers=auth_headers)
    assert r1.status_code == 200 and r2.status_code == 200
    id1 = r1.json()["new_root_task_id"]
    id2 = r2.json()["new_root_task_id"]
    assert id1 != id2

    spawned = [
        e for e in db.get_audit_logs("TASK-052") if e["action"] == "revisit_spawned"
    ]
    assert sorted(e["payload"]["new_root"] for e in spawned) == sorted([id1, id2])


def test_revisit_a_revisit_chain_of_chains(
    tmp_home, app, daemon_state, auth_headers,
) -> None:
    """TASK-P → TASK-N (via revisit) → TASK-N' (revisit of TASK-N)."""
    from src.models import TaskRecord, TaskStatus, TaskType
    db = daemon_state.db
    db.insert_task(TaskRecord(id="TASK-052", type=TaskType.GENERAL, brief="x"))
    db.update_task("TASK-052", status=TaskStatus.FAILED)
    client = TestClient(app)
    r1 = client.post("/api/v1/tasks/TASK-052/revisit", json={}, headers=auth_headers)
    id_n = r1.json()["new_root_task_id"]
    # Mark the new root as failed so it's revisit-eligible.
    db.update_task(id_n, status=TaskStatus.FAILED, note="also failed")
    r2 = client.post(f"/api/v1/tasks/{id_n}/revisit", json={}, headers=auth_headers)
    id_n2 = r2.json()["new_root_task_id"]

    assert id_n != id_n2
    # Second revisit's revisit_of points at id_n, not the original TASK-052.
    logs_n2 = db.get_audit_logs(id_n2)
    ro = next(e for e in logs_n2 if e["action"] == "revisit_of")
    assert ro["payload"]["predecessor_root"] == id_n
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/daemon/test_routes_tasks.py -k revisit -v`
Expected: all revisit tests PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/daemon/test_routes_tasks.py
git commit -m "test(daemon): lineage guard, concurrent revisits, revisit-of-revisit"
```

---

## Task 7: First-step EH prompt header injection

**Files:**
- Modify: `src/orchestrator/run_step.py` (new `_revisit_header_if_applicable` helper + call site in `_build_agent_prompt`)
- Modify: `tests/test_run_step.py` (append 3 tests)

- [ ] **Step 1: Write failing tests for header injection**

Append to `tests/test_run_step.py`:

```python
def test_run_step_revisit_header_injected_on_first_step(
    runtime, db, monkeypatch,
):
    """New-root task with a revisit_of audit entry and no orchestration_step
    entry: EH prompt must start with the revisit context header."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-072", type=TaskType.IMPLEMENT_FEATURE, brief="Add Alipay support",
        assigned_agent="engineering_head",
    ))
    db.insert_audit_log(
        task_id="TASK-072", agent="founder", action="revisit_of",
        payload={
            "predecessor_root": "TASK-052",
            "flagged": "TASK-058",
            "cascade": ["TASK-052", "TASK-053", "TASK-058"],
            "prior_status": "failed",
            "founder_note": "PR #103 already merged",
        },
    )
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort after prompt build")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-072")

    prompt = captured["prompt"]
    assert prompt.startswith("REVISIT CONTEXT:")
    assert "TASK-052" in prompt
    assert "failed" in prompt
    assert "TASK-058" in prompt
    assert "TASK-052 -> TASK-053 -> TASK-058" in prompt or \
           "TASK-052 → TASK-053 → TASK-058" in prompt
    assert "PR #103 already merged" in prompt


def test_run_step_revisit_header_absent_on_second_step(
    runtime, db, monkeypatch,
):
    """After the first orchestration_step audit entry lands, the header must
    disappear — subsequent EH cycles see a vanilla capabilities prompt."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-072", type=TaskType.GENERAL, brief="x",
        assigned_agent="engineering_head",
    ))
    db.update_task("TASK-072", orchestration_step_count=1)
    db.insert_audit_log(
        task_id="TASK-072", agent="founder", action="revisit_of",
        payload={
            "predecessor_root": "TASK-052", "flagged": "TASK-052",
            "cascade": ["TASK-052"], "prior_status": "failed",
            "founder_note": None,
        },
    )
    db.insert_audit_log(
        task_id="TASK-072", agent="orchestrator", action="orchestration_step",
        payload={"step_number": 1, "decision": {"action": "done"}},
    )
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-072")

    assert not captured["prompt"].startswith("REVISIT CONTEXT:")


def test_run_step_revisit_header_omits_note_line_when_none(
    runtime, db, monkeypatch,
):
    """founder_note == None => no 'Founder note:' line in the header."""
    from src.orchestrator.orchestrator import Orchestrator
    db.insert_task(TaskRecord(
        id="TASK-072", type=TaskType.GENERAL, brief="x",
        assigned_agent="engineering_head",
    ))
    db.insert_audit_log(
        task_id="TASK-072", agent="founder", action="revisit_of",
        payload={
            "predecessor_root": "TASK-052", "flagged": "TASK-052",
            "cascade": ["TASK-052"], "prior_status": "failed",
            "founder_note": None,
        },
    )
    orch = Orchestrator(db=db, settings=Settings(), runtime=runtime)

    captured = {}
    def capture(task_id, agent, prompt, on_session_started=None):
        captured["prompt"] = prompt
        raise RuntimeError("abort")
    monkeypatch.setattr(orch, "_run_agent", capture)
    orch.run_step("TASK-072")

    assert "Founder note:" not in captured["prompt"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_run_step.py -k revisit -v`
Expected: FAIL — prompts don't contain `REVISIT CONTEXT:`.

- [ ] **Step 3: Implement the helper and wire it into `_build_agent_prompt`**

In `src/orchestrator/run_step.py`, add a helper near `_build_prior_steps_from_db` (around line 232):

```python
def _revisit_header_if_applicable(orch: "Orchestrator", task_id: str) -> str | None:
    """Return a 5-6 line revisit context header, or None.

    Trigger: the task has a `revisit_of` audit entry AND no `orchestration_step`
    audit entry. The latter is how we detect "first step" without timestamps —
    once the EH has produced a decision, `log_orchestration_step` writes a row
    and this helper returns None on every subsequent call.
    """
    logs = orch._db.get_audit_logs(task_id)
    revisit_entry = next(
        (e for e in logs if e["action"] == "revisit_of"), None,
    )
    if revisit_entry is None:
        return None
    if any(e["action"] == "orchestration_step" for e in logs):
        return None

    payload = revisit_entry["payload"]
    predecessor = payload["predecessor_root"]
    flagged = payload["flagged"]
    prior_status = payload["prior_status"]
    cascade = payload.get("cascade") or [predecessor]
    note = payload.get("founder_note")

    lines = [
        f"REVISIT CONTEXT: this root is a revisit of {predecessor} "
        f"(which ended in {prior_status}).",
        f"Founder flagged {flagged} in the predecessor lineage — "
        "start your investigation there.",
        "Cascade chain (predecessor root -> flagged): "
        + " -> ".join(cascade),
    ]
    if note:
        lines.append(f"Founder note: {note}")
    lines.append(
        f"Inspect via: `opc details {predecessor}`, "
        f"`opc audit {predecessor}`, `opc recall {predecessor}`."
    )
    lines.append(
        "You may reuse successful sub-tasks' artifacts (referenced by path in "
        "new child briefs); old child task rows stay frozen."
    )
    return "\n".join(lines) + "\n\n"
```

Then modify `_build_agent_prompt` at line 191: the function currently returns the capabilities prompt for `engineering_head` and the raw brief for workers. Prepend the header to the EH return value:

```python
def _build_agent_prompt(orch: "Orchestrator", task, agent: str) -> str:
    """Build the capabilities prompt for an EH decision step, or pass the
    brief verbatim for a worker. Prior steps are rebuilt from the DB so this
    works identically on first pickup and on post-delegation resumption.

    For revisited roots, a one-shot context header is prepended to the EH
    prompt on the very first orchestration step (detected via audit log).
    """
    from src.orchestrator.capabilities import build_capabilities_prompt
    if agent != "engineering_head":
        return task.brief
    agent_names, tiers = _list_candidate_agents(orch)
    agents_for_prompt = []
    for name in agent_names:
        enrollment = orch._db.get_enrollment(name)
        desc = enrollment["description"] if enrollment else name
        tier = tiers.get(name)
        agents_for_prompt.append({
            "name": name,
            "description": desc,
            "tier": tier.value if tier else "green",
        })
    prior_steps = _build_prior_steps_from_db(orch, task.id)
    base = build_capabilities_prompt(
        brief=task.brief,
        agents=agents_for_prompt,
        step_number=task.orchestration_step_count + 1,
        max_steps=orch._settings.max_orchestration_steps,
        prior_steps=prior_steps,
    )
    header = _revisit_header_if_applicable(orch, task.id)
    if header is not None:
        return header + base
    return base
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_run_step.py -k revisit -v`
Expected: 3 new tests PASS.

- [ ] **Step 5: Confirm no run_step regressions**

Run: `uv run pytest tests/test_run_step.py -v`
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add src/orchestrator/run_step.py tests/test_run_step.py
git commit -m "feat(orchestrator): inject revisit context header on first EH step"
```

---

## Task 8: CLI `cmd_revisit` — TTY gate and confirmation prompt

**Files:**
- Modify: `src/cli.py` (add `cmd_revisit` + subparser)
- Modify: `tests/test_cli.py` (append 2 tests)

- [ ] **Step 1: Write failing tests for TTY gate + confirmation**

Append to `tests/test_cli.py`:

```python
def test_cmd_revisit_rejects_non_tty(capsys):
    """No TTY => abort before any HTTP call."""
    from src.cli import cmd_revisit

    fake = MagicMock()
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli.sys.stdin") as mock_stdin, \
         patch("src.cli.sys.stdout") as mock_stdout:
        mock_stdin.isatty.return_value = False
        mock_stdout.isatty.return_value = True
        args = MagicMock(task_id="TASK-052", note=None)
        with pytest.raises(SystemExit):
            cmd_revisit(args)
    # Never touched the client.
    fake.post.assert_not_called()
    assert "interactive terminal" in capsys.readouterr().out


def test_cmd_revisit_aborts_on_negative_confirmation(capsys, monkeypatch):
    """TTY present but founder types 'n' => no POST."""
    from src.cli import cmd_revisit

    fake = MagicMock()
    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli.sys.stdin") as mock_stdin, \
         patch("src.cli.sys.stdout") as mock_stdout, \
         patch("builtins.input", return_value="n"):
        mock_stdin.isatty.return_value = True
        mock_stdout.isatty.return_value = True
        args = MagicMock(task_id="TASK-052", note=None)
        with pytest.raises(SystemExit):
            cmd_revisit(args)
    fake.post.assert_not_called()


def test_cmd_revisit_submits_and_streams_on_yes(capsys):
    """'y' confirmation => POST + stream."""
    from src.cli import cmd_revisit

    fake = MagicMock()
    fake.post.return_value.status_code = 200
    fake.post.return_value.json.return_value = {
        "new_root_task_id": "TASK-072",
        "predecessor_root_task_id": "TASK-052",
        "flagged_task_id": "TASK-052",
        "cascade": ["TASK-052"],
        "predecessor_status": "failed",
    }
    fake.stream.return_value = iter(['{"type": "task_complete"}'])

    with patch("src.cli.OpcClient.from_env", return_value=fake), \
         patch("src.cli.sys.stdin") as mock_stdin, \
         patch("src.cli.sys.stdout") as mock_stdout, \
         patch("builtins.input", return_value="y"):
        mock_stdin.isatty.return_value = True
        mock_stdout.isatty.return_value = True
        args = MagicMock(task_id="TASK-052", note="PR merged")
        cmd_revisit(args)

    fake.post.assert_called_once_with(
        "/api/v1/tasks/TASK-052/revisit",
        json={"founder_note": "PR merged"},
    )
    out = capsys.readouterr().out
    assert "TASK-072" in out
    assert "task_complete" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k revisit -v`
Expected: FAIL — `cmd_revisit` doesn't exist.

- [ ] **Step 3: Add `cmd_revisit` and its subparser**

In `src/cli.py`, add `cmd_revisit` after `cmd_cancel` (around line 781):

```python
def cmd_revisit(args: argparse.Namespace) -> None:
    """Founder action: spawn a NEW root task that inherits a terminal
    predecessor's brief, with the EH gated on an audit-log-backed context
    header. TTY-gated — no --yes bypass."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print("opc revisit requires an interactive terminal (no --yes bypass).")
        sys.exit(1)

    print(f"About to revisit {args.task_id} (founder-initiated).")
    print("This creates a NEW root task that inherits the original brief.")
    print(
        f"The existing lineage rooted at {args.task_id} stays frozen "
        "(read-only history)."
    )
    print(
        "The EH for the new root can inspect the old lineage via "
        "`opc details` / `opc audit` / `opc recall`."
    )
    reply = input("Continue? [y/N] ").strip().lower()
    if reply not in ("y", "yes"):
        print("Aborted.")
        sys.exit(1)

    try:
        client = OpcClient.from_env()
    except (DaemonNotRunning, DaemonStateInconsistent) as exc:
        print(f"Error: {exc}")
        sys.exit(1)

    r = client.post(
        f"/api/v1/tasks/{args.task_id}/revisit",
        json={"founder_note": args.note},
    )
    if r.status_code == 404:
        print(f"Task {args.task_id} not found.")
        sys.exit(1)
    if r.status_code == 409:
        detail = {}
        try:
            detail = r.json().get("detail", {})
        except ValueError:
            pass
        if detail.get("code") == "cannot_revisit":
            print(
                f"Cannot revisit {args.task_id}: "
                f"predecessor {detail.get('predecessor_root_task_id')} "
                f"is {detail.get('predecessor_status')}."
            )
            sys.exit(1)
    if not _ok(r):
        return
    body = r.json()
    new_id = body["new_root_task_id"]
    print(
        f"Created {new_id} (predecessor: {body['predecessor_root_task_id']}, "
        f"flagged: {body['flagged_task_id']})."
    )
    print(f"Submitted {new_id}; streaming events (Ctrl-C to detach)...")
    _stream_task_events(client, new_id)
```

In `build_parser()` (around line 1008, just before `return parser`), add:

```python
    # opc revisit — founder-initiated; TTY-gated; no --yes flag by design.
    p_revisit = sub.add_parser(
        "revisit",
        help=(
            "Spawn a NEW root that inherits a terminal predecessor's brief "
            "(founder; TTY-gated)"
        ),
    )
    p_revisit.add_argument("task_id", help="Any task id in the lineage to revisit")
    p_revisit.add_argument(
        "--note", default=None,
        help="Optional founder hint surfaced to the EH in the first-step prompt header",
    )
    p_revisit.set_defaults(func=cmd_revisit)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k revisit -v`
Expected: 3 PASS.

- [ ] **Step 5: Confirm no CLI regressions**

Run: `uv run pytest tests/test_cli.py -v`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/cli.py tests/test_cli.py
git commit -m "feat(cli): add opc revisit with TTY gate and confirmation prompt"
```

---

## Task 9: Integration test — revisit roundtrip

**Files:**
- Modify: `tests/integration/test_end_to_end.py` (append 1 test)

- [ ] **Step 1: Read existing integration patterns**

Skim lines 108-226 of `tests/integration/test_end_to_end.py` to confirm:
- `_register_runtime` / `_init_agent` / `_submit_task` / `_wait_for_terminal_status` helpers exist
- Tests drive the fake Claude binary via plan files written to `plans/`
- The pattern for asserting terminal status is `_wait_for_terminal_status(base, task_id, headers, ...)`

- [ ] **Step 2: Write failing integration test**

Append to `tests/integration/test_end_to_end.py`:

```python
@pytest.mark.integration
def test_revisit_roundtrip_creates_new_root_and_completes(
    daemon_running, tmp_runtime, tmp_plans,
):
    """End-to-end: fail a task → POST /revisit directly → fake EH on the new
    root returns {"action": "done"} → new root reaches `completed`, predecessor
    stays `failed`. CLI is bypassed because integration tests run non-TTY."""
    base, headers = daemon_running
    _register_runtime(base, tmp_runtime)
    _init_agent(base, "engineering_head", headers)

    # Step 1: plan script for the INITIAL run — EH escalates so the root
    # lands in a terminal-ish state the founder can revisit.
    _write_plan(tmp_plans / "eh-fail.json", """
        {"action": "escalate", "reason": "need founder call"}
    """)
    _write_agent_config(tmp_runtime, "engineering_head", "claude")

    task_id = _submit_task(base, brief="Revisit me", headers=headers)
    _wait_for_terminal_status(
        base, task_id, headers,
        accept={"blocked"},  # blocked(escalated) is terminal-ish for revisit
    )

    # Step 2: revisit via HTTP (no CLI — non-TTY in the test harness).
    r = httpx.post(
        f"{base}/api/v1/tasks/{task_id}/revisit",
        json={"founder_note": "try again"},
        headers=headers,
    )
    assert r.status_code == 200
    new_id = r.json()["new_root_task_id"]
    assert r.json()["predecessor_status"] == "blocked-escalated"

    # Step 3: new-root plan — EH calls it `done` immediately.
    _write_plan(tmp_plans / "eh-done.json", """
        {"action": "done", "summary": "revisit succeeded"}
    """)
    _wait_for_terminal_status(base, new_id, headers, accept={"completed"})

    # Step 4: predecessor is frozen.
    r_pre = httpx.get(f"{base}/api/v1/tasks/{task_id}", headers=headers)
    assert r_pre.json()["task"]["status"] == "blocked"
```

> Note: this test assumes the project's fake-Claude harness reads plan JSON files from `tmp_plans` in the order they're written and that `_wait_for_terminal_status` already accepts a custom `accept` set. If either signature differs, adjust to match — the key invariants to assert are: revisit returns 200, new root reaches `completed`, predecessor status is unchanged.

- [ ] **Step 3: Run the integration test**

Run: `uv run pytest tests/integration/test_end_to_end.py::test_revisit_roundtrip_creates_new_root_and_completes -v -m integration`
Expected: PASS. If it fails, the most common cause is plan-file ordering in the fake Claude harness — align with whichever pattern `test_delegate_and_resume_roundtrip` uses.

- [ ] **Step 4: Commit**

```bash
git add tests/integration/test_end_to_end.py
git commit -m "test(integration): revisit roundtrip end-to-end through real daemon"
```

---

## Task 10: Documentation updates

**Files:**
- Modify: `CLAUDE.md`
- Modify: `skills/opc/SKILL.md`
- Modify: `README.md`

- [ ] **Step 1: Update CLAUDE.md CLI list**

In `CLAUDE.md`, find the `opc cancel` line in the "Running the Daemon + CLI" block (around the end of the CLI snippet). Add below it:

```
opc revisit TASK-052 [--note "..."]                             # founder: spawn NEW root that inherits the predecessor's brief (TTY-gated)
```

Also in the `routes/tasks.py` bullet of the directory-layout block, append `, POST /tasks/{id}/revisit` so the list reads `..., POST /tasks/{id}/resolve-escalation, POST /tasks/{id}/revisit, callbacks`.

- [ ] **Step 2: Update skills/opc/SKILL.md**

In `skills/opc/SKILL.md`, in the `## Tasks` section, append:

```bash
# Revisit — founder-initiated: spawn a NEW root task that inherits the brief of a terminal predecessor.
# TTY-gated; no --yes bypass; prompts for confirmation before POSTing.
scripts/opc revisit TASK-052 [--note "founder hint to the new-root EH"]
```

In the `## Safety Rules` section, under **Confirm with user first**, add a new bullet:

```
- `revisit` — founder-initiated spawn of a new root task from a terminal predecessor (TTY-gated CLI; agent sessions cannot invoke it)
```

- [ ] **Step 3: Update README.md**

In `README.md`'s command reference section, add one line near `opc cancel`:

```
opc revisit TASK-052 [--note "..."]   # founder: spawn a new root that inherits a terminal predecessor's brief
```

- [ ] **Step 4: Commit docs**

```bash
git add CLAUDE.md skills/opc/SKILL.md README.md
git commit -m "docs: add opc revisit to CLAUDE.md, opc skill, and README"
```

---

## Task 11: Final verification

- [ ] **Step 1: Run the full unit test suite**

Run: `uv run pytest tests/ -v`
Expected: all tests pass (including all new revisit tests).

- [ ] **Step 2: Run integration tests**

Run: `uv run pytest tests/ -v -m integration`
Expected: all pass (including the new revisit roundtrip).

- [ ] **Step 3: Smoke test the CLI manually**

In a real terminal:

```bash
scripts/daemon.sh start
uv run opc init /tmp/opc-revisit-smoke
uv run opc run --brief "smoke test for revisit"
# Wait for it to fail / escalate, then:
uv run opc tasks
uv run opc revisit TASK-001 --note "retry smoke"
# Confirm [y/N] prompt appears; type 'y'; watch events.
```

Expected: `About to revisit TASK-001...` banner, `y` accepts, new `TASK-00N` created, SSE stream starts.

- [ ] **Step 4: Stop daemon**

```bash
scripts/daemon.sh stop
```

No commit on this task.

---

## Self-Review

### 1. Spec coverage

| Spec section | Covered by |
| ------------ | ---------- |
| §3.1 CLI (TTY gate, confirm prompt, no `--yes`) | Task 8 |
| §3.1 After-confirmation POST + SSE streaming | Task 8 |
| §3.2 HTTP `POST /tasks/{id}/revisit` body + response | Task 3 |
| §3.2 404, 409 `cannot_revisit`, 500 `lineage_too_deep` | Tasks 3, 5, 6 |
| §4 Architecture: walk → validate → atomic insert + 2 audit | Task 3 |
| §4.1 State semantics (predecessor frozen; new root fresh) | Tasks 3 (assertions), 5 |
| §4.3 First-step prompt header | Task 7 |
| §5 Validation rules (4 eligible states, 3 rejected) | Tasks 3, 4, 5 |
| §6 Atomic mutation order inside `db_lock` | Task 3 (helper uses `async with state.db_lock`) |
| §7 Edge cases 1-8 | Tasks 3, 4, 5, 6, 7 |
| §7 Edge case 8 (agent non-TTY reject) | Task 8 |
| §7 Edge case 9 (`revisit_of` only from endpoint) | Task 3 (structural — endpoint is the only writer) |
| §8 Unit tests 1-17 | Tasks 1-8 |
| §8 Integration test 18 | Task 9 |
| §9 LOC budget ~560 | Tasks 1-10 |
| §10 Rollout (merge order matches DB → endpoint → header → CLI → integration → docs) | Task order |

Spec §8 tests 2, 4, 12, 13, 14, 15, 16 all map cleanly to the tasks above. Tests 3 (predecessor-not-mutated snapshot) and 4 (brief+task_type inheritance) are folded into Task 3's happy-path assertions; test 10 (flagged-is-root) is Task 3's single-element cascade. Test 11 (lineage_too_deep) is Task 6. Test 5/6/7 are Task 4. Test 8 (parameterized rejections) is Task 5. Test 9 (404) is Task 4. Test 17 (CLI non-TTY) is Task 8.

### 2. Placeholder scan

Searched for TBD/TODO/`similar to`/vague clauses: none found. Every step contains the exact code to paste or the exact command to run with expected output.

### 3. Type consistency

- `walk_ancestors(task_id, max_hops=20)` — same signature used by Task 1 tests and Task 3's endpoint call.
- `log_revisit_of` / `log_revisit_spawned` — same parameter names in Task 2 (tests + impl) and Task 3 (endpoint).
- `RevisitBody.founder_note: str | None = None` — tests POST `{"founder_note": ...}` (Tasks 3, 8); endpoint reads `body.founder_note` (Task 3); CLI sends `{"founder_note": args.note}` (Task 8).
- `_revisit_header_if_applicable(orch, task_id) -> str | None` — defined in Task 7 step 3, called from `_build_agent_prompt` in the same task.
- `prior_status` values: `failed`, `failed-cancelled`, `blocked-escalated`, `completed` — consistent across spec §4.3 table, Task 3's `_classify_predecessor_status`, Task 4 tests, and Task 7 header tests.
- Response body keys `new_root_task_id` / `predecessor_root_task_id` / `flagged_task_id` / `cascade` / `predecessor_status` — consistent across spec §3.2, Task 3 endpoint, Tasks 3-6 tests, Task 8 CLI.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-21-opc-revisit.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
