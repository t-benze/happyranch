# Thread Task-Followup Re-Invocation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a task dispatched from a thread reaches its true terminal state, inject a system message into the thread and re-invoke the dispatching agent to compose the follow-up reply it promised.

**Architecture:** A new helper `_maybe_post_thread_followup` in `src/orchestrator/run_step.py` is invoked at every terminal-transition site, paired with the existing `_notify_failure_if_eligible` calls and reusing each site's known `auto_revisit_spawned` value. The helper walks `walk_revisit_chain` backward to find the originating thread on the dispatched root, then under `db_lock` appends a `task_completed`/`task_failed` SYSTEM message, auto-extends the thread's `turn_cap` if needed, mints a new invocation with the new purpose `TASK_FOLLOWUP`, and enqueues it onto `org.thread_queue`.

**Tech Stack:** Python 3.13, pydantic v2, SQLite + WAL, FastAPI, asyncio queues. Tests use pytest + the existing `fake_claude.sh` thread plan fixtures.

**Spec:** `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md`

---

## File Map

**Modify:**
- `src/models.py` — extend `ThreadInvocationPurpose`.
- `src/infrastructure/database.py` — `count_pending_turn_obligations`, `bump_thread_turn_cap`, `get_dispatcher_for_task` (audit-row lookup).
- `src/infrastructure/audit_logger.py` — new event helpers.
- `src/daemon/routes/threads.py` — promote helper, extend `require_purposes` for reply/decline.
- `src/daemon/thread_runner.py` — new `_purpose_note` branch.
- `src/infrastructure/thread_store.py` — render `task_completed` / `task_failed` system messages in transcript.
- `src/daemon/thread_forward.py` — same two tags in forwarded-thread rendering.
- `src/orchestrator/run_step.py` — `_maybe_post_thread_followup` helper + paired calls at every terminal site.
- `src/daemon/routes/tasks.py` — `/cancel` hook for PENDING-task cancellation (the running case is already covered transitively through run_step).
- `web/src/features/threads/` — renderer cases for the two new tags.
- `CLAUDE.md` — invariants subsection.

**Create:**
- `tests/test_thread_task_followup.py` — unit tests for the helper + fire predicate truth table.
- `tests/integration/test_thread_task_followup_e2e.py` — three integration tests using `fake_claude_plan_env` + `fake_claude_thread_plan_env`.

---

## Task 1: Add `TASK_FOLLOWUP` to `ThreadInvocationPurpose`

**Files:**
- Modify: `src/models.py:161-164`
- Test: `tests/test_thread_task_followup.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_thread_task_followup.py
from src.models import ThreadInvocationPurpose


def test_task_followup_purpose_value():
    assert ThreadInvocationPurpose.TASK_FOLLOWUP.value == "task_followup"
    assert "task_followup" in {p.value for p in ThreadInvocationPurpose}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_task_followup.py::test_task_followup_purpose_value -v`
Expected: FAIL with `AttributeError: TASK_FOLLOWUP`.

- [ ] **Step 3: Add the enum value**

Edit `src/models.py:161-164`:

```python
class ThreadInvocationPurpose(StrEnum):
    REPLY = "reply"
    BOOTSTRAP = "bootstrap"
    CLOSE_OUT = "close_out"
    TASK_FOLLOWUP = "task_followup"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_thread_task_followup.py::test_task_followup_purpose_value -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_thread_task_followup.py
git commit -m "feat(threads): add TASK_FOLLOWUP invocation purpose"
```

---

## Task 2: Promote `_pending_reply_load` to `Database.count_pending_turn_obligations`

The helper is module-private to `routes/threads.py`. Promote it to `Database` so `run_step.py` can call it, and include `TASK_FOLLOWUP` in the counted set.

**Files:**
- Modify: `src/infrastructure/database.py` (new method)
- Modify: `src/daemon/routes/threads.py` (3 call sites)
- Test: `tests/test_thread_task_followup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_thread_task_followup.py
import pytest
from pathlib import Path
from src.infrastructure.database import Database
from src.models import (
    ThreadInvocationPurpose, ThreadInvocationStatus,
    ThreadRecord, ThreadInvocationRecord,
)


def _fresh_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    db.initialize()
    return db


def test_count_pending_turn_obligations_counts_reply_bootstrap_followup(tmp_path):
    db = _fresh_db(tmp_path)
    db.insert_thread(ThreadRecord(id="THR-001", subject="t"))
    db.add_thread_participant("THR-001", agent_name="alice", added_by="founder")
    seq = db.append_thread_message(
        thread_id="THR-001", speaker="founder", kind="message",
        body_markdown="hi", addressed_to=["@all"],
    )
    # Mint one of each interesting purpose.
    for purpose in (
        ThreadInvocationPurpose.REPLY,
        ThreadInvocationPurpose.BOOTSTRAP,
        ThreadInvocationPurpose.TASK_FOLLOWUP,
        ThreadInvocationPurpose.CLOSE_OUT,  # must NOT be counted
    ):
        db.mint_thread_invocation(
            thread_id="THR-001", agent_name="alice",
            triggering_seq=seq, purpose=purpose,
        )

    assert db.count_pending_turn_obligations("THR-001") == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_task_followup.py::test_count_pending_turn_obligations_counts_reply_bootstrap_followup -v`
Expected: FAIL with `AttributeError: 'Database' object has no attribute 'count_pending_turn_obligations'`.

- [ ] **Step 3: Add the Database method**

In `src/infrastructure/database.py`, near `list_thread_invocations`:

```python
def count_pending_turn_obligations(self, thread_id: str) -> int:
    """Pending invocations that will increment turns_used when consumed.

    REPLY, BOOTSTRAP, TASK_FOLLOWUP count. CLOSE_OUT is excluded
    (per threads spec §5.10.1). The single-source helper for both the
    send/compose projection in routes/threads.py and the auto-extend
    projection in _maybe_post_thread_followup.
    """
    from src.models import ThreadInvocationPurpose, ThreadInvocationStatus
    counted = {
        ThreadInvocationPurpose.REPLY.value,
        ThreadInvocationPurpose.BOOTSTRAP.value,
        ThreadInvocationPurpose.TASK_FOLLOWUP.value,
    }
    with self._conn() as c:
        row = c.execute(
            "SELECT COUNT(*) AS n FROM thread_invocations "
            "WHERE thread_id = ? AND status = ? AND purpose IN ({})".format(
                ",".join("?" * len(counted))
            ),
            (thread_id, ThreadInvocationStatus.PENDING.value, *counted),
        ).fetchone()
    return int(row["n"])
```

- [ ] **Step 4: Switch the three call sites in `routes/threads.py`**

Replace `_pending_reply_load` definition (lines 671-683) and its three callers (search `_pending_reply_load(`) with `org.db.count_pending_turn_obligations(thread_id)`. Delete the now-unused module-private function.

- [ ] **Step 5: Run test + full suite**

Run: `uv run pytest tests/test_thread_task_followup.py::test_count_pending_turn_obligations_counts_reply_bootstrap_followup tests/test_threads.py -v`
Expected: PASS (new test + all existing thread-route tests).

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/database.py src/daemon/routes/threads.py tests/test_thread_task_followup.py
git commit -m "refactor(threads): promote pending-load helper to Database; include TASK_FOLLOWUP"
```

---

## Task 3: Extend `require_purposes` on reply/decline to admit `TASK_FOLLOWUP`

Reply (`threads.py:713`) and decline (`threads.py:770`) currently require `purpose ∈ {REPLY, BOOTSTRAP}`. Add `TASK_FOLLOWUP`. Leave dispatch (`threads.py:833`) unchanged — it stays restricted to `{REPLY, BOOTSTRAP}` per spec §6.4.

**Files:**
- Modify: `src/daemon/routes/threads.py:716`, `:773`
- Test: `tests/test_thread_task_followup.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_thread_task_followup.py
import httpx
from tests.test_threads import _bootstrap_org_with_thread  # existing helper


def test_reply_admits_task_followup_purpose(live_daemon, runtime):
    """A TASK_FOLLOWUP invocation token can be used to land a reply."""
    base = f"http://127.0.0.1:{live_daemon}/api/v1/orgs/test"
    thread_id, seq, _ = _bootstrap_org_with_thread(base, runtime)

    # Manually mint a TASK_FOLLOWUP invocation.
    from src.infrastructure.database import Database
    from src.models import ThreadInvocationPurpose
    db = Database(runtime / "orgs" / "test" / "grassland.db")
    inv = db.mint_thread_invocation(
        thread_id=thread_id, agent_name="dev_agent",
        triggering_seq=seq, purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )

    r = httpx.post(
        f"{base}/threads/{thread_id}/reply",
        json={
            "thread_id": thread_id,
            "invocation_token": inv.invocation_token,
            "speaker": "dev_agent",
            "body_markdown": "followup body",
            "in_response_to_seq": seq,
        },
        headers={"Authorization": f"Bearer {token}"},  # via _auth_headers helper
    )
    assert r.status_code == 200, r.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_task_followup.py::test_reply_admits_task_followup_purpose -v`
Expected: FAIL with `400 invocation_purpose_unexpected`.

- [ ] **Step 3: Extend require_purposes**

In `src/daemon/routes/threads.py` at both `_validate_invocation_token(...)` calls inside `reply_thread_endpoint` and `decline_thread_endpoint`:

```python
require_purposes=[
    ThreadInvocationPurpose.REPLY,
    ThreadInvocationPurpose.BOOTSTRAP,
    ThreadInvocationPurpose.TASK_FOLLOWUP,
],
```

Leave the call inside `dispatch_from_thread_endpoint` (~line 833) **unchanged** — that one stays `[REPLY, BOOTSTRAP]`.

- [ ] **Step 4: Run test + verify dispatch still rejects TASK_FOLLOWUP**

Add a complementary test:

```python
def test_dispatch_rejects_task_followup_purpose(live_daemon, runtime):
    """Spec §6.4: a TASK_FOLLOWUP turn may not dispatch new tasks."""
    base = f"http://127.0.0.1:{live_daemon}/api/v1/orgs/test"
    thread_id, seq, _ = _bootstrap_org_with_thread(base, runtime)
    # Mint TASK_FOLLOWUP invocation, attempt dispatch.
    # ... (mirror Step 1 setup, then POST /dispatch)
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "invocation_purpose_unexpected"
```

Run: `uv run pytest tests/test_thread_task_followup.py -v -k purpose`
Expected: both PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/threads.py tests/test_thread_task_followup.py
git commit -m "feat(threads): admit TASK_FOLLOWUP for reply/decline; dispatch stays restricted"
```

---

## Task 4: `_purpose_note` branch for `task_followup` in the prompt builder

**Files:**
- Modify: `src/daemon/thread_runner.py:54-71` (the `_purpose_note` function)
- Modify: `src/daemon/thread_runner.py:73-106` (the `build_thread_prompt` signature gains a `triggering_message` parameter)
- Test: `tests/test_thread_task_followup.py`

- [ ] **Step 1: Write the failing test**

```python
def test_purpose_note_task_followup_renders_task_id_and_status():
    from src.daemon.thread_runner import _purpose_note
    from src.models import ThreadMessage

    triggering = ThreadMessage(
        thread_id="THR-1", seq=4, speaker="family_manager", kind="system",
        system_payload={
            "kind_tag": "task_completed",
            "task_id": "TASK-007", "original_task_id": "TASK-007",
            "status": "completed", "final_output_summary": "report uploaded",
        },
        created_at="2026-05-28T01:43:23+00:00",
    )
    note = _purpose_note(
        purpose="task_followup", triggering_seq=4,
        addressed_to=None, invoked_agent="family_manager",
        triggering_message=triggering,
    )
    assert "TASK-007" in note
    assert "completed" in note
    assert "grassland details" in note
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_task_followup.py::test_purpose_note_task_followup_renders_task_id_and_status -v`
Expected: FAIL with TypeError (no `triggering_message` kwarg) or default `reply` branch returned.

- [ ] **Step 3: Update `_purpose_note` + threading the message through**

In `src/daemon/thread_runner.py`:

```python
def _purpose_note(
    purpose: str,
    triggering_seq: int,
    addressed_to: list[str] | None,
    invoked_agent: str,
    triggering_message: "ThreadMessage | None" = None,
) -> str:
    if purpose == "bootstrap":
        return "The founder has added you to this thread"
    if purpose == "close_out":
        return "This thread is being archived; provide a close-out"
    if purpose == "task_followup":
        payload = (triggering_message.system_payload or {}) if triggering_message else {}
        task_id = payload.get("task_id", "?")
        status = payload.get("status", "?")
        return (
            f"Task {task_id} that you dispatched from this thread reached "
            f"`{status}`. Compose a follow-up reply with the result (pull "
            f"details via `grassland details {task_id}`), or decline if "
            f"there is nothing substantive to add. Dispatching a new task "
            f"from this turn is not allowed; mention any new action in the "
            f"reply and let the founder loop in."
        )
    # purpose == "reply"
    addr = addressed_to or []
    if addr == ["@all"]:
        return f"Message {triggering_seq} addressed @all"
    if invoked_agent in addr:
        return f"Message {triggering_seq} addressed you individually"
    return f"Message {triggering_seq} (no explicit addressee)"
```

Update `build_thread_prompt` to pass `triggering_message`:

```python
def build_thread_prompt(
    *,
    thread, participants, messages,
    invocation_token, invoked_agent, purpose, triggering_seq,
) -> str:
    triggering = next((m for m in messages if m.seq == triggering_seq), None)
    addressed_to = triggering.addressed_to if triggering else None
    # ... existing body ...
    note = _purpose_note(
        purpose, triggering_seq, addressed_to, invoked_agent,
        triggering_message=triggering,
    )
    # ... rest unchanged ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_thread_task_followup.py::test_purpose_note_task_followup_renders_task_id_and_status -v`
Expected: PASS. Also run `uv run pytest tests/test_thread_runner.py -v` (existing) — must all still pass.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/thread_runner.py tests/test_thread_task_followup.py
git commit -m "feat(threads): add task_followup branch to thread prompt builder"
```

---

## Task 5: Render `task_completed` / `task_failed` system messages

Two existing renderers walk `system_payload`'s `kind_tag` to format SYSTEM messages: `src/infrastructure/thread_store.py` (for `_index.md` transcript) and `src/daemon/thread_forward.py` (for forwarded thread rendering). Add the two new tags to both.

**Files:**
- Modify: `src/infrastructure/thread_store.py:80-95` (system-message switch)
- Modify: `src/daemon/thread_forward.py:30-55`
- Test: `tests/test_thread_task_followup.py`

- [ ] **Step 1: Write the failing test**

```python
def test_thread_store_renders_task_completed_system_message():
    from src.infrastructure.thread_store import _render_system_message
    out = _render_system_message({
        "kind_tag": "task_completed",
        "task_id": "TASK-007", "original_task_id": "TASK-007",
        "status": "completed",
        "final_output_summary": "PDF uploaded to Drive",
        "final_artifact_dir": "workspaces/family_manager/work/task-007",
        "cancelled": False,
        "revisit_chain_length": 1,
    })
    assert "Task TASK-007 completed" in out
    assert "PDF uploaded to Drive" in out


def test_thread_store_renders_task_failed_with_cancelled_and_revisits():
    from src.infrastructure.thread_store import _render_system_message
    out = _render_system_message({
        "kind_tag": "task_failed",
        "task_id": "TASK-031", "original_task_id": "TASK-007",
        "status": "failed",
        "cancelled": True,
        "revisit_chain_length": 3,
    })
    assert "Task TASK-031 failed" in out
    assert "founder-cancelled" in out
    assert "after 2 revisits" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_thread_task_followup.py -v -k task_completed -k task_failed`
Expected: FAIL — current renderer returns a generic "system event" line for unknown tags.

- [ ] **Step 3: Add renderer cases**

In `src/infrastructure/thread_store.py` (in the existing `kind_tag` switch / if-ladder near line 85):

```python
elif tag == "task_completed":
    task_id = payload.get("task_id", "?")
    summary = (payload.get("final_output_summary") or "").strip()
    summary = summary if len(summary) <= 240 else summary[:237] + "..."
    artifact = payload.get("final_artifact_dir")
    parts = [f"**Task {task_id} completed**"]
    if summary: parts.append(summary)
    if artifact: parts.append(f"`{artifact}`")
    return "\n".join(parts)
elif tag == "task_failed":
    task_id = payload.get("task_id", "?")
    cancelled = payload.get("cancelled", False)
    revisits = int(payload.get("revisit_chain_length", 1)) - 1
    parts = [f"**Task {task_id} failed**"]
    if cancelled: parts.append("founder-cancelled")
    if revisits > 0: parts.append(f"after {revisits} revisit{'s' if revisits != 1 else ''}")
    return "; ".join(parts)
```

Mirror the same two branches in `src/daemon/thread_forward.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_thread_task_followup.py tests/test_thread_store.py tests/test_thread_forward.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infrastructure/thread_store.py src/daemon/thread_forward.py tests/test_thread_task_followup.py
git commit -m "feat(threads): render task_completed and task_failed system messages"
```

---

## Task 6: Database helper `bump_thread_turn_cap` + audit helpers

**Files:**
- Modify: `src/infrastructure/database.py` (new method near `update_thread_turn_cap` if present, else next to `get_thread`)
- Modify: `src/infrastructure/audit_logger.py` (3 new methods)
- Test: `tests/test_thread_task_followup.py`

- [ ] **Step 1: Write the failing test**

```python
def test_bump_thread_turn_cap_increments_and_returns_new_cap(tmp_path):
    db = _fresh_db(tmp_path)
    from src.models import ThreadRecord
    db.insert_thread(ThreadRecord(id="THR-1", subject="t", turn_cap=500))
    new_cap = db.bump_thread_turn_cap("THR-1", delta=1)
    assert new_cap == 501
    refetched = db.get_thread("THR-1")
    assert refetched.turn_cap == 501
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_task_followup.py::test_bump_thread_turn_cap_increments_and_returns_new_cap -v`
Expected: FAIL with `AttributeError`.

- [ ] **Step 3: Add the Database method**

```python
def bump_thread_turn_cap(self, thread_id: str, *, delta: int = 1) -> int:
    """Atomically increment turn_cap by ``delta`` and return the new value.

    Used by the task-followup hook to make room for the system-triggered
    re-invocation when the projected turn count would exceed the current
    cap. Each bump is audited at the call site via
    log_thread_turn_cap_auto_extended.
    """
    with self._conn() as c:
        cur = c.execute(
            "UPDATE threads SET turn_cap = turn_cap + ? WHERE id = ? "
            "RETURNING turn_cap",
            (delta, thread_id),
        )
        row = cur.fetchone()
    if row is None:
        raise KeyError(f"thread {thread_id} not found")
    return int(row["turn_cap"])
```

- [ ] **Step 4: Add audit helpers**

In `src/infrastructure/audit_logger.py`, near `log_thread_dispatch`:

```python
def log_thread_task_followup_enqueued(
    self, thread_id: str, *, original_task_id: str,
    terminal_task_id: str, dispatcher: str, invocation_token: str,
) -> None:
    self.log_event(
        action="thread_task_followup_enqueued",
        task_id=terminal_task_id,
        payload={
            "thread_id": thread_id,
            "original_task_id": original_task_id,
            "dispatcher": dispatcher,
            "invocation_token_prefix": invocation_token[:8],
        },
    )

def log_thread_followup_skipped(
    self, thread_id: str, *, original_task_id: str, terminal_task_id: str,
    reason: str, **extra,
) -> None:
    self.log_event(
        action="thread_followup_skipped",
        task_id=terminal_task_id,
        payload={
            "thread_id": thread_id,
            "original_task_id": original_task_id,
            "reason": reason,
            **extra,
        },
    )

def log_thread_turn_cap_auto_extended(
    self, thread_id: str, *, original_task_id: str,
    reason: str, new_cap: int,
) -> None:
    self.log_event(
        action="thread_turn_cap_auto_extended",
        task_id=original_task_id,
        payload={
            "thread_id": thread_id,
            "reason": reason,
            "new_cap": new_cap,
        },
    )
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_thread_task_followup.py -v -k turn_cap`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/infrastructure/database.py src/infrastructure/audit_logger.py tests/test_thread_task_followup.py
git commit -m "feat(threads): add bump_thread_turn_cap and followup audit helpers"
```

---

## Task 7: `_maybe_post_thread_followup` core helper

**Files:**
- Modify: `src/orchestrator/run_step.py` (new free function near `_maybe_spawn_auto_revisit`)
- Test: `tests/test_thread_task_followup.py`

- [ ] **Step 1: Write the failing tests (truth table)**

```python
# tests/test_thread_task_followup.py
import pytest
from src.models import TaskStatus, TaskRecord, ThreadRecord
from src.orchestrator.run_step import _maybe_post_thread_followup


def _seed_dispatched_root(orch, thread_id="THR-1", task_id="TASK-1"):
    """Helper: dispatched-from-thread root task + open thread + dispatcher audit row."""
    orch._db.insert_thread(ThreadRecord(id=thread_id, subject="t"))
    orch._db.add_thread_participant(thread_id, agent_name="alice", added_by="founder")
    orch._db.insert_task(TaskRecord(
        id=task_id, brief="b", team="ops", assigned_agent="alice",
        dispatched_from_thread_id=thread_id,
    ))
    orch._audit.log_thread_dispatch(
        thread_id, task_id=task_id, dispatcher="alice",
        target_agent="alice", team="ops",
    )


# Truth table from spec §4
@pytest.mark.parametrize(
    "status,spawned,cancelled,should_fire",
    [
        (TaskStatus.COMPLETED, False, False, True),   # row 1
        (TaskStatus.FAILED,    True,  False, False),  # row 2
        (TaskStatus.FAILED,    False, False, True),   # row 3
        (TaskStatus.FAILED,    False, True,  True),   # row 4
    ],
)
def test_fire_predicate_truth_table(orch_with_db, status, spawned, cancelled, should_fire):
    orch = orch_with_db
    _seed_dispatched_root(orch)
    if cancelled:
        orch._db.update_task("TASK-1", cancelled_at="2026-05-28T00:00:00+00:00")
    orch._db.update_task("TASK-1", status=status)
    _maybe_post_thread_followup(orch, "TASK-1", status=status, auto_revisit_spawned=spawned)
    # Did a TASK_FOLLOWUP invocation get minted?
    from src.models import ThreadInvocationStatus, ThreadInvocationPurpose
    invs = orch._db.list_thread_invocations("THR-1", status=ThreadInvocationStatus.PENDING)
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert (len(followups) == 1) == should_fire


def test_non_root_task_does_not_fire(orch_with_db):
    """Child tasks reaching terminal must NOT fire the followup."""
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.insert_task(TaskRecord(
        id="TASK-2", brief="b", team="ops", assigned_agent="alice",
        parent_task_id="TASK-1",  # child, not root
    ))
    _maybe_post_thread_followup(orch, "TASK-2",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    invs = orch._db.list_thread_invocations("THR-1")
    assert all(i.purpose != "task_followup" for i in invs)


def test_walks_revisit_chain_to_find_thread(orch_with_db):
    """A revisit root does not carry dispatched_from_thread_id; walk back to find it."""
    orch = orch_with_db
    _seed_dispatched_root(orch, task_id="TASK-1")
    orch._db.update_task("TASK-1", status=TaskStatus.FAILED)
    orch._db.insert_task(TaskRecord(
        id="TASK-2", brief="b", team="ops", assigned_agent="alice",
        revisit_of_task_id="TASK-1",  # no dispatched_from_thread_id
    ))
    orch._db.update_task("TASK-2", status=TaskStatus.COMPLETED)
    _maybe_post_thread_followup(orch, "TASK-2",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    from src.models import ThreadInvocationPurpose
    invs = orch._db.list_thread_invocations("THR-1")
    followups = [i for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP]
    assert len(followups) == 1


def test_thread_not_open_skips_with_audit(orch_with_db):
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.update_thread_status("THR-1", "archived")
    orch._db.update_task("TASK-1", status=TaskStatus.COMPLETED)
    _maybe_post_thread_followup(orch, "TASK-1",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    # No invocation; audit row written.
    invs = orch._db.list_thread_invocations("THR-1")
    assert not any(i.purpose == "task_followup" for i in invs)
    audit_rows = orch._db.get_audit_logs("TASK-1")
    assert any(r["action"] == "thread_followup_skipped" for r in audit_rows)


def test_turn_cap_auto_extends_when_projected_over(orch_with_db):
    orch = orch_with_db
    _seed_dispatched_root(orch)
    orch._db.update_thread_turn_cap("THR-1", turn_cap=1)  # tight cap
    orch._db.update_task("TASK-1", status=TaskStatus.COMPLETED)
    _maybe_post_thread_followup(orch, "TASK-1",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    refetched = orch._db.get_thread("THR-1")
    assert refetched.turn_cap == 2  # bumped by 1
```

(`orch_with_db` is an existing fixture; create one if not present that yields an `Orchestrator` with a fresh in-memory DB. See `tests/conftest.py` for the pattern.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_thread_task_followup.py -v -k fire_predicate -k non_root -k walks_revisit -k thread_not_open -k turn_cap_auto`
Expected: all FAIL with `ImportError: cannot import name '_maybe_post_thread_followup'`.

- [ ] **Step 3: Implement the helper**

In `src/orchestrator/run_step.py`, near `_maybe_spawn_auto_revisit`:

```python
def _maybe_post_thread_followup(
    orch: "Orchestrator",
    task_id: str,
    *,
    status: TaskStatus,
    auto_revisit_spawned: bool,
) -> None:
    """Post a task-followup system message + re-invoke the dispatcher.

    Fires iff:
      - status == COMPLETED                              → always
      - status == FAILED and not auto_revisit_spawned    → true terminal
    And the task is a thread-dispatched root chain (backward-walk finds an
    original task with dispatched_from_thread_id).

    Spec: docs/superpowers/specs/2026-05-28-thread-task-followup-design.md
    """
    # Fire-predicate gate.
    if status == TaskStatus.FAILED and auto_revisit_spawned:
        return
    if status not in (TaskStatus.COMPLETED, TaskStatus.FAILED):
        return  # defense; never called with non-terminal but keep safe

    db = orch._db
    audit = orch._audit
    terminal_task = db.get_task(task_id)
    if terminal_task is None:
        return

    # Only root tasks fire. Child terminals cascade through `_fail` at the
    # parent and re-enter this helper there.
    if terminal_task.parent_task_id is not None:
        return

    # Find the original dispatched root via revisit chain.
    chain = db.walk_revisit_chain(task_id, direction="backward")
    original = chain[-1] if chain else terminal_task
    thread_id = original.dispatched_from_thread_id
    if thread_id is None:
        return

    # Thread-state guard.
    thread = db.get_thread(thread_id)
    if thread is None or thread.status.value != "open":
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="thread_not_open",
            thread_status=(thread.status.value if thread else "missing"),
            task_status=status.value,
        )
        return

    # Dispatcher identity from the task_dispatched audit row.
    dispatch_rows = [
        r for r in db.get_audit_logs(original.id)
        if r["action"] == "thread_dispatch"
    ]
    if not dispatch_rows:
        audit.log_thread_followup_skipped(
            thread_id, original_task_id=original.id, terminal_task_id=task_id,
            reason="dispatcher_unresolved",
        )
        return
    dispatcher = dispatch_rows[0]["payload"]["dispatcher"]

    # Build system payload + render.
    kind_tag = "task_completed" if status == TaskStatus.COMPLETED else "task_failed"
    system_payload = {
        "kind_tag": kind_tag,
        "task_id": task_id,
        "original_task_id": original.id,
        "root_task_id": original.id,
        "status": status.value,
        "final_output_summary": terminal_task.final_output_summary or "",
        "final_artifact_dir": terminal_task.final_artifact_dir,
        "cancelled": terminal_task.cancelled_at is not None,
        "revisit_chain_length": len(chain) if chain else 1,
    }

    # Turn-cap projection + auto-extend.
    pending = db.count_pending_turn_obligations(thread_id)
    projected = thread.turns_used + pending + 1
    if projected > thread.turn_cap:
        new_cap = db.bump_thread_turn_cap(thread_id, delta=1)
        audit.log_thread_turn_cap_auto_extended(
            thread_id, original_task_id=original.id,
            reason="task_followup", new_cap=new_cap,
        )

    # Append system message + mint invocation.
    sys_seq = db.append_thread_message(
        thread_id=thread_id, speaker=dispatcher,
        kind="system", system_payload=system_payload,
    )
    inv = db.mint_thread_invocation(
        thread_id=thread_id, agent_name=dispatcher,
        triggering_seq=sys_seq,
        purpose=ThreadInvocationPurpose.TASK_FOLLOWUP,
    )
    audit.log_thread_task_followup_enqueued(
        thread_id, original_task_id=original.id, terminal_task_id=task_id,
        dispatcher=dispatcher, invocation_token=inv.invocation_token,
    )

    # Enqueue onto the thread queue.
    queue = getattr(orch, "_thread_queue", None)
    if queue is not None:
        # Synchronous put_nowait — same pattern as auto-revisit's _queue use.
        from src.daemon.thread_queue import ThreadJob
        queue.put_nowait(ThreadJob(org_slug=orch._slug, invocation_token=inv.invocation_token))
```

Required imports at the top of the function (already present in the module: `TaskStatus`, `ThreadInvocationPurpose`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_thread_task_followup.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py tests/test_thread_task_followup.py
git commit -m "feat(threads): _maybe_post_thread_followup helper (offline)"
```

---

## Task 8: Wire `_maybe_post_thread_followup` into every terminal site

Six call sites in `run_step.py` reach terminal state. Each gets a paired call to the helper.

**Files:**
- Modify: `src/orchestrator/run_step.py` lines 105-118, 153-164, 175-185, 200-205, 235-243, 323-330, 880-893

- [ ] **Step 1: Write the failing integration check**

Add a `run_step`-level test that drives a thread-dispatched task to COMPLETED via the real run_step flow and asserts a followup invocation lands. This will exercise the wiring.

```python
def test_completed_thread_dispatched_task_fires_followup(orch_with_db):
    """End-to-end at run_step level: COMPLETED root → followup minted."""
    orch = orch_with_db
    _seed_dispatched_root(orch)
    # Simulate run_step calling _complete then the wiring.
    from src.orchestrator.run_step import _complete, _maybe_post_thread_followup
    _complete(orch, "TASK-1", note="done", artifact_dir=None)
    _maybe_post_thread_followup(orch, "TASK-1",
                                status=TaskStatus.COMPLETED, auto_revisit_spawned=False)
    from src.models import ThreadInvocationPurpose
    invs = orch._db.list_thread_invocations("THR-1")
    assert sum(1 for i in invs if i.purpose == ThreadInvocationPurpose.TASK_FOLLOWUP) == 1
```

- [ ] **Step 2: Run test to verify it passes** (Task 7 already implements the helper; this test simply confirms _complete + helper interact cleanly).

Run: `uv run pytest tests/test_thread_task_followup.py::test_completed_thread_dispatched_task_fires_followup -v`
Expected: PASS.

- [ ] **Step 3: Wire each terminal site**

For each line, add the helper call right after the existing `_notify_failure_if_eligible` (FAILED sites) or `_complete` (COMPLETED site), passing the matching `auto_revisit_spawned` value. **Exact insertions:**

**Site A: line 117** (after `_notify_failure_if_eligible` in the exception path):
```python
_maybe_post_thread_followup(
    orch, task_id,
    status=TaskStatus.FAILED, auto_revisit_spawned=spawned,
)
```

**Site B: line 164** (after `_notify_failure_if_eligible` in the session-failure path):
```python
_maybe_post_thread_followup(
    orch, task_id,
    status=TaskStatus.FAILED, auto_revisit_spawned=spawned,
)
```

**Site C: line 185** (after `_notify_failure_if_eligible` in the self-blocked path):
```python
_maybe_post_thread_followup(
    orch, task_id,
    status=TaskStatus.FAILED, auto_revisit_spawned=False,
)
```

**Site D: line 205** (after the `_complete(...)` call on the success path):
```python
_maybe_post_thread_followup(
    orch, task_id,
    status=TaskStatus.COMPLETED, auto_revisit_spawned=False,
)
```

**Site E: line 243** (after `_notify_failure_if_eligible` in the invalid_delegate path):
```python
_maybe_post_thread_followup(
    orch, task_id,
    status=TaskStatus.FAILED, auto_revisit_spawned=False,
)
```

**Site F: line 330** (after `_notify_failure_if_eligible` in the unknown_action path):
```python
_maybe_post_thread_followup(
    orch, task_id,
    status=TaskStatus.FAILED, auto_revisit_spawned=False,
)
```

**Site G: line 893** (after `_notify_failure_if_eligible` in the parent-cascade path of `_enqueue_parent_if_waiting`):
```python
_maybe_post_thread_followup(
    orch, parent.id,
    status=TaskStatus.FAILED,
    auto_revisit_spawned=root_auto_revisit_spawned,
)
```

(Pass `root_auto_revisit_spawned`, not `False`. The cascade may be racing an auto-revisit on the root; the helper's gate handles it.)

- [ ] **Step 4: Run the unit suite + thread-followup tests**

Run: `uv run pytest tests/ -q --tb=short`
Expected: 1439+ passed (existing) + 10+ new (this plan). Zero failures.

- [ ] **Step 5: Commit**

```bash
git add src/orchestrator/run_step.py
git commit -m "feat(threads): wire _maybe_post_thread_followup into all terminal sites"
```

---

## Task 9: `/cancel` route — followup for PENDING-task cancellation

The running-task cancellation path is already covered transitively (SIGTERM → rc=-15 → run_step opaque-failure site → helper). But cancelling a PENDING task never enters run_step. Add the hook to the cancel route for that case.

**Files:**
- Modify: `src/daemon/routes/tasks.py` (`cancel_task` endpoint near line 721)

- [ ] **Step 1: Write the failing test**

```python
def test_cancel_pending_thread_dispatched_task_fires_followup(live_daemon, runtime):
    """PENDING task cancellation (no subprocess) → followup posts."""
    # 1. Bootstrap an org + open thread + thread-dispatched PENDING task.
    # 2. POST /tasks/{id}/cancel.
    # 3. Assert thread now has a task_failed system message + TASK_FOLLOWUP invocation.
    # ... (concrete bootstrap mirrors existing test_cancel.py patterns)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_thread_task_followup.py::test_cancel_pending_thread_dispatched_task_fires_followup -v`
Expected: FAIL — no system message in thread.

- [ ] **Step 3: Add hook to `cancel_task`**

In `src/daemon/routes/tasks.py` after the cancel loop transitions tasks to FAILED, for each cancelled task call:

```python
from src.orchestrator.run_step import _maybe_post_thread_followup
from src.models import TaskStatus
# After db.update_task(tid, status=TaskStatus.FAILED, cancelled_at=...):
_maybe_post_thread_followup(
    org.orchestrator, tid,
    status=TaskStatus.FAILED, auto_revisit_spawned=False,
)
```

(Place inside the existing loop in `cancel_task`. Each cascade-cancelled task is processed individually; the helper's parent_task_id gate keeps it from firing on children.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_thread_task_followup.py::test_cancel_pending_thread_dispatched_task_fires_followup -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/daemon/routes/tasks.py tests/test_thread_task_followup.py
git commit -m "feat(threads): fire task-followup hook on PENDING-task cancellation"
```

---

## Task 10: Web UI renderers for the two new tags

`web/src/features/threads/` renders SYSTEM messages by `kind_tag`. Add cases for `task_completed` and `task_failed`.

**Files:**
- Modify: `web/src/features/threads/components/SystemMessage.tsx` (or the file the existing `task_dispatched` case lives in — `grep -rn task_dispatched web/src` to locate)
- Test: `web/src/features/threads/__tests__/SystemMessage.test.tsx` (or the existing test file for the component)

- [ ] **Step 1: Locate the existing renderer**

```bash
grep -rn '"task_dispatched"\|task_dispatched' web/src/features/threads/
```

- [ ] **Step 2: Write the failing test**

```tsx
// e.g. web/src/features/threads/__tests__/SystemMessage.test.tsx
import { render, screen } from '@testing-library/react';
import { SystemMessage } from '../components/SystemMessage';

test('renders task_completed system message with task id and summary', () => {
  render(<SystemMessage payload={{
    kind_tag: 'task_completed',
    task_id: 'TASK-007',
    status: 'completed',
    final_output_summary: 'PDF uploaded',
  }} orgSlug="family" />);
  expect(screen.getByText(/TASK-007/)).toBeInTheDocument();
  expect(screen.getByText(/PDF uploaded/)).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /TASK-007/i }))
    .toHaveAttribute('href', '/orgs/family/tasks/TASK-007');
});

test('renders task_failed system message with cancelled and revisit annotations', () => {
  render(<SystemMessage payload={{
    kind_tag: 'task_failed',
    task_id: 'TASK-031',
    status: 'failed',
    cancelled: true,
    revisit_chain_length: 3,
  }} orgSlug="family" />);
  expect(screen.getByText(/TASK-031/)).toBeInTheDocument();
  expect(screen.getByText(/founder-cancelled/i)).toBeInTheDocument();
  expect(screen.getByText(/2 revisits/i)).toBeInTheDocument();
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run from `web/`: `npm test -- SystemMessage`
Expected: FAIL — unknown tag falls through to the default `system event` renderer.

- [ ] **Step 4: Add the two renderer cases**

Mirror the visual style of the existing `task_dispatched` case (badge + task-link + 240-char body tail). Link target: `/orgs/{orgSlug}/tasks/{task_id}` (existing route in the SPA).

- [ ] **Step 5: Run tests to verify they pass**

Run: `npm test -- SystemMessage`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add web/src/features/threads/
git commit -m "feat(web): render task_completed and task_failed thread system messages"
```

---

## Task 11: Integration tests with `fake_claude.sh`

Three end-to-end tests verifying the full chain.

**Files:**
- Create: `tests/integration/test_thread_task_followup_e2e.py`

- [ ] **Step 1: Write integration test scaffold**

Model on `tests/integration/test_threads_e2e.py::test_agent_dispatch_from_thread_creates_task`, which already sets both `fake_claude_plan_env` and `fake_claude_thread_plan_env`. Use:

```python
import time, httpx, pytest

@pytest.mark.integration
def test_followup_fires_on_completed_thread_dispatched_task(
    live_daemon, runtime, fake_claude_plan_env, fake_claude_thread_plan_env,
):
    """Founder send → manager reply+dispatch → task completes → followup runs."""
    port = live_daemon
    base = f"http://127.0.0.1:{port}/api/v1/orgs/test"
    _seed_thread_agent(runtime, "dev_agent")

    # Thread plan: turn 1 reply+dispatch; turn 2 (task_followup) reply only.
    fake_claude_thread_plan_env.write_text(r'''#!/usr/bin/env bash
thread_id=$1; token=$2; agent=$3; org=$4; purpose=$5
if [ "$purpose" = "task_followup" ]; then
  payload=$(mktemp)
  printf '{"thread_id":"%s","invocation_token":"%s","speaker":"%s","body_markdown":"task done, here is the link","in_response_to_seq":3}' "$thread_id" "$token" "$agent" > "$payload"
  grassland threads reply --org "$org" --thread-id "$thread_id" --from-file "$payload"
else
  # First turn: reply + dispatch
  dispatch_payload=$(mktemp); reply_payload=$(mktemp)
  printf '{"thread_id":"%s","invocation_token":"%s","dispatcher":"%s","brief":"do the thing"}' "$thread_id" "$token" "$agent" > "$dispatch_payload"
  grassland threads dispatch --org "$org" --thread-id "$thread_id" --from-file "$dispatch_payload"
  printf '{"thread_id":"%s","invocation_token":"%s","speaker":"%s","body_markdown":"dispatched, will report back","in_response_to_seq":1}' "$thread_id" "$token" "$agent" > "$reply_payload"
  grassland threads reply --org "$org" --thread-id "$thread_id" --from-file "$reply_payload"
fi
''')
    fake_claude_thread_plan_env.chmod(0o755)

    # Task plan: simple completion.
    fake_claude_plan_env.write_text(r'''#!/usr/bin/env bash
task_id=$1; session_id=$2; agent=$3; org=$4
payload=$(mktemp)
printf '{"task_id":"%s","session_id":"%s","status":"completed","output_summary":"report uploaded"}' "$task_id" "$session_id" > "$payload"
grassland report-completion --from-file "$payload"
''')
    fake_claude_plan_env.chmod(0o755)

    # Compose thread.
    r = httpx.post(f"{base}/threads", json={"subject": "t", "recipients": ["dev_agent"]}, headers=_auth())
    assert r.status_code == 200, r.text
    thread_id = r.json()["thread_id"]

    # Founder send to trigger the manager.
    httpx.post(f"{base}/threads/{thread_id}/send",
               json={"body_markdown": "please do X", "addressed_to": ["@all"]},
               headers=_auth())

    # Wait until the thread has at least 5 messages: founder, manager system task_dispatched,
    # manager reply, system task_completed, manager followup reply.
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        msgs = httpx.get(f"{base}/threads/{thread_id}/messages", headers=_auth()).json()
        if len(msgs["messages"]) >= 5: break
        time.sleep(0.5)

    tags = [m.get("system_payload", {}).get("kind_tag") for m in msgs["messages"]]
    assert "task_dispatched" in tags
    assert "task_completed" in tags
    # Final manager reply present.
    assert any(m["kind"] == "message" and m["speaker"] == "dev_agent"
               and "task done" in (m.get("body_markdown") or "") for m in msgs["messages"])
```

- [ ] **Step 2: Add the revisit-aware test**

```python
@pytest.mark.integration
def test_followup_fires_once_after_revisit(
    live_daemon, runtime, fake_claude_plan_env, fake_claude_thread_plan_env,
):
    """Task fails once + auto-revisit + revisit completes → exactly one followup."""
    # Task plan: first call fails (exit 1, no callback); subsequent calls complete.
    # Use a counter file to switch behavior.
    counter = runtime / "task_counter"
    fake_claude_plan_env.write_text(rf'''#!/usr/bin/env bash
task_id=$1; session_id=$2; agent=$3; org=$4
n=$(cat "{counter}" 2>/dev/null || echo 0); n=$((n+1)); echo $n > "{counter}"
if [ "$n" = "1" ]; then exit 1; fi  # first attempt: no callback → auto-revisit
payload=$(mktemp)
printf '{{"task_id":"%s","session_id":"%s","status":"completed","output_summary":"after revisit"}}' "$task_id" "$session_id" > "$payload"
grassland report-completion --from-file "$payload"
''')
    fake_claude_plan_env.chmod(0o755)
    # Same thread plan as the prior test.

    # ... bootstrap, send, wait for followup, then assert:
    #   - exactly one task_failed OR task_completed system message
    #   - exactly one TASK_FOLLOWUP invocation (consumed)
    #   - the followup's triggering_seq points at the COMPLETED system message
```

- [ ] **Step 3: Add the archived-thread test**

```python
@pytest.mark.integration
def test_followup_skipped_when_thread_archived_before_task_terminal(
    live_daemon, runtime, fake_claude_plan_env, fake_claude_thread_plan_env,
):
    """Thread archived between dispatch and terminal → audit only, no thread mutation."""
    # 1. Bootstrap and dispatch as in test 1.
    # 2. Archive the thread (POST /threads/{id}/request-archive) before the task completes.
    # 3. Wait for task to complete.
    # 4. Assert: no new message in the thread post-archive; an audit row
    #    with action="thread_followup_skipped", reason="thread_not_open".
```

- [ ] **Step 4: Run integration suite**

Run: `uv run pytest tests/integration/test_thread_task_followup_e2e.py -v -m integration`
Expected: 3 passed.

- [ ] **Step 5: Run full integration suite to confirm no regressions**

Run: `uv run pytest tests/ -v -m integration`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/integration/test_thread_task_followup_e2e.py
git commit -m "test(threads): e2e coverage for task-followup re-invocation"
```

---

## Task 12: Update CLAUDE.md with invariants

Add a "Thread task-followup" subsection under the existing threads notes documenting the non-obvious points future readers will hit.

**Files:**
- Modify: `CLAUDE.md` (in the "Threads" / "Implementation Order" section, after item 13)

- [ ] **Step 1: Add the subsection**

```markdown
## Thread task-followup (system bridges task terminal → thread)

When a task dispatched from a thread reaches its true terminal state, `_maybe_post_thread_followup` (`src/orchestrator/run_step.py`) appends a `task_completed` or `task_failed` SYSTEM message to the originating thread and mints a fresh invocation with purpose `TASK_FOLLOWUP` so the dispatching agent can compose the result-bearing reply it promised. Spec: `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md`.

**Non-obvious invariants:**

- **Call order matters.** The helper must be invoked *after* `_maybe_spawn_auto_revisit` at the two opaque-failure sites in `run_step_impl`, because the predicate ignores FAILED-with-spawned (the revisit chain will reach a later terminal that re-enters the helper). Mirrors the existing constraint between `_maybe_spawn_auto_revisit` and `_enqueue_parent_if_waiting`.
- **Thread linkage lives on the original root, not on revisit roots.** Auto-revisit and `/revisit` only copy `session_timeout_seconds`; they do NOT copy `dispatched_from_thread_id`. The helper walks `db.walk_revisit_chain(task_id, direction="backward")` and reads the column off `chain[-1]`. Do not propagate the column on revisit insert — the backward walk is the contract.
- **Dispatcher identity is read from audit, not stored on the task.** The `task_dispatched` audit row written by the dispatch route at `src/daemon/routes/threads.py:912` is the source of truth. If absent (missing original row), the helper audits `thread_followup_skipped(reason=dispatcher_unresolved)` rather than guessing.
- **Only root tasks fire.** Child task terminals cascade up to the root via `_enqueue_parent_if_waiting`'s `_fail(parent, ...)`, which re-enters the helper at that site. The `parent_task_id is not None` short-circuit is load-bearing — without it, every child completion in a dispatched-task tree would spam the thread.
- **`TASK_FOLLOWUP` purpose can reply or decline, but not dispatch.** `/threads/{id}/dispatch` keeps `require_purposes=[REPLY, BOOTSTRAP]`, which structurally rules out followup→dispatch recursion. Combined with the turn-cap auto-extend being per-followup, the loop is bounded.
- **Turn-cap auto-extend silently bumps `turn_cap` by 1 when projected over.** Audited via `thread_turn_cap_auto_extended(reason=task_followup)`. The pending-load projection counts `REPLY + BOOTSTRAP + TASK_FOLLOWUP` invocations via `Database.count_pending_turn_obligations`; `CLOSE_OUT` is excluded.
- **Non-OPEN threads skip everything.** `archiving`, `archived`, `abandoned` → audit-only, no system message, no mutation. The state-machine guards on send/reply/dispatch already reject non-OPEN; the helper matches that policy.
- **Cancelled tasks fire** (founder set `cancelled_at` → status FAILED → `auto_revisit_spawned=False` → fire). The system message's `cancelled: true` field is the surface; the founder gets a transparent thread record of the dispatch chain ending. To suppress, change the predicate; do not silently filter in the call sites.
```

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs(claude.md): document thread task-followup invariants"
```

---

## Final verification

- [ ] **Step 1: Run the full unit suite**

Run: `uv run pytest tests/ -q`
Expected: 1450+ passed (existing 1439 + this plan's new tests).

- [ ] **Step 2: Run the full integration suite**

Run: `uv run pytest tests/ -q -m integration`
Expected: all integration tests pass, including the 3 new ones.

- [ ] **Step 3: Manual smoke (optional)**

```bash
# Replay the THR-002 shape:
grassland threads compose --org family --recipients family_manager --subject "smoke followup" --body "please test"
# Wait for manager reply + dispatch + task completion
grassland threads show <new-thread-id>
# Expect: founder, manager reply, system task_dispatched, system task_completed, manager followup reply
```

- [ ] **Step 4: Open a PR**

```bash
git push -u origin worktree-thread-task-followup
gh pr create --base main --title "feat(threads): task-followup re-invocation on terminal" \
  --body "$(cat <<'EOF'
Implements `docs/superpowers/specs/2026-05-28-thread-task-followup-design.md`.

When a task dispatched from a thread reaches its true terminal state, the runtime now:
1. appends a `task_completed` / `task_failed` SYSTEM message into the thread
2. re-invokes the dispatching agent with new purpose `task_followup`, so it can compose the result-bearing reply it promised

Surfaced by THR-002 (family org): `family_manager` promised "我会在本 thread 回贴附链接" but the runtime had no completion→thread bridge to honor that.

Spec, design notes, and CLAUDE.md updated. New e2e covers the happy path, the revisit case, and the archived-thread skip.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
